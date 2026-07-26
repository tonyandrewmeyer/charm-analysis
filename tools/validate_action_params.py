#!/usr/bin/env python3

"""Validate that event.params accesses match action definitions.

This script checks that all parameters accessed via event.params in charm
action handlers are properly defined in actions.yaml or charmcraft.yaml.

Accesses via ``[]`` are reported as errors (the charm will raise KeyError at
runtime if the parameter is not supplied), accesses via ``.get()`` are reported
as warnings, and anything that cannot be resolved statically (spread operators,
handlers that are not plain ``self`` methods, and so on) is flagged for manual
review.

Exit status:
    0: no errors were found (and no warnings, when --strict is used).
    1: at least one error was found (or a warning, when --strict is used).
    2: the cache folder does not exist.
"""

import ast
import collections
import csv
import dataclasses
import logging
import pathlib
import sys
from typing import Any, Literal, TextIO

import click
import rich.console
import rich.table
from helpers import configure_logging, iter_repositories, load_yaml, parse_module

logger = logging.getLogger(__name__)

AccessType = Literal["subscript", "get", "spread"]

#: Suffix that ops adds to the event name for every action.
ACTION_EVENT_SUFFIX = "_action"

#: Names that are conventionally the instance/class argument of a method.
_INSTANCE_ARG_NAMES = frozenset({"self", "cls"})


@dataclasses.dataclass
class ParamDef:
    """Definition of an action parameter."""

    name: str
    type: str | None = None
    required: bool = False
    default: Any | None = None


@dataclasses.dataclass
class ActionDefinition:
    """Definition of a charm action."""

    name: str
    params: dict[str, ParamDef] = dataclasses.field(default_factory=dict)
    required_params: set[str] = dataclasses.field(default_factory=set)
    #: None means that additionalProperties was not explicitly set in the YAML.
    additional_properties: bool | None = None
    source_file: str = ""


@dataclasses.dataclass
class ParamAccess:
    """A parameter access in code."""

    #: The name of the parameter, or None for a spread (``**event.params``).
    param_name: str | None
    access_type: AccessType
    line_number: int
    #: True when a missing parameter would not raise, i.e. for ``.get()``.
    is_safe: bool


@dataclasses.dataclass
class HandlerInfo:
    """Information about a discovered action handler."""

    handler_name: str
    #: The candidate action names for the observed event, most likely first.
    action_names: tuple[str, ...]
    observe_file: pathlib.Path
    #: True if observed as self.handler (rather than a bare function).
    is_self_method: bool

    @property
    def action_name(self) -> str:
        """The most likely name of the action that this handler serves."""
        return self.action_names[0]


@dataclasses.dataclass
class Violation:
    """A validation error."""

    action_name: str
    handler_name: str
    param_name: str
    access_type: str
    line_number: int
    file_path: str
    message: str
    additional_properties: bool | None = None


@dataclasses.dataclass
class ValidationWarning:
    """A validation warning."""

    action_name: str
    param_name: str
    line_number: int
    file_path: str
    message: str
    additional_properties: bool | None = None


@dataclasses.dataclass
class ManualReview:
    """An item flagged for manual review."""

    action_name: str
    line_number: int
    file_path: str
    reason: str


@dataclasses.dataclass
class ValidationResult:
    """Result of validating a charm."""

    charm_name: str
    charm_path: pathlib.Path
    total_actions: int
    violations: list[Violation] = dataclasses.field(default_factory=list)
    warnings: list[ValidationWarning] = dataclasses.field(default_factory=list)
    manual_review: list[ManualReview] = dataclasses.field(default_factory=list)


def _required_param_names(action_name: str, required: Any, source: pathlib.Path) -> set[str]:
    """Return the set of required parameter names from a ``required`` value."""
    if required is None:
        return set()
    if isinstance(required, str):
        # A bare string is not valid, but treating it as a sequence of
        # characters (or worse, doing substring matching against it) is never
        # what was meant.
        logger.warning(
            "'required' for action %r in %s is a string, not a list.",
            action_name,
            source,
        )
        return {required}
    if not isinstance(required, (list, tuple, set)):
        logger.warning(
            "Ignoring invalid 'required' for action %r in %s: %r",
            action_name,
            source,
            required,
        )
        return set()
    return {str(name) for name in required}


