# ADR: Provisional benchmark methodology policy

## Status

Accepted as provisional policy on 2026-11-16. The project owner may amend or
replace any answer; an amendment edits this record and the
[methodology page](https://benchbox.dev/docs/concepts/benchmark-methodology.html)
together. Two questions stay open for the owner (clause 4c wording and the
citation format) and are marked as such.

## Context

The methodology page consolidates only policy that was already written down.
Twelve questions had no recorded answer, so the page left them out. Tools,
documentation and reviewers need an answer for each, or an explicit statement
that there is none.

The owner decided that each answer is derived from existing
repository practice rather than invented, and that the answers are provisional.
Vendor terms for publishing results are a separate record:
[results-vendor-publication-terms.md](../results-vendor-publication-terms.md).

Sources used:

- [perf-smoke.md](../perf-smoke.md) and
  [nightly-perf-gate-review.md](../nightly-perf-gate-review.md): the nightly
  gate, its floors and how they were derived.
- `scripts/perf_smoke_baseline.py`: baseline construction.
- `tests/uat/throughput_baseline.py`: the throughput gate.
- `benchbox/core/tpc_patterns.py` and `benchbox/core/tpch/compliance.py`:
  compliance classes.
- The site's subscale ranking ADR, results summary pages ADR and
  cross-benchmark score ADR (the last is Proposed).
- [tpc-licensing-analysis.md](../../tpc-licensing-analysis.md) and
  `_sources/tpc-h/EULA.txt`.

"Enforced today" in the table below says whether code or CI already applies
the rule. A rule that is not enforced is policy for documentation and review
only; no tool may reject on it until a separate change implements it.

## Decision

All answers are provisional.

| # | Question | Provisional answer | Derived from | Enforced today |
|---|---|---|---|---|
| 1 | Vendor terms on publishing results | Handled in the vendor terms record; texts only, no conclusion. Static result pages keep publishing every published result (owner decision 2026-11-13). | Owner decision | The submit-time vendor checks only |
| 2 | Minimum iterations or warm-ups | A result presented as a measurement has one warm-up and at least three measured iterations, the CLI default. Runs with fewer measured iterations (`--iterations 1` or `2`) are exploratory and are described as such. Advice of 5 to 10 iterations in the power-run guide stays advice. Baselines for regression gates are built from at least five separate runs. | CLI default; perf-smoke (3 measured after 1 warm-up); `MIN_SOURCE_RESULTS = 5` in the baseline script; at least three repetitions in the cross-benchmark ADR | No minimum on submission |
| 3 | Aggregate across repeated runs and dispersion | Across separate runs, aggregate each query by the median. Within one run, Power@Size keeps using the final iteration. Report dispersion as the minimum and maximum (spread) of the constituent runs and publish every constituent run; never select the fastest run. | `median_result` in the baseline script; the median rule and "cannot be selected from one unusually fast run" in the cross-benchmark ADR; the spread used to derive floors | Power@Size final iteration only |
| 4 | Noise thresholds | A gate's noise floor is measured, not assumed: 1.25 times the largest per-query spread across at least five runs on one runner class, rounded up to a whole millisecond (the same rule for aggregates), recomputed whenever the baseline is refreshed. No policy for a maximum host load, a coefficient of variation or a confidence interval; `environment.host_load` is recorded at the start and end of a run for review and the integrity validator reports it informationally. The existing "investigate queries with more than three times the variance" advice stays advice. | perf-smoke Noise floor; `_check_host_load`; the data model page | The nightly gate floors (7 ms, 82 ms) |
| 5 | Cold against warm | Published and ranked results represent the warm state: measured iterations follow a warm-up pass, with result caching disabled where the platform has one. No result is labelled cold. A cold label is allowed only after a platform has a replayable, verified cold-start procedure; an uncontrolled run is never called cold. | Submission cache evidence check; cache-state rule in the cross-benchmark ADR | Result-cache disabling at submission |
| 6 | Seed cohorts | A seedless run (`qgen -d` default parameters) and a seeded run (`qgen -r`) are different parameter conventions, and different seeds are different workloads. Ranking and comparison cohorts are keyed on the recorded convention and seed (`config.query_parameters`, `config.seed`). Non-official runs need no seed; official mode requires one. Plan fingerprints are compared only at the same seed. | `run-official` seed requirement; CLI run reference; public contracts | Official mode only |
| 7 | Hardware and tuning in a cohort | `tuning_mode` is a cohort facet and matches exactly (existing rule). Hardware is not a ranking facet today and is not made one: every result discloses its environment, and a comparison that crosses hardware says so. A regression or throughput gate compares only runs from the same runner class (CPU model, and core count where the gate records it). | Tuning mode vocabulary ADR; results summary pages ADR (cohorts per benchmark, scale and phase); throughput gate per-class median; the perf-smoke runner-class caveat | Tuning mode and per-class gates |
| 8 | Non-standard scale of a non-TPC benchmark | The scale must be accepted by the benchmark. A run outside the benchmark's standard set is labelled not comparable with published results, is not submitted for ranking, and compares only with runs at the identical scale factor. This mirrors the exclusion of non-standard TPC runs from ranking. | Benchmarks overview note; subscale ranking ADR, decisions 1 and 7 | Scale range check; TPC classes only |
| 9 | Scale factor purpose | Smoke: 0.01. Regression: 0.01 for the nightly gate, otherwise the one scale the baseline was built at. Comparison: identical scale factor; TPC-H and TPC-DS at scale factor 1 or above avoid the subscale cohort. Publication: a TPC-H or TPC-DS `official` run at scale factor 1 or above, or another benchmark's standard scale with its label. Where a benchmark page's size table disagrees, the page's accepted scale set decides what is allowed, and this table decides purpose. | Benchmark pages; perf-smoke; compliance classes | Compliance gate for TPC |
| 10 | Regression thresholds | A performance regression is a slowdown of more than 10% and also more than the measured absolute floor from answer 4. The throughput gate fails below 80% of the median of the latest 10 same-class records and reports only until it has five samples. New gates use these values unless their measurement shows otherwise, and record the derivation next to the baseline. | `benchbox compare --fail-on-regression`; the perf-smoke gate; `throughput_baseline.py` | Both nightly gates |
| 11 | Is the clause 4c wording sufficient | Open for the owner. No agent concludes it. The current practice is the "Derived from TPC-H. These results do not comply with the TPC-H Specification and must not be compared with official TPC-H results." wording and "not an official TPC result" on published pages; the owner decides whether that meets clause 4c(3) of the TPC End User License Agreement, which reads "any performance results must be clearly identified as not being comparable to TPC Benchmark Results unless specifically authorized by TPC". | EULA text; trademarks page; licensing analysis (covers redistribution, not publication) | Labels only |
| 12 | Citing a result in third-party media | The subscale or derived label travels with the number, as the subscale ADR already requires on BenchBox surfaces. Cite by `public_result_id` with the snapshot hash and date that `results:cite` prints. The exact citation format a third party should use is open for the owner. | Subscale ADR "Fair use"; hosted results contract; results embeds | Embeds only |

## Open for the owner

1. Whether the derived-from wording satisfies EULA clause 4c(3) or needs
   change, and whether to seek TPC authorization.
2. The citation format for third parties (wording, which fields, link form).
3. Any amendment to a provisional answer above, in particular the minimum of
   three measured iterations, the choice not to make hardware a ranking facet
   and the warm-only statement.

## Consequences

- The methodology page lists these answers as provisional policy with their
  source and replaces its open-questions section.
- Tools and documentation may cite these answers. Where "Enforced today" is
  not a mechanism, a tool must not reject on the answer until a change makes
  it a rule.
- An amendment is a change to this record and the page, not a silent edit.
