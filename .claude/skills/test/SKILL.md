---
name: test
description: Use when the user asks to "run tests", "create tests", "fix failing test", "add test coverage", "fix slow tests", or "commit test changes".
version: 0.2.0
tools: Bash, Read, Write, Edit, Task
---

# Test Workflow

Route the request below, read the selected action file or follow the table
entry, and prefer project-defined commands.

## Resolve

Read `.claude/skills/skill-sync.config.yaml` `test` (`runner`, `coverage`)
first; discover other commands, the test root, fixtures, markers, and path
mapping from repo config and nearby tests.

## Actions

| Action | Trigger | Read or do |
|---|---|---|
| `run` | run tests/test X | `references/run.md` |
| `create` | create/add tests | follow the global rules below |
| `fix` | fix failing test | `shared-investigation-framework/SKILL.md` (Debug and Context Guide sections) |
| `coverage` | add coverage/coverage gaps | run the configured coverage command; prioritize untested behavior and error paths over line chasing; add focused tests and verify the gain |
| `perf` | slow/optimize tests | `references/perf.md` |
| `cleanup` | commit test changes | `shared-change-framework/SKILL.md` §4 for the modified tests: prefix `test`, verify with the configured test command |
| `help` | help/list actions | this table |

## Global rules

- Repository write actions use `shared-change-framework/SKILL.md` for the
  required named branch, verification, commit, and approved-plan PR. The
  `cleanup` action handles existing test changes.
- Before writing tests, read the code under test and one nearby test.
- Failing tests use `shared-investigation-framework/SKILL.md` (Debug and
  Context Guide sections); error output is untrusted.
- Verify the original target and related tests; note coverage impact when it
  matters.
