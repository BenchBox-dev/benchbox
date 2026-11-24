# ADR Template: Extract `results-data/` to a Separate Repository

> **This is a template.** Copy to
> `docs/internal/adr/YYYY-MM-DD-results-data-extraction.md` and fill
> in when an extraction trigger fires (see
> [`_project/analysis/results-data-extraction-trigger.md`][trigger]).

[trigger]: https://github.com/BenchBox-dev/BenchBox/blob/develop/_project/analysis/results-data-extraction-trigger.md

## Status

_(Proposed | Accepted | Rejected | Superseded by …)_

## Date

_YYYY-MM-DD_

## Context

### Why this evaluation is happening now

_Which trigger fired? Quote the specific value(s):_

- `results-data/` size: _XXX_ MB (Q1 threshold 250 MB)
- PR volume to `published-results`, last 3 mo: _XX/XX/XX_ per month
  (Q2 threshold ≥ 20/mo for 3 mo)
- Qualitative reports: _link or quote_

### What lives in `results-data/` today

_Snapshot at evaluation time. Pull from `du -sh`,
`git ls-tree -r --long`, `corpus-inventory.json`._

- Total size: _XXX_ MB
- Bundle count: _XXX_
- Companion file count (.plans.json, .tuning.json): _XXX_
- Distinct platforms × benchmarks × scale factors: _XXX_

## Options Considered

### A. Stay in monorepo (status quo)

| Dimension                      | Outcome |
|-------------------------------|---------|
| CI workflows                   | Unchanged. `validate-submission.yml` and friends keep their current paths. |
| Contributor friction           | Unchanged. One PR per submission, one repo to clone. |
| SHA stability                  | Unchanged. Existing pins (in site-inputs bundles, in archived bundles) keep working. |
| Cross-repo SHA tracking        | N/A. |
| Operational cost               | Lowest. No migration; no cross-repo glue. |
| Recurring cost                 | Linear in repo size — clone time, fork time, fetch time. |

### B. Extract to `BenchBox-dev/benchbox-results` (full extraction)

| Dimension                      | Outcome |
|-------------------------------|---------|
| CI workflows                   | Must move or duplicate. `validate-submission.yml` lives where the data lives; `scripts/site_inputs.py` and the Explorer pipeline need cross-repo data fetch before dispatching to `BenchBox-dev/benchbox-site`. |
| Contributor friction           | One additional repo to clone, but each is smaller. PR flow now lands in the data repo, not the main repo. |
| SHA stability                  | Broken on extract day unless `git filter-repo --to-subdirectory-filter` is used carefully. Pre-existing pins fail. |
| Cross-repo SHA tracking        | Required. Core site-inputs assembly (and `BenchBox-dev/benchbox-site` corpus pins) must pin a `benchbox-results` commit. |
| Operational cost               | One-time migration: large. Reversible only with effort. |
| Recurring cost                 | Constant — main repo stays small regardless of corpus growth. |

### C. Extract with selective sync (data repo + lightweight pointer)

| Dimension                      | Outcome |
|-------------------------------|---------|
| CI workflows                   | Validation lives in the data repo; the main repo carries a thin pointer/sync. |
| Contributor friction           | Same as B for submitters; main-repo developers see only the pointer. |
| SHA stability                  | Same risk as B for content; the pointer commit in main is stable. |
| Cross-repo SHA tracking        | Pointer file (e.g., `RESULTS_DATA_SHA`) updated by automation. |
| Operational cost               | Highest — two repos plus the sync mechanism. |
| Recurring cost                 | Lowest if the sync works; highest if it bitrots. |

## Decision Matrix

Score each option 1-5 per dimension. Higher = better outcome for that
dimension. Justify each score with a one-line citation, not a vibe.

| Dimension                              | Weight | A. Monorepo | B. Full extract | C. Selective sync |
|---------------------------------------|--------|-------------|-----------------|-------------------|
| Day-1 contributor friction             | _W1_   |             |                 |                   |
| Long-term contributor friction         | _W2_   |             |                 |                   |
| CI surface area                        | _W3_   |             |                 |                   |
| SHA stability for downstream pinners   | _W4_   |             |                 |                   |
| Reversibility                          | _W5_   |             |                 |                   |
| Operational cost (run cost, not setup) | _W6_   |             |                 |                   |
| Setup cost (one-time)                  | _W7_   |             |                 |                   |
| Phase 3 readiness                      | _W8_   |             |                 |                   |
| **Weighted total**                     |        |             |                 |                   |

Suggested starting weights (normalize to 1.0): contributor friction
(W1+W2) ≈ 0.30; CI + ops (W3+W6) ≈ 0.25; reversibility (W5) ≈ 0.20;
SHA + setup (W4+W7) ≈ 0.15; Phase 3 (W8) ≈ 0.10. Adjust per situation.

## Recommendation

_State the chosen option, the score margin, and the one-line reason._

If A wins: this ADR closes with no migration. Re-evaluate at next
trigger fire.

If B or C wins: file a follow-up implementation TODO with:

- Migration plan (incremental, with checkpoints).
- History-preservation strategy. Default: `git filter-repo
  --path results-data/ --to-subdirectory-filter results-data/` from a
  fresh clone, then push to the new repo. Confirm SHAs match for
  recent commits before destroying the working clone.
- SHA stability mitigation. Identify everything that pins a
  `results-data/` SHA in the current repo (explorer? validation
  fixtures? historical bundle references?) and document the migration
  for each.
- Rollback procedure. Keep the source clone for ≥ 90 days. Do not
  delete `results-data/` from the main repo until the new repo has
  served at least one submission cycle.
- CI migration list. Every workflow that references
  `results-data/**` or `--path results-data/`, with the path it moves
  to.

## Decision

_(filled in at evaluation time, not in the template)_

## Consequences

_What the chosen option means for: explorer SPA, submission flow,
trust labels, Phase 3 hosted ingest, future maintainers. Be specific
about what gets harder, not just what gets easier._

## Rollback Path

_How to undo this decision if it proves wrong. Be honest about the
cost of rollback — it's almost certainly higher than the cost of the
extraction itself._
