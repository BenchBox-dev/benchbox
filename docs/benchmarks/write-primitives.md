<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Write Primitives Benchmark

```{tags} intermediate, concept, write-primitives, custom-benchmark, performance
```

> **CLI name:** `write_primitives` - use `benchbox run --benchmark write_primitives`
>
> **Query templates:** [every Write Primitives query](queries/write_primitives/index.md)

## Overview

The Write Primitives benchmark provides comprehensive testing of fundamental database write operations using TPC-H schema as foundation. It tests INSERT, UPDATE, DELETE, BULK_LOAD, MERGE, and DDL operations across a wide range of platforms.

**Note**: Transaction operations (COMMIT, ROLLBACK, SAVEPOINT, isolation levels) are provided separately in the [Transaction Primitives](transaction-primitives.md) benchmark for better platform compatibility and focused testing.

## Key Features

- **112 comprehensive write operations** across 6 categories
- **Broad platform compatibility** including ClickHouse, BigQuery, Snowflake, PostgreSQL, DuckDB
- **Non-transactional cleanup** using explicit DELETE/DROP statements
- **Category-based isolation** with 18 dedicated staging tables
- **Dynamic data partitioning** that works with any dataset size
- **TPC-H schema integration** for realistic data patterns
- **Automatic validation** of write correctness

## Quick Start

```python
from benchbox import WritePrimitives
import duckdb

bench = WritePrimitives(scale_factor=0.01)

from benchbox import TPCH
tpch = TPCH(scale_factor=0.01)
tpch.generate_data()

conn = duckdb.connect(":memory:")
tpch.load_data_to_database(conn)

bench.setup(conn)

result = bench.execute_operation("insert_single_row", conn)
print(f"Success: {result.success}")
print(f"Rows affected: {result.rows_affected}")
print(f"Duration: {result.write_duration_ms:.2f}ms")
```

Setup needs TPC-H data loaded first, so the example generates and loads it before calling `bench.setup(conn)`, which creates the staging tables.

## Operations Catalog

The benchmark includes **112 write operations** across 6 categories:

### INSERT (12 operations)

- Single row INSERT
- Batch INSERT (10, 100, 1000 rows)
- INSERT...SELECT (simple, with JOIN, aggregated, from multiple tables)
- INSERT...UNION
- INSERT with default values
- INSERT...ON CONFLICT (UPSERT)
- INSERT...RETURNING

**Purpose**: Test basic row insertion, bulk insertion patterns, and INSERT variants including UPSERT.

### UPDATE (15 operations)

- Single row by primary key
- Selective (10%, 50%, 100% of rows)
- With subquery, JOIN, aggregate
- Multi-column updates (5+ columns)
- With CASE expression, computed columns
- String manipulation, date arithmetic
- Conditional updates
- UPDATE...RETURNING

**Purpose**: Test row modification patterns from simple primary key updates to complex multi-table updates.

### DELETE (14 operations)

- Single row by primary key
- Selective (10%, 25%, 50%, 75% of rows)
- With subquery, JOIN, aggregation
- With NOT EXISTS (anti-join)
- DELETE...RETURNING
- Cascade simulation
- DELETE vs TRUNCATE comparison
- GDPR-compliant deletion patterns (1%, 5% of suppliers)

**Purpose**: Test row deletion patterns including cascade effects and data retention compliance scenarios.

### BULK_LOAD (36 operations)

- CSV loads (3 sizes: 1K, 100K, 1M rows)
  - 4 compression variants each: uncompressed, gzip, zstd, bzip2
- Parquet loads (3 sizes: 1K, 100K, 1M rows)
  - 4 compression variants each: uncompressed, snappy, gzip, zstd
- Special operations:
  - Column subset loading
  - Inline transformations (CAST, UPPER, CONCAT)
  - Error handling (skip malformed rows)
  - Parallel multi-file loading
  - UPSERT mode (MERGE during load)
  - Append vs replace modes
  - Custom delimiters, quoted fields
  - NULL handling, UTF-8 encoding
  - Custom date formats

**Purpose**: Test bulk data loading from files with various formats, compressions, and transformation requirements.

### MERGE (23 operations)

