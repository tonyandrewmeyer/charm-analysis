"""Utilities common across multiple tools."""

from __future__ import annotations

import ast
import logging
import pathlib
from collections.abc import Iterable, Iterator
from typing import Any

import rich.logging
import rich.table
import yaml

logger = logging.getLogger(__name__)

DEFAULT_ENTRYPOINT = "src/charm.py"


def configure_logging(level: int = logging.INFO) -> None:
    """Set up logging with RichHandler, used by all CLI tools."""
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[rich.logging.RichHandler()],
    )
    # httpx logs a line for every request, which drowns out everything else
    # when a tool is querying CharmHub or GitHub for 150+ charms.
    logging.getLogger("httpx").setLevel(max(level, logging.WARNING))


class CharmYamlLoader(yaml.SafeLoader):
    """A SafeLoader that is a better fit for charm metadata.

    PyYAML implements YAML 1.1, where unquoted ``on``, ``off``, ``yes``, ``no``
    and ``y``/``n`` resolve to booleans. Charm names, action names, parameter
    names and so on are always strings, so scalar mapping keys are kept exactly
    as they are written in the file, which avoids (for example) a parameter
    called ``on`` turning into ``True`` and never matching
    ``event.params["on"]``.

    Duplicate keys - which PyYAML silently resolves to the last one wins - are
    collected in ``duplicate_keys`` so that callers can report them.
    """

    def __init__(self, stream: Any):
        super().__init__(stream)
        self.duplicate_keys: list[str] = []

    def construct_mapping(self, node: yaml.Node, deep: bool = False) -> dict[Any, Any]:
        """Construct a mapping, keeping scalar keys as strings."""
        if not isinstance(node, yaml.MappingNode):
            return super().construct_mapping(node, deep=deep)
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            if isinstance(key_node, yaml.ScalarNode):
                # ScalarNode.value is always the raw string from the document.
                key = key_node.value
            else:
                key = self.construct_object(key_node, deep=True)
            try:
                duplicate = key in mapping
            except TypeError:
                # An unhashable (e.g. list) key: charm YAML never has these.
                logger.debug("Ignoring unhashable YAML key: %r", key)
                continue
            if duplicate:
                self.duplicate_keys.append(str(key))
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping

    def construct_sequence(self, node: yaml.Node, deep: bool = False) -> list[Any]:
        """Construct a sequence, keeping items that look like booleans as text.

        Sequences in charm metadata are lists of names (the parameters in an
        action's ``required``, for example), so the YAML 1.1 booleans are just
        as wrong here as they are in a mapping key: ``required: [on]`` has to
        stay ``["on"]`` to match the parameter called ``on``.
        """
        items = super().construct_sequence(node, deep=deep)
        if not isinstance(node, yaml.SequenceNode):
            return items
        for index, item_node in enumerate(node.value):
            if isinstance(item_node, yaml.ScalarNode) and isinstance(
                items[index], bool
            ):
                items[index] = item_node.value
        return items


