#! /usr/bin/env python3

"""Utility to bulk run tox commands across charm repositories."""

import asyncio
import contextlib
import logging
import pathlib
import re
import shutil
import subprocess
import tomllib

import click
import packaging.requirements
import rich.logging

logger = logging.getLogger(__name__)


def _patch_requirements_file(
    requirements: pathlib.Path, ops_source: str, ops_source_branch: str | None
):
    """Replace a dependency in a requirements file and return the original
    content."""
    original = []
    adjusted = []
    with requirements.open() as req:
        for line in req:
            original.append(line)
            line = line.split("#", 1)[0].strip()
            # TODO: There must be a proper way to tokenise the requirements file?
            if not line or line.startswith("--hash"):
                continue
            # TODO: This should probably attempt to handle someone having
            # operator from git, at least with canonical/operator, or maybe
            # anything with the name "operator" (presumably most forks keep
            # the name).
            if line.startswith("git+https://"):
                logger.debug("Not considering %s in requirements patching %s", line, requirements)
                adjusted.append(line)
                continue
            try:
                req = packaging.requirements.Requirement(line)
            except packaging.requirements.InvalidRequirement:
                logger.error("Unable to understand requirement %r in %s", line, requirements)
                continue
            if req.name != "ops":
                adjusted.append(line)
    if ops_source_branch:
        adjusted.append(f"\ngit+{ops_source}@{ops_source_branch}\n")
    else:
        adjusted.append(f"\ngit+{ops_source}\n")
    with requirements.open("w") as req:
        req.write("\n".join(adjusted))
    return "".join(original)


@contextlib.contextmanager
def patch_ops(location: pathlib.Path, ops_source: str, ops_source_branch: str | None):
    requirements = location / "requirements.txt"
    pyproject = location / "pyproject.toml"
    if requirements.exists():
        original = _patch_requirements_file(requirements, ops_source, ops_source_branch)
        # Annoyingly, sometimes there's also a requirements-*.txt file that also
        # has an ops dependency, so patch those as well.
        extras = {}
        for fn in requirements.glob("requirements-*.txt"):
            extras[fn] = _patch_requirements_file(fn, ops_source, ops_source_branch)
        try:
            yield requirements
        finally:
            with requirements.open("w") as req:
                req.write(original)
            for fn, content in extras.items():
                with fn.open("w") as req:
                    req.write(content)
    elif pyproject.exists():
        with pyproject.open() as data:
            original = data.read()
        poetry_lock = location / "poetry.lock"
        if poetry_lock.exists():
            with poetry_lock.open() as data:
                original_poetry_lock = data.read()
        else:
            original_poetry_lock = None
        with pyproject.open("rb") as data:
            adjusted = tomllib.load(data)
        # TODO: Fix this so that something like ops-x" doesn't match.
        adjusted["dependencies"] = [
            req for req in adjusted.get("dependencies", ()) if not req.startswith("ops")
        ]
        if ops_source_branch:
            ops = f'{{ git = "{ops_source}", branch = "{ops_source_branch}" }}'
        else:
            ops = f'{{ git = "{ops_source}" }}'
        adjusted["dependencies"].append(f"\nops = {ops}")
        # Some charms require running poetry lock after this change.
        if "poetry" in adjusted.get("tool", {}):
            subprocess.run(["poetry", "lock"], cwd=location, stdout=subprocess.PIPE)
        try:
            yield pyproject
        finally:
            with pyproject.open("w") as req:
                req.write(original)
            if original_poetry_lock is not None:
                with poetry_lock.open("w") as lock:
                    lock.write(original_poetry_lock)
    else:
        raise NotImplementedError(
            f"Only know how to patch requirements.txt and pyproject.toml (in {location})"
        )


async def run_tox(location: pathlib.Path, environment: str | None, results: list):
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
        if stdout.strip():
            # This is potentially useful, but it's so verbose that it just
            # pollutes the log. It's simpler to just re-run tox manually in that
            # repo to get the output.
            # TODO: There must be a better solution here, like sending it to
            # somewhere it can be accessed in a handy format?
            pass
        #            logger.info("tox failed:%s in %s: %r", environment, location, stdout)
        if stderr.strip():
            logger.error("Errors from tox:%s in %s: %r", environment, location, stderr)
    # TODO: Should do more than this - does everything have a coverage report?
    # Can we get totals reliably?
    passed = tox.returncode == 0
    if passed:
        logger.info("%s: passed", location)
    else:
        logger.warning("%s did not pass", location)
    results.append({"passed": passed})


