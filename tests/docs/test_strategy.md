<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# BenchBox Test Strategy

## Overview

This document outlines the comprehensive testing strategy for the BenchBox library, a hermetic library designed to embed benchmark datasets and queries for database evaluation. The strategy follows Test-Driven Development (TDD) principles and aims to ensure high quality, reliability, and performance of the library across different database systems.

## Testing Objectives

1. Validate that the library meets all functional requirements
2. Ensure cross-database compatibility and consistent behavior
3. Verify performance characteristics for data generation and query execution
4. Confirm extensibility for custom benchmarks and database systems
5. Ensure code quality, maintainability, and documentation

## Test Levels

### Unit Testing

**Scope**: Individual classes, methods, and functions
**Focus**: Core functionality, edge cases, error handling
**Tools**: pytest, pytest-cov, pytest-mock
**Coverage Target**: ≥ 90% line coverage

#### Key Unit Testing Areas:
- Abstract base classes and interfaces (using mock implementations)
- Data generation components
- Query handling and translation
- SQL dialect transformation using sqlglot
- Utility functions and helpers

### Integration Testing

**Scope**: Interaction between components
**Focus**: Component interaction, workflow validation
**Tools**: pytest with fixtures, docker-compose for database instances
**Coverage**: All component interaction paths

#### Key Integration Testing Areas:
- Benchmark initialization and setup
- Data generation pipeline
- Query execution workflow
- Cross-component data flow
- Configuration management

### System Testing

**Scope**: End-to-end functionality of the library
**Focus**: Real-world usage scenarios
**Tools**: pytest, docker containers for database systems
**Coverage**: All supported benchmarks and databases

#### Key System Testing Areas:
- Complete benchmark executions
- Cross-database query execution
- Performance measurements
- Resource utilization
- Realistic data volumes (scaled-down)

### Performance Testing

**Scope**: Performance characteristics of critical operations
**Focus**: Execution time, memory usage, scalability
**Tools**: pytest-benchmark, memory-profiler
**Coverage**: Data generation, query execution, SQL translation

#### Performance Testing Areas:
- Data generation speed with various scale factors
- Query execution time across database systems
- Memory consumption for large datasets
- Scaling characteristics

## Testing Approaches

### Test-Driven Development (TDD)

1. **Write Tests First**: All features begin with test specification
2. **Implement Features**: Develop minimal code to pass tests
3. **Refactor**: Improve implementation while maintaining test compliance
4. **Iterate**: Expand tests and features incrementally

### Mock Testing

- Use pytest-mock for creating mock objects
- Create test doubles for database connections
- Simulate various database responses and errors
- Mock file systems and external resources

### Parametrized Testing

- Test with multiple database dialects
- Test with various data scale factors
- Test with different configuration settings
- Test with multiple query variations

### Property-Based Testing

- Use hypothesis for property-based testing
- Test data generation with various constraints
- Verify SQL translations across dialects
- Confirm invariants in query result processing

## Test Environments

### Local Development Environment

- pytest for running tests
- Pre-commit hooks for running tests before commits
- Containerized databases for integration testing

### Continuous Integration Environment

- Automated test execution on every pull request
- Matrix testing across Python versions (3.11, 3.12, 3.13, 3.14)
- Coverage reporting and enforcement
- Performance regression detection

## Test Data Management

- Small, fixed test datasets for unit tests
- Generated test data for integration tests
- Standard benchmark data at minimum scale factors
- Cross-database test data consistency validation

## Test Organization

### Directory Structure

```
tests/
├── unit/               # Unit tests for individual components
│   ├── core/           # Tests for core components
│   ├── benchmarks/     # Tests for specific benchmarks
│   ├── data_gen/       # Tests for data generation
│   └── query/          # Tests for query management
├── integration/        # Tests for component interactions
├── system/             # End-to-end tests
├── performance/        # Performance benchmarks
└── conftest.py         # Common test fixtures and utilities
```

### Naming Conventions

- Test files: `test_<module_name>.py`
- Test classes: `Test<ClassName>`
- Test methods: `test_<functionality>_<scenario>`
- Fixtures: `<resource_type>_<characteristics>`

## Test Monitoring and Reporting

- Test coverage reports using pytest-cov
- Performance benchmark history
- Test execution time tracking
- Failure analysis and categorization

## Continuous Testing Workflow

