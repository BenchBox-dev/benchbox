# ADR: Benchmark methodology policy

## Status

Accepted on 2026-11-17 for answers 1 to 12. The project owner confirmed the
answers that were provisional on 2026-11-16, replaced answer 7 with the
hardware facet and closed answers 11 and 12. Amended on 2026-11-23 to point
answer 1 to the 2026-11-19 vendor-terms owner decisions and to document the
all-source perf-smoke spread and clamped floor in answer 4 (the companion
update to the public methodology page in `BenchBox-dev/benchbox-site` is
pending as a separate site PR). The owner may amend or replace
any answer; an amendment edits this record and the
[methodology page](https://benchbox.dev/docs/concepts/benchmark-methodology.html)
together.

Amended on 2026-11-22: answers 2, 3 and 9 follow the
[corpus rule ADR](adr-corpus-derived-outcome-rule.md), which gives the owner
decisions and the rule. Answers 6, 8, 11 and 12 are unchanged.

## Context

The methodology page consolidates only policy that was already written down.
Twelve questions had no recorded answer, so the page left them out. Tools,
documentation and reviewers need an answer for each, or an explicit statement
that there is none.

The owner decided that each answer is derived from existing
repository practice rather than invented, and on 2026-11-16 that the answers
were provisional until confirmed; they were confirmed on 2026-11-17.
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

All answers are accepted.

| # | Question | Answer | Derived from | Enforced today |
|---|---|---|---|---|
| 1 | Vendor terms on publishing results | Governed by the separate vendor-terms record ([results-vendor-publication-terms.md](../results-vendor-publication-terms.md)) and the 2026-11-19 owner decisions (`_project/decisions/vendor-publication-terms-2026-11-19.md`, D1 to D6): submitters carry vendor conditions, provenance declarations and unsettled-vendor refusal are enforced per that record, and static result pages keep publishing every published result (owner decision 2026-11-13). | Owner decisions 2026-11-13, 2026-11-17, 2026-11-19 | Submit-time vendor declarations and unsettled-vendor refusal |
| 2 | Minimum iterations or warm-ups | A result presented as a measurement has one warm-up and at least three measured iterations, the CLI default. Runs with fewer than three measured iterations (`--iterations 1` or `2`) stay ranked where the corpus rule ranks them and are labelled "single iteration" (amended 2026-11-22; they were previously only "described as exploratory"). Advice of 5 to 10 iterations in the power-run guide stays advice. Baselines for regression gates are built from at least five separate runs. | CLI default; perf-smoke (3 measured after 1 warm-up); `MIN_SOURCE_RESULTS = 5` in the baseline script; at least three repetitions in the cross-benchmark ADR; owner decision 2026-11-22 | No minimum on submission |
| 3 | Aggregate across repeated runs and dispersion | Across separate runs, aggregate each query by the median. Within one run, the score is recomputed from the run's own timings by the corpus rule for every lane; a stored Power@Size is not an input (amended 2026-11-22; which iteration the timings come from is unchanged). The leaderboard shows the newest released version that has a ranked result, as the median of its runs; development and pre-release builds never stand for the platform, and all versions stay in a version view. Report dispersion as the minimum and maximum (spread) of the constituent runs and publish every constituent run; never select the fastest run. | `median_result` in the baseline script; the median rule and "cannot be selected from one unusually fast run" in the cross-benchmark ADR; the spread used to derive floors; owner decision 2026-11-22 | Power@Size final iteration only |
| 4 | Noise thresholds | A gate's noise floor is measured, not assumed: 1.25 times the largest per-query spread across at least five runs on one runner class, rounded up to a whole millisecond (the same rule for aggregates), recomputed whenever the baseline is refreshed. For per-query perf-smoke checks, `scripts/perf_smoke_baseline.py` always derives the largest spread across all supplied source runs (regardless of runner class, because a nightly run on any hosted CPU model is compared with the baseline), applies a flat 5 ms floor when that spread is at most 4.0 ms, and caps the computed floor at 15 ms (see [perf-smoke.md](../perf-smoke.md)). Provisional exception for the perf-smoke baseline: while no runner class has five results, the baseline takes the per-query median over at least five pooled results of any class (answer 3's aggregation), the aggregate floor also derives from every source, and the sources ledger records the rule used; the baseline is rebuilt from the slowest eligible class as soon as one class has five results. No policy for a maximum host load, a coefficient of variation or a confidence interval; `environment.host_load` is recorded at the start and end of a run for review and the integrity validator reports it informationally. The existing "investigate queries with more than three times the variance" advice stays advice. | perf-smoke Noise floor; `_check_host_load`; the data model page | The nightly gate floors (5 ms, 31 ms) |
| 5 | Cold against warm | Published and ranked results represent the warm state: measured iterations follow a warm-up pass, with result caching disabled where the platform has one. No result is labelled cold. A cold label is allowed only after a platform has a replayable, verified cold-start procedure; an uncontrolled run is never called cold. | Submission cache evidence check; cache-state rule in the cross-benchmark ADR | Result-cache disabling at submission |
| 6 | Seed cohorts | A seedless run (`qgen -d` default parameters) and a seeded run (`qgen -r`) are different parameter conventions, and different seeds are different workloads. Ranking and comparison cohorts are keyed on the recorded convention and seed (`config.query_parameters`, `config.seed`). Non-official runs need no seed; official mode requires one. Plan fingerprints are compared only at the same seed. | `run-official` seed requirement; CLI run reference; public contracts | Official mode only |
| 7 | Hardware and tuning in a cohort | `tuning_mode` is a cohort facet and matches exactly (existing rule). Hardware is an optional ranking facet, not a cohort key: the default ranking stays hardware-agnostic, every result discloses its environment, and the hardware class from the pinned CPU class table (see "Hardware key") filters a ranking where the class has at least three platforms. Managed services are one `managed` class with the size shown as an attribute; a result whose class cannot be determined is `unclassified` and is never guessed. A regression or throughput gate compares only runs from the same runner class (CPU model, and core count where the gate records it); the perf-smoke pooled-median baseline in answer 4 is the one provisional exception. | Owner decision 2026-11-17; tuning mode vocabulary ADR; results summary pages ADR (cohorts per benchmark, scale and phase); throughput gate per-class median; the perf-smoke runner-class caveat | Tuning mode and per-class gates; the hardware class in the read model |
| 8 | Non-standard scale of a non-TPC benchmark | The scale must be accepted by the benchmark. A run outside the benchmark's standard set is labelled not comparable with published results, is not submitted for ranking, and compares only with runs at the identical scale factor. This mirrors the exclusion of non-standard TPC runs from ranking. | Benchmarks overview note; subscale ranking ADR, decisions 1 and 7 | Scale range check; TPC classes only |
| 9 | Scale factor purpose | Smoke: 0.01. Regression: 0.01 for the nightly gate, otherwise the one scale the baseline was built at. Comparison: identical scale factor; TPC-H and TPC-DS at scale factor 1 or above avoid the subscale cohort. Publication and ranking differ (amended 2026-11-22; previously only an `official` run was published): a TPC-H or TPC-DS result at scale factor 1 or above in class `full_spec`, `partial`, `power_only` or `throughput_only` is admitted and ranked in its lane with the derived label, never described as an official TPC result; `nonstandard_scale` at a TPC benchmark is admitted on both paths and never ranked; `subscale` is maintainer-only and ranked only in the subscale cohort. Another benchmark's standard scale is published with its label. Where a benchmark page's size table disagrees, the page's accepted scale set decides what is allowed, and this table decides purpose. | Benchmark pages; perf-smoke; corpus rule | Compliance gate for TPC |
| 10 | Regression thresholds | A performance regression is a slowdown of more than 10% and also more than the measured absolute floor from answer 4. The throughput gate fails below 80% of the median of the latest 10 same-class records and reports only until it has five samples. New gates use these values unless their measurement shows otherwise, and record the derivation next to the baseline. | `benchbox compare --fail-on-regression`; the perf-smoke gate; `throughput_baseline.py` | Both nightly gates |
| 11 | Is the clause 4c wording sufficient | Yes. The owner accepted on 2026-11-17 that the "Derived from TPC-H. These results do not comply with the TPC-H Specification and must not be compared with official TPC-H results." wording, with "not an official TPC result" on published pages, meets clause 4c(3) of the TPC End User License Agreement, which reads "any performance results must be clearly identified as not being comparable to TPC Benchmark Results unless specifically authorized by TPC". This is the owner's decision; no agent concludes it. | Owner decision 2026-11-17; EULA text; trademarks page; licensing analysis (covers redistribution, not publication) | Labels only |
| 12 | Citing a result in third-party media | The subscale or derived label travels with the number, as the subscale ADR already requires on BenchBox surfaces. Third parties cite as `BenchBox <version>, result <public_result_id>, <permalink>, accessed <date>. Not an official TPC result.` The version is the BenchBox release that produced the result when the bundle records it; otherwise it is inferred as the latest release published on or before the run date (from the release headings in `CHANGELOG.md`, pinned in `benchbox/core/results/release_history.py`) and the line reads `BenchBox <version> (inferred from run date)`. The id is the `public_result_id`, the permalink is the result's page URL and the date is the day the citing party read it. | Owner decision 2026-11-17; subscale ADR "Fair use"; hosted results contract; results embeds | Result pages and summaries render the line |

