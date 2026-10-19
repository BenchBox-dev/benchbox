<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Transaction Primitives Benchmark

```{tags} intermediate, concept, transaction-primitives, custom-benchmark
```

> **CLI name:** `transaction_primitives` - use `benchbox run --benchmark transaction_primitives`
>
> **Query templates:** [every Transaction Primitives query](queries/transaction_primitives/index.md)

## Overview

The Transaction Primitives benchmark provides focused testing of database transaction capabilities including ACID guarantees, isolation levels, and advanced transaction control. It tests fundamental transaction operations that require full ACID support.

This benchmark is designed for databases with robust transaction support (PostgreSQL, MySQL, SQL Server, etc.) and tests capabilities that go beyond simple write operations.

> **Platform Availability**
>
> Transaction Primitives is designed for ACID-compliant OLTP databases. BenchBox supports:
> - **PostgreSQL**: Full transaction support including savepoints (`--platform postgresql`)
> - **TimescaleDB**: PostgreSQL-based with time-series extensions (`--platform timescaledb`)
> - **DuckDB**: Basic transactions but no savepoints (`--platform duckdb`)
> - **SQLite**: Transactions with limited concurrency (`--platform sqlite`)
>
> Platform adapters for MySQL and SQL Server are planned. See the [Development Roadmap](../development/roadmap.md) for details.

## Key Features

- **23 comprehensive transaction operations** testing ACID guarantees
- **Multi-statement transactions** with validation of atomicity and isolation
- **Advanced transaction control** including savepoints, CTEs, and nested transactions
- **Isolation level testing** (READ COMMITTED, REPEATABLE READ, SERIALIZABLE)
- **Transaction lifecycle validation** (BEGIN, COMMIT, ROLLBACK, SAVEPOINT)
- **Complex transaction patterns** including mixed DML, read-your-writes, and multi-table operations
- **TPC-H schema integration** for realistic data patterns
- **Automatic validation** of transaction semantics and data consistency

## Quick Start

Setup requires TPC-H data to be loaded first, then creates the transaction staging tables:

```python
from benchbox import TransactionPrimitives
import duckdb

bench = TransactionPrimitives(scale_factor=0.01)

from benchbox import TPCH
tpch = TPCH(scale_factor=0.01)
tpch.generate_data()

conn = duckdb.connect(":memory:")
tpch.load_data_to_database(conn)

bench.setup(conn)

result = bench.execute_operation("transaction_commit_small", conn)
print(f"Success: {result.success}")
print(f"Rows affected: {result.rows_affected}")
print(f"Duration: {result.write_duration_ms:.2f}ms")
```

## Operations Catalog

The benchmark includes **23 transaction operations** organized into key transaction patterns:

### Transaction Overhead Operations (5 operations)

Tests basic transaction commit and rollback behavior with varying data volumes:

- **[transaction_commit_small](queries/transaction_primitives/transaction-commit-small.md)** - Commits 10 INSERT operations
- **[transaction_commit_medium](queries/transaction_primitives/transaction-commit-medium.md)** - Commits 100 INSERT operations
- **[transaction_commit_large](queries/transaction_primitives/transaction-commit-large.md)** - Commits 1000 INSERT operations
- **[transaction_rollback_small](queries/transaction_primitives/transaction-rollback-small.md)** - Rolls back 3 INSERT operations
- **[transaction_rollback_medium](queries/transaction_primitives/transaction-rollback-medium.md)** - Rolls back 100 INSERT operations

**Purpose**: Validate transaction commit/rollback performance at different scales and ensure all changes are persisted or undone atomically.

### SAVEPOINT Operations (2 operations)

Tests advanced transaction control with nested savepoints:

- **[transaction_savepoint_nested](queries/transaction_primitives/transaction-savepoint-nested.md)** - Tests nested SAVEPOINTs with partial rollback
- **[transaction_savepoint_deep_nesting](queries/transaction_primitives/transaction-savepoint-deep-nesting.md)** - Tests deep savepoint nesting with selective rollback at different levels

**Purpose**: Validate savepoint functionality for complex transaction workflows with partial rollback capabilities.

**Note**: Not all databases support SAVEPOINTs (e.g., DuckDB does not support this feature).

### Isolation Level Operations (3 operations)

Tests transaction isolation guarantees at different levels:

