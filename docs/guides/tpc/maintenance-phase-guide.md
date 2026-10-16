<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# TPC Maintenance Phase: Complete Guide

```{tags} advanced, guide, tpc-h, tpc-ds
```

> Standalone Maintenance tests permanently modify the database. TPC-H Power and Throughput also write
> when `tpch_refresh_functions=true`. Reload the original data before reusing update sets; otherwise,
> use `tpch_refresh_first_set` to continue at the next unused set.

## Overview

The TPC maintenance phases test data warehouse refresh operations. Standalone Maintenance tests write, update, and delete data. TPC-H Power and Throughput remain read-only by default but can apply RF1/RF2 when refresh integration is enabled.
These operations support common warehouse workflows:

- New transactions arrive continuously from source systems
- Historical data requires periodic updates
- Old data is archived or purged
- Data corrections are applied

### Key Characteristics

| Aspect | Power/Throughput Tests | Standalone Maintenance Test |
|--------|------------------------|------------------------------|
| **Data Modification** | None by default; optional TPC-H RF1/RF2 | Permanent (INSERT/UPDATE/DELETE) |
| **Database State** | Unchanged by default; modified with TPC-H refresh | **Modified after test** |
| **Repeatability** | Reuse clean data or advance update-set number | **Reload or use unused update sets** |
| **Purpose** | Query performance; optionally concurrent refresh | Data modification performance |
| **TPC Compliance** | Depends on configuration and evidence | Required for official results |

## When to Use Maintenance Tests

### Use Maintenance Tests When You Need To:

* ✅ **Test data warehouse ETL performance** - Measure how the database handles data refresh operations
* ✅ **Evaluate write performance under analytical workloads** - Understand the cost of data modifications
* ✅ **Simulate mixed read/write workloads** - TPC-H Throughput can run its opt-in refresh stream concurrently with query streams
* ✅ **Benchmark data maintenance windows** - Measure how long it takes to process data refreshes

### Skip Maintenance Tests When:

* ❌ **Only measuring query performance** - Power and Throughput tests are sufficient for read-only benchmarks
* ❌ **Running quick exploratory tests** - Maintenance tests require database reload, adding operational overhead
* ❌ **Comparing read-only OLAP systems** - Some analytical databases are immutable and don't support data modifications
* ❌ **Working in production environments** - Never run Maintenance tests against production data (they permanently modify it!)

## TPC-H Refresh Functions

TPC-H uses DBGen update sets. `tpch_refresh_functions` defaults to `false`; set it to `true` to include RF1/RF2 in Power and the refresh stream in Throughput. `tpch_refresh_first_set` defaults to 1 and selects the first DBGen update set.

DBGen creates update files before measurement. Power uses RF1(set F), runs the 22 queries, then applies RF2(set F); only the final measurement iteration modifies data. Throughput starts a separate refresh stream and runs S serial RF1/RF2 pairs on sets F+1 through F+S alongside the S query streams. Refresh time contributes to the throughput interval; the refresh safety checks run before the interval starts and after it ends.

The standalone TPC-H maintenance phase runs only on adapters that declare refresh support (DuckDB, SQLite, PostgreSQL and the adapters built on it, Snowflake, and SingleStore). On other adapters it completes with one skipped `maintenance_not_supported` row and issues no refresh SQL.

RF1 inserts the DBGen order and lineitem rows in 1,000-row chunks and rejects order-key collisions. RF2 verifies that all selected orders exist, then deletes their lineitems before the orders. Each function uses a transaction. The standalone Maintenance phase uses the same generator and executor. These operations permanently modify the database.

After a combined run using Power set F and Throughput sets F+1..F+S, the next unused set is F+S+1. Reload the original data to repeat the run from set 1. This feature is verified in memory on DuckDB and SQLite; support is declared by the adapter.

```python
from benchbox import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter

benchmark = TPCH(
    scale_factor=1.0,
    tpch_refresh_functions=True,
    tpch_refresh_first_set=1,
)
benchmark.generate_data()
adapter = DuckDBAdapter(database_path="tpch.duckdb", force_recreate=True)

power_result = adapter.run_benchmark(benchmark, test_execution_type="power")
throughput_result = adapter.run_benchmark(benchmark, test_execution_type="throughput")
```

