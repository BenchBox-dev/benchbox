# Installation and environment setup

```{tags} beginner, quickstart
```

BenchBox requires Python 3.11 or newer and ships as a single Python package. The recommended workflow uses [uv](https://docs.astral.sh/uv/) for fast installs, but the commands below include alternatives for `pip` and `pipx`.

BenchBox plans to require Python 3.12 in its first release after Python 3.11
reaches end of life in October 2027.

## 1. Install BenchBox

The options below are, in order: `uv` (recommended, modern package management), `uv pip` (pip-compatible), traditional `pip` (uses the active Python environment), and `pipx` for a dedicated CLI environment.

```bash
uv add benchbox

uv pip install benchbox

python -m pip install benchbox

pipx install benchbox
```

BenchBox installs a `benchbox` executable. If you use `uv`, prefer `uv run -- benchbox <command>` to ensure the project virtual environment is activated automatically.

(installation-extras)=
## 2. Pick Optional Extras

Extras keep the base install lean.

Choose the smallest extra that supports the platform you plan to run. This
keeps installation faster, reduces dependency conflicts, and avoids installing
cloud SDKs or database drivers that you do not use. You can add another extra
later by running the matching install command again.

| Extra | Enables | Recommended (uv) | Alternative (pip-compatible) |
| --- | --- | --- | --- |
| `(none)` | SQLite only (core package, no DuckDB) | `uv add benchbox` | `uv pip install benchbox` |
| `[duckdb]` | DuckDB for local analytics | `uv add benchbox --extra duckdb` | `uv pip install "benchbox[duckdb]"` |
| `[cloudstorage]` | Cloud path helpers (S3, GCS, Azure) | `uv add benchbox --extra cloudstorage` | `uv pip install "benchbox[cloudstorage]"` |
| `[cloud]` | Databricks, BigQuery, Redshift, Snowflake connectors | `uv add benchbox --extra cloud` | `uv pip install "benchbox[cloud]"` |
| `[clickhouse]` | ClickHouse native driver | `uv add benchbox --extra clickhouse` | `uv pip install "benchbox[clickhouse]"` |
| `[databricks]` / `[bigquery]` / `[redshift]` / `[snowflake]` | Single-platform installs | `uv add benchbox --extra databricks` | `uv pip install "benchbox[databricks]"` |
| `[all]` | Everything listed above | `uv add benchbox --extra all` | `uv pip install "benchbox[all]"` |

### DuckDB 2.0 Preview

Stable DuckDB releases remain the default. To test the current
[DuckDB 2.0 preview](https://duckdb.org/install/preview), install BenchBox's
DuckDB extra and then select the preview package explicitly. The first two commands are for a `uv` project, and the last is for an active pip environment:

```bash
uv add benchbox --extra duckdb
uv add --prerelease=allow "duckdb==1.6.0.dev379"

python -m pip install "benchbox[duckdb]" "duckdb==1.6.0.dev379"
```

DuckDB distributes the 2.0 alpha engine in the Python package's 1.6 development
series. Use `1.6.0.dev379` (engine `v2.0.0-alpha39998`); later preview builds
are not yet tested with BenchBox.

### Cloud Spark Platforms

For managed Spark platforms, use provider-specific extras to install only the dependencies you need:

| Extra | Platforms | Dependencies |
| --- | --- | --- |
| `[cloud-spark-aws]` | AWS Glue, EMR Serverless, Athena Spark | boto3 |
| `[cloud-spark-gcp]` | Google Cloud Dataproc, Dataproc Serverless | google-cloud-dataproc, google-cloud-storage |
| `[cloud-spark-azure]` | Azure Synapse Analytics Spark, Fabric Spark | azure-identity, azure-storage-file-datalake, requests |
| `[cloud-spark-snowflake]` | Snowflake Snowpark | snowflake-snowpark-python, pyspark |
| `[cloud-spark-databricks]` | Databricks Connect | databricks-connect, databricks-sdk |
| `[cloud-spark]` | All cloud Spark platforms | All of the above |

The first command installs only the AWS Spark dependencies. The second installs all cloud Spark dependencies for multi-cloud use. The third combines Spark extras with other extras:

```bash
uv add benchbox --extra cloud-spark-aws

uv add benchbox --extra cloud-spark

uv add benchbox --extra cloud-spark-aws --extra athena
```

### Combining Extras

The first command (recommended) enables all cloud platforms and ClickHouse. The second is the pip-compatible alternative:

```bash
uv add benchbox --extra cloud --extra clickhouse

uv pip install "benchbox[cloud,clickhouse]"
```

Re-run the installer at any time to add extras. For `pipx`, use `pipx inject benchbox "benchbox[cloud]"`.

### Choose an installer

- Use `uv add` when BenchBox is a dependency of a Python project.
- Use `python -m pip install` in an activated virtual environment when your
  project uses pip.
- Use `pipx install` when you want an isolated, system-wide `benchbox` command.
- Quote `"benchbox[extra]"` with pip-compatible commands so shells such as zsh
  do not interpret the brackets.

## 3. Verify the CLI

```bash
uv run -- benchbox --version
```

The command prints the current BenchBox version and validates that `pyproject.toml`, `benchbox/__init__.py`, and doc version markers match.

## 4. Run Dependency Checks

`benchbox check-deps` inspects optional connectors and suggests install commands. The first command gives an overview of all platforms, the second a detailed matrix with extras guidance, and the third focuses on a single platform:

```bash
uv run -- benchbox check-deps

uv run -- benchbox check-deps --matrix

uv run -- benchbox check-deps --platform snowflake --verbose
```

## 5. Create a Workspace (Optional)

BenchBox stores generated data and results under `benchmark_runs/` by default. Set a custom location with `--output PATH` when invoking `benchbox run`, or point to cloud storage such as `s3://` or `gs://` if the corresponding extras are installed.

For repeatable environments, initialise a project-level virtual environment with `uv venv .venv && source .venv/bin/activate` (or the Windows equivalent) before running the commands above.

## Next Steps

- Follow the [5-minute walkthrough](getting-started.md) for your first benchmark.
- Browse the [CLI quick reference](cli-quick-start.md) to learn the most-used commands.
- Keep [Troubleshooting](troubleshooting.md) handy for resolving dependency or connectivity issues.
