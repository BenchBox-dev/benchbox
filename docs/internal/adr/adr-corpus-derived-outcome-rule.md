# ADR: One derived admission and ranking rule for the results corpus

## Status

Accepted on 2026-11-22. The owner decisions below were given by the project
owner on that date. The planner decisions are technical choices that follow
from them and remain open to challenge in review. This record supersedes,
extends or amends the decisions named under "Records changed by this ADR".

## Context

Whether a result is accepted, which ranking it enters, which score it shows
and how it is labelled depends today on what the writer recorded rather than
on facts in the result file. Five faults follow from that:

- **T1. Outcome keyed on what the writer recorded.** Two fields decide it:
  `benchmark.compliance_class`, and `summary.tpc_metrics` (the writer omits
  Power@Size when it stamps a run non-standard). Three DuckDB TPC-H SF1
  query-only results with the same facts can end up with one ranked and two
  excluded.
- **T2. Fact combinations with no outcome.** A query-only or throughput-only
  run cannot be official, and no rule said where it goes; the ranking lanes
  still hold older results of those shapes.
- **T3. Many implementations of the rule.** About forty readers across core,
  CI, the mirror branch and the site's TypeScript apply the rule, and they
  disagree, including four different identity rules.
- **T4. Evidence not in the result file.** Row-count references are missing
  above small scales, the stream count is read from a configured value, and
  the engine that ran is not recorded.
- **T5. Labels that do not match the fact.** `passed`, `not_validated` and
  `uncertain` are used for the same fact; embeds carry no class label; one
  label covers a default engine that changed.

The rule below replaces every one of those readers with one function of facts
in the result file. The writer calls the same function and keeps writing the
existing `compliance_class` vocabulary as information only.

## Decision

### The rule (facts -> class | public | maintainer | lane | score)

- TPC-H/TPC-DS, standard scale, power + throughput + refresh + stream minimum -> full_spec | yes | yes | combined | QphH/QphDS and Power@Size
- same, power + throughput, no refresh or below stream minimum -> partial | yes | yes | both the power-only and the throughput cohort | from timings
- same, power + refresh, no throughput -> partial | yes | yes | power-only | Power@Size from query timings only (refresh excluded)
- same, power only -> power_only | yes | yes | power-only | Power@Size from timings
- same, throughput only -> throughput_only | yes | yes | throughput | from timings
- TPC-H/TPC-DS below scale 1 -> subscale | no | yes | subscale cohort | Power@Size from timings
- any benchmark, non-standard scale -> nonstandard_scale | yes | yes | none | timings only
- other benchmark, standard scale -> none | yes | yes | its own | its own
- no timed phase (load-only, data-only) -> not a result: not staged, not stored
- TPC-DI -> decided by the TPC-DI admission rule (tpcdi-* items); this rule calls it and does not restate it
- gateway-routed, known defect, failed validation -> unchanged from today
Every ranked TPC row carries "Derived from TPC-H/TPC-DS ... not an official TPC result" plus the class in plain words. Vendor terms, anonymization, tuning and timing coverage stay separate admission checks behind the same entry point; they are not class inputs.

The columns are: the class the facts give, whether the public path (submit,
publish) accepts the result, whether the maintainer path accepts it, the
ranking lane it enters, and the score shown. `nonstandard_scale` and `none`
are class values the rule returns on read; this ADR adds no new stored class
value, and no reader decides anything from `benchmark.compliance_class`,
`conformance_class` or `summary.tpc_metrics.suppressed`.

### The power-only lane

A TPC-H or TPC-DS result at a standard scale with a power phase and no
complete throughput-plus-refresh evidence is not official, and is ranked in a
labelled lane instead of being dropped:

- `power_only` (power phase only) enters the power-only lane with Power@Size
  recomputed from its timings.
- `partial` with power and refresh but no throughput enters the power-only
  lane with Power@Size from the query timings only; the refresh phase is
  excluded from the score.
- `partial` with power and throughput but no refresh, or fewer streams than
  the stream minimum, enters both the power-only lane and the throughput
  cohort, each scored from timings.
