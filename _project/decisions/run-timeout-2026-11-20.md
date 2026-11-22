# Run timeout: one optional limit for the whole run

Date: 2026-11-20 (updated 2026-11-22 to the behaviour as delivered)
Status: Decided by the owner on 2026-11-20 and 2026-11-21; delivered.

## Delivered in

- Deadline, surfaces, marker, exit code and hard stop: #4423.
- Generators and compilers ended at the limit; their fixed limits removed; partial-data marker: #4434.
- Run-time checks in every execution loop, remote cleanup, no Power@Size for a power test stopped at the limit: #4435.
- UAT cells stopped by the product limit (`timed-out` and `hung`), unused timers and config keys removed, plan
  capture bounded by the run: #4437.
- Generation duration estimate: #4439.
- Follow-ups in this change (not yet merged): the public import path of `RunTimeoutError`, the `run-official` usage
  exit for an invalid config value, the UAT retry budget, the removal of `execution.timeout_minutes` and the unused
  `memory_gb` argument, and the verified TERM, wait, KILL sequence.

## Context

No surface let a user bound a run, and the runner could not stop one. Separate
fixed limits existed instead: 30 and 300 seconds on `dsqgen`, 10 seconds on
`qgen`, 300 and 600 seconds on generator compilation, and execution defaults of
3600 and 7200 seconds per stream. A fixed limit stops a legitimate large run,
and none of them could be changed from the command line. The owner's request:

> "We should have a generalized run timeout (e.g. `--timeout SECONDS`) that applies to any type of run; not jsut data generation but also benchmark executions." <!-- codespell:ignore jsut -->

> "We do need to **ALLOW** a user to specify a datagen timeout even though we MUST NOT enforce a default timeout."

The owner's rule: BenchBox must allow a user to set a limit and must not impose one.

## Decisions

1. **One setting.** `--timeout SECONDS` on `run`, `run-official` and `datagen`;
   `timeout_seconds` on the MCP `run_benchmark` tool; `execution.timeout_seconds`
   in the config file; `timeout_seconds=` on `benchmark.generate_data()` and
   `adapter.run_benchmark()`. The command line wins over the config file.
   (`benchbox/cli/run_time_limit.py`, `benchbox/utils/run_deadline.py`.)
2. **No default.** Unset means no limit. `0` also means no limit, so a command
   line can switch off a value set in the config file. Negative and fractional
   values are usage errors (exit 2). The owner's words: "0 means no limit,
   matching the existing settings; it lets a command line switch off a limit
   set in the config file. Negative and fractional values are usage errors."
   An invalid `execution.timeout_seconds` in the config file is a usage error
   naming that key for `run`, `run-official` and `datagen` alike; `run-official`
   no longer reports it as a failed run with exit 1 (this change,
   `benchbox/cli/commands/run_official.py`).
3. **One clock for the invocation.** Generation, loading and execution share
   it. Narrower settings remain tighter limits inside it. In Python each of the
   two calls has its own clock; a call made inside an already bounded run uses
   that run's clock. Time spent waiting for the host lock does not count toward
   the limit: the clock starts when the command starts running the benchmark.
