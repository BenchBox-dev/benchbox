<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# ADR: High-Throughput Data Generation Boundary, Budgets, and Validation Gates

## Status

Accepted (2026-11-24). Extends
`docs/internal/adr/adr-python-core-runtime.md` to cover TPC data generation,
streaming format conversion, synthetic generator vectorization, provenance
versioning, and the entry criteria for any future standalone Rust generator
package.

## Context

`docs/internal/adr/adr-python-core-runtime.md` keeps BenchBox's core harness
pure Python (`py3-none-any`) and establishes a two-step acceleration order for
non-TPC synthetic generators that hit a measured throughput or memory wall:
vectorized PyArrow/NumPy (or embedded DuckDB SQL behind the optional `duckdb`
extra) first, and a decoupled native
generator package second. That ADR left TPC-H and TPC-DS on bundled per-OS C
binaries (`dbgen` and `dsdgen` in `benchbox/_binaries/`).

Maintaining precompiled C binaries across platforms has proved fragile (for
example, an `-O2` compiler undefined-behavior regression in macOS `dbgen`
invalidated published TPC-H results), while several pure-Python synthetic
generators and the delimited-to-Parquet converter hit severe wall-time and
memory walls.

### Empirical Baselines (2026-11-23, N=1 Single Runs Under `make heavy-run`)

Measured on macOS 27.0.1 arm64 (10 CPUs, 16 GiB RAM, Python 3.11.17, PyArrow
24.0.0, DuckDB 2.0.0, DataFusion 55.0.0, `tpcgen-cli` 4.0.0) in isolated
subprocesses under `make heavy-run` (`ADMITTED`, memory pressure level 1,
5-minute host load 2.0–4.6) using `benchbox.utils.clock.mono_time()` and OS
peak RSS (`/usr/bin/time -l`). All `dbgen`/`dsdgen` baselines ran with
`parallel=1` (single process). These are `N=1` scoping baselines on a
non-quiesced development host; cutover enforcement requires 3-run medians
(`N=3`) on a quiet host.

| Benchmark | SF 1 Gen (s) | SF 1 Gen RSS (MiB) | SF 1 Out (MiB) | SF 1 Parquet Conv (s) | SF 1 Conv RSS (MiB) | Notes |
| :--- | ---: | ---: | ---: | ---: | ---: | :--- |
| `h2odb` | 120.62 | 165.6 | 1,348.0 | 5.56 | 2,183.6 | Slowest synthetic generator; highest converter RSS |
| `tpcds` | 55.71 | 137.2 | 1,177.8 | 5.37 | 1,393.2 | Bundled `dsdgen` (`parallel=1`) |
| `tsbs_devops` | 49.99 | 313.7 | 877.3 | *(fails)* | 140.1 | 5 tables (`tags`, `cpu`, `mem`, `disk`, `net`); converter schema error |
| `tpch_skew` | 43.56 | 450.6 | 1,022.4 | 3.25 | 1,485.4 | Includes base `dbgen` (`parallel=1`) plus Python skew pass |
| `clickbench` | 43.11 | 165.7 | 526.6 | 2.18 | 1,063.6 | Synthetic fallback mode |
| `ssb` | 34.99 | 279.2 | 581.6 | 2.09 | 1,257.5 | Scalar Python `random.Random(42)` |
| `vector_search` | 34.54 | 189.7 | 1,279.9 | *(fails)* | 202.3 | Converter fails on CSV header row |
| `coffeeshop` | 28.95 | 166.2 | 858.4 | *(fails)* | 141.9 | Converter ignores comma delimiter |
| `joinorder_synthetic` | 17.48 | 3,133.1 | 432.2 | *(fails)* | 141.0 | Highest generator RSS; converter schema error |
| `amplab` | 15.84 | 1,422.6 | 648.3 | 1.45 | 695.2 | Second-highest generator RSS |
| `tpch` | 12.88 | 301.8 | 1,041.5 | 3.34 | 1,478.4 | Bundled `dbgen` (`parallel=1`; SF 10 measured: 111.65 s, 301.8 MiB RSS, 10,629 MiB out) |

**SF 10 baseline scope and extrapolations (not directly measured beyond `tpch`):**

