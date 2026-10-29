<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

```text
█                   █    █
█▀▀▄ █▀▀█ █▀▀▄ █▀▀▀ █▀▀▄ █▀▀▄ ▄▀▀▄ ▀▄▄▀
█▄▄▀ █▄▄▄ █  █ █▄▄▄ █  █ █▄▄▀ ▀▄▄▀ ▄▀▀▄
```

# BenchBox

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Beta Software](https://img.shields.io/badge/Status-Beta-blue.svg)](https://github.com/BenchBox-dev/benchbox/issues)
[![codecov](https://codecov.io/github/BenchBox-dev/BenchBox/graph/badge.svg?token=3NY6DK7MDO)](https://codecov.io/github/BenchBox-dev/BenchBox)
[![PyPI Release](https://img.shields.io/pypi/v/benchbox)](https://pypi.org/project/benchbox/)
[![PyPI Downloads](https://img.shields.io/pepy/dt/benchbox.svg?label=PyPI%20Downloads)](https://pypi.org/project/benchbox/)

BenchBox is an open-source Python toolkit for benchmarking analytical data
platforms. It generates and loads data, runs repeatable workloads, validates
execution, and records comparable results through one workflow.

Use BenchBox to evaluate local databases, cloud data warehouses, and DataFrame
runtimes with the same benchmark definitions. It is built for data engineers,
platform evaluators, and performance practitioners who need evidence they can
inspect and reproduce.

BenchBox focuses on online analytical processing (OLAP). If you need an
online transaction processing (OLTP) benchmark, see the
[comparison of database benchmarking tools](https://benchbox.dev/docs/concepts/benchmarking-tools-compared.html).

## Why BenchBox?

- **Run recognized workloads.** Use TPC-H, TPC-DS, TPC-DI, ClickBench, SSB,
  Join Order Benchmark, and BenchBox's focused primitive workloads.
- **Compare different kinds of engines.** Run the same benchmark against SQL
  databases, cloud warehouses, and native DataFrame APIs.
- **Control the full run.** Generate data, create schemas, load tables, execute
  queries, validate results, and capture metrics from one command.
- **Reproduce results.** Record the benchmark, scale, platform, configuration,
  tuning, environment, timings, and validation evidence in structured result
  bundles.
- **Inspect before you spend.** Preview planned queries, files, phases, and
  configuration with dry-run support.
- **Analyze and share evidence.** Compare runs, render terminal charts, export
  reports, and contribute results to the public Results Explorer.

## Quick Start

The quickest local path uses DuckDB and a small TPC-H dataset.

### 1. Install BenchBox with DuckDB

Using [uv](https://docs.astral.sh/uv/):

```bash
uv add benchbox --extra duckdb
```

Using pip:

```bash
python -m pip install "benchbox[duckdb]"
```

DuckDB is an optional dependency. A plain `benchbox` installation includes
SQLite but does not include DuckDB.

The commands below use the `benchbox` executable installed by either method.
If you used `uv add` and have not activated the project environment, prefix
each command with `uv run --`.

### 2. Run a benchmark

```bash
benchbox run \
  --platform duckdb \
  --benchmark tpch \
  --scale 0.01
```

This command generates about 10 MB of TPC-H data, loads it into DuckDB, runs
the benchmark, validates the execution, and stores the result under
`benchmark_runs/`.

### 3. Inspect the result

```bash
benchbox results
```

The summary lists each recent run's benchmark, platform, timestamp, duration,
query count, and BenchBox version.

To preview a run without executing it:

```bash
benchbox run \
  --dry-run ./preview \
  --platform duckdb \
  --benchmark tpch \
  --scale 0.01
```

Continue with the [five-minute guide](https://benchbox.dev/docs/usage/getting-started.html), or see
the [installation guide](https://benchbox.dev/docs/usage/installation.html) for other platforms and
package extras.

## What can you benchmark?

### Benchmarks

BenchBox includes several kinds of analytical workloads:

- **TPC standards:** TPC-H, TPC-DS, and TPC-DI
- **Academic benchmarks:** SSB, AMPLab, and Join Order Benchmark
- **Industry and real-world workloads:** ClickBench, H2O DB Benchmark, NYC
  Taxi, Flight Data, TSBS DevOps, and CoffeeShop
- **Focused primitives:** read, write, transaction, metadata, and AI operations
- **AI and machine learning:** Vector Search
- **Experimental variants:** TPC-DS One Big Table, TPC-Havoc, TPC-H Skew, and
  TPC-H Data Vault

See the [benchmark catalog](https://benchbox.dev/docs/benchmarks/index.html) for workload details,
resource guidance, and selection help.

### Platforms

BenchBox uses adapters to run workloads across three broad groups:

- local and embedded SQL engines, such as DuckDB, SQLite, and DataFusion;
- cloud warehouses and distributed SQL engines, such as Snowflake, BigQuery,
  Databricks, Redshift, ClickHouse, Spark, Trino, and PrestoDB; and
- native DataFrame runtimes, such as Polars, Pandas, PySpark, DataFusion,
  Dask, and cuDF.

Support status and published evidence answer different questions. A supported
adapter can be available before the public results corpus contains a run for
that platform. Check the [platform guides](https://benchbox.dev/docs/platforms/index.html),
[platform comparison matrix](https://benchbox.dev/docs/platforms/comparison-matrix.html), and
[public support contract](https://benchbox.dev/docs/reference/public-contracts.html) before planning
a comparison.

<!-- benchbox-registry-counts:start -->

- Platform registry: **53** metadata entries; **49** SQL-capable; **18** DataFrame-capable; **14** dual-mode; support status counts: stable=5, beta=28, experimental=19, deprecated=1.
- Benchmark registry: **23** metadata entries; **22** public discovery entries.

<!-- benchbox-registry-counts:end -->

These counts come from BenchBox's registries and are checked in the test suite.
Use `benchbox platforms list` and `benchbox benchmarks list` for the current
names and support details.

## Results you can inspect and compare

Each run records more than a headline time. Result bundles can include query
timings, validation status, resource measurements, platform configuration,
tuning evidence, environment metadata, cost data, and captured query plans.
The available fields depend on the platform and run configuration.

Use the CLI to inspect, visualize, and export local results:

```bash
uv run -- benchbox results
uv run -- benchbox visualize benchmark_runs/results/*.json
uv run -- benchbox export --last --format html
```

The public [BenchBox Results Explorer](https://benchbox.dev/results/) lets you
inspect published runs and their provenance. Comparisons are meaningful only
when the workload, scale, phase, configuration, tuning, hardware, and evidence
are compatible; a shared benchmark name alone is not enough.

To contribute a complete run, follow the
[result contribution guide](https://benchbox.dev/docs/contributing-results.html). It explains local
validation, privacy safeguards, trust labels, and the `published-results`
submission process.

## Learn more

| Goal | Start here |
| --- | --- |
| Install BenchBox | [Installation and environment setup](https://benchbox.dev/docs/usage/installation.html) |
| Run your first benchmark | [Getting started in five minutes](https://benchbox.dev/docs/usage/getting-started.html) |
| Learn the CLI | [CLI quick reference](https://benchbox.dev/docs/usage/cli-quick-start.html) |
| Choose a benchmark | [Benchmark catalog](https://benchbox.dev/docs/benchmarks/index.html) |
| Choose a platform | [Platform selection guide](https://benchbox.dev/docs/platforms/platform-selection-guide.html) |
| Use a DataFrame runtime | [DataFrame platforms](https://benchbox.dev/docs/platforms/dataframe.html) |
| Use the Python API | [Python API reference](https://benchbox.dev/docs/reference/python-api/index.html) |
| Find examples | [Examples guide](https://benchbox.dev/docs/usage/examples.html) |
| Troubleshoot a run | [Troubleshooting guide](https://benchbox.dev/docs/usage/troubleshooting.html) |
| Understand the design | [Architecture overview](https://benchbox.dev/docs/concepts/architecture.html) |
| Add a platform | [Adding new platforms](https://benchbox.dev/docs/development/adding-new-platforms.html) |
| Create a custom benchmark | [Custom benchmark guide](https://benchbox.dev/docs/advanced/custom-benchmarks.html) |

The [documentation index](docs/README.md) links to the complete user,
reference, design, and contributor documentation.

## Installation and platform setup

The quick start intentionally installs only the DuckDB extra. BenchBox offers
separate extras for cloud services, database drivers, DataFrame libraries, and
development tools so that you install only what you need.

See the [installation guide](https://benchbox.dev/docs/usage/installation.html) for the supported
package managers and extras. Then use the dependency checker for your target
platform:

```bash
uv run -- benchbox check-deps --platform databricks
```

Platform guides cover credentials, connection settings, and platform-specific
options. Do not put credentials in configuration files that you commit.

## Project status

> **BenchBox is BETA software.** The CLI and core workflows are usable, but
> public APIs may change before 1.0.

Current release: v0.5.0rc2.

Support labels describe the stability of each public surface. The
`benchbox.experimental` namespace has no compatibility guarantee and can change
or be removed without notice. Read the
[public contracts and support taxonomy](https://benchbox.dev/docs/reference/public-contracts.html)
before depending on an experimental or beta interface.

See [PyPI](https://pypi.org/project/benchbox/) for published releases and
[DISCLAIMER.md](DISCLAIMER.md) for project limitations. Release and versioning
details live in the
[backward-compatibility policy](https://benchbox.dev/docs/reference/backward-compatibility.html) and
[release guide](docs/internal/release-guide.md).

## Contributing

Bug reports, documentation improvements, platform adapters, benchmark work,
and result contributions are welcome.

- Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing the codebase.
- Use [GitHub Issues](https://github.com/BenchBox-dev/benchbox/issues) for bugs
  and feature requests.
- Use [GitHub Discussions](https://github.com/BenchBox-dev/benchbox/discussions)
  for questions and ideas.
- Follow the [result contribution guide](https://benchbox.dev/docs/contributing-results.html) to add a
  benchmark run to the public corpus.

## Disclaimer

BenchBox is an independent open-source project. It is not affiliated with the
Transaction Processing Performance Council or with Joe Harris's past or
present employers. See [DISCLAIMER.md](DISCLAIMER.md) for details.

## License

BenchBox is available under the [MIT License](LICENSE).