This example writes to the database in both phases. Use the next unused update set or reload the data before another run.

## TPC-DS Maintenance Test

### Overview

TPC-DS maintenance operations simulate ongoing retail data warehouse updates across multiple sales channels and inventory systems. Unlike TPC-H's two discrete refresh functions, TPC-DS executes a series of operations that rotate through INSERT, UPDATE, and DELETE across multiple tables.

### What TPC-DS Maintenance Does

The test cycles through three operation types across seven tables:

1. **INSERT**: New sales transactions, returns, and inventory receipts
2. **UPDATE**: Corrections to existing records (prices, quantities, statuses)
3. **DELETE**: Removal of cancelled orders, consolidated returns, or expired inventory

Default configuration: **4 operations** that rotate through operation types and tables.

### Affected Tables

| Table | Purpose | Sales Channel |
|-------|---------|---------------|
| `catalog_sales` | Catalog orders | Catalog |
| `catalog_returns` | Catalog returns | Catalog |
| `web_sales` | Online orders | Web |
| `web_returns` | Online returns | Web |
| `store_sales` | In-store purchases | Store |
| `store_returns` | In-store returns | Store |
| `inventory` | Product stock levels | All channels |

### Data Modified in TPC-DS

**Per-Operation Row Counts:**

| Operation | Rows Modified per SF | SF=1 Example | SF=10 Example |
|-----------|----------------------|--------------|---------------|
| INSERT    | ~1,000               | 1,000 rows   | 10,000 rows   |
| UPDATE    | ~500                 | 500 rows     | 5,000 rows    |
| DELETE    | ~200                 | 200 rows     | 2,000 rows    |

**Total for Default Configuration (4 operations):**

| Scale Factor | Total Rows Modified | Breakdown |
|--------------|---------------------|-----------|
| 0.1          | ~340 rows           | 2× INSERT, 1× UPDATE, 1× DELETE |
| 1.0          | ~3,400 rows         | 2× INSERT, 1× UPDATE, 1× DELETE |
| 10.0         | ~34,000 rows        | 2× INSERT, 1× UPDATE, 1× DELETE |
| 100.0        | ~340,000 rows       | 2× INSERT, 1× UPDATE, 1× DELETE |

### TPC-DS Code Example

```python
from benchbox.tpcds import TPCDS
from benchbox.platforms.duckdb import DuckDBAdapter
from pathlib import Path

benchmark = TPCDS(scale_factor=1.0, output_dir=Path("./tpcds_data"))
benchmark.generate_data()

print("Step 1: Load fresh database...")
adapter = DuckDBAdapter(database_path="tpcds.duckdb", force_recreate=True)

print("Step 2: Running Power and Throughput tests...")
power_result = adapter.run_benchmark(benchmark, test_execution_type="power")
throughput_result = adapter.run_benchmark(benchmark, test_execution_type="throughput")

print("Step 3: Running Maintenance test (can run immediately after Power/Throughput)...")
maintenance_result = adapter.run_benchmark(benchmark, test_execution_type="maintenance")

print("\n⚠️  Database modified - MUST reload before running Power/Throughput again!")
print("To run more tests: adapter = DuckDBAdapter(database_path='tpcds.duckdb', force_recreate=True)")
```

### TPC-H Refresh Integrity

RF1 uses DBGen's generated update rows and rejects any order key already present before inserting. RF2 reads the DBGen delete keys and verifies that every order exists before deleting LINEITEM children and then ORDERS parents. The function transaction rolls back if a validation or DML operation fails.

### TPC-DS Foreign Key Handling

**Returns Must Reference Sales:**

Per TPC-DS specification section 5.4, all return transactions must reference valid parent sales:

- `STORE_RETURNS` → Must reference `STORE_SALES` (SS_TICKET_NUMBER, SS_ITEM_SK)
- `CATALOG_RETURNS` → Must reference `CATALOG_SALES` (CS_ORDER_NUMBER, CS_ITEM_SK)
- `WEB_RETURNS` → Must reference `WEB_SALES` (WS_ORDER_NUMBER, WS_ITEM_SK)

BenchBox queries actual sales records before generating returns, ensuring all returns have valid parent transactions.

**Dynamic Dimension Range Initialization:**