- `tpch` (`dbgen`, `parallel=1`) was measured to completion at SF 10: **111.65 s**,
  301.8 MiB peak RSS, 10,629 MiB output.
- The other eight generators (`tpcds`, `ssb`, `h2odb`, `tsbs_devops`,
  `vector_search`, `coffeeshop`, `clickbench`, `tpch_skew`) were stopped at a
  45 s safety cap during SF 10 probing; their legacy SF 10 wall times are
  unmeasured (linearly projected at ~290–1,200 s from SF 1).
- `joinorder_synthetic` (3.13 GiB at SF 1; standard `scale_options` up to 2.0,
  unbounded max), `amplab` (1.42 GiB at SF 1; standard `scale_options` up to
  1.0, unbounded max), and full-table SF 10 `BaseFormatConverter` Parquet
  conversion (1.06–2.18 GiB at SF 1) were **skipped** at SF 10 rather than
  OOM-tested because linear projection places their working sets above this
  host's 8 GiB heavy-work memory budget.

### Empirical Validation Probes (2026-11-23 and 2026-11-24)

1. **Upstream `tpcgen-cli` 4.0.0 (`datafusion-contrib/tpcgen-rs`, Apache-2.0):**
   - **TPC-H (SF 0.01, 0.1, 1.0):** All 8 tables are 100% SHA-256 byte-identical
     to bundled `dbgen` at both `--num-threads 1` and default threads once the
     trailing `|` terminator is stripped from `tpcgen-cli` output. Bidirectional
     DuckDB `EXCEPT ALL` on Parquet returns 0 rows across all 8 tables. At SF 1
     against single-process `dbgen` (`parallel=1`, 12.88 s), `tpcgen-cli` runs
     in **3.39 s** at 1 thread (**3.8x** like-for-like, 529 MiB RSS), **1.34 s**
     at default 10 threads (**9.6x**, 932 MiB RSS), and **2.52 s** for direct
     Parquet (616 MiB RSS vs 16.22 s legacy `dbgen` + convert).
   - **TPC-DS (SF 1.0, `--compat c`):** All 24 tables are 100% SHA-256
     byte-identical to bundled `dsdgen` at both 1 thread and default threads,
     with 0-row bidirectional DuckDB `EXCEPT ALL` on Parquet. Against
     single-process `dsdgen` (`parallel=1`, 55.71 s), `tpcgen-cli` runs in
     **5.13 s** at 1 thread (**10.9x** like-for-like, 31 MiB RSS), **0.91 s**
     at default 10 threads (**61.2x**, 156 MiB RSS), and **1.05 s** for direct
     Parquet (586 MiB RSS vs 61.08 s legacy `dsdgen` + convert).
   - **TPC-DS fractional sub-scales (`SF < 1.0`):** At SF 0.01 and SF 0.1,
     `tpcgen-cli` 4.0.0 uses a different fractional scaling curve than
     BenchBox's patched `dsdgen` (17/24 tables differ at SF 0.01, 15/24 at
     SF 0.1; for example `customer_demographics` stays at 1,920,800 rows and
     `store_sales` has 120,527 vs 28,810 rows). Only the 7 fixed-size tables
     match byte-for-byte.
   - **Framing, streaming, parts, refresh sets, and wheels:**
     - `--stdout` output is SHA-256 identical to file output, enabling
       single-pass trailing-`|` stripping and compression without a second disk
       pass.
     - `normalize_tbl_trailing_delimiters()` in `benchbox/core/tpch/generator.py`
       inspects only the last 3 bytes of a file. While safe for TPC-H today
       (every TPC-H table ends with a non-empty comment column), reusing it
       heuristically on TPC-DS `time_dim.dat` (where the final `t_meal_time`
       column is empty on 50,400 rows) strips the final field separator on
       already-normalized files. The adapter must strip trailing `|`
       unconditionally from `tpcgen-cli` output only.
     - `--parts N` writes nested `<table>/<table>.<part>.<ext>` paths and
       defaults to 25 TPC-DS tables (including `dbgen_version`). The adapter
       must select only the 24 benchmark tables and map chunk paths to
       BenchBox's flat layout.
     - `tpcgen-cli` 4.0.0 has no TPC-H `RF1`/`RF2` or TPC-DS `-update` refresh
       options and publishes no Windows arm64 wheel (13 wheels cover macOS
       arm64/x86_64, Linux glibc/musl, and Windows x86/x86_64).
   - **Parquet layout, schema alignment, and 2x2 timing isolation:**
     - `ConversionOptions.row_group_size` in
       `benchbox/utils/format_converters/base.py` currently defaults to
       `128 * 1024 * 1024` (134M **rows**, confusing bytes with rows), causing
       `ParquetConverter` to write **1 row group per table** (8 RGs / 303 MiB
       TPC-H; 24 RGs / 383 MiB TPC-DS). `tpcgen-cli` defaults to 7 MiB row
       groups (94 RGs / 345 MiB TPC-H; 72 RGs / 399 MiB TPC-DS).
     - **Schema alignment:** `ParquetConverter` matches BenchBox's
       `ArrowTypeMapper` (`schema.py`), which maps SQL `INTEGER` to `int64`,
       strings to `string` (`Utf8`), and bare `DECIMAL` (used in
       `benchbox/core/tpcds/schema/table_specs.yaml`) to `decimal128(15, 2)`.
       `tpcgen-cli` emits TPC-spec `int32` for `INTEGER`, `decimal128(7, 2)`
       for TPC-DS decimals, and Arrow `string_view` (`Utf8View`).
     - **Timing shift and 2x2 isolation:** At SF 1.0 on identical values
       (identical `result_digest` on both engines), Parquet-scan timings
       shifted 4.6x–9.4x (DuckDB Q1 141.4 ms to 25.5 ms; DataFusion Q6
       171.6 ms to 18.2 ms; DuckDB `store_sales` aggregation 46.4 ms to
       7.9 ms). A 2x2 isolation test on TPC-H SF 0.1 `lineitem` (DuckDB Q1,
       5-run median) separated row-group count from Arrow types:
       `leg_default_rg` (1 RG, BenchBox types) **12.94 ms** vs `leg_100k_rg`
       (7 RGs, BenchBox types) **5.14 ms**; `nat_1rg` (1 RG, `tpcgen-cli`
       types) **11.28 ms** vs `nat_7mb_rg` (6 RGs, `tpcgen-cli` types)
       **4.35 ms**. Roughly 85% of the timing shift comes from row-group count
       and ~15% from types/writer differences.
