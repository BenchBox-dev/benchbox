<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# TPC Benchmark Guides

```{tags} guide, tpc-h, tpc-ds
```

Comprehensive guides for running official TPC benchmarks with BenchBox.

## Official Benchmark Guides

### TPC-H
- [TPC-H Official Benchmark Guide](tpc-h-official-guide.md) - Complete guide for running TPC-H derived benchmarks

### TPC-DS
- [TPC-DS Official Benchmark Guide](tpc-ds-official-guide.md) - Complete guide for running TPC-DS derived benchmarks

### TPC-DI
- [TPC-DI Deployment Guide](tpc-di-deployment-guide.md) - Deployment and configuration for TPC-DI
- [TPC-DI ETL Guide](tpc-di-etl-guide.md) - Extract, Transform, Load processes for TPC-DI

## Cross-Cutting Guides

- **[Maintenance Phase Guide](maintenance-phase-guide.md)** - **⚠️ CRITICAL:** Complete guide for TPC Maintenance Tests, database reload requirements, and proper workflow sequences (TPC-H and TPC-DS)

## Additional Resources

- [TPC Patterns Usage](tpc-patterns-usage.md) - Common patterns and best practices for TPC benchmarks
- [TPC Validation Guide](tpc-validation-guide.md) - Validation strategies for checking results against TPC specification requirements

## Related Documentation

- [Benchmarks Overview](../../benchmarks/index.md) - All available benchmarks
- [Row Count Validation](../row-count-validation.md) - Automated result validation
- [Performance Optimization](../../advanced/performance-optimization.md) - Tuning and optimization techniques

## TPC Resources

- [TPC Official Website](https://www.tpc.org/)
- [TPC-H Specification](https://www.tpc.org/tpch/)
- [TPC-DS Specification](https://www.tpc.org/tpcds/)
- [TPC-DI Specification](https://www.tpc.org/tpcdi/)

```{toctree}
:maxdepth: 1
:caption: TPC Guides
:hidden:

tpc-h-official-guide
tpc-ds-official-guide
tpc-di-deployment-guide
tpc-di-etl-guide
maintenance-phase-guide
tpc-patterns-usage
tpc-validation-guide
```
