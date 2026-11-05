---
title: TPC Measured Intervals
status: reference
owner: tpc-measured-intervals-reference-20261103
created: 2026-11-05
---

# TPC Measured Intervals

Every item that moves a timer boundary must update its row in this page in the same PR.

This page lists every site in `benchbox/core/tpch`, `tpcds`, `tpcdi` and `throughput` that starts or stops a measured interval, the specification clause that governs it, what sits inside and outside the interval, and which tracker item owns any gap. It describes the code as it is on this branch. Sites are named by file and symbol; look the symbol up instead of trusting a line number.

Specifications read for this page (paraphrased, never copied): TPC-H 3.0.1, TPC-DS 4.0.0, TPC-DI 1.1.0. Clause numbers were read from the published PDFs.

## Clauses

| Clause | What it says (paraphrase) |
|---|---|
| TPC-H 3.0.1 2.5.3.1 | RF1/RF2 may be pre-processed or compiled before or during the measurement interval, and the data to insert or the keys to delete may be supplied to the SUT before the run, but the refresh functions may not be executed ahead of time. |
| TPC-H 3.0.1 5.3.6.1 | Throughput interval Ts starts at the first character of the first query of the first stream or the first character requesting the first refresh function, whichever is first, and ends at the last output character of the last query of the last stream or the last refresh transaction committed with its success message, whichever is last. |
| TPC-H 3.0.1 5.3.6.2 | Ts is rounded up to the next 0.01 s when used in metrics and when reported. |
| TPC-H 3.0.1 5.3.7.2 | Query interval QI(i,s) runs from submission of the first character of the query to submission of the first character of the next query; for the last query of the set it ends when the last character of its output reaches the driver. Everything the query needs (for example a temporary table or view) is inside the interval. |
| TPC-H 3.0.1 5.3.7.3 | Refresh interval RI(j,s) runs from submission of the first character requesting the refresh function to the last transaction committed and a success message received by the driver. |
| TPC-H 3.0.1 5.3.7.5 | Each query and refresh interval is rounded to the nearest 0.01 s for metrics and reporting; values under 0.005 s become 0.01 s. |
| TPC-H 3.0.1 5.3.7.10 | Before the refresh stream, the dbgen data for RF1 and RF2 may only be generated, permuted and relocated to the SUT; any other operation, such as formatting or database activity, is part of the timed refresh function. |
| TPC-H 3.0.1 5.3.2 | The performance test has two runs and the reported metric is the run with the lower QphH. |
| TPC-H 3.0.1 5.4.1.4 | If the longest power-test query interval is more than 1000 times the shortest, shorter intervals are raised to longest/1000 for Power@Size. |
| TPC-DS 4.0.0 5.2.3 | Refresh data sets are produced with dsdgen and dsdgen execution is not timed. |
| TPC-DS 4.0.0 5.2.5 | The refresh data set is loaded, and timed, as part of the refresh run, through a generic loader. |
| TPC-DS 4.0.0 5.2.7 | If a staging area is used, loading the refresh data into it is a timed part of data maintenance. |
| TPC-DS 4.0.0 7.4.5.1 | TPower runs from a timestamp taken before the first character of the first query of stream 0 to a timestamp taken after the last output character of its last query. |
| TPC-DS 4.0.0 7.4.7.1, 7.4.7.2 | Query elapsed time QD(s,i,t) runs from submission of the first character of the query to the last output character returned with a success message, for both the Power Test and the Throughput Tests; every operation of the query is inside it. |
| TPC-DS 4.0.0 7.4.7.3 to 7.4.7.5 | Throughput Test 1 runs from before the first character of the first query of the first stream to after the last output character of the last query of the last stream. |
| TPC-DS 4.0.0 7.4.9.1 | Data maintenance interval DI(i,s) starts at the earliest of first submission of the function, first submission of the request to execute it, or first read of its refresh data from outside the Database Location, and ends at the last output character plus a success message. |
| TPC-DI 1.1.0 1.2.2.1.1, 7.2.1.1.2 | Data generation, and copying the generated files into the Staging Area, are not timed. |
| TPC-DI 1.1.0 1.2.2.1.2 | Creating the Data Warehouse database and tables is not a DI operation and is not timed. |
| TPC-DI 1.1.0 7.2.3.6 | The initialization phase is not timed. |
| TPC-DI 1.1.0 7.2.4.1, 7.2.4.3 | The Historical Load transforms the Batch1 Source Data read from the Staging Area into the warehouse and is timed as described in 7.5.2. |
| TPC-DI 1.1.0 7.2.5.4 | Each Incremental Update is timed as described in 7.5.3. |
| TPC-DI 1.1.0 7.4.2 | The batch validation query is included in the time of a timed phase it runs in. |
| TPC-DI 1.1.0 7.5.1 to 7.5.3 | Phase elapsed times are differences between phase completion timestamps taken from the phase completion records (PCR) in the `DImessages` table. |
| TPC-DI 1.1.0 1.2.2.4 | The automated audit phase is not timed. |