2. **Vectorized Python prototype on `h2odb` (slowest synthetic generator):**
   - Chunked NumPy + PyArrow streaming (250,000 rows per chunk, per-chunk
     deterministic `np.random.default_rng([seed, chunk_idx])`, exact Arrow
     schema match against `benchbox/core/h2odb/schema.py`) generated SF 1
     (10M rows) in **6.20 s** to Parquet (**20.3x faster** than 126.18 s
     legacy gen + convert) at **388.6 MiB** peak RSS (**5.6x lower** than
     legacy conversion), and **7.09 s** to `.tbl` (**17.0x faster**, 345.6 MiB
     RSS).
   - At SF 10 (100M rows, 5.76 GiB Parquet / 18.41 GiB `.tbl`), it completed
     in **62.14 s** (Parquet) and **71.83 s** (`.tbl`) with flat O(1) memory
     (**395.8 MiB** / **343.7 MiB** peak RSS — only +7 MiB over SF 1).
   - **Value-format and distribution verification (SF 0.01, 100k rows):**
     Prototype `.tbl` output is 36.6% larger than legacy (193 vs 141
     bytes/row) solely because Arrow CSV formatting emits fixed-width decimals
     and microseconds (`DECIMAL(18,14)` pads 4 lat/lon columns with 8 trailing
     zeros = +32 B/row; 2 `timestamp[us]` columns add `.000000` = +14 B/row; 6
     `DECIMAL(8,2)` columns pad trailing zeros = +6 B/row). Across 100k rows,
     numeric distributions match legacy closely (`avg_passengers` 1.556 vs
     1.555, `avg_trip_distance` 18.043 vs 17.999, `avg_fare` 47.61 vs 47.50,
     `avg_tip` 2.06 vs 2.08, `avg_total` 52.36 vs 52.28, `store_and_fwd_flag`
     `Y` rate 0.0502 vs 0.0479, identical `2015-01-01..2019-12-31` span).
     Production `.tbl` emission should match legacy precision formatting
     (`timestamp[s]`, 6-decimal coordinates) to avoid bloating delimited files.

