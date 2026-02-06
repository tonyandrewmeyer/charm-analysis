#! /usr/bin/env python3

"""Produce simple statistics about the charms' dependencies."""

import ast
import collections
import logging
import operator
import pathlib
import tomllib

import click
import packaging.requirements
import rich.console
import rich.logging
from helpers import count_and_percentage_table
from helpers import iter_repositories

logger = logging.getLogger(__name__)


def _ops_version(line: str, location: pathlib.Path):
    """Extract out the version specifier from a requirements line."""
    if line == "ops":
        return "latest"
    req = packaging.requirements.Requirement(line)
    if req.marker:
        logger.info("ops in %s has marker %s", location, req.marker)
    if req.url:
        logger.info("ops in %s is found at %s", location, req.url)
    if req.extras:
        logger.warning("ops in %s wants ops extras: %s", location, req.extras)
    return str(req.specifier)


def requirements_txt(
    location: pathlib.Path,
    ops_versions: collections.Counter,
    all_dependencies: collections.Counter,
    all_dependencies_pinned: collections.Counter,
):
    with location.open() as requirements:
        for line in requirements.readlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("--hash"):
                continue
            # Assume that if the line endswith a \ the rest is just hashes and
            # so can be ignored here (is this a reasonable assumption?).
            line = line.rstrip("\\")
            if ops_versions and "ops" in line:
                ops_versions[_ops_version(line, location)] += 1
            else:
                # There should be a cleaner way to do this.
                all_dependencies[line.strip().split("=", 1)[0]] += 1
                all_dependencies_pinned[line.strip()] += 1


def setup_py(
    location: pathlib.Path,
    ops_versions: collections.Counter,
    all_dependencies: collections.Counter,
    all_dependencies_pinned: collections.Counter,
    python_versions: collections.Counter,
):
    has_install_requires = False
    with location.open() as setup:
        tree = ast.parse(setup.read())
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.Call)
                or getattr(node.func, "id", None) != "setup"
            ):
                continue
            for kw in node.keywords:
                if kw.arg == "install_requires":
                    has_install_requires = True
                    for val in kw.value.elts:
                        val_str = val.value if isinstance(val, ast.Constant) else ast.literal_eval(val)
                        if "ops" in val_str:
                            ops_versions[_ops_version(val_str, location)] += 1
                        else:
                            # There should be a cleaner way to do this.
                            all_dependencies[val_str.split("=", 1)[0]] += 1
                            all_dependencies_pinned[val_str] += 1
                elif kw.arg == "python_requires":
                    python_versions[kw.value.value] += 1
    return has_install_requires


def pyproject_toml(
    location: pathlib.Path,
    ops_versions: collections.Counter,
    all_dependencies: collections.Counter,
    all_dependencies_pinned: collections.Counter,
    python_versions: collections.Counter,
    optional_dependency_sections: collections.Counter,
    dev_dependencies=collections.Counter,
):
    with location.open("rb") as pyproject:
        data = tomllib.load(pyproject)
        if "dependencies" in data:
            yield "pyproject.toml"
            for dep in data["dependencies"]:
                if "ops" in dep:
                    ops_versions[_ops_version(dep, location)] += 1
                else:
                    # There should be a cleaner way to do this.
                    all_dependencies[dep.split("=", 1)[0]] += 1
                    all_dependencies_pinned[dep] += 1
        if "requires-python" in data:
            python_versions[data["requires-python"]] += 1
        for section in data.get("project", {}).get("optional-dependencies", {}):
            optional_dependency_sections[section] += 1
            if section == "dev":
                for dep in data["project"]["optional-dependencies"]["dev"]:
                    dev_dependencies[dep] += 1
        if "poetry" in data.get("tool", {}):
            yield "poetry"
            for dep in data["tool"]["poetry"].get("dependencies", ()):
                if dep == "group":
                    for section in data["tool"]["poetry"]["dependencies"]["group"]:
                        optional_dependency_sections[section] += 1
                        # Clearly something better is needed here...
                        if section in (
                            "dev",
                            "unit",
                            "integration",
                            "static",
                            "scenario",
                            "static-{charm,lib}",
                            "dev-environment",
                            "static-charm",
                            "static-lib",
                            "charm-integration",
                            "functional",
                            "static-{charm,lib,unit,integration}",
                            "integration-charm",
                            "integration-scaling",
                            "functional-tests",
                            "static-{charm, lib}",
                        ):
                            for devdep in data["tool"]["poetry"]["dependencies"][
                                "group"
                            ][section].get("dependencies", ()):
                                dev_dependencies[devdep] += 1
                    continue
                if "ops" in dep:
                    ops_versions[_ops_version(dep, location)] += 1
                else:
                    # There should be a cleaner way to do this.
                    all_dependencies[dep.split("=", 1)[0]] += 1
                    all_dependencies_pinned[dep] += 1


