# ADR: JoinOrder Small Workload Accessibility

Date: 2026-05-12

Status: No public small scale in Step 1; revisit through separate JOB-light design.
*(Note 2026-11-23: `benchbox/core/joinorder/dataframe_queries.py` has since
implemented all 113 canonical query IDs, superseding the 13-of-113 partial
DataFrame note in Consequences below.)*

## Question

Should public `joinorder` expose a reachable small workload for quick laptop,
CI, or metered-egress iteration after the canonical IMDb 2013 cutover?

## Decision

Keep public `joinorder` at `--scale 1` only for now. Do not promote the
`joinorder_canonical_tiny` fixture or build a new `JOB-light` archive as a
public selectable scale in this step.

`joinorder_synthetic` remains the internal uncorrelated smoke-test benchmark.
It is not a small JOB substitute.

## Options Considered

| Option | Outcome | Rationale |
|---|---|---|
| Build JOB-light | Deferred | A useful correlated subset needs a deterministic transitive-closure builder, its own reference cardinalities, data-delivery path, and comparability labeling. That belongs in a separate Track-2 design, not the Step-1 canonical cutover. |
| Promote the tiny fixture | Rejected for now | The fixture is useful CI evidence, but it is smoke-only, not measurement-grade JOB. Making it public would create a second user-facing IMDb-derived data contract without solving comparability. |
| Document no small path | Selected | This keeps the public benchmark contract honest: canonical `joinorder` means full IMDb 2013 JOB at scale 1. Users who need lightweight unrelated smoke coverage can use the internal synthetic benchmark, but those results are not comparable to canonical JOB. |

## Consequences

- `BENCHMARK_METADATA["joinorder"]["scale_options"]` stays `[1.0]`.
- `JoinOrderBenchmark(scale_factor != 1.0)` continues to fail fast.
- No new packaged Parquet fixture, archive, manifest, or reference cardinality
  file is added.
- Docs must state that there is currently no small comparable JOB workload.
- Partial DataFrame runs still need explicit comparability metadata because
  they execute 13 of the 113 canonical query IDs.

## Follow-Up

A future small JOB design should be handled as separate Track-2 work after the
canonical scale-1 path has stabilized. Revisit either a measurement-grade
`JOB-light` subset or a smoke-only user-visible fixture with a clear
`unofficial_subscale`/unranked result contract.
