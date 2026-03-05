#! /usr/bin/env python3

"""Utility to bulk run tox commands across charm repositories."""

import ast
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
    filter_framework: typing.Optional[str]


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


# Imports that indicate use of a given testing framework.
# For simple cases, any import of the module name is sufficient.
FRAMEWORK_IMPORTS = {
    "scenario": {"scenario"},
    "jubilant": {"jubilant"},
}

# Names imported from ops.testing that indicate Scenario usage (as opposed to
# the old Harness framework, which is also in ops.testing).
SCENARIO_OPS_TESTING_NAMES = {
    "Context", "State", "Mount", "Relation",
    "CloudSpec", "Secret", "PeerRelation", "SubordinateRelation",
}

# Package names in requirements files that indicate a framework dependency.
# ops[testing] is handled separately by checking extras on the "ops" package.
FRAMEWORK_DEPS = {
    "scenario": {"ops-scenario"},
    "jubilant": {"jubilant"},
}


def _iter_test_python_files(base: pathlib.Path):
    """Iterate through all .py files in a tests directory."""
    if not base.exists():
        return
    for node in base.iterdir():
        if node.name.startswith("."):
            continue
        if node.is_dir():
            yield from _iter_test_python_files(node)
        elif node.name.endswith(".py"):
            yield node


def _has_framework_import(location: pathlib.Path, framework: str) -> bool:
    """Check if test files in the charm import the specified framework.

    For most frameworks, a simple module-name match is enough. For Scenario,
    we also need to distinguish ``from ops.testing import Context`` (Scenario)
    from ``from ops.testing import Harness`` (old Harness framework).
    """
    simple_modules = FRAMEWORK_IMPORTS.get(framework, set())
    for py_file in _iter_test_python_files(location / "tests"):
        try:
            with py_file.open() as raw:
                tree = ast.parse(raw.read())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in simple_modules:
                        return True
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module in simple_modules:
                    return True
                # Check for Scenario-specific names imported from ops.testing.
                if framework == "scenario" and node.module == "ops.testing":
                    for alias in node.names:
                        if alias.name in SCENARIO_OPS_TESTING_NAMES:
                            return True
    return False


def _req_matches_framework(
    req: packaging.requirements.Requirement, framework: str, dep_names: set
) -> bool:
    """Check if a parsed requirement matches the specified framework."""
    if req.name in dep_names:
        return True
    # ops[testing] provides Scenario.
    if framework == "scenario" and req.name == "ops" and "testing" in req.extras:
        return True
    return False


def _has_framework_dep_in_requirements(req_path: pathlib.Path, framework: str) -> bool:
    """Check if a requirements file contains the specified framework as a dependency."""
    dep_names = FRAMEWORK_DEPS.get(framework, set())
    if not req_path.exists():
        return False
    with req_path.open() as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-") or line.startswith("git+"):
                continue
            try:
                req = packaging.requirements.Requirement(line)
            except packaging.requirements.InvalidRequirement:
                continue
            if _req_matches_framework(req, framework, dep_names):
                return True
    return False


def _has_framework_dep_in_pyproject(pyproject_path: pathlib.Path, framework: str) -> bool:
    """Check if pyproject.toml contains the specified framework as a dependency."""
    dep_names = FRAMEWORK_DEPS.get(framework, set())
    if not pyproject_path.exists():
        return False
    with pyproject_path.open("rb") as f:
        try:
            data = tomllib.load(f)
        except Exception:
            return False
    # Check all dependency lists: top-level, project, project.optional-dependencies,
    # and poetry groups.
    all_dep_strings = list(data.get("dependencies", []))
    all_dep_strings.extend(data.get("project", {}).get("dependencies", []))
    for section_deps in data.get("project", {}).get("optional-dependencies", {}).values():
        all_dep_strings.extend(section_deps)
    # Poetry dependencies.
    poetry = data.get("tool", {}).get("poetry", {})
    all_dep_strings.extend(poetry.get("dependencies", {}).keys())
    for group in poetry.get("group", {}).values():
        all_dep_strings.extend(group.get("dependencies", {}).keys())

    for dep_str in all_dep_strings:
        try:
            req = packaging.requirements.Requirement(dep_str)
        except packaging.requirements.InvalidRequirement:
            continue
        if _req_matches_framework(req, framework, dep_names):
            return True
    return False


