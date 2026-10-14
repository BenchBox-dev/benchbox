<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# MCP Server Reference

```{tags} reference, advanced
```

Complete reference for the BenchBox MCP (Model Context Protocol) server, including all available tools, resources, and prompts.

## Running the Server

### Prerequisites

Install BenchBox with the MCP extra, using either `uv pip` or `pip`:

```bash
uv pip install "benchbox[mcp]"

python -m pip install "benchbox[mcp]"
```

In a source checkout, run `uv sync --extra mcp` instead.

The MCP extra includes DuckDB because `duckdb` is the advertised local
execution platform in the MCP surface. Other platforms keep their separate
optional extras.

### Starting the Server

The first command starts the server through the installed entry point. The second starts it through the Python module. The third runs it from a source checkout. The fourth sets explicit MCP path overrides. The fifth opts in to localhost Streamable HTTP.

```bash
benchbox-mcp

python -m benchbox.mcp

uv run benchbox-mcp

benchbox-mcp --results-dir /tmp/benchbox-results --charts-dir /tmp/benchbox-charts

benchbox-mcp --transport streamable-http
```

The default server communicates via stdio using JSON-RPC, compatible with
Claude Code and other local MCP clients. Streamable HTTP is an explicit
localhost-only option at `http://127.0.0.1:8000/mcp`.

```json
{
  "mcpServers": {
    "benchbox": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

Do not expose the unauthenticated localhost endpoint through a public bind,
port forward, or reverse proxy. Non-loopback binding requires a complete
`--security-config` policy. See [Remote MCP security and tenancy](../operations/mcp-remote-security.md)
for its threat model, token-digest provisioning, scopes, tenant workspaces,
shared admission store, and fail-closed proxy requirements. This capability is
not a production-readiness claim.

### SDK Compatibility

BenchBox uses the Python MCP SDK 2.x `MCPServer` API and reports its own
`benchbox` server name and BenchBox package version during initialization.
The stdio server exposes 12 tools, 4 static resources, 2 resource templates,
and 7 prompts.

An authenticated remote server adds four durable job tools. They use shared
storage and return immediately, so sessionless requests may reach different
workers without losing ownership or lifecycle state.

Streamable HTTP supports modern MCP `2026-07-28` as a sessionless protocol.
The only supported legacy handshake is `2025-11-25`; earlier revisions are
not supported. Each request can reach any server process and no `Mcp-Session-Id` is issued.
The same endpoint retains the SDK's stateless compatibility path for supported
handshake-era clients. Protocol discovery, version negotiation, headers, and
DNS-rebinding checks are provided by the MCP SDK rather than reimplemented by
BenchBox. Responses remain streaming-capable; JSON-only mode is intentionally
disabled so progress and future request-scoped notifications remain possible.

### Testing Locally

To verify the server works, you can test it interactively. This command starts the server and sends a test request:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | benchbox-mcp
```

This should return a JSON response listing all available tools.

### Using the MCP Inspector

