#! /usr/bin/env python3

"""Summarise details about the charm code."""

import ast
import collections
import csv
import logging
import pathlib

import click
import rich.console
from helpers import configure_logging
from helpers import count_and_percentage_table
from helpers import iter_entries

logger = logging.getLogger(__name__)


def _normalise_event(event: str):
    """Drop uninteresting elements like the names of containers."""
    if event.endswith("_pebble_ready"):
        return "pebble_ready"
    elif event.endswith("_action"):
        return "action"
    # Rough but should be sufficient.
    elif "_relation_" in event:
        return "_".join(event.rsplit("_", 2)[1:])
    return event


def observing(module: pathlib.Path):
    """Iterate through the events that a charm is observing."""
    with module.open() as charm:
        tree = ast.parse(charm.read())
    # Assume that any calls to a method called "observe" are framework.observe calls.
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or node.func.attr != "observe"
            or not node.args
        ):
            continue
        arg0 = node.args[0]
        if isinstance(arg0, ast.Attribute):
            yield _normalise_event(arg0.attr), node.args[1].attr
        elif isinstance(arg0, ast.Name):
            yield _normalise_event(arg0.id), node.args[1].attr
        elif (
            isinstance(arg0, ast.Call)
            and getattr(arg0.func, "id", "") == "getattr"
            and arg0.args[0].attr == "on"
        ):
            yield _normalise_event(arg0.args[1].value), node.args[1].attr
        else:
            yield "TODO", "TODO"


def defer_count(module: pathlib.Path):
    """Count the number of times that defer() is called."""
    count = 0
    with module.open() as charm:
        tree = ast.parse(charm.read())
    # Assume that all calls to a function called "defer" are event.defer()s.
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or node.func.attr != "defer"
        ):
            continue
        count += 1
    return count


def relation_broken(module: pathlib.Path, handler_name: str):
    with module.open() as charm:
        logger.info("%s has a relation-broken event handler, %s", module, handler_name)
        tree = ast.parse(charm.read())
        # Walk through the tree to get to the methods we want - there are much better ways
        # to do this.
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == handler_name:
                body = node.body
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


@click.option("--cache-folder", default=".cache")
@click.option("--log-defer-over", default=10)
@click.option("--team-info", default=None, type=click.File())
@click.command()
def main(cache_folder, log_defer_over, team_info):
    """Output simple statistics about the charm code."""
    configure_logging()

    # TODO: This won't work with bundles or monorepos.
    teams = {}
    if team_info:
        reader = csv.DictReader(team_info)
        for row in reader:
            if not row["Repository"]:
                continue
            repo = row["Repository"].rsplit("/", 1)[1]
            teams[repo] = row["Team"]

    total = 0
    events = collections.Counter()
    defers = collections.Counter()
    defers_by_team = collections.Counter()
    for entry in iter_entries(pathlib.Path(cache_folder)):
        total += 1
        # This will have some collisions - e.g. all actions get normalised to a
        # single `event`, relation events are normalised, etc.
        repo_events = {event: method for event, method in observing(entry)}
        events.update(repo_events.keys())
        if "relation_broken" in repo_events:
            relation_broken(entry, repo_events["relation_broken"])
        total_defers = sum(defer_count(module) for module in entry.parent.glob("**/*.py"))
        # TODO: This assumes the entry is in a "src" (or otherwise named) folder.
        defers_by_team[teams.get(entry.parent.parent.name, "Unknown")] += total_defers

        if total_defers > log_defer_over:
            logger.info("%s has %s defer() calls", entry, total_defers)
            for module in entry.parent.glob("**/*.py"):
                module_count = defer_count(module)
                if module_count:
                    logger.info("%s: %s defer() calls", module, module_count)
        defers[total_defers] += 1

    report(total, events, defers, defers_by_team)


def report(total, events, defers, defers_by_team):
    """Output a report of the results to the console."""
    console = rich.console.Console()
    console.print()  # Separate out from any logging.

    # There's probably a more interesting way to show this.
    table = count_and_percentage_table("Events", "Event", total, sorted(events.items()))
    console.print(table)
    console.print()

    # Fill in the zeros.
    freq = [(i, defers[i]) for i in range(max(defers) + 1)]
    table = count_and_percentage_table("event.defer() Frequency", "Frequency", total, freq)
    table.add_section()
    defer_total = sum(defers.values())
    pct = f"{(defer_total / total * 100):.1f}" if total else "N/A"
    table.add_row("Total", str(defer_total), pct)
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "Team", "Defer Count", sum(defers_by_team.values()), defers_by_team.items()
    )
    console.print(table)
    console.print()

    # TODO:
    # * Presumably there's a lot more analysis that could be done here!


if __name__ == "__main__":
    main()
