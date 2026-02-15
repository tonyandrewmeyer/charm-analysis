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


def summarise_actions(repo: pathlib.Path):
    yaml_file = None
    key = None
    if (repo / "actions.yaml").exists():
        yaml_file = repo / "actions.yaml"
        key = None
    elif (repo / "charmcraft.yaml").exists():
        yaml_file = repo / "charmcraft.yaml"
        key = "actions"
    if not yaml_file:
        logger.info("Cannot find actions metadata for %s", repo)
        return
    with yaml_file.open() as source:
        metadata = yaml.safe_load(source)
        if key:
            try:
                metadata = metadata[key]
            except KeyError:
                logger.info("Cannot find actions metadata for %s", repo)
                return
    total_actions = 0
    params_count = collections.Counter()
    actions_by_type = collections.Counter()
    actions_required_percentage = collections.Counter()
    has_additional_properties = 0
    has_execution_group = 0
    has_parallel = 0
    defaults = set()
    for name, action in metadata.items():
        total_actions += 1
        if "additionalProperties" in action:
            has_additional_properties += 1
        if "parallel" in action:
            has_parallel += 1
        if "execution-group" in action:
            has_execution_group += 1
        params_count[len(action.get("params", ()))] += 1
        for param_name, param in action.get("params", {}).items():
            actions_by_type[param["type"]] += 1
            if "default" in param:
                defaults.add(str(param["default"]))
            if "properties" in param:
                logger.warning(
                    "Properties: %s: %s: %s: %r",
                    repo,
                    name,
                    param_name,
                    param["properties"],
                )
        if "required" in action:
            required_count = len(action["required"])
            param_count = len(action.get("params", ()))
            if param_count == 0:
                actions_required_percentage["n/a"] += 1
            else:
                actions_required_percentage[required_count / param_count] += 1

    return (
        total_actions,
        params_count,
        actions_by_type,
        actions_required_percentage,
        has_additional_properties,
        has_execution_group,
        has_parallel,
        defaults,
    )


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
    total_actions = 0
    juju = collections.Counter()
    assumes = collections.Counter()
    assumes_all = collections.Counter()
    assumes_any = collections.Counter()
    containers = collections.Counter()
    resources = collections.Counter()
    relations = collections.Counter()
    storages = collections.Counter()
    devices = collections.Counter()

    has_additional_properties = 0
    has_parallel = 0
    has_execution_group = 0
    action_defaults = set()
    action_params_count = collections.Counter()
    actions_by_type = collections.Counter()
    actions_required_percentage = collections.Counter()

    for repo in iter_repositories(pathlib.Path(cache_folder)):
        total += 1
        yaml_file = None
        if (repo / "metadata.yaml").exists():
            yaml_file = repo / "metadata.yaml"
        elif (repo / "charmcraft.yaml").exists():
            yaml_file = repo / "charmcraft.yaml"
        if not yaml_file:
            logger.warning("Cannot find metadata for %s", repo)
            continue
        with yaml_file.open() as source:
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
        action_data = summarise_actions(repo)
        if action_data:
            (
                charm_total_actions,
                charm_params_count,
                charm_actions_by_type,
                charm_actions_required_percentage,
                charm_has_additional_properties,
                charm_has_execution_group,
                charm_has_parallel,
                charm_defaults,
            ) = action_data
            total_actions += charm_total_actions
            has_parallel += charm_has_parallel
            has_execution_group += charm_has_execution_group
            has_additional_properties += charm_has_additional_properties
            action_defaults = action_defaults.union(charm_defaults)
            action_params_count.update(charm_params_count)
            actions_by_type.update(charm_actions_by_type)
            actions_required_percentage.update(charm_actions_required_percentage)

    logger.info(
        "Found %s actions in total with %s params", total_actions, action_params_count
    )
    logger.info(
        "%s charms have additional properties set to True", has_additional_properties
    )
    logger.info("%s charms have parallel set", has_parallel)
    logger.info("%s charms have execution-group set", has_execution_group)
    import pprint

    pprint.pprint(action_defaults)
    pprint.pprint(actions_by_type)
    pprint.pprint(actions_required_percentage)

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
