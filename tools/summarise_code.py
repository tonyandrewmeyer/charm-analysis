#! /usr/bin/env python3

"""Summarise details about the charm code."""

import ast
import collections
import csv
import logging
import pathlib
import typing

import click
import rich.console
from helpers import configure_logging, count_and_percentage_table, iter_entries, parse_module

logger = logging.getLogger(__name__)

#: Placeholder used when the event or handler cannot be statically determined.
UNKNOWN = "TODO"


def _normalise_event(event: str) -> str:
    """Drop uninteresting elements like the names of containers."""
    if event.endswith("_pebble_ready"):
        return "pebble_ready"
    elif event.endswith("_action"):
        return "action"
    # Rough but should be sufficient.
    elif "_relation_" in event:
        return "_".join(event.rsplit("_", 2)[1:])
    return event


def _event_name(node: ast.expr) -> str | None:
    """Return the name of the event referred to by an `observe` first argument.

    Returns None if the event cannot be determined statically.
    """
    if isinstance(node, ast.Attribute):
        # e.g. self.on.start
        return node.attr
    if isinstance(node, ast.Name):
        # e.g. start (imported or a local alias)
        return node.id
    if isinstance(node, ast.Call):
        # e.g. getattr(self.on, f"{name}_pebble_ready")
        func = node.func
        is_getattr = (isinstance(func, ast.Name) and func.id == "getattr") or (
            isinstance(func, ast.Attribute) and func.attr == "getattr"
        )
        if is_getattr and len(node.args) >= 2:
            attr = node.args[1]
            if isinstance(attr, ast.Constant) and isinstance(attr.value, str):
                return attr.value
    if isinstance(node, ast.Subscript):
        # e.g. self.on[f"{name}_relation_changed"]
        index = node.slice
        if isinstance(index, ast.Constant) and isinstance(index.value, str):
            return index.value
    return None


def _handler_name(node: ast.expr) -> str | None:
    """Return the name of the handler passed as an `observe` second argument."""
    if isinstance(node, ast.Attribute):
        # e.g. self._on_start
        return node.attr
    if isinstance(node, ast.Name):
        # e.g. a module level function, or a local variable.
        return node.id
    return None


def observing(tree: ast.Module) -> typing.Iterator[tuple[str, str]]:
    """Iterate through the (event, handler) pairs that a charm is observing."""
    # Assume that any calls to a method called "observe" are framework.observe
    # calls (both `self.framework.observe` and a bare `observe` alias).
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr != "observe":
                continue
        elif isinstance(func, ast.Name):
            if func.id != "observe":
                continue
        else:
            continue
        if len(node.args) < 2:
            # Not an `observe(event, handler)` call after all (or the arguments
            # are being unpacked, which we cannot follow).
            logger.debug("Skipping observe() call with %s args", len(node.args))
            continue
        event = _event_name(node.args[0])
        handler = _handler_name(node.args[1])
        if event is None:
            logger.debug("Could not determine observed event: %s", ast.unparse(node))
        yield (
            _normalise_event(event) if event is not None else UNKNOWN,
            handler if handler is not None else UNKNOWN,
        )


def defer_count(tree: ast.Module) -> int:
    """Count the number of times that defer() is called in the module."""
    # Assume that all calls to a method called "defer" are event.defer()s.
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "defer"
    )


def _iter_functions(
    tree: ast.AST,
) -> typing.Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Iterate through every function and method defined in the tree."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node


def relation_broken(tree: ast.Module, module: pathlib.Path, handler_name: str) -> None:
    """Log what a relation-broken handler does with the relation.

    Handlers that use `relation.id` or the `.relation` attribute are of
    interest, because relation-broken events need particular care.
    """
    logger.info("%s has a relation-broken event handler, %s", module, handler_name)
    for func in _iter_functions(tree):
        if func.name == handler_name:
            body = func.body
            break
    else:
        logger.info("Couldn't find %s in %s", handler_name, module)
        return
    for expr in body:
        for node in ast.walk(expr):
            # Is this sufficient to check what we need to know?
            if isinstance(node, ast.Attribute):
                if node.attr == "id":
                    logger.info("Found x.id in relation-broken handler.")
                elif node.attr == "relation":
                    logger.info("Found .relation in relation-broken handler.")


