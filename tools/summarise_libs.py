#! /usr/bin/env python3

"""Summarise the Charm libs used and provided by a set of charms."""

import ast
import collections
import logging
import pathlib

import click
import packaging.requirements
import packaging.utils
import rich.console

from helpers import configure_logging
from helpers import count_and_percentage_table
from helpers import iter_repositories
from helpers import parse_module


logger = logging.getLogger(__name__)

# A charm lib is identified by the charm that provides it, the name of the
# module, the API (major) version, the patch (minor) version, and one of the
# libs's PYDEPS (or None, if it has none).
LibKey = tuple[str, str, str, int | None, str | None]


@click.option("--cache-folder", default=".cache")
@click.command()
def main(cache_folder: str):
    """Output simple statistics about the libs used/provided by the charms."""
    configure_logging()

    total = 0
    lib_count: collections.Counter[int] = collections.Counter()
    libs: collections.Counter[str] = collections.Counter()
    lib_deps: collections.Counter[LibKey] = collections.Counter()
    for repo in iter_repositories(pathlib.Path(cache_folder)):
        total += 1
        charms_folder = repo / "lib" / "charms"
        if not charms_folder.is_dir():
            lib_count[0] += 1
            continue
        found = 0
        for lib_folder in sorted(charms_folder.iterdir()):
            if not lib_folder.is_dir():
                logger.info("Assuming %s is not a charm lib", lib_folder)
                continue
            libs[lib_folder.name] += 1
            found += count_dependencies(lib_deps, lib_folder)
        lib_count[found] += 1

    report(total, lib_count, libs, lib_deps)


def _constant(node: ast.expr | None) -> object | None:
    """Return the value of an AST node if it is a literal constant, else None."""
    return node.value if isinstance(node, ast.Constant) else None


def _is_ops(pydep: str) -> bool:
    """Report whether a PYDEPS entry is a requirement on ops itself."""
    try:
        req = packaging.requirements.Requirement(pydep)
    except packaging.requirements.InvalidRequirement:
        return False
    return packaging.utils.canonicalize_name(req.name) == "ops"


def _lib_metadata(lib: pathlib.Path) -> tuple[str | None, int | None, list[str] | None]:
    """Extract the LIBAPI, LIBPATCH, and PYDEPS values from a charm lib module.

    Any of the values is None if the lib does not define it (or defines it as
    something other than a literal).
    """
    tree = parse_module(lib)
    if tree is None:
        # parse_module has already logged why.
        return None, None, None
    api_version: str | None = None
    patch_version: int | None = None
    pydeps: list[str] | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id == "LIBAPI":
                value = _constant(node.value)
                api_version = None if value is None else str(value)
            elif target.id == "LIBPATCH":
                value = _constant(node.value)
                patch_version = value if isinstance(value, int) else None
            elif target.id == "PYDEPS":
                if isinstance(node.value, (ast.List, ast.Tuple)):
                    pydeps = [
                        element.value
                        for element in node.value.elts
                        if isinstance(element, ast.Constant)
                        and isinstance(element.value, str)
                    ]
                else:
                    logger.warning("PYDEPS in %s is not a literal list", lib)
    return api_version, patch_version, pydeps


def count_dependencies(
    lib_deps: collections.Counter[LibKey], lib_folder: pathlib.Path
) -> int:
    """Count the PYDEPS of every lib provided by a charm.

    Args:
        lib_deps: counter to add the (charm, lib, major, minor, dep) keys to.
        lib_folder: a ``lib/charms/<charm-name>`` folder.

    Returns:
        The number of lib modules found in the folder.
    """
    found = 0
    for major_version_folder in sorted(lib_folder.iterdir()):
        if not major_version_folder.is_dir():
            logger.debug("Ignoring %s", major_version_folder)
            continue
        if not major_version_folder.name.startswith("v"):
            # Charm libs live in a "v{API version}" folder - anything else
            # (such as __pycache__) is not a charm lib.
            logger.info("Ignoring non-version folder %s", major_version_folder)
            continue
        major_version = major_version_folder.name[1:]
        for lib in sorted(major_version_folder.iterdir()):
            if not lib.is_file() or lib.suffix != ".py":
                logger.debug("Ignoring %s", lib)
                continue
            found += 1
            api_version, patch_version, pydeps = _lib_metadata(lib)
            if api_version is not None and api_version != major_version:
                logger.warning(
                    "Lib version mismatch in %s: %s != %s",
                    lib,
                    api_version,
                    major_version,
                )
            if patch_version is None:
                logger.warning("No LIBPATCH found for %s", lib)
            if not pydeps:
                if pydeps is None:
                    logger.info("%s does not have any PYDEPS", lib)
                lib_deps[
                    lib_folder.name, lib.name, major_version, patch_version, None
                ] += 1
                continue
            for pydep in pydeps:
                if _is_ops(pydep):
                    # ops is not a real dependency - it will always be in the
                    # charm requirements.
                    continue
                lib_deps[
                    lib_folder.name, lib.name, major_version, patch_version, pydep
                ] += 1
    return found


def report(
    total: int,
    repo_lib_count: collections.Counter[int],
    lib_usage: collections.Counter[str],
    lib_deps: collections.Counter[LibKey],
) -> None:
    """Output a report of the results to the console."""
    console = rich.console.Console()
    console.print()  # Separate out from any logging.

    table = count_and_percentage_table(
        "Charm Lib Usage",
        "Number of Libs",
        total,
        sorted(repo_lib_count.items()),
    )
    table.add_section()
    # The counter maps "number of libs in a repo" to "number of repos", so the
    # total number of libs is the sum of the products, and the average per
    # repository is more meaningful than a percentage.
    lib_total = sum(count * repos for count, repos in repo_lib_count.items())
    average = f"{(lib_total / total):.1f} per charm" if total else "N/A"
    table.add_row("Total", str(lib_total), average)
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "Common Charm Libs", "Lib", total, lib_usage.most_common(20)
    )
    console.print(table)
    console.print()

    # TODO: These two tables are not properly showing percentages.
    # They should either just show the first column or there's probably some
    # better way to show this information.
    table = count_and_percentage_table(
        "Charm Lib PYDEPS (by lib version)",
        "Dependency",
        total,
        sorted(lib_deps.items()),
    )
    console.print(table)
    console.print()

    no_deps: set[str] = set()
    all_libs: set[str] = set()
    simple_deps: collections.Counter[tuple[str, str]] = collections.Counter()
    for (group, lib, _major, _minor, dep), count in lib_deps.items():
        name = f"{group}/{lib}"
        all_libs.add(name)
        if dep is None:
            no_deps.add(name)
        simple_deps[name, dep or "None"] += count
    logger.info("%s of %s libs have no dependencies", len(no_deps), len(all_libs))
    table = count_and_percentage_table(
        "Charm Lib PYDEPS (by lib)", "Dependency", total, sorted(simple_deps.items())
    )
    console.print(table)
    console.print()

    deps: collections.Counter[str] = collections.Counter()
    for (_group, _lib, _major, _minor, dep), count in lib_deps.items():
        if dep is None:
            continue
        deps[dep] += count
    table = count_and_percentage_table(
        "Charm Lib PYDEPS (by dependency)", "Dependency", total, sorted(deps.items())
    )
    console.print(table)
    console.print()

    # TODO:
    # * Include information about what libraries are provided.
    # * Maybe include information sourced from charmhub, like how up-to-date
    #   the libraries are?


if __name__ == "__main__":
    main()