- Simple UPSERT
- UPSERT with DELETE clause (tri-directional)
- Varying overlap scenarios (10%, 50%, 90%, none, all)
- Multi-column join conditions
- Aggregated source queries
- Conditional UPDATE and INSERT
- Multi-column updates
- Computed values, string operations, date arithmetic
- CTE sources
- MERGE...RETURNING
- Error handling (duplicate sources)
- ETL aggregation with running totals
- Window function deduplication (ROW_NUMBER pattern)
- SCD Type 2 dimension maintenance ([`merge_scd_type2_basic`](queries/write_primitives/merge-scd-type2-basic.md),
  [`merge_scd_type2_no_change`](queries/write_primitives/merge-scd-type2-no-change.md), [`merge_scd_type2_new_keys_only`](queries/write_primitives/merge-scd-type2-new-keys-only.md)), which closes the
  current version of a changed business key and opens a new one, expressed as
  portable UPDATE + INSERT against the dedicated `scd2_ops_*` dimension tables
  (no `APPLY CHANGES INTO` / declarative-pipeline syntax, so it runs unchanged on
  standard-DML engines). The catalog marks only DataFusion unsupported; ClickHouse
  has no standard UPDATE, so only the insert-only op is portable there and the
  close-old ops would need a mutation rewrite (they are not auto-skipped today).
  The standard-DML scoping covers the harness setup too, not just the operation
  SQL: the dimension/stage population uses `||` concatenation and
  `CAST(... AS VARCHAR)` to build the change-detection fingerprint, so an engine
  where `||` is not string concatenation would need the setup rewritten as well

**Purpose**: Test UPSERT/MERGE operations for CDC, ETL, slowly-changing
dimension maintenance, and incremental data loading scenarios.

### DDL (12 operations)

- CREATE TABLE (simple, with constraints, with indexes)
- CREATE TABLE AS SELECT (simple, aggregated)
- ALTER TABLE (ADD COLUMN, DROP COLUMN, RENAME COLUMN)
- CREATE INDEX (on empty table, on existing data)
- DROP INDEX
- CREATE VIEW
- DROP TABLE
- TRUNCATE TABLE (small, large datasets)

**Purpose**: Test schema modification operations including table and index creation/modification.

## Design Philosophy

The Write Primitives benchmark is designed for **broad platform compatibility** by focusing on individual write operations rather than multi-statement transactions.

**Architecture Decisions**:
- **Platform Compatibility**: Works across analytical databases (ClickHouse, BigQuery, Snowflake, Databricks, Redshift) and embedded databases (DuckDB, SQLite)
- **Focused Testing**: Tests write operation performance and correctness independently from transaction semantics
- **Clear Separation**: Transaction testing is handled by the separate [Transaction Primitives](transaction-primitives.md) benchmark

**Key Features**:
- **Category-based isolation**: 18 dedicated staging tables (one per operation category)
- **Non-transactional cleanup**: Explicit DELETE/DROP statements work across all platforms
- **Dynamic data partitioning**: Works with any dataset size (3 rows to 10M rows)
- **Optional dependencies**: Graceful handling of missing tables (e.g., supplier)

**Operation Count**:
- **Write Primitives**: 112 operations (INSERT, UPDATE, DELETE, MERGE, BULK_LOAD, DDL)
- **Transaction Primitives**: 8 operations (COMMIT, ROLLBACK, SAVEPOINT, isolation levels)

## Design

### Data Sharing

- **Reuses TPC-H data** via `get_data_source_benchmark() -> "tpch"`
- No duplicate data generation
- Staging tables created from base TPC-H tables

### Category-Based Isolation

Each operation category has dedicated staging tables:

**INSERT category**:
- `insert_ops_lineitem` - Target for lineitem inserts
- `insert_ops_orders` - Target for order inserts
- `insert_ops_orders_summary` - Target for aggregated inserts
- `insert_ops_lineitem_enriched` - Target for joined inserts

**UPDATE category**:
- `update_ops_orders` - Copy of orders for update testing

**DELETE category**:
- `delete_ops_orders` - Copy of orders for delete testing
- `delete_ops_lineitem` - Copy of lineitem for delete testing
- `delete_ops_supplier` - Copy of supplier for GDPR deletion testing (optional)

**MERGE category**:
- `merge_ops_target` - First 50% of orders (merge target)
- `merge_ops_source` - Second 50% of orders (merge source)
- `merge_ops_lineitem_target` - First 50% of lineitems
- `merge_ops_summary_target` - Target for aggregated merges
- `scd2_ops_dim_customer` - SCD Type 2 dimension seeded from customer (one current version per business key)
- `scd2_ops_stage_customer` - Incoming change batch (changed/unchanged/new) derived dynamically from customer

