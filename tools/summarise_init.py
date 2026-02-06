#! /usr/bin/env python3

"""Summarise what charms do in __init__ (directly or indirectly)."""

import ast
import collections
import copy
import logging
import operator
import pathlib

import click
import rich.console
from helpers import configure_logging
from helpers import count_and_percentage_table
from helpers import iter_entries

logger = logging.getLogger(__name__)


def _get_root_tree(path):
    with path.open() as file:
        return ast.parse(file.read(), path.name)


def _find_charm_ast(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                if isinstance(base, ast.Name) and base.id == "CharmBase":
                    return node
                if isinstance(base, ast.Attribute) and base.attr == "CharmBase":
                    # We assume that this is ops.CharmBase.
                    return node
    return None


def _find_func_by_name(tree, name):
    logger.debug("Searching for %s in %s", name, tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


class OperationCounter(ast.NodeVisitor):
    def __init__(self, tree: ast.AST):
        self.tree = tree
        self.calls = collections.Counter()
        self.constants = collections.Counter()
        self.names = collections.Counter()
        self.raises = collections.Counter()
        self.asserts = collections.Counter()
        self.explicit_return = collections.Counter()

    def visit_Call(self, node: ast.Call):
        node = copy.deepcopy(node)
        node.args.clear()
        node.keywords.clear()
        self.calls[ast.unparse(node)] += 1

        # Recurse into the call, simplistically.
        # TODO: It seems like this could be made to recurse into libs,
        # especially when it's instantiating a lib class (so the __init__ of
        # that class).
        if isinstance(node.func, ast.Name):
            if node.func.id in dir(__builtins__):
                logger.debug("No recursing into builtin %s", node.func.id)
            elif node.func.id == "observe":
                logger.debug("Not trying to recurse into ops.")
            else:
                sub = _find_func_by_name(self.tree, node.func.id)
                if sub:
                    logger.info("Recursing into global %s", node.func.id)
                    self.visit(sub)
                else:
                    logger.info("Could not find function %s", ast.unparse(node.func))
        elif isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                if node.func.value.id in ("logger", "tempfile"):
                    logger.debug(
                        "Not trying to recurse into stdlib %s", node.func.value.id
                    )
                elif node.func.attr == "observe":
                    logger.debug("Not trying to recurse into ops.")
                elif node.func.attr == "append":
                    # An assumption, but seems reasonable.
                    logger.debug("Not trying to recurse into list append.")
                else:
                    charm = _find_charm_ast(self.tree)
                    sub = _find_func_by_name(charm, node.func.attr)
                    if sub:
                        logger.info("Recursing into method %s", node.func.attr)
                        self.visit(sub)
                    else:
                        logger.info("Could not find method %s", ast.unparse(node.func))
            else:
                logger.warning("Unknown attribute value type: %s", node.func.value)
        else:
            logger.warning("Unknown call type: %s", node.func)

    def visit_Constant(self, node: ast.Constant):
        # TODO: Is there any practical use for this?
        self.constants[node.value] += 1

    def _count_assign(self, target):
        if isinstance(target, ast.Name):
            self.names[target.id] += 1
        elif isinstance(target, ast.Attribute):
            self.names[ast.unparse(target)] += 1
        elif isinstance(target, ast.Subscript):
            # Ignore the slice.
            if isinstance(target.value, ast.Name):
                self.names[target.value.id] += 1
            elif isinstance(target.value, ast.Attribute):
                self.names[ast.unparse(target.value)] += 1
            else:
                logger.error("Unknown subscript value type: %s", target.value)
        else:
            logger.error("Unknown target type: %s", target)

    def visit_Assign(self, node: ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Tuple):
                for subtarget in target.elts:
                    self._count_assign(subtarget)
            else:
                self._count_assign(target)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        self._count_assign(node.target)

    def visit_Raise(self, node: ast.Raise):
        self.raises[node.exc] += 1

    def visit_Assert(self, node: ast.Assert):
        self.asserts[node.test] += 1

    def visit_Return(self, node: ast.Return):
        self.explicit_return[node.value] += 1


def walk_init(path):
    """Walk through the __init__ method and summarise what it does.

    Using the ast module, walk through the __init__ method and get counts for
    each of the operations that it executes, recursively walking through any
    function that's called.
    """
    tree = _get_root_tree(path)
    charm = _find_charm_ast(tree)
    if not charm:
        logger.warning("Could not find a CharmBase subclass in %s", path)
        return
    init = _find_func_by_name(charm, "__init__")
    if not init:
        logger.warning("Could not find __init__ in %s, %s", path, charm)
        return None
    counter = OperationCounter(charm)
    counter.visit(init)
    return counter


@click.option("--cache-folder", default=".cache")
@click.command()
def main(cache_folder):
    """Output simple statistics about the charm's __init__ code."""
    configure_logging()

    total = 0
    calls = collections.Counter()
    calls_per_charm = collections.Counter()
    names = collections.Counter()
    raises = collections.Counter()
    asserts = collections.Counter()
    explicit_return = collections.Counter()
    for entry in iter_entries(pathlib.Path(cache_folder)):
        total += 1
        counter = walk_init(entry)
        if counter:
            calls.update(counter.calls)
            for call in counter.calls:
                calls_per_charm[call] += 1
            names.update(counter.names)
            raises.update(counter.raises)
            asserts.update(counter.asserts)
            explicit_return.update(counter.explicit_return)

    report(total, calls, calls_per_charm, names, raises, asserts, explicit_return)


def report(total, calls, calls_per_charm, names, raises, asserts, explicit_return):
    """Output a report of the results to the console."""
    console = rich.console.Console()
    console.print()  # Separate out from any logging.
    console.print(f"Examined {total} charms.\n")

    # Subprocess and Pebble calls here are probably red flags.
    # TODO: Can we make red flags red in the table?
    table = count_and_percentage_table(
        "__init__ calls",
        "Function Name",
        total,
        sorted(
            [(k, v) for k, v in calls.items() if v > 0],
            key=operator.itemgetter(1),
            reverse=True,
        ),
    )
    console.print(table)
    console.print()

    # Subprocess and Pebble calls here are probably red flags.
    # TODO: Can we make red flags red in the table?
    table = count_and_percentage_table(
        "__init__ calls (per charm)",
        "Function Name",
        total,
        sorted(
            [(k, v) for k, v in calls_per_charm.items() if v > 2],
            key=operator.itemgetter(1),
            reverse=True,
        ),
    )
    console.print(table)
    console.print()
    # Setting app or unit status here is probably a red flag.
    # TODO: Can we make red flags red in the table?
    table = count_and_percentage_table(
        "__init__ assignment",
        "Name",
        total,
        sorted(
            [(k, v) for k, v in names.items() if v > 10],
            key=operator.itemgetter(1),
            reverse=True,
        ),
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "__init__ raises",
        "Name",
        total,
        sorted(raises.items(), key=operator.itemgetter(1), reverse=True),
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "__init__ asserts",
        "Name",
        total,
        sorted(asserts.items(), key=operator.itemgetter(1), reverse=True),
    )
    console.print(table)
    console.print()

    # This currently has no values. If it did, then it would be more useful if
    # it ignored the return value (presumably always None) and instead just
    # counted the number of returns or listed the charms that had one, or
    # something like that.
    table = count_and_percentage_table(
        "__init__ explicit returns",
        "Return Value",
        total,
        sorted(explicit_return.items(), key=operator.itemgetter(1), reverse=True),
    )
    console.print(table)
    console.print()


if __name__ == "__main__":
    main()
