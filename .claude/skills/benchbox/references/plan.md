# Benchmark Plan And Execute Reference

Use for benchmark/platform features that need research, implementation, and verification.

## Flow

1. Research current benchmark/platform patterns, tests, docs, and TODOs.
2. State goal, scope, constraints, public interfaces, and success criteria.
3. Apply `shared-change-framework/SKILL.md` Section 1 before source-code edits,
   then slice vertically, implement one working path, and verify. Close out
   per `AGENTS.md` [WRITE-CLOSEOUT-001].
4. Preserve phase propagation, validation, timing policy, artifact paths, and lazy optional deps.
5. Update docs/tests only where the behavior changes.

## Verification

Run targeted tests and `make test-fast`, then `make pr-preflight` before
`make pr-open`. Report skipped expensive or live checks.