For interactive testing, use the pinned official
[MCP Inspector](https://github.com/modelcontextprotocol/inspector). This command connects to an already-running localhost Streamable HTTP endpoint:

```bash
npx --yes @modelcontextprotocol/inspector@2.0.0 --cli \
  http://127.0.0.1:8000/mcp --transport http --method tools/list --format json
```

The inspector provides a web UI to browse tools, test calls, and view responses.

### Server Options

`benchbox-mcp` supports explicit flags and environment-variable fallback.

**CLI flags**

| Flag | Description |
|------|-------------|
| `--results-dir` | Results root used by MCP result reads/writes |
| `--charts-dir` | Charts root used by MCP visualization paths |
| `--log-level` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `--transport` | `stdio` (default) or opt-in `streamable-http` |
| `--host` | Streamable HTTP bind host; non-loopback requires `--security-config` (default `127.0.0.1`) |
| `--port` | Streamable HTTP bind port (default `8000`) |
| `--streamable-http-path` | Streamable HTTP endpoint path (default `/mcp`) |
| `--security-config` | Remote-only JSON policy for SDK auth, tenancy, authorization, admission, and audit |

**Environment variables**

| Variable | Default | Description |
|----------|---------|-------------|
| `BENCHBOX_RESULTS_DIR` | `benchmark_runs/results` | Results root when `--results-dir` is not provided |
| `BENCHBOX_CHARTS_DIR` | `benchmark_runs/charts` | Charts root when `--charts-dir` is not provided |
| `BENCHBOX_OUTPUT_DIR` | `benchmark_runs` | Base root used to derive results/charts when specific vars are unset |
| `BENCHBOX_LOG_LEVEL` | `INFO` | Logging level when `--log-level` is not provided |

A non-loopback bind also needs `--readiness-evidence` and its related
environment variables. See
[Remote MCP security and tenancy](../operations/mcp-remote-security.md).

Discovery and list responses carry a public five-minute cache hint. Resource
bodies, including recent results and system profiles, are always private and
immediately stale. BenchBox exports only bounded allow-listed MCP span fields;
tool arguments, result payloads, identities, authorization data, credentials,
and raw database URLs are excluded.

**Precedence**

1. Explicit MCP flag (`--results-dir`, `--charts-dir`, `--log-level`)
2. Specific env var (`BENCHBOX_RESULTS_DIR`, `BENCHBOX_CHARTS_DIR`, `BENCHBOX_LOG_LEVEL`)
3. Derived from `BENCHBOX_OUTPUT_DIR` for paths
4. Built-in defaults (`benchmark_runs/results`, `benchmark_runs/charts`, `INFO`)

Example:

```bash
BENCHBOX_RESULTS_DIR=/tmp/results BENCHBOX_LOG_LEVEL=DEBUG benchbox-mcp
```

Additional localhost examples: the first uses IPv4 loopback with a custom port and path, and the second uses IPv6 loopback.

```bash
benchbox-mcp --transport streamable-http --port 8765 --streamable-http-path /benchbox-mcp

benchbox-mcp --transport streamable-http --host ::1
```

## Tools

Tools are executable actions that can be invoked by AI assistants. BenchBox MCP
is a **beta-public scoped surface over the shared BenchBox engine**: all
benchmark business logic lives in `benchbox.core` below both CLI and MCP, and
each surface exposes a chosen subset of it. Differences between the CLI and MCP
surfaces are deliberate: the CLI controls that MCP does not expose, and the reason
for each, are listed in the omission tables below the `run_benchmark` behavior
notes.

MCP run results are exported through `ResultExporter` as normal result JSON
bundles and include `execution_context.entry_point = "mcp"` when the result
object supports execution context metadata. Because both surfaces compute
results with the same core implementation, MCP numbers are comparable to CLI
numbers by construction; the bundles are schema-level comparable to CLI result
bundles.

### Actual Tool Inventory

| Tool | Category | Writes | Purpose |
|---|---|---:|---|
| `list_available` | discovery | No | List platforms, benchmarks, chart templates, or all discovery data. |
| `get_benchmark_info` | discovery | No | Return benchmark metadata, queries, schema, and scale-factor information. |
| `system_profile` | discovery | No | Return CPU, memory, disk, Python, package, and BenchBox environment facts. |
| `check_dependencies` | discovery | No | Report platform dependency availability and install guidance. |
| `run_benchmark` | execution | Yes | Run, dry-run, or validate a benchmark through the MCP-scoped subset of the shared engine. |
| `get_query_details` | execution aid | No | Return SQL or DataFrame query details for a benchmark/query/platform. |
| `get_results` | results | Optional | List result files, read one result, or export a result in another format. |
| `analyze_results` | analytics | No | Compare result files, detect regressions, calculate trends, or aggregate runs. |
| `get_query_plan` | analytics | No | Read captured query plans from a result bundle. |
| `validate_results` | analytics | No | Validate result JSON integrity, completeness, and believability. |
| `suggest_charts` | visualization | No | Suggest useful chart types for one or more result files. |
| `generate_chart` | visualization | No | Generate ASCII chart output from result files. |

Authenticated remote mode additionally registers:

| Tool | Category | Writes | Purpose |
|---|---|---:|---|
| `start_benchmark` | durable execution | Yes | Queue a tenant-owned benchmark and return an `execution_id`. |
| `get_benchmark_status` | durable execution | No | Read owned job state, attempts, cancellation, and timestamps. |
| `get_benchmark_result` | durable execution | No | Read the owned result after atomic publication. |
| `cancel_benchmark` | durable execution | Yes | Cancel queued work or request cancellation at the next safe worker boundary. |
| `get_benchmark_capacity` | durable execution | No | Read queue depth, running capacity, owned usage, and quarantined jobs. |

### Run Surface Contract

`run_benchmark` remains the synchronous local-compatible execution tool.
Dry-run preview and configuration validation are modes on it (`dry_run=true`
and `validate_only=true`), not separate MCP tools. Authenticated remote clients
should use `start_benchmark` for a normal long-running execution; the durable
tool accepts the same normal-run fields plus an optional `idempotency_key`, but
does not expose `dry_run` or `validate_only`. In remote mode, a normal
`run_benchmark` call is rejected immediately; its `dry_run` and `validate_only`
modes remain available because they do not hold the request for execution.

**MCP run parameter schema**

| Name | Type | Required | Default | Description |
|---|---|---:|---|---|
| `platform` | string | Yes | - | Target platform, for example `duckdb`, `snowflake`, or `polars-df`. A platform whose manifest declares deployment modes also accepts `<platform>:<mode>`, for example `clickhouse:server` or `timescaledb:cloud`; an undeclared mode is rejected. |
| `benchmark` | string | Yes | - | Benchmark identifier, for example `tpch`, `tpcds`, or `joinorder`. |
| `scale_factor` | number | No | `0.01` | Data scale factor; benchmark-specific defaults and constraints may still apply. |
| `queries` | string or null | No | `null` | Comma-separated query IDs, for example `1,3,6`. |
| `phases` | string or null | No | `null` | Comma-separated phases; execution defaults to `load,power` when omitted. |
| `mode` | string or null | No | `null` | Execution mode: `sql`, `dataframe`, or `data_only`. |
| `execution_engine` | string | No | `default` | Execution engine: which execution machinery runs the query, validated against the platform manifest; equivalent to the CLI `--execution-engine` flag. |
| `capture_plans` | boolean | No | `false` | Capture query plans where the selected platform supports them. |
| `dry_run` | boolean | No | `false` | Preview the run plan without executing queries. |
| `validate_only` | boolean | No | `false` | Validate platform, benchmark, scale, and mode without executing. |
| `link_probe` | boolean | No | `true` | Measure post-benchmark statement overhead (6 metered `SELECT 1` statements on billable warehouses). Set `false` to skip; equivalent to the CLI `--no-link-probe` flag. |
| `platform_options` | object or null | No | `null` | Typed, bounded, non-secret settings approved for the selected platform; credentials, endpoints, paths, and package-install controls are rejected. |

**Behavior**

- `validate_only=true` returns configuration validity, resolved execution mode,
  errors, and warnings.
- `dry_run=true` uses the core dry-run executor and returns the plan/resources
  preview the MCP subset can model; it currently reports the default
  load/power plan rather than applying the `phases` parameter.
- `dry_run=true` and `validate_only=true` apply `execution_engine`,
  `platform_options` and the `<platform>:<mode>` selector through the same
  core validation as a real run. A conflicting or unsupported combination is
  rejected, and a valid one is echoed back as `execution_engine`, `deployment`
  and `platform_options` in the response.
- Every response from `run_benchmark`, `start_benchmark`,
  `get_benchmark_status`, `cancel_benchmark` and `get_benchmark_result`
  carries a `deprecations` list, empty when nothing deprecated was used. Each
  entry is `{option, replacement, removed_in, message}`. `replacement` names
  the MCP form to use instead, either an MCP parameter such as
  `execution_engine='streaming'` or a platform name such as `clickhouse-server`,
  and `removed_in` is the BenchBox release that drops the old spelling. The
  list covers the request being answered, so a status call reports the
  deprecations of the job it describes. `deprecations` is added to the
  response next to the existing fields; no other field changes.
- Under a remote security policy, `allowed_platforms` is matched against the
  platform string exactly as sent. Listing `clickhouse` does not admit
  `clickhouse:server`; list the selector form you want to allow.
- `mode=data_only` generates benchmark data without running queries. MCP
  accepts this value but `benchbox run --mode` does not: on the CLI, use
  `--phases generate` for the same result. `sql` and `dataframe` select how
  queries run; `data_only` means "run no queries at all". `datagen` and
  `generate` are accepted spellings of `data_only`.
- `phases` is validated against
  `benchbox.core.constants.VALID_PHASES` at admission, on both `run_benchmark`
  and `start_benchmark`. An unknown phase is rejected with the valid list.
- `phases` applies to normal execution and maps to the benchmark execution type
  used by `BaseBenchmark.run_with_platform()`.
- `platform_options` is normalized and validated before any adapter is built.
  The allow-list is intentionally narrower than the CLI's
  `--platform-option` surface: only bounded execution settings such as
  DuckDB `memory_limit`/`threads`, DataFusion partition settings, and selected
  DataFrame toggles are accepted. Unknown keys, credentials, DSNs, hosts,
  filesystem paths, unbounded values, and driver auto-install/version controls
  fail closed. Authenticated durable jobs persist only this normalized object,
  so retries and worker restarts cannot reintroduce raw request mappings.
- Velox `deployment` is not exposed over MCP. Local execution is the only deployment MCP can fully describe; `remote` would require an operator-approved endpoint (`sc://`) and additional packaging/runtime controls that are not part of the MCP allow-list. Both `remote` and `docker` are rejected at admission, so a request can never redirect execution to an endpoint it did not name via a server-owned profile. `docker` is rejected: the `docker/velox/` tree is packaging infrastructure for local development, not a deployment mode with its own lifecycle, endpoint, isolation, and cleanup contract. See the `benchbox run` flag table below.
- DuckDB `threads` is the public option name and maps to the adapter's
  `thread_limit`, which becomes a `SET threads` statement on the connection.
- Databricks clustering options are translated into effective tuning before the
  adapter is built. `databricks_clustering_strategy` and
  `liquid_clustering_columns` become a `PlatformOptimizationConfiguration` that
  the clustering resolver consumes; forwarding the raw names would leave them to
  be dropped by `from_config` and silently fall back to ZORDER. Contradictory
  combinations (for example `z_order` with liquid clustering columns) are
  rejected as validation errors at admission, before a durable job is persisted
  and before any remote connection.
- Dask cluster sizing is bounded in aggregate, not only per field. A request's
  `n_workers`, `n_workers` x `threads_per_worker`, and `n_workers` x
  `memory_limit` must all fit inside a server-owned budget, enforced before the
  adapter builds its `LocalCluster` (see
  [MCP remote security](../operations/mcp-remote-security.md)).
- ClickHouse connection destinations are server-owned. A request cannot set
  `port` or `secure`; both are rejected for every ClickHouse spelling. A
  non-default port or TLS setting is reachable only through
  `connection_profile`, which names a profile the operator defined in
  `BENCHBOX_MCP_CLICKHOUSE_PROFILES` (see
  [MCP remote security](../operations/mcp-remote-security.md)). Requests carry
  and persist only the profile name; the port and TLS policy are resolved from
  server configuration at execution time. `connection_profile` is accepted when
  the request resolves to the server deployment: platform `clickhouse:server` or
  `clickhouse-server`. It does not need `deployment_mode`.
- ClickHouse `deployment_mode` is a deprecated spelling of the
  `clickhouse:local` and `clickhouse:server` platform names and is answered
  with a `deprecations` entry. The `mode` spelling is not accepted over MCP.
  Velox `velox:remote` is rejected for the same reason as the Velox
  `deployment` option below.
- The authoritative option-to-consumer, security-class, alias, and rejection
  matrix is maintained in
  `docs/development/mcp-platform-option-contract.md`. Every allow-listed key
  must have a matching matrix entry; a missing entry fails closed before
  adapter construction or durable-job persistence.
- Normal execution uses `BaseBenchmark.run_with_platform()` through public
  benchmark and adapter APIs.
- MCP execution intentionally suppresses console output and returns structured
  JSON for agent clients.
- Exported result bundles are anonymized when, and only when, the server runs
  under a remote security policy. Local stdio serves a same-trust-boundary agent
  that needs real paths and hostnames to act on results; a remote tenant is a
  different trust boundary, so `run_benchmark`, durable job publication, and
  `analyze_results` comparisons all export with anonymization enabled there.

**Scoped-surface omission ledger**

Some CLI commands and `benchbox run` controls are not available over MCP. The
tables below list each one with one of three reasons:

- **security-scoped** — will not be added. The control could name credentials,
  endpoints, filesystem or cloud destinations, or unbounded resources, or could
  trigger destructive or publishing side effects. Where MCP needs a safe part of
  such a control, it offers a separate bounded, server-validated parameter.
- **interaction-scoped** — will not be added. The control governs terminal
  interaction or presentation and has no meaning in a structured
  request/response protocol.
- **not-yet-demanded** — may be added later. Nothing about security or
  interaction blocks it; no MCP client has needed it yet.

Any CLI control not listed in these tables is omitted unintentionally rather than by design.

### Per-Tool CLI↔MCP Mapping Ledger

The first table maps each of the 12 local MCP tools to its CLI counterparts, or
`none`. The second lists the CLI command families that have no MCP tool, with
the reason.

**MCP tool → CLI mapping (12 local tools)**

| MCP Tool | Category | CLI counterpart(s) | Notes |
|---|---|---|---|
| `list_available` | discovery | `benchbox platforms list`, `benchbox benchmarks list` | Discovery inventory. CLI lists are the authoritative registry read path; MCP exposes the same metadata via registry. |
| `get_benchmark_info` | discovery | `benchbox benchmarks list` | Single-benchmark metadata, query counts, and scale constraints. CLI `list` is the registry read path; MCP returns enriched per-ID detail via `get_benchmark_info`. |
| `system_profile` | discovery | `benchbox profile` | Host, CPU, memory, and package facts. |
| `check_dependencies` | discovery | `benchbox check-deps [platform]` | Dependency availability and install guidance. |
| `run_benchmark` | execution | `benchbox run` | Scoped subset of `benchbox run`; omitted flags are listed in the `benchbox run` flag table below. |
| `get_query_details` | execution aid | `none` | MCP-only convenience: CLI users read query SQL from the benchmark source tree; MCP returns it structured per platform/mode. |
| `get_results` | results | `benchbox results`, `benchbox export` | Lists, reads, and exports result bundles; MCP inline-reads while CLI renders to stdout/files and supports cloud export. |
| `analyze_results` | analytics | `benchbox compare`, `benchbox report`, `benchbox aggregate` | Comparison, regression, trend, and aggregation over result bundles. |
| `get_query_plan` | analytics | `benchbox show-plan`, `benchbox compare --include-plans` | Reads captured plans from a result bundle; CLI also renders live plans. |
| `validate_results` | analytics | `validate_results.py` (source-checkout script) | Result JSON integrity and believability checks (`benchbox validate` checks config YAML, not result bundles). |
| `suggest_charts` | visualization | `benchbox visualize` | Suggests semantic chart types for result files. |
| `generate_chart` | visualization | `benchbox visualize` | Generates ASCII charts; MCP is inline-only by contract, CLI may write files. |

**CLI command families with no MCP tool**

| CLI command family | Tier | Reason |
|---|---|---|
| `benchbox auth` | security-scoped | Hosted credential provisioning and token lifecycle; MCP carries no credential-issuance surface. |
| `benchbox publish` | security-scoped | Publication writes to an external destination and assigns trust labels; MCP requests do not carry publish authority. |
| `benchbox submit` | security-scoped | Posts a result bundle to the hosted results platform; remote tenants must not submit on behalf of the server identity. |
| `benchbox setup` | security-scoped | Interactive credential and connection bootstrap that writes local config and touches filesystem/cloud state. |
| `benchbox shell` | interaction-scoped | Interactive SQL REPL; interaction has no meaning in a request/response protocol. |
| `benchbox datagen` | not-yet-demanded | Standalone data generation without a power run; MCP expresses this as `run_benchmark` with `mode=data_only`, so a separate datagen tool is not yet demanded. |
| `benchbox convert` | not-yet-demanded | Table-format conversion (e.g. parquet → delta); bounded enum, no MCP client has demanded it. |
| `benchbox tuning` | not-yet-demanded | Tuning template discovery and validation; promotion would be the enum subset (`tuned`/`notuning`/`auto`), not YAML paths. |
| `benchbox plan-history` | not-yet-demanded | Plan evolution history over multiple runs; bounded read, no client demand yet. |
| `benchbox download-answers` | security-scoped | Fetches external TPC answer keys from a remote source; network fetch with no tenant budget. |
| `benchbox metrics` | not-yet-demanded | Power@Size and Throughput@Size calculation from result files; bounded, no MCP client has demanded it. |
| `benchbox config` / `benchbox validate` (config file) | not-yet-demanded | Config-file syntax and completeness check; file-path input outside the MCP result-registry surface. |

### Scoped-Surface Omission Ledger — `benchbox run` Flags

These `benchbox run` flags are not `run_benchmark` parameters. The reasons use
the same three categories as above.

| CLI surface | MCP status | Tier | Reason |
|---|---|---|---|
| `--output` | Omitted | security-scoped | Result roots are server configuration (`--results-dir`, env vars). A request must not name a local or cloud write destination. |
| `--platform-option` | Narrow MCP subset | security-scoped | MCP accepts only its typed, non-secret allow-list; the full CLI key/value surface can carry credentials, DSNs, hosts, and paths. |
| `--benchmark-option` | Omitted | security-scoped | Unbounded key/value plumbing into benchmark internals has no typed, fail-closed admission model. |
| `--force` | Omitted | security-scoped | Forced regeneration and upload overwrite server-owned data outside the requesting tenant's lifecycle. |
| `--global-cache` | Omitted | security-scoped | Redirects writes to a shared `~/.benchbox/datagen/` root outside the server's configured result tree. |
| `--publish` | Omitted | security-scoped | Publication writes to an external destination; MCP requests do not carry publish authority. |
| `--publish-target` | Omitted | security-scoped | Names an external local or cloud destination (`s3://`, `gs://`, `abfss://`). |
| `--publish-label` | Omitted | security-scoped | Trust labelling is a maintainer attestation, not a request-supplied field. |
| `--non-interactive` | Omitted | interaction-scoped | MCP requests are non-interactive by protocol; interactive prompts are never issued, so the flag has no effect to expose. |
| `--no-progress` | Omitted | interaction-scoped | Progress bars are terminal presentation; MCP returns structured JSON. |
| `--quiet` | Omitted | interaction-scoped | Console verbosity control; MCP already suppresses console output. |
| `--verbose` | Omitted | interaction-scoped | Console verbosity control; MCP response detail is governed by tool schemas. |
| `--iterations` | Omitted | not-yet-demanded | Repeated power-test measurement is expressible over MCP; no client has demanded it. |
| `--seed` | Omitted | not-yet-demanded | RNG seed for query parameter generation; bounded integer, no client demand yet. |
| `--official` | Omitted | not-yet-demanded | TPC-compliant mode is a bounded boolean; it additionally requires `--seed`, so both promote together. |
| `--compression` | Omitted | not-yet-demanded | Bounded codec enum; no client demand yet. |
| `--table-format` | Omitted | not-yet-demanded | Bounded table-format spec; no client demand yet. |
| `--presort` | Omitted | not-yet-demanded | Bounded pre-sort enum; no client demand yet. |
| `--tuning` | Omitted | not-yet-demanded | Promotion must be enum-only (`tuned`, `notuning`, `auto`); the CLI's YAML-path spelling is security-scoped and stays CLI-only. |
| `--table-mode` | Omitted | not-yet-demanded | Bounded `native`/`external` enum; no client demand yet. |
| `--sorted-ingestion-*` | Omitted | not-yet-demanded | Bounded ingestion-ordering controls; no client demand yet. |
| `--validation` | Omitted | not-yet-demanded | MCP exposes only the `validate_only` boolean; the validation-strictness enum is promotable. |
| Velox `deployment` (`remote`/`docker`) | Omitted | security-scoped | Remote Velox would require an operator-approved endpoint and runtime controls; only local Velox is exposed over MCP to avoid caller-controlled destination selection. |
| `--plan-config` | Omitted | not-yet-demanded | MCP exposes only the `capture_plans` boolean; per-query plan-capture selection is promotable. |
| `--no-monitoring` | Omitted | not-yet-demanded | Metrics-collection toggle; bounded boolean, no client demand yet. |
| `--show-plans` | Omitted | interaction-scoped | Live plan display is terminal presentation; MCP returns structured results and captured plans through result tools. |
| `--normalize-plan-literals` | Omitted | not-yet-demanded | Plan normalization toggle is a bounded control with no client demand yet. |
| `--stats-per-table-timing` | Omitted | not-yet-demanded | Per-table timing detail is a bounded reporting control with no client demand yet. |
| `--strict-translation` | Omitted | not-yet-demanded | Strict SQL-translation behavior is a bounded execution control with no client demand yet. |
| `--analyze-plans` | Omitted | not-yet-demanded | Plan-capture detail toggle is a bounded control with no client demand yet; MCP already exposes only `capture_plans`. |
| `--stats-reset` | Omitted | not-yet-demanded | Statistics rebuild/reset is a bounded measurement control with no client demand yet. |
| `--concurrency` | Omitted | security-scoped | Concurrent streams are a resource-budget control; MCP must not admit caller-chosen unbounded fan-out. |
| `--streams` | Omitted | security-scoped | Canonical alias of `--concurrency` on `benchbox run`; same resource-budget control, same omission. |
| `--ignore-memory-warnings` | Omitted | security-scoped | Skips memory-admission checks. Resource-budget bypasses stay permanently omitted from MCP. |
| `--funding` | Omitted | not-yet-demanded | Funding metadata is a bounded provenance field with no client demand yet. |
| `--result-source` | Omitted | not-yet-demanded | Result-source selection is a bounded provenance control with no client demand yet. |
| `--client-region` | Omitted | not-yet-demanded | Attested client-region metadata is a bounded provenance field with no client demand yet. |
| `--client-cloud` | Omitted | not-yet-demanded | Attested client-cloud metadata is a bounded provenance field with no client demand yet. |
| `--polars-streaming` | Omitted | not-yet-demanded | Deprecated CLI alias for `--execution-engine streaming`; MCP exposes the canonical `execution_engine` parameter. |
| Compute `compute_resource` / `compute_size` (`--platform-option`) | Omitted | security-scoped | Names or sizes a billable vendor compute object (warehouse, engine, workgroup, cluster, pool, application, service). Server operators select compute through local configuration; MCP requests must not name cloud resources or caller-chosen resource budgets. |
| `--gateway` | Omitted | security-scoped | Gateway routing names an intermediary endpoint or custom proxy host. Server operators configure gateway connectivity locally; MCP requests must not redirect query endpoints. |
| Gateway connection options (`--platform-option`) | Omitted | security-scoped | Gateway connection parameters (`gateway_host`, `gateway_port`, `gateway_protocol`, `gateway_ca_bundle`, `gateway_ocsp_fail_open`, `allow_insecure_gateway`) configure network endpoints and TLS trust. Server operators configure endpoints locally; MCP requests must not name arbitrary network hosts or bypass TLS verification. |

The textcharts MCP server remains a separate-client integration, not a bundled or proxied part of `benchbox-mcp`. See `docs/design/textcharts-mcp-boundary.md` for the accepted separate textcharts configuration and the rejected bundle/proxy alternatives.

### Discovery Tools

#### `list_available`

List platforms, benchmarks, chart templates, or all discovery data.
Benchmark rows include `support_status` from
`benchbox.core.benchmark_registry`; MCP does not maintain a separate support
classification.

| Name | Type | Required | Default | Description |
|---|---|---:|---|---|
| `category` | string | No | `all` | `platforms`, `benchmarks`, `charts`, or `all`. |

#### `get_benchmark_info`

| Name | Type | Required | Default | Description |
|---|---|---:|---|---|
| `benchmark` | string | Yes | - | Benchmark identifier. |

Returns benchmark metadata including `support_status`, category, query/schema
information, scale-factor constraints, and DataFrame capability.

#### `system_profile`

No parameters. Returns host and package information useful for capacity
planning.

#### `check_dependencies`

| Name | Type | Required | Default | Description |
|---|---|---:|---|---|
| `platform` | string or null | No | `null` | Specific platform to check; omitted checks all platforms. |
| `verbose` | boolean | No | `false` | Include detailed package information. |

### Benchmark Query Tools

#### `get_query_details`

| Name | Type | Required | Default | Description |
|---|---|---:|---|---|
| `benchmark` | string | Yes | - | Benchmark identifier. |
| `query_id` | string | Yes | - | Query identifier, for example `1`, `Q1`, or `17`. |
| `platform` | string or null | No | `null` | Optional platform for dialect-specific query lookup. |
| `mode` | string or null | No | `null` | `sql` or `dataframe`; inferred from platform when omitted. |

Public benchmark query details include registry `support_status` in
`benchmark_info`. Internal/repo-only benchmark details remain addressable by
explicit ID, but do not expose support-status claims.
See the [Benchmark Visibility Policy](public-contracts.md#benchmark-visibility-policy)
for the full surface-by-surface matrix.

### Results Tools

#### `get_results`

`get_results` combines recent-run listing, result reading, and result export.

| Name | Type | Required | Default | Description |
|---|---|---:|---|---|
| `result_file` | string or null | No | `null` | Result filename; omitted lists recent runs. |
| `format` | string | No | `details` | `list`, `details`, `json`, `csv`, `html`, `text`, or `markdown`. |
| `output_path` | string or null | No | `null` | Export path relative to the configured results dir. |
| `limit` | integer | No | `10` | Max recent runs when listing. |
| `platform` | string or null | No | `null` | Platform filter when listing. |
| `benchmark` | string or null | No | `null` | Benchmark filter when listing. |
| `include_queries` | boolean | No | `true` | Include query details when reading one result. |

### Analytics Tools

#### `analyze_results`

| Name | Type | Required | Default | Description |
|---|---|---:|---|---|
| `analysis` | string | No | `compare` | `compare`, `regressions`, `trends`, or `aggregate`. |
| `file1` | string or null | No | `null` | Baseline result file for `compare`. |
| `file2` | string or null | No | `null` | Comparison result file for `compare`. |
| `platform` | string or null | No | `null` | Platform filter for non-compare analyses. |
| `benchmark` | string or null | No | `null` | Benchmark filter for non-compare analyses. |
| `threshold_percent` | number | No | `10.0` | Regression/change threshold. |
| `metric` | string | No | `geometric_mean` | Trend metric: `geometric_mean`, `p50`, `p95`, `p99`, or `total_time`. |
| `group_by` | string | No | `platform` | Aggregate grouping: `platform`, `benchmark`, or `date`. |
| `limit` | integer | No | `10` | Max runs to analyze where applicable. |

#### `get_query_plan`

| Name | Type | Required | Default | Description |
|---|---|---:|---|---|
| `result_file` | string | Yes | - | Result filename containing captured query plans. |
| `query_id` | string | Yes | - | Query identifier. |
| `format` | string | No | `tree` | `tree`, `json`, or `summary`. |

#### `validate_results`

| Name | Type | Required | Default | Description |
|---|---|---:|---|---|
| `result_file` | string | No* | `""` | Path to one result JSON file. |
| `directory` | string | No* | `""` | Directory of result JSON files for batch validation. |
| `verbose` | boolean | No | `false` | Include PASS checks in output. |

*Provide either `result_file` or `directory`.

### Visualization Tools

BenchBox MCP visualization is result-aware semantic charting. `suggest_charts`
and `generate_chart` read BenchBox result files and accept semantic chart IDs
from `benchbox.core.visualization.chart_types`, such as `performance_bar`,
`power_bar`, and `query_heatmap`. These IDs are distinct from raw
`textcharts_*` primitive MCP tools. BenchBox does not register or proxy the
external `textcharts-mcp` server; if a client configures that server separately,
its tools remain a separate raw rendering namespace.
The `textcharts` Python package is an implementation dependency of BenchBox's
ASCII compatibility layer, not an MCP server registration. Running only
`benchbox-mcp` therefore publishes the result-aware tools listed below; a
client that intentionally configures a separate `textcharts-mcp` process sees
that server under its own namespace and must apply its own support and security
review.

#### `suggest_charts`

| Name | Type | Required | Default | Description |
|---|---|---:|---|---|
| `result_files` | string | Yes | - | Comma-separated result filenames. |

#### `generate_chart`

| Name | Type | Required | Default | Description |
|---|---|---:|---|---|
| `result_files` | string | Yes | - | Comma-separated result filenames. |
| `chart_type` | string | No | `performance_bar` | Chart type for single-chart output. |
| `template` | string or null | No | `null` | Template name for multi-chart output. |
| `output_dir` | string or null | No | `null` | Must remain `null`; MCP chart output is intentionally inline-only and caller-selected file paths are rejected. |
| `format` | string | No | `ascii` | Must be `ascii`; other formats are rejected until a tenant-scoped artifact contract is approved. |

Chart generation is intentionally inline-only. `generate_chart` returns the
ASCII content in the MCP response and does not create a caller-selected file.
This keeps chart output inside the response boundary while a future artifact
contract is designed for tenant ownership, path containment, overwrite and
retention semantics. Requests that set `output_dir` or choose another format
fail closed with a structured validation error.

Available `chart_type` values and template names are derived from the
visualization registries and are discoverable with `list_available(category="charts")`.

---

## Prompts

Prompts are reusable templates for AI analysis. Invoke via slash commands in Claude Code.
The same prompt catalog is available through both supported transports: use
`prompts/list` to discover the seven names and argument schemas, then
`prompts/get` with a prompt name and string-valued arguments to render one
prompt. Stdio and sessionless Streamable HTTP return the same prompt metadata
and rendered text; HTTP requests do not require or receive an `Mcp-Session-Id`.
The landing quickstart catalog references three of these prompts for guided
benchmark flows; the four remaining prompts are still first-class MCP prompts
and are discoverable at runtime.

### `analyze_results`

Analyze benchmark results and identify performance patterns.

**Arguments (positional):**
1. `benchmark` (default: "tpch")
2. `platform` (default: "duckdb")
3. `focus` (optional): Focus area like 'slowest_queries', 'memory', 'io'

**Usage:**
```
/mcp__benchbox__analyze_results tpch duckdb slowest_queries
```

---

### `compare_platforms`

Compare benchmark performance across multiple platforms.

**Arguments (positional):**
1. `benchmark` (default: "tpch")
2. `platforms` (default: "duckdb,polars-df"): Comma-separated platform names
3. `scale_factor` (default: 0.01)

**Usage:**
```
/mcp__benchbox__compare_platforms tpch "duckdb,polars-df,sqlite" 0.1
```

---

### `identify_regressions`

Identify performance regressions between benchmark runs.

**Arguments (positional):**
1. `baseline_run` (optional): Baseline result file
2. `comparison_run` (optional): Comparison result file
3. `threshold_percent` (default: 10.0)

**Usage:**
```
/mcp__benchbox__identify_regressions run1.json run2.json 5
```

---

### `benchmark_planning`

Help plan a benchmark strategy for a specific use case.

**Arguments (positional):**
1. `use_case` (default: "testing"): One of 'testing', 'production', 'comparison', 'regression'
2. `platforms` (optional): Comma-separated platform list
3. `time_budget_minutes` (default: 30)

**Usage:**
```
/mcp__benchbox__benchmark_planning comparison "duckdb,snowflake" 60
```

---

### `troubleshoot_failure`

Diagnose and resolve benchmark failures.

**Arguments (positional):**
1. `error_message` (optional): Error message from failed run
2. `platform` (optional): Platform where failure occurred
3. `benchmark` (optional): Benchmark that failed

**Usage:**
```
/mcp__benchbox__troubleshoot_failure "Connection refused" snowflake tpch
```

---

### `benchmark_run`

Execute a planned benchmark with validation and dependency checks.

**Arguments (positional):**
1. `platform` (default: "duckdb"): Target platform
2. `benchmark` (default: "tpch"): Benchmark to run
3. `scale_factor` (default: 0.01): Data scale factor
4. `queries` (optional): Query subset (e.g., "1,5,10")

**Usage:**
```
/mcp__benchbox__benchmark_run duckdb tpch 0.1
/mcp__benchbox__benchmark_run snowflake tpcds 1 "1,5,10"
```

This prompt:
1. Validates the configuration
2. Checks dependencies
3. Runs the benchmark
4. Provides execution summary and recommendations

---

### `platform_tuning`

Get tuning recommendations for a specific platform.

**Arguments (positional):**
1. `platform` (default: "duckdb"): Platform to tune
2. `workload` (optional): Workload characteristics description

**Usage:**
```
/mcp__benchbox__platform_tuning duckdb
/mcp__benchbox__platform_tuning snowflake "heavy aggregation workload"
```

This prompt provides:
1. Memory configuration recommendations
2. Parallelism settings
3. I/O optimization
4. Platform-specific tuning parameters

---

## Resources

Resources provide read-only access to BenchBox data. Currently, resources are accessed indirectly through tools.

## Error Handling

All tools return structured error information:

```json
{
  "error": "Description of the error",
  "error_type": "ExceptionClassName",
  "suggestion": "How to resolve the issue"
}
```

Common error types:
- **ConfigurationError**: Invalid platform or benchmark configuration
- **DependencyError**: Missing required dependencies
- **FileNotFoundError**: Result file not found

## Related Documentation

- [MCP Integration Guide](../guides/mcp-integration.md) - Setup and usage guide
- [CLI Reference](cli/index.md) - Command-line interface
- [API Reference](api-reference.md) - Python API
