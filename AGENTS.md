# Agent Notes for charm-analysis

## Project Overview

This repo contains tools for bulk analysis of Juju charm repositories. The
analysis tools (`tools/summarise_*.py`) read from a `.cache/` folder of cloned
charm repositories.

Cloning/refreshing the repos and running tox across them is now handled by
[canonical/hyrum](https://github.com/canonical/hyrum), which has succeeded the
previous `get_charms.py` and `super-tox.py` tools.

## Key Files

- `tools/helpers.py` - shared utilities for iterating repositories and entry points
- `tools/summarise_*.py` - individual analysis scripts
- `test_charms.csv`, `test_small.csv`, `test_mini.csv` - smaller charm lists for testing