## Hardware key

Answer 7 rests on a pinned, versioned CPU class table in
`benchbox/core/results/hardware_class.py` (`HARDWARE_TABLE_VERSION`). The
Explorer read model records the class, the score, the basis and the table
version for every result.

- **Score.** Per-thread factor times the logical CPUs the run could see
  (`cpu_count`, as `os.cpu_count()` reports it: a container CPU quota or
  cpuset below that count is not read, so a limited container is scored on
  the CPUs it can see), in
  "BenchBox CPU units" relative to one EPYC 7763 SMT thread. The unit is
  BenchBox's own; it is never a SPEC metric.
- **Factors.** x86: the median SPECrate2017_int_base per copy from
  SMT-enabled results, divided by 3.20. Azure-only parts use a retail
  stand-in (EPYC 9V74 to 9654 and 9634, EPYC 9V45 to 9655, Xeon 6973P-C to
  6972P, Xeon 8573C to 8580, Xeon 8370C to 8380) and are marked est. Apple
  Silicon: a bridge from Blender Open Data (CC0) Cycles runs, M4 at 2.27.
  Every row records its source, stand-in, retrieval date (2026-11-17) and est.
  flag. SPEC fair use applies: source and date, the SPEC trademark notice,
  no presentation as a SPEC metric, and disclosure that SPEC CPU2017 retires on
  2026-11-17. CPU2017 and CPU2026 numbers are never mixed.