1. **Pre-commit Testing**: Run unit tests and linting
2. **Pull Request Testing**: Run full test suite including integration tests
3. **Nightly Testing**: Run complete system and performance tests
4. **Release Testing**: Comprehensive testing across all supported configurations

## Defect Management

- All identified issues must have a corresponding test case
- Regression tests must be created for every fixed bug
- Test failure triage process with priority classification
- Non-deterministic test identification and handling

## Test Documentation

- Put useful test purposes, scenario requirements, and fixture provenance in this
  guide or the [test suite guide](../README.md), following the
  [comment policy](../../docs/internal/comment-policy.md).
- Express test behavior through names, setup, and assertions; retained source
  notices and consumed directives require exact policy registrations.
- Test data generation must be documented for reproducibility

## Response to Test Results

- Failed tests block merging of pull requests
- Performance regressions trigger alerts
- Coverage decreases require justification
- Test flakiness triggers investigation

## Version-Specific Testing Considerations

- Testing against multiple versions of key dependencies
- Database-specific test adaptations
- Operating system-specific considerations
- Python version compatibility testing

This test strategy is a living document and will be updated as the project evolves and new testing needs are identified.

## Coverage Boundaries and Fixture Requirements

Keep these requirements with the tests when changing fixtures or moving coverage.
The linked modules define the executable cases; their directory alone does not
establish live-platform certification.

### Live Runtime Coverage

[Athena](../integration/platforms/test_athena_live.py) uses a real workgroup.
Set `ATHENA_REGION` and `ATHENA_S3_OUTPUT`, provide AWS credentials through the
normal provider chain, then run `make test-live LIVE_PLATFORM=athena`. `ATHENA_WORKGROUP`
defaults to `primary` and `ATHENA_DATABASE` to `default`. Connection, metadata,
and a simple query exercise the adapter; this suite does not establish a spending
budget.

[Redshift](../integration/platforms/test_redshift_live.py) uses a real cluster.
Configure credentials with `benchbox platforms setup --platform redshift` or set
`REDSHIFT_HOST`, `REDSHIFT_USER` (or `REDSHIFT_USERNAME`), and
`REDSHIFT_PASSWORD`. `REDSHIFT_DATABASE` defaults to `dev` and
`REDSHIFT_PORT` to 5439. Run `make test-live LIVE_PLATFORM=redshift`. COPY tests also
require an S3 staging location from saved configuration or `REDSHIFT_S3_BUCKET`,
AWS credentials for uploads, and a usable COPY role (`REDSHIFT_IAM_ROLE` or the
cluster default role). The staged COPY case writes two CSV chunks containing
four rows and verifies the loaded table.

[Iceberg](../integration/platforms/test_iceberg_format_live.py) exercises
PyIceberg maintenance and transaction operations with PyArrow: table metadata,
append/overwrite, row mutations, snapshots, and format validation. This is
PyIceberg coverage, not a Spark/Iceberg runtime check.

The Delta suites require PySpark, delta-spark, and compatible Java. The
[runtime suite](../integration/platforms/test_pyspark_delta_live.py) checks the
session, reads/writes, history, and SQL DDL/DML. The
[catalog suite](../integration/platforms/test_pyspark_delta_catalog_live.py)
runs portable SELECT/DELETE against seeded tables; it does not execute catalog
BEGIN/COMMIT/SAVEPOINT statements verbatim. The
[execution suite](../integration/platforms/test_pyspark_delta_execution_live.py)
drives `DataFrameTransactionOperationsManager` through mutations, rollback,
version history, and time travel. These suites carry `live_integration` and are
excluded from the default suite.

The [Lakesail stub smoke tests](../integration/platforms/test_lakesail_stub_smoke.py)
check Spark Connect wiring with fakes. Real server coverage belongs to the
[separate live suite](../integration/platforms/test_lakesail_live.py).
The [TPC generator integration tests](../integration/test_tpc_generator_binaries.py)
resolve bundled dbgen and dsdgen, then exercise production TPC-H generation,
repeatability, and loading with dbgen at scale 0.01. They skip when the required
executable is absent; resolving dsdgen does not establish TPC-DS generation coverage.

### Cross-Component Contracts

The [literal-normalization CLI tests](../unit/cli/test_normalize_plan_literals_flag.py)
verify option recognition and dry-run reproduction. Real runs keep `fingerprint`
and add the separate 64-character `fingerprint_normalized` field in the plans
companion only when the flag is enabled.