- `throughput_only` enters the throughput cohort, scored from timings.
- `full_spec` is the combined lane, with QphH or QphDS and Power@Size.

Every ranked TPC row carries the derived-from wording and the class in plain
words.

### Owner decisions

Quoted as recorded on 2026-11-22 (dialog answers unless quoted):

1. "rank them with a label": query-only and partial TPC results at a standard scale are ranked in a labelled lane.
2. Public path (submit, publish) accepts them the same as the maintainer path. Subscale stays refused on the public path.
3. Cohort minimum: keep the recorded rule (engine settings and container runs count; versions of one platform do not).
4. Leaderboard shows the newest released version that has a ranked result, as the median of its runs. Development and pre-release builds never stand for the platform. All versions stay in a version view.
5. Results with no per-query row-count evidence stay ranked, labelled "row counts not checked". References are added first for TPC-H, TPC-DS and SSB at the scales that rank.
6. Runs with fewer than three measured iterations are labelled "single iteration" and stay ranked.
7. Benchmarks built with TPC tools (TPC-H Skew, TPC-Havoc, Data Vault, TPC-DS OBT) get the derived label too.
8. "Open local result" keeps showing what the file says, marked "as recorded by the BenchBox version that wrote it, not checked".

The owner also decided on the same date that the public path accepts and
ranks a submission with no row-count reference available, labelled "row counts
not checked".

### Planner decisions that shape the rule

Technical choices, open to challenge in review:

- The class is derived on read. The writer calls the same function and keeps writing the existing compliance_class vocabulary as information only.
- The score is recomputed from timings for every lane (R: reproduces all 33 ranked TPC power results within 0.1%).
- Stream count comes from throughput stream results, not run.streams.
- Seed is not a class input. Cohorts keyed on seed convention are out of scope (77 of 116 TPC results record none) (R).
- The site derives nothing; the pipeline writes class, lane, label, score and identity into the read model. A read-model version bump lands in the site first.
- A row-count reference above the scales the TPC answer sets cover is agreement with a named reference engine and is labelled as that.
- The variant label keeps coming from the requested engine (execution-variants ADR D8). The observed engine is a separate attribute.

The "(R)" counts were measured by an independent reviewer on 2026-11-22 and
are recorded here as measured then, not as standing facts.

### Guardrails

- No rule, exception, label or test names a platform or a version matrix.
- A stored result file is never edited to change its outcome; outcomes change
  only through the rule.
- The vendored rule module (see the slim-branch ADR) is stdlib-only, so the
  slim `published-results` branch runs the same rule as `develop`.

## Licence

The owner decided to rank at standard scale: "rank them with a label": query-only
and partial TPC results at a standard scale are ranked in a labelled lane
(owner decision 1), and the public path accepts them the same as the
maintainer path (owner decision 2).

This ADR does not conclude whether that is permitted. Open risks:

- **Open risk: fair-use coverage.** The fair-use section of the subscale ADR
  covers Power@Size at scales below the TPC minimum, where persistent
  Unofficial labels, QphH being off and a refused official submit path reduce
  the risk that a number is read as a TPC result. It does not cover ranking
  at a standard scale, which the owner decided; that is not a conclusion that
  it is covered or permitted.
- **Open risk: EULA clause 7.** Clause 7 of the TPC End User License Agreement
  (use of TPC names) has not been assessed against the "Derived from
  TPC-H/TPC-DS" wording or against the class names this rule shows.

Methodology answer 11 records the owner's acceptance that the "Derived from"
wording meets clause 4c(3). That acceptance is unchanged and does not cover
either risk above. Neither risk is a conclusion of this record.

## Records changed by this ADR

The subscale ADR (`adr-subscale-tpc-ranking-policy.md`) lives in the site
repository. Its decisions are numbered as in that record.

