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
- Never stop, resize, or signal a process or VM you did not start. Exception:
  the one machine named by `BENCHBOX_HEAVY_LINUX_MACHINE` (default
  `benchbox-agent`), never another machine or container, may be stopped by
  - `scripts/linux_machine_run.sh`, after every run, unless
    `BENCHBOX_KEEP_LINUX_MACHINE=1` (and the machine was not stopped before the
    run) or another same-user host process that is not its own child is running
    `container machine run`/`create` against the machine or another
    `linux_machine_run.sh <machine>`;
  - the sentinel, when the machine is running, no matching `container machine
    run`/`create` or `linux_machine_run.sh <machine>` process has been seen for
    `BENCHBOX_HEAVY_VM_IDLE_MINUTES`, and no live lock holder's tree contains
    `scripts/linux_machine_run.sh` or a `container machine run` for that
    machine.
- Interactive Linux VM use goes through
  `make heavy-run CMD='bash scripts/linux_machine_run.sh benchbox-agent -- <cmd>'`,
  never a hand-started `container machine run` or `create`. The documented
  setup `create` leaves the machine running; stop it with
  `container machine stop benchbox-agent`.

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

## Linux VM

`scripts/linux_machine_run.sh` stops the machine when its run ends, whatever
state the machine was in before (including unknown). It leaves it running only
when `BENCHBOX_KEEP_LINUX_MACHINE=1` (then the prior state is restored and the
idle reclaim below applies) or when another same-user host process that is not
its own child runs `container machine run`/`create` against the machine (`-n`
or `--name` naming it, or no name when the machine is `default: true`) or
the wrapper itself; it logs `left running: in use by pid N`. If that check
cannot run, the machine is stopped.

The sentinel stops the machine when it is running, no live lock holder's process
tree contains the wrapper or a `container machine run` for it, and no such
process has been seen for `BENCHBOX_HEAVY_VM_IDLE_MINUTES` (default 10). It
re-checks processes and holders immediately before stopping, gives
`container machine stop` 60 s without blocking memory checks, and after a
failure or timeout retries no sooner than the next idle window.
`BENCHBOX_HEAVY_WATCHDOG=warn` only logs the idle machine. Without `container`
on `PATH` every VM check is a no-op.

`make heavy-status` flags `VM running with no lock holder` (machine, state,
configured memory) with the remedy `container machine stop <machine>`, and
`UNGUARDED VM RUN pid N` for a live `container machine run`/`create` outside
every lock holder's tree. Both are report-only; unguarded runs are never
signalled.

The VM's physical footprint is shown as a separate line (`VM <machine>
footprint ... not counted in the shared budget`) in `make heavy-status`, the
sentinel log (written when it changes by 0.1 GiB or the VM process changes) and
the watchdog breach report. It is mapped to the machine by the exact
`/machines/<machine>/` segment of the VM process's open files (same user, 5 s
`lsof` bound), is read-only, and is skipped without `container` or off macOS.
`heavy-status` and the sentinel mark it `attributed to the lock holder` only
when a live holder's tree contains the wrapper for that machine. The watchdog
counts it toward a breach only above its own allowance,
`BENCHBOX_HEAVY_VM_ALLOWANCE_GIB` (default the machine's configured memory plus
10%), and only for the holder whose tree contains the wrapper; it is never added
to the shared budget. A VM breach SIGTERMs the tree so the wrapper's EXIT trap
stops the machine.

`benchbox-agent` is currently a 6G machine (the owner resized it from 8G on
2026-11-01), matching the documented `container machine create ... --memory 6G`.
`make ci-linux` and `scripts/linux_preflight_pytest.sh` export
`BENCHBOX_HEAVY_LINUX_MACHINE` from `CI_LINUX_MACHINE` (default that variable,
else `benchbox-agent`) so the wrapper and the sentinel name the same machine.

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
- A SIGKILLed `linux_machine_run.sh` skips its EXIT trap and leaves the machine
  running; the sentinel's idle reclaim stops it once installed.
- `container machine run -d` guest processes are invisible to the host; the
  machine is stopped when no host-side run remains, which ends them.
- `BENCHBOX_KEEP_LINUX_MACHINE=1` leaves the machine to the idle reclaim.
- VM memory is attributed only through the wrapper's machine name, never added
  to a holder's tree footprint, and admission does not subtract it.
- Orphan detection matches a fixed set of server names and needs `lsof` (or
  `/proc`) to read working directories; a failed lookup is shown as
  `unmeasured`, not as `none`.
