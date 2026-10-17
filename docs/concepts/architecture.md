<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# BenchBox Architecture

```{tags} concept, intermediate, python-api
```

High-level overview of BenchBox's design, components, and extension points.

## Design Philosophy

BenchBox is built on three core principles:

1. **Separation of Concerns**: Benchmark logic, platform adapters, and query execution are independent layers
2. **Extensibility**: New benchmarks and platforms can be added without modifying core code
3. **Standards Compliance**: TPC benchmarks follow official specifications for reproducibility

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                       CLI / Python API                       │
│                    (User Entry Points)                       │
└──────────────────┬──────────────────────────────────────────┘
                   │
┌──────────────────┴──────────────────────────────────────────┐
│                   Orchestration Layer                        │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Run Configuration   │   Query Selection             │  │
│  │  Parameter Generation│   Result Collection           │  │
│  └──────────────────────────────────────────────────────┘  │
└──────────────────┬──────────────────────────────────────────┘
                   │
     ┌─────────────┼─────────────┐
     │             │             │
┌────▼────┐  ┌────▼────┐  ┌────▼────┐
│Benchmark│  │Platform │  │ Results │
│  Layer  │  │ Adapter │  │  Model  │
└─────────┘  └─────────┘  └─────────┘
     │             │             │
┌────▼────────────▼─────────────▼────┐
│       Database Connection          │
│     (Platform-Specific Driver)     │
└────────────────────────────────────┘
```

## Repository map

The repository separates the installable product from its documentation,
tests, examples, and project operations:

| Path | Purpose |
| --- | --- |
| `benchbox/` | Python package, benchmark implementations, platform adapters, CLI, and result model |
| `docs/` | User guides, concepts, reference material, design records, and contributor documentation |
| `tests/` | Unit, integration, end-to-end, performance, and live test suites |
| `examples/` | Runnable examples, notebooks, tuning files, and workflow patterns |
| `results-data/` | Public result corpus and its validation metadata |

This map describes ownership boundaries rather than every directory. Use the
linked component documentation below for implementation details.

## Core Components

### 1. Benchmark Layer

**Location**: Public wrappers live in `benchbox/*.py` on `benchbox.base.BaseBenchmark`.
Core implementations live under `benchbox/core/{benchmark}/`.

**Responsibility**: Encapsulates benchmark-specific logic

**Key Classes**:
- `BaseBenchmark`: Public wrapper base in `benchbox/base.py` for benchmark
  construction and the `run_with_platform` API. It remains a supported public
  base; it is not a retired execution model.
- `{Benchmark}`: Implementation class (e.g., `TPCH`, `TPCDS`)
- `{Benchmark}Generator`: Data generation logic
- `{Benchmark}Queries`: Query templates and parameterization

A benchmark class knows how to generate data (the `dbgen` invocation), how to retrieve queries (with parameter
substitution), its schema definitions, and its validation rules.

**Example**:
```python
from benchbox import TPCH
```

See: [Custom Benchmarks Guide](../advanced/custom-benchmarks.md)

### 2. Platform Adapter Layer

**Location**: `benchbox/platforms/{platform}/`

**Responsibility**: Abstracts database-specific connection and execution logic

**Key Classes**:
- `{Platform}Adapter`: Main adapter class (e.g., `DuckDBAdapter`)
- `{Platform}Connection`: Connection wrapper implementing `DatabaseConnection`
- Platform-specific optimizations (bulk loading, query hints)

An adapter handles connection management, data loading strategies (COPY, INSERT, external tables), query execution and
error handling, and result collection and formatting.

**Example**:
```python
from benchbox.platforms.duckdb import DuckDBAdapter
```

**Supported Adapters**:
- DuckDB (embedded, local files)
- ClickHouse (native protocol)
- Databricks (SQL Warehouse, Unity Catalog)
- BigQuery (serverless, cloud storage)
- Snowflake (warehouse, stages)
- Redshift (serverless, provisioned)
- SQLite (testing, minimal datasets)

See: [Platform Selection Guide](../platforms/platform-selection-guide.md)

### 3. Results Model

**Location**: `benchbox/core/results/`

**Responsibility**: Structured representation of benchmark execution results

**Key Classes**:
- `QueryResult`: Schema-facing query row model used by compatibility and
  validation boundaries (`benchbox/core/schemas.py`); it is not the canonical
  in-memory execution object.
- `QueryExecution`: Canonical in-memory result for one executed query
  (`benchbox/core/results/models.py`), including timing, status, validation,
  resource usage, and optional plan data.
- `BenchmarkResults`: Aggregate benchmark result with timing, metadata,
  validation, and query-result summaries.
- `ExecutionPhases`: Setup, power test, throughput test phases
- `ValidationResult`: Data quality and correctness checks

**Schema**:
```python
{
    "benchmark_name": "TPC-H",
    "platform": "DuckDB",
    "scale_factor": 1.0,
    "execution_id": "tpch_1234567890",
    "timestamp": "2025-10-12T10:30:00Z",
    "total_execution_time": 45.2,
    "query_results": [
        {
            "query_id": "q1",
            "execution_time": 2.1,
            "status": "SUCCESS",
            "row_count": 4
        }
    ],
    "execution_phases": { ... },
    "validation_status": "PASSED"
}
```

See: [Result Schema Reference](../reference/result-schema-v1.md)

### 4. Connection Abstraction

**Location**: `benchbox/core/connection.py`

**Responsibility**: Unified interface for database operations

**Key Interface**:
```python
class DatabaseConnection(ABC):
    @abstractmethod
    def execute(self, query: str) -> Any:
        pass

    @abstractmethod
    def fetchall(self, cursor) -> list:
        pass

    @abstractmethod
    def close(self) -> None:
        pass
```

`execute` runs a query and returns a cursor or result, `fetchall` fetches all results from a cursor, and `close` closes the connection. All platform adapters implement this interface, enabling benchmark code to remain platform-agnostic.

### 5. Data Generation

**Location**: `benchbox/core/{benchmark}/generator.py`

**Responsibility**: Create synthetic benchmark data

**Mechanisms**:
- **TPC Benchmarks**: Invoke official C binaries (`dbgen`, `dsdgen`, `datagen`)
- **Custom Benchmarks**: Python-based generation using Faker, NumPy, Pandas
- **Output Formats**: Parquet (default), CSV, JSON

**Example**: TPC-H uses the official `dbgen` binary, and `generate()` returns a list of `.parquet` files. Custom
benchmarks use Python, and the coffee shop generator generates data with Faker.
```python
generator = TPCHGenerator(scale_factor=1.0, output_dir="./data")
file_paths = generator.generate()

generator = CoffeeShopGenerator(scale_factor=0.001)
file_paths = generator.generate()
```

See: [Data Generation Guide](../usage/data-generation.md)

### 6. CLI Orchestration

**Location**: `benchbox/cli/`

**Responsibility**: Command-line interface and workflow orchestration

**Commands**: the examples run a benchmark end-to-end, generate data only, do a dry run that previews queries, and check
dependencies.
```bash
benchbox run --benchmark tpch --platform duckdb --scale 1

benchbox datagen --benchmark tpcds --scale 0.1

benchbox run --benchmark tpch --dry-run ./output

benchbox check-deps --matrix
```

**Orchestrator Flow**:
1. Parse CLI arguments and config files
2. Validate platform dependencies
3. Initialize benchmark and platform adapter
4. Execute benchmark phases (setup, queries, validation)
5. Collect and save results
6. Generate reports

See: [CLI Quick Start](../usage/cli-quick-start.md)

## Data Flow

### End-to-End Execution Flow

```
1. User Command
   ├── benchbox run --benchmark tpch --platform duckdb --scale 1
   └── Parsed by CLI → creates RunConfiguration

2. Benchmark Initialization
   ├── TPCH(scale_factor=1.0)
   ├── Checks if data exists at output_dir
   └── generate_data() if needed

3. Platform Adapter Setup
   ├── DuckDBAdapter()
   ├── Establishes database connection
   └── Creates schema (CREATE TABLE statements)

4. Data Loading Phase
   ├── Platform-specific bulk load (COPY FROM, external tables)
   ├── Validates row counts
   └── Creates indexes/constraints

5. Query Execution Phase
   ├── For each query in benchmark:
   │   ├── Get query text with parameters
   │   ├── Execute via platform adapter
   │   ├── Measure execution time
   │   └── Collect results
   └── Aggregate timing statistics

6. Results Collection
   ├── Create BenchmarkResults object
   ├── Include execution metadata (platform info, system profile)
   ├── Validate results (if validation rules exist)
   └── Save to JSON file

7. Output
   ├── Print summary to console
   ├── Save detailed JSON to output_dir
   └── Optional: Upload to cloud storage
```

## Extension Points

### Adding a New Benchmark

1. Create `benchbox/core/{benchmark}/` directory
2. Implement classes:
   - `{Benchmark}BenchmarkImpl(BaseBenchmark)`
   - `{Benchmark}Generator`
   - `{Benchmark}Queries`
   - `{Benchmark}Schema`
3. Register in `benchbox/__init__.py`

See: [Custom Benchmarks Guide](../advanced/custom-benchmarks.md)

### Adding a New Platform

1. Create `benchbox/platforms/{platform}/` directory
2. Implement:
   - `{Platform}Adapter`
   - `{Platform}Connection(DatabaseConnection)`
3. Add platform extras to `pyproject.toml`
4. Add the platform's canonical entry to `benchbox/core/platform_manifest.py`,
   including one explicit `support_status`. The registry's `_OPTIONAL_ADAPTERS`
   and `_build_platform_metadata()` paths are live pass-throughs, not
   independent registration authorities.

#### External Table Support

Platforms can optionally support `--table-mode external` by setting
`supports_external_tables = True` and implementing `create_external_tables()`.
In external mode the runner skips native schema creation and `load_data()`,
calling `create_external_tables()` instead to register views or external table
definitions over staged Parquet files.

Shared logic for Hive-style external tables (Trino, Presto) lives in
`HiveExternalTableMixin` (`benchbox/platforms/base/external_table_mixin.py`).
Shared logic for managed Spark external tables (Athena Spark, EMR Serverless,
Dataproc Serverless, Glue) lives in `SparkExternalTableMixin`
(`benchbox/platforms/base/cloud_spark/external_tables.py`).
Platforms that need pre-flight validation (e.g. Athena checking for an S3 bucket)
can implement `validate_external_table_requirements()`, which the runner calls
before proceeding.

See: [Adding New Platforms](../development/adding-new-platforms.md)

### Adding Query Parameter Variants

TPC benchmarks support query variants with different parameter substitutions. The first call returns a query with
random parameters (the default), the second returns a query with specific parameters, and the third generates multiple
variants for a seed sweep:

```python
query = benchmark.get_query("q1")

query = benchmark.get_query("q1", params={"date": "1998-09-02", "quantity": 24})

variants = benchmark.generate_query_variants("q1", count=5, seed_start=42)
```

See: [TPC Patterns Usage](../guides/tpc/tpc-patterns-usage.md)

## Design Patterns

### 1. Adapter Pattern

Platform adapters isolate database-specific logic, allowing benchmarks to remain platform-agnostic.

### 2. Benchmark Lifecycle

The lifecycle is orchestrated by the core run service and lifecycle runner:
configuration and platform setup flow through `execute_run(...)`, which calls
`run_benchmark_lifecycle(...)`. `BaseBenchmark` remains the public wrapper base
and benchmark-facing API boundary; it is not the owner of the entire runtime
execution workflow.

### 3. Strategy Pattern

Data loading strategies vary by platform (bulk COPY, INSERT batches, external tables) but implement a common interface.

### 4. Factory Pattern

`get_platform_adapter(name)` creates appropriate adapter instances based on configuration.

## Performance Considerations

### Data Loading Optimization

- **Parquet Format**: Columnar storage for fast analytical queries
- **Bulk Loading**: Platform-specific optimizations (DuckDB COPY, ClickHouse INSERT, BigQuery external tables)
- **Parallel Loading**: Multi-threaded data ingestion where supported

### Query Execution Optimization

- **Connection Pooling**: Reuse connections across queries
- **Result Streaming**: Fetch results incrementally for large result sets
- **Query Compilation**: Use platform-specific prepared statements

### Memory Management

- **Scale Factor Validation**: Prevent OOM by validating scale vs. available memory
- **Streaming Results**: Don't materialize full result sets unless needed
- **Cleanup**: Explicit connection and resource cleanup

## Security Considerations

### Credential Management

- Environment variables for sensitive data
- Support for platform-specific auth (OAuth, IAM, service accounts)
- No credentials in configuration files or logs

### SQL Injection Prevention

- Parameterized queries where possible
- Input validation on query IDs and parameters
- No direct string concatenation into SQL

### Data Privacy

- Support for synthetic data only (no real customer data)
- Anonymization support for custom benchmarks
- Data residency controls for cloud platforms

## Testing Architecture

### Test Layers

1. **Unit Tests** (`tests/unit/`): Component-level testing
2. **Integration Tests** (`tests/integration/`): Database interaction tests
3. **Live Tests** (`tests/integration/platforms/`): Real cloud platform testing

See: [Testing Guide](../development/testing.md)

## Related Documentation

- [Workflow Concepts](workflow.md) - Typical benchmarking workflows
- [Data Model](data-model.md) - Result schema and data structures
- [Platform Selection Guide](../platforms/platform-selection-guide.md) - Choosing a platform
- [Custom Benchmarks](../advanced/custom-benchmarks.md) - Creating new benchmarks
- [Adding Platforms](../development/adding-new-platforms.md) - Platform development