## Decision

1. **Extend the runtime ADR to TPC generation via upstream `tpcgen-cli`.**
   Adopt published `tpcgen-cli` wheels (pinned by version and hashes in
   `uv.lock`) for TPC-H (all supported scales) and TPC-DS (`SF >= 1.0`, integer
   scales) through an opt-in subprocess adapter. Do not create a wrapper Rust
   repository for TPC generation. Keep `qgen`, `dsqgen`, `tpcds.idx`, and query
   templates in `benchbox`, and keep bundled `dbgen`/`dsdgen` for fractional
   TPC-DS scales (`SF < 1.0`), refresh sets (`RF1`/`RF2`, TPC-DS `-update`),
   and platforms without a `tpcgen-cli` wheel (Windows arm64).
2. **Upgrade format conversion to streaming with an explicit row-group policy.**
   Replace full-table `pyarrow.csv.read_csv` + `pa.concat_tables`
   materialization in `benchbox/utils/format_converters/base.py` and
   `parquet_converter.py` with streaming batch conversion and inline row
   counting, and fix the four existing `FormatConversionOrchestrator`
   incompatibilities (`tsbs_devops`, `vector_search`, `coffeeshop`,
   `joinorder_synthetic`).
   - **Row-group size and re-baseline policy:** Streaming conversion cannot
     hold a single SF 10 row group in <= 1,024 MiB RAM. Fix the
     `128 * 1024 * 1024` rows-vs-bytes default bug to an explicit streaming
     target of **122,880 rows per row group** (matching DuckDB's standard row
     group size, recorded in `ConversionOptions` and `data_generation_hash`).
   - **Owner decision (2026-11-24):** Switching from 1 row group per table to
     multi-row-group Parquet shifts Parquet-scan query timings by ~2.5x–6x on
     identical data (historical 1-RG Parquet files understated Parquet-scanning
     engines). The owner signed off on adopting **122,880 rows per row group**
     as the default Parquet layout for all runs immediately, under three
     provenance and corpus controls:
     1. `parquet_writer` and `parquet_row_group_size` are hashed into a
        Parquet-scoped identity (`parquet_data_generation_identity_hash`,
        emitted as `data_generation_hash` on Parquet runs while leaving
        delimited TBL/CSV runs on `base_constants_hash`), preventing silent
        cross-layout comparison between pre-change single-row-group bundles
        and streaming-layout bundles.
     2. Cached pre-streaming Parquet manifests lacking `parquet_writer` /
        `parquet_row_group_size` are treated as stale on Parquet runs and
        regenerated automatically.
     3. Pre-change Parquet corpus baseline inventory audited on
        `origin/published-results` (89 of 496 bundles reference Parquet:
        DataFusion 69, Spark 7, LakeSail 3, BigQuery 3, Databricks 3,
        Snowflake 3, unknown 1). Local Parquet suites (DataFusion, Spark,
        LakeSail) are scheduled for re-baselining once the streaming converter
        lands; existing cloud bundles (BigQuery, Databricks, Snowflake) remain
        marked pre-change without incurring live-cloud spend.
   - **Writer decision (2026-11-25):** PyArrow is the only Parquet writer.
     DuckDB is not a core dependency (it ships only in extras), and the DuckDB
     `COPY` fast path that the streaming converter first added ran out of its
     memory limit on default zstd chunked input and fell back to PyArrow for 6
     of 8 TPC-H tables, so the writer depended on host and input shape. Where
     it did run, its row groups did not match the recorded 122,880 rows, its
     bytes changed between runs, and it wrote NOT NULL columns as nullable.
     PyArrow `open_csv` is single-threaded, so the converter reads up to two
     chunk files concurrently and writes them in input order through one
     `ParquetWriter`; the output is byte-identical to the serial writer.
     Measured on TPC-H from 10-chunk zstd input on a loaded host: SF 1 4.8 s /
     431 MiB and SF 10 52.6 s / 512 MiB (serial: 9.5 s and 100.3 s).
     DataFusion (one partition: SF 10 30.5 s / 476 MiB) and Polars (SF 10
     13.1 s / 2,129 MiB) were evaluated and rejected: each adds a 105–160 MB
     dependency, silently truncates out-of-scale decimals, and would change
     Parquet bytes; Polars also exceeds the memory budget. A Linux SF 10 miss
     against the converter budget is the measured miss that would open the
     Rust repository gate.
