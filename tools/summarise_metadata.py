#! /usr/bin/env python3

"""Summarise interesting metadata from set of charms."""

import collections
import dataclasses
import logging
import operator
import pathlib
from typing import Any

import click
import rich.console
from helpers import configure_logging, count_and_percentage_table, iter_repositories, load_yaml

logger = logging.getLogger(__name__)

# The order matters: later files override earlier ones, and charmcraft gives
# the (legacy) split files precedence over the unified charmcraft.yaml.
METADATA_FILES = ("charmcraft.yaml", "metadata.yaml")

# Juju's default when a storage entry does not specify a type.
DEFAULT_STORAGE_TYPE = "filesystem"


def load_charm_metadata(repo: pathlib.Path) -> dict[str, Any] | None:
    """Load the metadata for a charm, whichever file(s) it is spread across.

    Charms either use the unified ``charmcraft.yaml`` or the older
    ``metadata.yaml`` (sometimes both, with the charm parts in one and the
    metadata in the other).  The keys from the files are combined, with
    ``metadata.yaml`` taking precedence, matching charmcraft's own behaviour.

    Returns None (having logged a warning) if no usable metadata is found.
    """
    combined: dict[str, Any] = {}
    for filename in METADATA_FILES:
        path = repo / filename
        if not path.exists():
            continue
        data = load_yaml(path)
        if data is None:
            continue
        combined.update(data)
    if not combined:
        logger.warning("Cannot find metadata for %s", repo)
        return None
    return combined


def get_mapping(metadata: dict[str, Any], key: str, repo: pathlib.Path) -> dict[str, Any]:
    """Return the named section of the metadata, which should be a mapping.

    An empty mapping is returned (with a warning if the key has an unexpected
    type) when the section is missing or malformed.
    """
    section = metadata.get(key)
    if section is None:
        return {}
    if not isinstance(section, dict):
        logger.warning("%s: '%s' is not a mapping in the metadata", repo, key)
        return {}
    return section


def get_field(definition: Any, field: str, default: str) -> str:
    """Return a string field from a metadata entry, falling back to a default."""
    if not isinstance(definition, dict):
        return default
    value = definition.get(field, default)
    return str(value)


@dataclasses.dataclass
class ActionStats:
    """Statistics about the actions declared by one or more charms."""

    total: int = 0
    params_count: collections.Counter[int] = dataclasses.field(default_factory=collections.Counter)
    params_by_type: collections.Counter[str] = dataclasses.field(
        default_factory=collections.Counter
    )
    required_percentage: collections.Counter[float | str] = dataclasses.field(
        default_factory=collections.Counter
    )
    additional_properties: int = 0
    execution_group: int = 0
    parallel: int = 0
    defaults: set[str] = dataclasses.field(default_factory=set)

    def merge(self, other: "ActionStats") -> None:
        """Add the counts from another set of statistics into this one."""
        self.total += other.total
        self.params_count.update(other.params_count)
        self.params_by_type.update(other.params_by_type)
        self.required_percentage.update(other.required_percentage)
        self.additional_properties += other.additional_properties
        self.execution_group += other.execution_group
        self.parallel += other.parallel
        self.defaults |= other.defaults


def load_actions(repo: pathlib.Path) -> dict[str, Any] | None:
    """Load the actions declared by a charm.

    Actions live either in ``actions.yaml`` or under the ``actions`` key of
    ``charmcraft.yaml``.  The former wins, but if it is missing or unusable
    then the latter is tried.
    """
    actions_yaml = repo / "actions.yaml"
    if actions_yaml.exists():
        actions = load_yaml(actions_yaml)
        if actions:
            return actions
    charmcraft_yaml = repo / "charmcraft.yaml"
    if charmcraft_yaml.exists():
        charmcraft = load_yaml(charmcraft_yaml)
        if charmcraft:
            actions = charmcraft.get("actions")
            if isinstance(actions, dict) and actions:
                return actions
            if actions is not None and not isinstance(actions, dict):
                logger.warning("%s: 'actions' is not a mapping", charmcraft_yaml)
    logger.info("Cannot find actions metadata for %s", repo)
    return None


