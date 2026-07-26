#! /usr/bin/env python3

"""Summarise the lint and formatting configuration used by a set of charms."""

import collections
import configparser
import dataclasses
import logging
import operator
import pathlib
import re
import tomllib
from collections.abc import Iterator
from typing import Any

import click
import rich.console

from helpers import configure_logging
from helpers import count_and_percentage_table
from helpers import iter_repositories
from helpers import load_yaml


logger = logging.getLogger(__name__)

# How many of the most common ignored rules and pre-commit hooks to report.
COMMON_RULES = 20
COMMON_HOOKS = 20

# A ruff selector is a rule set prefix followed by an optional number, so the
# leading letters identify the rule set: "E501" is "E", "PLR0913" is "PL".
RULE_SET = re.compile(r"^[A-Z]+")

# Ruff's own name for "every rule set", which has no numeric part to strip.
ALL_RULES = "ALL"

# The linters and formatters we look for, in the order they are reported.
TOOLS = ("ruff", "black", "isort", "flake8")

# Pre-commit hook ids that show a charm uses a tool, even when the tool has no
# configuration file of its own.
PRECOMMIT_TOOLS = {
    "ruff": "ruff",
    "ruff-check": "ruff",
    "ruff-format": "ruff",
    "black": "black",
    "isort": "isort",
    "flake8": "flake8",
}


@dataclasses.dataclass
class CharmLint:
    """The lint configuration found for a single charm."""

    tools: set[str] = dataclasses.field(default_factory=set)
    selected: set[str] = dataclasses.field(default_factory=set)
    ignored: set[str] = dataclasses.field(default_factory=set)
    #: The line length the charm configures, whichever tool sets it.
    line_length: int | None = None
    has_precommit: bool = False
    precommit_hooks: set[str] = dataclasses.field(default_factory=set)


@dataclasses.dataclass
class LintStats:
    """Statistics about the lint configuration of one or more charms."""

    #: Number of charms that configure any linter or formatter at all.
    charms_with_lint: int = 0
    charms_with_ruff: int = 0
    tools: collections.Counter[str] = dataclasses.field(
        default_factory=collections.Counter
    )
    #: The full set of tools a charm uses, as a single label.
    combinations: collections.Counter[str] = dataclasses.field(
        default_factory=collections.Counter
    )
    #: Ruff rule sets ("E", "PL", ...), counted once per charm that selects them.
    rule_sets: collections.Counter[str] = dataclasses.field(
        default_factory=collections.Counter
    )
    #: Individual rules a charm ignores, counted once per charm.
    ignored: collections.Counter[str] = dataclasses.field(
        default_factory=collections.Counter
    )
    line_lengths: collections.Counter[int] = dataclasses.field(
        default_factory=collections.Counter
    )
    with_precommit: int = 0
    precommit_hooks: collections.Counter[str] = dataclasses.field(
        default_factory=collections.Counter
    )


def config_roots(repo: pathlib.Path, base: pathlib.Path) -> Iterator[pathlib.Path]:
    """Yield the folders that may hold a charm's lint configuration.

    Linters are normally configured in the charm folder, but a charm in a
    monorepo often relies on configuration at the root of the repository, so
    the ancestors of the charm are searched too - stopping before the cache
    folder, which holds no charm's configuration.
    """
    yield repo
    try:
        relative = repo.relative_to(base)
    except ValueError:
        # The charm is not under the cache folder (it is the cache folder, or
        # the caller pointed at a single charm): there is nothing to walk up.
        return
    for parent in relative.parents:
        if parent == pathlib.Path("."):
            break
        yield base / parent


def read_toml(path: pathlib.Path) -> dict[str, Any] | None:
    """Load a TOML file, returning None if it cannot be read or parsed."""
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        logger.warning("Unable to read %s: %s", path, e)
        return None


def read_ini(path: pathlib.Path) -> configparser.ConfigParser | None:
    """Load an ini-style file, returning None if it cannot be read or parsed.

    Interpolation is disabled because these files are not ours: a stray "%" in
    a flake8 exclude pattern should not stop us reading the rest of the file.
    """
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, UnicodeDecodeError, configparser.Error) as e:
        logger.warning("Unable to parse %s: %s", path, e)
        return None
    return parser


def sub_table(data: Any, *keys: str) -> dict[str, Any] | None:
    """Return a nested mapping from parsed TOML, or None if it is missing."""
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data if isinstance(data, dict) else None


