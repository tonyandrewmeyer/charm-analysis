#! /usr/bin/env python3

"""Utility to bulk run tox commands across charm repositories."""

import asyncio
import contextlib
import dataclasses
import itertools
import logging
import os
import pathlib
import pprint
import re
import shlex
import shutil
import subprocess
import sys
from contextlib import nullcontext

try:
    import tomllib  # type: ignore
except ImportError:
    import tomli as tomllib  # type: ignore
import typing

import click
import packaging.requirements
import rich.logging

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class Settings:
    executable: str
    mode: typing.Literal["local", "lxd", "lxd-per-tox"]
    lxd_name: str
    lxd_image_alias: str
    keep_lxd_instance: bool
    cache_folder: pathlib.Path
    ops_source: str
    ops_source_branch: typing.Optional[str]
    remove_local_changes: bool
    git_pull: bool
    repo_re: str
    fresh_tox: bool
    workers: int
    verbose: bool
    sample: int


settings: Settings = None  # type: ignore


def _patch_requirements_file(requirements: pathlib.Path):
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
            # TODO: Should this be more generic, perhaps assuming that forks
            # will keep the "operator" name and matching any of those?
            # Technically, it ought to handle the optional "#" better too, in
            # case there is some "canonical/operator-x" library.
            # It should also handle "ops @ git+..." as a format.
            if line.startswith("git+https://github.com/canonical/operator"):
                continue
            if line.startswith("git+https://"):
                logger.debug(
                    "Not considering %s in requirements patching %s", line, requirements
                )
                adjusted.append(line)
                continue
            if line.startswith("-r "):
                # We could recursively work through each of these files.
                # In practice, it doesn't seem that any of the charms have ops
                # pulled in via an `-r` directive, however.
                logger.debug(
                    "Ignoring indirect dependencies from %s when patching %s",
                    line,
                    requirements,
                )
                adjusted.append(line)
                continue
            try:
                req = packaging.requirements.Requirement(line)
            except packaging.requirements.InvalidRequirement:
                logger.error(
                    "Unable to understand requirement %r in %s", line, requirements
                )
                continue
            if req.name != "ops":
                adjusted.append(line)
    if settings.ops_source_branch:
        adjusted.append(f"\ngit+{settings.ops_source}@{settings.ops_source_branch}\n")
    else:
        adjusted.append(f"\ngit+{settings.ops_source}\n")
    with requirements.open("w") as req:
        req.write("\n".join(adjusted))
    return "".join(original)


@contextlib.contextmanager
def patch_ops(location: pathlib.Path):
    requirements = location / "requirements.txt"
    pyproject = location / "pyproject.toml"
    if requirements.exists():
        original = _patch_requirements_file(requirements)
        # Annoyingly, sometimes there's also a requirements-*.txt file that also
        # has an ops dependency, so patch those as well.
        # We could perhaps parse the tox.ini file to find the appropriate
        # dependency declarations matching the specified environment (or all
        # environments, if one isn't), but this seems sufficient for now.
        extras = {}
        for fn in itertools.chain(
            requirements.glob("requirements-*.txt"),  # e.g. requirements-unit.txt
            requirements.glob("*-requirements.txt"),  # e.g. test-requirements.txt
            requirements.glob("requirements*.in"),
        ):
            extras[fn] = _patch_requirements_file(fn)
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
        if settings.ops_source_branch:
            ops = f'{{ git = "{settings.ops_source}", branch = "{settings.ops_source_branch}" }}'
        else:
            ops = f'{{ git = "{settings.ops_source}" }}'
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


async def run_tox(
    executable: str,
    location: pathlib.Path,
    environment: typing.Optional[str],
    results: list,
):
    """Run the specified tox environment in the given path."""
    logger.info("Running %s in %s", environment, location)
    args = shlex.split(executable)
    if environment:
        args.extend(["-e", environment])

    if settings.mode == "local":
        tox = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=location.resolve(),
        )
        stdout, stderr = await tox.communicate()
        returncode = tox.returncode
    else:
        # TODO: Handle: config, cache folder, ops patching (higher than this!), fresh tox, lxd name, image alias, keeping instance
        # Provide a partial to the method.
        with lxd_instance(
            f"{settings.lxd_name}-{location.name}",
            delete_on_exit=not settings.keep_lxd_instance,
        ) as lxd:
            returncode, stdout, stderr = lxd.execute(args)
    if returncode != 0:
        if stdout.strip():
            # This is potentially useful, but it's so verbose that it just
            # pollutes the log. It's simpler to just re-run tox manually in that
            # repo to get the output.
            # TODO: There must be a better solution here, like sending it to
            # somewhere it can be accessed in a handy format?
            # logger.info("tox failed:%s in %s: %r", environment, location, stdout)
            pass
        if stderr.strip():
            logger.error("Errors from tox:%s in %s: %r", environment, location, stderr)
    # TODO: Should do more than this - does everything have a coverage report?
    # Can we get totals reliably?
    passed = returncode == 0
    if passed:
        logger.info("%s: passed", location)
    elif returncode == 254:
        logger.info("No %s environment found in %s", environment, location)
    else:
        logger.warning("%s did not pass", location)
    results.append({"passed": passed, "location": location})


