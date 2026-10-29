# Dependency Inventory

```{tags} contributor, reference
```

Evidence-backed inventory of every dependency declared in `pyproject.toml`.
Built for the `audit-imported-dependencies-for-elimination` TODO and intended
to be the entry point when a contributor needs to know *why* a dep is in the
manifest, *who* uses it, and *whether* it can be dropped.

This document complements (does **not** replace) `dependency-compatibility.md`
(version caps on kept deps) and `dependency-audit-raw.md` (raw `pyproject.toml`
extract). Together:

| Document | Question it answers |
| --- | --- |
| `dependency-inventory.md` *(this file)* | Does this dep belong in the manifest at all? |
| `dependency-compatibility.md` | Is the version cap on this kept dep correct? |
| `dependency-audit-raw.md` | What does `pyproject.toml` literally declare? |

> **Process note.** This audit is **inventory + flag only**. No `pyproject.toml`,
> `uv.lock`, or `benchbox/` source change accompanies it. Each elimination
> recommendation lands as its own follow-up TODO so each removal carries its
> own test surface and reviewer.

---

## Methodology

1. **Source of truth for declared deps.** Parsed `pyproject.toml` with
   `tomllib` (`_project/scripts/dependency_audit/parse_deps.py`). `uv pip list`
   was deliberately avoided because it includes transitives and masks which
   extras group owns a package.
2. **Source of truth for import sites.** Walked every `.py` file under
   `benchbox/`, `scripts/` and `tests/` with
   `ast.parse` (`_project/scripts/dependency_audit/scan_imports.py`). For
   `from X import a, b` we record both `X` and the synthesized `X.a`, `X.b`
   so namespace packages (`google.cloud.*`, `azure.*`, `databricks.*`) are
   matched correctly.
3. **`_project/scripts/` was scanned separately.** Imports there belong to
   internal tooling, not the shipped wheel - packages used *only* there are
   called out so reviewers can decide whether the dep belongs in the public
   manifest at all.
4. **Transitive reach** is read from `uv tree`. A package that has no direct
   import sites but is required by another live dep is annotated
   `transitive-via-X` rather than flagged unused.
5. **Plugin-style deps verified.** `pytest-*`, `ruff`, `ty`, `tox`, `mutmut`,
   `codespell`, `pre-commit` and `pygments` are CLI tools or build-time plugins.
   They are correctly absent from
   `import` lines and were verified live by checking the relevant config
   surface (`pytest.ini`).
6. **Ongoing check.** `make audit-deps` runs
   `_project/scripts/dependency_audit/check_deps.py` in CI (`.github/workflows/lint.yml`)
   and fails if any declared package has zero import sites and is not in one
   of the two allowlists in `_project/scripts/dependency_audit/`. To add a
   justified exception, append an entry with a `reason:` to the appropriate
   allowlist file.

---

## Package → import-name map

Most package names match their top-level import name. The cases below do not -
keep this map in sync when adding or auditing deps.

| Package | Top-level import(s) |
| --- | --- |
| `pyyaml` | `yaml` |
| `markdown-it-py` | `markdown_it` |
| `psycopg2-binary` | `psycopg2` |
| `beautifulsoup4` *(not declared)* | `bs4` |
| `google-cloud-bigquery` | `google.cloud.bigquery`, `google.cloud.bigquery_storage` |
| `google-cloud-storage` | `google.cloud.storage` |
| `google-cloud-dataproc` | `google.cloud.dataproc_v1`, `google.cloud.dataproc` |
| `snowflake-connector-python` | `snowflake.connector` |
| `snowflake-snowpark-python` | `snowflake.snowpark` |
| `azure-identity` | `azure.identity` |
| `azure-storage-file-datalake` | `azure.storage.filedatalake` |
| `databricks-sql-connector` | `databricks.sql` |
| `databricks-sdk` | `databricks.sdk` |
| `databricks-connect` | `databricks.connect` |
| `presto-python-client` | `prestodb` |
| `redshift-connector` | `redshift_connector` |
| `firebolt-sdk` | `firebolt` |
| `ruamel-yaml` | `ruamel.yaml` |
| `influxdb3-python` | `influxdb_client_3` |
| `databend-driver` | `databend_driver` |
| `vortex-data` | `vortex` |
| `chdb-core` | `chdb_core` *(transitive of `chdb`)* |
| `delta-spark` | `delta` |
| `pytest-xdist` | `xdist` |
| `pytest-cov` | `pytest_cov` |
| `pytest-benchmark` | `pytest_benchmark` |
| `pytest-timeout` | `pytest_timeout` |
| `codespell` | `codespell_lib` |
| `opentelemetry-exporter-otlp-proto-http` | `opentelemetry.exporter.otlp.proto.http` |
| `opentelemetry-sdk` | `opentelemetry.sdk` |

