<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# ADR: High-Throughput Data Generation Boundary, Budgets, and Validation Gates

## Status

Proposed (2026-11-23). Extends `docs/internal/adr/adr-python-core-runtime.md` to
cover TPC data generation, streaming format conversion, synthetic generator
vectorization, provenance versioning, and the entry criteria for any future
standalone Rust generator package.

## Context

`docs/internal/adr/adr-python-core-runtime.md` keeps BenchBox's core harness
pure Python (`py3-none-any`) and establishes a two-step acceleration order for
non-TPC synthetic generators that hit a measured throughput or memory wall:
vectorized PyArrow/NumPy or embedded DuckDB SQL first, and a decoupled native
generator package second. That ADR left TPC-H and TPC-DS on bundled per-OS C
binaries (`dbgen` and `dsdgen` in `benchbox/_binaries/`).

Maintaining precompiled C binaries across platforms has proved fragile (for
example, an `-O2` compiler undefined-behavior regression in macOS `dbgen`
invalidated published TPC-H results), while several pure-Python synthetic
generators and the delimited-to-Parquet converter hit severe wall-time and
memory walls at Scale Factor 1 and Scale Factor 10.

### Empirical Baselines (2026-11-23)

Measured on macOS 27.0.1 arm64 (10 CPUs, 16 GiB RAM, Python 3.11.17, PyArrow
24.0.0, DuckDB 2.0.0, DataFusion 55.0.0, `tpcgen-cli` 4.0.0) in isolated
subprocesses using `benchbox.utils.clock.mono_time()` and OS peak RSS:

| Benchmark | SF 1 Gen (s) | SF 1 Gen RSS (MiB) | SF 1 Out (MiB) | SF 1 Parquet Conv (s) | SF 1 Conv RSS (MiB) | Notes |
| :--- | ---: | ---: | ---: | ---: | ---: | :--- |
| `h2odb` | 120.62 | 165.6 | 1,348.0 | 5.56 | 2,183.6 | Slowest synthetic generator; highest converter RSS |
| `tpcds` | 55.71 | 137.2 | 1,177.8 | 5.37 | 1,393.2 | Bundled `dsdgen` |
| `tsbs_devops` | 49.99 | 313.7 | 877.3 | *(fails)* | 140.1 | 5 tables (`tags`, `cpu`, `mem`, `disk`, `net`); converter schema error |
| `tpch_skew` | 43.56 | 450.6 | 1,022.4 | 3.25 | 1,485.4 | Includes base `dbgen` plus Python skew pass |
| `clickbench` | 43.11 | 165.7 | 526.6 | 2.18 | 1,063.6 | Synthetic fallback mode |
| `ssb` | 34.99 | 279.2 | 581.6 | 2.09 | 1,257.5 | Scalar Python `random.Random(42)` |
| `vector_search` | 34.54 | 189.7 | 1,279.9 | *(fails)* | 202.3 | Converter fails on CSV header row |
| `coffeeshop` | 28.95 | 166.2 | 858.4 | *(fails)* | 141.9 | Converter ignores comma delimiter |
| `joinorder_synthetic` | 17.48 | 3,133.1 | 432.2 | *(fails)* | 141.0 | Highest generator RSS; converter schema error |
| `amplab` | 15.84 | 1,422.6 | 648.3 | 1.45 | 695.2 | Second-highest generator RSS |
| `tpch` | 12.88 | 301.8 | 1,041.5 | 3.34 | 1,478.4 | Bundled `dbgen` (SF 10: 111.65 s, 301.8 MiB RSS, 10,629 MiB out) |

At SF 10, `tpcds`, `ssb`, `h2odb`, `tsbs_devops`, `vector_search`, `coffeeshop`,
`clickbench`, and `tpch_skew` all exceeded a 45 s per-step cap;
`joinorder_synthetic` and `amplab` (plus full-table `BaseFormatConverter`
Parquet conversion at 1.06–2.18 GiB SF 1 RSS) exceed the 8 GiB host memory
budget at SF 10.

### Empirical Validation Probes (2026-11-23)

