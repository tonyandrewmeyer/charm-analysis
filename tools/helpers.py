"""Utilities common across multiple tools."""

import itertools
import logging
import pathlib

import rich.logging
import rich.table
import yaml

logger = logging.getLogger(__name__)


def configure_logging():
    """Set up logging with RichHandler, used by all CLI tools."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[rich.logging.RichHandler()],
    )


def _iter_monorepo(base: pathlib.Path):
    """Iterate through each of the charms contained in a single repository."""
    for repo in pathlib.Path(base).iterdir():
        if repo.name.startswith("."):
            continue
        # We don't have a marker for "monorepo of charms", as we do with a
        # bundle, and we don't want to manually mark entries as monorepos, so
        # we have to use a heuristic here to decide if this is a monorepo.
        # For now, we'll assume that if there is either a "metadata.yaml" or
        # "charmcraft.yaml" file inside of the subfolder, then it's a charm.
        if (repo / "charmcraft.yaml").exists() or (repo / "metadata.yaml").exists():
            logger.info("Found %s in presumed monorepo %s", repo.name, base)
            yield repo
        # We'll also look for "bundle.yaml" in case there's a bundle inside of
        # a monorepo.
        if (repo / "bundle.yaml").exists():
            logger.info("Found bundle %s in presumed monorepo %s", repo.name, base)
            yield from _iter_bundles(repo)


def _iter_non_monorepo(base: pathlib.Path):
    """Iterate through charms in the top level folder."""
    if (base / "bundle.yaml").exists():
        yield from _iter_bundles(base)
    else:
        yield base


def _iter_bundles(base: pathlib.Path):
    """Automatically traverse the charms in a bundle."""
    logger.info("Unbundling %s", base)
    yield from (base / "charms").iterdir()


def iter_repositories(base: pathlib.Path):
    """Iterate through all the charm folders contained in the base location."""
    sources = itertools.chain(_iter_non_monorepo(base), _iter_monorepo(base))
    for repo in sources:
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


def iter_python_src(base: pathlib.Path):
    """Iterate through all the Python modules contained a 'src' folder in the base location."""
    for repo in iter_repositories(base):
        yield from repo.glob("src/**/*.py")


def count_and_percentage_table(title, col0_title, total, counts):
    """Return a rich.table.Table that has a count and percentage columns."""
    table = rich.table.Table(title=title)
    table.add_column(col0_title)
    table.add_column("Count")
    table.add_column("Percentage")
    for label, count in counts:
        pct = f"{(count / total * 100):.1f}" if total else "N/A"
        table.add_row(str(label), str(count), pct)
    return table