---

## Inventory

Categories: **C** = Core, **CL** = CLI/runtime support, **SQL** = SQL platform
adapter, **CS** = Cloud storage, **DF** = DataFrame engine, **BM** = Benchmark
data/format, **DEV** = Dev/test/lint/CI, **DOC** = Docs build, **MCP** = MCP
server. **TF** = Table format. **CSP** = Cloud Spark adapter.

Status legend: **KEEP** = live import sites, retained. **FLAG-UNUSED** = no
import sites in tracked source, no transitive role, no plugin/CLI use.
**FLAG-REDUNDANT** = duplicates a dep already pulled in by another extras
group with overlapping consumers. **FLAG-DEAD-EXTRA** = entire extras group
or declaration appears unused.

| Package | Cat | Owner module(s) | Import sites | Status |
| --- | --- | --- | ---: | --- |
| `chdb-core` | SQL | (transitive of `chdb`) | 0 | **FLAG-REDUNDANT** - installed transitively by `chdb`; declaring it as a *core* dep means every install pulls it even when chdb extras are not selected. See finding F1. |
| `click` | CL | `benchbox/cli/**` | 92 | KEEP |
| `jsonschema` | DEV | `_project/scripts/validate_todo.py` | 0 (runtime); 1 (tooling) | **FLAG-REDUNDANT** - declared as a *core* dep but only used by internal TODO tooling under `_project/`. See finding F2. |
| `numpy` | C | `benchbox/core/**`, `benchbox/experimental/**`, tests | 42 | KEEP |
| `packaging` | CL | `benchbox/core/query_plans/parsers/registry.py`, `benchbox/platforms/dataframe/platform_checker.py`, `benchbox/utils/**` | 8 | KEEP |
| `psutil` | CL | `benchbox/cli/**`, `benchbox/core/**`, `benchbox/monitoring/**`, `benchbox/mcp/**` | 39 | KEEP |
| `pyarrow` | C | `benchbox/core/**`, `benchbox/platforms/**`, `benchbox/utils/**`, tests | 120 | KEEP |
| `pydantic` | CL | `benchbox/cli/**`, `benchbox/core/**`, `benchbox/mcp/**`, tests | 6 | KEEP |
| `pyyaml` | C | `benchbox/cli/**`, `benchbox/core/**`, `scripts/`, `benchbox/security/**`, tests | 23 | KEEP |
| `rich` | CL | `benchbox/cli/**`, `benchbox/core/**`, `benchbox/platforms/**`, `benchbox/utils/**` | 146 | KEEP |
| `sqlglot` | — | `benchbox/_vendor/**` | 0 (public package) | BUNDLED - BenchBox ships only the pinned `BenchBox-dev/benchbox-sqlglot` runtime wheel; the public package is no longer a runtime requirement; advisories are checked by `make sqlglot-advisory-check`, which maps the pinned upstream version to OSV (pip-audit cannot see the bundle) |
| `textcharts` | C | `benchbox/core/visualization/ascii/**`, `benchbox/monitoring/**` | 20 | KEEP — retained by ADR follow-up; removing it would break supported ASCII shims and monitoring imports |
| `tomli` | — | — | 0 | REMOVED - Python 3.11's standard-library `tomllib` is now the minimum runtime |
| `zstandard` | C | `benchbox/core/primitives/**`, `benchbox/utils/**`, tests | 9 | KEEP |
| `azure-identity` | CSP | `benchbox/platforms/azure/**` | 9 | KEEP |
| `azure-storage-file-datalake` | CSP | `benchbox/platforms/azure/**`, `benchbox/platforms/base/cloud_spark/staging.py` | 3 | KEEP |
| `boto3` | CS | `benchbox/platforms/athena.py`, `benchbox/platforms/aws/**`, `scripts/`, tests | 32 | KEEP |
| `chdb` | SQL | `benchbox/platforms/clickhouse/**`, tests | 5 | KEEP |
| `clickhouse-connect` | SQL | `benchbox/platforms/clickhouse/_dependencies.py` | 1 | KEEP - HTTP protocol used by ClickHouse Cloud adapter |
| `clickhouse-driver` | SQL | `benchbox/platforms/clickhouse/**`, tests | 4 | KEEP - TCP protocol used by ClickHouse server adapter |
| `cloudpathlib` | CS | `benchbox/cli/**`, `benchbox/core/**`, `benchbox/utils/**`, tests | 16 | KEEP |
| `dask` | DF | `benchbox/platforms/dataframe/dask_df.py`, tests | 8 | KEEP |
| `databend-driver` | SQL | `benchbox/platforms/databend/**` | 6 | KEEP |
| `databricks-connect` | CSP | `benchbox/platforms/base/cloud_spark/session.py`, `benchbox/platforms/databricks/dataframe_adapter.py` | 2 | KEEP |
| `databricks-sdk` | CSP | `benchbox/platforms/base/cloud_spark/staging.py`, `benchbox/platforms/databricks/**`, tests | 14 | KEEP |
| `databricks-sql-connector` | SQL | `benchbox/platforms/databricks/adapter.py` | 3 | KEEP |
| `datafusion` | DF | `benchbox/platforms/dataframe/datafusion_df.py`, tests | 105 | KEEP |
| `delta-spark` | TF | `benchbox/core/metadata_primitives/dataframe_operations.py`, `benchbox/platforms/**` | 9 | KEEP |
| `deltalake` | TF | `benchbox/core/data_organization/**`, `benchbox/platforms/**`, `benchbox/utils/**`, tests | 38 | KEEP |
| `duckdb` | SQL | `benchbox/cli/**`, `benchbox/core/**`, `benchbox/platforms/**`, `benchbox/utils/**`, tests | 80 | KEEP |
| `firebolt-sdk` | SQL | `benchbox/platforms/firebolt.py` | 4 | KEEP |
| `google-cloud-bigquery` | SQL | `benchbox/platforms/bigquery.py`, tests | 8 | KEEP |
| `google-cloud-dataproc` | CSP | `benchbox/platforms/gcp/dataproc_adapter.py`, `benchbox/platforms/gcp/dataproc_serverless_adapter.py` | 2 | KEEP |
| `google-cloud-storage` | CS | `benchbox/platforms/base/cloud_spark/staging.py`, `benchbox/platforms/bigquery.py` | 7 | KEEP |
| `influxdb3-python` | SQL | `benchbox/platforms/influxdb/_dependencies.py` | 1 | KEEP |
| `mcp` | MCP | `benchbox/mcp/**`, tests | 18 | KEEP |
| `pandas` | DF/BM | `benchbox/core/**`, `benchbox/experimental/**`, `benchbox/platforms/**`, tests | 182 | KEEP (extras only) - removed from core deps; supplied by `extras:{pandas,dataframe-pandas,tpcdi,dask}` and transitively by `chdb`. See finding F7. |
| `polars` | DF | `benchbox/cli/**`, `benchbox/core/**`, `benchbox/platforms/**`, tests | 60 | KEEP |
| `presto-python-client` | SQL | `benchbox/platforms/presto.py` | 2 | KEEP |
| `psycopg2-binary` | SQL | `benchbox/platforms/pg_*.py`, `scripts/`, tests | 18 | KEEP |
| `pyathena` | SQL | `benchbox/platforms/athena.py` | 3 | KEEP |
| `pyiceberg` | TF | `benchbox/core/metadata_primitives/dataframe_operations.py`, `benchbox/platforms/base/data_loading.py`, `benchbox/utils/**`, tests | 16 | KEEP |
| `pymysql` | SQL | `benchbox/platforms/doris.py`, `benchbox/platforms/starrocks/**`, tests | 7 | KEEP |
| `pyodbc` | SQL | `benchbox/platforms/azure_synapse.py`, `benchbox/platforms/fabric_lakehouse.py` | 3 | KEEP |
| `pyspark` | DF/CSP | `benchbox/core/**`, `benchbox/platforms/**`, tests | 102 | KEEP |
| `redshift-connector` | SQL | `benchbox/platforms/redshift.py` | 1 | KEEP |
| `requests` | CS | `benchbox/platforms/azure/**`, tests | 22 | KEEP - also reaches transitively via boto3 / google-cloud-* / snowflake; explicit declaration in `extras:cloud-spark*` and `extras:questdb` is intentional. See finding F4. |
| `singlestoredb` | SQL | `benchbox/platforms/singlestore.py`, `benchbox/platforms/credentials/singlestore.py` | 2 | KEEP |
| `snowflake-connector-python` | SQL | `benchbox/platforms/snowflake.py`, `benchbox/platforms/credentials/snowflake.py` | 4 | KEEP |
| `snowflake-snowpark-python` | CSP | `benchbox/platforms/snowpark_connect.py` | 2 | KEEP |
| `trino` | SQL | `benchbox/platforms/trino.py`, tests | 3 | KEEP |
| `vortex-data` | TF | `benchbox/platforms/base/data_loading.py`, `benchbox/utils/format_converters/vortex_converter.py` | 2 | KEEP |
| `pytest` | DEV | `tests/**` | 965 | KEEP |
| `pytest-benchmark` | DEV | (CLI plugin via `pytest`) | 0 | KEEP - pytest plugin loaded by entry point; verified used in `pytest-benchmark` markers |
| `pytest-cov` | DEV | (CLI plugin via `pytest`) | 0 | KEEP - `--cov` option used by `make coverage*` and CI |
| `pytest-timeout` | DEV | (CLI plugin via `pytest`) | 0 | KEEP - pytest plugin (timeout config in `pytest.ini` markers) |
| `pytest-xdist` | DEV | (CLI plugin via `pytest`) | 0 | KEEP - `-n auto` baked into `pytest.ini` addopts |
| `ruff` | DEV | (CLI tool) | 0 | KEEP - `ruff check`/`ruff format`; pinned `==0.16.7` |
| `ty` | DEV | (CLI tool) | 0 | KEEP - `uv run ty check`; configured under `[tool.ty]` |
| `tox` | DEV | (CLI tool) | 0 | REVIEW - no `tox.ini` remains; no config or CI step invokes it |
| `mutmut` | DEV | (CLI tool) | 0 | KEEP - `[tool.mutmut]` config block targets specific files |
| `codespell` | DEV | (CLI tool) | 0 | KEEP - invoked via `pre-commit` and CI |
| `pre-commit` | DEV | (CLI tool) | 0 | KEEP - invoked by local hooks and content-guard CI |
| `lxml` | DEV | `tests/unit/core/tpcdi/test_etl_sources.py` | 1 | **FLAG-UNUSED-RUNTIME** - only test imports; verify whether `benchbox/core/tpcdi/etl/sources.py` actually requires lxml at runtime (it parses XML). If yes, promote to runtime extra; if no, keep dev-only. See finding F3. |
| `clickhouse-connect` | DEV | (also in dev for tests) | covered above | KEEP |
| `cloudpathlib[s3,gs,azure]` | DEV | tests use `cloudpathlib` core | covered above | KEEP - extras pin S3/GCS/Azure providers for live tests |
| `pyiceberg[sql-sqlite,pyarrow]` | DEV | tests use `pyiceberg` core | covered above | KEEP - extras pin SQL-SQLite catalog for tests |
| `ruamel-yaml` | DEV | (none) | 0 | **REMOVAL CANDIDATE** - its only consumer was `_project/scripts/todo_cli.py`, removed in the 0.6.x MCP cutover. todo-db now handles YAML import through `pyyaml` (`_project/scripts/pyproject.toml`). No current declaration or import; confirm before dropping. |
| `pygments` | DEV | `scripts/comment_syntax.py` | 1 | KEEP - comment-policy lexers |

