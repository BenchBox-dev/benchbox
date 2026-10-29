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

Expression-family engines (Polars, DataFusion, PySpark, LakeSail) capture after the query is collected, so planning work that the engine caches on the frame (Spark analysis and optimization) is paid by the measured collect and not moved into `plan_capture_time_ms`. Capture time is reported in `plan_capture_time_ms` and subtracted from the measured execution time. Strict capture therefore raises after one execution of the query. SDK imports stay lazy: LakeSail and Databricks reuse the PySpark helper and import nothing from their client packages during capture.

The plans companion keeps one entry per `Q#<stream>:<test_type>`; the loader attaches it to every row of that key, including DataFrame rows whose `test_type` is empty.

The capability table is `PlanCaptureSupport` in `benchbox/core/dataframe/profiling.py`.
