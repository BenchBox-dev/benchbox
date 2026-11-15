---
name: benchbox
description: Use only in the BenchBox repository, when the user asks to "test TPC-H", "check compliance", "review architecture", "run quality checks", "check binaries", "test dialect translation", "compare implementations", "run live platform tests", "cut a release", "finalize a release", or "plan and execute" a benchmark feature.
version: 0.3.0
tools: Bash, Read, Write, Edit, Glob, Grep, Task
---

# BenchBox Workflow

Route the request below and read the selected action file. Keep
machine-readable output (JSON, result files) alongside the human summary when
the command produces it.

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

## Hard rules

- Repository rules (`uv run --` for Python, repository `make` targets, the
  timing clock, DDL rewrite registration, heavy-work lock, UAT output
  directory) are in the BenchBox `AGENTS.md`; follow it.
- Pass phases through `--phases` (comma-separated; valid names are
  `benchbox.core.constants.VALID_PHASES`). Smoke scale defaults to `0.01`;
  scales >=1 are whole integers.
- Do not run live cloud tests without explicit approval and credentials.

## Output

Report commands, results, artifacts, findings, and next steps. For failures,
include a root-cause hypothesis and the narrowest next check.