def uses_framework(location: pathlib.Path, framework: str) -> bool:
    """Check if a charm uses the specified testing framework.

    Checks dependencies in requirements files first (cheaper), then falls back
    to AST-based import scanning of test files.
    """
    # Check all requirements*.txt and *-requirements.txt files.
    req_files = set(itertools.chain(
        location.glob("requirements*.txt"),
        location.glob("*-requirements.txt"),
    ))
    for req_file in req_files:
        if _has_framework_dep_in_requirements(req_file, framework):
            return True
    if _has_framework_dep_in_pyproject(location / "pyproject.toml", framework):
        return True
    # Fall back to more expensive AST-based import scanning.
    return _has_framework_import(location, framework)


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
    if returncode == 0:
        logger.info("%s: passed", location)
        results.append({"passed": True, "location": location})
    elif returncode == 254:
        logger.info("Skipping %s - no '%s' tox environment", location, environment)
        results.append({
            "skipped": "no_environment",
            "location": location,
            "reason": f"no '{environment}' tox environment",
        })
    else:
        logger.warning("%s did not pass", location)
        results.append({"passed": False, "location": location})


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

    passed = [r for r in results if r.get("passed") is True]
    failed = [r for r in results if r.get("passed") is False]
    skipped = [r for r in results if "skipped" in r]
    ran = [r for r in results if "passed" in r]

    if ran:
        pct = 100 * len(passed) // len(ran)
        print(f"{len(passed)} out of {len(ran)} ({pct}%) runs passed.")
    else:
        print("No tests were run.")

    if skipped:
        skip_reasons = {}
        for r in skipped:
            skip_type = r["skipped"]
            skip_reasons.setdefault(skip_type, []).append(r)
        parts = []
        if "no_framework" in skip_reasons:
            fw = settings.filter_framework
            parts.append(f"{len(skip_reasons['no_framework'])} had no {fw} tests")
        if "no_environment" in skip_reasons:
            parts.append(
                f"{len(skip_reasons['no_environment'])} had no '{environment}' tox environment"
            )
        if "ignored" in skip_reasons:
            parts.append(f"{len(skip_reasons['ignored'])} in ignore list")
        print(f"Skipped {len(skipped)} charms ({', '.join(parts)}).")

    if settings.verbose:
        if failed:
            print("Failed:")
            pprint.pprint([
                str(d["location"].relative_to(settings.cache_folder))
                for d in failed
            ])
        if skipped:
            print("Skipped:")
            for r in skipped:
                loc = str(r["location"].relative_to(settings.cache_folder))
                print(f"  {loc}: {r.get('reason', r['skipped'])}")


IGNORE_REASONS = {
    "expensive": "too expensive",
    "manual": "requires manual intervention",
    "requirements": "cannot install dependencies",
    "not_ops": "does not use ops",
    "misc": "in misc ignore list",
}


async def worker(name, queue, conf):
    logger.info("Starting worker: %s", name)
    ignore = conf.get("ignore", {})
    all_ignored = set(itertools.chain.from_iterable(ignore.values()))
    while True:
        try:
            repo, environment, results = await queue.get()
        except asyncio.CancelledError:
            # We don't need to do anything any more.
            break
        location = str(repo.relative_to(settings.cache_folder))
        ignored = False
        for category, reason in IGNORE_REASONS.items():
            if location in ignore.get(category, ()):
                logger.info("Skipping %r - %s", location, reason)
                results.append({"skipped": "ignored", "location": repo, "reason": reason})
                queue.task_done()
                ignored = True
                break
        if ignored:
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
            if location in all_ignored:
                logger.info("Skipping %r - in ignore list", location)
                results.append({"skipped": "ignored", "location": repo, "reason": "in ignore list"})
                continue
            if settings.filter_framework:
                if not uses_framework(repo, settings.filter_framework):
                    logger.info(
                        "Skipping %s - does not use %s",
                        location,
                        settings.filter_framework,
                    )
                    results.append({
                        "skipped": "no_framework",
                        "location": repo,
                        "reason": f"does not use {settings.filter_framework}",
                    })
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
@click.option(
    "--filter",
    "filter_framework",
    default=None,
    type=click.Choice(["scenario", "jubilant"], case_sensitive=False),
    help="only run for charms that use this testing framework",
)
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
        if settings.filter_framework:
            cmd.append(f"--filter={settings.filter_framework}")
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