@click.option("--cache-folder", default=".cache")
@click.command()
def main(cache_folder: str):
    """Output simple statistics about the dependencies of the charms."""
    FORMAT = "%(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=FORMAT,
        datefmt="[%X]",
        handlers=[rich.logging.RichHandler()],
    )

    total = 0
    dependencies_source = collections.Counter()
    python_versions = collections.Counter()
    ops_versions = collections.Counter()
    all_dependencies = collections.Counter()
    all_dependencies_pinned = collections.Counter()
    dev_dependencies = collections.Counter()
    optional_dependency_sections = collections.Counter()
    for repo in iter_repositories(pathlib.Path(cache_folder)):
        total += 1
        # Look for requirements.txt, setup.py, and pyproject.toml.
        # It's possible that a single repository has more than one of these.
        # For now, let's count them all, although we'll need to de-duplicate the
        # Python and ops version counts.
        if (repo / "requirements.txt").exists():
            dependencies_source["requirements.txt"] += 1
            requirements_txt(
                repo / "requirements.txt",
                ops_versions,
                all_dependencies,
                all_dependencies_pinned,
            )
        if (repo / "requirements-dev.txt").exists():
            dependencies_source["requirements-dev.txt"] += 1
            requirements_txt(
                repo / "requirements-dev.txt",
                None,
                all_dependencies,
                all_dependencies_pinned,
            )
        if (repo / "setup.py").exists():
            if setup_py(
                repo / "setup.py",
                ops_versions,
                all_dependencies,
                all_dependencies_pinned,
                python_versions,
            ):
                dependencies_source["setup.py"] += 1
        if (repo / "pyproject.toml").exists():
            for source in pyproject_toml(
                repo / "pyproject.toml",
                ops_versions,
                all_dependencies,
                all_dependencies_pinned,
                python_versions,
                optional_dependency_sections,
                dev_dependencies,
            ):
                dependencies_source[source] += 1
    assert not python_versions, "Found some Python versions, add that to the report!"
    assert not dev_dependencies, "Found some dev dependencies, add that to the report!"
    report(
        total,
        dependencies_source,
        ops_versions,
        all_dependencies,
        all_dependencies_pinned,
        optional_dependency_sections,
    )


def report(
    total,
    dependencies_source,
    ops_versions,
    all_dependencies,
    all_dependencies_pinned,
    optional_dependency_sections,
):
    """Output a report of the results to the console."""
    console = rich.console.Console()
    console.print()  # Separate out from any logging.

    table = count_and_percentage_table(
        "Dependency Sources", "Source", total, sorted(dependencies_source.items())
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "Ops Versions", "Version", total, sorted(ops_versions.items())
    )
    console.print(table)
    console.print()

    common_deps = [(env, count) for env, count in all_dependencies.items()]
    common_deps.sort(key=operator.itemgetter(1), reverse=True)
    table = count_and_percentage_table(
        "Common Dependencies", "Package", total, common_deps[:100]
    )
    console.print(table)
    console.print()

    common_deps = [(env, count) for env, count in all_dependencies_pinned.items()]
    common_deps.sort(key=operator.itemgetter(1), reverse=True)
    table = count_and_percentage_table(
        "Common Dependencies and Version", "Package", total, common_deps[:5]
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "pyproject.toml Optional Dependency Sections",
        "Section",
        total,
        sorted(optional_dependency_sections.items()),
    )
    console.print(table)
    console.print()

    # TODO:
    # * Properly parse the version specifiers for the dependencies
    #   (this should also avoid more of the FPs)
    # * Remove duplication of dependencies caused by charms listing them in
    #   multiple sources (e.g. a lock file plus setup.py).
    # * Do the charms really not specify the Python versions they work with?
    # * Why am I not finding the dev dependencies?


if __name__ == "__main__":
    main()
