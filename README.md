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

Provides insight into the Charm libs that are used and provided by the charms.
For example:

```
            Charm Lib Usage
┏━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Number of Libs ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ 0              │ 30    │ 19.9       │
│ 1              │ 28    │ 18.5       │
│ 2              │ 19    │ 12.6       │
│ 3              │ 16    │ 10.6       │
│ 4              │ 11    │ 7.3        │
│ 5              │ 11    │ 7.3        │
│ 6              │ 13    │ 8.6        │
│ 7              │ 10    │ 6.6        │
│ 8              │ 7     │ 4.6        │
│ 9              │ 4     │ 2.6        │
│ 10             │ 1     │ 0.7        │
│ 11             │ 1     │ 0.7        │
├────────────────┼───────┼────────────┤
│ Total          │ 151   │ 100.0      │
└────────────────┴───────┴────────────┘

                   Common Charm Libs
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Lib                            ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ observability_libs             │ 79    │ 52.3       │
│ prometheus_k8s                 │ 55    │ 36.4       │
│ grafana_k8s                    │ 47    │ 31.1       │
│ tls_certificates_interface     │ 38    │ 25.2       │
│ data_platform_libs             │ 37    │ 24.5       │
│ loki_k8s                       │ 28    │ 18.5       │
│ traefik_k8s                    │ 22    │ 14.6       │
│ operator_libs_linux            │ 19    │ 12.6       │
│ nginx_ingress_integrator       │ 15    │ 9.9        │
│ grafana_agent                  │ 10    │ 6.6        │
│ rolling_ops                    │ 9     │ 6.0        │
│ sdcore_nrf                     │ 8     │ 5.3        │
│ tempo_k8s                      │ 7     │ 4.6        │
│ catalogue_k8s                  │ 7     │ 4.6        │
│ kubeflow_dashboard             │ 7     │ 4.6        │
│ certificate_transfer_interface │ 5     │ 3.3        │
│ harness_extensions             │ 4     │ 2.6        │
│ istio_pilot                    │ 4     │ 2.6        │
│ postgresql_k8s                 │ 4     │ 2.6        │
│ zookeeper                      │ 4     │ 2.6        │
└────────────────────────────────┴───────┴────────────┘
```

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
