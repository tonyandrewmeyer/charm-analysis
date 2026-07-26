#! /usr/bin/env python3

"""Summarise details about the testing done when developing the charms."""

import ast
import collections
import configparser
import itertools
import logging
import pathlib
import re
import tomllib
from collections.abc import Iterable, Iterator

import click
import rich.console
from helpers import configure_logging
from helpers import count_and_percentage_table
from helpers import iter_repositories
from helpers import parse_module

logger = logging.getLogger(__name__)

# Word-boundary match, so that (for example) "copyright" in a tox command is
# not counted as a use of pyright.
STATIC_CHECKERS = re.compile(r"\b(pyright|mypy|pyrefly|ty)\b")

# Names that are only available from `ops.testing` when using Scenario (as
# opposed to Harness). Used to tell the two frameworks apart, since ops 2.17
# re-exports Scenario from `ops.testing`.
SCENARIO_NAMES = frozenset({
    "Context",
    "State",
    "Manager",
    "CharmEvents",
    "CheckInfo",
    "Notice",
})
HARNESS_NAMES = frozenset({"Harness", "ExecArgs", "ExecResult"})

# Frameworks that are detected purely by the top level module that is imported.
# Maps the counter key to the module name that is imported.
SIMPLE_FRAMEWORK_MODULES = {
    "unittest": "unittest",
    "pytest": "pytest",
    "pytest_operator": "pytest_operator",
    "jubilant": "jubilant",
    "pytest_jubilant": "pytest_jubilant",
    # TODO: I'm not sure that importing zaza is always required - it seems like
    # there is always a tests.yaml file, but not at the top level, and there are
    # other reasons to have a tests.yaml file. The requirements file should have
    # zaza, so maybe this is sufficient?
    "zaza": "zaza",
}


def expand_factors(name: str) -> list[str]:
    """Expand a generative tox environment name into the names it defines.

    For example, ``{unit,integration}-py311`` expands to ``unit-py311`` and
    ``integration-py311``. Names without braces are returned unchanged.
    """
    parts: list[list[str]] = []
    remainder = name
    while "{" in remainder:
        before, _, rest = remainder.partition("{")
        group, sep, remainder = rest.partition("}")
        if not sep:
            # Unbalanced braces: give up and use the name as-is.
            return [name]
        if before:
            parts.append([before])
        parts.append([factor.strip() for factor in group.split(",")])
    if remainder:
        parts.append([remainder])
    if not parts:
        return [name]
    return ["".join(combination) for combination in itertools.product(*parts)]


def _tox_environments(
    config: configparser.ConfigParser,
) -> tuple[set[str], set[str]]:
    """Collect the tox environment names, and those that run static checks."""
    environments: set[str] = set()
    static: set[str] = set()
    for section in config.sections():
        if not section.startswith("testenv:"):
            continue
        names = expand_factors(section.split(":", 1)[1].strip())
        environments.update(names)
        try:
            commands = config.get(section, "commands")
        except configparser.Error:
            continue
        if STATIC_CHECKERS.search(commands):
            static.update(names)
    return environments, static


def _parse_tox_ini(text: str, location: pathlib.Path) -> tuple[set[str], set[str]]:
    """Parse tox ini-style configuration, returning the environments it defines.

    Returns a set of environment names, and the subset of those that run a
    static type checker. Both are empty if the configuration cannot be parsed.
    """
    # strict=False so that charms with duplicated sections or options (which tox
    # itself tolerates) are still counted.
    config = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        config.read_string(text, source=str(location))
    except configparser.Error as exc:
        logger.warning("Failed to parse tox configuration in %s: %s", location, exc)
        return set(), set()
    return _tox_environments(config)


def tox_ini(location: pathlib.Path) -> tuple[set[str], set[str]]:
    """Get the environments defined in a tox.ini file, and the static ones."""
    try:
        text = location.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Unable to read %s: %s", location, exc)
        return set(), set()
    return _parse_tox_ini(text, location)


