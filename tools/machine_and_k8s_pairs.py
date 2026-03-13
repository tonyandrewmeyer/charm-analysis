#! /usr/bin/env python

"""Identify charms that have both machine and Kubernetes variants.

A "pair" is defined as a charm that exists both as a machine charm and as
a Kubernetes charm sharing the same base name. Charm names are normalized
by stripping a trailing "-operator" suffix if present. Kubernetes charms
are expected to use a "-k8s" suffix (optionally followed by "-operator"),
while machine charms omit the "-k8s" suffix. The script prints the base
names for which both forms are found in the cached charm entries.
"""

import logging
import pathlib

import click
import rich.logging
from helpers import iter_entries


@click.option("--cache-folder", default=".cache")
@click.command()
def main(cache_folder):
    """Output information about paired machine and K8s charms."""
    FORMAT = "%(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=FORMAT,
        datefmt="[%X]",
        handlers=[rich.logging.RichHandler()],
    )

    total = 0
    names = set()
    k8s_names = set()
    for entry in iter_entries(pathlib.Path(cache_folder)):
        total += 1
        charm_name = entry.parent.parent.name
        if charm_name.endswith("-operator"):
            charm_name = charm_name.rsplit("-", 1)[0]
        if charm_name.endswith("-k8s"):
            k8s_names.add(charm_name.rsplit("-", 1)[0])
        else:
            names.add(charm_name)
    for name in k8s_names:
        if name in names:
            print(name)


if __name__ == "__main__":
    main()
