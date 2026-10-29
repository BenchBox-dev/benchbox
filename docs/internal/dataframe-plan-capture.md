# DataFrame plan capture contract

`--capture-plans` on a DataFrame platform records one plan per measurement row. What a row carries depends on the engine.

| Engine | Capability | Source | Row fields | Plans companion `plan_format` |
| --- | --- | --- | --- | --- |
| DataFusion | `dag` | `df.logical_plan()` text, parsed by the DataFusion parser | `query_plan` (`QueryPlanDAG`), `plan_fingerprint`, `plan_capture_time_ms` | `dag` |
| PySpark | `dag` | `df.explain(extended=True)` text, parsed by the Spark parser (`pyspark` resolves to it) | same as DataFusion | `dag` |
| LakeSail | `dag` | PySpark capture over Spark Connect, Spark parser | same as DataFusion | `dag` |
| Databricks (DataFrame mode) | `dag` | PySpark capture on the Databricks Connect result frame, Spark parser | same as DataFusion | `dag` |
| Polars | `raw_text` | `LazyFrame.explain()` optimized and unoptimized text | `query_plan` (raw `QueryPlan`), `plan_capture_time_ms`; no fingerprint | `text` |
| Pandas, Dask, cuDF | `unsupported` | none: eager engines build no plan | `plan_capture_error`; counted in `capture_failures` with reason text | none |

`plan_fingerprint_normalized` is added to DAG rows when `--normalize-plan-literals` is set.

Polars has no parser, so its plan stays raw text: no DAG, no fingerprint and no plan comparison. `benchbox show-plan` prints the stored text and `compare --include-plans` skips it. Strict plan capture raises `PlanCaptureError` with reason `unsupported` on Pandas, Dask and cuDF.

Capture runs after collection and its time is reported in `plan_capture_time_ms`, never in the measured execution time. SDK imports stay lazy: LakeSail and Databricks reuse the PySpark helper and import nothing from their client packages during capture.

When several measurement iterations of one query share a stream, the plans companion keys them `Q1#<stream>:<test_type>@<iteration>`; the loader re-attaches each plan by that key.

The capability table is `PlanCaptureSupport` in `benchbox/core/dataframe/profiling.py`.
