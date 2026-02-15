#!/usr/bin/env python3
"""Validate that event.params accesses match action definitions.

This script checks that all parameters accessed via event.params in charm
action handlers are properly defined in actions.yaml or charmcraft.yaml.
"""

import ast
import csv
import logging
import pathlib
from dataclasses import dataclass, field
from typing import Any, Literal

import click
import rich.console
import rich.logging
import rich.table
import yaml

from helpers import iter_repositories

logger = logging.getLogger(__name__)


@dataclass
class ParamDef:
    """Definition of an action parameter."""
    name: str
    type: str | None = None
    required: bool = False
    default: Any | None = None


@dataclass
class ActionDefinition:
    """Definition of a charm action."""
    name: str
    params: dict[str, ParamDef] = field(default_factory=dict)
    required_params: set[str] = field(default_factory=set)
    source_file: str = ""


@dataclass
class ParamAccess:
    """A parameter access in code."""
    param_name: str | None  # None for spread operator
    access_type: Literal['subscript', 'get', 'spread']
    line_number: int
    is_safe: bool  # True for .get(), False for []


@dataclass
class Violation:
    """A validation error."""
    action_name: str
    handler_name: str
    param_name: str
    access_type: str
    line_number: int
    file_path: str
    message: str


@dataclass
class Warning:
    """A validation warning."""
    action_name: str
    param_name: str
    line_number: int
    file_path: str
    message: str


@dataclass
class ManualReview:
    """An item flagged for manual review."""
    action_name: str
    line_number: int
    file_path: str
    reason: str


@dataclass
class ValidationResult:
    """Result of validating a charm."""
    charm_name: str
    charm_path: pathlib.Path
    total_actions: int
    violations: list[Violation] = field(default_factory=list)
    warnings: list[Warning] = field(default_factory=list)
    manual_review: list[ManualReview] = field(default_factory=list)


def load_action_definitions(repo: pathlib.Path) -> dict[str, ActionDefinition]:
    """Load action definitions from actions.yaml or charmcraft.yaml.

    Args:
        repo: Path to the charm repository

    Returns:
        Dictionary mapping action-name → ActionDefinition
    """
    actions = {}

    # Try actions.yaml first (standalone file)
    actions_yaml = repo / "actions.yaml"
    if actions_yaml.exists():
        try:
            with actions_yaml.open() as f:
                actions_data = yaml.safe_load(f) or {}

            for action_name, action_spec in actions_data.items():
                if not isinstance(action_spec, dict):
                    continue

                params_dict = {}
                params_section = action_spec.get("params") or action_spec.get("properties") or {}
                required_list = action_spec.get("required", [])

                for param_name, param_spec in params_section.items():
                    if isinstance(param_spec, dict):
                        params_dict[param_name] = ParamDef(
                            name=param_name,
                            type=param_spec.get("type"),
                            required=param_name in required_list,
                            default=param_spec.get("default"),
                        )
                    else:
                        params_dict[param_name] = ParamDef(name=param_name)

                actions[action_name] = ActionDefinition(
                    name=action_name,
                    params=params_dict,
                    required_params=set(required_list),
                    source_file="actions.yaml",
                )
        except Exception as e:
            logger.warning(f"Failed to parse {actions_yaml}: {e}")

    # Try charmcraft.yaml if no actions.yaml or to augment it
    charmcraft_yaml = repo / "charmcraft.yaml"
    if charmcraft_yaml.exists():
        try:
            with charmcraft_yaml.open() as f:
                charmcraft_data = yaml.safe_load(f) or {}

            actions_data = charmcraft_data.get("actions", {})
            for action_name, action_spec in actions_data.items():
                # Skip if already loaded from actions.yaml
                if action_name in actions:
                    continue

                if not isinstance(action_spec, dict):
                    continue

                params_dict = {}
                params_section = action_spec.get("params") or action_spec.get("properties") or {}
                required_list = action_spec.get("required", [])

                for param_name, param_spec in params_section.items():
                    if isinstance(param_spec, dict):
                        params_dict[param_name] = ParamDef(
                            name=param_name,
                            type=param_spec.get("type"),
                            required=param_name in required_list,
                            default=param_spec.get("default"),
                        )
                    else:
                        params_dict[param_name] = ParamDef(name=param_name)

                actions[action_name] = ActionDefinition(
                    name=action_name,
                    params=params_dict,
                    required_params=set(required_list),
                    source_file="charmcraft.yaml",
                )
        except Exception as e:
            logger.warning(f"Failed to parse {charmcraft_yaml}: {e}")

    return actions


