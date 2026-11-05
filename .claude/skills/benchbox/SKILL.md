---
name: benchbox
description: Use when the user asks to "test TPC-H", "check compliance", "review architecture", "run quality checks", "check binaries", "test dialect translation", "compare implementations", "run live platform tests", "cut a release", "finalize a release", or "plan and execute" a benchmark feature.
version: 0.3.0
tools: Bash, Read, Write, Edit, Glob, Grep, Task
---

# BenchBox Workflow

Route the request below and read the selected action file. Detect the runner,
honor non-interactive mode, and produce human-readable and machine-readable
artifacts when supported.

## Actions

| Action | Read |
|---|---|
| `test` | `references/test.md` |
| `quality` | `references/quality.md` |
| `compliance` | `references/compliance.md` |
| `dialect` | `references/dialect.md` |
| `binary` | `references/binary.md` |
| `compare` | `references/compare.md` |
| `live` | `references/live.md` |
| `architecture` | `references/architecture.md` |
| `plan` | `references/plan.md` |
| `release` | `references/release.md` |

Aliases remain accepted: `benchmark-test`, `quality-check`, `qa`,
`tpc-compliance`, `dialect-translation`, `sql`, `binary-check`, `binaries`,
`compare-impl`, `live-test`, `platform-test`, `arch`, `arch-review`,
`plan-execute`, and `implement`.

## Hard rules

- Use `uv run --` for Python; Makefile wrappers are fine.
- Pass `generate`, `load`, `power`, `throughput`, and `maintenance` through
  `--phases`. Smoke scale defaults to `0.01`; scales >=1 are whole integers.
- Do not run live cloud tests without explicit approval and credentials.
- Use `benchbox.utils.clock.mono_time()` / `elapsed_seconds()` for durations.
- Register adapter DDL rewrites under
  `benchbox/sql_compat/rules/ddl_optimize/`.
- UAT/long-run sweeps follow AGENTS.md "Long-Running UAT" and
  `docs/operations/uat-framework.md`.

## Output

Report commands, results, artifacts, findings, and next steps. For failures,
include a root-cause hypothesis and the narrowest next check.
