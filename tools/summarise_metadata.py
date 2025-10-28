#! /usr/bin/env python3

"""Summarise interesting metadata from set of charms."""

import collections
import logging
import operator
import pathlib

import click
import rich.console
import rich.logging
import yaml
from helpers import count_and_percentage_table
from helpers import iter_repositories

logger = logging.getLogger(__name__)


@click.option("--cache-folder", default=".cache")
@click.command()
def main(cache_folder: str):
    """Output simple statistics about the metadata provided by the charms."""
    FORMAT = "%(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=FORMAT,
        datefmt="[%X]",
        handlers=[rich.logging.RichHandler()],
    )

    total = 0
    juju = collections.Counter()
    assumes = collections.Counter()
    assumes_all = collections.Counter()
    assumes_any = collections.Counter()
    containers = collections.Counter()
    resources = collections.Counter()
    relations = collections.Counter()
    storages = collections.Counter()
    devices = collections.Counter()

    for repo in iter_repositories(pathlib.Path(cache_folder)):
        total += 1
        if not (repo / "metadata.yaml").exists():
            logger.warning("Cannot find metadata.yaml for %s", repo)
            continue
        with (repo / "metadata.yaml").open() as source:
            metadata = yaml.safe_load(source)
        for assumption in metadata.get("assumes", ()):
            if isinstance(assumption, dict):
                for assumpt in assumption.get("any-of", ()):
                    if "juju" in assumpt:
                        juju[assumpt] += 1
                    try:
                        assumes_any[assumpt] += 1
                    except TypeError:
                        logger.error("Cannot handle %s in %s", assumpt, repo)
                for assumpt in assumption.get("all-of", ()):
                    if "juju" in assumpt:
                        juju[assumpt] += 1
                    assumes_all[assumpt] += 1
            else:
                if "juju" in assumption:
                    juju[assumption] += 1
                assumes[assumption] += 1
        containers[len(metadata.get("containers", ()))] += 1
        for resource in metadata.get("resources", ()):
            resources[resource] += 1
        for relation in metadata.get("requires", ()):
            relations[
                f"{relation} : {metadata['requires'][relation]['interface']}"
            ] += 1
        for storage in metadata.get("storage", ()):
            storages[metadata["storage"][storage]["type"]] += 1
        for device in metadata.get("devices", ()):
            devices[metadata["devices"][device]["type"]] += 1

    assert (
        not assumes_any and not assumes_all
    ), "assumes_any and assumes_all have values, integrate them!"
    assert not devices, "Found some devices, add them to the report!"
    report(total, juju, assumes, containers, resources, relations, storages)


#    pprint.pprint(juju)
#    pprint.pprint(assumes)
#    pprint.pprint(assumes_all)
#    pprint.pprint(assumes_any)
#    pprint.pprint(containers)
#    pprint.pprint(resources)
#    pprint.pprint(relations)
#    pprint.pprint(storages)
#    pprint.pprint(devices)


def report(total, juju, assumes, containers, resources, relations, storages):
    """Output a report of the results to the console."""
    console = rich.console.Console()
    console.print()  # Separate out from any logging.

    table = count_and_percentage_table(
        "Juju Versions", "Version", total, sorted(juju.items())
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "Assumes",
        "Requirement",
        total,
        sorted([(k, v) for k, v in assumes.items() if "juju" not in k]),
    )
    console.print(table)
    console.print()

    common_resources = [(env, count) for env, count in resources.items()]
    common_resources.sort(key=operator.itemgetter(1), reverse=True)
    table = count_and_percentage_table(
        "Common Resources", "Resource", total, common_resources[:5]
    )
    console.print(table)
    console.print()

    common_relations = [(env, count) for env, count in relations.items()]
    common_relations.sort(key=operator.itemgetter(1), reverse=True)
    table = count_and_percentage_table(
        "Common Relations", "Relation", total, common_relations[:5]
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "Storage Types", "Storage", total, sorted(storages.items())
    )
    console.print(table)
    console.print()

    # TODO:
    # * Handle the combined file (charmcraft.yaml).
    # * We're finding hardly any Juju versions - does that mean it's optional
    #   and there's a default? Or are we missing some?


if __name__ == "__main__":
    main()