- **Tiers.** Bands four times wide on a log scale with edges at 2.5, 10, 40,
  160 and 640: T0 below 2.5, T1 from 2.5 to 10, T2 from 10 to 40, T3 from 40
  to 160, and so on. A change to a factor makes a new table version; moving a
  CPU across an edge is a reviewed change listed in the changelog.
- **Missing data, in order.** (1) Model known, count missing: the tier is set
  only when every core count the model ships with lands in one tier (Apple M4
  at 8 or 10 cores is T2); the score stays empty. (2) A container records the
  host model when the launcher passes it in `BENCHBOX_CONTAINER_HOST_CPU_MODEL`
  (provenance `user_attested`); with a host vendor of Apple and no model, the
  M4 factor times the visible CPU count is used. (3) A bundle with no CPU model
  takes one only from a sidecar `cpu_attestation` (provenance `user_attested`);
  the 45 Darwin arm64 bundles with no CPU model carry the owner's attestation
  that they ran on the Apple M4 (10 cores). (4) Anything else is
  `unclassified` and is never guessed.
- **Managed services.** One `managed` class, never CPU-tiered. The server-side
  size (`platform.compute` size, warehouse size or edition) is shown as an
  attribute, not used as a key. The client's CPU is not recorded at the top
  level of the environment for a managed service; it stays under
  `environment.client_host`. A `remote_server` run is `unclassified` for the
  same reason: the recorded CPU is the client's.
- **Surface.** The default ranking of a cohort (benchmark, scale, phase, tuning
  mode and the other existing keys) stays hardware-agnostic. The class is an
  optional facet filter, offered only where the class has at least three
  platforms in the cohort, so a filtered ranking never falls below the
  three-identity minimum.

## Decided by the owner

The owner decided on 2026-11-17 that:

1. Answers 1 to 12 stand as written here, with 7 replaced by the hardware
   facet and 11 and 12 closed.
2. The "Derived from" wording meets EULA clause 4c(3). This is the owner's
   decision, not a legal opinion recorded by an agent.
3. Third parties cite a result in the form given in answer 12.

The 2026-11-22 amendment to answers 2, 3 and 9 is recorded in the
[corpus rule ADR](adr-corpus-derived-outcome-rule.md). The site methodology
page is amended by the site rendering change described there.

Any later amendment edits this record and the public methodology page
together.

## Consequences

- The methodology page states these answers as accepted policy and carries the
  hardware rule, the EULA answer and the citation format.
- Tools and documentation may cite these answers. Where "Enforced today" is
  not a mechanism, a tool must not reject on the answer until a change makes
  it a rule.
- The default ranking does not change, so no result changes rank. The
  Explorer shows each result's hardware class, and result pages and summaries
  carry the citation line.
- An amendment is a change to this record and the page, not a silent edit.