def _additional_properties(
    action_name: str, spec: dict[str, Any], source: pathlib.Path
) -> bool | None:
    """Return the additionalProperties setting, or None if it is not set."""
    value = spec.get("additionalProperties")
    if value is None or isinstance(value, bool):
        return value
    logger.warning(
        "Ignoring non-boolean 'additionalProperties' for action %r in %s: %r",
        action_name,
        source,
        value,
    )
    return None


def _parse_action(
    action_name: str, action_spec: Any, source: pathlib.Path
) -> ActionDefinition | None:
    """Build an ActionDefinition from a single entry of an actions mapping.

    An action with no body at all (``my-action:``) is valid and means an action
    that takes no parameters, so that is not an error.
    """
    if action_spec is None:
        action_spec = {}
    if not isinstance(action_spec, dict):
        logger.warning(
            "Ignoring action %r in %s: expected a mapping, got %s",
            action_name,
            source,
            type(action_spec).__name__,
        )
        return None

    params_section = action_spec.get("params") or action_spec.get("properties") or {}
    if not isinstance(params_section, dict):
        logger.warning(
            "Ignoring 'params' for action %r in %s: expected a mapping, got %s",
            action_name,
            source,
            type(params_section).__name__,
        )
        params_section = {}

    required = _required_param_names(action_name, action_spec.get("required"), source)
    params: dict[str, ParamDef] = {}
    for param_name, param_spec in params_section.items():
        name = str(param_name)
        if isinstance(param_spec, dict):
            params[name] = ParamDef(
                name=name,
                type=param_spec.get("type"),
                required=name in required,
                default=param_spec.get("default"),
            )
        else:
            params[name] = ParamDef(name=name, required=name in required)

    for name in sorted(required - set(params)):
        logger.warning(
            "Action %r in %s requires %r, but does not define it.",
            action_name,
            source,
            name,
        )

    return ActionDefinition(
        name=action_name,
        params=params,
        required_params=required,
        additional_properties=_additional_properties(action_name, action_spec, source),
        source_file=source.name,
    )


def _actions_from_mapping(data: Any, source: pathlib.Path) -> dict[str, ActionDefinition]:
    """Convert a mapping of action-name to action-spec into definitions.

    `data` is deliberately typed as Any: it is either a whole actions.yaml
    document (which helpers.load_yaml has already reduced to a mapping or None)
    or the value of the ``actions`` key of charmcraft.yaml, which is not
    validated by helpers.load_yaml and so may be any type at all.
    """
    if data is None:
        return {}
    if not isinstance(data, dict):
        logger.warning(
            "Ignoring actions in %s: expected a mapping, got %s",
            source,
            type(data).__name__,
        )
        return {}
    actions = {}
    for action_name, action_spec in data.items():
        name = str(action_name)
        action_def = _parse_action(name, action_spec, source)
        if action_def is not None:
            actions[name] = action_def
    return actions


def load_action_definitions(repo: pathlib.Path) -> dict[str, ActionDefinition]:
    """Load action definitions from actions.yaml or charmcraft.yaml.

    Args:
        repo: Path to the charm repository.

    Returns:
        Dictionary mapping action-name to ActionDefinition.
    """
    actions: dict[str, ActionDefinition] = {}

    actions_yaml = repo / "actions.yaml"
    if actions_yaml.exists():
        # helpers.load_yaml returns None (having already logged a message that
        # names the file and the type that was found) when the document is not a
        # mapping, so a file like `- do-thing` is reported there rather than by
        # _actions_from_mapping. The charm is still counted as examined: it
        # simply ends up with no actions, and so appears in the summary under
        # "No action definitions" exactly as it did before.
        actions.update(_actions_from_mapping(load_yaml(actions_yaml), actions_yaml))

    charmcraft_yaml = repo / "charmcraft.yaml"
    if charmcraft_yaml.exists():
        charmcraft_data = load_yaml(charmcraft_yaml)
        if charmcraft_data is not None:
            from_charmcraft = _actions_from_mapping(
                charmcraft_data.get("actions"), charmcraft_yaml
            )
            duplicated = sorted(set(from_charmcraft) & set(actions))
            if duplicated:
                # charmcraft rejects this, so the charm is likely broken.
                logger.warning(
                    "%s defines %s, which are also in actions.yaml: "
                    "using the actions.yaml definitions.",
                    charmcraft_yaml,
                    ", ".join(duplicated),
                )
            for name, action_def in from_charmcraft.items():
                actions.setdefault(name, action_def)

    return actions