4. **At the limit BenchBox asks the run to stop.** It checks between phases and
   between queries, never inside a timed section. A generator, compiler or
   tool build that is running is ended at the limit (#4434): `dbgen`, `dsdgen`,
   `qgen`, `dsqgen` and the tool builds run through the deadline, which ends
   their process group. Every other execution loop checks the deadline too
   (#4435). If the run has not stopped 30 seconds later, the command
   cancels remote work (decision 10), writes the partial result, prints its
   path, ends every child process it started and exits. The result is written
   before the child processes are ended, and a run that returns while this is
   happening waits for it, so the exit code and the result do not depend on
   which finishes first. The 30 seconds is a constant
   (`HARD_STOP_GRACE_SECONDS`), not a setting. The hard bound is therefore the
   limit plus 30 seconds.
5. **Exit code 124.** The value `timeout(1)` uses.
6. **The partial result is kept and marked.** The owner's words: "a new status
   value `timed_out` in `summary.validation`, plus a new optional key
   `execution.timeout` holding `timeout_seconds`, `phase` and `query_id`."
   `query_id` is the query that was about to start when the run
   stopped; it did not run. It is absent when the run stopped between phases,
   finished its work after the limit, or was ended by the hard stop. Result
   schema 2.6 adds the key; a 2.5 or older bundle that carries it is refused.
   A run that failed, errored or was interrupted keeps that verdict and its
   exit code even when it also passed the limit; the timeout is recorded
   beside it. An error raised after the limit is still reported as that
   error, not as a timeout. A bundle that is loaded and exported again keeps
   its `execution.timeout` block.
7. **A timed-out result is never a benchmark result.** It loses an official
   compliance class, is refused by publish and submit, is excluded from
   ranking, and fails corpus validation. Each path refuses it because
   `timed_out` is a non-clean validation status. A power test stopped at the
   limit reports no Power@Size, and the run reports no TPC metrics (#4435,
   `benchbox/core/tpch/power_test.py`, `benchbox/core/tpcds/power_test.py`,
   `benchbox/core/results/builder.py`).
8. **Python does not exit the caller's process.** The owner's words: "In Python
   a timeout raises `RunTimeoutError`; there is no hard exit of the caller's
   process, and the docs say so." `adapter.run_benchmark()` raises
   `RunTimeoutError`, which carries the marked partial result. There is no hard
   stop in Python: a call that never reaches a check is not interrupted.
   `generate_data()` raises `RunTimeoutError` when a generator is ended at the
   limit, and when it returns after the limit it raises `RunTimeoutError` with
   the generated paths as `partial_result`; when it fails it raises its own
   error. Import the class from `benchbox.core.exceptions`; it is defined in
   `benchbox/utils/run_deadline.py` so that `utils` does not import `core`
   (this change). It derives from `Exception`, not `BenchBoxError`.
9. **MCP.** `run_benchmark` and `start_benchmark` both take
   `timeout_seconds`. A timed-out run returns an error result with
   `mcp_metadata.status = timed_out`. A durable job is recorded as incomplete
   and is not retried. The server process is never exited.
10. **Remote cleanup** (#4435). When the timeout is recorded, or the limit
    passes, BenchBox calls `PlatformAdapter.cancel_remote_work` on each adapter
    the run created. It cancels only work this run started and has not seen
    finish; it never stops a warehouse, application, cluster or session that
    existed before the run. Implemented for Onehouse Quanton, BigQuery, EMR
    Serverless, AWS Glue and Dataproc. The run waits for the cleanup at most
    25 seconds, which is 5 seconds before the 30-second grace ends, so a run
    that stopped cleanly still exports its full result; the hard stop waits only
    for what is left of that time. An unfinished cleanup is reported as
    `Remote cleanup did not finish within 25 seconds`. A failed cleanup does not
    change exit code 124 or the `timed_out` result; its error is printed,
    logged on MCP and Python, and recorded in
    `validation_details["timeout"]["cleanup_errors"]`.
    (`benchbox/utils/run_deadline.py`, `docs/operations/run-safety.md`.) The
    owner chose the warning texts. With cleanup implemented: "A run time limit
    is set. If the run times out, BenchBox will try to cancel the work it
    started on {platform}. This cleanup has not been checked against the live
    service, so work may keep running and billing." Not implemented: "A run
    time limit is set. If the run times out, work BenchBox started on
    {platform} may keep running and billing: BenchBox cannot cancel it on this
    platform." Cleanup is verified with stubbed clients only; the owner ruled
    out live cloud runs.
11. **Plan capture** keeps its own 30-second default
    (`plan_capture_timeout_seconds`) and is bounded by the smaller of that
    setting and the time left on the run (#4437,
    `benchbox/platforms/base/result_capture.py`). When the run's remaining time
    is the binding budget, plan capture records the run timeout, returns no plan
    and reports no plan-capture failure; the run stops at its next check and
    keeps the measured result.
12. **UAT** (#4437, `tests/uat/runner.py`). Every cell passes `--timeout`
    equal to its cell limit, so BenchBox stops a slow cell itself. The cell is
    `timed-out` when BenchBox stopped it (exit 124) and `hung` when the runner
    killed it from outside, which happens only at the cell limit plus 60
    seconds. A cell that relied on the removed 3600 or 7200 second stream
    defaults sets the old value explicitly. A failed cell with an
    infrastructure signature is retried once, only when the job's remaining
    budget covers the cell limit plus the 60-second outside-kill margin (this
    change, `tests/uat/infrastructure_retry.py`).
13. **Generation estimate** (#4439, `benchbox/core/generation_estimate.py`).
    TPC-H and TPC-DS runs print a measured estimate before data generation and
    before TPC-DS query generation, and the dry run and interactive previews
    print the same line. The owner chose the texts: "Rough estimate: generation
    will take about {estimate} (measured on {host_class} at scale
    {scales}{extrapolated_note})." and "The run limit of {limit} seconds is
    shorter than the estimated generation time of about {estimate}." The second
    is printed only when a limit is set and is shorter; the limit is not changed.
14. **Removed settings** (#4437; this change for the last key). The timeout
    manager, the `ResourceLimitsConfig` timeout fields and the config keys
    `benchmarks.timeout_minutes`,
    `execution.power_run.timeout_per_iteration_minutes` and
    `execution.concurrent_queries.query_timeout_seconds` are removed, and so
    are `execution.timeout_minutes` and the unused `memory_gb` argument of
    `ExecutionConfigHelper.optimize_for_system` (this change). A config file that
    sets a removed key still loads and prints the owner's message: "`{key}` is
    no longer used and has no effect. Use `execution.timeout_seconds` to limit a
    run." (`benchbox/cli/config.py`.) The 3600 and 7200 second stream defaults
    are removed; an unset `stream_timeout_seconds` means no stream limit.
15. **Process stopping.** A child tree is stopped with SIGTERM, a 0.2-second
    wait, then SIGKILL (`benchbox/utils/process_group.py`). A test starts child
    trees that ignore SIGTERM and shows each caller still ends them and that the
    hard stop still exits 124 within the limit plus 30 seconds (this change,
    `tests/unit/utils/test_process_group_term_ignoring_children.py`).
16. **No new CI check** against hard-coded timeouts. Container start-up,
    health-check and credential limits and the 5 and 15 second generator `-h`
    probes stay.

## What the limit does not cover

- Container start-up, health checks, credential checks, the generator `-h`
  probes and the plan-capture setting keep their own limits.
- The clock starts when the command starts running the benchmark, after any
  wait for the host lock.
- The backstop uses a thread and the monotonic clock, not `SIGALRM`, so it
  works on Windows. On a platform without process groups only the process
  BenchBox started is ended, not its children.
- Work already submitted to a remote platform may keep running after BenchBox
  exits. The hard-stop message says so. Platforms without cleanup warn
  accordingly (decision 10).

## Rejected

- **A default limit.** It is the defect being removed.
- **A separate limit per phase or per tool.** The owner asked for one setting.
- **A configurable grace.** One more number to explain, for no user need.
- **Reusing `failed` or `interrupted`.** Both are dropped without a bundle; a
  timed-out run keeps the queries it finished, and the reason must be visible
  in the bundle.

## Deferred

- A live check of remote cleanup: blocked, because no live cloud run is allowed.
  Until one is done the warning says the cleanup has not been checked against
  the live service.
- Remote cleanup for more adapters: deferred by the owner.