Instead of hardcoded FK ranges, BenchBox queries dimension tables at runtime:

```python
cursor = connection.execute("SELECT MIN(D_DATE_SK), MAX(D_DATE_SK) FROM DATE_DIM")
min_date, max_date = cursor.fetchone()

date_sk = random.randint(min_date, max_date)
```

This approach works across any scale factor and ensures all generated FKs reference existing dimension records.

**Retry Logic for FK Violations:**

BenchBox includes automatic retry logic for transient FK violations:

- Detects FK constraint errors across different database platforms
- Automatically retries failed batches (up to 3 attempts)
- Re-initializes dimension ranges on retry for fresh FK values
- Logs warnings for FK issues while maintaining operation success

### Performance Optimizations

**Batched Multi-Row INSERTs:**

All insert operations use batched multi-row INSERT statements for ~100x performance improvement:

```sql
INSERT INTO STORE_SALES (...) VALUES (?, ?, ?, ...)
INSERT INTO STORE_SALES (...) VALUES (?, ?, ?, ...)
... (998 more times)

INSERT INTO STORE_SALES (...) VALUES
  (?, ?, ?, ...),
  (?, ?, ?, ...),
  ...
  (?, ?, ?, ...)
```

**Batched DELETE Operations:**

RF2 deletes use batched IN clauses instead of individual DELETE statements:

```sql
DELETE FROM LINEITEM WHERE L_ORDERKEY = ?
DELETE FROM LINEITEM WHERE L_ORDERKEY = ?
... (1498 more times)

DELETE FROM LINEITEM WHERE L_ORDERKEY IN (?, ?, ?, ...)
```

The per-key form issues 1,500 individual DELETE statements. The batched form replaces them with one statement whose IN list covers all 1,500 keys. This provides ~750x performance improvement for delete operations.

**Platform-Specific Parameter Placeholders:**

BenchBox automatically detects the correct SQL parameter placeholder style:

- `?` for SQLite, DuckDB, SQL Server
- `%s` for PostgreSQL, MySQL, Redshift

This ensures compatibility across all supported platforms without manual configuration.

## Why Database Reload Is Required

Understanding why you must reload after Maintenance tests is critical for valid benchmarking:

### 1. Data Changes Are Permanent

All INSERT, UPDATE, and DELETE operations are **committed transactions**. The changes persist in the database permanently. There is no automatic rollback or cleanup.

**What this means:**
- New rows exist in ORDERS, LINEITEM, catalog_sales, etc.
- Existing rows have modified values
- Some rows have been permanently deleted
- All database indexes and statistics are updated

### 2. Query Results Will Differ

Power and Throughput queries execute against the **modified dataset**, producing different results:

**Affected query patterns:**
- `COUNT(*)` returns different row counts
- `SUM(revenue)` calculates different totals
- `AVG(price)` computes different averages
- `JOIN` operations match different rows
- Date range filters include/exclude different records

**Example:**
```sql
SELECT COUNT(*) FROM orders;

SELECT COUNT(*) FROM orders;

SELECT COUNT(*) FROM orders;
```

The same statement run at three points returns these results at scale factor 1.0:

- Before maintenance: 1,500,000 rows (the original count).
- After RF1, which inserted 1,500 orders: 1,501,500 rows, which differs from the original.
- After RF2, which deleted 1,500 orders: 1,500,000 rows again, but the rows are not the original ones.

Even if row counts match after both RF1 and RF2, the **actual data is different** - you deleted old orders and inserted new ones!

### 3. Not Idempotent

Running Maintenance tests multiple times doesn't produce the same result:

- **First run**: Inserts orders 1,501,000-1,502,500, deletes orders 1-1,500
- **Second run**: Inserts orders 1,502,501-1,504,000, deletes orders 1,501-3,000
- **Third run**: Inserts orders 1,504,001-1,505,500, deletes orders 3,001-4,500

Each execution modifies **different data**. There's no "undo" button.

### 4. TPC Specification Requirement

The official TPC specifications explicitly require:

> "The database must be in its initial loaded state before each Performance Test (Power or Throughput). Maintenance operations must not affect the database state for Performance Tests."

**What this means:**
- Official TPC results require clean data for Power/Throughput
- Running tests on modified data **violates the specification**
- Results become non-comparable to other published benchmarks
- Attempting to submit modified-data results would be rejected