(Total declared package names: **76**. KEEP: **70**. FLAG-*: **6**.)

---

## Elimination candidates (FLAG findings)

### F1 - `chdb-core` should be moved out of core dependencies

- **Where:** `pyproject.toml` line 49 (`[project] dependencies`).
- **Evidence:**
  - `chdb-core` has zero direct `import chdb_core` sites in `benchbox/`, `tests/`,
    `scripts/`, or `docs/`.
  - `uv tree` confirms `chdb-core` is depended on by `chdb v4.1.6`, which itself
    is declared in **optional** extras (`extras:all`, `extras:clickhouse-local`,
    `dep-group:dev`).
  - As declared today, every minimal install pulls `chdb-core` (~tens of MB,
    bundled C++ binary) even when the user did not request the chdb extra.
- **Recommended action:** Remove `chdb-core>=26.1.0` from
  `[project] dependencies`. It will continue to be installed transitively
  whenever `chdb` is selected.
- **Risk:** If a code path imports `chdb_core` directly (none found), it would
  break. Verification command: `grep -r "chdb_core" benchbox tests scripts`.

### F2 - `jsonschema` should not be a core runtime dependency

- **Where:** `pyproject.toml` line 42.
- **Evidence:**
  - Zero import sites in `benchbox/`, `tests/`, `scripts/`, or `docs/`.
  - Single import site is `_project/scripts/validate_todo.py:18` - internal
    TODO management tooling that is **not** part of the shipped wheel
    (`_project/` is excluded from packaging via `tool.setuptools.packages.find`
    convention and is not in any package data).
