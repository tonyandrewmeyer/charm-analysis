#! /usr/bin/env python

"""Identify charms that have both machine and Kubernetes variants.

A "pair" is a charm that exists both as a machine charm and as a Kubernetes
charm sharing the same base name. The charm name is taken from charmcraft.yaml
(or metadata.yaml), falling back to the name of the folder that the charm is in.
Names are normalised by stripping any trailing "-operator" or "-charm" suffix.
Kubernetes charms are expected to use a "-k8s" (or "-kubernetes") suffix, while
machine charms omit it. The script prints the base names for which both forms
are found in the cached charms.
"""

import collections
import logging
import pathlib

import click
import rich.console
from helpers import configure_logging
from helpers import iter_repositories
from helpers import load_yaml

logger = logging.getLogger(__name__)

# Suffixes that are part of the repository (or charm) name, but not part of the
# name of the charm as it is deployed.
DECORATIVE_SUFFIXES = ("-operator", "-charm")

# Suffixes that indicate that the charm is a Kubernetes charm.
K8S_SUFFIXES = ("-k8s", "-kubernetes")

METADATA_FILES = ("charmcraft.yaml", "metadata.yaml")


def charm_name(repo: pathlib.Path) -> str:
    """Get the name of the charm in the given folder.

    Uses the name from the charm's metadata if available, otherwise the name of
    the folder itself.
    """
    for filename in METADATA_FILES:
        location = repo / filename
        # The is_file() check keeps "the charm has no metadata.yaml" (expected,
        # and silent) distinct from "the file is there but unusable", which
        # load_yaml logs.
        if not location.is_file():
            continue
        data = load_yaml(location)
        if data is not None and isinstance(data.get("name"), str):
            return data["name"]
    return repo.name


def normalise(name: str) -> tuple[str, bool]:
    """Split a charm name into its base name and whether it is a Kubernetes charm."""
    name = name.strip().lower()
    for suffix in DECORATIVE_SUFFIXES:
        # Only strip the suffix if something is left, so that a charm actually
        # called "operator" is not reduced to an empty name.
        if name.endswith(suffix) and len(name) > len(suffix):
            name = name[: -len(suffix)]
            break
    for suffix in K8S_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)], True
    return name, False


def find_variants(
    base: pathlib.Path,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Collect the machine and Kubernetes charms found in the cache folder.

    Returns two mappings of base charm name to the names of the charms that have
    that base name: the first for machine charms, the second for Kubernetes ones.
    """
    machine: dict[str, set[str]] = collections.defaultdict(set)
    k8s: dict[str, set[str]] = collections.defaultdict(set)
    for repo in iter_repositories(base):
        name = charm_name(repo)
        stem, is_k8s = normalise(name)
        if not stem:
            logger.warning("Could not determine a base name for %s", repo)
            continue
        (k8s if is_k8s else machine)[stem].add(name)
    return machine, k8s


@click.option("--cache-folder", default=".cache")
@click.command()
def main(cache_folder: str):
    """Output information about paired machine and K8s charms."""
    configure_logging()

    machine, k8s = find_variants(pathlib.Path(cache_folder))
    report(machine, k8s)


def report(machine: dict[str, set[str]], k8s: dict[str, set[str]]):
    """Output a report of the results to the console."""
    console = rich.console.Console()
    console.print()  # Separate out from any logging.

    if not machine and not k8s:
        console.print("No charms found.")
        return

    pairs = sorted(set(machine) & set(k8s))
    for name in pairs:
        # More than one charm with the same base name means that the pairing is
        # ambiguous: show the individual charm names so that it can be checked.
        candidates = machine[name] | k8s[name]
        if len(candidates) > 2:
            console.print(f"{name} (ambiguous: {', '.join(sorted(candidates))})")
        else:
            console.print(name)

    total = len(set(machine) | set(k8s))
    console.print()
    console.print(
        f"{len(pairs)} out of {total} charm names "
        f"({(len(pairs) / total * 100):.1f}%) have both machine and K8s variants."
    )
    console.print(
        f"Machine only: {len(set(machine) - set(k8s))}. "
        f"K8s only: {len(set(k8s) - set(machine))}."
    )


if __name__ == "__main__":
    main()
