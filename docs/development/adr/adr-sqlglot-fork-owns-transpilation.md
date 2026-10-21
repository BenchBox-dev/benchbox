# ADR: The SQLGlot Fork Owns Transpilation

- Status: Accepted
- Date: 2026-10-20
- Supersedes in part: [SQLGlot Use and Non-Use](adr-sqlglot-use-and-non-use.md)
  (the bounded, retirable fork framing) and
  [`sql_compat` Phase-Aware Compatibility Pipeline](adr-sql-compat-phase-aware-pipeline.md)
  (engine-bug workarounds, physical DDL bodies and benchmark-variant rewrites
  move to the fork).
- Constrains: `benchbox/utils/dialect_utils.py`, every module that imports
  `benchbox._vendor.sqlglot`, and the platform registry's dialect mapping.

## Context

BenchBox ships a pinned wheel of
[`BenchBox-dev/benchbox-sqlglot`](https://github.com/BenchBox-dev/benchbox-sqlglot)
as `benchbox._vendor.sqlglot`. Around that wheel BenchBox accumulated
translation fix-ups: wrapper post-processors, per-adapter safeguards and
rewriters, and DDL rewrite rules. They are regex and string based, are
duplicated across paths that bypass the wrapper, and cannot be executed against
real engines in isolation from BenchBox. The fork was framed as a bounded set of
patches to retire, which kept the fix-ups in BenchBox.

## Decision

The fork is permanent and owns SQL transpilation, including DDL translation.
BenchBox calls it only through `translate_sql_query` in
`benchbox/utils/dialect_utils.py` and the wrapper functions beside it
(`translate_ddl`, `apply_rewrite`). No other BenchBox module calls the vendored
SQLGlot to generate dialect output. Parse-only uses (DataFrame translation,
equivalence checks, qualification) are allowed and listed with reasons in a
repository test that fails on any other direct call.

BenchBox keeps benchmark definitions, execution, result validation, DataFrame
translation and governance metadata. Benchmark-keyed data (query-id sets, shard
and sort key tables, variant dispatch tables) stays in BenchBox and is passed to
the fork as arguments; the mechanism lives in the fork.

Rules that follow:

- Each piece moved into the fork has a fork test that executes the translated
  SQL on a real engine where one is obtainable. Otherwise it ships with AST
  tests and a recorded gap.
- Output may change when semantically equivalent. Each adoption lists every
  output difference over the unit-test inputs of the moved areas and carries a
  result-version note. Changes to published results stop for an owner decision.
- Behavior-changing options, such as `safe_division`, are opt-in in the fork and
  passed explicitly by each adapter.
- The platform-to-dialect mapping lives in BenchBox's platform registry; the
  fork provides dialects only. An unknown platform raises, with no silent
  Postgres fallback.
- Generally useful fixes are still offered upstream. BenchBox correctness does
  not wait on upstream review.

## Alternatives Considered

- **Keep fix-ups in BenchBox around a bounded fork**: rejected. It leaves
  mechanism split across string rewrites and bypass paths, and the fork's
  generators cannot be tested against engines with the fix-up applied.
- **Contribute everything upstream and carry nothing**: rejected. Upstream
  review timing and scope (engine-semantic rewrites, physical DDL) do not match
  BenchBox's correctness needs.
- **Put the platform-to-dialect mapping in the fork**: rejected. Adding a
  platform would require a fork release.
- **Fall back to a default dialect for unknown platforms**: rejected. It hides
  missing dialect work behind plausible but wrong SQL.
- **Enable division safeguards by default**: rejected. A zero divisor returning
  NULL changes published semantics.

## Consequences

- One entry point to test, audit and roll back; adding translation behavior
  means a fork change plus a wheel adoption, not BenchBox string rewriting.
- The fork carries long-lived patches to upstream-owned files, tracked in its
  downstream manifest and replayed on upstream refreshes.
- Each adoption needs a recorded fork commit, a corpus diff and a pin, artifact
  and provenance update together. Rollback restores the previous wheel, pin and
  baseline together.
- `benchbox/sql_compat/` and its phase architecture remain as governance; rows
  that anchor adapter-local runtime behavior stay until that behavior moves.
