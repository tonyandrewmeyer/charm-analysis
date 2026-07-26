#! /usr/bin/env python3

"""Summarise what charms do in __init__ (directly or indirectly)."""

import ast
import builtins
import collections
import logging
import operator
import pathlib

import click
import rich.console
from helpers import configure_logging, count_and_percentage_table, iter_entries, parse_module

logger = logging.getLogger(__name__)

#: Only show calls made by more than this many charms in the per-charm table.
MIN_CHARMS_PER_CALL = 2
#: Only show names assigned more than this many times in the assignment table.
MIN_ASSIGNMENTS = 10

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def _charm_base_names(tree: ast.Module) -> set[str]:
    """Return the local names that refer to ops.CharmBase in this module.

    This handles aliased imports, such as ``from ops import CharmBase as CB``
    or ``from ops.charm import CharmBase``.
    """
    names = {"CharmBase"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module not in ("ops", "ops.charm"):
            continue
        for alias in node.names:
            if alias.name == "CharmBase":
                names.add(alias.asname or alias.name)
    return names


def _find_charm_class(tree: ast.Module) -> ast.ClassDef | None:
    """Return the AST of the first class that subclasses ops.CharmBase."""
    names = _charm_base_names(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id in names:
                return node
            if isinstance(base, ast.Attribute) and base.attr == "CharmBase":
                # We assume that this is ops.CharmBase (or ops.charm.CharmBase).
                return node
    return None


def _find_method(class_node: ast.ClassDef | None, name: str) -> FunctionNode | None:
    """Return the method with the given name defined directly on the class.

    Only the class body is searched, so that (for example) an ``__init__`` in a
    class nested inside the charm class is not mistaken for the charm's own.
    """
    if class_node is None:
        return None
    for node in class_node.body:
        if isinstance(node, FunctionNode) and node.name == name:
            return node
    return None


def _find_function(tree: ast.Module, name: str) -> FunctionNode | None:
    """Return the module level function with the given name."""
    for node in tree.body:
        if isinstance(node, FunctionNode) and node.name == name:
            return node
    return None


class OperationCounter(ast.NodeVisitor):
    """Count the operations performed by a chunk of charm code.

    Calls to functions and methods defined in the same module are followed, so
    that work done indirectly is also counted.
    """

    def __init__(self, module: ast.Module, charm: ast.ClassDef | None):
        self.module = module
        self.charm = charm
        self.calls: collections.Counter[str] = collections.Counter()
        self.constants: collections.Counter[object] = collections.Counter()
        self.names: collections.Counter[str] = collections.Counter()
        self.raises: collections.Counter[str] = collections.Counter()
        self.asserts: collections.Counter[str] = collections.Counter()
        self.explicit_return: collections.Counter[str] = collections.Counter()
        # The functions currently being visited, so that (mutually) recursive
        # code does not send us into an infinite loop.
        self._in_progress: list[FunctionNode] = []

    def visit_function(self, func: FunctionNode) -> None:
        """Count the operations performed by the given function."""
        self._recurse_into(func, func.name)

    def _recurse_into(self, func: FunctionNode, description: str) -> None:
        """Visit the body of a called function, unless we are already in it."""
        if any(func is seen for seen in self._in_progress):
            logger.debug("Not recursing into %s again", description)
            return
        logger.info("Recursing into %s", description)
        self._in_progress.append(func)
        try:
            self.generic_visit(func)
        finally:
            self._in_progress.pop()

    def visit_Call(self, node: ast.Call):
        # Record the call without its arguments, e.g. "self.framework.observe()".
        self.calls[f"{ast.unparse(node.func)}()"] += 1

        # Recurse into the call, simplistically.
        # TODO: It seems like this could be made to recurse into libs,
        # especially when it's instantiating a lib class (so the __init__ of
        # that class).
        if isinstance(node.func, ast.Name):
            if node.func.id in dir(builtins):
                logger.debug("Not recursing into builtin %s", node.func.id)
            elif node.func.id == "observe":
                logger.debug("Not trying to recurse into ops.")
            else:
                sub = _find_function(self.module, node.func.id)
                if sub:
                    self._recurse_into(sub, f"global {node.func.id}")
                else:
                    logger.info("Could not find function %s", ast.unparse(node.func))
        elif isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id in (
                "logger",
                "tempfile",
            ):
                logger.debug("Not trying to recurse into stdlib %s", node.func.value.id)
            elif node.func.attr == "observe":
                logger.debug("Not trying to recurse into ops.")
            elif node.func.attr == "append":
                # An assumption, but seems reasonable.
                logger.debug("Not trying to recurse into list append.")
            elif isinstance(node.func.value, ast.Call) and (
                isinstance(node.func.value.func, ast.Name) and node.func.value.func.id == "super"
            ):
                # e.g. super().__init__(framework): this is the *base* class's
                # method, not the charm's own (recursing here would count the
                # charm's own __init__ a second time).
                logger.debug("Not trying to recurse into super().%s", node.func.attr)
            else:
                sub = _find_method(self.charm, node.func.attr)
                if sub:
                    self._recurse_into(sub, f"method {node.func.attr}")
                else:
                    logger.info("Could not find method %s", ast.unparse(node.func))
        else:
            logger.debug("Unhandled call type: %s", ast.unparse(node.func))

        # Visit the arguments, which may contain calls of their own.
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant):
        # TODO: Is there any practical use for this?
        try:
            self.constants[node.value] += 1
        except TypeError:
            # An unhashable constant shouldn't be possible, but be safe.
            logger.debug("Skipping unhashable constant in %s", ast.unparse(node))

    def _count_assign(self, target: ast.expr):
        """Count an assignment to the given target."""
        if isinstance(target, ast.Name):
            self.names[target.id] += 1
        elif isinstance(target, ast.Attribute):
            self.names[ast.unparse(target)] += 1
        elif isinstance(target, ast.Subscript):
            # Ignore the slice.
            self._count_assign(target.value)
        elif isinstance(target, ast.Starred):
            self._count_assign(target.value)
        elif isinstance(target, ast.Tuple | ast.List):
            for element in target.elts:
                self._count_assign(element)
        else:
            logger.info("Unhandled assignment target: %s", ast.unparse(target))

    def visit_Assign(self, node: ast.Assign):
        for target in node.targets:
            self._count_assign(target)
        # The value may contain calls, e.g. `self.foo = SomeLib(self)`.
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        self._count_assign(node.target)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign):
        self._count_assign(node.target)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr):
        # A walrus, e.g. `if (container := self.unit.get_container(...)):`.
        self._count_assign(node.target)
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise):
        self.raises[ast.unparse(node.exc) if node.exc else "raise"] += 1
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert):
        self.asserts[ast.unparse(node.test)] += 1
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return):
        self.explicit_return[ast.unparse(node.value) if node.value else "None"] += 1
        self.generic_visit(node)


