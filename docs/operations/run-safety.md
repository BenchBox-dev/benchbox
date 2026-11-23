<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Run safety: billable platforms, driver installs and --force

## Billable-platform acknowledgement

`benchbox run`, `benchbox run-official` and `benchbox compare` refuse a non-interactive run of a billable platform
(registry category `cloud`, or a managed deployment) with exit 6 unless `--acknowledge-billable-platform` or
`BENCHBOX_ACKNOWLEDGE_BILLABLE_PLATFORM=1` is set. The refusal happens before any adapter, credential read or
connection. A terminal session is not prompted. The acknowledgement and its source are recorded in the result bundle.
The flag is a statement of intent, not an access control.

Paths outside the guard, by decision:

| Path | Why it is not guarded |
|---|---|
| `run --dry-run` | It previews configuration. It reads `~/.benchbox/credentials.yaml`, installs no package and makes no Databricks network call. |
| `setup --validate-only`, `setup --diagnose` | Connecting is their purpose. A Snowflake check can resume a suspended warehouse. |
| Remote `start_benchmark` | Gated only by the operator allow-list; the acknowledgement is not plumbed through it. |
| `shell` with an `md:` MotherDuck path | The user asked for that connection. |

Local MCP `run_benchmark` is guarded.

## Driver auto-install

`--platform-option driver_version=X` selects an exact driver version. The value must be a valid package version
(for example `1.2.3`); anything else is refused before it reaches an installer.

When the installed driver differs from the requested version and no isolated runtime matches, BenchBox can install
the requested version with `uv pip install` or `pip install`. This happens only when `driver_auto_install=true` is
passed as a platform option or `BENCHBOX_DRIVER_AUTO_INSTALL=1` is set. BenchBox prints the exact
`package==version` and the target interpreter to stderr before it installs.

In a non-interactive session (stdin or stdout is not a terminal, or `BENCHBOX_NON_INTERACTIVE` is set) the platform
option alone is refused with exit 6; set `BENCHBOX_DRIVER_AUTO_INSTALL=1` to allow the install. A dry run never
installs a package: a missing or mismatched driver is reported and the preview continues without it.

`benchbox platforms install` prints installation instructions only; it never installs.

## DuckDB version isolation

An isolated DuckDB runtime is a virtualenv under a directory listed in `BENCHBOX_DRIVER_RUNTIME_ROOTS` (default
`benchmark_runs/driver_envs`) whose installed `duckdb` matches `driver_version`. DuckDB is a native module that
cannot be reloaded in a running process, so the requested version must be the first DuckDB the process loads.
Platform detection and the DuckDB adapter therefore avoid importing it until the adapter is created. If another
DuckDB was already loaded, the run fails with an error naming the loaded version and path instead of continuing on
the wrong one.

## What --force deletes

| Value | Local data | Target database |
|---|---|---|
| `datagen` | Regenerated, overwriting existing files | Deleted before the run (same as `--platform-option force_recreate=true`) |
| `upload` | Untouched | Untouched; data is re-uploaded to remote storage even if a valid copy exists |
| `all` or `datagen,upload` | As `datagen` | As `datagen`, plus the re-upload |

