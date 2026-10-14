# Remote MCP security and tenancy

BenchBox Streamable HTTP is local-only unless an operator supplies a complete
security policy. Stateless transport removes sticky protocol sessions; it does
not authenticate callers, authorize tools, isolate files, or coordinate load.

## Threat model

| Asset | Trust boundary | Primary abuse cases | Control |
|---|---|---|---|
| Database credentials and bearer tokens | Client, OAuth issuer, reverse proxy, BenchBox | disclosure through errors, logs, traces, or audit | bearer values exist only during SDK verification; config stores SHA-256 digests; MCP logs and audit omit raw arguments and exception text |
| Benchmark execution capacity | Untrusted authenticated caller to platform adapters | expensive platforms/scales, request floods, worker hopping | per-tool scopes and platform/benchmark/scale policy; transactional shared rate, queue, and concurrency limits |
| Results, charts, exports, and jobs | One authenticated principal to another | guessed names, traversal, caller-selected workspace, cross-worker identity confusion | stable principal derived from SDK client ID, issuer, and subject; server-owned workspace root; existing path-containment checks run inside that tenant root |
| HTTP endpoint | Browser/network to MCP process | public accidental bind, DNS rebinding, hostile Host/Origin, missing auth | loopback default; non-loopback startup requires the full policy and HTTPS public URLs; SDK bearer, Host, Origin, and content-type middleware validate every request |
| Audit and coordination database | All MCP workers to durable storage | process-local quota bypass, restart reset, secret-rich audit | transactional shared SQLite store; bounded audit schema contains only opaque principal, tool, decision, execution ID, and artifact basename |