## Complete Workflow Examples

### Workflow 1: Power + Throughput Only (No Maintenance)

```python
benchmark.generate_data()

adapter = DuckDBAdapter(database_path="benchmark.duckdb", force_recreate=True)

power_result = adapter.run_benchmark(benchmark, test_execution_type="power")
throughput_result = adapter.run_benchmark(benchmark, test_execution_type="throughput")

```

### Workflow 2: Full Official Benchmark (All Three Tests)

```python
benchmark.generate_data()

adapter = DuckDBAdapter(database_path="benchmark.duckdb", force_recreate=True)

power_result = adapter.run_benchmark(benchmark, test_execution_type="power")

throughput_result = adapter.run_benchmark(benchmark, test_execution_type="throughput")

maintenance_result = adapter.run_benchmark(benchmark, test_execution_type="maintenance")

```

### Workflow 3: Maintenance Only (Testing ETL Performance)

```python
benchmark.generate_data()

adapter = DuckDBAdapter(database_path="benchmark.duckdb", force_recreate=True)

maintenance_result = adapter.run_benchmark(benchmark, test_execution_type="maintenance")

adapter = DuckDBAdapter(database_path="benchmark.duckdb", force_recreate=True)
maintenance_result_2 = adapter.run_benchmark(benchmark, test_execution_type="maintenance")
```

### Workflow 4: Investigating Query Changes After Maintenance

```python
adapter = DuckDBAdapter(database_path="benchmark.duckdb", force_recreate=True)
result_before = adapter.execute("SELECT COUNT(*), SUM(o_totalprice) FROM orders")
print(f"Before: {result_before}")

maintenance_result = adapter.run_benchmark(benchmark, test_execution_type="maintenance")

result_after = adapter.execute("SELECT COUNT(*), SUM(o_totalprice) FROM orders")
print(f"After: {result_after}")
```

## CLI Usage

### Running Individual Tests

```bash
benchbox run --platform duckdb --benchmark tpch --scale 1.0 --phases power

benchbox run --platform duckdb --benchmark tpch --scale 1.0 --phases throughput

benchbox run --platform duckdb --benchmark tpch --scale 1.0 --phases load,maintenance
```

### Correct Workflow

```bash
benchbox run \
  --platform duckdb \
  --benchmark tpch \
  --scale 1.0 \
  --phases generate,load,power,throughput,maintenance

benchbox run \
  --platform duckdb \
  --benchmark tpch \
  --scale 1.0 \
  --phases load,power,throughput
```

### Incorrect Workflows ❌

```bash
benchbox run \
  --platform duckdb \
  --benchmark tpch \
  --scale 1.0 \
  --phases generate,load,power,maintenance,throughput

benchbox run \
  --platform duckdb \
  --benchmark tpch \
  --scale 1.0 \
  --phases generate,load,power,throughput,maintenance
benchbox run \
  --platform duckdb \
  --benchmark tpch \
  --scale 1.0 \
  --phases power
```

If standalone Maintenance runs between Power and Throughput, the Throughput queries run against modified data. With TPC-H refresh integration enabled, Power and Throughput deliberately use consecutive update sets in that order; do not insert standalone Maintenance between them. Reload or choose the next unused update set before repeating a run.

## Troubleshooting

### "I ran Maintenance and now my query results changed"

✅ **This is expected behavior!** Maintenance tests permanently modify data. Reload the database to restore original results.

### "Can I run Power or Throughput after Maintenance?"

For query-only comparisons, load fresh data before Power or Throughput. When TPC-H refresh integration is enabled, the phases intentionally use consecutive update sets in Power-then-Throughput order; run standalone Maintenance afterward with the next unused set or reload first.

### "I want to run Maintenance multiple times to test sustained ETL load"

✅ **Possible, but requires reload between each run:**

```python
for i in range(5):
    adapter = DuckDBAdapter(database_path="benchmark.duckdb", force_recreate=True)

    result = adapter.run_benchmark(benchmark, test_execution_type="maintenance")
    print(f"Run {i+1}: {result.total_execution_time:.2f}s")
```

### "How do I verify my database was modified?"

