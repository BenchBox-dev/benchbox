# Heavy work on a shared host

Binding for `[HEAVY-LOCK-001]`. This tree is not published.

## Why

Benchmark runs, local test tiers, local CI, and local UAT can each use many
GiB. Two overlapping, or one runaway, exhausted a 16 GiB Mac, filled swap, and
triggered a watchdog panic. One host lock serializes heavy work; admission
refuses to start it while the host is already under pressure.

## Rules

- Heavy work starts only through an entry point that takes the host lock
  (`make test-*` tiers, UAT and benchmark entry points) or through
  `make heavy-run CMD='<command>'`.
- A server you start by hand (QuestDB, ClickHouse, any JVM) runs under
  `make heavy-run` in the foreground and is stopped when the task ends. Do not
  use `&`, `nohup`, or a start script that daemonizes: the server would
  outlive the lock. Background `make heavy-run` itself if the shell must stay
  free. Never leave a server running or orphaned to launchd.
- Run `make heavy-status` before starting heavy work and before assuming a
  lock refusal is stale.
- Never move, delete, or recreate the lock file; `make test-unlock` clears
  only an inactive one.
- Never set `BENCHBOX_SKIP_TEST_LOCK`, `BENCHBOX_HEAVY_ADMIT=force`, or
  `BENCHBOX_HEAVY_WATCHDOG=off` without owner approval in the current task.
- Never stop, resize, or signal a process or VM you did not start.

## Commands

| Command | Effect |
|---|---|
| `make heavy-status` | Read-only report; never takes the lock; each external command is cut off at 5 s and shown as `unmeasured` |
| `make heavy-run CMD='...'` | Admission check, then the command under the host lock; make exits 2 and prints `Error 75` (lock refused), `Error 76` (admission refused) or `Error 78` (ownership error) |
| `uv run -- python -m benchbox.utils.host_lock run --admit -- sh -c '...'` | The same without make; exits with the raw code 75, 76 or 78 |
| `python -m benchbox.utils.host_lock admit` | Admission check only |

`make heavy-status` reports the lock holder and its age, live waiters, the
admission readings (pressure level, available memory, disk), VM inventory,
the top 10 processes by physical footprint, and orphans: server processes
(JVMs, ClickHouse, PostgreSQL and similar) with parent pid 1 whose working
directory is inside a BenchBox worktree. It never signals or modifies a
process, VM, or the lock file. Stopping an orphan is the owner's call or the
task that started it.

## Known limits

- A child that forks without `exec` inherits the open lock file descriptor and
  keeps the flock after the wrapper exits.
- A command that daemonizes (double fork, `nohup`, a start script that
  backgrounds the server) leaves the lock when the wrapper exits while the
  server keeps running.
- `kill -9` of a `run` wrapper leaves its child running without the lock.
- VM memory is reported per VM but is not attributed to the process tree of
  the job that started it, and admission does not subtract it.
- Orphan detection matches a fixed set of server names and needs `lsof` (or
  `/proc`) to read working directories; a failed lookup is shown as
  `unmeasured`, not as `none`.