PDF pages where each clause was read are in the landing report, not here. The spec text matched the brief in every case; one wording check is noted under TPC-DI.

## Recorded decision

The accepted locality ADR amendment "Round Trip Removed and Platform Timings", item 6, says timers measure only the query: adapters move tag setup, stats lookups, validation, plan capture and power-test query generation out of the measured timer, runs record `run.timing_method`, and runs are compared and ranked within one method. The ADR moved to the site repository in #3545; its text before the move is `docs/development/adr/adr-client-link-locality-disclosure.md` at commit `e4a8a09091^`.

Reconciliation with the sites below:

- Query generation: out of the timer for TPC-H and TPC-DS power (this change set, `power-test-times-query-generation-20261031`), for TPC-H throughput (always pre-generated), and for TPC-DS throughput only while `enable_preflight` is true (see F3).
- Validation and metadata: the connection wrapper's `execute` validates row counts and records metadata inside the per-query timer. Item 6 wants it out; `result-schema-2-4-producer-20261022` owns this.
- Tag setup, stats lookups, plan capture: `set_query_context` only stores the query and stream ids; whether an adapter does tag setup or plan capture inside `execute` was not checked per platform and is not claimed here.
- `run.timing_method`: `benchbox/core/results/loader.py` reads it and `benchbox/core/results/schema.py` exports it when the result carries one, but no producer sets it. `result-schema-2-4-producer-20261022` owns writing it. Until then runs from different timer layouts carry no method label.

## Sites

Deviation column: tracker item id, `none`, or `F<n>` for a finding recorded in the next section that has no tracker owner yet. `K<n>` refers to the known-deviations list.

### TPC-H

