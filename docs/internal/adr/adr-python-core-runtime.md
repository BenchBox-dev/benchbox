<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# ADR: Retain Python as BenchBox's core runtime

## Status

Accepted (2026-11-23). Governs the core orchestration, adapter, CLI, and SQL
compatibility architecture under `benchbox/`.

## Context

BenchBox is an integration-breadth benchmarking harness spanning 50+ active
platforms (`benchbox/core/platform_manifest.py`), 20+ benchmarks
(`benchbox/core/benchmark_registry.yaml`), DataFrame and SQL execution modes,
cloud-Spark job runners, platform-specific query-plan parsers
(`benchbox/core/query_plans/parsers/`), and a vendored SQLGlot fork
(`benchbox/_vendor/`, `BenchBox-dev/benchbox-sqlglot`) governed by 100+
`sql_compat` rules (`benchbox/sql_compat/rules/`).

We evaluated replacing the Python core with a standalone Rust or Go core, or
converting BenchBox into a DuckDB extension, to pursue single-binary
distribution, faster startup, and lower runtime overhead.

## Decision

**Retain Python as BenchBox's core and orchestration runtime.** Do not port the
core harness to Rust, Go, or a DuckDB extension.

Keep the core package pure Python (`py3-none-any`), while permitting targeted
native acceleration strictly behind narrow data-generation boundaries in the
future.

## Why Ports to Rust, Go, or a DuckDB Extension Fail

Benchmark execution time is dominated by the target engine (C++, Rust, JVM, or
cloud warehouse), not Python orchestration. Porting the core trades away
BenchBox's primary advantages for negligible runtime gain:

1. **Loss of Python platform drivers (BenchBox's primary moat).** Vendors ship
   first-class Python clients first, and many targets *are* Python runtimes
   (Polars, Pandas, Dask, cuDF, PySpark, DataFusion DF, Snowpark Connect,
   `chdb`, and version-isolated drivers managed by
   `benchbox/utils/runtime_env.py`). ADBC covers SQL execution for a subset of
   engines, but does not replace DataFrame APIs, cloud-Spark job submission,
   cloud object staging (`PUT`/`COPY`), or platform-specific server telemetry
   (`platform_timing`, `QUERY_HISTORY`, `job_statistics`, `system.query_log`).
2. **The `benchbox-sqlglot` trap.** Cross-dialect query and DDL translation
   relies on `BenchBox-dev/benchbox-sqlglot` plus custom AST rewrites and the
   `sql_compat` governance registry. Leaving translation in Python forces every
   query and DDL statement across an FFI or subprocess IPC boundary—keeping a
   Python runtime dependency anyway. Rewriting it onto younger third-party
   ports (`polyglot-sql`, `sqlglot-go`) creates a second custom transpiler fork
   to maintain without addressing a real performance bottleneck.
3. **Fatal tradeoffs specific to each native target:**
   - **Rust core:** Slows iteration across dozens of platform adapters (compile times,
     borrow-checker and async friction, dual-toolchain CI, and cross-OS/arch
     `maturin`/wheel matrices if hybrid via PyO3).
   - **Go core:** Faster to compile and strong for networked SQL and cloud
     SDKs, but loses pure-Go (`CGO_ENABLED=0`) cross-compilation as soon as
     DuckDB (`duckdb-go`) or C/Rust ADBC shared-library drivers enter. Unlike
     Rust's PyO3, Go has no clean in-process Python embedding (GC and scheduler
     vs. GIL), forcing an all-or-nothing rewrite or brittle subprocess RPC.
   - **DuckDB extension:** Contaminates measurements when benchmarking DuckDB
     itself, blocks multi-version DuckDB comparisons, and binds distribution to
     DuckDB's per-OS/arch/ABI extension matrix.
4. **Maintainer velocity.** A single-language Python codebase (`uv`, `pytest`,
   `ruff`, `ty`, pure-Python wheel) is what makes maintaining 50+ platforms and
   rapidly tracking vendor changes sustainable.

## Future Path: Accelerating Data Generation

Synthetic data generation for non-TPC benchmarks (`ssb`, `clickbench`,
`amplab`, `tsbs_devops`, `coffeeshop`, `h2odb`, `vector_search`) is the one
CPU-bound subsystem where pure Python can become a bottleneck at large scale
factors. TPC-H and TPC-DS already delegate to precompiled C binaries
(`dbgen`/`dsdgen` in `benchbox/_binaries/`), and TPC-DI delegates to the
official `DIGen` generator.

If a non-TPC generator hits a measured throughput or memory wall, accelerate it
in this order:

1. **Vectorized PyArrow/NumPy or embedded DuckDB SQL** inside Python (the
   pattern already used by `tpcds_obt` and `datavault`), keeping zero new build
   toolchains.
2. **A decoupled native generator package** (preferring Rust over Go so the
   same core crate can expose Python wheels via PyO3/Maturin and optionally a
   companion DuckDB extension via the C Extension API).

Any native generator must stay strictly behind a file/Arrow boundary:

```text
(benchmark, scale_factor, seed, format, output_dir) -> generated data files + manifest
```

It must carry **zero** dependency on `PlatformAdapter`, `benchbox-sqlglot`, or
result orchestration, and remain optional or cleanly isolatable so core Python
development stays fast.

## Consequences

- BenchBox remains a Python-first library and CLI (`uvx` and `uv tool install`
  address end-user install ergonomics without changing languages).
- Platform adapters continue using official vendor Python SDKs, DataFrame APIs,
  ADBC drivers where beneficial (for example, GizmoSQL), and isolated venv
  runtimes.
- **Re-evaluation trigger:** Revisit only if BenchBox intentionally drops
  DataFrame and Python-embedded targets, or if a stable, feature-complete
  non-Python SQL transpiler replaces `benchbox-sqlglot` without losing dialect
  coverage.
