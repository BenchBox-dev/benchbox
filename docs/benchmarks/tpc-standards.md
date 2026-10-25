---
myst:
  enable_extensions:
    - deflist
---

# TPC Standards

```{tags} reference, tpc-h, tpc-ds, tpc-di
```

Official industry standards for comparing databases.

## About the Transaction Processing Performance Council

The **Transaction Processing Performance Council (TPC)** is a non-profit organization founded in 1988 that defines database benchmarking standards and publishes independently verified performance results. TPC benchmarks are the gold standard for database performance comparison because they provide:

- **Standardized workloads** - Precisely defined queries, data schemas, and execution rules
- **Audited results** - Independent verification ensures published results are reproducible
- **Price/performance metrics** - Enables cost-effectiveness comparisons, not just raw speed
- **Full disclosure** - Complete system configurations must be published with results

## Why TPC Benchmarks Matter

When evaluating databases, TPC benchmarks provide several critical advantages:

**Apples-to-apples comparison**
: Unlike vendor-provided benchmarks that may be optimized for specific systems, TPC benchmarks use identical workloads across all platforms. This makes cross-platform comparison meaningful.

**Real-world complexity**
: TPC benchmarks model actual business scenarios with realistic data distributions, query complexity, and concurrency requirements. They test systems under conditions similar to production workloads.

**Industry acceptance**
: TPC results are widely recognized by analysts, procurement teams, and engineers. Published TPC results carry credibility that proprietary benchmarks cannot match.

**Historical tracking**
: With decades of results, TPC benchmarks enable tracking of performance improvements over time and across hardware generations.

## TPC Benchmarks in BenchBox

BenchBox implements three TPC benchmarks, each targeting different use cases:

```{list-table}
:header-rows: 1
:widths: 20 40 40

* - Benchmark
  - Focus
  - Best For
* - **TPC-H**
  - Decision support queries on business data
  - General OLAP performance, platform comparison
* - **TPC-DS**
  - Complex analytics with modern SQL features
  - Advanced analytics, query optimizer testing
* - **TPC-DI**
  - Data integration and ETL operations
  - ETL pipeline performance, data warehouse loading
```

### Note on Compliance

BenchBox implements TPC benchmark *workloads* for development and evaluation purposes. Official TPC compliance requires independent auditing and full disclosure reports. Results from BenchBox should be labeled as "TPC-H-like" or "derived from TPC-H" rather than official TPC results unless properly audited. See {doc}`tpc-trademarks` for what the TPC trademarks and derived query text mean for you.

## Included Benchmarks

```{toctree}
:maxdepth: 1

tpc-h
tpc-ds
tpc-di
tpc-trademarks
```

## See Also

- [TPC Organization](http://www.tpc.org/) - Official TPC website with specifications and published results
- {doc}`../guides/tpc/tpc-h-official-guide` - Running TPC-H derived benchmarks
- {doc}`../guides/tpc/tpc-ds-official-guide` - Running TPC-DS derived benchmarks