| # | File and symbol | Clause | Inside | Outside | Deviation |
|---|---|---|---|---|---|
| H1 | `benchbox/core/tpch/power_test.py` `TPCHPowerTest._run_queries` | TPC-H 5.3.7.2 | `set_query_context`, reference-seed context, `connection.execute`, platform-result status check and metadata propagation, result-count fetch, `commit` | Query generation and dialect translation (`_preflight_validate_generation` before the first timer; a cache miss in `_generate_query` runs before `query_start`); result digest (`_result_digest_from_cursor` runs after `elapsed_seconds`); loop bookkeeping between queries | K1; K7; K8 (delivered here: generation now precedes the timer, earlier versions timed it) |
| H2 | `benchbox/core/tpch/power_test.py` `TPCHPowerTest._execute_refresh_function` | TPC-H 5.3.7.3, 5.3.7.10, 2.5.3.1 | `BEGIN`, reading and parsing `orders.tbl.uN`, `lineitem.tbl.uN` or the delete-key file, batched `INSERT ... VALUES` (RF1) or `DELETE` (RF2), `COMMIT`; duration stored in ms with a 1 ms floor | Refresh connection open; update-set generation (`generate_refresh_update_sets`, before the test); `check_rf1_preconditions` and `check_rf2_preconditions` (database reads); `check_rf2_applied` after the timer | `tpch-refresh-precheck-timing-decision-20261103` (precondition reads sit outside though 5.3.7.10 counts database activity); `perf-tpch-rf1-bulk-load-20261031` (cost of the VALUES path, not a boundary issue); K2 |
| H3 | `benchbox/core/tpch/power_test.py` `TPCHPowerTest.run` | none (spec has no whole-power-test interval; Power@Size uses QI and RI only) | Preflight generation, both refresh connections and functions, all queries, teardown, reported as `total_time` | n/a | none; not an input to Power@Size |
| H4 | `benchbox/core/tpch/throughput_test.py` `TPCHThroughputTest._execute_stream` (stream window) with `benchbox/core/throughput/runner.py` `StreamRunner.compute_metrics` | TPC-H 5.3.6.1 | Stream window starts after `connection_factory()` returns and ends in `finally` before `connection.close()`; Ts = earliest stream start to latest stream end across query streams and the refresh stream | Connection open and close; pre-generation of all stream queries (`_pregenerate_stream_queries`, before the Ts clock) | K4 |
| H5 | `benchbox/core/tpch/throughput_test.py` `TPCHThroughputTest._execute_stream` (per query) | TPC-H 5.3.7.2 | `_resolve_query_text` (a lookup of the pre-generated text), `set_query_context`, `execute`, platform-result check and metadata, `commit`, then `_count_cursor_rows` | Query generation (pre-generated; a stored generation error is re-raised inside the timer and fails the query) | K1; K7; `fetch-timing-contract-decision-20261103` (rows are counted after `commit`, unlike power) |
| H6 | `benchbox/core/tpch/throughput_test.py` `TPCHThroughputTest._execute_refresh_stream` | TPC-H 5.3.6.1 | `start_monotonic` taken before `refresh_connection_factory()`, so connection setup is inside; `end_monotonic` taken in `finally` before `connection.close()`; feeds the Ts span in `compute_metrics` | Update-set generation (`_prepare_refresh_sets`); preconditions (`_check_refresh_preconditions`, separate session before the Ts clock); postconditions (`_check_refresh_postconditions`, after the streams) | `tpch-refresh-stream-window-excludes-connect-20261103` (fix pending, see Pending changes); K4 |
| H7 | `benchbox/core/tpch/throughput_test.py` `TPCHThroughputTest._execute_refresh_function` | TPC-H 5.3.7.3, 5.3.7.10 | Same as H2 (`apply_rf1` or `apply_rf2` between the timer calls) | Same as H2; with `prevalidated` the RF1 check and the RF2 applied-check run outside, before and after the Ts clock | `tpch-refresh-precheck-timing-decision-20261103`; `perf-tpch-rf1-bulk-load-20261031`; K2 |
| H8 | `benchbox/core/tpch/throughput_test.py` `TPCHThroughputTest.run` | TPC-H 5.3.6.1 | `start_time` taken after pre-generation and precondition checks; used by `compute_metrics` only when no stream interval exists, and for `total_time` on the error path | n/a | none; the Ts value comes from H4 and H6 |
| H9 | `benchbox/core/tpch/maintenance_test.py` `TPCHMaintenanceTest._execute_rf1`, `TPCHMaintenanceTest._execute_rf2` | TPC-H 5.3.7.3, 5.3.7.10 | `apply_rf1` or `apply_rf2` between the timer calls (same content as H2); the `finally` clock read only closes an interval that failed before its end was taken | Per-function connection open and close; preconditions; `check_rf2_applied`; update-set generation in `run_maintenance_test` | `tpch-refresh-precheck-timing-decision-20261103` (the standalone maintenance test is the third site); `perf-tpch-rf1-bulk-load-20261031`; K2 |
| H10 | `benchbox/core/tpch/maintenance_test.py` `TPCHMaintenanceTest.run_maintenance_test`, `_run_maintenance_pairs`, `_finish_maintenance_test` | none (the standalone maintenance test is not a TPC-H interval; refresh functions only appear inside the power and throughput tests) | All pairs: connects, preconditions, both functions, closes and the configured `time.sleep` pauses, reported as `total_time` and `overall_throughput` | n/a | none |
| H11 | `benchbox/core/tpch/official_benchmark.py` `TPCHOfficialBenchmark.run_official_benchmark` | none | Whole run wall time (power, throughput, maintenance) as `total_time` | n/a | none; K5 (no two-run selection) |

### TPC-DS

