# Iterate To Green

Use when the user asks to drive a command, test suite, CI gate, lint, typecheck,
or migration until it passes.

## Loop

1. Run the requested command exactly unless unsafe.
2. Save long or recurring output under the project's artifact directory, else
   `/tmp/iterate-<slug>/run<N>.log`.
3. Cluster failures by signature: error class + failing unit + likely layer.
4. For one cluster at a time: research, debug, make a narrow fix, verify the
   target, review, and commit it.
5. Re-run the original command.
6. Stop on green, documented hard blocker, or `--max-iterations` (default 20).

## Flags

- `--max-iterations N`: cap loop.
- `--narrow "<cmd>"`: preferred minimal repro.
- `--dry-run`: plan clusters/fixes but do not edit.
- `--no-commit`: use only when the user explicitly requires local-only work.

## Artifacts

- `status.md`: current command, iteration, cluster status, last result.
- `run<N>.log`: raw command output when useful.
- `blockers.md`: root cause, tried/ruled fix hierarchy, why remaining work is outside authority.

## Rules

- Do not batch unrelated fixes.
- Apply the investigation framework's hard-blocker criteria before marking work
  blocked.
- Do not hide remaining failures after one cluster turns green.
