#! /usr/bin/env python3

"""Summarise the Charm libs used and provided by a set of charms."""

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
            lib_count[total_libs + 1 - ignored] += 1
        else:
            lib_count[0] += 1

    report(total, lib_count, libs)


def report(total, repo_lib_count, lib_usage):
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

    # TODO:
    # * Include information about what libraries are provided.
    # * Maybe include information sourced from charmhub, like how up-to-date
    #   the libraries are?


if __name__ == "__main__":
    main()