- **[transaction_isolation_read_committed](queries/transaction_primitives/transaction-isolation-read-committed.md)** - Tests READ COMMITTED isolation level
- **[transaction_isolation_repeatable_read](queries/transaction_primitives/transaction-isolation-repeatable-read.md)** - Tests REPEATABLE READ isolation level
- **[transaction_isolation_serializable](queries/transaction_primitives/transaction-isolation-serializable.md)** - Tests SERIALIZABLE isolation level

**Purpose**: Verify correct implementation of SQL standard isolation levels and their impact on concurrent transaction behavior and locking overhead.

### Multi-Statement Transaction Operations (8 operations)

Tests complex transactions with multiple DML statements:

- **[transaction_mixed_dml_small](queries/transaction_primitives/transaction-mixed-dml-small.md)** - Mixed INSERT/UPDATE/DELETE within single transaction (small scale)
- **[transaction_mixed_dml_medium](queries/transaction_primitives/transaction-mixed-dml-medium.md)** - Mixed DML operations on multiple rows with JOINs
- **[transaction_insert_update_chain](queries/transaction_primitives/transaction-insert-update-chain.md)** - Dependent INSERT then UPDATE on same rows within transaction
- **[transaction_delete_insert_same_key](queries/transaction_primitives/transaction-delete-insert-same-key.md)** - DELETE then re-INSERT of same primary key within transaction
- **[transaction_read_your_writes](queries/transaction_primitives/transaction-read-your-writes.md)** - Tests that transaction sees its own uncommitted changes
- **[transaction_insert_with_subquery](queries/transaction_primitives/transaction-insert-with-subquery.md)** - INSERT...SELECT from same table within transaction
- **[transaction_multi_table_writes](queries/transaction_primitives/transaction-multi-table-writes.md)** - Writes to multiple tables within single transaction
- **[transaction_long_running_mixed](queries/transaction_primitives/transaction-long-running-mixed.md)** - Long-running transaction with many statements to measure log/lock overhead

**Purpose**: Validate atomicity and consistency across multiple dependent operations, ensuring transactions can read their own uncommitted changes and handle complex multi-statement patterns.

### Advanced Transaction Features (5 operations)

Tests advanced database transaction capabilities:

- **[transaction_truncate_in_transaction](queries/transaction_primitives/transaction-truncate-in-transaction.md)** - Tests TRUNCATE within transaction and rollback behavior
- **[transaction_create_temp_table](queries/transaction_primitives/transaction-create-temp-table.md)** - Tests transaction-scoped temporary table creation and usage
- **[transaction_with_cte](queries/transaction_primitives/transaction-with-cte.md)** - Tests complex CTE (Common Table Expression) within transaction
- **[transaction_nested_subquery_updates](queries/transaction_primitives/transaction-nested-subquery-updates.md)** - Tests UPDATE with nested subqueries within transaction
- **[transaction_rollback_after_error](queries/transaction_primitives/transaction-rollback-after-error.md)** - Tests automatic rollback behavior after constraint violation

**Purpose**: Validate advanced transaction features including DDL in transactions, CTEs, complex subqueries, and error handling semantics.

## Design Philosophy

### ACID-Focused Testing

Unlike Write Primitives (which focuses on write operations without strict transaction requirements), Transaction Primitives specifically tests:

- **Atomicity**: All-or-nothing transaction execution
- **Consistency**: Database constraint enforcement
- **Isolation**: Concurrent transaction behavior
- **Durability**: Persistence guarantees after commit

### Platform Requirements

**Fully Supported Platforms**:
- PostgreSQL ✓ (Full transaction support including savepoints)
- TimescaleDB ✓ (PostgreSQL-based with time-series extensions)

**Limited Transaction Support**:
- DuckDB ⚠️ (Supports basic transactions in single-connection mode, but no savepoints)
- SQLite ⚠️ (Supports transactions but limited concurrency)

**Planned Platforms** (adapters not yet available):
- MySQL (see [Development Roadmap](../development/roadmap.md))
- SQL Server

**Not Suitable For**:
- ClickHouse ❌ (No multi-statement transactions)
- BigQuery ❌ (Limited transaction support)
- Snowflake ❌ (Implicit transactions only)
- Databricks ❌ (Implicit commit behavior)
- Redshift ❌ (Autocommit by default)

### Data Sharing

- **Reuses TPC-H data** via `get_data_source_benchmark() -> "tpch"`
- Creates staging tables (`transaction_ops_orders`, `transaction_ops_lineitem`)
- No duplicate data generation

### Validation Approach

Each operation includes:
1. **Pre-execution validation**: Check initial state
2. **Transaction execution**: Run multi-statement transaction
3. **Post-execution validation**: Verify expected state changes
4. **Cleanup**: Automatic rollback or explicit cleanup

