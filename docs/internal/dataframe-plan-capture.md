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

The plans companion keeps one entry per query key: `Q#` when a query ran once, `Q#<stream>` when the stream disambiguates the executions, and `Q#<stream>:<test_type>` otherwise. An entry records the `iteration` and `run_type` of the execution it came from, and the loader attaches the plan, fingerprint and capture time only to that row. DataFrame rows carry `test_type` `power`, like the SQL power rows, so keys have the same shape on every platform; companions written before that change with keys ending in `:` (for example `Q1#0:`) still load.

Plan text is redacted at capture time, before any plan is parsed or fingerprinted. The loaded data directories and the working directory are replaced by `<redacted>` and the rest of the path is kept, so `/srv/d/tpch_sf1/lineitem.parquet` becomes `<redacted>/lineitem.parquet` and the parsers still derive table names from it. Paths under the home directory that are not under a loaded data directory, and paths that match the private-path detector, are replaced whole by `<redacted>`. An absolute path outside those roots that the detector does not match is not redacted, so check a companion with `find_public_path_leaks` and review any other absolute path before publishing it. Fingerprints of plans under a loaded data directory do not depend on the machine's paths; Polars text plans carry no fingerprint.

Accepted limit: the private-path detector matches only its known roots (`~`, `/Users`, `/home`, `/root`, `/private`, `/var/folders`, `/var/run`, `/Volumes`, `/tmp`, `/mnt`, `/opt`), Windows drive letters, UNC paths, the current home directory (`$HOME`, read the same way redaction reads it; `/`, empty and neutral placeholder homes are ignored) and any configured roots. An absolute path elsewhere, such as `/srv/...`, is flagged and redacted only when its directory is configured as a root. The known root list and the detector are deliberately not widened to every absolute path. The configured-roots mechanism is the `roots` argument of `find_public_path_leaks`, `anonymize_plans_payload` and `redact_plan_local_paths`: capture passes the loaded data directories, and `benchbox publish` passes the source bundle's directory. Add the directory that holds your data or outputs there when it lies outside the known roots. `anonymize_plans_payload` redacts top-level `errors` text the same way as plan text.

When the executions that share one companion entry captured different plan fingerprints, the entry also carries two optional fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `fingerprint_varied` | `true` | Present only when at least two distinct `plan_fingerprint` values were captured for that entry |
| `fingerprint_variants` | list of `{fingerprint, iteration, run_type}` | One item per distinct fingerprint, in capture order, naming the first execution that produced it; `iteration` and `run_type` are omitted when the row has none |

Both fields are absent when every execution produced the same fingerprint, and executions without a fingerprint (for example Polars text plans) are not counted. The stored `plan`, `fingerprint`, `iteration` and `run_type` always describe the last captured execution, so the variants list is the only record of the earlier plans' fingerprints; the earlier plan bodies are not stored.

Databricks DataFrame mode runs inside the plan-capture phase: while a measured workload is active it only records the result frame per query and stream, and the explain round trips run after measurement, for successful rows only, with the same accounting as SQL capture.

The capability table is `PlanCaptureSupport` in `benchbox/core/dataframe/profiling.py`.