def action_name_candidates(event_name: str) -> tuple[str, ...]:
    """Return the possible action names for an ops action event name.

    ops replaces the hyphens in the action name with underscores and appends
    "_action", so ``create-oauth-client`` becomes ``create_oauth_client_action``.
    Some charms do use underscores in the action name itself, though, so the
    name with the underscores kept is offered as a fallback.

    Examples:
        create_oauth_client_action -> ("create-oauth-client", "create_oauth_client")
        get_password_action -> ("get-password", "get_password")
    """
    base = event_name.removesuffix(ACTION_EVENT_SUFFIX)
    hyphenated = base.replace("_", "-")
    if hyphenated == base:
        return (hyphenated,)
    return (hyphenated, base)


def _observed_event_name(event_arg: ast.expr) -> str | None:
    """Return the name of the event in the first argument of observe()."""
    if isinstance(event_arg, ast.Attribute):
        # self.on.foo_action
        return event_arg.attr
    if isinstance(event_arg, ast.Name):
        # A local alias, e.g. `event = self.on.foo_action`.
        return event_arg.id
    if (
        isinstance(event_arg, ast.Call)
        and isinstance(event_arg.func, ast.Name)
        and event_arg.func.id == "getattr"
        and len(event_arg.args) >= 2
        and isinstance(event_arg.args[1], ast.Constant)
        and isinstance(event_arg.args[1].value, str)
    ):
        # getattr(self.on, "foo_action")
        return event_arg.args[1].value
    return None


def _observed_handler(handler_arg: ast.expr) -> tuple[str | None, bool]:
    """Return the (name, is_self_method) of the handler passed to observe()."""
    if isinstance(handler_arg, ast.Attribute):
        is_self_method = (
            isinstance(handler_arg.value, ast.Name) and handler_arg.value.id in _INSTANCE_ARG_NAMES
        )
        return handler_arg.attr, is_self_method
    if isinstance(handler_arg, ast.Name):
        return handler_arg.id, False
    return None, False


#: Modules that helpers.parse_module has already rejected.
_unparseable_modules: set[pathlib.Path] = set()


def _parse_module_once(module: pathlib.Path) -> ast.Module | None:
    """Parse a module, asking helpers.parse_module about each file only once.

    helpers.parse_module logs at WARNING (a module that cannot be parsed is a
    gap in the analysis, not a detail), but _locate_handler retries every module
    in the charm for every handler, so the same file is offered for parsing
    several times. Remembering the failures keeps the report to one warning per
    unparseable module without holding on to the parsed trees.
    """
    if module in _unparseable_modules:
        return None
    tree = parse_module(module)
    if tree is None:
        _unparseable_modules.add(module)
    return tree


def find_action_handlers(module: pathlib.Path) -> list[HandlerInfo]:
    """Find action event handlers and their action names.

    Args:
        module: Path to Python file.

    Returns:
        List of HandlerInfo objects for each framework.observe() call found.
    """
    tree = _parse_module_once(module)
    if tree is None:
        return []

    handlers = []
    # Pattern: self.framework.observe(self.on.XXX_action, handler_method)
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or node.func.attr != "observe"
            or len(node.args) < 2
        ):
            continue

        event_name = _observed_event_name(node.args[0])
        handler_name, is_self_method = _observed_handler(node.args[1])

        # Only track events that ops would have generated for an action.
        if not event_name or not handler_name:
            continue
        if not event_name.endswith(ACTION_EVENT_SUFFIX):
            continue

        handlers.append(
            HandlerInfo(
                handler_name=handler_name,
                action_names=action_name_candidates(event_name),
                observe_file=module,
                is_self_method=is_self_method,
            )
        )

    return handlers


