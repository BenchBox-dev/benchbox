<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Reference Documentation

```{tags} reference
```

Complete reference documentation for BenchBox APIs, CLI, and data formats.

## Command Line Interface

- [CLI Reference](cli/index.md) - Complete command-line interface documentation
  - [run](cli/run.md) - Primary benchmark execution command
  - [convert](cli/convert.md) - Convert data to Parquet, Vortex, Delta Lake, or Iceberg
  - [shell](cli/shell.md) - Interactive SQL shell
  - [platforms](cli/platforms.md) - Platform management
  - [results](cli/results.md) - Export, view, and compare results
  - [tuning](cli/tuning.md) - Tuning configuration commands
  - [utilities](cli/utilities.md) - Utility commands

## Python API

- [API Reference](api-reference.md) - Python API overview
- [Python API Documentation](python-api/index.md) - Detailed API reference
- [Public Contracts and Support Taxonomy](public-contracts.md) - Compatibility tiers, support status vocabulary, and source-of-truth map

## Integrations

- [MCP Server Reference](mcp.md) - Model Context Protocol server for AI assistants

## Data Formats & Schemas

- [Result Export Formats](result-formats.md) - Comprehensive guide to JSON, CSV, HTML exports
- [Tuning Platform Support](tuning-platform-support.md) - Which tuning types each platform applies and which can reach `applied_verified`
- [Result Schema v1.0](result-schema-v1.md) - Benchmark result JSON schema specification
- [Backward Compatibility Registry](backward-compatibility.md) - Lifecycle policy and shim tracking process

## Related Documentation

- [Usage Guides](../usage/index.md) - Practical usage examples
- [How-To Guides](../guides/index.md) - Task-oriented guides
- [Platform Documentation](../platforms/index.md) - Platform-specific reference

```{toctree}
:maxdepth: 2

cli/index
cli
api-reference
public-contracts
mcp
result-formats
tuning-platform-support
result-schema-v1
backward-compatibility
python-api/index
python-api/platforms
```