def _load_teams(team_info: typing.TextIO) -> dict[str, str]:
    """Load a mapping of repository name to owning team from a CSV file."""
    teams: dict[str, str] = {}
    for row in csv.DictReader(team_info):
        repository = (row.get("Repository") or "").strip()
        if not repository:
            continue
        # The repository is normally a URL, but a bare name works as well.
        repo = repository.rstrip("/").rsplit("/", 1)[-1]
        teams[repo] = row.get("Team") or "Unknown"
    return teams


@click.option("--cache-folder", default=".cache", help="Path to the charm cache.")
@click.option(
    "--log-defer-over",
    default=10,
    help="Log details for charms with more than this many defer() calls.",
)
@click.option(
    "--team-info",
    default=None,
    type=click.File(),
    help="CSV file mapping each Repository to a Team.",
)
@click.command()
def main(cache_folder: str, log_defer_over: int, team_info: typing.TextIO | None) -> None:
    """Output simple statistics about the charm code."""
    configure_logging()

    # TODO: This won't work with bundles or monorepos.
    teams = _load_teams(team_info) if team_info else {}

    total = 0
    events: collections.Counter[str] = collections.Counter()
    defers: collections.Counter[int] = collections.Counter()
    defers_by_team: collections.Counter[str] = collections.Counter()
    for entry in iter_entries(pathlib.Path(cache_folder)):
        total += 1
        tree = parse_module(entry)
        repo_events: dict[str, str] = {}
        if tree is not None:
            # This will have some collisions - e.g. all actions get normalised
            # to a single `event`, relation events are normalised, etc. Each
            # event is counted at most once per charm.
            repo_events = dict(observing(tree))
            events.update(repo_events.keys())
            if "relation_broken" in repo_events:
                relation_broken(tree, entry, repo_events["relation_broken"])

        # Count defer() calls across every module next to the entry point,
        # parsing each module only once.
        defers_per_module = {}
        for module in sorted(entry.parent.glob("**/*.py")):
            module_tree = parse_module(module)
            if module_tree is None:
                continue
            defers_per_module[module] = defer_count(module_tree)
        total_defers = sum(defers_per_module.values())
        # TODO: This assumes the entry is in a "src" (or otherwise named) folder.
        defers_by_team[teams.get(entry.parent.parent.name, "Unknown")] += total_defers

        if total_defers > log_defer_over:
            logger.info("%s has %s defer() calls", entry, total_defers)
            for module, module_count in defers_per_module.items():
                if module_count:
                    logger.info("%s: %s defer() calls", module, module_count)
        defers[total_defers] += 1

    report(total, events, defers, defers_by_team)


def report(
    total: int,
    events: collections.Counter[str],
    defers: collections.Counter[int],
    defers_by_team: collections.Counter[str],
) -> None:
    """Output a report of the results to the console."""
    console = rich.console.Console()
    console.print()  # Separate out from any logging.
    console.print(f"Examined {total} charms.\n")

    # There's probably a more interesting way to show this.
    table = count_and_percentage_table("Events", "Event", total, sorted(events.items()))
    console.print(table)
    console.print()

    # Fill in the zeros.
    freq = [(i, defers[i]) for i in range(max(defers, default=0) + 1)]
    table = count_and_percentage_table("event.defer() Frequency", "Frequency", total, freq)
    table.add_section()
    # The number of charms that call defer() at least once (the sum of all the
    # rows would simply be the number of charms examined).
    charms_with_defers = sum(count for freq_, count in defers.items() if freq_)
    pct = f"{(charms_with_defers / total * 100):.1f}" if total else "N/A"
    table.add_row("Total", str(charms_with_defers), pct)
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "Team",
        "Defer Count",
        sum(defers_by_team.values()),
        sorted(defers_by_team.items()),
    )
    console.print(table)
    console.print()

    # TODO:
    # * Presumably there's a lot more analysis that could be done here!


if __name__ == "__main__":
    main()
