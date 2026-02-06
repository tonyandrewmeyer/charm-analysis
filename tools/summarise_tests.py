#! /usr/bin/env python3

"""Summarise details about the testing done when developing the charms."""

import ast
import collections
import configparser
import logging
import operator
import pathlib

import click
import rich.console
from helpers import configure_logging
from helpers import count_and_percentage_table
from helpers import iter_repositories

logger = logging.getLogger(__name__)


def tox_ini(location: pathlib.Path, tox: collections.Counter, static: collections.Counter):
    tox_conf = configparser.ConfigParser(interpolation=None)
    tox_conf.read(location)
    for section in tox_conf.sections():
        if section.startswith("testenv:"):
            tox[section.split(":", 1)[1]] += 1
            try:
                commands = tox_conf.get(section, "commands")
            except configparser.NoOptionError:
                continue
            # This has FPs, e.g. "copyright", but is good enough for now.
            # It could maybe be a re with `\bpyright\b` or similar.
            if "pyright" in commands or "mypy" in commands:
                static[section.split(":", 1)[1]] += 1


def find_imports(module):
    """Iterate through the names of the modules imported by the specified module."""
    with module.open() as raw:
        tree = ast.parse(raw.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            yield node.module
        elif isinstance(node, ast.Import):
            for name in node.names:
                yield name.name


def find_test_imports(base):
    """Iterate through the imports from modules in the tests folder."""
    for node in base.iterdir():
        if node.name.startswith("."):
            continue
        if node.is_dir():
            yield from find_test_imports(node)
            continue
        if node.name.endswith(".py"):
            yield from find_imports(node)


@click.option("--cache-folder", default=".cache")
@click.command()
def main(cache_folder):
    """Output simple statistics about the tests of the charms."""
    configure_logging()

    total = 0
    uses_tox = 0
    tox = collections.Counter()
    tox_static = collections.Counter()
    test_imports = collections.Counter()
    test_frameworks = collections.Counter()
    for repo in iter_repositories(pathlib.Path(cache_folder)):
        total += 1
        if (repo / "tox.ini").exists():
            uses_tox += 1
            tox_ini(repo / "tox.ini", tox, tox_static)
        if (repo / "tests").exists():
            repo_test_imports = set(find_test_imports(repo / "tests"))
            if "ops.testing" in repo_test_imports:
                test_frameworks["harness"] += 1
            if "scenario" in repo_test_imports:
                logger.info("%s uses Scenario", repo)
                test_frameworks["scenario"] += 1
            if "unittest" in repo_test_imports:
                test_frameworks["unittest"] += 1
            if "pytest" in repo_test_imports:
                test_frameworks["pytest"] += 1
            if "pytest_operator.plugin" in repo_test_imports:
                test_frameworks["pytest_operator"] += 1
            if "zaza" in repo_test_imports:
                # TODO: I'm not sure if this is always required - it seems like
                # there is always a tests.yaml file, but not at the top level,
                # and there are other reasons to have a tests.yaml file. The
                # requirements file should have zaza, so maybe this is sufficient?
                logger.info("%s uses Zaza", repo)
                test_frameworks["zaza"] += 1
            test_imports.update(repo_test_imports)

    report(uses_tox, total, test_imports, tox, tox_static)


def report(uses_tox, total, test_imports, tox_environments, tox_static_environments):
    """Output a report of the results to the console."""
    console = rich.console.Console()
    console.print()  # Separate out from any logging.

    pct = f"{(uses_tox / total * 100):.1f}" if total else "N/A"
    console.print(f"{uses_tox} out of {total} ({pct}%) use tox.")
    console.print()

    table = count_and_percentage_table(
        "Unit Test Libraries",
        "Library",
        uses_tox,
        (("unittest", test_imports["unittest"]), ("pytest", test_imports["pytest"])),
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "Testing Frameworks",
        "Framework",
        uses_tox,
        (
            ("Harness", test_imports["ops.testing"]),
            ("Scenario", test_imports["scenario"]),
            ("pytest-operator", test_imports["pytest_operator.plugin"]),
            ("zaza", test_imports["zaza"]),
        ),
    )
    console.print(table)
    console.print()

    common_environments = [(env, count) for env, count in tox_environments.items()]
    common_environments.sort(key=operator.itemgetter(1), reverse=True)
    table = count_and_percentage_table(
        "Common Tox Environments", "Environment", uses_tox, common_environments[:10]
    )
    console.print(table)
    console.print()

    # TODO:
    # * 20 have a "scenario" tox environment, but only 15 are importing scenario:
    #   one of those numbers is surely wrong.

    static_environments = [(env, count) for env, count in tox_static_environments.items()]
    static_environments.sort(key=operator.itemgetter(1), reverse=True)
    table = count_and_percentage_table(
        "Static Checking Tox Environments", "Environment", uses_tox, static_environments
    )
    console.print(table)
    console.print()


if __name__ == "__main__":
    main()
