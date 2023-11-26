"""Utilities common across multiple tools."""

import logging
import pathlib

import yaml

logger = logging.getLogger(__name__)


def _iter_bundles(base: pathlib.Path):
    """Automatically traverse the charms in a bundle."""
    for repo in pathlib.Path(base).iterdir():
        if repo.name.startswith("."):
            continue
        if (repo / "bundle.yaml").exists():
            logger.info("Unbundling %s", repo)
            yield from (repo / "charms").iterdir()
        else:
            yield repo


def iter_repositories(base: pathlib.Path):
    """Iterate through all the charm folders contained in the base location."""
    for repo in _iter_bundles(base):
        if (repo / "reactive").exists():
            logger.info("Ignoring reactive charm: %s", repo)
            continue
        elif (repo / "hooks").exists():
            logger.info("Ignoring hook charm: %s", repo)
            continue
        yield repo


def iter_entries(base: pathlib.Path):
    """Iterate through all the charm entry points contained in the base location."""
    for repo in iter_repositories(base):
        entry = "src/charm.py"
        if (repo / "charmcraft.yaml").exists():
            with (repo / "charmcraft.yaml").open() as charmcraft:
                data = yaml.safe_load(charmcraft)
                # For now, (wrongly) assume all the code is in the entrypoint module.
                try:
                    entry = data["parts"]["charm"]["charm-entrypoint"]
                except KeyError:
                    pass
        if not (repo / entry).exists():
            logger.warning("Unable to find entrypoint for %s (guessed %s).", repo, entry)
            continue
        yield (repo / entry)
