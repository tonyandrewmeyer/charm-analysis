#! /usr/bin/env python3

"""Summarise the Charm libs used and provided by a set of charms."""

import pprint
import pathlib
import collections

import click


@click.option("--cache-folder", default=".cache")
@click.command()
def main(cache_folder: str):
    """Output simple statistics about the libs used/provided by the charms."""
    total = 0
    lib_count = collections.Counter()
    libs = collections.Counter()
    for repo in pathlib.Path(cache_folder).iterdir():
        if repo.name.startswith("."):
            continue
        total += 1
        if (repo / "lib" / "charms").exists():
            ignored = 0
            for total_libs, lib in enumerate((repo / "lib" / "charms").iterdir()):
                if not lib.is_dir():
                    ignored += 1
                    continue
                libs[lib.name] += 1
            lib_count[total_libs + 1 - ignored] += 1
        else:
            lib_count[0] += 1

    print("Total:", total)
    pprint.pprint(lib_count)
    pprint.pprint(libs)


if __name__ == "__main__":
    main()