| Record | Decision | Effect | Replacement |
|---|---|---|---|
| Subscale ADR | 1 (rank subscale by Power@Size; exclusion set `{"unofficial_nonstandard"}`) | **Superseded** | The rule above. Subscale is the class `subscale` with its own cohort and a score from timings. Ranking exclusion is decided by the derived class and lane (`nonstandard_scale` has lane none), not by a stored-class exclusion set. |
| Subscale ADR | 5 (recorded value used only if within 5% of the recomputed value) | **Superseded** | The score is recomputed from timings for every lane; a recorded Power@Size is not an input. |
| Subscale ADR | 2 (writer records `power_at_size` and `unofficial`; non-standard suppressed) | Extended | The writer calls the rule function and still writes the existing vocabulary, as information only. No reader decides from it or from `suppressed`. |
| Subscale ADR | 3 (QphH unavailable; submit and publish refuse subscale) | Extended | Subscale stays refused on the public path. Standard-scale `partial`, `power_only` and `throughput_only` results are accepted there, with no official metric. |
| Subscale ADR | 4 (read below-SF1 TPC-H/TPC-DS as subscale whatever class is recorded) | Extended | Every class is derived from facts in the file, not only the subscale class, with the same benchmark-id normalization. |
| Subscale ADR | 6 (derive Power@Size from timings for suppressed bundles) | Extended | Derivation from timings applies to every lane, not only the suppressed subscale bundles. |
| Subscale ADR | 7 (labels) | Extended | Every ranked TPC row carries the derived-from wording and the class in plain words; the subscale label and cohort header stay. |
| Methodology ADR | Answer 2 (iterations) | Amended | Runs with fewer than three measured iterations are ranked, labelled "single iteration" (owner decision 6), not only "described as exploratory". |
| Methodology ADR | Answer 3 (aggregation) | Amended | The score is recomputed from timings for every lane; the leaderboard shows the median of the runs of the newest released version with a ranked result (owner decision 4). |
| Methodology ADR | Answer 9 (publication scale purpose) | Amended | Publication of a TPC-H or TPC-DS result at scale factor 1 or above is no longer limited to `official` runs; any class in the rule table that the public path accepts is published and ranked in its lane, labelled. |
| Methodology ADR | Answers 6, 8, 11, 12 | Kept | Unchanged. Answer 8 (non-standard scale of a non-TPC benchmark) matches the `nonstandard_scale` row: accepted and compared only at identical scale, in no ranking lane. |
| Slim-branch ADR | Vendored runtime | Amended | One more vendored file set: the stdlib-only rule module and its data file. |
| Execution-variants ADR | D4 | Kept | Unchanged. |
| Execution-variants ADR | D8 | Label rule kept | The variant label still comes from the requested engine. The identity amendment to include the comparison version is a separate change. |

### Site copy and methodology page

The site copy of the subscale ADR (decisions 1 and 5 superseded; 2 to 4, 6
and 7 extended) and the site methodology page are amended by the site
rendering change that follows this record. That change makes the site render
what the pipeline writes into the read model: class, lane, label, score and
identity. The site no longer derives a class, a lane or a score from stored
fields. A read-model version bump lands in the site first, so the site accepts
the new fields before the pipeline emits them. The site then replaces its
subscale label and cohort-header logic with the pipeline's label, adds the
derived-from wording and class to every ranked TPC row, and rewords the
methodology page's answers 2, 3 and 9 to match the amendments above. Until it
lands, the site copy of the subscale ADR and the site methodology page still
describe the superseded decisions.

## Consequences

- Query-only, power-only and partial TPC results at a standard scale, which
  were stamped non-standard or excluded, enter a labelled lane. Rankings that
  these results join will change; every change to a stored result's outcome
  must appear in the committed outcome snapshot diff of the change that
  causes it.
- The corpus carries one outcome per result file, the same on `develop`, on
  `published-results` and in the site.
- Row counts, iteration count and the TPC-tool derivation each get a label
  rather than an exclusion: "row counts not checked", "single iteration" and
  the derived label.
- Licence coverage for ranking at standard scale is an open question for the
  owner. Nothing in this record answers it.