Deleting the target database means: a local database file or directory is deleted; on a remote platform the database
is dropped, except Snowflake (drops this benchmark's tables and tuning metadata, keeps the database) and Databricks
(truncates every table in the schema). Platforms that cannot drop a database refuse the run instead.

## Generation time estimate

Before TPC-H or TPC-DS data generation, and before TPC-H or TPC-DS query generation, BenchBox prints a rough estimate
read from `benchbox/core/generation_estimate.json`, which holds wall times measured with `benchbox run --phases
generate` (parquet), `qgen` and `dsqgen` on a darwin-arm64 Apple M4 (10 cores, 16 GiB) at commit 93f336032a, with the
host's one-minute load average below 3 at the start of every measurement. The TPC-H data generation figures at scale
0.01 and 0.1 were measured again the same way after BenchBox began starting at most four `dbgen` processes below scale
1; those two points carry their own `measured_at_commit`:

> Rough estimate: generation will take about {estimate} (measured on {host_class} at scale {scales}{extrapolated_note}).

Data generation was measured at scale 0.01, 0.1, 1 and 10; TPC-H query generation (all 22 queries) at scale 1, 10, 100
and 1000; TPC-DS query generation (all 99 queries) at scale 1, 100,
1000, 3000 and 5000. Between measured scales the estimate interpolates linearly. A scale above the largest measured one
scales the largest measurement linearly, and the line says so (`; extrapolated for scale 100, above the largest measured
scale 10`). The figures describe that host class; other hardware will differ. No estimate is printed for benchmarks
without measurements. The same line is shown by the dry run, the interactive configuration preview and the interactive
scale prompt, so every surface that talks about generation time reads this one measured source. The whole-run "Est. Time"
ranges of the prompt and the dry run are run time, not generation time, and are unchanged.

When a run limit is set and is shorter than the estimate, the run also prints:

> The run limit of {limit} seconds is shorter than the estimated generation time of about {estimate}.

Nothing is printed when no limit is set or the limit is larger. The limit is not changed.

## Run time limit and remote work

When a run time limit is set (`--timeout`, `execution.timeout_seconds`, `timeout_seconds=`), BenchBox warns once for
every platform in the registry category `cloud`. No warning is printed without a limit (unset or `0`), and
none for a local platform. Cleanup is implemented for Onehouse Quanton, Google BigQuery, Amazon EMR Serverless, AWS Glue
and Google Dataproc; those platforms print:

> A run time limit is set. If the run times out, BenchBox will try to cancel the work it started on {platform}. This cleanup has not been checked against the live service, so work may keep running and billing.

Every other cloud platform prints:

> A run time limit is set. If the run times out, work BenchBox started on {platform} may keep running and billing: BenchBox cannot cancel it on this platform.

Cleanup has not been checked against live services. It is verified only with stubbed clients: no cloud run was made
and no credentials were used. Work may keep running and billing after a timeout until a live check has been done.

The cleanup starts when the run times out: when the timeout is recorded, or when the limit passes, whichever comes
first. It calls one hook, `PlatformAdapter.cancel_remote_work`, on each adapter the run created. An adapter cancels
only what this run started and has not seen finish: Onehouse jobs and a cluster it provisioned itself, BigQuery query
jobs, EMR Serverless job runs and an application the run created, Glue job runs, and Dataproc jobs and an ephemeral
cluster the run created. It never stops a warehouse, application, cluster or session that existed before the run. The
run waits for the cleanup only until 5 seconds before the 30-second grace ends, so a run that stopped cleanly still
exports its full result. The hard stop waits only for what is left of that time. The 30 seconds are not an exit
bound: stopping starts at the limit plus 30 seconds. At that point BenchBox cancels remote work (only if the cleanup
had not already started at the limit), writes the partial result, ends the child processes it started and exits with
code 124. That takes a few seconds, within 5 seconds when the remote cleanup had started at the limit; it takes up to 25
seconds longer only if the cleanup had not started. A cleanup that has not finished by then is reported as
`Remote cleanup did not finish within 25 seconds`. A failed cleanup does not change the exit code (124) or the
`timed_out` result. Its error is printed to standard error as `Remote cleanup failed: ...` on the command line, also
in a quiet run, logged as a warning by MCP runs and by `adapter.run_benchmark(timeout_seconds=...)`, and recorded in
`validation_details["timeout"]["cleanup_errors"]` of the timed-out result. A resource whose stop failed stays marked
as started, so the normal close of the adapter tries again.

The warning is shown on every surface that takes a limit: the command line (once per run, from the platform the run
uses, including a platform chosen interactively), the `warnings` list of the MCP `run_benchmark` and `start_benchmark`
results, and a log warning from `adapter.run_benchmark(timeout_seconds=...)` in Python.

From Python, `adapter.run_benchmark(timeout_seconds=...)` and `benchmark.generate_data(timeout_seconds=...)` raise
`RunTimeoutError` when the limit is reached. Import it from the public module:

```python
from benchbox.core.exceptions import RunTimeoutError
```

The error carries `timeout_seconds`, `phase`, `query_id` and `detail`. It derives from `Exception`, not `BenchBoxError`.

| Platform | Cancel a statement | Stop a job, session or cluster | Run-started work is known | Implemented |
|---|---|---|---|---|
| Onehouse Quanton | Job cancel API | Terminate a cluster the client provisioned | Job ids the client submitted | Yes |
| BigQuery | Query job cancel | None in the query client | Job per query | Yes |
| EMR Serverless | Job run cancel | Stop an application the run created | Job run ids; created-by-run flag | Yes |
| AWS Glue | Stop job runs | None used | Job run ids | Yes |
| Dataproc | Job cancel | Delete an ephemeral cluster the run created | Job ids; created-by-run flag | Yes |
| Dataproc Serverless | Batch cancel is not in the Python client | None | Batch id | No |
| Athena, Athena Spark | Stop query or calculation, session terminate | Terminate session | Ids recorded, not tracked in flight | No |
| Databricks (SQL) | Cursor cancel | Warehouse stop (never ours) | Statement id after the call | No |
| Databricks DataFrame, Snowpark Connect | Session interrupt needs tags the adapter does not set | Session stop | No | No |
| Snowflake | Abort by query id | None | Statement id after the call | No |
| Redshift | None on the connector path | Cluster stop (never ours) | Query id after the call | No |
| Starburst | Cursor cancel | None | Statement id | No |
| Synapse, Fabric Warehouse, Fabric Lakehouse | ODBC cancel | None | No | No |
| Fabric Spark, Synapse Spark | Statement cancel over Livy | Delete a session the run created | Session and statement ids | No |
| ClickHouse Cloud | Server `KILL QUERY` | None | Query ids generated by the run | No |
| Firebolt, Databend, MotherDuck | Not retained or no ids | None | No | No |

GizmoSQL is not a registry `cloud` platform; it already cancels a running statement at the limit.
