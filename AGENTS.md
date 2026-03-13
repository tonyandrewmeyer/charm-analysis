# Agent Notes for charm-analysis

## Project Overview

This repo contains tools for bulk-testing Juju charm repositories against the
`ops` library (canonical/operator). The workflow is:

1. `tools/get_charms.py charms.csv` - clones/pulls all charm repos listed in the
   CSV into `.cache/`
2. `tools/super-tox.py -e unit` - runs `tox -e unit` across all cached repos,
   optionally patching their ops dependency to point at a specific git source/branch

## Key Files

- `super-tox.toml` - ignore lists for repos that are expensive, require manual intervention, can't install deps, don't use ops, or are misc broken
- `tools/get_charms.py` - async git clone/pull tool
- `tools/super-tox.py` - async tox runner with ops dependency patching
- `test_charms.csv`, `test_small.csv`, `test_mini.csv` - smaller test lists

## Running

```bash
# Clone all repos
cd /home/ubuntu/charm-analysis
python3 tools/get_charms.py charms.csv

# Run unit tests against ops main
cd /home/ubuntu/charm-analysis
python3 tools/get_charms.py charms.csv
python3 tools/super-tox.py -e unit --verbose --workers 4

# Run against a specific ops branch
python3 tools/super-tox.py -e unit --ops-source https://github.com/canonical/operator --ops-source-branch main

# Test a single repo
python3 tools/super-tox.py -e unit --repo "vault-k8s-operator"

# Use a smaller test list
python3 tools/get_charms.py test_charms.csv
python3 tools/super-tox.py -e unit
```