1. **Upstream `tpcgen-cli` 4.0.0 (`datafusion-contrib/tpcgen-rs`, Apache-2.0):**
   - **TPC-H (SF 0.01, 0.1, 1.0):** All 8 tables are 100% SHA-256 byte-identical
     to bundled `dbgen` at both `--num-threads 1` and default threads once the
     trailing `|` terminator is stripped from `tpcgen-cli` output. Bidirectional
     DuckDB `EXCEPT ALL` on Parquet returns 0 rows across all 8 tables. At SF 1,
     delimited generation drops from 12.88 s to 1.34 s (9.4x faster, 932 MiB
     RSS; 1 thread: 3.39 s, 529 MiB RSS) and direct Parquet takes 2.52 s
     (616 MiB RSS).
   - **TPC-DS (SF 1.0, `--compat c`):** All 24 tables are 100% SHA-256
     byte-identical to bundled `dsdgen` at both 1 thread and default threads,
     with 0-row bidirectional DuckDB `EXCEPT ALL` on Parquet. Delimited
     generation drops from 55.71 s to 0.91 s (61–64x faster, 156 MiB RSS; 1
     thread: 5.13 s, 31 MiB RSS) and direct Parquet takes 1.05 s (586 MiB RSS).
   - **TPC-DS fractional sub-scales (`SF < 1.0`):** At SF 0.01 and SF 0.1,
     `tpcgen-cli` 4.0.0 uses a different fractional scaling curve than
     BenchBox's patched `dsdgen` (17/24 tables differ at SF 0.01, 15/24 at
     SF 0.1; for example `customer_demographics` stays at 1,920,800 rows and
     `store_sales` has 120,527 vs 28,810 rows). Only the 7 fixed-size tables
     match byte-for-byte.
   - **Framing, streaming, parts, refresh sets, and wheels:**
     - `--stdout` output is SHA-256 identical to file output, allowing
       single-pass trailing-`|` stripping and compression without a second disk
       pass.
     - `normalize_tbl_trailing_delimiters()` in `benchbox/core/tpch/generator.py`
       inspects only the last 3 bytes of a file, which corrupts tables whose
       final column can be empty (such as TPC-DS `time_dim.dat`). The adapter
       must strip trailing `|` unconditionally from `tpcgen-cli` output only.
     - `--parts N` writes nested `<table>/<table>.<part>.<ext>` paths and
       defaults to 25 TPC-DS tables (including `dbgen_version`). The adapter
       must select only the 24 benchmark tables and map chunk paths to
       BenchBox's flat layout.
     - `tpcgen-cli` 4.0.0 has no TPC-H `RF1`/`RF2` or TPC-DS `-update` refresh
       options and publishes no Windows arm64 wheel (13 wheels cover macOS
       arm64/x86_64, Linux glibc/musl, and Windows x86/x86_64).
   - **Parquet layout and timing impact:** BenchBox's converter wrote 1 row
     group per table (8 RGs / 303 MiB TPC-H; 24 RGs / 383 MiB TPC-DS), whereas
     `tpcgen-cli` wrote 7 MiB row groups (94 RGs / 345 MiB TPC-H; 72 RGs /
     399 MiB TPC-DS) with Arrow logical-type differences (`string_view` vs
     `string`, `int32` vs `int64`, and TPC-DS `decimal128(7,2)` vs
     `decimal128(15,2)`). On identical values (identical `result_digest` on
     both engines), Parquet-scan timings shifted **4.6x–9.4x** (DuckDB Q1
     141.4 ms to 25.5 ms; DataFusion Q6 171.6 ms to 18.2 ms; DuckDB
     `store_sales` aggregation 46.4 ms to 7.9 ms).
2. **Vectorized Python prototype on `h2odb` (slowest synthetic generator):**
   - Chunked NumPy + PyArrow streaming (250,000 rows per chunk, per-chunk
     deterministic `np.random.default_rng([seed, chunk_idx])`, exact schema
     match against `benchbox/core/h2odb/schema.py`) generated SF 1 (10M rows)
     in **6.20 s** to Parquet (**20.3x faster** than 126.18 s legacy gen +
     convert) at **388.6 MiB** peak RSS (**5.6x lower** than legacy conversion),
     and **7.09 s** to `.tbl` (**17.0x faster**, 345.6 MiB RSS).
   - At SF 10 (100M rows, 5.76 GiB Parquet / 18.41 GiB `.tbl`), it completed in
     **62.14 s** (Parquet) and **71.83 s** (`.tbl`) with flat O(1) memory
     (**395.8 MiB** / **343.7 MiB** peak RSS — only +7 MiB over SF 1).

## Decision

