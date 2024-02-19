#! /usr/bin/env python3

"""Utility to bulk clone/update repositories from a CSV file."""

# Single-threaded, 153 repositories:
#
# Cloning:
#   real    9m14.792s
#   user    0m6.284s
#   sys     0m4.172s
# Pulling (all up-to-date already)
#   real    7m39.197s
#   user    0m4.199s
#   sys	    0m2.518s
#
# Coroutines, 153 repositories:
#
# Cloning:
#   real    0m8.815s
#   user    0m5.324s
#   sys     0m3.190s
# Pulling (all up-to-date already)
#   real    0m3.701s
#   user    0m3.757s
#   sys     0m1.608s

import asyncio
import csv
import logging
import os
import pathlib
import typing

import click
import rich.logging

logger = logging.getLogger(__name__)


async def clone(dest_folder: pathlib.Path, name: str, repository: str, branch: str):
    """Ensure that a clone of the repository exists."""
    logger.info("Cloning %s from %s into %s", name, repository, dest_folder.resolve())
    args = [
        "git",
        "clone",
        "--depth=1",
        "--shallow-submodules",
        "--single-branch",
        "--no-tags",
        "--quiet",
    ]
    if branch:
        args.extend(["--branch", branch])
    clone = await asyncio.create_subprocess_exec(
        *args,
        repository,
        cwd=dest_folder.parent.name,
    )
    await clone.wait()
    if clone.returncode != 0:
        logger.error("Could not clone %s from %s", name, repository)


async def pull(dest_folder: pathlib.Path, name: str):
    """Ensure that the clone of the repository is up-to-date."""
    logger.info("Pulling %s in %s", name, dest_folder)
    pull = await asyncio.create_subprocess_exec(
        "git", "pull", "--quiet", cwd=dest_folder.resolve()
    )
    await pull.wait()
    if pull.returncode != 0:
        logger.warning("Could not pull %s", name)


async def process_input(input: csv.DictReader, cache_folder: pathlib.Path):
    """Clone or pull the repositories in the input CSV."""
    async with asyncio.TaskGroup() as tg:
        for row in input:
            if not row or not row["Repository"]:
                continue
            name = row["Charm Name"]
            repository = row["Repository"]
            branch = row.get("Branch (if not the default)")
            # Convert from HTTP to git@ to more easily get private repositories.
            repository = repository.replace("https://github.com/", "git@github.com:")
            base_name = repository.rstrip("/").rsplit("/", 1)[1]
            if branch:
                repo_folder = cache_folder / f"{base_name}-{branch}"
            else:
                repo_folder = cache_folder / base_name
            if repo_folder.exists():
                # We don't do a 'checkout' / 'switch' here, assuming that the
                # user has either not manually adjusted the cache, or that if
                # they have they want it to be that way.
                # TODO: reconisder this - maybe the branch in the input file
                # has changed?
                tg.create_task(pull(repo_folder, name))
            else:
                tg.create_task(clone(repo_folder, name, repository, branch))


@click.option("--cache-folder", default=".cache")
@click.argument("charm-list", type=click.File("rt"))
@click.command()
def main(cache_folder: str, charm_list: typing.TextIO):
    """Ensure updated repositories for all the charms from the provided list.

    If a repository does not exist, clone it, otherwise do a pull. Assumes that
    all the repositories are in an essentially read-only state and so pull will
    run cleanly.

    The `git` CLI tool is used via a subprocess, so must be able to handle any
    authentication required.
    """
    FORMAT = "%(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=FORMAT,
        datefmt="[%X]",
        handlers=[rich.logging.RichHandler()],
    )

    os.makedirs(cache_folder, exist_ok=True)
    input = csv.DictReader(charm_list)
    asyncio.run(process_input(input, pathlib.Path(cache_folder)))


if __name__ == "__main__":
    main()