| # | File and symbol | Clause | Inside | Outside | Deviation |
|---|---|---|---|---|---|
| D1 | `benchbox/core/tpcds/power_test.py` `TPCDSPowerTest._execute_one_query` | TPC-DS 7.4.7.1, 7.4.7.2 | `set_query_context`, reference-seed context, `connection.execute`, platform-result check and metadata, result-count fetch, `commit` | dsqgen generation and dialect translation (`_preflight_validate_generation` caches them; a miss in `_generate_query` runs before `query_start`) | K1; K7; K8 (delivered here) |
| D2 | `benchbox/core/tpcds/power_test.py` `TPCDSPowerTest.run`, `_finalize_power_metrics` | TPC-DS 7.4.5.1 | `start_time` taken at the top of `run`, so stream building, preflight generation, connection open and close are inside `total_time` | Nothing; 7.4.5.1 starts TPower only before the first query is submitted | F2 |
| D3 | `benchbox/core/tpcds/throughput_test.py` `TPCDSThroughputTest._execute_stream` (stream window) with `StreamRunner.compute_metrics` | TPC-DS 7.4.7.3 to 7.4.7.5 | Window starts after `connection_factory()` returns and ends in `finally` before the connection closes; the throughput time is the earliest stream start to the latest stream end | Connection open and close; with `enable_preflight` true, all query generation (`_pregenerate_stream_queries`) | F3 (with `enable_preflight` false, `_build_stream_queries` runs inside the window) |
| D4 | `benchbox/core/tpcds/throughput_test.py` `TPCDSThroughputTest._execute_single_query` | TPC-DS 7.4.7.1 | `_cached_query_text` lookup, `set_query_context`, `execute`, platform-result check, `_count_cursor_rows`, `commit`, metadata propagation | Generation when preflight is on | F3 (confirmed: with `enable_preflight` false, `_get_stream_query_text` generates the SQL after `query_start`); K1; K7; `fetch-timing-contract-decision-20261103` |
| D5 | `benchbox/core/tpcds/throughput_test.py` `TPCDSThroughputTest.run` | TPC-DS 7.4.7.3 | `start_time` taken at the top of `run`; used by `compute_metrics` only when no stream interval exists, and for `total_time` on the error path | n/a | none |
| D6 | `benchbox/core/tpcds/maintenance_test.py` `TPCDSMaintenanceTest._execute_maintenance_operation` with `benchbox/core/tpcds/maintenance_operations.py` `MaintenanceOperations.execute_operation` | TPC-DS 7.4.9.1, 5.2.3, 5.2.5, 5.2.7 | The handler call only: synthetic row generation for the operation and its generated INSERT/UPDATE/DELETE statements; two clocks wrap it (`operation.duration` and `MaintenanceResult.duration`) | Connection open, `initialize()` (dimension MIN/MAX queries) and connection close | `tpcds-maintenance-timer-hygiene-20261103` (delivered here: connection and initialization moved out, generator seeded from a recorded value); `tpcds-maintenance-data-source-decision-20261103`; `tpcds-maintenance-dsdgen-refresh-data-20261103` (rows are synthetic, 10 per operation, nothing is read from dsdgen refresh files or loaded into a staging area, so 5.2.5, 5.2.7 and the file-read start of 7.4.9.1 have no counterpart) |
| D7 | `benchbox/core/tpcds/maintenance_test.py` `TPCDSMaintenanceTest.run`, `_finalize_maintenance_result` | TPC-DS 7.4.9.2 (DI(s) for a refresh run) | Loop over all operations: connection open, initialization, handler, close and `operation_interval` sleeps, as `total_time` and `overall_throughput` | n/a | `tpcds-maintenance-timer-hygiene-20261103` covered the per-operation clock only; the whole-loop value still includes per-operation setup and sleeps (F5) |
| D8 | `benchbox/core/tpcds/official_benchmark.py` `TPCDSOfficialBenchmark.run_official_benchmark` | none | Whole run wall time as `total_time` | n/a | none; F6 (one throughput phase, no Throughput Test 2 after maintenance) |
| D9 | `benchbox/core/tpcds/benchmark/runner.py` `TPCDSBenchmark._execute_power_test_query` | TPC-DS 7.4.7.1 | `query_start` (`time.time()`) taken before `get_query`, so generation and translation are inside; `execute`, `fetchall` | n/a | F4 |
| D10 | `benchbox/core/tpcds/benchmark/runner.py` `TPCDSBenchmark.run_power_test` | TPC-DS 7.4.5.1 | Whole 99-query loop as `total_time` (`time.time()`); Power@Size computed from that total, not from per-query intervals | Warm-up queries | F4 |
| D11 | `benchbox/core/tpcds/benchmark/runner.py` `TPCDSBenchmark.run_official_benchmark`, `_finalize_benchmark_result`, `run_throughput_test`, `_aggregate_stream_results` | none | Wall-clock span of the whole legacy run, of the routed throughput wrapper, and of a stream aggregate (`time.time()`); the throughput wrapper reports `routed.total_time` as the duration | n/a | none; F4 notes the legacy entry points |

