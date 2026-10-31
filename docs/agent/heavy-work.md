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
| `make heavy-run CMD='...'` | Admission check, then the command under the host lock and watchdog; make exits 2 and prints `Error 75` (lock refused), `Error 76` (admission refused), `Error 78` (ownership error) or `Error 79` (watchdog stopped the tree) |
| `uv run -- python -m benchbox.utils.host_lock run --admit --guard -- sh -c '...'` | The same without make; exits with the raw code 75, 76, 78 or 79 |
| `python -m benchbox.utils.host_lock admit` | Admission check only |
| `make heavy-sentinel` | Foreground sentinel; `SENTINEL_ARGS='--duration 30 --log /tmp/s.log'` bounds it and redirects the log |
| `make heavy-sentinel-install` / `make heavy-sentinel-uninstall` | Write or remove the per-user LaunchAgent `~/Library/LaunchAgents/dev.benchbox.sentinel.plist` and load or unload it with `launchctl bootstrap/bootout gui/$UID`; changes host state, owner approval required |

`make heavy-status` reports the lock holder and its age, live waiters, the
admission readings (pressure level, available memory, disk), VM inventory,
the top 10 processes by physical footprint, and orphans: server processes
(JVMs, ClickHouse, PostgreSQL and similar) with parent pid 1 whose working
directory is inside a BenchBox worktree. It never signals or modifies a
process, VM, or the lock file. Stopping an orphan is the owner's call or the
task that started it.

## Sentinel

The sentinel is a per-user watcher independent of any lock holder. Every 5 s it
reads pressure and footprints of python, java and database-server processes
whose working directory or executable is inside a BenchBox checkout or linked
worktree (from `git worktree list` of the primary clone, refreshed every
minute). Command-line paths scope a process only when its working directory
cannot be read. Node/bun, editors, language servers (argv stems containing
`lsp`, `language_server`, `language-server` or `langserver`), MCP servers
(`mcp` in the script name, its parent directory or the module), shells and
`git` are never candidates. A process over the memory budget, or elevated
pressure (level 2) while such processes together exceed it, gets SIGTERM, then
SIGKILL after 10 s (`BENCHBOX_HEAVY_WATCHDOG=warn` only reports). Processes in
a live lock holder's tree are left to that holder's watchdog unless pressure is
critical. Breaches, actions and a process summary go to
`~/.benchbox/sentinel.log` (rotated, at most 3 MiB); the summary is written
only when the set of processes over half the budget, the pressure level or the
git-flood state changes, and stderr gets the same lines only on a terminal.
The pid file is `~/.benchbox/sentinel.pid`, independent of `--log`. The
LaunchAgent writes launchd errors to `~/.benchbox/sentinel.launchd.err` and
carries the `BENCHBOX_HEAVY_*`, `BENCHBOX_GIT_FLOOD_*` and
`BENCHBOX_TEST_LOCK_DIR` settings present at install time; install first runs
`<python> -m benchbox.utils.host_lock sentinel --help` in the checkout and
refuses on failure. `make heavy-status` reports whether the sentinel is running.

## Git floods

`git` is never a kill candidate, but the sentinel and `make heavy-status`
report when same-user `git` processes with a working directory in a BenchBox
checkout number `BENCHBOX_GIT_FLOOD_COUNT` (default 16) or more, or their
summed footprint reaches `BENCHBOX_GIT_FLOOD_RAM_FRACTION` (default 0.25) of
RAM. The sentinel logs a WARNING with the five largest once when the flood
starts and a line when it clears. Nothing is signalled; stop the launching
shells instead.

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