def string_list(config: dict[str, Any], path: pathlib.Path, *keys: str) -> set[str]:
    """Return the union of several list-of-strings settings."""
    values: set[str] = set()
    for key in keys:
        value = config.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            logger.warning("%s: '%s' is not a list", path, key)
            continue
        values.update(str(item) for item in value)
    return values


def line_length(config: Any, path: pathlib.Path, *keys: str) -> int | None:
    """Return the first line length setting present, or None."""
    if not isinstance(config, dict):
        return None
    for key in keys:
        value = config.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            logger.warning("%s: '%s' is not a number: %r", path, key, value)
    return None


def ruff_settings(
    root: pathlib.Path, pyproject: dict[str, Any] | None
) -> tuple[dict[str, Any], pathlib.Path] | None:
    """Return a folder's ruff configuration, and the file it came from.

    A dedicated ``ruff.toml`` (or ``.ruff.toml``) takes precedence over the
    ``[tool.ruff]`` table of ``pyproject.toml``, as it does for ruff itself.
    """
    for name in ("ruff.toml", ".ruff.toml"):
        path = root / name
        if not path.is_file():
            continue
        data = read_toml(path)
        if data is not None:
            return data, path
    if pyproject is not None:
        config = sub_table(pyproject, "tool", "ruff")
        if config is not None:
            return config, root / "pyproject.toml"
    return None


def ruff_rules(config: dict[str, Any], path: pathlib.Path) -> tuple[set[str], set[str]]:
    """Return the rules a ruff configuration selects and ignores.

    Ruff moved these settings under a ``lint`` table in 0.2, but still accepts
    them at the top level, and charms use both spellings.
    """
    lint = sub_table(config, "lint") or {}
    selected = string_list(lint, path, "select", "extend-select") | string_list(
        config, path, "select", "extend-select"
    )
    ignored = string_list(lint, path, "ignore", "extend-ignore") | string_list(
        config, path, "ignore", "extend-ignore"
    )
    return selected, ignored


def rule_set(selector: str) -> str | None:
    """Return the rule set a ruff selector belongs to, or None if it is odd."""
    if selector == ALL_RULES:
        return ALL_RULES
    match = RULE_SET.match(selector)
    return match.group(0) if match else None


def ini_section(
    parser: configparser.ConfigParser, *names: str
) -> configparser.SectionProxy | None:
    """Return the first of several sections that the file defines."""
    for name in names:
        if parser.has_section(name):
            return parser[name]
    return None


def precommit_hooks(path: pathlib.Path) -> set[str]:
    """Return the ids of the hooks a .pre-commit-config.yaml runs."""
    data = load_yaml(path)
    if data is None:
        return set()
    repos = data.get("repos")
    if not isinstance(repos, list):
        logger.warning("%s: 'repos' is not a list", path)
        return set()
    hooks: set[str] = set()
    for repo in repos:
        if not isinstance(repo, dict):
            logger.warning("%s: unexpected repos entry: %r", path, repo)
            continue
        entries = repo.get("hooks")
        if not isinstance(entries, list):
            continue
        for hook in entries:
            if isinstance(hook, dict) and isinstance(hook.get("id"), str):
                hooks.add(hook["id"])
    return hooks


def analyse_root(root: pathlib.Path, lint: CharmLint) -> None:
    """Add the lint configuration found in a single folder to the charm's."""
    pyproject_path = root / "pyproject.toml"
    pyproject = read_toml(pyproject_path) if pyproject_path.is_file() else None

    ruff = ruff_settings(root, pyproject)
    if ruff is not None:
        config, path = ruff
        lint.tools.add("ruff")
        selected, ignored = ruff_rules(config, path)
        lint.selected |= selected
        lint.ignored |= ignored
        length = line_length(config, path, "line-length")
        if length is not None and lint.line_length is None:
            lint.line_length = length

    if pyproject is not None:
        black = sub_table(pyproject, "tool", "black")
        if black is not None:
            lint.tools.add("black")
            length = line_length(black, pyproject_path, "line-length")
            if length is not None and lint.line_length is None:
                lint.line_length = length
        if sub_table(pyproject, "tool", "isort") is not None:
            lint.tools.add("isort")
        if sub_table(pyproject, "tool", "flake8") is not None:
            lint.tools.add("flake8")

    # flake8 has no pyproject.toml support, so it is always configured in one
    # of these ini files; isort accepts several of the same ones.
    for name in (".flake8", "setup.cfg", "tox.ini", ".isort.cfg"):
        path = root / name
        if not path.is_file():
            continue
        parser = read_ini(path)
        if parser is None:
            continue
        flake8 = ini_section(parser, "flake8")
        if flake8 is not None:
            lint.tools.add("flake8")
            length = line_length(flake8, path, "max-line-length", "max_line_length")
            if length is not None and lint.line_length is None:
                lint.line_length = length
        isort_names = ("isort",) if name != ".isort.cfg" else ("isort", "settings")
        if ini_section(parser, *isort_names) is not None:
            lint.tools.add("isort")

    precommit = root / ".pre-commit-config.yaml"
    if precommit.is_file():
        lint.has_precommit = True
        hooks = precommit_hooks(precommit)
        lint.precommit_hooks |= hooks
        for hook in hooks:
            tool = PRECOMMIT_TOOLS.get(hook)
            if tool is not None:
                lint.tools.add(tool)