1. **Extend the runtime ADR to TPC generation via upstream `tpcgen-cli`.**
   Adopt published `tpcgen-cli` wheels (pinned by version and hashes in
   `uv.lock`) for TPC-H (all supported scales) and TPC-DS (`SF >= 1.0`, integer
   scales) through an opt-in subprocess adapter. Do not create a wrapper Rust
   repository for TPC generation. Keep `qgen`, `dsqgen`, `tpcds.idx`, and query
   templates in `benchbox`, and keep bundled `dbgen`/`dsdgen` for fractional
   TPC-DS scales (`SF < 1.0`), refresh sets (`RF1`/`RF2`, TPC-DS `-update`),
   and platforms without a `tpcgen-cli` wheel (Windows arm64).
2. **Upgrade format conversion to streaming first.**
   Replace full-table `pyarrow.csv.read_csv` + `pa.concat_tables` materialization
   in `benchbox/utils/format_converters/base.py` and `parquet_converter.py` with
   streaming batch conversion and inline row counting, and fix the four
   existing `FormatConversionOrchestrator` incompatibilities (`tsbs_devops`,
   `vector_search`, `coffeeshop`, `joinorder_synthetic`).
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
4. **Backend selection and missing-extra default rule.**
   - Expose `--datagen-backend legacy|native` on `benchbox datagen` and
     `benchbox run` backed by a per-benchmark default table in
     `benchbox/utils/datagen_backend.py` (initialized with every benchmark on
     `legacy`). Do not add an `auto` mode.
   - Explicit `--datagen-backend native` **always fails loudly** with an
     actionable install/fix message if `native-datagen` is not installed, the
     platform lacks a wheel, or the benchmark/scale is unsupported (for
     example, `tpcds` at `SF < 1.0`). Never silently fall back on an explicit
     request.
   - When `--datagen-backend` is omitted (default resolution):
     - **In-tree vectorized Python generators** (synthetic benchmarks and SSB)
       need no optional extra; once validated and promoted to default, they run
       unconditionally, with `--datagen-backend legacy` preserved as an escape
       hatch for at least one minor release.
     - **Byte-identical delimited TPC generation (`tpch` all scales, `tpcds`
       `SF >= 1.0`):** once promoted to default, BenchBox runs `tpcgen-cli`
       when `native-datagen` is installed on a supported platform/scale and
       uses the byte-identical bundled C binary when the extra is absent (or on
       Windows arm64, `tpcds` `SF < 1.0`, or refresh streams). Because both
       emit byte-identical delimited data, dataset content never depends on
       installed extras.
     - **Non-identical outputs** (direct `tpcgen-cli` Parquet, or any future
       optional native synthetic generator) never activate implicitly from an
       installed extra; they require explicit selection or an owner-approved
       default cutover with distinct provenance.
5. **Per-benchmark versioning, folder identity, and Parquet layout policy.**
   - **Never bump global `DATA_GENERATION_VERSION`** in
     `benchbox/utils/datagen_version.py` for individual generator changes,
     because it feeds `compute_base_constants_hash()` for every benchmark and
     would invalidate all cached datasets. Use a per-benchmark generator
     version registry instead.
   - Include `generator_backend`, `generator_backend_version`, per-benchmark
     version, and Parquet-writer identity in `compute_datagen_identity_hash()`.
   - Byte-identical delimited backends may share `datagen/<benchmark>_<sf>`.
     Non-identical backends (direct native Parquet, rewritten synthetic/SSB
     generators) must be distinguished by `manifest_datagen_is_current()` and
     isolated from shared caches during validation.
   - Keep BenchBox's streaming `ParquetConverter` as the default for published
     runs while building and validating direct `tpcgen-cli` Parquet in
     parallel (opt-in, recorded in `data_generation_hash`). Any switch of the
     published-run Parquet default requires a separate post-validation owner
     decision and documented re-baseline because writer row-group layout shifts
     Parquet-scan timings by 4.6x–9.4x on identical values.
6. **Vectorize over-budget synthetic generators and SSB in Python first; gate
   any Rust repository on measured failure.**
   - Re-implement over-budget synthetic benchmarks (`h2odb`, `tsbs_devops`,
     `clickbench` synthetic, `tpch_skew`, `vector_search`, `coffeeshop`,
     `joinorder_synthetic`, `amplab`) one benchmark at a time in vectorized
     NumPy/PyArrow or embedded DuckDB SQL with explicit deterministic seeding
     and per-benchmark versioning.
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

### Per-Benchmark Target Budgets (macOS arm64 / Linux x86_64 reference)