The [tuning marker round-trip tests](../unit/cli/test_tuning_marker_coverage_roundtrip.py)
feed actual `resolve_tuning()` information messages to `status_from_log_text()`.
Do not replace the producer side with hand-written marker strings: that would
miss wording drift between the resolver and coverage parser.
The [hash tests](../unit/core/tuning/test_hash_canonicality.py) distinguish the
64-character configuration identity from the 16-character template hash used
for compact display, and verify ordering stability and differing configurations.
These helpers are not interchangeable identities.

The [SQL/DataFrame mapping tests](../unit/core/equivalence/test_dataframe_id_mapping.py)
check NYC Taxi and TSBS builder maps and TPC-H, TPC-DS, and TPC-H Skew Q-prefix
mappings. Read Primitives separately pins four classified DataFrame-only queries
and 148 DuckDB/DataFrame overlaps. Unmapped query IDs must fail rather than
disappear from comparison.
The setup deliberately imports `benchbox.core.dataframe.benchmark_suite` before
benchmark mappings and TPC-H wrappers. This side-effect import was added for
TPC-H circular-import avoidance; its unused bound name does not make it dead
setup. Preserve that initialization order when changing the fixture.
[TPC-H Skew](../unit/core/tpch_skew/test_tpch_skew_id_mapping.py) maps inherited
SQL IDs 1–22 to Q1–Q22. [TSBS DevOps](../unit/core/tsbs_devops/test_tsbs_devops_id_mapping.py)
uses each SQL catalog entry's numeric `id` to connect its slug to QN; do not
infer the bridge from catalog position.

The [TPC-DI mutation tests](../unit/core/tpcdi/test_tpcdi_dataframe_mutation_execution.py)
run historical load, expiry, and insertion through `DataFrameETLBackend` and
`PolarsMaintenanceOperations`, then inspect stored Parquet state. Faked condition
rendering and table-root tests do not replace this execution check.
The [TPC-DS-OBT comparison](../unit/core/tpcds_obt/test_tpcds_obt_dataframe_execution.py)
uses ten deterministic rows for Q1–Q17 on both backends. Keep the fixture below
query LIMITs so tied groups are not truncated differently; comparisons ignore
row order but require equal contents.

The [Polars window and sorting regressions](../unit/platforms/dataframe/test_window_and_sort_fixes.py)
require lag/lead to sort by window ORDER BY before shifting, NTILE to distribute
SQL-style buckets, and `desc()` to act as a sort-direction marker rather than
reorder expression values.
The [shared staged-load tests](../unit/platforms/base/test_staged_table_loads.py)
preserve Redshift's lowercase and Snowflake's uppercase statistics keys, fail-fast
and timing behavior, and full failure text. The
[Spark external-table mixin suite](../unit/platforms/cloud_spark/test_spark_external_table_mixin.py)
uses a stub for staging, format selection, fresh upload/reuse, registration, and
row counts; platform-specific registration hooks need their adapter tests.
The [QuestDB DDL tests](../unit/platforms/test_questdb_schema_enhancement.py)
use representative column subsets for symbol/date conversion, designated
timestamps, and monthly partitions, not the complete shipped TPC-H DDL.

The [applied-receipt export tests](../unit/core/results/test_applied_receipt_export.py)
check receipt serialization and anonymization of free text and identifiers,
including raw statements, without mutating the input receipt.
The [Exasol translation tests](../unit/utils/test_exasol_identifier_policy.py)
keep ordinary identifiers unquoted for uppercase folding and reserved words
quoted, exercise both translation paths, and check other targets' behavior.

### Generator Fixture Provenance

The [TPC-DS parameter-log parser tests](../unit/tpcds/test_parameter_log.py)
use logs captured from bundled dsqgen at seed 7 and scale 1. Binary cases check
repeatability with the same seed, changes with a different seed, and reported
stream IDs; cross-platform value identity is separate coverage. The shortened
Q8 fixture and import-restoration boundaries are documented in
[the suite guide](../README.md#fixture-boundaries).

The [dsdgen framing check](../unit/core/tpcds/generator/test_tpcds_terminate_framing.py)
parses generator call sites and requires `-terminate n` for each invocation.
This fixes the no-trailing-separator convention at runtime, unlike dbgen's
compile-time `EOL_HANDLING`. It does not itself execute a generator or certify
byte identity across platforms.
