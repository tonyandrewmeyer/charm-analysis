# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

charm-analysis is a collection of CLI tools for bulk analysis of Juju operator charms. It clones 150+ charm repositories and runs various analyses (dependencies, metadata, test infrastructure, code patterns, etc.), producing Rich-formatted ASCII table reports. There is no test suite for this project itself; validation is done by running the tools against real charm repositories.

## Running Tools

All tools are standalone Python scripts in `tools/`. They require Python 3.11+ and dependencies managed with `uv`:

```bash
uv run python tools/get_charms.py --cache-folder=.cache charms.csv
uv run python tools/summarise_dependencies.py --cache-folder=.cache
uv run python tools/summarise_libs.py --cache-folder=.cache
uv run python tools/summarise_metadata.py --cache-folder=.cache
uv run python tools/summarise_tests.py --cache-folder=.cache
uv run python tools/summarise_code.py --cache-folder=.cache
uv run python tools/summarise_artifacts.py --cache-folder=.cache
uv run python tools/summarise_init.py --cache-folder=.cache
uv run python tools/super-tox.py --cache-folder=.cache
```

`get_charms.py` must be run first to populate the cache with cloned repos. The input CSV must have "Charm Name" and "Repository" columns.

## Linting and Formatting

```bash
pre-commit run --all-files
# Or directly:
ruff check tools/
ruff format tools/
```

Ruff runs with `--preview` mode enabled. Pre-commit excludes the `lib/` directory.

## Architecture

- **`tools/helpers.py`** — Shared utilities used by all analysis scripts:
  - `configure_logging()` — Centralised logging setup with RichHandler, called by every CLI tool's `main()`.
  - `iter_repositories(base)` — Iterates charm folders, automatically handling monorepos (detected via charmcraft.yaml/metadata.yaml heuristic) and bundles (bundle.yaml). Skips reactive and hook-based charms.
  - `iter_entries(base)` — Finds charm entry points (defaults to `src/charm.py`, reads `charmcraft.yaml` for custom entrypoints).
  - `iter_python_src(base)` — Finds all Python files under `src/` directories.
  - `count_and_percentage_table()` — Generates Rich tables with count/percentage columns.

- **`tools/get_charms.py`** — Async git clone/pull using `asyncio.TaskGroup`. Converts HTTPS GitHub URLs to `git@` for auth. Shallow single-branch clones.

- **`tools/super-tox.py`** — Async tox orchestrator. Can patch ops library versions in charm dependencies. Configured via `super-tox.toml` for repository exclusions. Key flags: `--executable`, `--ops-source`, `--workers`, `--sample`.

- **`tools/summarise_*.py`** — Each analysis script follows the same pattern: iterate repos via helpers, collect data into counters/collections, print Rich tables. `summarise_code.py` and `summarise_init.py` use Python's `ast` module for source code introspection. `summarise_artifacts.py` queries the CharmHub API via `httpx`.

## Key Patterns

- All CLI tools use `click` for argument parsing.
- Async I/O via `asyncio.TaskGroup()` (Python 3.11+ requirement) for concurrent git/tox operations.
- The `.cache` folder (configurable via `--cache-folder`) holds all cloned charm repositories and is not committed.
- CSV files (`charms.csv`, `test_*.csv`) list charm repositories to analyze and are not committed.