### TPC-DI

| # | File and symbol | Clause | Inside | Outside | Deviation |
|---|---|---|---|---|---|
| I1 | `benchbox/core/tpcdi/benchmark.py` `TPCDIBenchmark.run_etl_pipeline` | TPC-DI 7.2.4.1, 7.2.4.3, 7.2.5.4, 7.4.2; untimed: 1.2.2.1.1 | `total_duration` covers `generate_source_data` (the extract phase), `_transform_source_data`, `_load_transformed_data` and `backend.validate_results()`; per-phase clocks wrap each step | Schema creation (called earlier by the full benchmark) | F7 |
| I2 | `benchbox/core/tpcdi/benchmark.py` `TPCDIBenchmark._execute_etl_stage_queries` | TPC-DI 7.5.2, 7.5.3 | Stage clock around one `run_etl_pipeline` call (historical, incremental, scd); `execution_time_seconds` is the pipeline `total_duration`, so it feeds the ETL phase times with generation included | n/a | F7, F8 |
| I3 | `benchbox/core/tpcdi/benchmark.py` `TPCDIBenchmark.run_full_benchmark` | none (untimed: 1.2.2.1.2) | Whole run `execution_time_seconds` including initialization, schema creation, ETL, validation and metrics | n/a | F7 |
| I4 | `benchbox/core/tpcdi/benchmark.py` `TPCDIBenchmark.run_etl_benchmark` (wall-time pair around `_execute_etl_stage_queries` via `datetime.now()`) | TPC-DI 7.5.1 | `ETLResult.total_execution_time` as an end-minus-start difference of two wall timestamps around the stage queries | n/a | F8 |
| I5 | `benchbox/core/tpcdi/benchmark.py` `TPCDIBenchmark.run_enhanced_etl_pipeline` | none (BenchBox-specific phases) | Enhanced processing, SCD, incremental loading and optional quality monitoring phases and their total | n/a | none |
| I6 | `benchbox/core/tpcdi/loader.py` `TPCDIDataLoader._load_data`, `TPCDIDataLoader.load_csv_file` | TPC-DI 7.2.4.1 (load step) | Per-table pre/post counts, normalization, insert; CSV read and parse for `load_csv_file` | n/a | none; per-table load times are diagnostic |

## Not a measured interval

These sites read a clock but do not start or stop an interval a specification defines. Each is listed so the grep count reconciles.

| File and symbol | Reason |
|---|---|
| `benchbox/core/tpcdi/benchmark.py` `TPCDIBenchmark.run_benchmark` | Times each validation query iteration; TPC-DI has no per-query timing and the audit phase is untimed (1.2.2.4). |
| `benchbox/core/tpcdi/etl/data_quality_monitor.py` `DataQualityRuleEngine.execute_rule`, `DataQualityMonitor.run_quality_check_batch` | Per-rule and per-batch durations for the BenchBox quality monitor. |
| `benchbox/core/tpcdi/etl/incremental_loader.py` `IncrementalDataLoader._process_table_incremental_changes` | Per-table processing time statistic. |
| `benchbox/core/tpcdi/etl/scd_processor.py` `EnhancedSCDType2Processor._analyze_dimension_changes` | Analysis duration statistic. |
| `benchbox/core/tpcds/power_test.py` `TPCDSPowerTest._execute_query` | Single-query helper with no caller on the `run` path (only unit tests call it); `run` uses `_execute_one_query` (D1). |
| `benchbox/core/tpcds/qualification/runner.py` `worker`, `supervise`, `main` | Qualification harness: per-query elapsed reporting, query and overall deadlines, and worker lifetime. Pass/fail and timeouts, not benchmark metrics. |
| `benchbox/core/tpcds/c_tools.py` `DSQGenBinary._generate_parameter_variations` | `time.time()` seeds a random generator when no seed is given; not a duration. |

