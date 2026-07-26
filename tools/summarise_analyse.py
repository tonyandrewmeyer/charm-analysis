#! /usr/bin/env python3

"""Produce simple statistics on the results of 'charmcraft analyse'."""

import collections
import logging
import operator
import pathlib
import re
import subprocess
import typing

import click
import rich.console
from helpers import configure_logging, count_and_percentage_table, iter_repositories

logger = logging.getLogger(__name__)

#: Levels that don't indicate a problem with the charm.
NOT_A_PROBLEM = ("NONAPPLICABLE", "OK")

#: 'charmcraft analyse' output lines, e.g. "entrypoint: [OK] Entrypoint found".
RESULT_RE = re.compile(r"(?P<key>[^:]+): \[(?P<result>\w+)\] (?P<detail>.*)")


def pack_repo(repo: pathlib.Path, repack: bool) -> bool:
    """Ensure that the charm in the repository is packed.

    Returns False if the charm could not be packed.
    """
    if not repack and tuple(repo.glob("*.charm")):
        return True
    try:
        subprocess.run(["charmcraft", "pack"], check=True, cwd=repo, capture_output=True)
    except FileNotFoundError:
        logger.error("charmcraft is not installed, so %s cannot be packed.", repo)
        return False
    except subprocess.CalledProcessError as e:
        # It might be interesting to look into these, but it seems out of scope for analyse.
        logger.error("Failed to pack charm: %s: %s (%r/%r)", repo, e, e.stdout, e.stderr)
        return False
    # Remove the build environment - otherwise, this ends up using a huge amount
    # of disk space (in /var/snap/lxd).
    subprocess.run(["charmcraft", "clean"], check=False, cwd=repo)
    return True


def analyse_repo(repo: pathlib.Path, repack: bool) -> dict[str, set[str]]:
    """Run 'charmcraft analyse' on the provided repository.

    Returns a mapping of result level (e.g. "OK", "WARNING") to the set of
    checks that had that result, across all the charms in the repository.
    """
    logger.info("Analysing %s", repo)
    results: dict[str, set[str]] = collections.defaultdict(set)
    if not pack_repo(repo, repack):
        return results
    # A previous line that we know is continued on the following line.
    continuation: tuple[str, str] | None = None
    for charm in repo.glob("*.charm"):
        # Run the analysis.
        try:
            charmcraft = subprocess.run(
                ["charmcraft", "analyse", charm.name], cwd=repo, capture_output=True
            )
        except FileNotFoundError:
            logger.error("charmcraft is not installed, so %s cannot be analysed.", repo)
            return results
        # Annoyingly, there isn't a machine-readable / structured version of the output.
        for line in charmcraft.stderr.decode(errors="replace").splitlines():
            if line.startswith("Linting "):
                continue
            if line.startswith(("Some config-options", "Some action params")):
                # This is a continuation of a previous line, but there doesn't seem to be any way
                # to programmatically determine that.
                if continuation:
                    charm_name, prev_line = continuation
                    logger.warning("%s (%s): %s %s", repo, charm_name, prev_line, line)
                    continuation = None
                continue
            if line.startswith(
                (
                    "language: Charm language unknown",
                    "framework: The charm is not based on any known Framework",
                )
            ):
                logger.info("%s (%s): %s", repo, charm.name, line)
                continue
            mo = RESULT_RE.match(line)
            if not mo:
                logger.error("Couldn't parse analyse output: %s (%s): %s", repo, charm.name, line)
                continue
            key = mo.group("key")
            result = mo.group("result")
            if key in ("language", "framework"):
                logger.debug("%s (%s): %s", repo, charm.name, line)
                continue
            if result not in NOT_A_PROBLEM:
                if key == "naming-conventions":
                    # The interesting information is annoyingly on the next line.
                    continuation = (charm.name, line)
                else:
                    continuation = None
                    logger.warning("%s (%s): %s", repo, charm.name, line)
            results[result].add(key)
    return results


LogLevel = typing.Literal["debug", "info", "warning", "error", "critical"]


@click.option("--cache-folder", default=".cache", help="Path to the charm cache.")
@click.option(
    "--repack/--no-repack",
    default=False,
    help="Pack each charm again, even if a .charm file already exists.",
)
@click.option(
    "--log-level",
    default="info",
    type=click.Choice(["debug", "info", "warning", "error", "critical"], case_sensitive=False),
    help="Verbosity of the logging output.",
)
@click.command()
def main(cache_folder: str, repack: bool, log_level: LogLevel) -> None:
    """Output simple statistics about 'charmcraft analyse' results."""
    configure_logging()
    logging.getLogger().setLevel(log_level.upper())

    total = 0
    overall_results: dict[str, collections.Counter[str]] = collections.defaultdict(
        collections.Counter
    )
    repo_count: collections.Counter[str] = collections.Counter()
    problems_by_repo: collections.Counter[str] = collections.Counter()
    for repo in iter_repositories(pathlib.Path(cache_folder)):
        if repo.name == cache_folder:
            continue
        total += 1
        results = analyse_repo(repo, repack)
        for result, keys in results.items():
            for key in keys:
                overall_results[result][key] += 1
            if result not in NOT_A_PROBLEM:
                # Count each failing check, not just each level.
                problems_by_repo[repo.name] += len(keys)
            repo_count[result] += 1
    logger.info("Overall results: %r", {k: dict(v) for k, v in overall_results.items()})
    report(total, repo_count, problems_by_repo)


def report(
    total: int,
    repo_count: collections.Counter[str],
    problems_by_repo: collections.Counter[str],
) -> None:
    """Output a report of the results to the console."""
    console = rich.console.Console()
    console.print()  # Separate out from any logging.
    console.print(f"Examined {total} repositories.\n")

    table = count_and_percentage_table(
        "Repositories with at least one at this level",
        "Level",
        total,
        sorted(repo_count.items()),
    )
    console.print(table)
    console.print()

    # Here the percentage is the share of all the problems found, rather than
    # the share of the repositories.
    table = count_and_percentage_table(
        "Total warnings or errors",
        "Repository",
        sum(problems_by_repo.values()),
        sorted(problems_by_repo.items(), key=operator.itemgetter(1), reverse=True),
    )
    console.print(table)
    console.print()


if __name__ == "__main__":
    main()
