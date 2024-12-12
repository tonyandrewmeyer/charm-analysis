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
import rich.logging
from helpers import count_and_percentage_table
from helpers import iter_repositories

logger = logging.getLogger(__name__)


def analyse_repo(repo: pathlib.Path, repack: bool):
    """Run 'charmcraft analyse' on the provided repository."""
    logger.info(f"Analysing {repo}")
    results = collections.defaultdict(set)
    # Make sure that the charm is packed.
    if repack or not tuple(repo.glob("*.charm")):
        try:
            subprocess.run(
                ["charmcraft", "pack"], check=True, cwd=repo, capture_output=True
            )
        except subprocess.CalledProcessError as e:
            # It might be interesting to look into these, but it seems out of scope for analyse.
            logger.error(
                "Failed to pack charm: %s: %s (%r/%r)", repo, e, e.stdout, e.stderr
            )
            return results
        # Remove the build environment - otherwise, this ends up using a huge amount
        # of disk space (in /var/snap/lxd).
        subprocess.run(["charmcraft", "clean"], check=True, cwd=repo)
    continuation = None
    for charm in repo.glob("*.charm"):
        # Run the analysis.
        charmcraft = subprocess.run(
            ["charmcraft", "analyse", charm.name], cwd=repo, capture_output=True
        )
        # Annoyingly, there isn't a machine-readable / structured version of the output.
        for line in charmcraft.stderr.decode().splitlines():
            if line.startswith("Linting "):
                continue
            if line.startswith(("Some config-options", "Some action params")):
                # This is a continuation of a previous line, but there doesn't seem to be any way
                # to programmatically determine that.
                if continuation:
                    full_line = f"{continuation[2]} {line}"
                    logger.warning("%s (%s): %s", *continuation[:-1], full_line)
                continue
            if line.startswith((
                "language: Charm language unknown",
                "framework: The charm is not based on any known Framework",
            )):
                logger.info("%s (%s): %s", repo, charm.name, line)
                continue
            mo = re.match(r"(?P<key>[^:]+)\: \[(?P<result>\w+)\] (?P<detail>.*)", line)
            if not mo:
                logger.error(
                    "Couldn't parse analyse output: %s (%s): %s", repo, charm.name, line
                )
                continue
            key = mo.group("key")
            result = mo.group("result")
            if key in ("language", "framework"):
                logger.debug("%s (%s): %s", repo, charm.name, line)
                continue
            if result not in ("NONAPPLICABLE", "OK"):
                if line.startswith("naming-conventions"):
                    # The interesting information is annoyingly on the next line.
                    continuation = (repo, charm.name, line)
                else:
                    continuation = None
                    logger.warning("%s (%s): %s", repo, charm.name, line)
            results[result].add(key)
    return results


@click.option("--cache-folder", default=".cache")
@click.option("--repack/--no-repack", default=False)
@click.option(
    "--log-level",
    default="info",
    type=click.Choice(
        ["debug", "info", "warning", "error", "critical"], case_sensitive=False
    ),
)
@click.command()
def main(
    cache_folder: str,
    repack: bool,
    log_level: typing.Literal["debug", "info", "warning", "error", "critical"],
):
    """Output simple statistics about 'charmcraft analyse' results."""
    FORMAT = "%(message)s"
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format=FORMAT,
        datefmt="[%X]",
        handlers=[rich.logging.RichHandler()],
    )

    total = 0
    overall_results = collections.defaultdict(lambda: collections.Counter())
    repo_count = collections.Counter()
    by_repo = collections.Counter()
    for repo in iter_repositories(pathlib.Path(cache_folder)):
        if repo.name == cache_folder:
            continue
        total += 1
        results = analyse_repo(repo, repack)
        for result, keys in results.items():
            for key in keys:
                overall_results[result][key] += 1
            if result not in ("NONAPPLICABLE", "OK"):
                by_repo[repo.name] += 1
            repo_count[result] += 1
    logger.info("Overall results: %r", overall_results)
    report(
        total,
        repo_count,
        by_repo,
    )


def report(
    total,
    repo_count,
    by_repo,
):
    """Output a report of the results to the console."""
    console = rich.console.Console()
    console.print()  # Separate out from any logging.

    table = count_and_percentage_table(
        "Repositories with at least one at this level",
        "Level",
        total,
        repo_count.items(),
    )
    console.print(table)
    console.print()

    total_problems = [(repo, count) for repo, count in by_repo.items()]
    total_problems.sort(key=operator.itemgetter(1), reverse=True)
    table = count_and_percentage_table(
        "Total warnings or errors", "Repository", total, total_problems
    )
    console.print(table)
    console.print()


if __name__ == "__main__":
    main()
