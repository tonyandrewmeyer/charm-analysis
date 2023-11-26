#! /usr/bin/env python3

"""Summarise details about the testing done when developing the charms."""

import ast
import collections
import configparser
import pathlib
import pprint

import click


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
    uses_tox = 0
    tox = collections.Counter()
    test_imports = collections.Counter()
    test_frameworks = collections.Counter()
    for repo in pathlib.Path(cache_folder).iterdir():
        if repo.name.startswith("."):
            continue
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
    print("Uses tox:", uses_tox)
    pprint.pprint(tox)
    print(
        f"Harness: {test_imports['ops.testing']}, "
        f"Scenario: {test_imports['scenario']}, "
        f"unittest {test_imports['unittest']}, "
        f"pytest {test_imports['pytest']}, "
        f"pytest_operator: {test_imports['pytest_operator.plugin']}"
    )
    pprint.pprint(test_frameworks)


if __name__ == "__main__":
    main()
