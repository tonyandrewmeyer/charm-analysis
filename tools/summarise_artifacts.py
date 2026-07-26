#! /usr/bin/env python3

"""Summarise information about the charm artifacts on CharmHub."""

import collections
import concurrent.futures
import datetime
import functools
import logging
import pathlib
import time
from typing import Any, NamedTuple

import click
import httpx
import rich.console
from helpers import configure_logging, count_and_percentage_table, iter_repositories, load_yaml

logger = logging.getLogger(__name__)

STORE_API = "https://api.snapcraft.io/v2/charms/info/{name}"
# TODO: Figure out what other fields might be interesting.
STORE_FIELDS = "channel-map,result.store-url"

# Status codes that are worth retrying: rate limiting and transient server
# side problems.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3
MAX_BACKOFF = 30.0
REQUEST_TIMEOUT = 20.0


class CharmDetails(NamedTuple):
    """The details of a single charm, as published on CharmHub."""

    frameworks: set[str]
    languages: set[str]
    tracks: set[str]
    channels: set[str]
    revisions: set[int]
    ages: set[int]
    """The age, in days, of each release in the channel map."""


def charm_name(repo: pathlib.Path) -> str | None:
    """Return the name of the charm in the given folder.

    ``metadata.yaml`` takes precedence over ``charmcraft.yaml``, but if it does
    not provide a name (or cannot be read) then ``charmcraft.yaml`` is used.
    Returns None (having logged a warning) if no name can be found.
    """
    for filename in ("metadata.yaml", "charmcraft.yaml"):
        path = repo / filename
        if not path.exists():
            continue
        data = load_yaml(path)
        if data is None:
            # load_yaml has already logged why.
            continue
        name = data.get("name")
        if isinstance(name, str) and name:
            return name
        logger.info("%s does not provide a charm name", path)
    logger.warning("Cannot find name for %s", repo)
    return None


def _backoff(response: httpx.Response | None, attempt: int) -> float:
    """Return how long to wait before retrying, honouring Retry-After."""
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(float(retry_after), MAX_BACKOFF)
            except ValueError:
                # It may be an HTTP date rather than a number of seconds; fall
                # back to the regular backoff rather than parsing it.
                logger.debug("Unable to parse Retry-After: %r", retry_after)
    return min(2.0**attempt, MAX_BACKOFF)


def fetch_charm_info(client: httpx.Client, name: str) -> dict[str, Any] | None:
    """Request the CharmHub information for a charm.

    Network problems, rate limiting, and unexpected responses are logged and
    result in None being returned, rather than being fatal.
    """
    url = STORE_API.format(name=name)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response: httpx.Response | None = None
        try:
            response = client.get(url, params={"fields": STORE_FIELDS})
        except httpx.HTTPError as e:
            logger.warning("Request for %s failed: %r", name, e)
        else:
            if response.status_code == 404:
                logger.info("%s does not seem to be published on CharmHub", name)
                return None
            if response.status_code not in RETRY_STATUSES:
                if response.is_error:
                    logger.warning("CharmHub returned %s for %s", response.status_code, name)
                    return None
                try:
                    data = response.json()
                except ValueError as e:
                    logger.warning("CharmHub returned invalid JSON for %s: %s", name, e)
                    return None
                if not isinstance(data, dict):
                    logger.warning("CharmHub returned unexpected data for %s", name)
                    return None
                return data
            logger.info(
                "CharmHub returned %s for %s (attempt %s of %s)",
                response.status_code,
                name,
                attempt,
                MAX_ATTEMPTS,
            )
        if attempt < MAX_ATTEMPTS:
            time.sleep(_backoff(response, attempt))
    logger.warning("Giving up on %s after %s attempts", name, MAX_ATTEMPTS)
    return None