- **Recommended action:** Either move `jsonschema` to a tooling-only
  `[dependency-groups]` entry (e.g. add to `dev`), or extract `_project/`
  scripts to their own pinned environment under `_project/scripts/pyproject.toml`.
- **Risk:** Low. `jsonschema` is an unconditional install today; removing it
  from core would shrink the wheel install set by ~2 MB plus its (relatively
  small) transitive surface (`attrs`, `referencing`, `rpds-py`, `jsonschema-specifications`).

### F3 - `lxml` is declared in `dep-group:dev` but only one test imports it

- **Where:** `pyproject.toml` line 482.
- **Evidence:**
  - `lxml` has one import site: `tests/unit/core/tpcdi/test_etl_sources.py:22`.
  - `grep -r "^import lxml\|^from lxml\|lxml\." benchbox/` returns **no
    matches** - TPC-DI ETL uses `xml.etree` (stdlib), not `lxml`.
- **Recommended action:** Either remove `lxml>=5.0.0` from `dep-group:dev`
  and rewrite the single test to use stdlib `xml.etree`, or keep the dep and
  document the test rationale inline. Removal is the lower-surface option.
- **Risk:** Low. The single test (`test_etl_sources.py`) would need to be
  rewritten to drop the lxml-specific assertion path.

### F4 - `requests` is reachable transitively but explicitly declared in three extras