## Schema

### Staging Tables

- `transaction_ops_orders` - Copy of ORDERS for transaction testing
- `transaction_ops_lineitem` - Copy of LINEITEM for transaction testing

These tables are created during `setup()` and populated from TPC-H base tables.

### No Metadata Tables

Unlike Write Primitives, Transaction Primitives does not create audit log tables. Each operation is self-contained and validated through direct queries.

## CLI Integration

The commands below list the available benchmarks, run Transaction Primitives on DuckDB (which offers only limited transaction testing), and run specific operations. There are no `--categories` or `--operations` options. Select individual operations with `--queries`.

```bash
benchbox benchmarks list

benchbox run --benchmark transaction_primitives --platform duckdb --scale 0.01

benchbox run --benchmark transaction_primitives --platform duckdb \
    --queries transaction_commit_small,transaction_rollback_small
```

**Important Notes**:
- Requires TPC-H data to be loaded first
- Requires database with full transaction support
- Some operations (savepoints) may not work on all platforms

## API Reference

### TransactionPrimitives Class

#### Constructor

```text
TransactionPrimitives(
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

Execute a single transaction operation with validation.

- **Parameters**:
  - `operation_id`: Operation ID (e.g., "transaction_commit_small")
  - `connection`: Database connection
- **Returns**: `OperationResult` with execution metrics
- **Raises**: `ValueError` if operation not found

**`run_benchmark(connection, operation_ids=None) -> List[OperationResult]`**

Run multiple operations.

- **Parameters**:
  - `operation_ids`: List of specific operation IDs to run
- **Returns**: List of `OperationResult` objects

#### Query Methods

**`get_all_operations() -> Dict[str, Operation]`**

Get all available operations (23 total).

**`get_operation(operation_id) -> Operation`**

Get specific operation by ID.

**`get_operations_by_category(category) -> Dict[str, Operation]`**

Get operations filtered by category string (e.g., `"transaction"`). Returns a dict
keyed by operation ID, containing only operations whose `category` matches.

**`get_operation_categories() -> List[str]`**

Get list of available categories (returns `["transaction"]`).

**`get_schema(dialect="standard") -> Dict[str, Dict]`**

Get staging table schema definitions.

#### Cleanup Methods

**`cleanup_auxiliary_files() -> None`**

Remove any auxiliary files (logs, temp exports) produced during benchmark execution.
Call after `teardown()` to ensure the working directory is clean. Safe to call even
when no auxiliary files were produced.

### OperationResult

Result object returned by `execute_operation` and `run_benchmark`.

**Fields**:
- `operation_id: str` - Operation identifier
- `success: bool` - Whether operation succeeded
- `write_duration_ms: float` - Execution time in milliseconds
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

> **Note**: These examples use DuckDB as it's currently supported in BenchBox. For PostgreSQL/MySQL examples, see [Platform-Specific Notes](#platform-specific-notes) below (adapters not yet available).

```python
from benchbox import TPCH, TransactionPrimitives
import duckdb

tpch = TPCH(scale_factor=0.01)
tpch.generate_data()

conn = duckdb.connect(":memory:")
tpch.load_data_to_database(conn)
```

### Setup Staging Tables

This is step 2 of the walkthrough, after loading TPC-H data:

```python
bench = TransactionPrimitives(scale_factor=0.01)
setup_result = bench.setup(conn, force=True)

print(f"Setup: {setup_result['success']}")
print(f"Tables created: {setup_result['tables_created']}")
```

Expected output:

```text
Setup: True
Tables created: ['transaction_ops_orders', 'transaction_ops_lineitem']
```

### Execute Single Operation

`execute_operation` validates the result automatically.

```python
result = bench.execute_operation("transaction_commit_small", conn)

print(f"Operation: {result.operation_id}")
print(f"Success: {result.success}")
print(f"Rows affected: {result.rows_affected}")
print(f"Duration: {result.write_duration_ms:.2f}ms")
print(f"Validation passed: {result.validation_passed}")
```

Expected output (the duration varies):

```text
Operation: transaction_commit_small
Success: True
Rows affected: 10
Duration: 15.34ms
Validation passed: True
```

### Run Full Benchmark

This runs all transaction operations and then prints the per-operation results:

```python
results = bench.run_benchmark(conn)

print(f"Total operations: {len(results)}")
successful = [r for r in results if r.success]
print(f"Successful: {len(successful)}/{len(results)}")

