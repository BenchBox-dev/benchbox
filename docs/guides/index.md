<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Guides

```{tags} guide
```

Practical how-to guides for accomplishing specific tasks with BenchBox.

## TPC Benchmarks

Procedures for running TPC-derived benchmarks. Results are derived from TPC benchmarks and do not comply with the TPC specifications:

- [TPC Guides Overview](tpc/index.md) - Index of all TPC-specific documentation
- [TPC-H Official Guide](tpc/tpc-h-official-guide.md) - Complete TPC-H benchmark procedure
- [TPC-DS Official Guide](tpc/tpc-ds-official-guide.md) - Complete TPC-DS benchmark procedure
- [TPC-DI Deployment Guide](tpc/tpc-di-deployment-guide.md) - TPC-DI setup and deployment
- [TPC-DI ETL Guide](tpc/tpc-di-etl-guide.md) - TPC-DI ETL implementation
- [Maintenance Phase Guide](tpc/maintenance-phase-guide.md) - Data modification operations
- [TPC Validation Guide](tpc/tpc-validation-guide.md) - Result validation procedures

## Performance & Optimization

Guides for getting the best performance from your benchmarks:

- [Performance Optimization](../advanced/performance-optimization.md) - Comprehensive tuning guide
- [Power Run & Concurrent Queries](../advanced/power-run-concurrent-queries.md) - Multi-stream execution
- [Optimizer Test Suites](../advanced/optimizer-tests.md) - Testing query optimizers
- [Performance Monitoring](../advanced/performance.md) - Tracking and profiling

## Data Formats & Storage

Working with different data formats and storage systems:

- [Table Format Guides](table-formats/index.md) - Conceptual guides for Parquet, Delta Lake, Iceberg, and Vortex
  - [Parquet Deep Dive](table-formats/parquet-deep-dive.md) - Row groups, compression, statistics <!-- content-ok: cliche -->
  - [Delta Lake Guide](table-formats/delta-lake-guide.md) - Transactions, OPTIMIZE, Z-ORDER
  - [Apache Iceberg Guide](table-formats/iceberg-guide.md) - Multi-engine support, partition evolution
  - [Vortex Guide](table-formats/vortex-guide.md) - Composable encodings, DuckDB integration
- [Format Conversion](../advanced/format-conversion.md) - CLI reference for format conversion
- [Cloud Storage](cloud-storage.md) - S3, GCS, and Azure Blob integration
- [Compression](compression.md) - Data compression strategies

## Platform Comparison

Guides for comparing benchmark performance across platforms:

- [Platform Comparison Guide](platform-comparison.md) - Unified comparison for SQL and DataFrame platforms
- [DataFrame Comparison Guide](dataframe-comparison.md) - DataFrame cross-platform comparison
- [DataFrame Benchmarking Guide](dataframe-migration.md) - Adopting DataFrame benchmarking

## Visualization

Creating charts and reports from benchmark results:

- [Visualization Overview](../visualization/overview.md) - Getting started with visualization
- [Chart Generation Guide](../visualization/chart-generation-guide.md) - Creating charts
- [Chart Types](../visualization/chart-types.md) - Available chart types
- [Customization](../visualization/customization.md) - Styling and theming
- [Templates](../visualization/templates.md) - Reusable chart templates

## Validation & Quality

Ensuring benchmark result accuracy:

- [Row Count Validation](row-count-validation.md) - Validate query results
- [TPC Patterns Usage](tpc/tpc-patterns-usage.md) - Common TPC usage patterns

## Integration

Integrating BenchBox into your workflow:

- [Hosted Result Submission](hosted-submission.md) - Submit a local result to
  the hosted results platform
- [MCP Integration](mcp-integration.md) - Use BenchBox with Claude Code and AI assistants
- [CI/CD Integration](../advanced/ci-cd-integration.md) - Automated benchmark pipelines
- [Custom Benchmarks](../advanced/custom-benchmarks.md) - Creating your own benchmarks

## Related Documentation

- [User Guide](../usage/index.md) - Daily usage reference
- [Reference Documentation](../reference/index.md) - Complete API and CLI reference
- [Troubleshooting](../usage/troubleshooting.md) - Common issues and solutions

```{toctree}
:maxdepth: 1

tpc/index
table-formats/index
platform-comparison
dataframe-comparison
dataframe-migration
row-count-validation
cloud-storage
compression
hosted-submission
mcp-integration
../features/query-plan-analysis
../performance/dataframe-benchmarks
../performance/dataframe-optimization
```
