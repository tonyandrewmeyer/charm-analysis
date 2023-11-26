#! /usr/bin/env python3

"""Produce simple statistics about the charms' dependencies."""

import ast
import collections
import pathlib
import pprint
import tomllib

import click


def requirements_txt(
    location: pathlib.Path,
    ops_versions: collections.Counter,
    all_dependencies: collections.Counter,
):
    with location.open() as requirements:
        for line in requirements.readlines():
            line = line.strip()
            if ops_versions and "ops" in line:
                ops_versions[line] += 1
            elif not line.startswith(("--hash", "#")):
                all_dependencies[line.strip()] += 1


def setup_py(
    location: pathlib.Path,
    ops_versions: collections.Counter,
    all_dependencies: collections.Counter,
    python_versions: collections.Counter,
):
    has_install_requires = False
    with location.open() as setup:
        tree = ast.parse(setup.read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or getattr(node.func, "id", None) != "setup":
                continue
            for kw in node.keywords:
                if kw.arg == "install_requires":
                    has_install_requires = True
                    for val in kw.value.elts:
                        if "ops" in val:
                            ops_versions[val] += 1
                        else:
                            all_dependencies[val] += 1
                elif kw.arg == "python_requires":
                    python_versions[kw.value.value] += 1
    return has_install_requires


def pyproject_toml(
    location: pathlib.Path,
    ops_versions: collections.Counter,
    all_dependencies: collections.Counter,
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
                    ops_versions[dep] += 1
                else:
                    all_dependencies[dep] += 1
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
                            for devdep in data["tool"]["poetry"]["dependencies"]["group"][
                                section
                            ].get("dependencies", ()):
                                dev_dependencies[devdep] += 1
                    continue
                if "ops" in dep:
                    ops_versions[dep] += 1
                else:
                    all_dependencies[dep] += 1


@click.option("--cache-folder", default=".cache")
@click.command()
def main(cache_folder: str):
    """Output simple statistics about the dependencies of the charms."""
    total = 0
    dependencies_source = collections.Counter()
    python_versions = collections.Counter()
    ops_versions = collections.Counter()
    all_dependencies = collections.Counter()
    dev_dependencies = collections.Counter()
    optional_dependency_sections = collections.Counter()
    for repo in pathlib.Path(cache_folder).iterdir():
        if repo.name.startswith("."):
            continue
        total += 1
        # Look for requirements.txt, setup.py, and pyproject.toml.
        # It's possible that a single repository has more than one of these.
        # For now, let's count them all, although we'll need to de-duplicate the
        # Python and ops version counts.
        if (repo / "requirements.txt").exists():
            dependencies_source["requirements.txt"] += 1
            requirements_txt(repo / "requirements.txt", ops_versions, all_dependencies)
        if (repo / "requirements-dev.txt").exists():
            dependencies_source["requirements-dev.txt"] += 1
            requirements_txt(repo / "requirements-dev.txt", None, all_dependencies)
        if (repo / "setup.py").exists():
            if setup_py(repo / "setup.py", ops_versions, all_dependencies, python_versions):
                dependencies_source["setup.py"] += 1
        if (repo / "pyproject.toml").exists():
            for source in pyproject_toml(
                repo / "pyproject.toml",
                ops_versions,
                all_dependencies,
                python_versions,
                optional_dependency_sections,
                dev_dependencies,
            ):
                dependencies_source[source] += 1

    pprint.pprint(dependencies_source)
    pprint.pprint(python_versions)
    pprint.pprint(ops_versions)
    pprint.pprint(all_dependencies)
    pprint.pprint(dev_dependencies)
    pprint.pprint(optional_dependency_sections)
    print("Total", total)


if __name__ == "__main__":
    main()