async def super_tox(
    conf,
    cache_folder: pathlib.Path,
    environment: str,
    workers: int,
    ops_source: str,
    ops_source_branch: str | None,
    repo_re: str,
    fresh_tox: bool,
):
    """Run `tox -e {environment}` in each repository's folder."""
    results = []
    queue = asyncio.Queue()
    for repo in cache_folder.iterdir():
        if not re.match(repo_re, repo.name, re.IGNORECASE):
            logger.info("Skipping %s - doesn't match specified pattern", repo)
            continue
        if not (repo / "tox.ini").exists():
            continue
        if fresh_tox:
            tox_cache = repo / ".tox"
            if tox_cache.exists():
                shutil.rmtree(str(tox_cache))
        queue.put_nowait((repo, environment, results))

    tasks = []
    for i in range(workers):
        task = asyncio.create_task(
            worker(f"worker-{i}", queue, conf, ops_source, ops_source_branch)
        )
        tasks.append(task)
    await queue.join()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks)
    success_count = 0
    for result in results:
        try:
            if not result["passed"]:
                continue
        except (TypeError, asyncio.CancelledError) as e:
            logger.error("Task was cancelled: %s", e, exc_info=True)
            continue
        except Exception as e:
            logger.error("Unable to process result: %s", e, exc_info=True)
            continue
        success_count += 1
    print(f"{success_count} out of {len(results)} ({success_count/len(results)}%) runs passed.")


async def worker(name, queue, conf, ops_source: str, ops_source_branch: str | None):
    logger.info("Starting worker: %s", name)
    ignore = conf.get("ignore", {})
    while True:
        repo, environment, results = await queue.get()
        if repo.name in ignore.get("expensive", ()):
            logger.info("Skipping %s - too expensive", repo)
            continue
        elif repo.name in ignore.get("manual", ()):
            logger.info("Skipping %s - requires manual intervention", repo)
            continue
        elif repo.name in ignore.get("requirements", ()):
            logger.info("Skipping %s - cannot install dependencies", repo)
            continue
        elif repo.name in ignore.get("not_ops", ()):
            logger.info("Skipping %s - does not use ops", repo)
            continue
        elif repo.name in ignore.get("misc", ()):
            logger.info("Skipping %s - in misc ignore list", repo)
            continue

        if (
            (repo / "bundle.yaml").exists()
            # TODO: We really don't want to have this special case here, but
            # the repo is laid out as a bundle but doesn't have the .yaml file,
            # perhaps because there's only one charm.
            or repo.name == "kserve-operators"
        ):
            repos = (repo / "charms").iterdir()
        else:
            repos = [repo]

        for repo in repos:
            # TODO: This specific case really shouldn't be here, but this repo
            # is different than all the others, and I'm not sure how best to
            # make it generic.
            if repo.name == "catalogue-k8s-operator":
                repo = repo / "charm"
            try:
                if ops_source:
                    with patch_ops(repo, ops_source, ops_source_branch):
                        await run_tox(repo, environment, results)
                else:
                    await run_tox(repo, environment, results)
            except Exception as e:
                logger.error("Failed running tox: %s", e, exc_info=True)
        queue.task_done()


@click.option("--workers", default=3, type=click.IntRange(1))
@click.option("--cache-folder", default=".cache")
@click.option(
    "-c",
    "--config",
    type=click.Path(exists=True, dir_okay=False, path_type=pathlib.Path),
    default=pathlib.Path("super-tox.toml"),
)
@click.option("--ops-source", default="https://github.com/canonical/operator")
@click.option("--ops-source-branch", type=click.STRING, default=None)
@click.option(
    "--log-level",
    default="info",
    type=click.Choice(["debug", "info", "warning", "error", "criticial"], case_sensitive=False),
)
@click.option("--repo", default=".*")
@click.option("--fresh-tox/--no-fresh-tox", default=False)
@click.option("-e", default=None, type=click.STRING)
@click.command()
def main(
    cache_folder: str,
    e: str,
    workers: int,
    ops_source: str,
    ops_source_branch: str | None,
    log_level: str,
    repo: str,
    fresh_tox: bool,
    config: pathlib.Path,
):
    """Run the specified tox environment in all of the charm repositories.

    Assumes that `tox` is installed and available on the current path.
    """

    format = "%(relativeCreated)d %(message)s"
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format=format,
        datefmt="[%X]",
        handlers=[rich.logging.RichHandler()],
    )

    with config.open("rb") as raw:
        conf = tomllib.load(raw)

    asyncio.run(
        super_tox(
            conf,
            pathlib.Path(cache_folder),
            e,
            workers,
            ops_source,
            ops_source_branch,
            repo,
            fresh_tox,
        )
    )


if __name__ == "__main__":
    main()
