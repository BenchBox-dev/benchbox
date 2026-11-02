# Code Implementation Actions

## Implement

Follow configured project conventions and checks.

## Commit

Discover session files, inspect `git status --porcelain` and the diff, verify,
make a conventional commit, and push through the commit framework. A delegated
batch member instead stops at the committed, checked change and hands it to its
leader: see the delegated batch member exception in
`shared-change-framework/SKILL.md` §4.

## Fix

- Lint: use configured lint/fix commands.
- Type: run typecheck and add annotations where needed.
- Runtime: apply the investigation framework's Debug workflow.

## Debug

Use the Debug and Context Guide sections of
`shared-investigation-framework/SKILL.md`.

## Perf

Measure a baseline, profile, optimize, and remeasure. State the performance
budget. Before optimizing, apply Layer 3 of
`shared-review-protocol/SKILL.md` to confirm the measured bottleneck is the
real constraint.