**BULK_LOAD category**:
- `bulk_load_ops_target` - Target for bulk load testing

**DDL category**:
- `ddl_truncate_target` - Target for TRUNCATE testing

**Metadata**:
- `write_ops_log` - Audit log for operations
- `batch_metadata` - Tracks batch operations

### Non-Transactional Cleanup

The benchmark uses explicit cleanup operations instead of transaction rollback for maximum platform compatibility:

```sql
INSERT INTO insert_ops_orders ...;

DELETE FROM insert_ops_orders WHERE o_orderkey = ...;
```

The first statement is the write operation. The second is the explicit cleanup.

**Benefits**:
- Works on databases without transaction support (ClickHouse, BigQuery)
- Clear, predictable cleanup behavior
- Easier debugging and validation
- Consistent behavior across all platforms

### Dynamic Data Partitioning

The benchmark uses percentage-based queries instead of hard-coded key ranges for maximum flexibility:

```sql
INSERT INTO merge_ops_target
SELECT * FROM orders
WHERE o_orderkey <= (SELECT CAST(MAX(o_orderkey) * 0.5 AS INTEGER) FROM orders);
```

This dynamic approach works with any data size.

**Benefits**:
- Works with test data (3 rows) and production data (10M rows)
- Consistent 50/50 splits for MERGE testing
- No hard-coded assumptions about key ranges
- Adapts automatically to actual data distribution

### Validation

- Every write operation has corresponding read queries
- Validates correctness and consistency
- Measures end-to-end write-read cycle

## Schema

### Staging Tables (18 total)

**INSERT category** (4 tables):
- `insert_ops_lineitem`
- `insert_ops_orders`
- `insert_ops_orders_summary`
- `insert_ops_lineitem_enriched`

**UPDATE category** (1 table):
- `update_ops_orders`

**DELETE category** (3 tables):
- `delete_ops_orders`
- `delete_ops_lineitem`
- `delete_ops_supplier` (optional)

**MERGE category** (6 tables):
- `merge_ops_target`
- `merge_ops_source`
- `merge_ops_lineitem_target`
- `merge_ops_summary_target`
- `scd2_ops_dim_customer` (SCD Type 2 dimension)
- `scd2_ops_stage_customer` (SCD Type 2 change batch)

**BULK_LOAD category** (1 table):
- `bulk_load_ops_target`

**DDL category** (1 table):
- `ddl_truncate_target`

**Metadata** (2 tables):
- `write_ops_log`
- `batch_metadata`

## CLI Integration

```bash
benchbox benchmarks list

benchbox run --benchmark write_primitives --platform duckdb --scale 0.01

benchbox run --benchmark write_primitives --platform duckdb \
    --queries insert_single_row,update_single_row_pk
```

The first command lists available benchmarks, the second runs Write Primitives, and the third runs specific operations. There are no `--categories` or `--operations` options. Select individual operations with `--queries`.

**Important Notes**:
- Requires TPC-H data to be loaded first
- Staging tables automatically created during setup
- Each operation includes automatic validation and cleanup

## API Reference

### WritePrimitives Class

#### Constructor

```text
WritePrimitives(
    scale_factor: float = 1.0,
    output_dir: str | Path | None = None,
    quiet: bool = False
)
```

**Parameters**:
- `scale_factor`: TPC-H scale factor (must match TPC-H data)
- `output_dir`: Directory for data files. Defaults to the shared TPC-H data
  directory, `benchmark_runs/datagen/tpch_<scale>` (or under
  `BENCHBOX_OUTPUT_DIR` when set)
- `quiet`: Suppress verbose logging

#### Lifecycle Methods

**`setup(connection, force=False) -> Dict[str, Any]`**

Creates staging tables and populates them from TPC-H base tables.

- **Parameters**:
  - `connection`: Database connection
  - `force`: If True, drop existing staging tables first
- **Returns**: Dict with keys `success`, `tables_created`, `tables_populated`
- **Raises**: `RuntimeError` if TPC-H tables don't exist

**`is_setup(connection) -> bool`**

Check if staging tables are initialized.

**`reset(connection) -> None`**

Truncate and repopulate staging tables to initial state.

**`teardown(connection) -> None`**

Drop all staging tables.

#### Execution Methods

**`execute_operation(operation_id, connection) -> OperationResult`**

Execute a single write operation with validation and cleanup.