def walk_init(path: pathlib.Path) -> OperationCounter | None:
    """Walk through the __init__ method and summarise what it does.

    Using the ast module, walk through the __init__ method and get counts for
    each of the operations that it executes, recursively walking through any
    function that's called.

    Returns None if the charm's __init__ could not be found or parsed.
    """
    tree = parse_module(path)
    if tree is None:
        return None
    charm = _find_charm_class(tree)
    if not charm:
        logger.warning("Could not find a CharmBase subclass in %s", path)
        return None
    init = _find_method(charm, "__init__")
    if not init:
        logger.warning("Could not find __init__ in %s, %s", path, charm.name)
        return None
    counter = OperationCounter(tree, charm)
    counter.visit_function(init)
    return counter


@click.option("--cache-folder", default=".cache", help="Path to the charm cache.")
@click.command()
def main(cache_folder: str) -> None:
    """Output simple statistics about the charm's __init__ code."""
    configure_logging()

    total = 0
    analysed = 0
    calls: collections.Counter[str] = collections.Counter()
    calls_per_charm: collections.Counter[str] = collections.Counter()
    names: collections.Counter[str] = collections.Counter()
    raises: collections.Counter[str] = collections.Counter()
    asserts: collections.Counter[str] = collections.Counter()
    explicit_return: collections.Counter[str] = collections.Counter()
    for entry in iter_entries(pathlib.Path(cache_folder)):
        total += 1
        counter = walk_init(entry)
        if counter is None:
            continue
        analysed += 1
        calls.update(counter.calls)
        # Each call is counted at most once per charm here.
        calls_per_charm.update(counter.calls.keys())
        names.update(counter.names)
        raises.update(counter.raises)
        asserts.update(counter.asserts)
        explicit_return.update(counter.explicit_return)

    report(total, analysed, calls, calls_per_charm, names, raises, asserts, explicit_return)


def _most_common(counts: collections.Counter[str], minimum: int = 0) -> list[tuple[str, int]]:
    """Return the counts above the minimum, most common first."""
    return sorted(
        ((k, v) for k, v in counts.items() if v > minimum),
        key=operator.itemgetter(1),
        reverse=True,
    )


def report(
    total: int,
    analysed: int,
    calls: collections.Counter[str],
    calls_per_charm: collections.Counter[str],
    names: collections.Counter[str],
    raises: collections.Counter[str],
    asserts: collections.Counter[str],
    explicit_return: collections.Counter[str],
) -> None:
    """Output a report of the results to the console."""
    console = rich.console.Console()
    console.print()  # Separate out from any logging.
    console.print(f"Examined {total} charms ({analysed} with an __init__ found).\n")

    # Percentages are relative to the number of charms that we were able to
    # analyse, rather than every charm that we looked at.
    tables: list[tuple[str, str, list[tuple[str, int]]]] = [
        # Subprocess and Pebble calls here are probably red flags.
        # TODO: Can we make red flags red in the table?
        ("__init__ calls", "Function Name", _most_common(calls)),
        (
            "__init__ calls (per charm)",
            "Function Name",
            _most_common(calls_per_charm, MIN_CHARMS_PER_CALL),
        ),
        # Setting app or unit status here is probably a red flag.
        ("__init__ assignment", "Name", _most_common(names, MIN_ASSIGNMENTS)),
        ("__init__ raises", "Name", _most_common(raises)),
        ("__init__ asserts", "Name", _most_common(asserts)),
        # It would be more useful if this ignored the return value (presumably
        # always None) and instead just counted the number of returns or listed
        # the charms that had one, or something like that.
        ("__init__ explicit returns", "Return Value", _most_common(explicit_return)),
    ]
    for title, col0_title, counts in tables:
        console.print(count_and_percentage_table(title, col0_title, analysed, counts))
        console.print()


if __name__ == "__main__":
    main()