def summarise_actions(repo: pathlib.Path) -> ActionStats | None:
    """Summarise the actions declared by a single charm.

    Returns None if the charm does not declare any actions.
    """
    actions = load_actions(repo)
    if actions is None:
        return None

    stats = ActionStats()
    for name, action in actions.items():
        stats.total += 1
        if action is None:
            # An action with no body at all is valid.
            stats.params_count[0] += 1
            continue
        if not isinstance(action, dict):
            logger.warning("%s: action %s is not a mapping", repo, name)
            continue
        if "additionalProperties" in action:
            stats.additional_properties += 1
        if "parallel" in action:
            stats.parallel += 1
        if "execution-group" in action:
            stats.execution_group += 1
        params = action.get("params")
        if params is not None and not isinstance(params, dict):
            logger.warning("%s: params of action %s is not a mapping", repo, name)
            params = None
        params = params or {}
        stats.params_count[len(params)] += 1
        for param_name, param in params.items():
            if not isinstance(param, dict):
                logger.warning(
                    "%s: param %s of action %s is not a mapping", repo, param_name, name
                )
                stats.params_by_type["unknown"] += 1
                continue
            stats.params_by_type[str(param.get("type", "unknown"))] += 1
            if "default" in param:
                stats.defaults.add(str(param["default"]))
            if "properties" in param:
                logger.warning(
                    "Properties: %s: %s: %s: %r",
                    repo,
                    name,
                    param_name,
                    param["properties"],
                )
        required = action.get("required")
        if required is not None:
            if not isinstance(required, (list, tuple, set)):
                logger.warning("%s: required of action %s is not a list", repo, name)
            elif not params:
                stats.required_percentage["n/a"] += 1
            else:
                stats.required_percentage[len(required) / len(params)] += 1

    return stats


def count_assumes(
    assumptions: Any,
    repo: pathlib.Path,
    juju: collections.Counter[str],
    assumes: collections.Counter[str],
    assumes_any: collections.Counter[str],
    assumes_all: collections.Counter[str],
) -> None:
    """Count the entries of an ``assumes`` block.

    Nested ``any-of``/``all-of`` blocks are recursed into, and their leaves
    counted separately from the top level ones.  Anything that mentions "juju"
    is also counted as a Juju version requirement.
    """
    if isinstance(assumptions, str) or not isinstance(assumptions, (list, tuple)):
        logger.warning("%s: 'assumes' is not a list: %r", repo, assumptions)
        return
    for assumption in assumptions:
        if isinstance(assumption, dict):
            for key, counter in (("any-of", assumes_any), ("all-of", assumes_all)):
                nested = assumption.get(key)
                if nested is None:
                    continue
                if isinstance(nested, str) or not isinstance(nested, (list, tuple)):
                    logger.warning("%s: '%s' is not a list: %r", repo, key, nested)
                    continue
                for item in nested:
                    if isinstance(item, dict):
                        # A nested any-of/all-of: count its leaves instead.
                        count_assumes([item], repo, juju, assumes, assumes_any, assumes_all)
                        continue
                    item = str(item)
                    if "juju" in item:
                        juju[item] += 1
                    counter[item] += 1
            unknown = set(assumption) - {"any-of", "all-of"}
            if unknown:
                logger.warning("%s: unknown keys in 'assumes': %s", repo, unknown)
        elif isinstance(assumption, str):
            if "juju" in assumption:
                juju[assumption] += 1
            assumes[assumption] += 1
        else:
            logger.warning("%s: cannot handle assumption %r", repo, assumption)


