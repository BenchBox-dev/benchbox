# BenchBox CLI Reference

```{tags} reference, cli
```

This section provides comprehensive documentation for all BenchBox command-line interface (CLI) commands and options.

## Global Options

These options are available for all BenchBox commands:

- `--version`: Show version information and exit
- `--help`: Show help message and exit

## Commands Overview

BenchBox provides the following commands:

| Command                | Purpose                                            | Documentation                             |
| ---------------------- | -------------------------------------------------- | ----------------------------------------- |
| `run`                  | Execute benchmarks with full configuration control | [run](run.md)                             |
| `convert`              | Convert data to Parquet, Vortex, Delta Lake, or Iceberg | [convert](convert.md)                     |
| `shell`                | Launch interactive SQL shell for database          | [shell](shell.md)                         |
| `platforms`            | Manage database platform adapters                  | [platforms](platforms.md)                 |
| `check-deps`           | Check dependency status and installation guidance  | [utilities](utilities.md#check-deps)      |
| `profile`              | Profile system resources                           | [utilities](utilities.md#profile)         |
| `benchmarks`           | Manage benchmark suites                            | [utilities](utilities.md#benchmarks)      |
| `validate`             | Validate configuration files                       | [utilities](utilities.md#validate)        |
| `tuning init`          | Generate sample tuning configurations              | [tuning](tuning.md#tuning-init)           |
| `tuning` (DataFrame)   | DataFrame tuning via unified tuning commands        | [tuning](tuning.md#tuning-dataframe)      |
| `export`               | Export benchmark results to various formats        | [results](results.md#export)              |
| `submit`               | Package results for PR contribution or hosted API upload | [submit](submit.md)                   |
| `auth`                 | Manage hosted submission credentials               | [auth](auth.md)                           |
| `results`              | Display exported benchmark results                 | [results](results.md#results)             |
| `compare`              | Compare benchmark results for regression detection | [results](results.md#compare)             |
| `visualize`            | Generate ASCII charts from benchmark results       | [visualize](visualize.md)                 |
| `report`               | Historical analysis, rankings, and regressions     | [report](report.md)                       |
| `metrics`              | Calculate TPC Power@Size and Throughput@Size      | [metrics](metrics.md)                     |
| `aggregate`            | Aggregate results into CSV performance trends      | [aggregate](aggregate.md)                 |
| `datagen`              | Generate benchmark data without running queries    | [datagen](datagen.md)                     |
| `setup`                | Interactive cloud platform credential setup        | [setup](setup.md)                         |
| `show-plan`            | Display query plan as ASCII tree                   | [query-plans](query-plans.md)             |
| `plan-history`         | Show query plan evolution across runs              | [query-plans](query-plans.md)             |
| `download-answers`     | Pre-download TPC answer files for validation       | [download-answers](download-answers.md)   |

## Quick Start

`--help-topic all` shows all options including advanced ones, and
`--help-topic examples` shows categorized usage examples.

```bash
benchbox run --platform duckdb --benchmark tpch --scale 0.01

benchbox run --help
benchbox run --help-topic all
benchbox run --help-topic examples
```

## Documentation Sections

```{toctree}
:maxdepth: 1
:caption: CLI Commands

run
convert
shell
platforms
utilities
tuning
results
submit
auth
visualize
report
metrics
aggregate
datagen
setup
query-plans
download-answers
configuration
workflows
```