def _normalize_action_name(event_name: str) -> str:
    """Convert event name to action name.

    Examples:
        create_oauth_client_action → create-oauth-client
        get_password_action → get-password
    """
    # Remove _action suffix
    if event_name.endswith("_action"):
        event_name = event_name[:-7]

    # Convert underscores to hyphens
    return event_name.replace("_", "-")


def find_action_handlers(module: pathlib.Path) -> dict[str, str]:
    """Find action event handlers and their action names.

    Args:
        module: Path to Python file

    Returns:
        Dictionary mapping handler_method_name → action-name
    """
    handlers = {}

    try:
        with module.open() as f:
            tree = ast.parse(f.read())
    except Exception as e:
        logger.debug(f"Failed to parse {module}: {e}")
        return handlers

    # Find framework.observe calls for actions
    # Pattern: self.framework.observe(self.on.XXX_action, handler_method)
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or node.func.attr != "observe"
            or len(node.args) < 2
        ):
            continue

        # First arg: self.on.XXX_action
        event_arg = node.args[0]
        event_name = None

        if isinstance(event_arg, ast.Attribute) and hasattr(event_arg, 'attr'):
            event_name = event_arg.attr
        elif isinstance(event_arg, ast.Name):
            event_name = event_arg.id
        elif (
            isinstance(event_arg, ast.Call)
            and hasattr(event_arg.func, "id")
            and event_arg.func.id == "getattr"
            and len(event_arg.args) >= 2
        ):
            if isinstance(event_arg.args[1], ast.Constant):
                event_name = event_arg.args[1].value

        # Second arg: handler method
        handler_arg = node.args[1]
        handler_name = None

        if isinstance(handler_arg, ast.Attribute):
            handler_name = handler_arg.attr
        elif isinstance(handler_arg, ast.Name):
            handler_name = handler_arg.id

        # Only track if it looks like an action event
        if event_name and handler_name and "_action" in event_name:
            action_name = _normalize_action_name(event_name)
            handlers[handler_name] = action_name

    return handlers


