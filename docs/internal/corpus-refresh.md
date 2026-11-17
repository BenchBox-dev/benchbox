# Public corpus refresh

The public corpus on `published-results` goes stale when nobody *submits*,
not when CI stops running. Nightly tests do not publish bundles. This page
is the publication calendar.

## Cadence

Monthly, on the 1st at 07:00 UTC, via `.github/workflows/seed-corpus.yml`
(`schedule` plus the existing `workflow_dispatch`). Each run opens a PR
against `develop`. Merging that PR triggers
`sync-results-data-to-published.yml`, which opens a draft mirror onto
`published-results` for a maintainer to accept.

Do not add a second nightly UAT gate for this. Generation already lives in
`seed-corpus.yml`; the schedule is what was missing.

## Recurring matrix

The workflow is the canonical matrix. It uses only local DuckDB, DataFusion,
and Polars DataFrame identities. Cloud warehouses are not on this calendar.

| Benchmark | Scales | Platforms | Notes |
| --- | --- | --- | --- |
| TPC-H | 0.01, 0.1 | DuckDB, DataFusion, Polars-DF | Power phase only |
| SSB | 0.01, 0.1 | DuckDB, DataFusion, Polars-DF | Power phase only |

The TPC-H SF 1 and TPC-DS SF 1 official cohorts are not regenerated. They stay
frozen at their published bundles until DataFusion and ClickHouse Local support
full-spec runs (throughput plus refresh or maintenance), then return as
full-spec cells.

TPC-H SF10 / SF100 and extra TPC-DS platforms stay operator-run (too large
for a GitHub-hosted monthly job). They still publish through
`benchbox submit` onto `published-results`, as in PR #1786. Those direct
operator submissions remain archive-only unless a separate reviewed PR
promotes them into `develop`'s curated release-preview corpus.

## Operator trigger

```bash
gh workflow run seed-corpus.yml
```

To seed one benchmark only, add `-f benchmark=tpch`, replacing `tpch` with the benchmark name.

After the develop PR merges, confirm the mirror draft against
`published-results` and merge it. `corpus-drift-check.yml` remains the
loud canary if a push-triggered mirror is dropped.

The mirror workflow captures the accepted `published-results` commit before it
builds its union overlay and compares that ref again before pushing the draft
branch. If the accepted branch moved during the run, the workflow reports both
SHAs, opens no stale mirror PR, and must be rerun after the accepted
publication settles. This is a freshness/reconciliation check; it does not
change the immutable snapshot used to reproduce an existing ledger seed.

## Freshness ownership

Freshness — whether the accepted corpus moved and when the ledger seed
must be regenerated — belongs to this flow, not to the ledger. The seed
(`publication/ledger-seed.json`) records the exact snapshot SHA it
validated (`source` plus `source_resolved_at/from` provenance). Ledger
interfaces never fetch, resolve, or reconcile freshness: `materialize`
reads bytes from the recorded SHA, and reproduction/validation passes
`--expect-source` with the recorded SHA so a moved mirror fails with an
actionable message instead of silently switching inputs.

Regenerating the seed against a new source SHA is a deliberate, reviewed
act (regenerate, inspect the union diff, commit). The cutover workflow's
regen-and-diff step is the loud detector: it regenerates from the live
ref, and any content drift against the committed seed means the mirror
moved and freshness must be revalidated through this flow. Generation
without `--expect-source` exists only for that detection step.
