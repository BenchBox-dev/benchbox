# ADR: Corpus Public-Mirror Validation-Status Admission Policy

- Status: Accepted
- Date: 2026-09-01
- Constrains: `benchbox/validation/bundle.py` (public submission and trusted
  mirror validators), the maintainer seed/curated corpus under
  `results-data/bundles/`, and any future change to
  `PUBLIC_MIRROR_ALLOWED_VALIDATION_STATUSES`.

## Context

`benchbox/validation/bundle.py:93` defines:

```python
PUBLIC_MIRROR_ALLOWED_VALIDATION_STATUSES = frozenset({"passed", "partial", "not_run"})
```

This is the admission gate for `summary.validation` on the **trusted
maintainer mirror path** (`--allow-partial-validation`, wired only in
`scripts/validate_submission.py:316-320` with the comment "Trusted
maintainer mirror only... Community CI must not pass this flag"). It is
enforced by `_validate_summary_section` (`bundle.py:316-346`), called from
`_validate_bundle` (`bundle.py:715-741`) with `allow_partial_validation`
threaded through from that flag. Community submissions never set the flag,
so `_validate_summary_section` falls back to `allowed =
frozenset({PUBLIC_CLEAN_VALIDATION_STATUS})` — `"passed"` only
(`bundle.py:332-336`).

The allowlist has changed twice, and the reasoning for the second change is
not obvious from the diff:

1. **2026-08-05:** the allowlist was introduced as `{"passed", "partial"}`,
   letting the trusted mirror admit partial cohorts while community
   submissions stayed `passed`-only.
2. **2026-08-26:** `"not_run"` was added. Read as a bare diff, this looks like
   a loosening — admitting *unvalidated* results into a published corpus. It
   is the opposite. The corpus then held 136 bundles, 52 of which were legacy
   bundles whose `phases.validation.status` was `NOT_RUN` while
   `summary.validation` claimed `passed` (23 bundles) or `partial` (29
   bundles). Those were historical claims, not rerun evidence. Their summary
   status became `not_run`; their query timing and failure records were left
   unchanged. This keeps truthful partial measurements as non-ranking
   capability evidence without promoting failed queries or inferring
   validation results. A future rerun may replace a `not_run` claim only when
   the validation phase records actual evidence.

   The alternative to admitting `not_run` was not "publish nothing for
   those 52 bundles" — it was "keep publishing them as `passed`/`partial`,
   which they were not." `_validate_validation_phase_consistency`
   (`bundle.py:684-711`) now rejects exactly that contradiction: a `passed`
   or `partial` summary claim paired with a `not_run` (or otherwise
   inconsistent) validation phase fails admission outright, on both paths.

   This is the same pattern as the 2026-08-24 withdrawal of 60 legacy
   DataFrame bundles that claimed `summary.validation=passed` after
   executing zero queries: a false clean claim is worse than an honest
   non-clean one, and the fix in both cases was to correct the label, not
   hide the evidence.

### What admitting `not_run` costs the corpus

Verified against the checked-out `results-data/bundles/` tree (184 bundle
JSONs, including the 28 `duckdb-version-matrix/` bundles, classified by
`platform.config.execution_mode`):

| | SQL-mode | DataFrame-mode |
| --- | --- | --- |
| `passed` | 135 | 2 |
| `partial` | 6 | 0 |
| `not_run` | 0 | 41 |

No SQL-mode bundle carries `not_run`; every `not_run` bundle is
DataFrame-mode. Disallowing `not_run` on the trusted mirror path would take
the DataFrame corpus from 43 bundles to 2:

- Pandas: 17 → 0
- Polars: 24 → 1
- PySpark: 2 → 1

Only TPC-H SF1 (one Polars bundle, one PySpark bundle, both already
`passed`) would keep DataFrame representation. AMPLab, ClickBench,
CoffeeShop, H2O-DB, JoinOrder, Read Primitives, SSB, TPC-DS, and TPC-H Skew
would lose all DataFrame representation.

### Why admission is safe

Admission is not the control that keeps `not_run` results out of ranked
comparisons — downstream exclusion is:

- `NON_CLEAN_VALIDATION_STATUSES` includes `"not_run"`
  (`benchbox/core/results/status.py:10-11`).
- The Results Explorer build pipeline's `is_ranking_eligible` gates on
  `not validation_status_is_non_clean(entry.validation_status)`.
- Its `ranking_exclusion_reason` returns `"validation_not_clean"` for any
  non-clean status, `not_run` included.
- The Compare view's exclusion catalog carries a matching
  `validation_not_clean` entry with a recovery hint
  (`results-explorer/src/lib/compareExclusionReasons.ts:119-125`):
  `"Choose a run with clean validation status."`
- Community submissions still require `summary.validation == "passed"`
  outright; only the trusted mirror path can admit `partial` or `not_run`,
  and only through the explicit `--allow-partial-validation` flag.

This was verified by tracing the code paths above.

## Decision

**Keep `PUBLIC_MIRROR_ALLOWED_VALIDATION_STATUSES = {"passed", "partial",
"not_run"}` on the trusted maintainer mirror path.** Admitting `not_run` is
a truthfulness tightening, not a quality loosening: it lets the corpus state
"this ran, but validation did not execute" instead of a false `passed` or
`partial` claim, while `_validate_validation_phase_consistency` blocks the
reverse (claiming validation that the phase record does not back). Ranking
and comparison eligibility, not admission, is the gate that keeps non-clean
results out of ranked/compared output, and that gate already treats
`not_run` as non-clean identically to `partial`, `failed`, `uncertain`, and
the other members of `NON_CLEAN_VALIDATION_STATUSES`.

Community submissions are unaffected: they still require `passed`
unconditionally.

## Open questions (not decided here)

**Whether to admit `uncertain`.** `uncertain` is not in
`PUBLIC_MIRROR_ALLOWED_VALIDATION_STATUSES` today. It is deferred pending
visual verification of the Explorer and is **not** decided by this ADR.
The tension worth recording: `uncertain` and `not_run` are both members of
`NON_CLEAN_VALIDATION_STATUSES` and both excluded from ranking by the same
`is_ranking_eligible` / `ranking_exclusion_reason` code — they differ only
at the admission gate. But `uncertain` means an oracle ran and reported
incomplete coverage; `not_run` means nothing was checked at all. `uncertain`
is strictly more evidence than `not_run`. The current allowlist admits the
weaker signal and rejects the stronger one. This asymmetry is not
resolved here; admitting `uncertain` is deferred.

**Whether `not_run` should be split.** The current taxonomy cannot
distinguish "no oracle exists for this benchmark/platform combination" from
"an oracle exists but validation was skipped for this run."
`UNVALIDATED_VALIDATION_STATUSES` (`status.py:25`, derived as
`NON_CLEAN_VALIDATION_STATUSES - CLI_FAILURE_VALIDATION_STATUSES`) separates
never-executed validation from CLI-level failures, but not *why* validation
did not run. A future taxonomy change may need to split `not_run`
accordingly; this ADR does not propose one.

## Consequences

**Positive**

- The corpus can carry an honest label for 43 DataFrame bundles (52 legacy
  bundles when `not_run` was admitted) that ran but were never validated,
  instead of a false `passed`/`partial` claim.
- `_validate_validation_phase_consistency` closes the matching hole:
  a bundle can no longer claim `passed`/`partial` while its own
  `phases.validation.status` says otherwise.
- Ranking and comparison correctness do not depend on the admission
  allowlist being narrow — they have their own independent, already-tested
  gate (`is_ranking_eligible`, `ranking_exclusion_reason`,
  `compareExclusionReasons.ts`).

**Negative**

- The published corpus's DataFrame coverage is mostly non-ranking capability
  evidence rather than validated measurement: 41 of 43 DataFrame bundles are
  `not_run`. A reader who does not know to check `summary.validation` (or
  who only reads bundle *counts*) could overstate DataFrame coverage.
- The admission allowlist and the ranking-exclusion set now diverge by
  design (`not_run` and `partial` are admitted but not ranking-eligible),
  which is a two-gate model a future contributor could conflate into one.
- A future rerun is the only way to move a `not_run` bundle to `passed`;
  the relabeling is one-directional and does not by
  itself improve DataFrame validation coverage.

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| Leave the 52 legacy bundles as `passed`/`partial` | Publishes a false validation claim; the exact defect class the 2026-08-24 zero-query withdrawal and this normalization both exist to remove. |
| Withdraw the 52 legacy bundles instead of relabeling | Loses truthful capability evidence (query timings, failure records) that the relabel preserves; several cohorts would have dropped below corpus depth floors with no replacement available at the time. |
| Narrow the mirror allowlist back to `{"passed", "partial"}` and exclude `not_run` bundles entirely | Re-admits the choice between a false claim and no coverage that this decision exists to avoid; downstream ranking already excludes `not_run`, so narrowing admission buys no additional ranking safety, only fewer honest capability records. |
| Also admit `uncertain` now | Deferred pending visual verification of the Explorer; recorded as an open question above rather than decided. |

## References

- [Rank subscale TPC-H and TPC-DS results as unofficial](adr-subscale-tpc-ranking-policy.md):
  the compliance-class rule that decides which admitted bundles rank.

- `benchbox/validation/bundle.py:88-94` (allowlist definitions and their
  comment), `:316-346` (`_validate_summary_section`), `:684-711`
  (`_validate_validation_phase_consistency`), `:715-741` (`_validate_bundle`)
- `scripts/validate_submission.py:301-360` (`--allow-partial-validation`
  wiring, community-CI comment)
- `benchbox/core/results/status.py:10-41` (`NON_CLEAN_VALIDATION_STATUSES`,
  `CLI_FAILURE_VALIDATION_STATUSES`, `UNVALIDATED_VALIDATION_STATUSES`,
  `validation_status_is_non_clean`)
- `results-explorer/src/lib/compareExclusionReasons.ts:119-125`
  (`validation_not_clean` Compare-view exclusion entry)
