#! /usr/bin/env python3

"""Summarise the Charm libs used and provided by a set of charms."""

import ast
import collections
import logging
import operator
import pathlib

import click
import rich.logging
import rich.console

from helpers import iter_repositories, count_and_percentage_table


logger = logging.getLogger(__name__)


@click.option("--cache-folder", default=".cache")
@click.command()
def main(cache_folder: str):
    """Output simple statistics about the libs used/provided by the charms."""
    FORMAT = "%(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=FORMAT,
        datefmt="[%X]",
        handlers=[rich.logging.RichHandler()],
    )

    total = 0
    lib_count = collections.Counter()
    libs = collections.Counter()
    lib_deps = collections.Counter()
    for repo in iter_repositories(pathlib.Path(cache_folder)):
        total += 1
        if (repo / "lib" / "charms").exists():
            ignored = 0
            for total_libs, lib in enumerate((repo / "lib" / "charms").iterdir()):
                if not lib.is_dir():
                    logger.info("Assuming %s is not a charm lib", lib)
                    ignored += 1
                    continue
                libs[lib.name] += 1
                count_dependencies(lib_deps, lib)
            lib_count[total_libs + 1 - ignored] += 1
        else:
            lib_count[0] += 1

    report(total, lib_count, libs, lib_deps)


def count_dependencies(lib_deps, lib_folder):
    for major_version_folder in lib_folder.iterdir():
        if not major_version_folder.is_dir():
            logger.debug("Ignoring %s", major_version_folder)
            continue
        major_version = major_version_folder.name[1:]
        for lib in major_version_folder.iterdir():
            if not lib.is_file():
                logger.debug("Ignoring %s", lib)
                continue
            minor_version = None
            pydeps = None
            with lib.open() as charm:
                tree = ast.parse(charm.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            if target.id == "LIBAPI" and str(node.value.value) != major_version:
                                logger.warning("Lib version mismatch: %s != %s", node.value.value, major_version)
                            elif target.id == "LIBPATCH":
                                minor_version = node.value.value
                            elif target.id == "PYDEPS":
                                pydeps = node.value.elts
            if minor_version is None:
                logger.warning("No LIBPATCH found for %s", lib)
            if pydeps is None:
                logger.info("%s does not have any PYDEPS", lib)
                lib_deps[lib_folder.name, lib.name, major_version, minor_version, None] += 1
                continue
            for pydep in pydeps:
                pydep = pydep.value
                if pydep.startswith(("ops<=", "ops==", "ops>=")):
                    # ops is not a real dependency - it will always be in the
                    # charm requirements.
                    continue
                lib_deps[lib_folder.name, lib.name, major_version, minor_version, pydep] += 1


def report(total, repo_lib_count, lib_usage, lib_deps):
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
    table.add_row(
        "Total",
        str(sum(repo_lib_count.values())),
        f"{(sum(repo_lib_count.values()) / total * 100):.1f}",
    )
    console.print(table)
    console.print()

    common_libs = [(env, count) for env, count in lib_usage.items()]
    common_libs.sort(key=operator.itemgetter(1), reverse=True)
    table = count_and_percentage_table("Common Charm Libs", "Lib", total, common_libs[:20])
    console.print(table)
    console.print()

    # TODO: These two tables are not properly showing percentages.
    # They should either just show the first column or there's probably some
    # better way to show this information.
    table = count_and_percentage_table("Charm Lib PYDEPS", "Dependency", total, sorted(lib_deps.items()))
    console.print(table)
    console.print()

    no_deps = set()
    all_libs = set()
    simple_deps = collections.Counter()
    for (group, lib, major, minor, dep), count in lib_deps.items():
        lib = f"{group}/{lib}"
        all_libs.add(lib)
        if dep is None:
            no_deps.add(lib)
        simple_deps[lib, dep or "None"] = count
    logger.info("%s of %s libs have no dependencies", len(no_deps), len(all_libs))
    table = count_and_percentage_table("Charm Lib PYDEPS", "Dependency", total, sorted(simple_deps.items()))
    console.print(table)
    console.print()

    deps = collections.Counter()
    for (group, lib, major, minor, dep), count in lib_deps.items():
        if dep is None:
            continue
        deps[dep] += count
    table = count_and_percentage_table("Charm Lib PYDEPS", "Dependency", total, sorted(deps.items()))
    console.print(table)
    console.print()

    # TODO:
    # * Include information about what libraries are provided.
    # * Maybe include information sourced from charmhub, like how up-to-date
    #   the libraries are?


if __name__ == "__main__":
    main()