- **Where:** `pyproject.toml` lines 172 (`extras:questdb`), 291 (`extras:cloud-spark-azure`),
  322 (`extras:cloud-spark`).
- **Evidence:**
  - 22 import sites use `requests` directly.
  - `uv tree` shows `requests` is reachable transitively from `boto3`,
    `google-cloud-*`, `snowflake-connector-python`, and `azure-identity`,
    so installs that pull any of these get `requests` for free.
  - The explicit declarations are nonetheless valuable: they protect Azure /
    QuestDB / generic Spark installs that do **not** pull a transitive
    provider.
- **Recommended action:** **Keep as declared.** Document the rationale here
  so future audits don't churn on it. (No follow-up TODO needed.)
- **Risk:** Removing the explicit declarations would reintroduce silent
  fragility - minimal QuestDB or Azure-only installs would lose `requests`.

### F5 and F6 - Sphinx extras

Resolved: the Sphinx documentation build and every Sphinx package left the
repository when benchbox.dev moved to `BenchBox-dev/benchbox-site`.

---

## Used-but-undeclared findings

Walking `benchbox/`, `scripts/` and `tests/`
turned up the following top-level imports for which **no** `pyproject.toml`
declaration exists. Each is classified below.

| Top-level module | Classification | Notes |
| --- | --- | --- |
| `chardet` | transitive-reach | Pulled in by `requests` / cloud SDKs. Direct use is implicit; safe today. |
| `coverage` | transitive-reach | Pulled in by `pytest-cov`. Safe. |
| `cryptography` | transitive-reach | Pulled in by `snowflake-connector-python`, `azure-identity`. Safe. |
| `cudf`, `cugraph`, `cuml`, `cupy`, `dask_cudf`, `pynvml`, `rmm` | guarded GPU | NVIDIA RAPIDS / CUDA stack. All are guarded behind `try/except ImportError` in `benchbox/platforms/dataframe/cudf_df.py` and friends. The placeholder `cudf` extra in `pyproject.toml` documents this. **No action.** |
| `dask_sql` | guarded optional | Optional Dask SQL backend; guarded import. |
| `flightsql` | guarded optional | Apache Arrow Flight SQL client; guarded import in InfluxDB / Doris paths. |
| `importlib_metadata` | first-party alias | Not a third-party package - `benchbox/utils/format_converters/vortex_converter.py:11` and `benchbox/utils/runtime_env.py:24` use `from importlib import metadata as importlib_metadata` (stdlib aliased). False positive. |
| `influxdb3` | guarded optional | Alternate InfluxDB client; guarded fallback in `benchbox/platforms/influxdb/_dependencies.py`. |
| `pysail` | guarded optional | LakeSail Spark distribution; guarded import in `benchbox/platforms/lakesail.py`. |
| `sentence_transformers`, `spacy`, `textblob`, `torch` | declared (extras) | NLP / ML stacks for `benchbox/core/ai_primitives/`. All guarded behind `try/except`. Now declared in `extras:ai-primitives` as `sentence-transformers>=2.0.0`, `torch>=2.0.0`, `textblob>=0.17.0`, `spacy>=3.0.0`. |
| `urllib3` | transitive-reach | Pulled in by `requests`. Safe. |
| `automate_release`, `build_release`, `check_windows_antipatterns`, `examples`, `finalize_release`, `generate_corpus_inventory`, `scripts`, `tests`, `unified_runner`, `unified_test_runner`, `update_version`, `utilities`, `validate_submission`, `verify_release` | first-party | Local scripts / packages - not third-party. |

