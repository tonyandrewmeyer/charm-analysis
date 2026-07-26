#! /usr/bin/env python3

"""Summarise the config options declared by a set of charms."""

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

# Juju's default when an option does not specify a type.
DEFAULT_OPTION_TYPE = "string"

# The types that Juju understands. Anything else is a mistake in the charm.
KNOWN_OPTION_TYPES = frozenset({"string", "int", "boolean", "float", "secret"})

# How many of the most common option names to include in the report.
COMMON_NAMES = 20


@dataclasses.dataclass
class ConfigStats:
    """Statistics about the config options declared by one or more charms."""

    #: Number of charms that declare a config section at all.
    charms_with_config: int = 0
    total: int = 0
    #: Number of options declared, per charm.
    options_per_charm: collections.Counter[int] = dataclasses.field(
        default_factory=collections.Counter
    )
    by_type: collections.Counter[str] = dataclasses.field(default_factory=collections.Counter)
    #: Options that omit "type" entirely, and so are implicitly strings.
    implicit_type: int = 0
    with_default: int = 0
    with_description: int = 0
    naming: collections.Counter[str] = dataclasses.field(default_factory=collections.Counter)
    names: collections.Counter[str] = dataclasses.field(default_factory=collections.Counter)
    #: Charms that use both "-" and "_" to separate words in option names.
    mixed_naming: list[str] = dataclasses.field(default_factory=list)


def load_config(repo: pathlib.Path) -> dict[str, Any] | None:
    """Load the config options declared by a charm.

    Options live either in ``config.yaml`` or under the ``config`` key of
    ``charmcraft.yaml``, in both cases nested under an ``options`` mapping.
    The former wins, but if it is missing or unusable then the latter is tried,
    matching the way that ``summarise_metadata.load_actions`` handles actions.
    """
    config_yaml = repo / "config.yaml"
    if config_yaml.exists():
        config = load_yaml(config_yaml)
        if config:
            options = get_options(config, config_yaml)
            if options:
                return options
    charmcraft_yaml = repo / "charmcraft.yaml"
    if charmcraft_yaml.exists():
        charmcraft = load_yaml(charmcraft_yaml)
        if charmcraft:
            config = charmcraft.get("config")
            if isinstance(config, dict):
                options = get_options(config, charmcraft_yaml)
                if options:
                    return options
            elif config is not None:
                logger.warning("%s: 'config' is not a mapping", charmcraft_yaml)
    logger.info("Cannot find config options for %s", repo)
    return None


def get_options(config: dict[str, Any], path: pathlib.Path) -> dict[str, Any] | None:
    """Return the ``options`` mapping of a config section, or None."""
    options = config.get("options")
    if options is None:
        return None
    if not isinstance(options, dict):
        logger.warning("%s: 'options' is not a mapping", path)
        return None
    return options or None


def naming_style(name: str) -> str:
    """Return the word separator style used by an option name."""
    hyphen = "-" in name
    underscore = "_" in name
    if hyphen and underscore:
        return "mixed"
    if hyphen:
        return "hyphen"
    if underscore:
        return "underscore"
    return "single word"


def summarise_config(repo: pathlib.Path) -> ConfigStats | None:
    """Summarise the config options declared by a single charm.

    Returns None if the charm does not declare any config options.
    """
    options = load_config(repo)
    if options is None:
        return None

    stats = ConfigStats(charms_with_config=1)
    stats.options_per_charm[len(options)] += 1
    for name, option in options.items():
        stats.total += 1
        stats.names[name] += 1
        stats.naming[naming_style(name)] += 1
        if option is None:
            # An option with no body at all: Juju treats this as a string.
            stats.by_type[DEFAULT_OPTION_TYPE] += 1
            stats.implicit_type += 1
            continue
        if not isinstance(option, dict):
            logger.warning("%s: option %s is not a mapping", repo, name)
            stats.by_type["unknown"] += 1
            continue
        if "type" in option:
            option_type = str(option["type"])
            if option_type not in KNOWN_OPTION_TYPES:
                logger.warning("%s: option %s has unknown type %r", repo, name, option_type)
        else:
            option_type = DEFAULT_OPTION_TYPE
            stats.implicit_type += 1
        stats.by_type[option_type] += 1
        if "default" in option:
            stats.with_default += 1
        if option.get("description"):
            stats.with_description += 1

    # Only the styles that actually separate words count as a disagreement: a
    # charm whose names are all single words has nothing to be consistent about.
    separators = {style for style in stats.naming if style != "single word"}
    if len(separators) > 1:
        stats.mixed_naming.append(str(repo))

    return stats


def merge(total: ConfigStats, charm: ConfigStats) -> None:
    """Add the counts from one charm's statistics into the running totals."""
    total.charms_with_config += charm.charms_with_config
    total.total += charm.total
    total.options_per_charm.update(charm.options_per_charm)
    total.by_type.update(charm.by_type)
    total.implicit_type += charm.implicit_type
    total.with_default += charm.with_default
    total.with_description += charm.with_description
    total.naming.update(charm.naming)
    total.names.update(charm.names)
    total.mixed_naming.extend(charm.mixed_naming)


@click.option("--cache-folder", default=".cache")
@click.command()
def main(cache_folder: str):
    """Output simple statistics about the config options declared by the charms."""
    configure_logging()

    charms = 0
    stats = ConfigStats()

    for repo in iter_repositories(pathlib.Path(cache_folder)):
        charms += 1
        charm_stats = summarise_config(repo)
        if charm_stats is not None:
            merge(stats, charm_stats)

    if stats.mixed_naming:
        logger.info(
            "Charms mixing '-' and '_' in option names: %s",
            ", ".join(sorted(stats.mixed_naming)),
        )

    report(charms, stats)


def report(charms: int, stats: ConfigStats) -> None:
    """Output a report of the results to the console."""
    console = rich.console.Console()
    console.print()  # Separate out from any logging.

    console.print(
        f"{stats.charms_with_config} out of {charms} charms declare config options, "
        f"{stats.total} in total."
    )
    console.print()

    if not stats.total:
        return

    table = count_and_percentage_table(
        "Options per Charm",
        "Options",
        stats.charms_with_config,
        sorted(stats.options_per_charm.items()),
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "Option Types", "Type", stats.total, sorted(stats.by_type.items())
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "Option Documentation",
        "Attribute",
        stats.total,
        (
            ("Has a default", stats.with_default),
            ("Has a description", stats.with_description),
            ("Type is implicit", stats.implicit_type),
        ),
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "Option Naming Style", "Style", stats.total, sorted(stats.naming.items())
    )
    console.print(table)
    console.print()

    common_names = sorted(stats.names.items(), key=operator.itemgetter(1), reverse=True)
    table = count_and_percentage_table(
        "Common Option Names", "Name", charms, common_names[:COMMON_NAMES]
    )
    console.print(table)
    console.print()


if __name__ == "__main__":
    main()