3. **Keep a strict file/statistics boundary and Python-only manifest ownership.**
   Any native generator stays behind:

   ```text
   (benchmark, scale_factor, seed, format, compression, threads, output_dir, options)
       -> generated table files + per-file statistics
   ```

   - Zero dependency on `PlatformAdapter`, `benchbox-sqlglot`, query
     translation, or runner orchestration.
   - `benchbox/utils/datagen_manifest.py` (`DataGenerationManifest`) remains
     the sole writer of `_datagen_manifest.json`, extended with optional
     `generator_backend` and `generator_backend_version` fields.
   - Native generators use a CLI subprocess interface packaged as binary-only
     `py3-none-<platform>` wheels under the optional extra
     `benchbox[native-datagen]`. Core `benchbox` stays `py3-none-any` and never
     makes a native generator a required dependency.
4. **Backend selection: no `auto` mode, no extra-dependent default switching.**
   - Expose `--datagen-backend legacy|native` on `benchbox datagen` and
     `benchbox run` backed by a per-benchmark default table in
     `benchbox/utils/datagen_backend.py` (initialized with every benchmark on
     `legacy`).
   - **Default resolution never depends on what optional extras are
     installed.** There is no `auto` mode and no silent fallback:
     - **Explicit `--datagen-backend native`:** always fails loudly with an
       actionable install/fix message if `native-datagen` is not installed,
       the platform lacks a wheel (Windows arm64), or the benchmark/scale is
       unsupported (`tpcds` at `SF < 1.0`).
     - **In-tree vectorized Python generators** (synthetic benchmarks and SSB)
       depend only on NumPy/PyArrow shipped in core `benchbox` (DuckDB is an
       optional extra, not a core dependency); once
       validated, promoting their default in `datagen_backend.py` is
       unconditional, with `--datagen-backend legacy` preserved for at least
       one minor release.
     - **Optional-extra `tpcgen-cli` (`tpch` and `tpcds` `SF >= 1.0`):** stays
       opt-in (`--datagen-backend native`) while `tpcgen-cli` remains optional.
       Promoting `tpcgen-cli` to the default backend on supported platforms
       requires a separate post-validation owner decision (either accepting an
       explicit install prompt when `native-datagen` is absent, or adopting a
       dedicated packaging change).
5. **Per-benchmark versioning, folder identity, and Parquet layout provenance.**
   - **Never bump global `DATA_GENERATION_VERSION`** in
     `benchbox/utils/datagen_version.py` for individual generator changes,
     because it feeds `compute_base_constants_hash()` for every benchmark and
     would invalidate all cached datasets. Use a per-benchmark generator
     version registry instead.
   - Record `generator_backend` and `generator_backend_version` in
     `_datagen_manifest.json` for audit provenance.
   - In `compute_datagen_identity_hash()`:
     - For **byte-identical delimited TPC output** (`tpch` all scales, `tpcds`
       `SF >= 1.0`), hash content/schema identity rather than the backend name
       so `dbgen`/`dsdgen` and `tpcgen-cli` delimited datasets share
       `datagen/<benchmark>_<sf>` and retain comparable corpus hashes.
     - For **non-identical outputs** (any Parquet writer/row-group change,
       direct `tpcgen-cli` Parquet, or any rewritten synthetic/SSB generator),
       include per-benchmark generator version, Parquet writer/row-group
       layout, and backend in `compute_datagen_identity_hash()`, enforce
       exact match in `manifest_datagen_is_current()`, and isolate validation
       runs in private directories.
   - Direct `tpcgen-cli` Parquet remains opt-in during parallel buildout.
