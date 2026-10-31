<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Heavy-work guard for the CLI

Benchmark runs can use most of a workstation's memory and disk. The guard makes the CLI take a host-wide lock,
check that the host has headroom, and run a watchdog for the lifetime of the command. It is off by default so
parallel cloud runs and CI behave as before.

## Enabling

Set `host_lock: true` at the top level of `~/.benchbox/config.yaml`:

```yaml
host_lock: true
```

Or set `BENCHBOX_HOST_LOCK=1` in the environment (`0` disables it and overrides the file). The file accepts `true`,
`1`, `yes` or `on`, as the variable does. Only the file under `.benchbox/` in the home directory (`$HOME`,
`%USERPROFILE%` on Windows) and the environment variable are consulted. A `benchbox.yaml` in the working directory
cannot enable or disable the guard.

## Commands that take the lock

When enabled, these commands hold the lock, pass admission and run under the watchdog until they exit:

- `benchbox run`, except `--dry-run`
- `benchbox run-official`
- `benchbox datagen`
- `benchbox convert`
- `benchbox compare` with `-p` platforms, or in the interactive wizard: the wizard cannot tell in advance whether it
  will run queries, so the whole command is locked. `compare` on result files, `compare --list-platforms` and
  `compare --non-interactive` without platforms (a usage error) never lock.
- `benchbox compare-dataframes`, except `--list-platforms`

Cloud-platform runs are locked too, even though only data generation is local. Help, results and other read-only
commands never lock.

Processes started by a locked command, and commands run from inside a locked test or UAT session, share that
lock instead of waiting on it.

## Contention

The lock file is `~/.benchbox/test.lock`. A command that finds it held waits up to an hour, printing the holder.

| Variable | Effect |
| --- | --- |
| `BENCHBOX_TEST_LOCK_DIR` | Use `<dir>/test.lock` instead of the default directory |
| `BENCHBOX_TEST_LOCK_WAIT_SECONDS` | Seconds to wait for the lock; `0` fails at once |
| `BENCHBOX_HOST_LOCK=0` | Bypass the guard for one command |

A refused command exits with a code that names the cause: 75 lock held, 76 admission refused, 78 ownership error
(the environment was scrubbed between lock holder and descendant), 79 watchdog stopped the command.

## Admission

Before starting, the command checks memory pressure, available memory and free disk. Tune with
`BENCHBOX_HEAVY_ADMIT_MIN_GIB`, `BENCHBOX_HEAVY_MIN_LEVEL` and `BENCHBOX_HEAVY_MIN_FREE_DISK_GIB`.
`BENCHBOX_HEAVY_ADMIT=force` runs despite a refusal and logs a warning.

## Watchdog

`BENCHBOX_HEAVY_WATCHDOG` selects the mode:

- `kill` (default): stop the command's process tree when its memory passes `BENCHBOX_HEAVY_MEMORY_BUDGET_GIB`,
  memory pressure turns critical, or free disk falls below `BENCHBOX_HEAVY_KILL_FREE_DISK_GIB`.
- `warn`: report the same conditions without stopping anything.
- `off`: no watchdog.

The watchdog uses macOS memory readings and a portable fallback elsewhere. On Windows the lock lives under the user's
home directory and no watchdog process is started.

The watched process is the command itself when the engine runs in-process (DuckDB, for example). On a breach the
watchdog sends SIGTERM and then SIGKILL after a grace period, so the command may exit 137 instead of 79.