| Subsystem / Benchmark | SF 1 Wall Budget | SF 1 Peak RSS Budget | SF 10 Peak RSS Budget | Baseline Reference (SF 1) |
| :--- | ---: | ---: | ---: | :--- |
| Streaming `ParquetConverter` (all benchmarks) | <= baseline | <= 512 MiB | <= 1,024 MiB | 1,064–2,184 MiB RSS (OOM > 8 GiB at SF 10) |
| `tpch` (`tpcgen-cli` delimited / Parquet) | <= 3 s / <= 5 s | <= 1,024 MiB | <= 2,048 MiB | 12.88 s gen + 3.34 s conv (measured native: 1.34 s / 2.52 s) |
| `tpcds` `SF >= 1` (`tpcgen-cli` delimited / Parquet) | <= 3 s / <= 5 s | <= 1,024 MiB | <= 2,048 MiB | 55.71 s gen + 5.37 s conv (measured native: 0.91 s / 1.05 s) |
| `h2odb`, `tsbs_devops`, `clickbench` (synthetic), `ssb`, `vector_search`, `coffeeshop` | <= 15 s (target <= 10 s) | <= 512 MiB | <= 512 MiB | 28.95–120.62 s (prototype `h2odb`: 6.20 s pq, 389 MiB) |
| `tpch_skew` | <= 15 s | <= 512 MiB | <= 1,024 MiB | 43.56 s gen, 451 MiB RSS |
| `amplab`, `joinorder_synthetic` | <= 15 s | <= 512 MiB | <= 1,024 MiB | 15.84 s / 1,423 MiB (`amplab`); 17.48 s / 3,133 MiB (`joinorder_synthetic`) |

### Mandatory Validation Gates

No benchmark may flip its default until corpus `data_generation_hash` export
gaps are closed and all applicable gates pass on the pinned version across
every supported CI platform:

- **Structure gate (all benchmarks, all backends):** Output passes
  `BenchmarkDataValidator`, matches `schema.py` column names/order/types,
  matches exact row counts (SF 0.01 and 0.1 on pull requests; SF 1 and 10 on
  pin bumps), and recovers cleanly from an interrupted `_generation_in_progress`
  marker.
- **Exact parity gate (TPC-H and TPC-DS `SF >= 1.0`):** SHA-256 byte identity
  of delimited output against bundled `dbgen`/`dsdgen` after unconditional
  trailing-`|` stripping on `tpcgen-cli` output (`--num-threads 1` and
  default); bidirectional DuckDB `EXCEPT ALL` = 0 rows on Parquet; secondary
  single-engine `result_digest.py` verification on the Parquet path; TPC-DS
  NULL vs empty-string and UTF-8 parity under `--compat c`; and validation of
  all 7 derived TPC consumers (`tpchavoc`, `read_primitives`,
  `write_primitives`, `transaction_primitives`, `datavault`, `tpcds_obt` smoke
  suites plus `ai_primitives` datagen/dry-run). Runs as a required
  path-filtered CI check on changes to the adapter, TPC generators, or pin.
- **Rewritten synthetic and SSB acceptance gate:** Exact table row counts, zero
  orphan foreign keys, documented hit rates, a one-time owner-approved
  acceptance report comparing legacy vs new distributions and every benchmark
  query's row count across DuckDB and DataFusion, followed by frozen golden
  per-table hashes and expected row counts. Never use arbitrary tolerance bands
  on deterministic output.
- **Performance and memory gate:** Wall time (`mono_time()`) and peak RSS
  measured under `make heavy-run` meeting the table above.

## Consequences

- TPC-H and TPC-DS (`SF >= 1.0`) gain 9x–64x generation speedups using an
  upstream Apache-2.0 wheel with zero custom Rust code to maintain.
- Streaming Parquet conversion removes the >2 GiB SF 1 / >8 GiB SF 10 memory
  wall for every backend and preserves published-run Parquet-scan timing
  comparability.
- Vectorized Python/PyArrow delivers ~20x speedups and flat ~390 MiB RSS up to
  SF 10 (100M rows) on numeric synthetic generators, making a standalone Rust
  repository unnecessary unless a string-heavy or complex FK-graph generator
  later misses budget.
- Bundled C binaries remain for `qgen`/`dsqgen`/templates, TPC-DS fractional
  scales (`SF < 1.0`), refresh sets (`RF1`/`RF2`, `-update`), and Windows arm64
  until upstream coverage or an explicit owner retirement decision changes that
  boundary.