6. **Vectorize over-budget synthetic generators and SSB in Python first; enforce
   NumPy stream stability; gate any Rust repository on measured failure.**
   - Re-implement over-budget synthetic benchmarks (`h2odb`, `tsbs_devops`,
     `clickbench` synthetic, `tpch_skew`, `vector_search`, `coffeeshop`,
     `joinorder_synthetic`, `amplab`) one benchmark at a time in vectorized
     NumPy/PyArrow (embedded DuckDB SQL only behind the optional `duckdb`
     extra) with explicit deterministic seeding
     and per-benchmark versioning.
   - **Python/NumPy stream stability:** `np.random.Generator` (PCG64) does not
     guarantee bit-identical streams across all future NumPy releases. Protect
     every vectorized Python generator with golden per-table SHA-256 tests in
     CI so any NumPy or PyArrow drift fails loudly on dependency updates; any
     intentional RNG/stream change requires a per-benchmark version bump and
     re-acceptance.
   - Include `ssb` as a per-benchmark versioned RNG and
     `SSB_EXPECTED_ROW_COUNTS` migration gated after active SSB reference-count
     and publication work finishes, preserving legacy `random.Random(42)`
     behind `--datagen-backend legacy` and requiring exact dual-engine DuckDB +
     DataFusion agreement across all 13 SSB queries at SF 0.01, 0.1, 1.0, and
     10.0.
   - **Rust repository trigger (`BenchBox-dev/benchbox-datagen`):** Do not
     create a standalone Rust repository unless an in-scope synthetic generator
     or SSB still misses its wall-time or peak-RSS budget below after Python
     vectorization and receives explicit owner approval. If created, keep it to
     one library crate and one binary crate, distribute binary-only
     `py3-none-<platform>` wheels (no PyO3), enforce cross-thread and
     cross-platform bit-exact determinism (fixed-block per-table RNG, pinned
     `rand`/`rand_chacha` with owned sampling, pure-Rust `libm` or integer
     math), print JSON per-file stats (no Rust manifest writer), and run schema
     checks in `benchbox` CI with no circular dependency.
7. **Defer Windows arm64 and legacy binary retirement until after validation.**
   Keep bundled C binaries untouched during parallel buildout and for at least
   one minor release after default cutover. Decide Windows arm64 keep-vs-drop
   and remove obsolete `dbgen`/`dsdgen` binaries or legacy Python loops only
   after confirming platform coverage, refresh-stream coverage, and historical
   published-result reproduction needs.

## Performance Budgets and Validation Gates

### Per-Benchmark Target Budgets (`N=3` Quiet-Host Medians, macOS arm64 / Linux x86_64)

| Subsystem / Benchmark | SF 1 Wall Budget | SF 10 Wall Budget | SF 1 Peak RSS | SF 10 Peak RSS | Basis & Margin |
| :--- | ---: | ---: | ---: | ---: | :--- |
| Streaming `ParquetConverter` (all 11 benchmarks, incl. the 4 currently failing) | <= 6 s (successful baselines) / <= 10 s (repaired 4) | <= 60 s / <= 100 s | <= 512 MiB | <= 1,024 MiB | SF 1 successful baselines 1.45–5.56 s at 695–2,184 MiB RSS; streaming 122,880-row RGs bounds RAM O(1). |
| `tpch` (`tpcgen-cli` delimited / direct Parquet) | <= 3 s / <= 5 s | <= 30 s / <= 50 s | <= 1,536 MiB (default threads) / <= 768 MiB (`-n 1`) | <= 2,048 MiB (or cap `--num-threads`) | SF 1 measured 1.34 s / 2.52 s (932 / 616 MiB on 10 CPUs); margin accounts for thread-scaled RSS. |
| `tpcds` `SF >= 1` (`tpcgen-cli` delimited / direct Parquet) | <= 3 s / <= 5 s | <= 30 s / <= 50 s | <= 1,024 MiB | <= 2,048 MiB | SF 1 measured 0.91 s / 1.05 s (156 / 586 MiB on 10 CPUs). |
| `h2odb`, `tsbs_devops`, `clickbench` (synthetic), `ssb`, `vector_search`, `coffeeshop` | <= 15 s (target <= 10 s) | <= 150 s (target <= 100 s) | <= 512 MiB | <= 512 MiB | Vectorized `h2odb` prototype measured 6.20 s / 389 MiB (SF 1) and 62.14 s / 396 MiB (SF 10); ~2x margin. |
| `tpch_skew` | <= 15 s | <= 150 s | <= 512 MiB | <= 1,024 MiB | SF 1 baseline 43.56 s / 451 MiB; streaming skew over `tpcgen-cli` / chunked batches. |
| `amplab`, `joinorder_synthetic` | <= 15 s | <= 150 s | <= 512 MiB | <= 1,024 MiB | Streaming-architecture target (not extrapolated from `h2odb`); legacy SF 1 RSS is 1,423 / 3,133 MiB. |