`datetime.now()` and `datetime.now().isoformat()` in these files are wall-clock labels (`start_time`, `end_time`) next to the monotonic readings and do not measure intervals, except the `ETLResult` pair in I4.

Coverage: 142 clock call sites (`mono_time()`, `elapsed_seconds()`, `time.time()`, excluding imports and helper definitions) in 47 file-and-symbol pairs. 120 sites in 37 symbols are covered by the 27 rows above that contain a counted call site (H1 to H11, D1 to D11, I1 to I3, I5, I6; I4 is a `datetime.now()` pair with no counted call site; `TPCHThroughputTest._execute_stream` is split across H4 and H5, and `MaintenanceOperations.execute_operation` is part of D6). The other 22 sites in 10 symbols are in the table above.

## Known deviations

Each line is a gap between a site and its clause, or a tracker item that changes a boundary.

| ID | Deviation | Status |
|---|---|---|
| K1 | A query interval ends when the work BenchBox runs for the query returns (after `commit` and the result-count fetch), not when the next query is submitted (5.3.7.2 for TPC-H, which also ends the last query at the last output character). The time between one query's end and the next one's start, including result digest computation and loop bookkeeping, belongs to no interval. TPC-DS 7.4.7.1 ends at the last output character, so only the commit and the fetch order are in question. | Listed here, no tracker item (F1) |
| K2 | Refresh and query intervals are not rounded to 0.01 s (5.3.7.5). Refresh durations are stored in whole milliseconds with a 1 ms floor and enter Power@Size as `max(0.001, ms / 1000)`. | Not implemented; recorded by `tpch-refresh-functions-in-power-and-throughput` (done, PR #3004) |
| K3 | No 1000x clamp of short power-test intervals (5.4.1.4). `TPCMetricsCalculator.calculate_power_at_size` takes the geometric mean of raw positive times. | Not implemented; recorded by `tpch-refresh-functions-in-power-and-throughput` |
| K4 | Ts is not rounded up to the next 0.01 s (5.3.6.2). `StreamRunner.compute_metrics` uses the raw monotonic span in Throughput@Size. | Not implemented; recorded by `tpch-refresh-functions-in-power-and-throughput` |
| K5 | No two-run rule (5.3.2): the official benchmark runs one power and one throughput phase and reports that run, with no lower-score selection. | Not implemented; recorded by `tpch-refresh-functions-in-power-and-throughput` |
| K6 | Per-query ms is exported at 7 significant digits by `_round_query_ms_for_export` in `benchbox/core/results/schema.py`; aggregate stats keep 0.1 ms rounding. Bundles exported before this change carry 0.1 ms per-query values. This is export precision, not a timer boundary. | `derived-power-export-rounding-20261017` (delivered here) |
| K7 | Row-count validation and metadata propagation run inside the per-query timer through the connection wrapper's `execute` and the platform-result check. ADR item 6 moves them out. `run.timing_method` has no producer. | `result-schema-2-4-producer-20261022` |
| K8 | Power-test query generation used to run inside the per-query timer; it now precedes it. Runs from earlier versions include generation and are not comparable. | `power-test-times-query-generation-20261031` (delivered here); baseline follow-up `perf-smoke-rebaseline-after-power-query-generation-20261103` |

Cost items that are not deviations: `perf-tpch-rf1-bulk-load-20261031` (RF1 uses batched `INSERT ... VALUES`, which is slower than a native reader; the interval is correct per 5.3.7.10, the cost is the implementation). Decisions that move a boundary when taken: `tpch-refresh-precheck-timing-decision-20261103` (H2, H7, H9), `fetch-timing-contract-decision-20261103` (H5, D4 and the other per-query sites), `tpcds-maintenance-data-source-decision-20261103` and `tpcds-maintenance-dsdgen-refresh-data-20261103` (D6).

## Findings with no tracker owner

| ID | Finding |
|---|---|
| F1 | K1 has no tracker owner. |
| F2 | TPC-DS `TPCDSPowerTest.run` reports `total_time` from a clock started before stream construction and preflight generation, and stopped after the connection closes. Power@Size uses per-query intervals, so the metric is unaffected, but `total_time` is not the 7.4.5.1 TPower. |
| F3 | Confirmed: with `enable_preflight` false in `benchbox/core/tpcds/throughput_test.py`, `_build_stream_queries` runs after the stream window starts and `_get_stream_query_text` generates each SQL text after `query_start`, so dsqgen generation and translation are inside the stream window and the per-query timer. With preflight on (the default) generation precedes both. |
| F4 | The legacy TPC-DS entry points `TPCDSBenchmark.run_power_test` and `_execute_power_test_query` use `time.time()`, time `get_query` inside the per-query interval, and compute Power@Size from whole-run elapsed time. `PlatformAdapter.run_power_test` and `TPCDSBenchmark.run_official_benchmark` can reach them. |
| F5 | The TPC-DS maintenance `total_time` includes per-operation connection and initialization time and the inter-operation sleeps. |
| F6 | `TPCDSOfficialBenchmark.run_official_benchmark` runs power, throughput and maintenance once each. TPC-DS 7.4.6.2 and 7.4.7.6 to 7.4.7.8 define Throughput Test 1 before and Throughput Test 2 after Data Maintenance Test 1, with TTT2 starting at the end of that maintenance. This ordering is not modelled. |
| F7 | TPC-DI: `run_etl_pipeline` timed phases include source data generation (the `extract` phase calls `generate_source_data`), which TPC-DI 1.1.0 1.2.2.1.1 and 7.2.1.1.2 exclude from timing; `run_full_benchmark` also includes schema creation, which 1.2.2.1.2 excludes. The spec starts the Historical Load at the SUT reading Source Data from the Staging Area (7.2.4.1). |
| F8 | TPC-DI 7.5.1 to 7.5.3 take phase times from PCR records in `DImessages`. `benchbox/core/tpcdi` writes no PCR records (no `DImessages` or `PCR` reference), so BenchBox phase times come from its own clocks, not the specified completion timestamps. |

The TPC-DI wording check: the brief expected an explicit clause that "transformation begins with the SUT reading Source Data". The spec states it as 7.2.4.1 (the Historical Load phase populates the warehouse by transforming Batch1 data from the Staging Area) and 1.2.2.1.1/7.2.1.1.2 (generation and copying are untimed). No separate sentence names the instant reading starts, because timing is defined through PCR timestamps (7.5.1).

## Pending changes (PR #3948, held; not current behaviour)

PR #3948 (`feat/tpch-refresh-path`) is open and held. If it lands, it changes:

- Refresh stream window (`TPCHThroughputTest._execute_refresh_stream`, H6): `start_monotonic` and `start_time` move to after `refresh_connection_factory()` returns; the end already precedes `connection.close()`. Connection setup then leaves the refresh stream interval and the Ts span in `StreamRunner.compute_metrics`. Owner: `tpch-refresh-stream-window-excludes-connect-20261103`.
- RF1 ingest (`apply_rf1` in `benchbox/core/tpch/refresh.py`, H2, H7, H9): the interval stays `BEGIN`, reading and parsing `orders.tbl.uN` and `lineitem.tbl.uN`, the insert, `COMMIT`, with file generation outside. The insert becomes the native reader on DuckDB (`INSERT ... SELECT FROM read_csv`), `COPY FROM STDIN` on PostgreSQL, and stays batched `INSERT ... VALUES` elsewhere. DuckLake and SQLite stay on `VALUES`. SingleStore stays on batched `INSERT ... VALUES` inside the RF1 timer; `LOAD DATA LOCAL INFILE` availability was not verified because no container runtime was available. Owner: `perf-tpch-rf1-bulk-load-20261031`.
- `docs/internal/throughput-result-alignment.md` gains a paragraph describing the refresh stream window.

Update H2, H6, H7 and H9 when that PR lands.