def _find_function_by_name(
    tree: ast.AST, name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Find a (possibly async) function or method definition by name."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _event_arg_name(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Return the name of the event argument of a handler, if there is one.

    Handlers are usually ``def handler(self, event)``, but a handler that is not
    a method is ``def handler(event)``. A method with no event argument at all
    is not a valid handler, and must not have ``self`` treated as the event.
    """
    args = [arg.arg for arg in func.args.posonlyargs + func.args.args]
    if args and args[0] in _INSTANCE_ARG_NAMES:
        args = args[1:]
    return args[0] if args else None


def _is_params_expr(node: ast.expr, event_name: str, aliases: set[str]) -> bool:
    """Return whether the node evaluates to the action parameters mapping."""
    if (
        isinstance(node, ast.Attribute)
        and node.attr == "params"
        and isinstance(node.value, ast.Name)
        and node.value.id == event_name
    ):
        return True
    return isinstance(node, ast.Name) and node.id in aliases


def _params_aliases(handler: ast.FunctionDef | ast.AsyncFunctionDef, event_name: str) -> set[str]:
    """Return local names that have been bound to ``<event>.params``."""
    aliases: set[str] = set()
    for node in ast.walk(handler):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not _is_params_expr(value, event_name, aliases):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                aliases.add(target.id)
    return aliases


def _string_constant(node: ast.expr) -> str | None:
    """Return the value of a string constant node, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _param_accesses(
    handler: ast.FunctionDef | ast.AsyncFunctionDef, event_name: str
) -> list[ParamAccess]:
    """Collect every parameter access in the body of a handler."""
    aliases = _params_aliases(handler, event_name)
    accesses: list[ParamAccess] = []

    def is_params(node: ast.expr) -> bool:
        return _is_params_expr(node, event_name, aliases)

    for node in ast.walk(handler):
        # Pattern 1: event.params["key"]
        if isinstance(node, ast.Subscript):
            name = _string_constant(node.slice)
            if name is not None and is_params(node.value):
                accesses.append(
                    ParamAccess(
                        param_name=name,
                        access_type="subscript",
                        line_number=node.lineno,
                        is_safe=False,
                    )
                )
        # Pattern 2: event.params.get("key"), and pattern 3: **event.params
        elif isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and is_params(node.func.value)
                and node.args
            ):
                name = _string_constant(node.args[0])
                if name is not None:
                    accesses.append(
                        ParamAccess(
                            param_name=name,
                            access_type="get",
                            line_number=node.lineno,
                            is_safe=True,
                        )
                    )
            accesses.extend(
                ParamAccess(
                    param_name=None,
                    access_type="spread",
                    line_number=node.lineno,
                    is_safe=True,
                )
                for keyword in node.keywords
                if keyword.arg is None and is_params(keyword.value)
            )
        # Pattern 3 (continued): {**event.params} in a dict display.
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=False):
                if key is None and is_params(value):
                    accesses.append(
                        ParamAccess(
                            param_name=None,
                            access_type="spread",
                            line_number=node.lineno,
                            is_safe=True,
                        )
                    )

    return accesses


def extract_event_params(
    module: pathlib.Path, handler_name: str
) -> tuple[list[ParamAccess], bool]:
    """Extract event.params accesses from a specific handler method.

    Args:
        module: Path to Python file.
        handler_name: Name of the handler method.

    Returns:
        Tuple of (list of ParamAccess objects, whether the handler was found).
    """
    tree = _parse_module_once(module)
    if tree is None:
        return [], False

    handler = _find_function_by_name(tree, handler_name)
    if handler is None:
        logger.debug("Handler %s not found in %s", handler_name, module)
        return [], False

    event_name = _event_arg_name(handler)
    if not event_name:
        logger.debug("Could not detect event argument for %s in %s", handler_name, module)
        return [], True

    return _param_accesses(handler, event_name), True


def _charm_python_files(
    repo: pathlib.Path,
) -> tuple[list[pathlib.Path], list[pathlib.Path]]:
    """Return the (src, lib) Python files that are worth scanning.

    src/**/*.py holds both observe() calls and handler definitions; lib/**/*.py
    holds charm libraries, which may also observe actions.
    """
    src_files = sorted(repo.glob("src/**/*.py"))
    lib_files = sorted(
        path
        for path in (repo / "lib").rglob("*.py")
        if not _is_ignored_module(path.relative_to(repo))
    )
    return src_files, lib_files


def _is_ignored_module(relative_path: pathlib.PurePath) -> bool:
    """Return whether a Python file is generated or is a test."""
    if "__pycache__" in relative_path.parts:
        return True
    if any(part in {"test", "tests"} for part in relative_path.parts):
        return True
    name = relative_path.name
    return name.startswith("test_") or name.endswith("_test.py")


