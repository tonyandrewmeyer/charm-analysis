#! /usr/bin/env python3

"""Summarise information about the charm artifacts on CharmHub"""

import collections
import datetime
import logging
import pathlib

import click
import httpx
import rich.console
import rich.logging
import yaml
from helpers import count_and_percentage_table
from helpers import iter_repositories

logger = logging.getLogger(__name__)


def charm_details(name):
    """Collect information about a charm from CharmHub."""
    # TODO: Figure out what other fields might be interesting.
    url = f"http://api.snapcraft.io/v2/charms/info/{name}?fields=channel-map,result.store-url"
    data = httpx.get(url).raise_for_status().json()
    store_url = data["result"]["store-url"]
    logger.info("The store URL for %s is %s", name, store_url)
    # Channel also has "base" and "name".
    # TODO: There's no need to loop through the objs many times like this - just
    # do one loop that builds up all the different lists (which can just be sets).
    tracks = [obj["channel"]["track"] for obj in data["channel-map"]]
    channels = [obj["channel"]["risk"] for obj in data["channel-map"]]
    release_times = [
        datetime.datetime.fromisoformat(obj["channel"]["released-at"])
        for obj in data["channel-map"]
    ]
    # Revision also has "version" (what is the difference with 'revision'?),
    # created-at, download, and bases.
    revisions = [obj["revision"]["revision"] for obj in data["channel-map"]]
    attributes = [obj["revision"]["attributes"] for obj in data["channel-map"]]
    # TODO: check if there are attributes other than framework and language.

    frameworks = {
        attribute["framework"]
        for attribute in attributes
        if "framework" in attribute and attribute["framework"] != "unknown"
    }
    languages = {
        attribute["language"]
        for attribute in attributes
        if "language" in attribute and attribute["language"] != "unknown"
    }
    now = datetime.datetime.now(datetime.timezone.utc)
    ages = {(now - release_time).days for release_time in release_times}
    return frameworks, languages, set(tracks), set(channels), set(revisions), ages


@click.option("--cache-folder", default=".cache")
@click.command()
def main(cache_folder):
    """Output simple statistics about the charm artifacts."""
    FORMAT = "%(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=FORMAT,
        datefmt="[%X]",
        handlers=[rich.logging.RichHandler()],
    )

    total = 0
    all_frameworks = collections.Counter()
    all_languages = collections.Counter()
    # all_tracks = collections.Counter()
    # all_channels = collections.Counter()
    # all_revisions = collections.Counter()
    min_ages = collections.Counter()
    max_ages = collections.Counter()
    # TODO: figure out what to do with bundles - should I iterate through
    # those instead?
    for entry in iter_repositories(pathlib.Path(cache_folder)):
        metadata = entry / "metadata.yaml"
        charmcraft = entry / "charmcraft.yaml"
        if metadata.exists():
            with metadata.open() as raw:
                name = yaml.safe_load(raw)["name"]
        elif charmcraft.exists():
            with charmcraft.open() as raw:
                try:
                    name = yaml.safe_load(raw)["name"]
                except KeyError:
                    logger.warning("charmcraft.yaml with no name: %s", entry)
                    continue
        else:
            logger.warning("Cannot find name for %s", entry)
            continue
        # TODO: This could be async, since it's doing a bunch of network requests.
        try:
            frameworks, languages, tracks, channels, revisions, ages = charm_details(name)
        except httpx.HTTPStatusError as e:
            logger.warning("Unable to get store info for %s: %s", entry, e)
            continue
        total += 1
        if len(frameworks) > 1:
            logger.warning("%s uses multiple frameworks: %s", entry, frameworks)
        for framework in frameworks:
            all_frameworks[framework] += 1
        if len(languages) > 1:
            logger.warning("%s uses multiple languages: %s", entry, languages)
        for language in languages:
            all_languages[language] += 1
        # TODO: Is there more that would be interesting than just max/min?
        min_ages[min(ages)] += 1
        max_ages[max(ages)] += 1

    report(total, all_frameworks, all_languages, min_ages, max_ages)


def report(total, frameworks, languages, min_ages, max_ages):
    """Output a report of the results to the console."""
    console = rich.console.Console()
    console.print()  # Separate out from any logging.

    table = count_and_percentage_table(
        "Frameworks", "Framework", total, sorted(frameworks.items())
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table("Languages", "Language", total, sorted(languages.items()))
    console.print(table)
    console.print()

    # TODO: probably this and the next one should be bucketed?
    table = count_and_percentage_table("Newest Artifact", "Days", total, sorted(min_ages.items()))
    console.print(table)
    console.print()

    table = count_and_percentage_table("Oldest Artifact", "Days", total, sorted(max_ages.items()))
    console.print(table)
    console.print()

    # TODO:
    # Do something with tracks, channels, revisions.


if __name__ == "__main__":
    main()
