#! /usr/bin/env python3

"""Summarise details about the charm code."""

import ast
import collections
import pathlib
import pprint

import click
import yaml


def _normalise_event(event):
    """Drop uninteresting elements like the names of containers."""
    if event.endswith("_pebble_ready"):
        return "pebble_ready"
    elif event.endswith("_action"):
        return "action"
    # Rough but should be sufficient.
    elif "_relation_" in event:
        return "_".join(event.rsplit("_", 2)[1:])
    return event


def observing(module):
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
                yield _normalise_event(arg0.attr)
            elif isinstance(arg0, ast.Name):
                yield _normalise_event(arg0.id)
            elif (
                isinstance(arg0, ast.Call)
                and getattr(arg0.func, "id", "") == "getattr"
                and arg0.args[0].attr == "on"
            ):
                yield _normalise_event(arg0.args[1].value)
            else:
                yield "TODO"


def defer_count(module):
    """Count the number of times that defer() is called."""
    count = 0
    with (module).open() as charm:
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


@click.option("--cache-folder", default=".cache")
@click.command()
def main(cache_folder):
    """Output simple statistics about the charm code."""
    events = collections.Counter()
    defers = collections.Counter()
    reactive = 0
    hooks = 0
    unknown = set()
    bundles = 0
    repos = []
    for repo in pathlib.Path(cache_folder).iterdir():
        if repo.name.startswith("."):
            continue
        if (repo / "bundle.yaml").exists():
            bundles += 1
            for charm in (repo / "charms").iterdir():
                repos.append(charm)
        else:
            repos.append(repo)
    for repo in repos:
        if repo.name.startswith("."):
            continue
        if (repo / "reactive").exists():
            reactive += 1
            # Not sure what to do with these - just ignore for now.
            continue
        elif (repo / "hooks").exists():
            hooks += 1
            # Not sure what to do with these - just ignore for now.
            continue
        # Assume that all the code is in src/charm.py if it exists.
        entry = "src/charm.py"
        if not (repo / entry).exists():
            if (repo / "charmcraft.yaml").exists():
                with (repo / "charmcraft.yaml").open() as charmcraft:
                    data = yaml.safe_load(charmcraft)
                    # Assume all the code is in the entrypoint module.
                    entry = data["parts"]["charm"]["charm-entrypoint"]
            else:
                unknown.add(repo)
            continue
        events.update(observing(repo / entry))
        defers[defer_count(repo / entry)] += 1

    pprint.pprint(events)
    pprint.pprint(defers)
    print(f"Reactive: {reactive}, Hooks: {hooks}, Bundles: {bundles}")
    pprint.pprint(unknown)


if __name__ == "__main__":
    main()
