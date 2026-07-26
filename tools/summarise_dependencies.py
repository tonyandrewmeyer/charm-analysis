#! /usr/bin/env python3

"""Produce simple statistics about the charms' dependencies."""

import ast
import collections
import dataclasses
import logging
import operator
import pathlib
import re
import tomllib
from collections.abc import Iterator

import click
import packaging.requirements
import packaging.utils
import rich.console
from helpers import configure_logging
from helpers import count_and_percentage_table
from helpers import iter_repositories
from helpers import parse_module

logger = logging.getLogger(__name__)

# Requirements files may contain pip options (``-r base.txt``, ``-e .``,
# ``--hash=...``, ``--index-url ...``) rather than requirements.
_PIP_OPTION = re.compile(r"^-")

# An inline comment in a requirements file must be preceded by whitespace,
# otherwise a '#' is part of the requirement (e.g. a URL '#egg=' fragment).
_INLINE_COMMENT = re.compile(r"(^|\s)#")

# Substrings that mark an optional-dependency section or dependency group as
# holding development (rather than runtime) dependencies.
_DEV_SECTION_KEYWORDS = (
    "dev",
    "unit",
    "integration",
    "static",
    "scenario",
    "functional",
    "lint",
    "format",
    "test",
    "coverage",
    "docs",
)


@dataclasses.dataclass
class DependencyStats:
    """Counts collected while scanning the charms' dependency declarations."""

    #: The number of charm repositories that were examined.
    total: int = 0
    #: Where the dependencies were declared, e.g. "requirements.txt".
    sources: collections.Counter[str] = dataclasses.field(
        default_factory=collections.Counter
    )
    #: The Python versions the charms declare support for.
    python_versions: collections.Counter[str] = dataclasses.field(
        default_factory=collections.Counter
    )
    #: The version specifiers used for the 'ops' dependency.
    ops_versions: collections.Counter[str] = dataclasses.field(
        default_factory=collections.Counter
    )
    #: Dependencies, by (normalised) package name.
    all_dependencies: collections.Counter[str] = dataclasses.field(
        default_factory=collections.Counter
    )
    #: Dependencies, by the full requirement string, including any specifier.
    all_dependencies_pinned: collections.Counter[str] = dataclasses.field(
        default_factory=collections.Counter
    )
    #: Dependencies found in development sections/groups.
    dev_dependencies: collections.Counter[str] = dataclasses.field(
        default_factory=collections.Counter
    )
    #: The names of [project.optional-dependencies] (or Poetry group) sections.
    optional_dependency_sections: collections.Counter[str] = dataclasses.field(
        default_factory=collections.Counter
    )
    #: The names of PEP 735 [dependency-groups] groups.
    dependency_groups: collections.Counter[str] = dataclasses.field(
        default_factory=collections.Counter
    )
    #: Dependencies found in PEP 735 groups, combined across all groups.
    dependency_group_deps: collections.Counter[str] = dataclasses.field(
        default_factory=collections.Counter
    )


def _requirement(
    spec: str, location: pathlib.Path
) -> packaging.requirements.Requirement | None:
    """Parse a requirement string, logging (and returning None) if it's invalid."""
    try:
        return packaging.requirements.Requirement(spec)
    except packaging.requirements.InvalidRequirement:
        logger.warning("Could not parse requirement %r in %s", spec, location)
        return None


def _package_name(spec: str, location: pathlib.Path) -> str:
    """Get the normalised package name from a requirement string.

    Falls back to the requirement string itself if it cannot be parsed.
    """
    req = _requirement(spec, location)
    return packaging.utils.canonicalize_name(req.name) if req else spec


def _ops_version(spec: str, location: pathlib.Path) -> str:
    """Extract the version specifier from a requirement line for 'ops'."""
    req = _requirement(spec, location)
    if req is None:
        return "unparseable"
    if req.marker:
        logger.info("ops in %s has marker %s", location, req.marker)
    if req.url:
        logger.info("ops in %s is found at %s", location, req.url)
    if req.extras:
        logger.warning("ops in %s wants ops extras: %s", location, req.extras)
    # An empty specifier means that any version will do.
    return str(req.specifier) or "latest"


