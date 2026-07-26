# Charm Analysis Tools

A collection of utilities to perform analysis on a set of charms.

## Populating the cache

The analysis tools below expect a `.cache` folder containing cloned charm
repositories. Use [canonical/hyrum](https://github.com/canonical/hyrum) to
clone and refresh the repositories; it has succeeded the previous
`get_charms.py` and `super-tox.py` tools that used to live here.

## Tools

### summarise_dependencies

Attempts to answer questions like:

* What version of ops is used?
* Are dependencies listed in requirements.txt, setup.py, or pyproject.toml?
* What dependencies are there other than ops?
* What version of Python is required?
* What optional dependency configurations are defined?

Example output:

```
             Dependency Sources
┏━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Source               ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ poetry               │ 15    │ 9.9        │
│ requirements-dev.txt │ 7     │ 4.6        │
│ requirements.txt     │ 142   │ 94.0       │
└──────────────────────┴───────┴────────────┘

                                            Ops Versions
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Version                                                                      ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ ops                                                                          │ 47    │ 31.1       │
│ ops < 2.0                                                                    │ 1     │ 0.7        │
│ ops >= 1.2.0                                                                 │ 2     │ 1.3        │
│ ops~=2.3.0                                                                   │ 1     │ 0.7        │
│ ops~=2.8.0                                                                   │ 1     │ 0.7        │
└──────────────────────────────────────────────────────────────────────────────┴───────┴────────────┘

           Common Dependencies
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Package          ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ jsonschema       │ 80    │ 53.0       │
│ lightkube-models │ 64    │ 42.4       │
│ lightkube        │ 63    │ 41.7       │
│ jinja2           │ 58    │ 38.4       │
│ pyyaml           │ 56    │ 37.1       │
│ tenacity         │ 56    │ 37.1       │
│ websocket-client │ 47    │ 31.1       │
│ requests         │ 46    │ 30.5       │
│ cryptography     │ 41    │ 27.2       │
│ pydantic         │ 41    │ 27.2       │
└──────────────────┴───────┴────────────┘

      Common Dependencies and Version
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Package            ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ pyyaml==6.0.1      │ 39    │ 25.8       │
│ requests==2.31.0   │ 38    │ 25.2       │
│ jsonschema         │ 37    │ 24.5       │
│ certifi==2023.7.22 │ 36    │ 23.8       │
│ idna==3.4          │ 36    │ 23.8       │
└────────────────────┴───────┴────────────┘

pyproject.toml Optional Dependency
             Sections
┏━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Section    ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ lib_pydeps │ 2     │ 1.3        │
└────────────┴───────┴────────────┘
```

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

Provides answers to questions like:

* What version of Juju is used?
* What other assumptions does the metadata define?
* How many of each type of container is required?
* How many of each type of storage is required?
* How many of each type of device is required? (Note: currently none, so not in the output).
* What types of relations are defined?
* What types of resources are required?

Example output:

```
            Juju Versions
┏━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Version       ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ juju          │ 2     │ 1.3        │
│ juju >= 2.9   │ 1     │ 0.7        │
│ juju >= 2.9.0 │ 2     │ 1.3        │
│ juju >= 3.0.2 │ 2     │ 1.3        │
│ juju >= 3.0.3 │ 2     │ 1.3        │
│ juju >= 3.1   │ 7     │ 4.6        │
└───────────────┴───────┴────────────┘

              Assumes
┏━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Requirement ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ k8s-api     │ 52    │ 34.4       │
└─────────────┴───────┴────────────┘

                    Common Resources
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Resource                         ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ oci-image                        │ 31    │ 20.5       │
│ httpbin-image                    │ 3     │ 2.0        │
│ lego-image                       │ 3     │ 2.0        │
│ postgresql-image                 │ 2     │ 1.3        │
│ statsd-prometheus-exporter-image │ 2     │ 1.3        │
└──────────────────────────────────┴───────┴────────────┘

                    Common Relations
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Relation                        ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ certificates : tls-certificates │ 33    │ 21.9       │
│ ingress : ingress               │ 31    │ 20.5       │
│ logging : loki_push_api         │ 20    │ 13.2       │
│ nginx-route : nginx-route       │ 10    │ 6.6        │
│ catalogue : catalogue           │ 7     │ 4.6        │
└─────────────────────────────────┴───────┴────────────┘

           Storage Types
┏━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Storage    ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ filesystem │ 58    │ 38.4       │
└────────────┴───────┴────────────┘
```

### summarise_config

Covers the `config` section of the metadata, which `summarise_metadata` does
not. Attempts to answer questions like:

* How many config options does a charm declare?
* What types are the options?
* How many options have a default, and how many have a description?
* Are option names separated with `-` or `_`?
* Which option names are shared across the fleet?

Example output:

```
4 out of 5 charms declare config options, 11 in total.

       Options per Charm
┏━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Options ┃ Count ┃ Percentage ┃
┡━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ 1       │     1 │       25.0 │
│ 2       │     1 │       25.0 │
│ 3       │     1 │       25.0 │
│ 5       │     1 │       25.0 │
└─────────┴───────┴────────────┘

          Option Types
┏━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Type    ┃ Count ┃ Percentage ┃
┡━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ boolean │     2 │       18.2 │
│ float   │     1 │        9.1 │
│ int     │     1 │        9.1 │
│ secret  │     1 │        9.1 │
│ string  │     4 │       36.4 │
└─────────┴───────┴────────────┘

           Option Documentation
┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Attribute         ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ Has a default     │     5 │       45.5 │
│ Has a description │     4 │       36.4 │
│ Type is implicit  │     2 │       18.2 │
└───────────────────┴───────┴────────────┘

        Option Naming Style
┏━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Style       ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ hyphen      │     4 │       36.4 │
│ single word │     6 │       54.5 │
│ underscore  │     1 │        9.1 │
└─────────────┴───────┴────────────┘

         Common Option Names
┏━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Name         ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ log-level    │     3 │       60.0 │
│ port         │     1 │       20.0 │
│ enable_thing │     1 │       20.0 │
└──────────────┴───────┴────────────┘
```

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

Attempts to answer questions like:
* What events are observed?
* How many times is defer used?
* How many charms are using `hooks` or `reactive`? (This is currently just in the logging output).
* How many of the repos are bundles? (This is currently just in the logging output).

Example output:

```
                          Events
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Event                             ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ action                            │ 52    │ 35.4       │
│ config_changed                    │ 100   │ 68.0       │
│ install                           │ 64    │ 43.5       │
│ leader_elected                    │ 25    │ 17.0       │
│ pebble_ready                      │ 74    │ 50.3       │
│ relation_broken                   │ 27    │ 18.4       │
│ relation_changed                  │ 44    │ 29.9       │
│ relation_created                  │ 22    │ 15.0       │
│ relation_joined                   │ 37    │ 25.2       │
│ remove                            │ 31    │ 21.1       │
│ start                             │ 25    │ 17.0       │
│ update_status                     │ 45    │ 30.6       │
│ upgrade_charm                     │ 41    │ 27.9       │
└───────────────────────────────────┴───────┴────────────┘

     event.defer() Frequency
┏━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Frequency ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ 0         │ 82    │ 55.8       │
│ 1         │ 22    │ 15.0       │
│ 2         │ 7     │ 4.8        │
│ 3         │ 5     │ 3.4        │
│ 4         │ 6     │ 4.1        │
│ 5         │ 6     │ 4.1        │
│ 6         │ 1     │ 0.7        │
│ 7         │ 1     │ 0.7        │
│ 8         │ 1     │ 0.7        │
│ 9         │ 2     │ 1.4        │
│ 10        │ 1     │ 0.7        │
│ 11        │ 7     │ 4.8        │
│ 12        │ 1     │ 0.7        │
│ 13        │ 3     │ 2.0        │
│ 14        │ 0     │ 0.0        │
│ 15        │ 1     │ 0.7        │
│ 16        │ 1     │ 0.7        │
├───────────┼───────┼────────────┤
│ Total     │ 147   │ 100.0      │
└───────────┴───────┴────────────┘
```

### summarise_artifacts

Attempts to answer questions like:

* Which frameworks and languages are charms using (according to CharmHub)?
* How long is it since charms have published an artifact?

Example output:

```
           Frameworks
┏━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Framework ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ operator  │ 136   │ 99.3       │
└───────────┴───────┴────────────┘

            Languages
┏━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Language ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ python   │ 136   │ 99.3       │
└──────────┴───────┴────────────┘

       Newest Artifact
┏━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Days ┃ Count ┃ Percentage ┃
┡━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ 0    │ 15    │ 10.9       │
│ 1    │ 35    │ 25.5       │
│ 2    │ 14    │ 10.2       │
│ 3    │ 3     │ 2.2        │
│ 4    │ 1     │ 0.7        │
│ 6    │ 1     │ 0.7        │
│ 8    │ 2     │ 1.5        │
│ 9    │ 4     │ 2.9        │
│ 10   │ 2     │ 1.5        │
│ 906  │ 1     │ 0.7        │
└──────┴───────┴────────────┘

       Oldest Artifact
┏━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Days ┃ Count ┃ Percentage ┃
┡━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ 1    │ 3     │ 2.2        │
│ 2    │ 1     │ 0.7        │
│ 3    │ 3     │ 2.2        │
│ 7    │ 1     │ 0.7        │
│ 8    │ 2     │ 1.5        │
│ 9    │ 1     │ 0.7        │
│ 986  │ 1     │ 0.7        │
│ 995  │ 1     │ 0.7        │
│ 1036 │ 1     │ 0.7        │
└──────┴───────┴────────────┘
```

### summarise_history

Uses the git history of the cloned repositories to answer questions like:

* When was the charm last worked on?
* How busy has it been over the last 90 and 365 days?
* How many people have worked on it?
* How long has it existed?
* Is the default branch `main` or `master`?

In a monorepo, the history is scoped to the folder holding each charm, so the
answers are about the charm rather than about its neighbours. Pass
`--no-details` to leave out the per-charm table and print only the aggregates.
Note that a shallow clone has no early history: the first commit and the number
of authors are lower bounds, and the affected repositories are logged.

Example output:

```
4 out of 4 charms have a git history.

                                                Charm History
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┓
┃ Charm                                ┃ Last Commit ┃ First Commit ┃ Commits (90d) ┃ Commits (365d) ┃ Authors ┃ Branch ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━┩
│ grafana-k8s-operator                 │ 2026-07-14  │ 2020-08-28   │            30 │             73 │      42 │ main   │
│ istio-operators/charms/istio-gateway │ 2026-07-16  │ 2021-10-07   │             2 │              7 │      20 │ main   │
│ istio-operators/charms/istio-pilot   │ 2026-07-16  │ 2020-03-30   │             1 │              7 │      24 │ main   │
│ postgresql-k8s-operator              │ 2026-07-21  │ 2026-07-21   │             1 │              1 │       1 │ main   │
└──────────────────────────────────────┴─────────────┴──────────────┴───────────────┴────────────────┴─────────┴────────┘

               Last Commit
┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Age               ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ up to a week      │     1 │       25.0 │
│ up to a month     │     3 │       75.0 │
│ up to 3 months    │     0 │        0.0 │
│ up to 6 months    │     0 │        0.0 │
│ up to a year      │     0 │        0.0 │
│ up to 2 years     │     0 │        0.0 │
│ more than 2 years │     0 │        0.0 │
└───────────────────┴───────┴────────────┘

     Commits in the Last 90 Days
┏━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Commits      ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ 0            │     0 │        0.0 │
│ 1            │     2 │       50.0 │
│ 2-5          │     1 │       25.0 │
│ 6-20         │     0 │        0.0 │
│ 21-50        │     1 │       25.0 │
│ more than 50 │     0 │        0.0 │
└──────────────┴───────┴────────────┘

          Distinct Authors
┏━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Authors      ┃ Count ┃ Percentage ┃
┡━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ 0            │     0 │        0.0 │
│ 1            │     1 │       25.0 │
│ 2-5          │     0 │        0.0 │
│ 6-20         │     1 │       25.0 │
│ 21-50        │     2 │       50.0 │
│ more than 50 │     0 │        0.0 │
└──────────────┴───────┴────────────┘

      Checked-out Branch
┏━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┓
┃ Branch ┃ Count ┃ Percentage ┃
┡━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━┩
│ main   │     3 │      100.0 │
└────────┴───────┴────────────┘
```

## To-do

* [x] When was the (main branch of the) repo last updated? (`summarise_history`)
* [ ] Is the charm on charmhub? If so, when was it last published?
* [ ] Check ['definition of great'](https://docs.google.com/document/d/1_2jTMSAaHRSX8B08upqOfhYyxAg9LwmcoS8eD-uVHx0/edit#heading=h.xc0m9jobc7ma)
* [ ] I think there's a bug in the monorepo handling where it thinks that the cache folder is a monorepo.
* [ ] It seems like mixing the charmhub info and the regular info would be informative.
* [ ] Should add the repo for the tutorial so we don't break that (and add repos for any other significant docs?)
