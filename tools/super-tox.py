#! /usr/bin/env python3

"""Utility to bulk run tox commands across charm repositories."""

import asyncio
import logging
import pathlib

import click

logger = logging.getLogger(__name__)


async def run_tox(location: pathlib.Path, environment: str | None):
    """Run the specified tox environment in the given path."""
    logger.info("Running %s in %s", environment, location)
    args = ["tox"]
    if environment:
        args.extend(["-e", environment])
    tox = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=location.resolve(),
    )
    stdout, stderr = await tox.communicate()
    if tox.returncode != 0:
        logger.error("Errors from tox:%s in %s: %r", environment, location, stderr)
    # Should do more than this - does everything have a coverage report? Can we get totals reliably?
    logger.info("%s: passed: %s", location, "congratulations :)" in stdout.decode())
    return stdout


async def super_tox(cache_folder: pathlib.Path, environment: str, workers: int):
    """Run `tox -e {environment}` in each repository's folder."""
    queue = asyncio.Queue()
    for repo in cache_folder.iterdir():
        if not (repo / "tox.ini").exists():
            continue
        queue.put_nowait((repo, environment))

    tasks = []
    for i in range(workers):
        task = asyncio.create_task(worker(f"worker-{i}", queue))
        tasks.append(task)
    await queue.join()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def worker(name, queue):
    while True:
        repo, environment = await queue.get()
        await run_tox(repo, environment)
        queue.task_done()


@click.option("--workers", default=3)
@click.option("--cache-folder", default=".cache")
@click.option("-e", default=None, type=click.STRING)
@click.command()
def main(cache_folder: str, e: str, workers: int):
    """Run the specified tox environment in all of the charm repositories.

    Assumes that `tox` is installed and available on the current path.
    """
    logging.basicConfig(level=logging.INFO)
    asyncio.run(super_tox(pathlib.Path(cache_folder), e, workers))


if __name__ == "__main__":
    main()