def _record_dependency(
    spec: str,
    location: pathlib.Path,
    stats: DependencyStats,
    *,
    count_ops: bool = True,
    name: str | None = None,
) -> None:
    """Add a single requirement string to the appropriate counters.

    Args:
        spec: the requirement, e.g. "ops ~= 2.15" or "pyyaml".
        location: the file the requirement was found in (used for logging).
        stats: the counters to update.
        count_ops: if true, count 'ops' separately from the other dependencies.
        name: the package name, if it is already known (for example, when the
            version constraint is not in PEP 440 form, as with Poetry).
    """
    name = (
        packaging.utils.canonicalize_name(name)
        if name is not None
        else _package_name(spec, location)
    )
    if count_ops and name == "ops":
        stats.ops_versions[_ops_version(spec, location)] += 1
        return
    stats.all_dependencies[name] += 1
    stats.all_dependencies_pinned[spec] += 1


def _is_dev_section(name: str) -> bool:
    """Report whether a dependency section/group name holds development deps."""
    return any(keyword in name.lower() for keyword in _DEV_SECTION_KEYWORDS)


def requirements_txt(
    location: pathlib.Path, stats: DependencyStats, *, count_ops: bool = True
) -> None:
    """Count the dependencies declared in a pip requirements file."""
    try:
        content = location.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Unable to read %s: %s", location, exc)
        return
    for raw_line in content.splitlines():
        line = _INLINE_COMMENT.split(raw_line, 1)[0].strip()
        # Assume that if the line ends with a \ the rest is just hashes and
        # so can be ignored here (is this a reasonable assumption?).
        line = line.rstrip("\\").strip()
        if not line or _PIP_OPTION.match(line):
            # Blank, a comment, or a pip option rather than a requirement.
            continue
        _record_dependency(line, location, stats, count_ops=count_ops)


def setup_py(location: pathlib.Path, stats: DependencyStats) -> bool:
    """Count the dependencies declared in a setup.py.

    Returns whether an ``install_requires`` argument was found.
    """
    tree = parse_module(location)
    if tree is None:
        # parse_module has already logged why.
        return False
    has_install_requires = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or getattr(node.func, "id", None) != "setup":
            continue
        for kw in node.keywords:
            if kw.arg == "install_requires":
                requires = _literal(kw.value, location)
                if not isinstance(requires, (list, tuple, set)):
                    logger.warning(
                        "install_requires in %s is not a literal sequence", location
                    )
                    continue
                has_install_requires = True
                for spec in requires:
                    if isinstance(spec, str):
                        _record_dependency(spec, location, stats)
            elif kw.arg == "python_requires":
                requires_python = _literal(kw.value, location)
                if isinstance(requires_python, str):
                    stats.python_versions[requires_python] += 1
    return has_install_requires


def _literal(node: ast.expr, location: pathlib.Path) -> object | None:
    """Evaluate an AST node as a Python literal, or None if that's not possible."""
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        logger.info("Non-literal value in %s (line %s)", location, node.lineno)
        return None


def pyproject_toml(location: pathlib.Path, stats: DependencyStats) -> Iterator[str]:
    """Count the dependencies declared in a pyproject.toml.

    Yields the name of each dependency source found in the file, e.g. "poetry".
    """
    try:
        with location.open("rb") as pyproject:
            data = tomllib.load(pyproject)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning("Unable to parse %s: %s", location, exc)
        return
    if not isinstance(data, dict):  # pragma: no cover - tomllib always gives a dict.
        return
    project = data.get("project") or {}
    tool = data.get("tool") or {}

    for group, entries in (data.get("dependency-groups") or {}).items():
        stats.dependency_groups[group] += 1
        for entry in entries if isinstance(entries, list) else ():
            # Non-string entries are "include-group" tables, which are counted
            # via the group they refer to.
            if isinstance(entry, str):
                name = _package_name(entry, location)
                stats.dependency_group_deps[name] += 1
                if _is_dev_section(group):
                    stats.dev_dependencies[name] += 1

    if project.get("dependencies"):
        yield "pyproject.toml"
        for dep in project["dependencies"]:
            if isinstance(dep, str):
                _record_dependency(dep, location, stats)
    if isinstance(project.get("requires-python"), str):
        stats.python_versions[project["requires-python"]] += 1
    for section, deps in (project.get("optional-dependencies") or {}).items():
        stats.optional_dependency_sections[section] += 1
        if _is_dev_section(section):
            for dep in deps if isinstance(deps, list) else ():
                if isinstance(dep, str):
                    stats.dev_dependencies[_package_name(dep, location)] += 1

    if "poetry" in tool:
        yield from _poetry(tool["poetry"], location, stats)