The pre-provisioned token verifier is a resource-server integration seam, not
an authorization server. Tokens must be issued out of band. For horizontally
scaled deployments, every worker must mount the configured state database and
workspace root from storage with shared locking and durability semantics. Do
not use node-local files on multiple hosts. A shared endpoint also needs
passing readiness evidence for the exact deployed revision (see "Shared
endpoint deployment" below). Local stdio and loopback Streamable HTTP need
none of this.

## Configuration

The policy file is JSON. It must contain no raw token; provision each token as
the lowercase SHA-256 digest of its exact bearer value.

```json
{
  "issuer_url": "https://login.example.com",
  "resource_server_url": "https://benchbox.example.com/mcp",
  "allowed_hosts": ["benchbox.example.com"],
  "allowed_origins": ["https://console.example.com"],
  "workspace_root": "/srv/benchbox/mcp/workspaces",
  "state_db": "/srv/benchbox/mcp/state/security.sqlite3",
  "tokens": [
    {
      "token_sha256": "<64 lowercase hex characters>",
      "client_id": "automation-client",
      "subject": "team-a",
      "scopes": ["benchbox:read", "benchbox:write", "benchbox:execute"]
    }
  ],
  "allowed_platforms": ["duckdb"],
  "allowed_benchmarks": ["tpch"],
  "max_scale_factor": 1.0,
  "admission": {
    "requests_per_minute": 120,
    "global_concurrency": 8,
    "principal_concurrency": 2,
    "queue_limit": 32,
    "queue_timeout_seconds": 5,
    "lease_seconds": 3600
  },
  "jobs": {
    "queue_limit": 32,
    "max_queued_per_principal": 8,
    "max_running": 8,
    "max_running_per_principal": 2,
    "lease_seconds": 60,
    "poll_seconds": 0.25,
    "max_attempts": 2,
    "retention_seconds": 604800
  }
}
```

The scope contract is:

- `benchbox:read`: discovery, system profile, result reads, analytics, chart
  suggestions, and durable job status/result reads;
- `benchbox:write`: chart generation and other non-benchmark artifact writes;
- `benchbox:execute`: `run_benchmark`, `start_benchmark`, and
  `cancel_benchmark`, still constrained by platform, benchmark, and
  maximum-scale policy.

## Server-owned ClickHouse connection profiles

A request can never set a ClickHouse `port` or `secure` value: a port is part of
a destination and `secure` is transport policy, so accepting either would let an
authenticated caller reach another listener on the host or downgrade
server-owned TLS. Both are rejected for every ClickHouse platform spelling.

To reach a non-default port or TLS setting, define named profiles in the server
process environment. Only the operator sets this; callers may name a profile but
never describe one.

```bash
export BENCHBOX_MCP_CLICKHOUSE_PROFILES='{"analytics": {"port": 9440, "secure": true}}'
```

Profile names are lowercase snake_case. Each entry accepts only `port`
(1-65535) and `secure` (boolean, default `false`); a malformed entry, or one
carrying hosts, credentials, or paths, is discarded and its profile becomes
unavailable rather than partially trusted.

Callers select a profile with `platform_options`:

```json
{"platform": "clickhouse-server", "platform_options": {"connection_profile": "analytics"}}
```

### Velox remote deployment

Velox `deployment` is not part of the MCP allow-list. The only deployment MCP
can fully describe is `local`; `remote` would require a caller-supplied
`sc://` endpoint and additional runtime configuration that would let an
authenticated caller steer the server at a listener the operator never approved
(the default `sc://localhost:50051` is host-local). Until a server-owned
Velox endpoint registry with execution-time resolution exists, both `remote`
and `docker` are rejected at admission (see `docs/reference/mcp.md` omission
ledger, security-scoped). Direct `VeloxAdapter(deployment_mode='remote',
endpoint=...)` construction outside MCP keeps working for CLI and Python-API
callers.

On the `clickhouse` platform a profile additionally requires
`deployment_mode: "server"` — local mode runs chDB in-process and must not gain
a network path. Requests, including persisted durable jobs, carry only the
profile name; the port and TLS policy are resolved from this environment at
execution time, so withdrawing a profile immediately fails closed for queued
retries. Rejections never echo the requested profile name, so a caller cannot
use validation errors to enumerate configured profiles.

## Dask aggregate resource envelope

Dask's per-field bounds constrain each knob in isolation, but the pressure a
request puts on the host is their product. Without an aggregate ceiling,
`n_workers: 256` combined with `threads_per_worker: 256` describes a
65,536-thread `LocalCluster` advertising 256 TB of memory from a single request.

Every MCP-requested Dask cluster must therefore fit inside a server-owned
aggregate budget, checked before the adapter is constructed — the adapter builds
its `LocalCluster` in `__init__`, so a later guard would have to start the
oversized cluster to discover it was oversized.

| Budget | Env var | Default |
|---|---|---|
| Worker count | `BENCHBOX_MCP_DASK_MAX_WORKERS` | `16` |
| `n_workers` x `threads_per_worker` | `BENCHBOX_MCP_DASK_MAX_TOTAL_THREADS` | `64` |
| `n_workers` x `memory_limit` | `BENCHBOX_MCP_DASK_MAX_TOTAL_MEMORY` | `64GB` |

`memory_limit` is per worker, so the advertised total scales with `n_workers`.
Fields the request omits are scored using the adapter's own conservative local
caps (2 workers, 2 threads per worker, 2 GB per worker), so an omitted field
never contributes more than the adapter would actually apply. A request that
omits `platform_options` entirely is held to the same envelope as an empty one,
so an optionless run cannot escape a budget tighter than those defaults.

A malformed or out-of-range override is ignored in favour of the reviewed
default rather than being partially trusted. Every override is bounded:
`..._MAX_WORKERS` at 256, `..._MAX_TOTAL_THREADS` at 65536, and
`..._MAX_TOTAL_MEMORY` at 16 TB — so a units slip such as `999999TB` falls back
to the default instead of silently disabling the memory ceiling.

This envelope is an additional per-run guard. The server-wide and per-principal
concurrency limits in `admission` remain enforced independently.

### Envelope tradeoffs

Two tradeoffs were accepted when the envelope landed:

1. **Host-derived budget.** The defaults (`16` workers, `64` total threads,
   `64GB` total) are static constants chosen for determinism and testability,
   not derived from `psutil` host capacity. On a 4-core runner the default
   still admits a 64-thread request; on a 64-core host it under-uses the
   machine. Operators can retune via `BENCHBOX_MCP_DASK_MAX_WORKERS` /
   `_MAX_TOTAL_THREADS` / `_MAX_TOTAL_MEMORY`, which are the explicit operator
   ceiling. The effective budget is therefore `min(configured, operator ceiling)`;
   host capacity is not an implicit additional ceiling, keeping the envelope
   deterministic under test (host capacity is injected in tests rather than read
   inside the validator).

2. **Inert-field handling.** The envelope is applied even when
   `use_distributed` is `false`, where `DaskDataFrameAdapter` creates no
   `LocalCluster`. A request that would consume nothing can still be rejected
   for its worker count. Fail-closed was the deliberate choice: `use_distributed=false`
   combined with a large `n_workers` is a contradictory request, and making the
   guard conditional would add a bypass shape. If relaxed in the future, the
   relaxation should reject the contradiction explicitly (an explicit
   `n_workers`/`threads_per_worker` with `use_distributed=false`) rather than
   silently skipping the envelope, so no bypass is introduced.


## Durable remote benchmark jobs

Remote clients should use `start_benchmark` instead of holding a
`run_benchmark` request open. It returns an opaque `execution_id`; use
`get_benchmark_status`, `get_benchmark_result`, and `cancel_benchmark` for the
rest of the lifecycle. These four tools are registered only when remote
security is configured. Remote `run_benchmark` retains its immediate
`dry_run` and `validate_only` modes but rejects normal execution with a pointer
to `start_benchmark`. Local stdio clients retain the complete synchronous
`run_benchmark` contract unchanged.

Jobs are persisted in `state_db` and owned by the stable authenticated
principal, never by an MCP session. Every worker uses transactional claims and
renewable leases. A lease is retried only when the old attempt is proven
quiescent: the owner reported its own failure after that attempt finished,
the publication commit point already holds the final response artifact (an
expired publishing lease then completes against that artifact). Cancellation
and an exhausted attempt budget prove nothing about termination. Otherwise
the old attempt may still be executing database work, so recovery records the
terminal `unknown` outcome instead of requeueing: the job is never claimed
again, and the operator must inspect the target before resubmitting with a new
idempotency key. A repeated `idempotency_key` returns the original job only
when its request is identical.

Cancellation reports `accepted` while queued, `requested` while running (the
attempt stops at the next safe boundary), and `too_late` once publication has
committed or the job is otherwise terminal. Once a worker enters the
publishing transition, publication is the commit point and cancellation is too
late.

Durable admission bounds queued depth and running capacity separately, across
every worker process and restart. Submission is refused past `queue_limit`
queued jobs globally or `max_queued_per_principal` for the caller, so one
tenant cannot fill the queue. An owned attempt that requests a retry must pass
the same queued limits transactionally; otherwise it terminates with
`retry_queue_full`. Claiming is refused past `max_running`
outstanding attempts globally or `max_running_per_principal` for the job's
owner, where outstanding means leased attempts plus `unknown` work whose
lease was lost without proof of termination. A lost lease therefore keeps
holding database capacity: no replacement attempt is admitted until a fenced
transition proves quiescence. When the displaced executor returns, its worker
records a separate durable attestation; only then does the unknown job release
capacity. Retention never deletes an unquiesced row, even after its result
artifacts expire. A serialized execution result that reports outstanding
streams is quarantined as `unknown` with `outstanding_work`; executor return is
not treated as quiescence for that job. Claims use database-owned monotonic
sequences to serve the least-recently-served principal with queued work first
and the oldest queued job within each principal. Host clock rollback cannot
change fairness or FIFO order. `get_benchmark_capacity` reports global row counts,
the caller's own usage, and the caller's quarantined jobs with the reason each
still holds capacity; it never exposes another tenant's jobs.

The worker writes the response and result bundle to a
tenant-owned staging directory, flushes it, atomically renames it to the final
job directory, and only then records `completed`. This prevents a completed
status from preceding its durable artifact. Terminal metadata and its owned
artifact are removed after `retention_seconds`, except that an unquiesced
`unknown` row is retained as a capacity fence.

## Shared endpoint deployment

This section and the storage, recovery, and incident sections below apply only
to a shared non-loopback endpoint. They are its fail-closed contract; local
stdio and loopback Streamable HTTP do not need them.

Run the process behind a TLS-terminating reverse proxy. The externally visible
issuer and resource-server URLs must use HTTPS. The proxy must preserve the
validated `Host`, `Origin`, `Content-Type`, and `Authorization` headers and must
not log bearer values. Configure request-body and header-size limits at the
proxy, disable response caching, and set an upstream timeout long enough for
normal MCP calls; benchmark execution itself uses durable job handles.

Run the following command only after the production readiness acceptance
matrix passes for the revision you deploy. The evidence JSON must name the deployed `BENCHBOX_BUILD_SHA`, be no more than seven
days old, contain passing automated and external gates, and match the digest
provisioned independently as `BENCHBOX_MCP_READINESS_SHA256`. OTLP credentials
belong in the standard OpenTelemetry environment, never in the evidence file.

```bash
export BENCHBOX_BUILD_SHA=<deployed-commit-sha>
export BENCHBOX_MCP_READINESS_SHA256=<sha256-of-readiness-json>
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://otel.example.com/v1/traces

benchbox-mcp \
  --transport streamable-http \
  --host 10.0.0.12 \
  --port 8000 \
  --security-config /etc/benchbox/mcp-security.json \
  --readiness-evidence /etc/benchbox/mcp-readiness.json
```

Startup is rejected if a non-loopback host has no policy, if its public URLs
are not HTTPS, if Host/Origin/token allowlists are empty, if OTLP is absent, or
if evidence is missing, stale, failed, digest-mismatched, pin-mismatched, or for
another source revision. Stdio keeps its existing local behavior and rejects
the remote-only policy and evidence options.

The SQLite file and workspace root are durable shared backends only for workers on storage that
provides correct cross-process locking. Before multi-host use, replace or mount
it on an explicitly supported shared storage service and pass the production
readiness acceptance matrix. Statelessness must never substitute for this
coordination.

## Storage, scaling, and recovery

Treat `state_db` and `workspace_root` as one recovery unit. The state database
contains authentication coordination, shared admission tickets, job leases,
and job metadata; the workspace contains the corresponding tenant artifacts.
Snapshots must preserve SQLite WAL consistency and artifact ordering. Test a
restore into an isolated environment and prove that completed jobs have their
published marker and response bundle, queued jobs remain claimable, expired
running jobs are recovered, and no artifact crosses tenant roots.

Scale workers only after the alternating-worker acceptance test passes against
the same storage class used in production. Watch queue depth, admission wait,
lease loss, retry count, failure rate, and artifact publication latency in
shared telemetry. Any process-local counter is diagnostics only; it is not
cross-worker truth and resets on restart.

Rollback uses the previous application revision with a readiness document
generated for that exact revision. Before rollback, stop new publication at
the proxy and let publishing jobs reach their commit point. Do not roll back
across an incompatible state schema. Resume traffic only after protocol,
storage, and smoke checks pass on the rollback pool.

## Incident diagnostics

1. Remove the endpoint from service discovery or deny traffic at the proxy;
   do not delete state or artifacts.
2. Preserve proxy request IDs, bounded OTLP trace IDs, redacted audit rows,
   application revision, evidence digest, state/WAL snapshots, and storage
   health. Never collect bearer tokens, raw database URLs, arguments, or result
   payloads into telemetry.
3. Determine whether the fault is protocol, identity/policy, admission,
   worker lease, queue, publication, or storage. Compare multiple workers; do
   not infer fleet health from one process-local metrics collector.
4. Rotate credentials if exposure is suspected, restore the state/workspace
   pair if durability is affected, and rerun the complete gate before
   republishing.

## Audit contract

Each protected tool decision records an opaque principal ID, tool name,
decision, and optional bounded execution/artifact identifiers. Audit records do
not contain token digests, bearer values, OAuth claims, tool arguments,
database URLs, result payloads, or exception messages. Operators should apply
normal access control, retention, backup, and integrity monitoring to the state
database.