**Status after `declare-undeclared-runtime-imports`:**

- `sentence_transformers`, `spacy`, `textblob`, `torch` declared in `extras:ai-primitives`.
- `PIL` in `benchbox/` or `tests/` - zero import sites found; no declaration needed in runtime extras.

---

## Consolidation proposals (no action required by this audit)

1. **`dataframe-*` aliases.** The plain-name extras (`pandas`, `polars`,
   `dask`, `pyspark`, `cudf`) duplicate the `dataframe-*` extras
   one-to-one. Cleanup is a breaking rename and is explicitly *deferred*
   per this TODO's `deferred[]`. Surfacing here for traceability.
2. **`databricks-connect` extras alias.** Marked deprecated in
   `pyproject.toml:304` in favor of `cloud-spark-databricks`. Removal is a
   breaking change and is *deferred*.
3. **`extras:all` vs per-extra duplication.** Every package in `extras:all`
   is also present in at least one focused extra. This is intentional and
   documented in `dependency-compatibility.md`.

---

## Summary

| Bucket | Count |
| --- | ---: |
| Total declared packages | 76 |
| KEEP | 70 |
| FLAG-* (elimination candidates) | 6 |
| Used-but-undeclared (audit candidates) | 0 (all resolved: AI-primitives → `extras:ai-primitives`) |

Each FLAG-* finding has a corresponding follow-up TODO under
`_project/TODO/main/planning/`. This audit performs **no removals**; the
follow-ups carry their own verification, `must_preserve`, and reviewer.