def summarise_lint(repo: pathlib.Path, base: pathlib.Path) -> CharmLint:
    """Summarise the lint configuration of a single charm."""
    lint = CharmLint()
    for root in config_roots(repo, base):
        analyse_root(root, lint)
    return lint


def combination(tools: set[str]) -> str:
    """Return a label describing the set of tools a charm uses."""
    if not tools:
        return "none"
    known = [tool for tool in TOOLS if tool in tools]
    return " + ".join(known + sorted(tools.difference(TOOLS)))


def merge(stats: LintStats, lint: CharmLint) -> None:
    """Add one charm's configuration into the running totals."""
    if lint.tools:
        stats.charms_with_lint += 1
    if "ruff" in lint.tools:
        stats.charms_with_ruff += 1
    for tool in lint.tools:
        stats.tools[tool] += 1
    stats.combinations[combination(lint.tools)] += 1
    sets = {rule_set(selector) for selector in lint.selected}
    for name in sets:
        if name is None:
            continue
        stats.rule_sets[name] += 1
    for rule in lint.ignored:
        stats.ignored[rule] += 1
    if lint.line_length is not None:
        stats.line_lengths[lint.line_length] += 1
    if lint.has_precommit:
        stats.with_precommit += 1
    for hook in lint.precommit_hooks:
        stats.precommit_hooks[hook] += 1


@click.option("--cache-folder", default=".cache")
@click.command()
def main(cache_folder: str):
    """Output simple statistics about the lint configuration of the charms."""
    configure_logging()

    base = pathlib.Path(cache_folder)
    charms = 0
    stats = LintStats()

    for repo in iter_repositories(base):
        charms += 1
        merge(stats, summarise_lint(repo, base))

    report(charms, stats)


def report(charms: int, stats: LintStats) -> None:
    """Output a report of the results to the console."""
    console = rich.console.Console()
    console.print()  # Separate out from any logging.

    console.print(
        f"{stats.charms_with_lint} out of {charms} charms configure a linter or "
        f"formatter, and {stats.with_precommit} out of {charms} charms have a "
        f".pre-commit-config.yaml."
    )
    console.print()

    if not charms:
        return

    table = count_and_percentage_table(
        "Lint Tools",
        "Tool",
        charms,
        [(tool, stats.tools[tool]) for tool in TOOLS if stats.tools[tool]],
    )
    console.print(table)
    console.print()

    combinations = sorted(
        stats.combinations.items(), key=operator.itemgetter(1), reverse=True
    )
    table = count_and_percentage_table(
        "Lint Tool Combinations", "Tools", charms, combinations
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "Configured Line Length",
        "Characters",
        charms,
        sorted(stats.line_lengths.items()),
    )
    console.print(table)
    console.print()

    if stats.charms_with_ruff:
        rule_sets = sorted(
            stats.rule_sets.items(), key=operator.itemgetter(1), reverse=True
        )
        table = count_and_percentage_table(
            "Ruff Rule Sets", "Rule Set", stats.charms_with_ruff, rule_sets
        )
        console.print(table)
        console.print()

        ignored = sorted(
            stats.ignored.items(), key=operator.itemgetter(1), reverse=True
        )
        table = count_and_percentage_table(
            "Most Ignored Ruff Rules",
            "Rule",
            stats.charms_with_ruff,
            ignored[:COMMON_RULES],
        )
        console.print(table)
        console.print()

    if stats.with_precommit:
        hooks = sorted(
            stats.precommit_hooks.items(), key=operator.itemgetter(1), reverse=True
        )
        table = count_and_percentage_table(
            "Common Pre-commit Hooks",
            "Hook",
            stats.with_precommit,
            hooks[:COMMON_HOOKS],
        )
        console.print(table)
        console.print()


if __name__ == "__main__":
    main()
