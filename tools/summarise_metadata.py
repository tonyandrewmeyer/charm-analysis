#! /usr/bin/env python3

"""Summarise interesting metadata from set of charms."""

import collections
import pathlib
import pprint

import click
import yaml


@click.option("--cache-folder", default=".cache")
@click.command()
def main(cache_folder: str):
    """Output simple statistics about the metadata provided by the charms."""
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
    for repo in pathlib.Path(cache_folder).iterdir():
        if repo.name.startswith("."):
            continue
        total += 1
        if (repo / "metadata.yaml").exists():
            metadata = yaml.safe_load((repo / "metadata.yaml").read_text())
            for assumption in metadata.get("assumes", ()):
                if isinstance(assumption, dict):
                    for assumpt in assumption.get("any-of", ()):
                        if "juju" in assumpt:
                            juju[assumpt] += 1
                        try:
                            assumes_any[assumpt] += 1
                        except TypeError:
                            print(f"Cannot handle {assumpt} in {repo}")
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
                relations[f"{relation} : {metadata['requires'][relation]['interface']}"] += 1
            for storage in metadata.get("storage", ()):
                storages[metadata["storage"][storage]["type"]] += 1
            for device in metadata.get("devices", ()):
                devices[metadata["devices"][device]["type"]] += 1

    print("Total:", total)
    pprint.pprint(juju)
    pprint.pprint(assumes)
    pprint.pprint(assumes_all)
    pprint.pprint(assumes_any)
    pprint.pprint(containers)
    pprint.pprint(resources)
    pprint.pprint(relations)
    pprint.pprint(storages)
    pprint.pprint(devices)


if __name__ == "__main__":
    main()