def _flatten_commands(commands: object) -> str:
    """Flatten a tox pyproject.toml ``commands`` value into a single string."""
    if isinstance(commands, str):
        return commands
    if isinstance(commands, list):
        return " ".join(_flatten_commands(command) for command in commands)
    return ""


def pyproject_tox(location: pathlib.Path) -> tuple[set[str], set[str]] | None:
    """Get the tox environments configured in a pyproject.toml file.

    Returns None if the file has no tox configuration at all (or cannot be
    read), otherwise the environment names and the subset that run a static
    type checker.
    """
    try:
        with location.open("rb") as pyproject:
            data = tomllib.load(pyproject)
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        logger.warning("Unable to parse %s: %s", location, exc)
        return None
    tool = data.get("tool")
    config = tool.get("tox") if isinstance(tool, dict) else None
    if not isinstance(config, dict):
        return None
    legacy = config.get("legacy_tox_ini")
    if isinstance(legacy, str):
        return _parse_tox_ini(legacy, location)
    environments: set[str] = set()
    static: set[str] = set()
    env_list = config.get("env_list")
    if isinstance(env_list, list):
        environments.update(
            itertools.chain.from_iterable(
                expand_factors(name) for name in env_list if isinstance(name, str)
            )
        )
    envs = config.get("env")
    if isinstance(envs, dict):
        for name, env in envs.items():
            names = expand_factors(name)
            environments.update(names)
            if not isinstance(env, dict):
                continue
            if STATIC_CHECKERS.search(_flatten_commands(env.get("commands"))):
                static.update(names)
    return environments, static


def find_imports(module: pathlib.Path) -> Iterator[str]:
    """Iterate through the names of the modules imported by the specified module.

    ``from x import y`` yields both ``x`` and ``x.y``, since ``y`` may itself be
    a submodule. Relative imports are skipped: they can never refer to a
    third-party test framework, and would otherwise be misattributed (for
    example, ``from .scenario import ...`` is not a use of Scenario).
    """
    tree = parse_module(module)
    if tree is None:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue
            yield node.module
            for name in node.names:
                if name.name != "*":
                    yield f"{node.module}.{name.name}"
        elif isinstance(node, ast.Import):
            for name in node.names:
                yield name.name


def find_test_imports(base: pathlib.Path) -> Iterator[str]:
    """Iterate through the imports from modules in the tests folder."""
    for node in base.iterdir():
        if node.name.startswith("."):
            continue
        if node.is_dir():
            yield from find_test_imports(node)
        elif node.suffix == ".py":
            yield from find_imports(node)


def imports_module(imports: Iterable[str], module: str) -> bool:
    """Whether any of the imports is the given module, or a name from within it."""
    prefix = f"{module}."
    return any(name == module or name.startswith(prefix) for name in imports)


def ops_testing_frameworks(imports: set[str]) -> tuple[bool, bool]:
    """Whether the imports of `ops.testing` indicate use of Harness and Scenario.

    Since ops 2.17, `ops.testing` provides both Harness and Scenario, so the
    names that are imported have to be used to tell them apart.
    """
    if not imports_module(imports, "ops.testing"):
        return False, False
    names = {
        name.rsplit(".", 1)[1]
        for name in imports
        if name.startswith("ops.testing.") and name.count(".") == 2
    }
    harness = bool(names & HARNESS_NAMES)
    scenario = bool(names & SCENARIO_NAMES)
    if not harness and not scenario:
        # e.g. `import ops.testing` or `from ops import testing`: the framework
        # cannot be identified without following how the module is used, so
        # assume Harness, which is what the module provided originally.
        logger.info("Ambiguous ops.testing import; assuming Harness")
        return True, False
    return harness, scenario


