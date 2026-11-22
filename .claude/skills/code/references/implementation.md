# Code Implementation Actions

## Implement

Apply the configured checks above and `shared-change-framework/SKILL.md`.

When code must classify the meaning of free text (error messages, prose, user
input) and a keyword or regex list would keep growing, use a typed judgment
call such as TypeSafe Jev (the `typesafe-ai` skill covers the API), with a
labelled calibration set kept as a test, a fixed threshold, recorded answers
so tests run offline, a defined fallback when no key is set, and the user's
approval before private data leaves the machine. Keep parsers, exact or
numeric comparisons, and code that must be reproducible or run offline (such
as benchmark paths) deterministic.

## Commit

Apply `shared-change-framework/SKILL.md` §4 to the files changed in this
session.

## Fix

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
