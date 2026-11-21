# Run timeout: one optional limit for the whole run

Date: 2026-11-20
Status: Decided by the owner on 2026-11-20.

## Context

No surface let a user bound a run, and the runner could not stop one. Separate
fixed limits existed instead: 30 and 300 seconds on `dsqgen`, 10 seconds on
`qgen`, 300 and 600 seconds on generator compilation, and execution defaults of
3600 and 7200 seconds per stream. A fixed limit stops a legitimate large run,
and none of them could be changed from the command line. The owner's rule:
BenchBox must allow a user to set a limit and must not impose one.

## Decisions

1. **One setting.** `--timeout SECONDS` on `run`, `run-official` and `datagen`;
   `timeout_seconds` on the MCP `run_benchmark` tool; `execution.timeout_seconds`
   in the config file; `timeout_seconds=` on `benchmark.generate_data()` and
   `adapter.run_benchmark()`. The command line wins over the config file.
2. **No default.** Unset means no limit. `0` also means no limit, so a command
   line can switch off a value set in the config file. Negative and fractional
   values are usage errors.
3. **One clock for the invocation.** Generation, loading and execution share
   it. Narrower settings remain tighter limits inside it. In Python each of the
   two calls has its own clock; a call made inside an already bounded run uses
   that run's clock.
4. **At the limit BenchBox asks the run to stop.** It checks between phases and
   between queries, never inside a timed section. In this change a generator
   that is already running is not interrupted at the limit; the run stops at
   the next check. If the run has not stopped 30 seconds later, the command
   writes the partial result, prints its path, ends every child process it
   started and exits. The result is written before the child processes are
   ended, and a run that returns while this is happening waits for it, so the
   exit code and the result do not depend on which finishes first. The 30
   seconds is a constant (`HARD_STOP_GRACE_SECONDS`), not a setting. The hard
   bound is therefore the limit plus 30 seconds.
5. **Exit code 124.** The value `timeout(1)` uses.
6. **The partial result is kept and marked.** `summary.validation` is
   `timed_out`, and `execution.timeout` records `timeout_seconds`, `phase` and
   `query_id`. `query_id` is the query that was about to start when the run
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
   `timed_out` is a non-clean validation status.
8. **Python does not exit the caller's process.** `adapter.run_benchmark()`
   raises `RunTimeoutError`, which carries the marked partial result. There is
   no hard stop in Python: a call that never reaches a check is not
   interrupted. Until generators are routed through the deadline,
   `generate_data()` is not stopped at the limit; when it returns after the
   limit it raises `RunTimeoutError` with the generated paths as
   `partial_result`, and when it fails it raises its own error.
9. **MCP.** `run_benchmark` and `start_benchmark` both take
   `timeout_seconds`. A timed-out run returns an error result with
   `mcp_metadata.status = timed_out`. A durable job is recorded as incomplete
   and is not retried. The server process is never exited.

## What the limit does not cover

- Container start-up, health checks, credential checks, the generator `-h`
  probes and query plan capture keep their own limits.
- The clock starts when the command starts running the benchmark, after any
  wait for the host lock.
- The backstop uses a thread and the monotonic clock, not `SIGALRM`, so it
  works on Windows. On a platform without process groups only the generator
  process BenchBox started is ended, not its children.
- Work already submitted to a remote platform may keep running after BenchBox
  exits. The hard-stop message says so.

## Rejected

- **A default limit.** It is the defect being removed.
- **A separate limit per phase or per tool.** The owner asked for one setting.
- **A configurable grace.** One more number to explain, for no user need.
- **Reusing `failed` or `interrupted`.** Both are dropped without a bundle; a
  timed-out run keeps the queries it finished, and the reason must be visible
  in the bundle.

## Delivery

This change adds the deadline, the surfaces, the marker, the exit code and the
backstop. Later changes route the generator and compiler calls through the
deadline and remove their fixed limits, extend the checks to the other
execution loops, verify remote cleanup, pass the limit from UAT cells, and
remove the unenforced timer settings.