def _poetry(
    poetry: dict[str, object], location: pathlib.Path, stats: DependencyStats
) -> Iterator[str]:
    """Count the dependencies declared in a [tool.poetry] table."""
    if not isinstance(poetry, dict):
        return
    yield "poetry"
    dependencies = poetry.get("dependencies") or {}
    if isinstance(dependencies, dict):
        for name, raw_constraint in dependencies.items():
            # A constraint is normally a string, but may also be a table (or a
            # list of tables, for multiple constraints, which is not handled).
            constraint = (
                raw_constraint.get("version")
                if isinstance(raw_constraint, dict)
                else raw_constraint
            )
            if not isinstance(constraint, str):
                constraint = None
            if name == "python":
                # Poetry declares the supported Python versions here, rather
                # than as a dependency.
                if constraint:
                    stats.python_versions[constraint] += 1
                continue
            if packaging.utils.canonicalize_name(name) == "ops":
                # Poetry constraints are not PEP 440 specifiers, so they are
                # recorded as-is.
                stats.ops_versions[constraint or "latest"] += 1
                continue
            # Likewise, the name is passed explicitly here rather than being
            # parsed out of the spec.
            spec = f"{name} {constraint}" if constraint else name
            _record_dependency(spec, location, stats, name=name)
    # Poetry's dependency groups live in [tool.poetry.group.<name>.dependencies].
    for section, group in (poetry.get("group") or {}).items():
        stats.optional_dependency_sections[section] += 1
        if not _is_dev_section(section) or not isinstance(group, dict):
            continue
        for name in group.get("dependencies") or ():
            stats.dev_dependencies[_package_name(name, location)] += 1


@click.option("--cache-folder", default=".cache")
@click.command()
def main(cache_folder: str):
    """Output simple statistics about the dependencies of the charms."""
    configure_logging()

    stats = DependencyStats()
    for repo in iter_repositories(pathlib.Path(cache_folder)):
        stats.total += 1
        # Look for requirements.txt, setup.py, and pyproject.toml.
        # It's possible that a single repository has more than one of these.
        # For now, let's count them all, although we'll need to de-duplicate the
        # Python and ops version counts.
        requirements = repo / "requirements.txt"
        if requirements.is_file():
            stats.sources["requirements.txt"] += 1
            requirements_txt(requirements, stats)
        dev_requirements = repo / "requirements-dev.txt"
        if dev_requirements.is_file():
            stats.sources["requirements-dev.txt"] += 1
            requirements_txt(dev_requirements, stats, count_ops=False)
        setup = repo / "setup.py"
        if setup.is_file() and setup_py(setup, stats):
            stats.sources["setup.py"] += 1
        pyproject = repo / "pyproject.toml"
        if pyproject.is_file():
            for source in pyproject_toml(pyproject, stats):
                stats.sources[source] += 1
    report(stats)


def report(stats: DependencyStats) -> None:
    """Output a report of the results to the console."""
    console = rich.console.Console()
    console.print()  # Separate out from any logging.
    total = stats.total

    table = count_and_percentage_table(
        "Dependency Sources", "Source", total, sorted(stats.sources.items())
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "Ops Versions", "Version", total, sorted(stats.ops_versions.items())
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "Common Dependencies", "Package", total, stats.all_dependencies.most_common(100)
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "Common Dependencies and Version",
        "Package",
        total,
        stats.all_dependencies_pinned.most_common(5),
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "pyproject.toml Optional Dependency Sections",
        "Section",
        total,
        sorted(stats.optional_dependency_sections.items()),
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "Python Versions", "Version", total, sorted(stats.python_versions.items())
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "Dev Dependencies", "Package", total, stats.dev_dependencies.most_common(50)
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "PEP 735 Dependency Groups",
        "Group",
        total,
        sorted(
            stats.dependency_groups.items(), key=operator.itemgetter(1), reverse=True
        ),
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "PEP 735 Dependency Group Packages (combined across all groups)",
        "Package",
        total,
        stats.dependency_group_deps.most_common(50),
    )
    console.print(table)
    console.print()

    # TODO:
    # * Remove duplication of dependencies caused by charms listing them in
    #   multiple sources (e.g. a lock file plus setup.py).
    # * Do the charms really not specify the Python versions they work with?


if __name__ == "__main__":
    main()
