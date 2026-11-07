---
title: TPC Measured Intervals
status: reference
owner: tpc-measured-intervals-reference-20261103
created: 2026-11-05
---

# TPC Measured Intervals

Every item that moves a timer boundary must update its row in this page in the same PR.

Every site in `tpch`, `tpcds`, `tpcdi` and `throughput` that starts or stops a measured interval, with clause, inside/outside and the tracker item owning any gap, as of this branch. Paths are under `benchbox/core/`; sites are named by symbol. Specs (paraphrased): TPC-H 3.0.1, TPC-DS 4.0.0, TPC-DI 1.1.0.

## Clauses

| Clause | Paraphrase |
|---|---|
| TPC-H 3.0.1 2.5.3.1 | Refresh input data may be supplied before the run, but RFs may not be pre-executed. |
| TPC-H 5.3.6.1 | Ts starts at the first query character of the first stream or the first RF request, whichever is first; it ends at the last output character of the last stream or the last RF commit with success, whichever is last. |
| TPC-H 5.3.6.2 | Ts is rounded up to the next 0.01 s. |
| TPC-H 5.3.7.2 | QI(i,s) runs from submission of the query's first character to submission of the next query's first character; for the last query, to the last output character received by the driver. |
| TPC-H 5.3.7.3 | RI(j,s) runs from the first character requesting the RF to the last transaction committed and success received. |
| TPC-H 5.3.7.5 | Intervals are rounded to the nearest 0.01 s; under 0.005 s becomes 0.01 s. |
| TPC-H 5.3.7.10 | Before the refresh stream, dbgen data may only be generated, permuted and relocated; formatting and database activity are timed. |
| TPC-H 5.3.2 | Two runs; the reported metric is the lower QphH. |
| TPC-H 5.4.1.4 | Power intervals under longest/1000 are raised to it. |
| TPC-DS 4.0.0 5.2.3 | Refresh data comes from dsdgen; dsdgen execution is not timed. |
| TPC-DS 5.2.5 | The refresh data set is loaded, and timed, as part of the refresh run. |
| TPC-DS 5.2.7 | Loading refresh data into a staging area is timed. |
| TPC-DS 7.4.5.1 | TPower runs from before the first query character of stream 0 to after the last output character of its last query. |
| TPC-DS 7.4.7.1, 7.4.7.2 | QD(s,i,t) runs from submission of the query's first character to the last output character with success, for power and throughput. |
| TPC-DS 7.4.7.3 to 7.4.7.5 | Throughput Test 1 runs from before the first query character of the first stream to after the last output character of the last stream. |
| TPC-DS 7.4.9.1 | DI(i,s) starts at the earliest of first submission of the function, first submission of the request to run it, or first read of its refresh data from outside the Database Location; it ends at the last output character plus success. |
| TPC-DI 1.1.0 1.2.2.1.1, 7.2.1.1.2 | Data generation and copying into the Staging Area are not timed. |
| TPC-DI 1.2.2.1.2 | Creating the warehouse database and tables is not timed. |
| TPC-DI 7.2.3.6 | The initialization phase is not timed. |
| TPC-DI 7.2.4.1, 7.2.4.3 | The Historical Load transforms Batch1 Source Data read from the Staging Area and is timed per 7.5.2. |
| TPC-DI 7.2.5.4 | Each Incremental Update is timed per 7.5.3. |
| TPC-DI 7.4.2 | The batch validation query is included in the phase it runs in. |
| TPC-DI 7.5.1 to 7.5.3 | Phase times are differences of completion timestamps from the PCR records in `DImessages`. |
| TPC-DI 1.2.2.4 | The automated audit phase is not timed. |

## Recorded decision

The accepted locality ADR amendment "Round Trip Removed and Platform Timings", item 6: timers measure only the query; adapters move tag setup, stats lookups, validation, plan capture and power-test query generation out of the timer; runs record `run.timing_method` and are compared and ranked within one method. The ADR moved to the site repository in #3545; earlier text: `docs/development/adr/adr-client-link-locality-disclosure.md` at `e4a8a09091^`.

- Query generation: out of the timer for TPC-H and TPC-DS power (`power-test-times-query-generation-20261031`; earlier versions include it), TPC-H throughput (pre-generated), TPC-DS throughput only while `enable_preflight` is true (F3).
- Validation and metadata run inside the per-query timer in the connection wrapper's `execute`; `result-schema-2-4-producer-20261022` owns moving them. Tag setup, stats lookups and plan capture were not checked per adapter.
- `run.timing_method`: `results/loader.py` reads it and `results/schema.py` exports it when present; no producer sets it. `result-schema-2-4-producer-20261022` owns writing it.