def _collect_handlers(modules: list[pathlib.Path]) -> list[HandlerInfo]:
    """Find the action handlers in the given modules, without duplicates."""
    handlers: list[HandlerInfo] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for module in modules:
        for handler in find_action_handlers(module):
            key = (handler.handler_name, handler.action_names)
            if key not in seen:
                seen.add(key)
                handlers.append(handler)
    return handlers


def _display_path(path: pathlib.Path, repo: pathlib.Path) -> str:
    """Return the path relative to the charm, for display."""
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)


def _locate_handler(
    handler: HandlerInfo, modules: list[pathlib.Path]
) -> tuple[list[ParamAccess], pathlib.Path | None]:
    """Find where a handler is defined, and what parameters it accesses.

    The file containing the observe() call is tried first, then every other
    module in the charm.
    """
    accesses, found = extract_event_params(handler.observe_file, handler.handler_name)
    if found:
        return accesses, handler.observe_file
    for module in modules:
        if module == handler.observe_file:
            continue
        accesses, found = extract_event_params(module, handler.handler_name)
        if found:
            return accesses, module
    return [], None


def _check_access(
    access: ParamAccess,
    action_def: ActionDefinition,
    handler: HandlerInfo,
    location: str,
    result: ValidationResult,
) -> None:
    """Record any problem with a single parameter access."""
    if access.access_type == "spread":
        result.manual_review.append(
            ManualReview(
                action_name=action_def.name,
                line_number=access.line_number,
                file_path=location,
                reason="Spread operator (**event.params) - cannot validate statically",
            )
        )
        return

    if access.param_name is None or access.param_name in action_def.params:
        return

    # If additionalProperties is explicitly True then extra parameters are
    # intentional, so there is nothing to report.
    if action_def.additional_properties is True:
        return

    if access.access_type == "subscript":
        result.violations.append(
            Violation(
                action_name=action_def.name,
                handler_name=handler.handler_name,
                param_name=access.param_name,
                access_type="[]",
                line_number=access.line_number,
                file_path=location,
                message=(
                    f"Parameter '{access.param_name}' accessed but not defined in "
                    f"{action_def.source_file}"
                ),
                additional_properties=action_def.additional_properties,
            )
        )
    elif access.access_type == "get":
        result.warnings.append(
            ValidationWarning(
                action_name=action_def.name,
                param_name=access.param_name,
                line_number=access.line_number,
                file_path=location,
                message=(
                    f"Parameter '{access.param_name}' accessed via .get() but not "
                    f"defined in {action_def.source_file}"
                ),
                additional_properties=action_def.additional_properties,
            )
        )


def _validate_handler(
    handler: HandlerInfo,
    action_defs: dict[str, ActionDefinition],
    repo: pathlib.Path,
    modules: list[pathlib.Path],
    result: ValidationResult,
) -> None:
    """Validate a single action handler, adding any problems to the result."""
    observe_location = _display_path(handler.observe_file, repo)

    action_def = next(
        (action_defs[name] for name in handler.action_names if name in action_defs),
        None,
    )
    if action_def is None:
        result.violations.append(
            Violation(
                action_name=handler.action_name,
                handler_name=handler.handler_name,
                param_name="N/A",
                access_type="N/A",
                line_number=0,
                file_path=observe_location,
                message=(f"Handler exists but action '{handler.action_name}' not defined in YAML"),
            )
        )
        return

    # For non-self-method handlers (imported functions or SomeClass.method),
    # we can't reliably locate the definition statically.
    if not handler.is_self_method:
        result.manual_review.append(
            ManualReview(
                action_name=action_def.name,
                line_number=0,
                file_path=observe_location,
                reason=(
                    f"Handler '{handler.handler_name}' is not a self.method - may be "
                    "imported, cannot validate statically"
                ),
            )
        )
        return

    accesses, handler_file = _locate_handler(handler, modules)
    if handler_file is None:
        result.manual_review.append(
            ManualReview(
                action_name=action_def.name,
                line_number=0,
                file_path=observe_location,
                reason=(
                    f"Handler '{handler.handler_name}' definition not found in src/ - "
                    "may be defined elsewhere"
                ),
            )
        )
        return

    location = _display_path(handler_file, repo)
    for access in accesses:
        _check_access(access, action_def, handler, location, result)


