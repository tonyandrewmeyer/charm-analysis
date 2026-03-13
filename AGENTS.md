# Agent Notes for charm-analysis

## Project Overview

This repo contains tools for bulk-testing Juju charm repositories against the
`ops` library (canonical/operator). The workflow is:

1. `get-charms charms.csv` - clones/pulls all charm repos listed in the
   CSV into `.cache/`
2. `super-tox -e unit` - runs `tox -e unit` across all cached repos,
   optionally patching their ops dependency to point at a specific git source/branch

## Key Files

- `super-tox.toml` - ignore lists for repos that are expensive, require manual intervention, can't install deps, don't use ops, or are misc broken
- `tools/get_charms.py` - async git clone/pull tool (entry point: `get-charms`)
- `tools/super_tox.py` - async tox runner with ops dependency patching (entry point: `super-tox`)
- `test_charms.csv`, `test_small.csv`, `test_mini.csv` - smaller test lists

## Running

```bash
# Install the project (entry points become available)
pip install -e .

# Clone all repos
get-charms charms.csv

# Run unit tests against ops main
get-charms charms.csv
super-tox -e unit --verbose --workers 4

# Run against a specific ops branch
super-tox -e unit --ops-source https://github.com/canonical/operator --ops-source-branch main

# Test a single repo
super-tox -e unit --repo "vault-k8s-operator"

# Use a smaller test list
get-charms test_charms.csv
super-tox -e unit
```