## Sites

Deviation column: tracker item id, `none`, `K<n>` (known deviations) or `F<n>` (finding without a tracker owner).

### TPC-H

| # | File and symbol | Clause | Inside | Outside | Deviation |
|---|---|---|---|---|---|
| H1 | `tpch/power_test.py` `TPCHPowerTest._run_queries` | 5.3.7.2 | `set_query_context`, reference-seed context, `execute`, platform-result check and metadata, result-count fetch, `commit` | Generation and translation (`_preflight_validate_generation`; a cache miss in `_generate_query` runs before `query_start`); result digest (after `elapsed_seconds`); loop bookkeeping | K1; K7; K8 (delivered here: generation now precedes the timer) |
| H2 | `tpch/power_test.py` `TPCHPowerTest._execute_refresh_function` | 5.3.7.3, 5.3.7.10, 2.5.3.1 | `BEGIN`, reading and parsing `orders.tbl.uN`, `lineitem.tbl.uN` or the delete keys, the RF1 insert (native reader or batched `INSERT ... VALUES`, see Refresh path) or `DELETE` (RF2), `COMMIT`; ms duration, 1 ms floor | Connection open; `generate_refresh_update_sets`; `check_rf1_applied` and `check_rf2_applied` (database reads, run after the timer stops, so no read of ORDERS or LINEITEM or of the update-set files precedes the timer start) | `tpch-refresh-checks-timing-per-decision-20261103` (checks after the timer; a failed check fails the function and names the update set and the reload); `perf-tpch-rf1-bulk-load-20261031` (delivered in #3948 for DuckDB and PostgreSQL); K2 |
| H3 | `tpch/power_test.py` `TPCHPowerTest.run` | none (Power@Size uses QI and RI only) | Preflight, both refresh connections and functions, all queries, teardown as `total_time` | n/a | none |
| H4 | `tpch/throughput_test.py` `TPCHThroughputTest._execute_stream` (window) with `throughput/runner.py` `StreamRunner.compute_metrics` | 5.3.6.1 | Starts after `connection_factory()` returns, ends in `finally` before `close()`; Ts is earliest start to latest end over all streams | Connection open and close; `_pregenerate_stream_queries` | K4 |
| H5 | `tpch/throughput_test.py` `TPCHThroughputTest._execute_stream` (per query) | 5.3.7.2 | `_resolve_query_text` (lookup), `set_query_context`, `execute`, platform-result check and metadata, `commit`, `_count_cursor_rows` | Generation (a stored error is re-raised inside the timer) | K1; K7; `fetch-timing-contract-decision-20261103` (count follows `commit`, unlike power) |
| H6 | `tpch/throughput_test.py` `TPCHThroughputTest._execute_refresh_stream` | 5.3.6.1 | `start_monotonic` after `refresh_connection_factory()` returns (connection setup outside); `end_monotonic` in `finally` before `close()`; feeds Ts | Connection open and close; update-set generation (`_prepare_refresh_sets`); `_check_refresh_postconditions` (RF1 and RF2 checks for every update set, run after the refresh stream's `end_monotonic` and the query streams, so Ts contains no check) | `tpch-refresh-stream-window-excludes-connect-20261103` (delivered in #3948); K4 |
| H7 | `tpch/throughput_test.py` `TPCHThroughputTest._execute_refresh_function` | 5.3.7.3, 5.3.7.10 | As H2 (apply only; row counts are returned in the evidence) | As H2; the RF1 and RF2 checks run in H6's `_check_refresh_postconditions` | `tpch-refresh-checks-timing-per-decision-20261103`; `perf-tpch-rf1-bulk-load-20261031`; K2 |
| H8 | `tpch/throughput_test.py` `TPCHThroughputTest.run` | 5.3.6.1 | `start_time` after pre-generation; used only when no stream interval exists, and for the error-path `total_time` | n/a | none; Ts comes from H4 and H6 |
| H9 | `tpch/maintenance_test.py` `TPCHMaintenanceTest._execute_rf1`, `_execute_rf2` | 5.3.7.3, 5.3.7.10 | As H2; the `finally` clock read only closes a failed interval | Per-function connect and close; `check_rf1_applied` and `check_rf2_applied` (after the timer stops); generation in `run_maintenance_test` | `tpch-refresh-checks-timing-per-decision-20261103` (third site); `perf-tpch-rf1-bulk-load-20261031`; K2 |
| H10 | `tpch/maintenance_test.py` `TPCHMaintenanceTest.run_maintenance_test`, `_run_maintenance_pairs`, `_finish_maintenance_test` | none (RFs exist only inside power and throughput) | All pairs: connects, functions, post-timer checks, closes, `time.sleep` pauses as `total_time` | n/a | none |
| H11 | `tpch/official_benchmark.py` `TPCHOfficialBenchmark.run_official_benchmark` | none | Whole run wall time | n/a | none; K5 |

### TPC-DS

| # | File and symbol | Clause | Inside | Outside | Deviation |
|---|---|---|---|---|---|
| D1 | `tpcds/power_test.py` `TPCDSPowerTest._execute_one_query` | 7.4.7.1, 7.4.7.2 | `set_query_context`, reference-seed context, `execute`, platform-result check and metadata, result-count fetch, `commit` | dsqgen generation and translation (`_preflight_validate_generation`; a miss in `_generate_query` runs before `query_start`) | K1; K7; K8 (delivered here) |
| D2 | `tpcds/power_test.py` `TPCDSPowerTest.run`, `_finalize_power_metrics` | 7.4.5.1 | `start_time` at the top of `run`: stream building, preflight, connection open and close are inside `total_time` | Nothing; TPower starts only before the first query | F2 |
| D3 | `tpcds/throughput_test.py` `TPCDSThroughputTest._execute_stream` (window) with `StreamRunner.compute_metrics` | 7.4.7.3 to 7.4.7.5 | Starts after `connection_factory()`, ends in `finally` before close; earliest start to latest end | Connection open and close; with `enable_preflight` true, `_pregenerate_stream_queries` | F3 (preflight false: `_build_stream_queries` runs inside the window) |
| D4 | `tpcds/throughput_test.py` `TPCDSThroughputTest._execute_single_query` | 7.4.7.1 | `_cached_query_text`, `set_query_context`, `execute`, platform-result check, `_count_cursor_rows`, `commit`, metadata propagation | Generation when preflight is on | F3 (confirmed: preflight false, `_get_stream_query_text` generates after `query_start`); K1; K7; `fetch-timing-contract-decision-20261103` |
| D5 | `tpcds/throughput_test.py` `TPCDSThroughputTest.run` | 7.4.7.3 | `start_time` at the top; used only when no stream interval exists, and for the error-path `total_time` | n/a | none |
| D6 | `tpcds/maintenance_test.py` `TPCDSMaintenanceTest._execute_maintenance_operation` with `tpcds/maintenance_operations.py` `MaintenanceOperations.execute_operation` | 7.4.9.1, 5.2.3, 5.2.5, 5.2.7 | Handler call only: synthetic row generation and generated INSERT/UPDATE/DELETE; two clocks wrap it | Connection open, `initialize()` (dimension MIN/MAX queries), connection close | `tpcds-maintenance-timer-hygiene-20261103` (delivered here: connection and initialization moved out, generator seeded from a recorded value); `tpcds-maintenance-data-source-decision-20261103`; `tpcds-maintenance-dsdgen-refresh-data-20261103` (rows are synthetic, 10 per operation; no dsdgen refresh files read or staged, so 5.2.5, 5.2.7 and the file-read start of 7.4.9.1 have no counterpart) |
| D7 | `tpcds/maintenance_test.py` `TPCDSMaintenanceTest.run`, `_finalize_maintenance_result` | 7.4.9.2 (DI(s)) | All operations: connect, initialization, handler, close, `operation_interval` sleeps as `total_time` | n/a | `tpcds-maintenance-timer-hygiene-20261103` covered the per-operation clock only (F5) |
| D8 | `tpcds/official_benchmark.py` `TPCDSOfficialBenchmark.run_official_benchmark` | none | Whole run wall time | n/a | none; F6 |
| D9 | `tpcds/benchmark/runner.py` `TPCDSBenchmark._execute_power_test_query` | 7.4.7.1 | `time.time()` before `get_query`: generation and translation inside; `execute`, `fetchall` | n/a | F4 |
| D10 | `tpcds/benchmark/runner.py` `TPCDSBenchmark.run_power_test` | 7.4.5.1 | 99-query loop as `total_time` (`time.time()`); Power@Size from that total | Warm-up | F4 |
| D11 | `tpcds/benchmark/runner.py` `TPCDSBenchmark.run_official_benchmark`, `_finalize_benchmark_result`, `run_throughput_test`, `_aggregate_stream_results` | none | Wall-clock spans of the legacy run, routed throughput wrapper (reports `routed.total_time`) and stream aggregate | n/a | none; F4 |

### TPC-DI

| # | File and symbol | Clause | Inside | Outside | Deviation |
|---|---|---|---|---|---|
| I1 | `tpcdi/benchmark.py` `TPCDIBenchmark.run_etl_pipeline` | 7.2.4.1, 7.2.4.3, 7.2.5.4, 7.4.2; untimed 1.2.2.1.1 | `total_duration` covers `generate_source_data` (extract phase), `_transform_source_data`, `_load_transformed_data`, `backend.validate_results()` | Schema creation | F7 |
| I2 | `tpcdi/benchmark.py` `TPCDIBenchmark._execute_etl_stage_queries` | 7.5.2, 7.5.3 | One `run_etl_pipeline` call per stage (historical, incremental, scd); `execution_time_seconds` is its `total_duration`, generation included | n/a | F7, F8 |
| I3 | `tpcdi/benchmark.py` `TPCDIBenchmark.run_full_benchmark` | none (untimed 1.2.2.1.2) | Whole run: initialization, schema creation, ETL, validation, metrics | n/a | F7 |
| I4 | `tpcdi/benchmark.py` `TPCDIBenchmark.run_etl_benchmark` (`datetime.now()` pair around `_execute_etl_stage_queries`) | 7.5.1 | `ETLResult.total_execution_time` as a wall-timestamp difference | n/a | F8 |
| I5 | `tpcdi/benchmark.py` `TPCDIBenchmark.run_enhanced_etl_pipeline` | none (BenchBox phases) | Enhanced processing, SCD, incremental loading, optional quality monitoring | n/a | none |
| I6 | `tpcdi/loader.py` `TPCDIDataLoader._load_data`, `load_csv_file` | 7.2.4.1 (load step) | Per-table counts, normalization, insert; CSV read for `load_csv_file` | n/a | none; diagnostic |

## Not a measured interval

Clock readers that define no specification interval, listed so the grep count reconciles (`datetime.now()` labels aside, except the I4 pair).

| File and symbol | Reason |
|---|---|
| `tpcdi/benchmark.py` `TPCDIBenchmark.run_benchmark` | Per validation-query iteration; TPC-DI has no per-query timing and audit is untimed (1.2.2.4). |
| `tpcdi/etl/data_quality_monitor.py` `DataQualityRuleEngine.execute_rule`, `DataQualityMonitor.run_quality_check_batch` | Quality monitor durations. |
| `tpcdi/etl/incremental_loader.py` `IncrementalDataLoader._process_table_incremental_changes` | Per-table statistic. |
| `tpcdi/etl/scd_processor.py` `EnhancedSCDType2Processor._analyze_dimension_changes` | Analysis duration statistic. |
| `tpcds/power_test.py` `TPCDSPowerTest._execute_query` | No caller on the `run` path (unit tests only); `run` uses D1. |
| `tpcds/qualification/runner.py` `worker`, `supervise`, `main` | Qualification harness elapsed reporting and deadlines. |
| `tpcds/c_tools.py` `DSQGenBinary._generate_parameter_variations` | `time.time()` seeds a random generator; not a duration. |

Coverage: 142 clock call sites (`mono_time()`, `elapsed_seconds()`, `time.time()`, excluding imports and helper definitions) in 47 file-and-symbol pairs. 120 sites in 37 symbols are covered by the 27 rows with a counted call site (H1 to H11, D1 to D11, I1 to I3, I5, I6; I4 is a `datetime.now()` pair with no counted call site; `TPCHThroughputTest._execute_stream` splits across H4 and H5, `MaintenanceOperations.execute_operation` is part of D6). The other 22 sites in 10 symbols are in the table above.

## Known deviations

| ID | Deviation | Status |
|---|---|---|
| K1 | A query interval ends after `commit` and the result-count fetch, not at submission of the next query (TPC-H 5.3.7.2, which also ends the last query at the last output character); the gap between queries (result digest, loop bookkeeping) belongs to no interval. TPC-DS 7.4.7.1 ends at the last output character, so only commit and fetch order differ. | No tracker item (F1) |
| K2 | Intervals are not rounded to 0.01 s (5.3.7.5); refresh durations are whole ms (1 ms floor) and enter Power@Size as `max(0.001, ms / 1000)`. | Not implemented; recorded by `tpch-refresh-functions-in-power-and-throughput` (done, PR #3004) |
| K3 | No 1000x clamp (5.4.1.4); `TPCMetricsCalculator.calculate_power_at_size` takes the geometric mean of raw times. | Same item |
| K4 | Ts is not rounded up to 0.01 s (5.3.6.2); `StreamRunner.compute_metrics` uses the raw span. | Same item |
| K5 | No two-run rule (5.3.2); one power and one throughput phase are reported. | Same item |
| K6 | Per-query ms is exported at 7 significant digits (`_round_query_ms_for_export`, `results/schema.py`); aggregate stats keep 0.1 ms; earlier bundles carry 0.1 ms. Export precision, not a boundary. | `derived-power-export-rounding-20261017` (delivered here) |
| K7 | Row-count validation and metadata propagation run inside the per-query timer; `run.timing_method` has no producer. | `result-schema-2-4-producer-20261022` |
| K8 | Power-test query generation used to run inside the per-query timer and now precedes it; earlier runs are not comparable. | `power-test-times-query-generation-20261031` (delivered here); follow-up `perf-smoke-rebaseline-after-power-query-generation-20261103` |

Cost, not a deviation: `perf-tpch-rf1-bulk-load-20261031` (batched `INSERT ... VALUES`). Decisions that move a boundary: `tpch-refresh-checks-timing-per-decision-20261103` (H2, H7, H9), `fetch-timing-contract-decision-20261103` (per-query sites), `tpcds-maintenance-data-source-decision-20261103`, `tpcds-maintenance-dsdgen-refresh-data-20261103` (D6).

## Findings with no tracker owner

| ID | Finding |
|---|---|
| F1 | K1 has no tracker owner. |
| F2 | `TPCDSPowerTest.run` `total_time` runs from before stream construction and preflight to after the connection closes; Power@Size is unaffected, but it is not 7.4.5.1 TPower. |
| F3 | Confirmed: with `enable_preflight` false in `tpcds/throughput_test.py`, `_build_stream_queries` runs inside the stream window and `_get_stream_query_text` generates SQL after `query_start`, so generation is inside both timers. With preflight on (default) it precedes both. |
| F4 | Legacy `TPCDSBenchmark.run_power_test` and `_execute_power_test_query` use `time.time()`, time `get_query` inside the interval and compute Power@Size from whole-run elapsed time; `PlatformAdapter.run_power_test` and `run_official_benchmark` reach them. |
| F5 | TPC-DS maintenance `total_time` includes per-operation connect, initialization and sleeps. |
| F6 | `TPCDSOfficialBenchmark.run_official_benchmark` runs power, throughput and maintenance once each; 7.4.6.2 and 7.4.7.6 to 7.4.7.8 put Throughput Test 2 after Data Maintenance Test 1; not modelled. |
| F7 | TPC-DI `run_etl_pipeline` times source data generation (the `extract` phase calls `generate_source_data`), which 1.2.2.1.1 and 7.2.1.1.2 exclude; `run_full_benchmark` also times schema creation (1.2.2.1.2). The Historical Load starts at the SUT reading Source Data from the Staging Area (7.2.4.1); timing itself uses PCR timestamps (7.5.1). |
| F8 | TPC-DI 7.5.1 to 7.5.3 take phase times from PCR records in `DImessages`; `tpcdi` writes none, so phase times come from BenchBox clocks. |

## Refresh path

The refresh stream window and RF1 ingest rows (H2, H6, H7, H9) describe the code after #3948. RF1 uses a native reader where the adapter provides one (DuckDB `INSERT ... SELECT FROM read_csv`, PostgreSQL `COPY FROM STDIN`, SingleStore `LOAD DATA LOCAL INFILE`) and batched `INSERT ... VALUES` elsewhere (DuckLake and SQLite among them). SingleStore sends the dbgen `orders.tbl.uN` and `lineitem.tbl.uN` files straight to `LOAD DATA LOCAL INFILE` inside the RF1 transaction, so file reading, parsing and the load all fall between the timer marks; the server rejects bad rows except that it silently turns non-numeric DECIMAL text into 0.00, so the statement routes DECIMAL columns through `SET col = IF(@col REGEXP <decimal pattern>, @col, 1e30)` to make the server abort on them (live checks in `tests/integration/platforms/test_singlestore_live.py`, marker `live_singlestore`: per-column parity with the VALUES path at SF0.01 and an injected bad decimal that leaves no rows). Owners: `tpch-refresh-stream-window-excludes-connect-20261103`, `perf-tpch-rf1-bulk-load-20261031`.