@click.option("--cache-folder", default=".cache")
@click.command()
def main(cache_folder: str):
    """Output simple statistics about the metadata provided by the charms."""
    configure_logging()

    total = 0
    juju: collections.Counter[str] = collections.Counter()
    assumes: collections.Counter[str] = collections.Counter()
    assumes_all: collections.Counter[str] = collections.Counter()
    assumes_any: collections.Counter[str] = collections.Counter()
    containers: collections.Counter[int] = collections.Counter()
    resources: collections.Counter[str] = collections.Counter()
    relations: collections.Counter[str] = collections.Counter()
    storages: collections.Counter[str] = collections.Counter()
    devices: collections.Counter[str] = collections.Counter()
    actions = ActionStats()

    for repo in iter_repositories(pathlib.Path(cache_folder)):
        total += 1
        metadata = load_charm_metadata(repo)
        if metadata is None:
            continue

        if "assumes" in metadata:
            count_assumes(metadata["assumes"], repo, juju, assumes, assumes_any, assumes_all)
        containers[len(get_mapping(metadata, "containers", repo))] += 1
        for resource in get_mapping(metadata, "resources", repo):
            resources[resource] += 1
        for relation, definition in get_mapping(metadata, "requires", repo).items():
            interface = get_field(definition, "interface", "unknown")
            relations[f"{relation} : {interface}"] += 1
        for definition in get_mapping(metadata, "storage", repo).values():
            storages[get_field(definition, "type", DEFAULT_STORAGE_TYPE)] += 1
        for definition in get_mapping(metadata, "devices", repo).values():
            devices[get_field(definition, "type", "unknown")] += 1

        charm_actions = summarise_actions(repo)
        if charm_actions is not None:
            actions.merge(charm_actions)

    logger.info("Found %s actions in total with %s params", actions.total, actions.params_count)
    logger.info("%s actions specify additionalProperties", actions.additional_properties)
    logger.info("%s actions have parallel set", actions.parallel)
    logger.info("%s actions have execution-group set", actions.execution_group)
    logger.info("Action defaults: %s", actions.defaults)
    logger.info("Actions by type: %s", actions.params_by_type)
    logger.info("Actions required percentage: %s", actions.required_percentage)

    # These are not (yet) included in the report: complain loudly if there is
    # anything to include, but don't throw away the rest of the run.
    if assumes_any or assumes_all:
        logger.warning(
            "assumes any-of (%s) and all-of (%s) have values, integrate them!",
            dict(assumes_any),
            dict(assumes_all),
        )
    if devices:
        logger.warning("Found some devices (%s), add them to the report!", dict(devices))

    report(total, juju, assumes, containers, resources, relations, storages)


def report(
    total: int,
    juju: collections.Counter[str],
    assumes: collections.Counter[str],
    containers: collections.Counter[int],
    resources: collections.Counter[str],
    relations: collections.Counter[str],
    storages: collections.Counter[str],
) -> None:
    """Output a report of the results to the console."""
    console = rich.console.Console()
    console.print()  # Separate out from any logging.

    table = count_and_percentage_table("Juju Versions", "Version", total, sorted(juju.items()))
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "Assumes",
        "Requirement",
        total,
        sorted((k, v) for k, v in assumes.items() if "juju" not in k),
    )
    console.print(table)
    console.print()

    common_resources = sorted(resources.items(), key=operator.itemgetter(1), reverse=True)
    table = count_and_percentage_table("Common Resources", "Resource", total, common_resources[:5])
    console.print(table)
    console.print()

    common_relations = sorted(relations.items(), key=operator.itemgetter(1), reverse=True)
    table = count_and_percentage_table("Common Relations", "Relation", total, common_relations[:5])
    console.print(table)
    console.print()

    table = count_and_percentage_table("Storage Types", "Storage", total, sorted(storages.items()))
    console.print(table)
    console.print()

    # TODO:
    # * We're finding hardly any Juju versions - does that mean it's optional
    #   and there's a default? Or are we missing some?
    # * Nothing is done with the container counts.


if __name__ == "__main__":
    main()
