<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# PrestoDB Platform Guide

```{tags} intermediate, guide, presto, sql-platform
```

PrestoDB is Meta's distributed SQL query engine (the original Presto project). BenchBox provides a dedicated PrestoDB adapter that intentionally diverges from the Trino adapter to respect protocol, driver, and dialect differences between the two forks.

## Overview

**Type**: Distributed SQL query engine
**Common Use Cases**: Legacy Presto clusters, Meta ecosystem deployments, Presto-compatible managed services
**BenchBox Extra**: `uv add benchbox --extra presto`
**Driver-only install**: `python -m pip install presto-python-client`

### Why a Separate Adapter (Not Shared With Trino)

- **Driver API**: PrestoDB uses `presto-python-client` (`prestodb` import) while Trino uses the `trino` package. Cursor semantics and auth helpers differ.
- **Protocol Headers**: PrestoDB sends `X-Presto-*` headers; Trino sends `X-Trino-*`. Mixing them causes authentication failures.
- **Dialect**: SQLGlot exposes a dedicated `presto` dialect. Trino inherits from it but overrides functions (LOG base order, TRIM handling, JSON behavior, LISTAGG rules).
- **System Metadata**: System schemas diverge (e.g., runtime tables, connector properties), so platform info queries cannot be shared.

The adapter follows the recommended path of keeping PrestoDB and Trino isolated for clarity and maintainability.

### SQL Dialect Notes (SQLGlot)

- PrestoDB uses the `presto` dialect identifier (`adapter.get_target_dialect()` returns `"presto"`).
- Function differences vs Trino include:
  - `LOG` argument order (base-first), distinct from Trino's override
  - `TRIM`, `JSON_QUERY`, and `LISTAGG` parsing
  - Array and aggregation transforms (e.g., `ArraySum`, `GroupConcat`, `Merge`) use Presto-specific rewrites
- Use this adapter when you need PrestoDB-specific query rendering; use the Trino adapter for Trino/Starburst workloads.

## Quick Start

Install the Presto extra (includes `presto-python-client` and `cloudpathlib`):

```bash
uv add benchbox --extra presto
```

```python
from benchbox.platforms.presto import PrestoAdapter
from benchbox import TPCH

adapter = PrestoAdapter(
    host="presto-coordinator.example.com",
    port=8080,
    catalog="hive",
    schema="default",
    username="presto",
)

benchmark = TPCH(scale_factor=1.0)
results = benchmark.run_with_platform(adapter)

print(f"Completed in {results.duration_seconds:.2f}s")
```

The adapter also accepts an optional `password` for basic auth.

### CLI Usage

The CLI reads connection settings from the `presto` section of `~/.benchbox/credentials.yaml`. Values written as `${VAR}` are read from the environment:

```yaml
presto:
  host: presto-coordinator.example.com
  port: 8080
  username: presto
  password: ${PRESTO_PASSWORD}
  schema: default
```

Then choose the catalog on the command line:

```bash
benchbox run --platform presto --benchmark tpch --scale 1.0 \
  --platform-option catalog=hive
```

The `catalog`, `table_format`, `staging_root`, and `source_catalog` settings are platform options. When `schema` is not set, BenchBox generates a schema name from the benchmark and scale factor.

## Configuration Highlights

- **Connection**: `host`, `port` (default `8080`), `catalog`, `schema`
- **Authentication**: Basic auth via `presto-python-client`, using `username` and `password` from the credentials file; Kerberos is not supported
- **Protocol**: `http_scheme` auto-selects `https` when a password is provided; SSL verification is configurable via `verify_ssl` or `ssl_cert_path`
- **Dialect**: Hard-coded to `"presto"` to avoid Trino behavior
- **Session Properties**: Can be set via `session_properties` mapping (applied after connect with `SET SESSION`)
- **Data Loading**: Uses batch `INSERT` statements with small batches tuned for PrestoDB; supports memory or Hive catalogs

## When to Choose PrestoDB vs Trino vs Amazon Athena

- **PrestoDB**: Existing Presto deployments, Meta ecosystem, or environments pinned to Presto-only features.
- **Trino**: Recommended default for modern clusters and Starburst Enterprise.
- **Amazon Athena**: AWS-managed Presto/Trino lineage with serverless execution.