def validate_charm(
    repo: pathlib.Path,
) -> tuple[ValidationResult | None, Literal["no_actions", "no_handlers"] | None]:
    """Validate action parameter usage for a single charm.

    Args:
        repo: Path to charm repository.

    Returns:
        Tuple of (ValidationResult or None, skip_reason or None). skip_reason is
        one of "no_actions" or "no_handlers", or None if the charm was validated.
    """
    action_defs = load_action_definitions(repo)
    if not action_defs:
        return None, "no_actions"

    src_files, lib_files = _charm_python_files(repo)
    modules = src_files + lib_files
    handlers = _collect_handlers(modules)
    if not handlers:
        logger.debug("No action handlers found in %s", repo.name)
        return None, "no_handlers"

    result = ValidationResult(
        charm_name=repo.name,
        charm_path=repo,
        total_actions=len(action_defs),
    )
    for handler in handlers:
        _validate_handler(handler, action_defs, repo, modules, result)
    return result, None


def _print_summary(
    results: list[ValidationResult],
    console: rich.console.Console,
    skip_stats: dict[str, int] | None,
) -> None:
    """Print the summary section of the report."""
    console.print("\n[bold]Action Parameter Validation Report[/bold]")
    console.print("═" * 50)
    console.print("\n[bold]Summary[/bold]")

    if skip_stats:
        console.print(f"Total charms processed: {skip_stats.get('total', 0)}")
        console.print(f"Charms analyzed (with actions and handlers): {len(results)}")

        no_actions = skip_stats.get("no_actions", 0)
        no_handlers = skip_stats.get("no_handlers", 0)
        total_skipped = no_actions + no_handlers
        if total_skipped:
            console.print(f"\n[dim]Skipped charms: {total_skipped}[/dim]")
            if no_actions:
                console.print(f"  [dim]- No action definitions: {no_actions}[/dim]")
            if no_handlers:
                console.print(f"  [dim]- Actions defined but no handlers: {no_handlers}[/dim]")
        console.print()
    else:
        console.print(f"Total charms analyzed: {len(results)}")

    console.print(f"Charms with violations: {sum(1 for r in results if r.violations)}")
    console.print(f"Charms with warnings: {sum(1 for r in results if r.warnings)}")
    console.print(f"Total violations (errors): {sum(len(r.violations) for r in results)}")
    console.print(f"Total warnings: {sum(len(r.warnings) for r in results)}")
    console.print(f"Total flagged for manual review: {sum(len(r.manual_review) for r in results)}")


def _print_param_table(
    results: list[ValidationResult],
    console: rich.console.Console,
    heading: str,
    style: str,
    issues: str,
) -> None:
    """Print the violations or warnings table (they have the same shape)."""
    console.print(f"\n[bold {style}]{heading}[/bold {style}]")
    console.print("═" * 80)

    table = rich.table.Table(show_header=True, header_style=f"bold {style}")
    table.add_column("Charm", style="cyan")
    table.add_column("Action", style="yellow")
    table.add_column("Parameter", style="magenta")
    table.add_column("Location", style="green")
    table.add_column("additionalProperties", style="white")

    for result in results:
        for issue in getattr(result, issues):
            table.add_row(
                result.charm_name,
                issue.action_name,
                issue.param_name,
                f"{issue.file_path}:{issue.line_number}",
                "not set"
                if issue.additional_properties is None
                else str(issue.additional_properties),
            )

    console.print(table)


def _print_manual_review_table(
    results: list[ValidationResult], console: rich.console.Console
) -> None:
    """Print the table of items that need to be reviewed by a person."""
    console.print(
        "\n[bold blue]MANUAL REVIEW: Handlers and spread operators requiring manual "
        "inspection[/bold blue]"
    )
    console.print("═" * 80)

    table = rich.table.Table(show_header=True, header_style="bold blue")
    table.add_column("Charm", style="cyan")
    table.add_column("Action", style="yellow")
    table.add_column("Location", style="green")
    table.add_column("Reason", style="white")

    for result in results:
        for item in result.manual_review:
            table.add_row(
                result.charm_name,
                item.action_name,
                f"{item.file_path}:{item.line_number}",
                item.reason,
            )

    console.print(table)


