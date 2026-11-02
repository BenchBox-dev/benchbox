# Benchmark Plan And Execute Reference

Use for benchmark/platform features that need research, implementation, and verification.

## Flow

1. Research current benchmark/platform patterns, tests, docs, and TODOs.
2. State goal, scope, constraints, public interfaces, and success criteria.
3. Apply `shared-change-framework/SKILL.md` Section 1 before source-code edits,
   then slice vertically, implement one working path, test, verify, commit, and
   submit the approved plan as a draft PR (a delegated batch member does not
   open one: it hands its change to the batch leader as a landing request).
4. Preserve phase propagation, validation, timing policy, artifact paths, and lazy optional deps.
5. Update docs/tests only where the behavior changes.

## Verification

Run targeted tests, fast smoke, applicable standards or platform checks, and
policy audits for touched areas. Report skipped expensive or live checks.