def load_yaml(path: pathlib.Path) -> dict[str, Any] | None:
    """Load a YAML mapping, returning None if it cannot be read or parsed.

    Any problem (missing file, unreadable file, invalid UTF-8, invalid YAML, a
    document that isn't a mapping) is logged rather than raised, because the
    point of these tools is to survey a large number of repositories that we do
    not control. Duplicate keys are reported, but the last one wins, as it does
    in PyYAML (and in charmcraft).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        logger.warning("Unable to decode %s as UTF-8: %s", path, e)
        return None
    except OSError as e:
        logger.warning("Unable to read %s: %s", path, e)
        return None

    loader = CharmYamlLoader(text)
    try:
        data = loader.get_single_data()
    except yaml.YAMLError as e:
        logger.warning("Failed to parse %s: %s", path, e)
        return None
    finally:
        for key in loader.duplicate_keys:
            logger.warning(
                "Duplicate key %r in %s: only the last definition is used.", key, path
            )
        loader.dispose()
    if data is None:
        # An empty document: not a problem, but there's nothing to look at.
        return None
    if not isinstance(data, dict):
        logger.warning("Expected a mapping in %s, but found %s.", path, type(data))
        return None
    return data


def parse_module(module: pathlib.Path) -> ast.Module | None:
    """Parse a Python module, returning None if it cannot be read or parsed.

    The file is read as bytes so that any PEP 263 encoding declaration is
    honoured, rather than assuming that the source is UTF-8. ValueError covers
    source that contains null bytes, which ast.parse rejects separately from a
    SyntaxError.
    """
    try:
        source = module.read_bytes()
    except OSError as e:
        logger.warning("Unable to read %s: %s", module, e)
        return None
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError) as e:
        logger.warning("Failed to parse %s: %s", module, e)
        return None


def _is_charm(folder: pathlib.Path) -> bool:
    """Report whether the folder looks like the root of a single charm.

    We don't have a marker for "monorepo of charms", as we do with a bundle,
    and we don't want to manually mark entries as monorepos, so we have to use
    a heuristic: if there is either a "metadata.yaml" or "charmcraft.yaml" file
    in the folder, then it's a charm.
    """
    return (folder / "charmcraft.yaml").exists() or (folder / "metadata.yaml").exists()


# Folders that never contain a charm we want to analyse, but often contain
# something that looks like one (in particular, the minimal charms that
# integration tests build and deploy).
EXCLUDED_FOLDERS = frozenset({
    "build",
    "dist",
    "docs",
    "examples",
    "lib",
    "node_modules",
    "spread",
    "src",
    "templates",
    "venv",
})

# How far below a repository to look for charms. A monorepo normally puts its
# charms one level down (usually in "charms/"), so this allows for a little
# more nesting than that without walking the whole tree.
MAX_MONOREPO_DEPTH = 3


def _is_excluded(folder: pathlib.Path) -> bool:
    """Report whether the folder should be skipped when looking for charms."""
    name = folder.name
    if name.startswith(".") or name.startswith("__"):
        return True
    if name.lower() in EXCLUDED_FOLDERS:
        return True
    # "tests", "testing", "test-charms", "integration_tests", and friends.
    return "test" in name.lower()


def _iter_subdirectories(base: pathlib.Path) -> Iterator[pathlib.Path]:
    """Iterate over the directories in base, in a stable order."""
    try:
        children = sorted(base.iterdir())
    except OSError:
        logger.warning("Unable to list %s", base)
        return
    for child in children:
        if child.is_dir():
            yield child


def _iter_charms(
    base: pathlib.Path, root: pathlib.Path, depth: int = MAX_MONOREPO_DEPTH
) -> Iterator[pathlib.Path]:
    """Iterate through each of the charms contained in a single repository.

    A charm is yielded rather than descended into, so charms nested inside of
    other charms (for example, a test fixture) are not found. `root` is only
    used for logging: it's the repository that the search started from.
    """
    for child in _iter_subdirectories(base):
        if _is_excluded(child):
            logger.debug("Not looking for charms in %s", child)
            continue
        if _is_charm(child):
            logger.info("Found %s in presumed monorepo %s", child.name, root)
            yield child
        # We'll also look for "bundle.yaml" in case there's a bundle inside of
        # a monorepo.
        elif (child / "bundle.yaml").exists():
            logger.info("Found bundle %s in presumed monorepo %s", child.name, root)
            yield from _iter_bundles(child)
        elif depth > 1:
            yield from _iter_charms(child, root, depth - 1)


def _iter_children(base: pathlib.Path) -> Iterator[pathlib.Path]:
    """Iterate through the charms in each of the repositories in the base folder."""
    for repo in _iter_subdirectories(base):
        if repo.name.startswith("."):
            continue
        if _is_charm(repo):
            yield repo
        elif (repo / "bundle.yaml").exists():
            yield from _iter_bundles(repo)
        else:
            # Presumably a monorepo: look for the charms inside of it.
            yield from _iter_charms(repo, repo)


def _iter_bundles(base: pathlib.Path) -> Iterator[pathlib.Path]:
    """Automatically traverse the charms in a bundle."""
    charms_dir = base / "charms"
    if not charms_dir.exists():
        logger.warning("Bundle %s has no charms/ directory", base)
        return
    logger.info("Unbundling %s", base)
    try:
        children = sorted(charms_dir.iterdir())
    except OSError:
        logger.warning("Unable to list %s", charms_dir)
        return
    for child in children:
        if child.name.startswith(".") or not child.is_dir():
            continue
        yield child


def iter_repositories(base: pathlib.Path) -> Iterator[pathlib.Path]:
    """Iterate through all the charm folders contained in the base location.

    The base location is normally the cache folder holding all of the cloned
    repositories, but may also be a single charm, or a bundle.
    """
    base = pathlib.Path(base)
    if (base / "bundle.yaml").exists():
        sources: Iterable[pathlib.Path] = _iter_bundles(base)
    elif _is_charm(base):
        # The base location is itself a single charm.
        sources = [base]
    else:
        sources = _iter_children(base)
    for repo in sources:
        if (repo / "reactive").exists():
            logger.info("Ignoring reactive charm: %s", repo)
            continue
        elif (repo / "hooks").exists():
            logger.info("Ignoring hook charm: %s", repo)
            continue
        yield repo


def charm_entrypoint(repo: pathlib.Path) -> pathlib.Path | None:
    """Return the entry point of the charm, or None if it cannot be found.

    The entry point defaults to ``src/charm.py``, but charmcraft.yaml may
    specify a different one.
    """
    entry = DEFAULT_ENTRYPOINT
    charmcraft = repo / "charmcraft.yaml"
    if charmcraft.exists():
        # If the file can't be loaded, fall back to the default entry point
        # rather than skipping the charm entirely: the charm nearly always uses
        # the default.
        data = load_yaml(charmcraft)
        if data is not None:
            # For now, (wrongly) assume all the code is in the entrypoint module.
            try:
                entry = data["parts"]["charm"]["charm-entrypoint"]
            except (KeyError, TypeError):
                pass
    if not isinstance(entry, str):
        logger.warning("Invalid charm-entrypoint in %s: %r", repo, entry)
        entry = DEFAULT_ENTRYPOINT
    if not (repo / entry).exists():
        logger.warning("Unable to find entrypoint for %s (guessed %s).", repo, entry)
        return None
    return repo / entry


def iter_entries(base: pathlib.Path) -> Iterator[pathlib.Path]:
    """Iterate through all the charm entry points contained in the base location."""
    for repo in iter_repositories(base):
        entry = charm_entrypoint(repo)
        if entry is not None:
            yield entry


def iter_python_src(base: pathlib.Path) -> Iterator[pathlib.Path]:
    """Iterate through all the Python modules contained a 'src' folder in the base location."""
    for repo in iter_repositories(base):
        yield from repo.glob("src/**/*.py")


def count_and_percentage_table(
    title: str,
    col0_title: str,
    total: int | None,
    counts: Iterable[tuple[object, int]],
) -> rich.table.Table:
    """Return a rich.table.Table that has a count and percentage columns.

    Pass None (or zero) as the total when the counts aren't out of a total, and
    the percentage column will be filled with "N/A".
    """
    table = rich.table.Table(title=title)
    table.add_column(col0_title)
    table.add_column("Count", justify="right")
    table.add_column("Percentage", justify="right")
    for label, count in counts:
        pct = f"{(count / total * 100):.1f}" if total else "N/A"
        table.add_row(str(label), str(count), pct)
    return table
