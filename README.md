# Charm Analysis Tools

A collection of utilities to perform analysis on a set of charms.

## Tools

### get_charms

```shellscript
$ python get_charms.py --help
Usage: get_charms.py [OPTIONS] CHARM_LIST

  Ensure updated repositories for all the charms from the provided list.

  If a repository does not exist, clone it, otherwise do a pull. Assumes that
  all the repositories are in an essentially read-only state and so pull will
  run cleanly.

  The `git` CLI tool is used via a subprocess, so must be able to handle any
  authentication required.

Options:
  --cache-folder TEXT
  --help               Show this message and exit.
```

This tool uses the `git` CLI tool to clone a provided list of repositories, or
if those repositories already exist, then will `git pull` each of them. The
clones are shallow single branch, and the assumption is that `pull` will always
run cleanly (for example, because there are no local changes). The CLI should be
configured with appropriate permission to clone and pull each repository.

By default, the repositories are cloned into a `.cache` folder, but this can be
changed using the `--cache-folder` option.

The input must be a CSV file that has "Charm Name" (only used for logging) and
"Repository" columns. The repository must be a source that can be provided to
`git`, for example `https://github.com/canonical/operator`. To simplify
authentication, `https://github.com/` is replaced by `git@github.com:` when
calling the CLI.

### summarise_dependencies

What version of ops is used?
Are dependencies listed in requirements.txt, setup.py, or pyproject.toml?
What dependencies are there other than ops?
What version of Python is required?
What optional dependency configurations are defined?

### summarise_libs

What charm libs are used?
How outdated are the libs being used?
Does the charm provide a lib?

### summarise_metadata

What version of Juju is used?
What other assumptions does the metadata define?
How many of each type of container is required?
How many of each type of storage is required?
How many of each type of device is required?
What types of relations are defined?
What types of resources are required?

### summarise_tests

Provides insight into the automated tests that the charms have. For example:

```
149 out of 151 (98.7%) use tox.

       Unit Test Libraries
┏━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Library  ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ unittest │ 90    │ 60.4       │
│ pytest   │ 144   │ 96.6       │
└──────────┴───────┴────────────┘

           Testing Frameworks
┏━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Framework       ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ Harness         │ 123   │ 82.6       │
│ Scenario        │ 15    │ 10.1       │
│ pytest-operator │ 116   │ 77.9       │
└─────────────────┴───────┴────────────┘

          Common Tox Environments
┏━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Environment         ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ lint                │ 149   │ 100.0      │
│ unit                │ 144   │ 96.6       │
│ integration         │ 127   │ 85.2       │
│ fmt                 │ 122   │ 81.9       │
│ static              │ 44    │ 29.5       │
│ update-requirements │ 39    │ 26.2       │
│ coverage-report     │ 24    │ 16.1       │
│ scenario            │ 20    │ 13.4       │
│ format              │ 19    │ 12.8       │
│ src-docs            │ 13    │ 8.7        │
└─────────────────────┴───────┴────────────┘
```

### summarise_code

What events are observed?
How many times is defer used?
How many charms are using `hooks` or `reactive`?
How many of the repos are bundles?

## To-do

When was the (main branch of the) repo last updated?
Is the charm on charmhub? If so, when was it last published?
Properly parse the version specifiers for the dependencies (also avoid more of the FPs)