async def super_tox(conf, environment: str):
    """Run `tox -e {environment}` in each repository's folder."""
    results = []
    queue = asyncio.Queue()
    end = settings.sample or sys.maxsize
    for repo in itertools.islice(settings.cache_folder.iterdir(), 0, end):
        if not re.match(settings.repo_re, repo.name, re.IGNORECASE):
            logger.info("Skipping %s - doesn't match specified pattern", repo)
            continue
        if not (repo / "tox.ini").exists():
            continue
        if settings.fresh_tox:
            tox_cache = repo / ".tox"
            if tox_cache.exists():
                shutil.rmtree(str(tox_cache))
        if settings.remove_local_changes:
            git = subprocess.Popen(
                ["git", "checkout", "."],
                cwd=repo.resolve(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            out, err = git.communicate()
            logger.debug(
                "`git checkout .` in %s: stdout %r, stderr: %s", repo, out, err
            )
            if git.returncode != 0:
                logger.error("`git checkout .` in %s failed.", repo)
        # Unlike `checkout .`, `pull` connects to the remote, so it might be
        # better to put this in the async worker, rather than blocking starting
        # off all the jobs to do it. Alternatively, maybe get rid of this option
        # and just make use of `tools/get_charms.py`, which already knows how to
        # do this very quickly.
        if settings.git_pull:
            git = subprocess.Popen(
                ["git", "pull"],
                cwd=repo.resolve(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            out, err = git.communicate()
            logger.debug("`git pull` in %s: stdout %r, stderr: %s", repo, out, err)
            if git.returncode != 0:
                logger.error("`git pull` in %s failed: %s", repo, err)
        queue.put_nowait((repo, environment, results))

    tasks = []
    for i in range(settings.workers):
        task = asyncio.create_task(worker(f"worker-{i}", queue, conf))
        tasks.append(task)
    await queue.join()
    for task in tasks:
        task.cancel()

    await asyncio.gather(*tasks)
    results.sort(key=lambda d: d["location"])
    success_count = 0

    for result in results:
        try:
            if not result["passed"]:
                continue
        # FIXME these exceptions never happen...
        except (TypeError, asyncio.CancelledError) as e:
            logger.error("Task was cancelled: %s", e, exc_info=True)
            continue
        except Exception as e:
            logger.error("Unable to process result: %s", e, exc_info=True)
            continue
        success_count += 1
    pct = 100 * success_count // len(results)
    print(f"{success_count} out of {len(results)} ({pct}%) runs passed.")
    if settings.verbose:
        print("Failed for these repos:")
        pprint.pprint(
            [
                str(d["location"].relative_to(settings.cache_folder))
                for d in results
                if not d["passed"]
            ]
        )


async def worker(name, queue, conf):
    logger.info("Starting worker: %s", name)
    ignore = conf.get("ignore", {})
    while True:
        try:
            repo, environment, results = await queue.get()
        except asyncio.CancelledError:
            # We don't need to do anything any more.
            break
        location = str(repo.relative_to(settings.cache_folder))
        if location in ignore.get("expensive", ()):
            logger.info("Skipping %r - too expensive", location)
            queue.task_done()
            continue
        elif location in ignore.get("manual", ()):
            logger.info("Skipping %r - requires manual intervention", location)
            queue.task_done()
            continue
        elif location in ignore.get("requirements", ()):
            logger.info("Skipping %r - cannot install dependencies", location)
            queue.task_done()
            continue
        elif location in ignore.get("not_ops", ()):
            logger.info("Skipping %r - does not use ops", location)
            queue.task_done()
            continue
        elif location in ignore.get("misc", ()):
            logger.info("Skipping %r - in misc ignore list", location)
            queue.task_done()
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
            location = str(repo.relative_to(settings.cache_folder))
            if location in sum(ignore.values(), []):
                logger.info("Skipping %r", location)
                continue
            if settings.mode == "local":
                try:
                    with patch_ops(repo) if settings.ops_source else nullcontext():
                        await run_tox(settings.executable, repo, environment, results)
                except Exception as e:
                    logger.error("Failed running tox: %s", e, exc_info=True)
        queue.task_done()


def get_lxd_instance(name: str, image_alias: str, create: bool = False):
    """Get a local lxd instance by the specified name.

    If create is True, then if there is no matching instance, then create one
    using the provided image alias."""
    import pylxd  # type: ignore

    client = pylxd.Client()
    try:
        return client.instances.get(name)
    except pylxd.exceptions.NotFound:
        if not create:
            raise
    logger.info("Creating LXD instance %s (%s)", name, image_alias)
    config = {"name": name, "source": {"type": "image", "alias": image_alias}}
    return client.instances.create(config, wait=True)


def lxd_exists(instance, filename: str) -> bool:
    """Return true iff the filename exists on the instance."""
    return instance.execute(["stat", filename]).exit_code == 0


def sync_to_lxd(instance, source: pathlib.Path, destination: str):
    """Recursively copy the source to the destination inside of the instance."""
    # TODO: This assumes that there aren't edges cases like the path exists
    # in the instance, but it's a file there and a directory locally.
    for item in source.iterdir():
        item_destination = os.path.join(destination, item.name)
        if not lxd_exists(instance, str(item)):
            # We can just copy it.
            instance.files.recursive_put(str(item), item_destination)
        elif item.is_file():
            # If it's a file, we can just replace it.
            instance.files.delete(item_destination)
            instance.files.put(str(item), item_destination)
        else:
            sync_to_lxd(instance, item, item_destination)


@contextlib.contextmanager
def lxd_instance(name: str, *, delete_on_exit: bool = True):
    instance = get_lxd_instance(name, settings.lxd_image_alias, create=True)
    try:
        if instance.status != "Running":
            logger.info("Starting lxd instance %s", instance.name)
            instance.start()
        # TODO: Ideally, the two copies would really be syncs, to speed things
        # up when the instance already exists, but without missing any changes
        # since the initial copy.
        logger.info("Copying charm-analysis to the instance")
        me = pathlib.Path(__file__).parent.parent
        sync_to_lxd(instance, me, "charm-analysis")
        # Copy the cache to the instance.
        if not settings.cache_folder.is_relative_to(me):
            logger.info("Copying cache to the instance")
            sync_to_lxd(instance, settings.cache_folder, "charm-analysis/.cache")
        yield instance
    finally:
        if instance.status == "Running":
            logger.info("Stopping lxd instance %s", instance.name)
            instance.stop()
        logger.info("Instance status: %s", instance.status)
        if delete_on_exit:
            logger.info("Deleting lxd instance %s", instance.name)
            instance.delete()


def fixme(f):
    def inner(*args, **kwargs):
        print(args, kwargs)
        return f(*args, **kwargs)

    return inner


@click.option("--workers", default=1, type=click.IntRange(1))
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
    type=click.Choice(
        ["debug", "info", "warning", "error", "critical"], case_sensitive=False
    ),
)
@click.option(
    "--mode",
    default="local",
    type=click.Choice(["local", "lxd", "lxd-per-tox"], case_sensitive=False),
)
@click.option("--lxd-name", default="super-tox")
@click.option("--lxd-image-alias", default="ubuntu-22.04")
@click.option("--keep-lxd-instance/--no-keep-lxd-instance", default=False)
@click.option("--git-pull/--no-git-pull", default=False)
@click.option("--remove-local-changes/--no-remove-local-changes", default=False)
@click.option("--repo", default=".*")
@click.option("--fresh-tox/--no-fresh-tox", default=False)
@click.option("-e", default=None, type=click.STRING)
@click.option("--verbose/--no-verbose", default=False, help="additional output")
@click.option("--sample", default=0, help="try to run only this many repositories")
@click.option("--executable", default="tox")
@click.command()
def main(
    cache_folder: str,
    e: str,
    log_level: str,
    repo: str,
    config: pathlib.Path,
    **kwargs,
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

    global settings
    settings = Settings(**kwargs, cache_folder=pathlib.Path(cache_folder), repo_re=repo)

    if settings.mode in ("local", "lxd-per-tox"):
        with config.open("rb") as raw:
            conf = tomllib.load(raw)

        asyncio.run(super_tox(conf, e))
    elif settings.mode == "lxd":
        cmd = [
            "/charm-analysis/super-tox.py",
            f"--config={config}",
            f"--cache-folder={settings.cache_folder}",  # TODO: This needs to adjust in some cases.
            "-e",
            e,
            f"--workers={settings.workers}",
            f"--ops-source={settings.ops_source}",
            f"repo={settings.repo_re}",
            f"--log-level={log_level}",
        ]
        if settings.ops_source_branch:
            cmd.append(f"--ops-source-branch={settings.ops_source_branch}")
        if settings.fresh_tox:
            cmd.append("--fresh-tox")
        if settings.remove_local_changes:
            cmd.append("--remove-local-changes")
        if settings.git_pull:
            cmd.append("--git-pull")
        with lxd_instance(
            settings.lxd_name,
            delete_on_exit=not settings.keep_lxd_instance,
        ) as lxd:
            exit_code, stdout, stderr = lxd.execute(cmd)
        print(stdout, file=sys.stdout)
        print(stderr, file=sys.stderr)
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