def charm_details(client: httpx.Client, name: str) -> CharmDetails | None:
    """Collect information about a charm from CharmHub.

    Returns None if the charm could not be found, or the information could not
    be retrieved or understood - in all cases the problem is logged rather than
    raised.
    """
    data = fetch_charm_info(client, name)
    if data is None:
        return None
    result = data.get("result")
    if isinstance(result, dict) and "store-url" in result:
        logger.info("The store URL for %s is %s", name, result["store-url"])
    channel_map = data.get("channel-map")
    if not isinstance(channel_map, list):
        logger.warning("No channel map provided for %s", name)
        return None

    frameworks: set[str] = set()
    languages: set[str] = set()
    tracks: set[str] = set()
    channels: set[str] = set()
    revisions: set[int] = set()
    ages: set[int] = set()
    now = datetime.datetime.now(datetime.UTC)
    for obj in channel_map:
        if not isinstance(obj, dict):
            logger.warning("Unexpected channel map entry for %s: %r", name, obj)
            continue
        # Channel also has "base" and "name".
        channel = obj.get("channel")
        if not isinstance(channel, dict):
            channel = {}
        if "track" in channel:
            tracks.add(channel["track"])
        if "risk" in channel:
            channels.add(channel["risk"])
        released_at = channel.get("released-at")
        if released_at:
            try:
                release_time = datetime.datetime.fromisoformat(released_at)
            except (TypeError, ValueError):
                logger.warning("Cannot parse release time for %s: %r", name, released_at)
            else:
                if release_time.tzinfo is None:
                    release_time = release_time.replace(tzinfo=datetime.UTC)
                ages.add((now - release_time).days)
        # Revision also has "version" (what is the difference with 'revision'?),
        # created-at, download, and bases.
        revision = obj.get("revision")
        if not isinstance(revision, dict):
            revision = {}
        if "revision" in revision:
            revisions.add(revision["revision"])
        # TODO: check if there are attributes other than framework and language.
        attributes = revision.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}
        if attributes.get("framework", "unknown") != "unknown":
            frameworks.add(attributes["framework"])
        if attributes.get("language", "unknown") != "unknown":
            languages.add(attributes["language"])

    return CharmDetails(frameworks, languages, tracks, channels, revisions, ages)


@click.option("--cache-folder", default=".cache")
@click.option(
    "--concurrency",
    default=8,
    show_default=True,
    help="Number of simultaneous requests to make to CharmHub.",
)
@click.command()
def main(cache_folder: str, concurrency: int):
    """Output simple statistics about the charm artifacts."""
    configure_logging()

    total = 0
    failed = 0
    all_frameworks: collections.Counter[str] = collections.Counter()
    all_languages: collections.Counter[str] = collections.Counter()
    min_ages: collections.Counter[int] = collections.Counter()
    max_ages: collections.Counter[int] = collections.Counter()

    # TODO: figure out what to do with bundles - should I iterate through
    # those instead?
    charms: list[tuple[pathlib.Path, str]] = []
    for entry in iter_repositories(pathlib.Path(cache_folder)):
        name = charm_name(entry)
        if name is None:
            continue
        charms.append((entry, name))

    if not charms:
        logger.warning("No charms found in %s", cache_folder)
        report(total, all_frameworks, all_languages, min_ages, max_ages)
        return

    # The requests are independent of each other, and dominated by waiting for
    # CharmHub, so run a handful of them at a time.
    workers = max(1, min(concurrency, len(charms)))
    with (
        httpx.Client(timeout=REQUEST_TIMEOUT) as client,
        concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool,
    ):
        details = list(
            pool.map(
                functools.partial(charm_details, client),
                (name for _, name in charms),
            )
        )

    for (entry, _), charm in zip(charms, details, strict=False):
        if charm is None:
            failed += 1
            continue
        total += 1
        if len(charm.frameworks) > 1:
            logger.warning("%s uses multiple frameworks: %s", entry, charm.frameworks)
        for framework in charm.frameworks:
            all_frameworks[framework] += 1
        if len(charm.languages) > 1:
            logger.warning("%s uses multiple languages: %s", entry, charm.languages)
        for language in charm.languages:
            all_languages[language] += 1
        # TODO: Is there more that would be interesting than just max/min?
        if charm.ages:
            min_ages[min(charm.ages)] += 1
            max_ages[max(charm.ages)] += 1
        else:
            logger.info("%s has no releases with a known age", entry)

    if failed:
        logger.warning("Unable to get store information for %s of %s charms", failed, len(charms))
    report(total, all_frameworks, all_languages, min_ages, max_ages)


def report(
    total: int,
    frameworks: collections.Counter[str],
    languages: collections.Counter[str],
    min_ages: collections.Counter[int],
    max_ages: collections.Counter[int],
) -> None:
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
