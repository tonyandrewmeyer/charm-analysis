#! /usr/bin/env python3

"""Utility to clean up disk space used by cached charm repositories."""

import logging
import pathlib
import shutil

import click
import rich.logging

logger = logging.getLogger(__name__)

JUNK_DIRS = (
    ".tox",
    ".mypy_cache",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "*.egg-info",
)

JUNK_FILES = (
    ".coverage",
)


def _get_dir_size(path: pathlib.Path) -> int:
    """Return the total size of a directory in bytes."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file(follow_symlinks=False):
                total += entry.stat(follow_symlinks=False).st_size
    except OSError:
        pass
    return total


def _format_size(size_bytes: int) -> str:
    """Format a size in bytes to a human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _find_junk(cache_folder: pathlib.Path):
    """Yield (path, size) for all junk directories and files in the cache."""
    for repo in cache_folder.iterdir():
        if not repo.is_dir():
            continue
        for pattern in JUNK_DIRS:
            for match in repo.rglob(pattern):
                if match.is_dir():
                    yield match, _get_dir_size(match)
        for pattern in JUNK_FILES:
            for match in repo.rglob(pattern):
                if match.is_file():
                    yield match, match.stat().st_size


@click.option("--cache-folder", default=".cache", help="Path to the cache folder.")
@click.option("--full", is_flag=True, help="Remove the entire cache folder.")
@click.option("--dry-run", is_flag=True, help="Show what would be removed without deleting.")
@click.command()
def main(cache_folder: str, full: bool, dry_run: bool):
    """Clean up disk space used by cached charm repositories.

    By default, removes known build artifact directories (.tox, __pycache__,
    etc.) and runs `git clean -fdx` in each repo. Use --full to delete the
    entire cache folder.
    """
    FORMAT = "%(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=FORMAT,
        datefmt="[%X]",
        handlers=[rich.logging.RichHandler()],
    )

    cache_path = pathlib.Path(cache_folder)
    if not cache_path.exists():
        logger.info("Cache folder %s does not exist, nothing to clean.", cache_folder)
        return

    if full:
        size = _get_dir_size(cache_path)
        if dry_run:
            click.echo(f"Would remove entire cache folder: {cache_path.resolve()}")
            click.echo(f"Estimated space to reclaim: {_format_size(size)}")
        else:
            shutil.rmtree(cache_path)
            click.echo(f"Removed entire cache folder: {cache_path.resolve()}")
            click.echo(f"Space reclaimed: {_format_size(size)}")
        return

    total_reclaimed = 0
    items_removed = 0

    for path, size in _find_junk(cache_path):
        if dry_run:
            click.echo(f"Would remove: {path} ({_format_size(size)})")
        else:
            logger.info("Removing %s (%s)", path, _format_size(size))
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        total_reclaimed += size
        items_removed += 1

    # Also run git clean in each repo.
    import subprocess

    for repo in cache_path.iterdir():
        if not repo.is_dir() or not (repo / ".git").exists():
            continue
        if dry_run:
            result = subprocess.run(
                ["git", "clean", "-fdxn"],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            if result.stdout.strip():
                click.echo(f"Would git-clean in {repo.name}:")
                for line in result.stdout.strip().splitlines():
                    click.echo(f"  {line}")
        else:
            result = subprocess.run(
                ["git", "clean", "-fdx"],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            if result.stdout.strip():
                logger.info("git clean in %s:\n%s", repo.name, result.stdout.strip())

    action = "Would remove" if dry_run else "Removed"
    click.echo(f"\n{action} {items_removed} items.")
    click.echo(f"{'Estimated space' if dry_run else 'Space'} reclaimed: {_format_size(total_reclaimed)}")
    if dry_run:
        click.echo("(Note: git clean savings not included in estimate.)")


if __name__ == "__main__":
    main()