def find_test_frameworks(tests: pathlib.Path, repo: pathlib.Path) -> set[str]:
    """Identify the test frameworks used by the tests in the given folder."""
    imports = set(find_test_imports(tests))
    frameworks = {
        key
        for key, module in SIMPLE_FRAMEWORK_MODULES.items()
        if imports_module(imports, module)
    }
    harness, scenario = ops_testing_frameworks(imports)
    if harness:
        frameworks.add("harness")
    if scenario or imports_module(imports, "scenario"):
        frameworks.add("scenario")
    if "scenario" in frameworks:
        logger.info("%s uses Scenario", repo)
    if "zaza" in frameworks:
        logger.info("%s uses Zaza", repo)
    return frameworks


@click.option("--cache-folder", default=".cache")
@click.command()
def main(cache_folder: str):
    """Output simple statistics about the tests of the charms."""
    configure_logging()

    total = 0
    uses_tox = 0
    has_tests = 0
    tox: collections.Counter[str] = collections.Counter()
    tox_static: collections.Counter[str] = collections.Counter()
    test_frameworks: collections.Counter[str] = collections.Counter()
    for repo in iter_repositories(pathlib.Path(cache_folder)):
        total += 1

        environments: tuple[set[str], set[str]] | None = None
        if (repo / "tox.ini").is_file():
            environments = tox_ini(repo / "tox.ini")
        elif (repo / "pyproject.toml").is_file():
            environments = pyproject_tox(repo / "pyproject.toml")
        if environments is not None:
            uses_tox += 1
            names, static_names = environments
            tox.update(names)
            tox_static.update(static_names)

        tests = repo / "tests"
        if not tests.is_dir():
            logger.info("%s has no tests folder", repo)
            continue
        has_tests += 1
        test_frameworks.update(find_test_frameworks(tests, repo))

    report(uses_tox, has_tests, total, test_frameworks, tox, tox_static)


def _by_count(counts: collections.Counter[str]) -> list[tuple[str, int]]:
    """Sort counts from most to least common, with ties broken by name.

    Counter.most_common() breaks ties by insertion order, which depends on the
    order that the repositories (and the sets of tox environments within them)
    happened to be iterated in, so the tables shuffle between runs.
    """
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def report(
    uses_tox: int,
    has_tests: int,
    total: int,
    test_frameworks: collections.Counter[str],
    tox_environments: collections.Counter[str],
    tox_static_environments: collections.Counter[str],
):
    """Output a report of the results to the console."""
    console = rich.console.Console()
    console.print()  # Separate out from any logging.

    if not total:
        console.print("No repositories found.")
        return
    console.print(
        f"{uses_tox} out of {total} ({(uses_tox / total * 100):.1f}%) use tox."
    )
    console.print(
        f"{has_tests} out of {total} ({(has_tests / total * 100):.1f}%) "
        "have a tests folder."
    )
    console.print()

    # These are percentages of all the charms, not only those that have tests,
    # so that the numbers can be compared across the report.
    table = count_and_percentage_table(
        "Unit Test Libraries",
        "Library",
        total,
        (
            ("unittest", test_frameworks["unittest"]),
            ("pytest", test_frameworks["pytest"]),
        ),
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "Testing Frameworks",
        "Framework",
        total,
        (
            ("Harness", test_frameworks["harness"]),
            ("Scenario", test_frameworks["scenario"]),
            ("pytest-operator", test_frameworks["pytest_operator"]),
            ("jubilant", test_frameworks["jubilant"]),
            ("pytest-jubilant", test_frameworks["pytest_jubilant"]),
            ("zaza", test_frameworks["zaza"]),
        ),
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "Common Tox Environments",
        "Environment",
        uses_tox,
        _by_count(tox_environments)[:10],
    )
    console.print(table)
    console.print()

    static_environments = _by_count(tox_static_environments)
    table = count_and_percentage_table(
        "Static Checking Tox Environments", "Environment", uses_tox, static_environments
    )
    console.print(table)
    console.print()


if __name__ == "__main__":
    main()