### Mandatory Validation Gates

No benchmark may flip its default until corpus `data_generation_hash` export
gaps are closed and all applicable gates pass on the pinned version across
every supported platform:

- **Structure gate (all benchmarks, all backends):** Output passes
  `BenchmarkDataValidator`, matches `schema.py` column names/order/types (or
  documents exact `tpcgen-cli` Parquet type differences for opt-in direct
  Parquet), matches exact row counts (SF 0.01 and 0.1 on pull requests; SF 1
  and 10 on pin bumps), and recovers cleanly from an interrupted
  `_generation_in_progress` marker.
- **Exact parity gate (TPC-H and TPC-DS `SF >= 1.0`):**
  - **PR required check** (path-filtered to the adapter, TPC generators, or
    `tpcgen-cli` pin): SHA-256 delimited byte identity (after unconditional
    trailing-`|` stripping on `tpcgen-cli` output, at `--num-threads 1` and
    default) and bidirectional DuckDB `EXCEPT ALL` = 0 rows on Parquet for
    **TPC-H at SF 0.01 and 0.1** and **TPC-DS at SF 1.0** (all 24 tables,
    including `--compat c` NULL vs empty-string and UTF-8 parity), plus
    validation of all 7 derived TPC consumers (`tpchavoc`, `read_primitives`,
    `write_primitives`, `transaction_primitives`, `datavault`, `tpcds_obt` smoke
    suites and `ai_primitives` datagen/dry-run).
  - **Pin-bump and pre-cutover required check:** Extends the parity matrix to
    **TPC-H SF 0.01, 0.1, 1.0, and 10.0** and **TPC-DS SF 1.0 and 10.0** (all
    24 tables), adding secondary single-engine `result_digest.py` verification
    on the Parquet path. Scales above SF 10 are not exhaustively byte-diffed
    against C binaries in CI and instead verify specification row counts and
    thread-count invariance.
- **Rewritten synthetic and SSB acceptance gate:** Exact table row counts, zero
  orphan foreign keys, documented hit rates, a one-time owner-approved
  acceptance report comparing legacy vs new distributions and every benchmark
  query's row count across DuckDB and DataFusion, followed by frozen golden
  per-table SHA-256 hashes and expected row counts (catching NumPy/PyArrow
  drift in CI). Never use arbitrary tolerance bands on deterministic output.
- **Performance and memory gate (ownership and execution):** Wall time
  (`mono_time()`) and peak RSS at both SF 1 and SF 10 across all 11 in-scope
  benchmarks (none has a registry upper scale cap) measured as `N=3` medians on
  a quiet host meeting the table above. Owned and run by the PR author on the
  maintainer host via `make heavy-run` (macOS arm64) and on the Linux CI/agent
  runner for PRs touching the streaming converter, `tpcgen-cli` pin/adapter, or
  any rewritten synthetic generator, with raw medians recorded in the PR body.

## Consequences

- TPC-H and TPC-DS (`SF >= 1.0`) gain 3.8x–10.9x single-threaded (9.6x–61.2x
  multi-threaded) generation speedups using an upstream Apache-2.0 wheel with
  zero custom Rust code to maintain.
- Streaming Parquet conversion fixes four broken benchmark conversions and
  removes the >2 GiB SF 1 memory wall, while explicitly surfacing the
  122,880-row-group layout change as a published-run re-baseline decision for
  the owner.
- Vectorized Python/PyArrow delivers ~20x speedups and flat ~390 MiB RSS up to
  SF 10 (100M rows) on numeric synthetic generators, making a standalone Rust
  repository unnecessary unless a string-heavy or complex FK-graph generator
  later misses budget.
- Bundled C binaries remain for `qgen`/`dsqgen`/templates, TPC-DS fractional
  scales (`SF < 1.0`), refresh sets (`RF1`/`RF2`, `-update`), and Windows arm64
  until upstream coverage or an explicit owner retirement decision changes that
  boundary.
