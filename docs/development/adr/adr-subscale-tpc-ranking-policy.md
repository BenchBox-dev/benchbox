# ADR: Rank Subscale TPC-H and TPC-DS Results as Unofficial

- Status: Accepted
- Date: 2026-10-16
- Constrains: `benchbox/core/results/subscale_policy.py`,
  `benchbox/core/results/builder.py` and `schema.py` (TPC metrics for
  subscale runs), `results-data/validate_corpus.py`, the Explorer pipeline
  (`_project/scripts/explorer_pipeline/models.py` and `transformer.py`), and
  the Explorer labels for subscale results.

## Context

TPC-H and TPC-DS define a minimum scale factor of 1. BenchBox runs below
SF 1 are recorded with `compliance_class: unofficial_subscale`. Until this
decision, three rules treated those runs inconsistently:

- New subscale runs stored `summary.tpc_metrics: {"suppressed": true}` and
  no Power@Size.
- `validate_corpus.py` and the Explorer excluded every subscale bundle from
  ranking with `unofficial_compliance`.
- Bundles written before compliance classes existed carry no class. They
  kept their Power@Size and stayed ranked, so 18 legacy SQL bundles at
  SF 0.01 and SF 0.1 were ranked while newer runs of the same engines were
  not.

Small scales are where most local runs and the seed corpus live, so the
exclusion left sub-SF1 TPC cohorts thin or empty. TPC-DS SF 0.1 published no
ranking at all.

## Decision

1. Subscale TPC-H and TPC-DS results stay ranked by Power@Size. One policy,
   keyed on `unofficial_subscale`, covers both benchmarks.
   `RANKING_EXCLUDED_COMPLIANCE_CLASSES` is `{"unofficial_nonstandard"}`;
   non-standard runs at SF 1 and above remain unranked.
2. New subscale runs record `summary.tpc_metrics.power_at_size` (and
   `throughput_at_size` when present) with the usual formula, plus
   `"unofficial": true, "reason": "compliance_class=unofficial_subscale"`.
   Non-standard runs keep `{"suppressed": true}`.
3. QphH@Size stays unavailable for subscale runs. `metrics_refusal_reason`,
   `benchbox submit` and `benchbox publish` still refuse them, so no
   official TPC metric can be produced from a subscale bundle.
4. A TPC-H or TPC-DS bundle below SF 1 is read as `unofficial_subscale`
   whatever class it records and however its benchmark id is spelled
   (`tpch`, `TPC-H`, `tpc_ds`), so a missing or incorrect class cannot avoid
   the label. This keeps the 18 legacy bundles, which record no class,
   ranked and labelled. Stored bundle bytes are not changed.
5. Derived and recorded Power@Size share one cohort ranking. A recorded
   subscale value is used only when it agrees within 5% with the value
   recomputed from the bundle's own final-iteration timings; otherwise the
   bundle has no primary metric and is not ranked. A recorded value that
   cannot be checked, because the final iteration is incomplete or lacks a
   usable timing, is not used either. Bundles at SF 1 and above keep their
   recorded values unchecked, as before. On the 25 published subscale
   bundles that store a value, every value can be checked and the largest
   difference is 1.4%.
6. Eleven TPC-H and three TPC-DS subscale bundles were written with
   suppressed metrics and no Power@Size. The read path derives Power@Size
   from their final measurement iteration: `SF × 3600 ÷ geomean(seconds)`,
   the same formula the result builder uses. The final iteration must cover
   every query (22 for TPC-H, 99 for TPC-DS), each with a passing status and
   a positive timing.
   The read model marks these values
   `power_score_source = "derived_from_timings"`, and the Explorer
   shows them as derived. Exported timings are rounded, so a derived value
   can differ slightly from what the run would have stored: about 0.1% or
   less on the legacy bundles that store both, with one Polars bundle at
   1.4%.
7. Every surface that shows a subscale result labels it
   "Subscale · Unofficial". Cohort headers add: "SF<1 is below TPC-H's
   minimum scale. Power@Size uses the TPC formula for comparison within this
   cohort only; not a TPC result." (TPC-DS wording is the same.) Charts title
   the score "Power@Size (subscale, unofficial)", and exports carry
   `compliance_class` with a caption.

## Fair use

TPC policy treats unaudited results as not comparable with published TPC
results, especially when they use TPC metric names at scales below the
minimum. The persistent Unofficial labels, the cohort disclaimer, QphH being
off and the refused official submit path reduce the risk that a subscale
Power@Size is read as a TPC result. They do not remove it: a screenshot or a
quoted number can lose its label.

## Consequences

- Sub-SF1 TPC-H and TPC-DS cohorts gain the newer duckdb, DataFusion and
  cloud-warehouse runs. TPC-DS SF 0.1 publishes a ranking for the first time.
- The Explorer read model moves to version 17 for the new
  `power_score_source` column on `results`, `benchmark_rankings` and the
  `result_detail_metrics` and `platform_index_rows` views.
- `validate_corpus.py` loads `subscale_policy.py` directly, so the corpus
  gate and the Explorer apply the same class derivation, exclusion set and
  Power@Size derivation.
- Seed-corpus staging inherits the change through `exclusion_reason`: new
  subscale bundles are staged instead of skipped.

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| Keep excluding subscale runs and de-rank the 18 legacy bundles | Removes most small-scale comparison from the Explorer, which is where most runs are. |
| Rank subscale runs on geomean latency instead of Power@Size | Breaks comparability with the legacy rows and with SF 1 and above, and still needs a label. |
| Rewrite the 14 suppressed bundles with a stored Power@Size | Changes signed, published bundle bytes; read-time derivation gives the same number and marks it as derived. |
| Rank non-standard runs too | Not requested; a non-standard scale at SF 1 or above is a different methodology, not a smaller one. |

## References

- `benchbox/core/results/subscale_policy.py` (class derivation, exclusion
  set, Power@Size derivation)
- `benchbox/core/results/metrics.py` (`metrics_refusal_reason`)
- `benchbox/validation/bundle.py` (`CLI_REFUSED_COMPLIANCE_CLASSES`; the opt-in
  `trust_internal_source` exempts bundles whose manifest sidecar declares
  `result_source: internal`, and the submission CI never sets it)
- [Corpus validation-status admission policy](adr-corpus-validation-admission-policy.md)