def _find_method_by_name(tree: ast.AST, method_name: str) -> ast.FunctionDef | None:
    """Find a method definition by name in an AST."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == method_name:
            return node
    return None


def extract_event_params(module: pathlib.Path, handler_name: str) -> list[ParamAccess]:
    """Extract event.params accesses from a specific handler method.

    Args:
        module: Path to Python file
        handler_name: Name of the handler method

    Returns:
        List of ParamAccess objects
    """
    accesses = []

    try:
        with module.open() as f:
            tree = ast.parse(f.read())
    except Exception as e:
        logger.debug(f"Failed to parse {module}: {e}")
        return accesses

    # Find the handler method
    handler_method = _find_method_by_name(tree, handler_name)
    if not handler_method:
        logger.debug(f"Handler {handler_name} not found in {module}")
        return accesses

    # Walk the handler method's AST
    for node in ast.walk(handler_method):
        # Pattern 1: event.params["key"] or event.params['key']
        if isinstance(node, ast.Subscript):
            # Check if this is event.params[...]
            if (
                isinstance(node.value, ast.Attribute)
                and node.value.attr == "params"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)
            ):
                accesses.append(ParamAccess(
                    param_name=node.slice.value,
                    access_type='subscript',
                    line_number=node.lineno,
                    is_safe=False,
                ))

        # Pattern 2: event.params.get("key")
        # Pattern 3: **event.params (spread operator)
        elif isinstance(node, ast.Call):
            # Pattern 2: .get() method
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "params"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                accesses.append(ParamAccess(
                    param_name=node.args[0].value,
                    access_type='get',
                    line_number=node.lineno,
                    is_safe=True,
                ))

            # Pattern 3: Spread operator in keyword arguments
            for keyword in node.keywords:
                if (
                    keyword.arg is None  # **kwargs pattern
                    and isinstance(keyword.value, ast.Attribute)
                    and keyword.value.attr == "params"
                ):
                    accesses.append(ParamAccess(
                        param_name=None,
                        access_type='spread',
                        line_number=node.lineno,
                        is_safe=True,
                    ))

    return accesses


def validate_charm(repo: pathlib.Path, entry: pathlib.Path) -> tuple[ValidationResult | None, str | None]:
    """Validate action parameter usage for a single charm.

    Args:
        repo: Path to charm repository
        entry: Path to charm entry point (src/charm.py)

    Returns:
        Tuple of (ValidationResult or None, skip_reason or None)
        skip_reason is one of: "no_actions", "no_handlers", or None if not skipped
    """
    charm_name = repo.name

    # Load action definitions
    action_defs = load_action_definitions(repo)
    if not action_defs:
        return None, "no_actions"

    # Find all Python files to scan (src/ and lib/)
    python_files = []

    # Add main charm entry
    if entry.exists():
        python_files.append(entry)

    # Add lib files
    lib_dir = repo / "lib"
    if lib_dir.exists():
        for py_file in lib_dir.rglob("*.py"):
            # Skip __pycache__ and test files
            if "__pycache__" not in str(py_file) and "test" not in py_file.name.lower():
                python_files.append(py_file)

    # Find action handlers across all files
    all_handlers = {}
    for py_file in python_files:
        handlers = find_action_handlers(py_file)
        for handler_name, action_name in handlers.items():
            # Store both handler name and file path
            all_handlers[handler_name] = (action_name, py_file)

    if not all_handlers:
        logger.debug(f"No action handlers found in {charm_name}")
        return None, "no_handlers"

    result = ValidationResult(
        charm_name=charm_name,
        charm_path=repo,
        total_actions=len(action_defs),
    )

    # Validate each handler
    for handler_name, (action_name, py_file) in all_handlers.items():
        # Check if action is defined
        if action_name not in action_defs:
            result.violations.append(Violation(
                action_name=action_name,
                handler_name=handler_name,
                param_name="N/A",
                access_type="N/A",
                line_number=0,
                file_path=str(py_file.relative_to(repo)),
                message=f"Handler exists but action '{action_name}' not defined in YAML",
            ))
            continue

        action_def = action_defs[action_name]
        defined_params = set(action_def.params.keys())

        # Extract param accesses
        param_accesses = extract_event_params(py_file, handler_name)

        for access in param_accesses:
            if access.access_type == 'spread':
                # Flag for manual review
                result.manual_review.append(ManualReview(
                    action_name=action_name,
                    line_number=access.line_number,
                    file_path=str(py_file.relative_to(repo)),
                    reason="Spread operator (**event.params) - cannot validate statically",
                ))
            elif access.param_name:
                # Check if parameter is defined
                if access.param_name not in defined_params:
                    if access.access_type == 'subscript':
                        # Error for direct access
                        result.violations.append(Violation(
                            action_name=action_name,
                            handler_name=handler_name,
                            param_name=access.param_name,
                            access_type='[]',
                            line_number=access.line_number,
                            file_path=str(py_file.relative_to(repo)),
                            message=f"Parameter '{access.param_name}' accessed but not defined in {action_def.source_file}",
                        ))
                    elif access.access_type == 'get':
                        # Warning for .get() access
                        result.warnings.append(Warning(
                            action_name=action_name,
                            param_name=access.param_name,
                            line_number=access.line_number,
                            file_path=str(py_file.relative_to(repo)),
                            message=f"Parameter '{access.param_name}' accessed via .get() but not defined in {action_def.source_file}",
                        ))

    return result, None


def report(results: list[ValidationResult], console: rich.console.Console, skip_stats: dict[str, int] | None = None):
    """Generate rich console report.

    Args:
        results: List of validation results
        console: Rich console for output
    """
    # Calculate summary statistics
    total_charms = len(results)
    charms_with_violations = sum(1 for r in results if r.violations)
    charms_with_warnings = sum(1 for r in results if r.warnings)
    total_violations = sum(len(r.violations) for r in results)
    total_warnings = sum(len(r.warnings) for r in results)
    total_manual_review = sum(len(r.manual_review) for r in results)

    # Summary
    console.print("\n[bold]Action Parameter Validation Report[/bold]")
    console.print("═" * 50)
    console.print(f"\n[bold]Summary[/bold]")

    # Show skip statistics if available
    if skip_stats:
        total_processed = skip_stats.get("total", 0)
        console.print(f"Total charms processed: {total_processed}")
        console.print(f"Charms analyzed (with actions and handlers): {total_charms}")

        # Show skipped charms breakdown
        no_actions = skip_stats.get("no_actions", 0)
        no_handlers = skip_stats.get("no_handlers", 0)
        total_skipped = no_actions + no_handlers

        if total_skipped > 0:
            console.print(f"\n[dim]Skipped charms: {total_skipped}[/dim]")
            if no_actions > 0:
                console.print(f"  [dim]- No action definitions: {no_actions}[/dim]")
            if no_handlers > 0:
                console.print(f"  [dim]- Actions defined but no handlers: {no_handlers}[/dim]")
        console.print()
    else:
        console.print(f"Total charms analyzed: {total_charms}")

    console.print(f"Charms with violations: {charms_with_violations}")
    console.print(f"Charms with warnings: {charms_with_warnings}")
    console.print(f"Total violations (errors): {total_violations}")
    console.print(f"Total warnings: {total_warnings}")
    console.print(f"Total flagged for manual review: {total_manual_review}")

    # Violations table
    if total_violations > 0:
        console.print("\n[bold red]ERRORS: Parameters accessed with [] but not defined[/bold red]")
        console.print("═" * 80)

        table = rich.table.Table(show_header=True, header_style="bold red")
        table.add_column("Charm", style="cyan")
        table.add_column("Action", style="yellow")
        table.add_column("Parameter", style="magenta")
        table.add_column("Location", style="green")

        for result in results:
            for v in result.violations:
                table.add_row(
                    result.charm_name,
                    v.action_name,
                    v.param_name,
                    f"{v.file_path}:{v.line_number}",
                )

        console.print(table)

    # Warnings table
    if total_warnings > 0:
        console.print("\n[bold yellow]WARNINGS: Parameters accessed with .get() but not defined[/bold yellow]")
        console.print("═" * 80)

        table = rich.table.Table(show_header=True, header_style="bold yellow")
        table.add_column("Charm", style="cyan")
        table.add_column("Action", style="yellow")
        table.add_column("Parameter", style="magenta")
        table.add_column("Location", style="green")

        for result in results:
            for w in result.warnings:
                table.add_row(
                    result.charm_name,
                    w.action_name,
                    w.param_name,
                    f"{w.file_path}:{w.line_number}",
                )

        console.print(table)

    # Manual review table
    if total_manual_review > 0:
        console.print("\n[bold blue]MANUAL REVIEW: Spread operator usage[/bold blue]")
        console.print("═" * 80)

        table = rich.table.Table(show_header=True, header_style="bold blue")
        table.add_column("Charm", style="cyan")
        table.add_column("Action", style="yellow")
        table.add_column("Location", style="green")
        table.add_column("Reason", style="white")

        for result in results:
            for m in result.manual_review:
                table.add_row(
                    result.charm_name,
                    m.action_name,
                    f"{m.file_path}:{m.line_number}",
                    m.reason,
                )

        console.print(table)

    # Success message if no issues
    if total_violations == 0 and total_warnings == 0:
        console.print("\n[bold green]✓ No validation issues found![/bold green]")


@click.command()
@click.option("--cache-folder", default=".cache", help="Path to .cache folder")
@click.option("--strict", is_flag=True, help="Treat warnings as errors")
@click.option("--output-csv", type=click.File('w'), help="Also output CSV for further analysis")
@click.option("--verbose", is_flag=True, help="Show detailed logging")
def main(cache_folder, strict, output_csv, verbose):
    """Validate action parameter usage across all charms."""
    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[rich.logging.RichHandler(rich_tracebacks=True)],
    )

    console = rich.console.Console()
    cache_path = pathlib.Path(cache_folder)

    if not cache_path.exists():
        console.print(f"[red]Error: Cache folder {cache_folder} does not exist[/red]")
        return 1

    console.print(f"[bold]Analyzing charms in {cache_folder}...[/bold]")

    results = []
    skip_stats = {
        "total": 0,
        "no_actions": 0,
        "no_handlers": 0,
    }

    for repo in iter_repositories(cache_path):
        # Find charm entry point
        entry = repo / "src" / "charm.py"
        if not entry.exists():
            continue

        skip_stats["total"] += 1
        result, skip_reason = validate_charm(repo, entry)

        if skip_reason:
            skip_stats[skip_reason] = skip_stats.get(skip_reason, 0) + 1
        elif result:
            results.append(result)

    # Generate report
    report(results, console, skip_stats)

    # Output CSV if requested
    if output_csv:
        writer = csv.writer(output_csv)
        writer.writerow(["charm", "action", "parameter", "access_type", "line", "file", "severity", "defined_params"])

        for result in results:
            for v in result.violations:
                writer.writerow([
                    result.charm_name,
                    v.action_name,
                    v.param_name,
                    v.access_type,
                    v.line_number,
                    v.file_path,
                    "error",
                    "",  # Could add defined params here
                ])

            for w in result.warnings:
                writer.writerow([
                    result.charm_name,
                    w.action_name,
                    w.param_name,
                    ".get()",
                    w.line_number,
                    w.file_path,
                    "warning",
                    "",
                ])

        console.print(f"\n[green]CSV output written to {output_csv.name}[/green]")

    # Return exit code
    total_violations = sum(len(r.violations) for r in results)
    total_warnings = sum(len(r.warnings) for r in results)

    if strict and (total_violations > 0 or total_warnings > 0):
        return 1
    elif total_violations > 0:
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
