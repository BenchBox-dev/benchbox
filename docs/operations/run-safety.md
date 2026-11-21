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
hard stop waits for the cleanup only for what is left of its 30-second grace, so the hard bound stays the limit plus
30 seconds. A cleanup that has not finished by then is reported as `Remote cleanup did not finish within 30 seconds`.
A failed cleanup does not change the exit code (124) or the `timed_out` result; its error is printed to standard
error as `Remote cleanup failed: ...`, also in a quiet run.

The warning is shown on every surface that takes a limit: the command line (once per run, from the platform the run
uses, including a platform chosen interactively), the `warnings` list of the MCP `run_benchmark` and `start_benchmark`
results, and a log warning from `adapter.run_benchmark(timeout_seconds=...)` in Python.

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
