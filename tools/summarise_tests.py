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
import rich.logging
from helpers import count_and_percentage_table
from helpers import iter_repositories


def tox_ini(location: pathlib.Path, tox: collections.Counter):
    tox_conf = configparser.ConfigParser()
    tox_conf.read(location)
    for section in tox_conf.sections():
        if section.startswith("testenv:"):
            tox[section.split(":", 1)[1]] += 1


def find_imports(module):
    """Iterate through the names of the modules imported by the specified module."""
    with module.open() as setup:
        tree = ast.parse(setup.read())
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
    FORMAT = "%(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=FORMAT,
        datefmt="[%X]",
        handlers=[rich.logging.RichHandler()],
    )

    total = 0
    uses_tox = 0
    tox = collections.Counter()
    test_imports = collections.Counter()
    test_frameworks = collections.Counter()
    for repo in iter_repositories(cache_folder):
        total += 1
        if (repo / "tox.ini").exists():
            uses_tox += 1
            tox_ini(repo / "tox.ini", tox)
        if (repo / "tests").exists():
            repo_test_imports = set(find_test_imports(repo / "tests"))
            if "ops.testing" in repo_test_imports:
                test_frameworks["harness"] += 1
            if "scenario" in repo_test_imports:
                test_frameworks["scenario"] += 1
            if "unittest" in repo_test_imports:
                test_frameworks["unittest"] += 1
            if "pytest" in repo_test_imports:
                test_frameworks["pytest"] += 1
            if "pytest_operator.plugin" in repo_test_imports:
                test_frameworks["pytest_operator"] += 1
            test_imports.update(repo_test_imports)

    report(uses_tox, total, test_imports, tox)


def report(uses_tox, total, test_imports, tox_environments):
    """Output a report of the results to the console."""
    console = rich.console.Console()
    console.print()  # Separate out from any logging.

    console.print(f"{uses_tox} out of {total} ({(uses_tox / total * 100):.1f}%) use tox.")
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
    # * There's a third unit test framework for charms, but I can't remember the
    #   name or where I would have that written down.


if __name__ == "__main__":
    main()