for result in results:
    status = "✅" if result.success else "❌"
    print(f"{status} {result.operation_id}: {result.write_duration_ms:.2f}ms")
```

## Platform-Specific Notes

### PostgreSQL

**Full transaction support** - Recommended for Transaction Primitives benchmark.

```bash
benchbox run --benchmark transaction_primitives --platform postgresql --scale 0.01
```

```python
from benchbox.platforms.postgresql import PostgreSQLAdapter
adapter = PostgreSQLAdapter(
    host="localhost",
    database="benchmark",
    user="postgres"
)
```

- Full ACID support ✅
- All isolation levels ✅
- Savepoints ✅
- Mature transaction implementation

### MySQL

> **⚠️ Adapter Not Yet Available**: MySQL adapter not implemented in BenchBox. Example shown for future reference. See [Development Roadmap](../development/roadmap.md).

**Supported**: Requires InnoDB engine.

```python
import mysql.connector
conn = mysql.connector.connect(
    host="localhost",
    user="root",
    database="benchmark"
)
```

- Full ACID support with InnoDB ✅
- Most isolation levels ✅
- Savepoints ✅
- Good performance

### DuckDB

**Limited Support**: No savepoint support.

```python
import duckdb
conn = duckdb.connect(":memory:")
```

- Basic transactions ✅
- Savepoints ❌ (not supported)
- Good for simple transaction testing
- Fast execution

**Workaround**: Skip savepoint tests when using DuckDB.

### ClickHouse

**Not Supported**: No multi-statement transaction support.

ClickHouse does not support `BEGIN/COMMIT/ROLLBACK` syntax. Use Write Primitives instead.

### BigQuery

**Not Recommended**: Limited transaction support.

BigQuery has implicit transactions only. Use Write Primitives for BigQuery testing.

## Troubleshooting

### "Required TPC-H table not found"

**Error**: `RuntimeError: Required TPC-H table 'orders' not found`

**Solution**: Load TPC-H data first before running Transaction Primitives.

```python
from benchbox import TPCH
tpch = TPCH(scale_factor=0.01)
tpch.generate_data()
tpch.load_data_to_database(conn)
```

### "Savepoint not supported"

**Error**: `Parser Error: syntax error at or near "SAVEPOINT"`

**Platform**: DuckDB and some other databases don't support savepoints.

**Solution**: Skip savepoint operations for these platforms:

```python
ops = bench.get_all_operations()
supported_ops = [op_id for op_id in ops if "savepoint" not in op_id]
results = bench.run_benchmark(conn, operation_ids=supported_ops)
```

### Transaction Validation Failures

**Issue**: Transaction commits but validation fails.

**Possible causes**:
1. **Concurrent modifications**: Other connections modifying data
2. **Timing issues**: Validation running before commit completes
3. **Isolation level effects**: Different isolation levels affecting visibility

**Solution**: Ensure single-connection testing and check validation queries.

### Performance Degradation

**Issue**: Transaction operations running slower than expected.

**Checks**:
1. **Transaction log size**: Large transaction logs can slow commits
2. **Lock contention**: Concurrent transactions causing waits
3. **Disk I/O**: Transaction durability requires disk writes

**Optimization**: Use smaller scale factors for faster testing.
```python
bench = TransactionPrimitives(scale_factor=0.001)
```

## Relationship to Write Primitives

Transaction Primitives was split from Write Primitives v2 to separate concerns:

### Write Primitives v2

- **Focus**: Individual write operations (INSERT, UPDATE, DELETE, MERGE, BULK_LOAD, DDL)
- **Platform support**: Broad (ClickHouse, BigQuery, Snowflake, Databricks, Redshift, DuckDB, SQLite)
- **Transaction requirements**: Optional, uses explicit cleanup
- **Operations**: 112 operations across 6 categories

### Transaction Primitives

- **Focus**: Multi-statement transactions and ACID guarantees
- **Platform support**: Narrow (requires full ACID support)
- **Transaction requirements**: Required, tests transaction semantics
- **Operations**: 23 operations focused on transaction behavior

**Use both benchmarks together** for comprehensive database testing:
- Write Primitives for individual operation performance
- Transaction Primitives for ACID guarantees and transaction control

## Testing

```bash
uv run -- python -m pytest tests/integration/test_transaction_primitives_duckdb.py -v

uv run -- python -c "from benchbox import TransactionPrimitives; bench = TransactionPrimitives(0.01); print(bench.get_benchmark_info())"
```

## License

Copyright 2026 Joe Harris / BenchBox Project

Licensed under the MIT License.