- **Parameters**:
  - `operation_id`: Operation ID (e.g., "insert_single_row")
  - `connection`: Database connection
- **Returns**: `OperationResult` with execution metrics
- **Raises**: `ValueError` if operation not found

**`run_benchmark(connection, operation_ids=None, categories=None) -> List[OperationResult]`**

Run multiple operations.

- **Parameters**:
  - `operation_ids`: List of specific operation IDs to run
  - `categories`: List of categories to run (e.g., ["insert", "update"])
- **Returns**: List of `OperationResult` objects

#### Query Methods

**`get_all_operations() -> Dict[str, WriteOperation]`**

Get all available operations (112 total).

**`get_operation(operation_id) -> WriteOperation`**

Get specific operation by ID.

**`get_operations_by_category(category) -> Dict[str, WriteOperation]`**

Get operations filtered by category.

**`get_operation_categories() -> List[str]`**

Get list of available categories: `["insert", "update", "delete", "merge", "bulk_load", "ddl"]`

**`get_schema(dialect="standard") -> Dict[str, Dict]`**

Get staging table schema definitions.

**`get_create_tables_sql(dialect="standard") -> str`**

Get SQL to create staging tables.

### OperationResult

Result object returned by `execute_operation` and `run_benchmark`.

**Fields**:
- `operation_id: str` - Operation identifier
- `success: bool` - Whether operation succeeded
- `write_duration_ms: float` - Write execution time in milliseconds
- `rows_affected: int` - Number of rows affected (-1 if unknown)
- `validation_duration_ms: float` - Validation time in milliseconds
- `validation_passed: bool` - Whether validation passed
- `validation_results: List[Dict]` - Detailed validation results
- `cleanup_duration_ms: float` - Cleanup time in milliseconds
- `cleanup_success: bool` - Whether cleanup succeeded
- `error: Optional[str]` - Error message if operation failed
- `cleanup_warning: Optional[str]` - Warning for cleanup failures

## Execution Examples

### Prerequisites

```python
from benchbox import TPCH, WritePrimitives
import duckdb

tpch = TPCH(scale_factor=0.01)
tpch.generate_data()

conn = duckdb.connect(":memory:")
tpch.load_data_to_database(conn)
```

### Setup Staging Tables

```python
bench = WritePrimitives(scale_factor=0.01)
setup_result = bench.setup(conn, force=True)

print(f"Setup: {setup_result['success']}")
print(f"Tables created: {len(setup_result['tables_created'])}")
```

Expected output:

- `Setup: True`
- `Tables created: 16`

### Execute Single Operation

```python
result = bench.execute_operation("insert_single_row", conn)

print(f"Operation: {result.operation_id}")
print(f"Success: {result.success}")
print(f"Rows affected: {result.rows_affected}")
print(f"Write time: {result.write_duration_ms:.2f}ms")
print(f"Validation passed: {result.validation_passed}")
```

The operation runs with automatic validation and cleanup. Expected output:

- `Operation: insert_single_row`
- `Success: True`
- `Rows affected: 1`
- `Write time: 2.45ms` (the time varies by platform)
- `Validation passed: True`

### Run Full Benchmark

```python
results = bench.run_benchmark(conn)

print(f"Total operations: {len(results)}")
successful = [r for r in results if r.success]
print(f"Successful: {len(successful)}")

for result in results:
    print(f"{result.operation_id}: {result.write_duration_ms:.2f}ms")
```

This runs all operations, so `len(results)` is 112.

### Filter by Category

```python
insert_results = bench.run_benchmark(conn, categories=["insert"])

specific_ops = ["insert_single_row", "update_single_row_pk", "delete_single_row_pk"]
results = bench.run_benchmark(conn, operation_ids=specific_ops)
```

The first call runs only INSERT operations. The second runs a specific list of operations.

## Platform-Specific Notes

### DuckDB

**Recommended for development and testing**.

```python
import duckdb
conn = duckdb.connect(":memory:")
```

- Fast execution ✅
- Full SQL support ✅
- Easy setup ✅
- Good for testing Write Primitives ✅

### PostgreSQL

**Full write operation support**.

```python
import psycopg
conn = psycopg.connect("dbname=benchmark")
```

- Full SQL support ✅
- All write operations supported ✅
- Production-grade ✅

### ClickHouse

**Supported with limitations**.

```python
from clickhouse_driver import Client
client = Client('localhost')
```