```python
before = adapter.execute("SELECT COUNT(*) FROM orders")[0][0]

maintenance_result = adapter.run_benchmark(benchmark, test_execution_type="maintenance")

after = adapter.execute("SELECT COUNT(*) FROM orders")[0][0]

print(f"Before: {before}, After: {after}, Difference: {after - before}")
```

### "Can I skip Maintenance tests?"

✅ **Yes, if you only need query performance metrics.** Maintenance tests are only required for:
- Official TPC compliance
- Complete benchmark suite
- ETL/data modification performance evaluation

### "What if my database doesn't support DELETE?"

Some immutable analytical databases (like certain columnar stores) don't support data modifications. In this case:
- Skip Maintenance tests entirely
- Document this limitation in your results
- Note that you cannot achieve full TPC compliance

## Workflow Summary

### Golden Rules

1. Standalone Maintenance can run after Power/Throughput, but its update set must not have been applied already.
2. Reload before reusing DBGen update sets; otherwise advance `tpch_refresh_first_set`.
3. Keep Power and Throughput on the same intended dataset sequence.
4. Run standalone Maintenance last when using query-only tests.

### Correct Sequences

```
✓ generate → load → power → throughput
✓ generate → load → power → throughput → maintenance using next unused set
✓ generate → load → maintenance
✓ maintenance → [RELOAD] → maintenance (repeat from the same set)
```

### Incorrect Sequences

```
✗ generate → load → power → maintenance → throughput ❌
  (Throughput runs on modified data!)

✗ generate → load → maintenance → power → throughput ❌
  (Power and Throughput run on modified data!)

✗ maintenance → [NO RELOAD] → power ❌
  (Power runs on modified data!)

✗ maintenance → [NO RELOAD] → maintenance ❌
  (Second Maintenance runs on already-modified data!)
```

## FAQ

**Q: Do I need to reload between Power and Throughput tests?**
A: Not by default. With TPC-H refresh enabled, Power applies set F and Throughput uses consecutive sets F+1..F+S; do not run standalone Maintenance between them.

**Q: What happens if Maintenance runs before Power/Throughput?**
A: It modifies the database. Reload original data or configure the next unused TPC-H update set before measuring.

**Q: Can refresh functions run during Throughput tests?**
A: Yes, for TPC-H when `tpch_refresh_functions=true`; one RF1/RF2 stream runs concurrently with query streams. This does not imply full TPC certification.

**Q: How long does database reload take?**
A: Depends on scale factor and platform:
- SF=1 on DuckDB: ~30 seconds to 2 minutes
- SF=10 on DuckDB: ~5-15 minutes
- SF=100 on cloud warehouse: ~30 minutes to 2 hours

**Q: Is there a way to avoid reload overhead?**
A: No. Reload is mandatory for valid results. Budget time accordingly.

**Q: Can I use database snapshots instead of full reload?**
A: Yes! This can speed up testing:

```python
adapter.create_snapshot("clean_data")

power_result = adapter.run_benchmark(benchmark, test_execution_type="power")

adapter.restore_snapshot("clean_data")

maintenance_result = adapter.run_benchmark(benchmark, test_execution_type="maintenance")
```

Check your platform's documentation for snapshot support.

**Q: What if my Maintenance test fails?**
A: The database may be in an inconsistent state. Reload and investigate:
- Check database logs for errors
- Verify sufficient disk space
- Confirm write permissions
- Check for constraint violations

**Q: Do I need Maintenance tests for non-TPC benchmarks?**
A: No. Maintenance tests are specific to TPC benchmarks. Other benchmarks (ClickBench, JoinOrder, etc.) don't have maintenance phases.

## See Also

- [TPC-H Official Guide](./tpc-h-official-guide.md) - Complete TPC-H implementation details
- [TPC-DS Official Guide](./tpc-ds-official-guide.md) - Complete TPC-DS implementation details
- [CLI Reference](../../reference/cli/index.md) - Command-line options for running benchmarks
- [Test Types Example](../../../examples/features/test_types.py) - Runnable code demonstrating all three test types
- [TPC-H Specification](https://www.tpc.org/tpc_documents_current_versions/current_specifications5.asp) - Official TPC-H specification
- [TPC-DS Specification](https://www.tpc.org/tpc_documents_current_versions/current_specifications5.asp) - Official TPC-DS specification
