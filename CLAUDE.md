# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

charm-analysis is a collection of CLI tools for bulk analysis of Juju operator charms. It clones 150+ charm repositories and runs various analyses (dependencies, metadata, test infrastructure, code patterns, etc.), producing Rich-formatted ASCII table reports. There is no test suite for this project itself; validation is done by running the tools against real charm repositories.

## Running Tools

All tools are standalone Python scripts in `tools/`. They require Python 3.11+ and dependencies managed with `uv`:

```bash
uv run python tools/summarise_dependencies.py --cache-folder=.cache
uv run python tools/summarise_libs.py --cache-folder=.cache
uv run python tools/summarise_metadata.py --cache-folder=.cache
uv run python tools/summarise_tests.py --cache-folder=.cache
uv run python tools/summarise_code.py --cache-folder=.cache
uv run python tools/summarise_artifacts.py --cache-folder=.cache
uv run python tools/summarise_init.py --cache-folder=.cache
```

The `.cache` folder must be populated with cloned charm repositories before running these tools. Use [canonical/hyrum](https://github.com/canonical/hyrum) to clone and refresh the repos; it replaces the former `get_charms.py` and `super-tox.py` tools.

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

- **`tools/summarise_*.py`** — Each analysis script follows the same pattern: iterate repos via helpers, collect data into counters/collections, print Rich tables. `summarise_code.py` and `summarise_init.py` use Python's `ast` module for source code introspection. `summarise_artifacts.py` queries the CharmHub API via `httpx`.

## Key Patterns

- All CLI tools use `click` for argument parsing.
- The `.cache` folder (configurable via `--cache-folder`) holds all cloned charm repositories and is not committed.
- CSV files (`charms.csv`, `test_*.csv`) list charm repositories to analyze and are not committed.