def report(
    results: list[ValidationResult],
    console: rich.console.Console,
    skip_stats: dict[str, int] | None = None,
) -> None:
    """Generate rich console report.

    Args:
        results: List of validation results.
        console: Rich console for output.
        skip_stats: Counts of the charms that were processed and skipped.
    """
    _print_summary(results, console, skip_stats)

    total_violations = sum(len(r.violations) for r in results)
    total_warnings = sum(len(r.warnings) for r in results)

    if total_violations:
        _print_param_table(
            results,
            console,
            "ERRORS: Parameters accessed with [] but not defined",
            "red",
            "violations",
        )
    if total_warnings:
        _print_param_table(
            results,
            console,
            "WARNINGS: Parameters accessed with .get() but not defined",
            "yellow",
            "warnings",
        )
    if any(r.manual_review for r in results):
        _print_manual_review_table(results, console)

    if not total_violations and not total_warnings:
        console.print("\n[bold green]✓ No validation issues found![/bold green]")


def write_csv(results: list[ValidationResult], output: TextIO) -> None:
    """Write every violation and warning to a CSV file."""
    writer = csv.writer(output)
    writer.writerow(
        [
            "charm",
            "action",
            "parameter",
            "access_type",
            "line",
            "file",
            "severity",
            "additionalProperties",
        ]
    )
    for result in results:
        rows = [(v, v.access_type, "error") for v in result.violations]
        rows += [(w, ".get()", "warning") for w in result.warnings]
        for issue, access_type, severity in rows:
            writer.writerow(
                [
                    result.charm_name,
                    issue.action_name,
                    issue.param_name,
                    access_type,
                    issue.line_number,
                    issue.file_path,
                    severity,
                    "" if issue.additional_properties is None else issue.additional_properties,
                ]
            )


def _charm_entrypoint(repo: pathlib.Path) -> str:
    """Return the charm entry point, using the same rules as helpers.py."""
    entry = "src/charm.py"
    charmcraft_yaml = repo / "charmcraft.yaml"
    if not charmcraft_yaml.exists():
        return entry
    data = load_yaml(charmcraft_yaml)
    try:
        configured = data["parts"]["charm"]["charm-entrypoint"]
    except (KeyError, TypeError):
        return entry
    return configured if isinstance(configured, str) else entry


def _has_python_source(repo: pathlib.Path) -> bool:
    """Return whether the charm has any Python source to analyse."""
    if (repo / _charm_entrypoint(repo)).exists():
        return True
    return next(repo.glob("src/**/*.py"), None) is not None


@click.command()
@click.option("--cache-folder", default=".cache", help="Path to .cache folder")
@click.option("--strict", is_flag=True, help="Treat warnings as errors")
@click.option("--output-csv", type=click.File("w"), help="Also output CSV for further analysis")
@click.option("--verbose", is_flag=True, help="Show detailed logging")
def main(cache_folder: str, strict: bool, output_csv: TextIO | None, verbose: bool):
    """Validate action parameter usage across all charms."""
    configure_logging()
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    console = rich.console.Console()
    cache_path = pathlib.Path(cache_folder)
    if not cache_path.is_dir():
        console.print(f"[red]Error: Cache folder {cache_folder} does not exist[/red]")
        sys.exit(2)

    console.print(f"[bold]Analyzing charms in {cache_folder}...[/bold]")

    results: list[ValidationResult] = []
    skip_stats: collections.Counter[str] = collections.Counter(
        {
            "total": 0,
            "no_actions": 0,
            "no_handlers": 0,
        }
    )

    for repo in iter_repositories(cache_path):
        # Skip repos with no Python source at all.
        if not _has_python_source(repo):
            continue

        skip_stats["total"] += 1
        result, skip_reason = validate_charm(repo)
        if skip_reason:
            skip_stats[skip_reason] += 1
        elif result:
            results.append(result)

    report(results, console, skip_stats)

    if output_csv:
        write_csv(results, output_csv)
        console.print(f"\n[green]CSV output written to {output_csv.name}[/green]")

    total_violations = sum(len(r.violations) for r in results)
    total_warnings = sum(len(r.warnings) for r in results)
    if total_violations or (strict and total_warnings):
        sys.exit(1)


if __name__ == "__main__":
    main()