- No multi-statement transactions ✅ (Write Primitives doesn't require them)
- MERGE may use ALTER TABLE UPDATE/DELETE syntax
- Optimized for bulk data loading ✅

### BigQuery

**Supported with limitations**.

```python
from google.cloud import bigquery
client = bigquery.Client()
```

- No multi-statement transactions ✅ (Write Primitives doesn't require them)
- MERGE fully supported ✅
- Excellent at scale ✅

### Snowflake

**Fully supported**.

```python
import snowflake.connector
conn = snowflake.connector.connect(...)
```

- Full SQL support ✅
- Excellent MERGE performance ✅
- Good for large-scale testing ✅

## Troubleshooting

### "Required TPC-H table not found"

**Error**: `RuntimeError: Required TPC-H table 'orders' not found`

**Solution**: Load TPC-H data first before running Write Primitives.

```python
from benchbox import TPCH
tpch = TPCH(scale_factor=0.01)
tpch.generate_data()
tpch.load_data_to_database(conn)
```

### "Staging tables not initialized"

**Error**: `RuntimeError: Staging tables not initialized`

**Solution**: Call setup() before executing operations.

```python
bench = WritePrimitives(scale_factor=0.01)
bench.setup(conn)
result = bench.execute_operation("insert_single_row", conn)
```

### Validation Failures

**Issue**: Operations succeed but validation fails.

**Possible causes**:
1. **Data-dependent operations**: Some operations depend on TPC-H data distribution
2. **Platform differences**: Different databases may return different rowcounts

**Solution**: Check validation results for details.

```python
result = bench.execute_operation("insert_select_simple", conn)
if not result.validation_passed:
    for val_result in result.validation_results:
        print(f"Query: {val_result['query_id']}")
        print(f"Expected: {val_result['expected_rows']}")
        print(f"Actual: {val_result['actual_rows']}")
```

### Performance Issues

**Issue**: Operations running slower than expected.

**Checks**:
1. **Scale factor**: Larger scale factors = more data = slower operations
2. **Indexes**: Ensure TPC-H tables have proper indexes
3. **Hardware**: Check disk I/O and memory

**Optimization**:
```python
bench = WritePrimitives(scale_factor=0.001)
```

Use smaller scale factors for faster testing.

## Relationship to Transaction Primitives

Write Primitives and Transaction Primitives are complementary benchmarks designed for different testing scenarios:

### Write Primitives

- **Focus**: Individual write operations (INSERT, UPDATE, DELETE, MERGE, BULK_LOAD, DDL)
- **Platform support**: Broad (ClickHouse, BigQuery, DuckDB, PostgreSQL, Snowflake, Redshift)
- **Transaction requirements**: None (uses explicit cleanup)
- **Operations**: 112 operations across 6 categories
- **Use case**: Testing write operation performance and correctness across diverse platforms

### Transaction Primitives

- **Focus**: Multi-statement transactions and ACID guarantees
- **Platform support**: Designed for ACID-capable databases (PostgreSQL, MySQL, SQL Server - adapters not yet available; currently limited support via DuckDB/SQLite)
- **Transaction requirements**: Required (tests transaction semantics)
- **Operations**: 8 operations focused on ACID behavior
- **Use case**: Testing transaction isolation, atomicity, consistency, durability

### Using Both Together

For comprehensive database testing, use both benchmarks:

```python
from benchbox import WritePrimitives, TransactionPrimitives

write_bench = WritePrimitives(scale_factor=0.01)
write_results = write_bench.run_benchmark(conn)

if platform_supports_acid:
    txn_bench = TransactionPrimitives(scale_factor=0.01)
    txn_results = txn_bench.run_benchmark(conn)
```

Write operations work on all platforms. Transaction tests run only on ACID-capable databases.

## DataFrame Support

The Write Primitives benchmark provides **DataFrame support** for write operations on DataFrame platforms, enabling native DataFrame API benchmarking for INSERT, UPDATE, DELETE, MERGE, and BULK_LOAD operations.

### Platform Capabilities

| Platform | INSERT | UPDATE | DELETE | MERGE | BULK_LOAD | Notes |
|----------|--------|--------|--------|-------|-----------|-------|
| **Polars** | ✅ | ✅ | ✅ | ✅ | ✅ | Full support via read-modify-write pattern |
| **Pandas** | ✅ | ❌ | ❌ | ❌ | ✅ | File-level operations only |
| **PySpark** | ✅ | ✅* | ✅* | ✅* | ✅ | *Requires Delta Lake table format |

### DataFrame Write Manager

Use the `DataFrameWriteOperationsManager` for programmatic DataFrame write operations:

```python
from benchbox.core.write_primitives.dataframe_operations import (
    DataFrameWriteOperationsManager,
    WriteOperationType,
    get_dataframe_write_manager,
)

manager = DataFrameWriteOperationsManager("polars-df")

caps = manager.get_capabilities()
print(f"Supports UPDATE: {caps.supports_operation(WriteOperationType.UPDATE)}")
print(f"Supports MERGE: {caps.supports_operation(WriteOperationType.MERGE)}")
print(f"Supported compressions: {caps.supported_compressions}")

result = manager.execute_bulk_load(
    source_path="/data/raw/orders.csv",
    target_path="/data/orders",
    source_format="csv",
    target_format="parquet",
    compression="zstd",
    partition_columns=["o_orderpriority"],
)

print(f"Success: {result.success}")
print(f"Rows written: {result.rows_affected}")
print(f"Duration: {result.duration_ms:.2f}ms")
```

The example creates a manager for Polars, checks which operations the platform supports, and then runs a BULK_LOAD.

### INSERT Operation (DataFrame)

```python
import polars as pl

new_orders = pl.DataFrame({
    "o_orderkey": [9999001, 9999002],
    "o_custkey": [12345, 12346],
    "o_orderstatus": ["O", "O"],
    "o_totalprice": [1500.00, 2500.00],
})

result = manager.execute_insert(
    table_path="/data/orders",
    dataframe=new_orders,
    mode="append",
)
```

The example builds a DataFrame of new rows and inserts it.

### UPDATE Operation (DataFrame)

```python
result = manager.execute_update(
    table_path="/data/orders",
    condition="o_orderstatus = 'P'",
    updates={"o_orderstatus": "'F'"},
)

print(f"Rows updated: {result.rows_affected}")
```

Polars uses a read-modify-write pattern for UPDATE.

### DELETE Operation (DataFrame)

```python
result = manager.execute_delete(
    table_path="/data/orders",
    condition="o_totalprice < 100",
)

print(f"Rows deleted: {result.rows_affected}")
```

### MERGE Operation (DataFrame)

```python
source_df = pl.DataFrame({
    "o_orderkey": [1, 2, 9999999],
    "o_custkey": [100, 200, 300],
    "o_totalprice": [1000.0, 2000.0, 3000.0],
})

result = manager.execute_merge(
    table_path="/data/orders",
    source_dataframe=source_df,
    merge_condition="target.o_orderkey = source.o_orderkey",
    when_matched={"o_totalprice": "source.o_totalprice"},
    when_not_matched={"o_orderkey": "source.o_orderkey", "o_custkey": "source.o_custkey"},
)
```

The source DataFrame mixes existing keys (1 and 2) with a new key (9999999), so the MERGE acts as an upsert.

### PySpark DataFrame Support

For PySpark, pass the SparkSession to enable DataFrame operations:

```python
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("WritePrimitives").getOrCreate()

manager = DataFrameWriteOperationsManager("pyspark-df", spark_session=spark)

result = manager.execute_bulk_load(
    source_path="/data/raw/orders.parquet",
    target_path="/data/orders_delta",
    source_format="parquet",
    target_format="delta",
    compression="zstd",
    partition_columns=["o_orderpriority"],
)
```

The Delta Lake target format provides ACID support, which PySpark UPDATE, DELETE, and MERGE require.

### Result Object

All DataFrame write operations return a `DataFrameWriteResult`:

```python
@dataclass
class DataFrameWriteResult:
    operation_type: WriteOperationType
    success: bool
    start_time: float
    end_time: float
    duration_ms: float
    rows_affected: int
    bytes_written: int | None = None
    compression: str | None = None
    file_count: int | None = None
    error_message: str | None = None
    validation_passed: bool = True
```

## Testing

```bash
uv run -- python -m pytest tests/unit/benchmarks/test_write_primitives_core.py -v

uv run -- python -m pytest tests/integration/test_write_primitives_duckdb.py -v

uv run -- python -c "from benchbox import WritePrimitives; bench = WritePrimitives(0.01); print(bench.get_benchmark_info())"
```

## License

Copyright 2026 Joe Harris / BenchBox Project

Licensed under the MIT License.
