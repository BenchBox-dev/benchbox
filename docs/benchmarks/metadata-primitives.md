<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Metadata Primitives Benchmark

```{tags} intermediate, concept, metadata-primitives, custom-benchmark
```

> **CLI name:** `metadata_primitives` - use `benchbox run --benchmark metadata_primitives`
>
> **Query templates:** [every Metadata Primitives query](queries/metadata_primitives/index.md)

The Metadata Primitives benchmark tests database catalog introspection performance using INFORMATION_SCHEMA views and platform-specific commands (SHOW, DESCRIBE, PRAGMA). Unlike data operation benchmarks, this focuses on metadata operations critical for data catalog integration, schema discovery, and data governance workflows.

## Overview

| Property | Value |
|----------|-------|
| **Total Queries** | 62 |
| **Categories** | 10 |
| **Data Source** | Database catalog (no data generation required) |
| **Supported Platforms** | All SQL platforms |
| **Complexity Testing** | Wide tables, view hierarchies, large catalogs |

### Why Metadata Primitives?

Metadata operations are critical for:

- **Data Catalogs**: Alation, Collibra, Atlan scan database metadata
- **IDE Autocomplete**: IntelliJ, VS Code, DataGrip query INFORMATION_SCHEMA
- **BI Tools**: Tableau, Looker, PowerBI discover schemas for modeling
- **Data Governance**: Access control, lineage, impact analysis
- **DevOps**: Schema migrations, CI/CD pipelines, database testing

## Query Categories

### Core Categories

| Category | Queries | Description |
|----------|---------|-------------|
| `schema` | 9 | Database/schema/table/view listing and discovery |
| `column` | 8 | Column metadata, types, constraints |
| `stats` | 6 | Table statistics, row counts, storage info |
| `query` | 4 | Query execution plans and introspection |
| `acl` | 11 | Access control list introspection and mutations |

### Complexity Categories

| Category | Queries | Description |
|----------|---------|-------------|
| `wide_table` | 5 | Tables with 100-1000+ columns |
| `view_hierarchy` | 4 | Nested view dependency chains |
| `complex_type` | 5 | ARRAY, STRUCT, MAP type handling |
| `large_catalog` | 6 | Catalogs with 100-500+ tables |
| `constraint` | 4 | Foreign key and constraint introspection |

## Quick Start

### CLI Usage

```bash
benchbox run --platform duckdb --benchmark metadata_primitives

benchbox run --platform snowflake --benchmark metadata_primitives \
  --benchmark-option categories=schema,column

benchbox run --platform duckdb --benchmark metadata_primitives \
  --benchmark-option complexity=wide_tables
```

The first command runs the benchmark on DuckDB. The second runs only the schema and column categories. The third runs with the `wide_tables` complexity preset.

### Programmatic Usage

```python
from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark
import duckdb

benchmark = MetadataPrimitivesBenchmark()

conn = duckdb.connect(":memory:")

conn.execute(benchmark.get_create_tables_sql())

result = benchmark.run_benchmark(
    connection=conn,
    dialect="duckdb",
    categories=["schema", "column"]
)

print(f"Queries: {result.total_queries}")
print(f"Successful: {result.successful_queries}")
print(f"Total time: {result.total_time_ms:.1f}ms")

for cat, summary in result.category_summary.items():
    print(f"{cat}: {summary['avg_time_ms']:.2f}ms avg")
```

`get_create_tables_sql()` creates the test schema, which holds TPC-H and TPC-DS tables. The final loop prints the category summary.

## Sample Queries

### Schema Discovery

```sql
SELECT table_name, table_type, table_schema
FROM information_schema.tables
WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
ORDER BY table_name;

SELECT table_name, view_definition
FROM information_schema.views
WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
ORDER BY table_name;
```

The first query is [`schema_list_tables`](queries/metadata_primitives/schema-list-tables.md) (list all tables). The second is [`schema_list_views`](queries/metadata_primitives/schema-list-views.md) (list all views).

### Column Introspection

```sql
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_name = 'lineitem'
ORDER BY ordinal_position;

SELECT data_type, COUNT(*) as count
FROM information_schema.columns
GROUP BY data_type
ORDER BY count DESC;
```

The first query is [`column_for_table`](queries/metadata_primitives/column-for-table.md) (get columns for a specific table). The second is [`column_data_types`](queries/metadata_primitives/column-data-types.md) (get the column type distribution).

### Table Statistics

```sql
SELECT table_name,
       pg_size_pretty(pg_total_relation_size(table_name::regclass)) as size
FROM information_schema.tables
WHERE table_type = 'BASE TABLE';
```

This query is [`stats_table_sizes`](queries/metadata_primitives/stats-table-sizes.md) (get table sizes and row estimates).

### Query Introspection

```sql
EXPLAIN SELECT * FROM lineitem WHERE l_quantity > 10;

EXPLAIN ANALYZE SELECT COUNT(*) FROM orders;
```

The first statement is `query_explain_plan` (get the execution plan). The second is `query_analyze` (get execution statistics).

## Complexity Testing

The benchmark supports stress testing metadata operations under various complexity conditions.

### Complexity Presets

| Preset | Description | Config |
|--------|-------------|--------|
| `wide_tables` | Tables with many columns | 100-1000 columns per table |
| `view_hierarchy` | Nested view chains | 3-10 levels of view dependencies |
| `large_catalog` | Many tables/views | 100-500+ tables |
| `complex_types` | Structured data types | ARRAY, STRUCT, MAP columns |
| `acl_dense` | Heavy permissions | 20+ grants per table |

### Running Complexity Benchmarks

```python
from benchbox.core.metadata_primitives import MetadataPrimitivesBenchmark

benchmark = MetadataPrimitivesBenchmark()

result = benchmark.run_complexity_benchmark(
    connection=conn,
    dialect="duckdb",
    config="wide_tables",
    iterations=3
)

print(f"Setup time: {result.setup_time_ms:.1f}ms")
print(f"Teardown time: {result.teardown_time_ms:.1f}ms")
print(f"Created: {result.generated_metadata.total_objects} objects")
```

This runs the benchmark with the wide tables preset.

### Custom Complexity Configuration

```python
from benchbox.core.metadata_primitives.complexity import (
    MetadataComplexityConfig,
    TypeComplexity,
    ConstraintDensity,
    PermissionDensity,
)

config = MetadataComplexityConfig(
    width_factor=500,
    catalog_size=100,
    view_depth=5,
    type_complexity=TypeComplexity.NESTED,
    constraint_density=ConstraintDensity.MODERATE,
    acl_role_count=10,
    acl_permission_density=PermissionDensity.DENSE,
)

result = benchmark.run_complexity_benchmark(
    connection=conn,
    dialect="duckdb",
    config=config
)
```

## ACL (Access Control) Testing

The benchmark includes comprehensive access control testing.

### ACL Introspection Queries

| Query ID | Description |
|----------|-------------|
| `acl_list_roles` | List all database roles |
| `acl_list_table_grants` | List table-level permissions |
| `acl_list_column_grants` | List column-level permissions |
| `acl_role_hierarchy` | Show role inheritance |
| `acl_user_effective_permissions` | Compute effective permissions |

### ACL Mutation Testing

```python
acl_result = benchmark.run_acl_benchmark(
    connection=conn,
    dialect="snowflake",
    config="acl_dense",
    iterations=3
)

print(f"Setup time: {acl_result.setup_time_ms:.1f}ms")
print(f"GRANTs/second: {acl_result.summary['grants_per_second']:.1f}")
print(f"Total operations: {acl_result.summary['total_operations']}")
```

This ACL benchmark measures GRANT and REVOKE performance.

### ACL Platform Support

Platforms fall into four tiers based on the depth of ACL introspection
exposed via SQL (see `benchbox/core/metadata_primitives/catalog/queries.yaml`
and the `supports_acl*` helpers in `benchbox/core/metadata_primitives/ddl.py`):

| Tier | Meaning | Platforms |
|------|---------|-----------|
| 1 - Full | Full SQL-level ACL introspection via INFORMATION_SCHEMA | PostgreSQL, Redshift, Firebolt, Synapse, Fabric |
| 2 - Partial | GRANT/REVOKE supported; introspection limited to system views | Databricks (Unity Catalog), ClickHouse, Snowflake, Trino, Presto |
| 3 - Limited | ACLs are IAM-managed; SQL introspection is dataset-scoped only | BigQuery |
| 4 - None | No SQL-level ACL support | DuckDB, SQLite, DataFusion, Spark, Polars |

Capability matrix (derived from the `supports_*` predicates in `ddl.py`):

| Platform | Tier | Roles | Table Grants | Column Grants | Role Hierarchy | SQL Introspection |
|----------|------|-------|--------------|---------------|----------------|--------------------|
| PostgreSQL | 1 | Yes | Yes | Yes | Yes | Yes |
| Redshift | 1 | Yes | Yes | Yes | Yes | Yes |
| Firebolt | 1 | Yes | Yes | No | Yes | Yes |
| Synapse | 1 | Yes | Yes | Yes | Yes | Yes |
| Fabric | 1 | Yes | Yes | Yes | Yes | Yes |
| Databricks | 2 | Yes | Yes | Yes | Yes | Yes |
| ClickHouse | 2 | Yes | Yes | No | Yes | Yes |
| Snowflake | 2 | Yes | Yes | Yes | Yes | No |
| BigQuery | 3 | IAM | Dataset | No | No | Dataset-scoped |
| DuckDB | 4 | No | No | No | No | No |
| SQLite | 4 | No | No | No | No | No |

"SQL Introspection" maps to `supports_acl_introspection`, which distinguishes
tiers 1/2 (queryable metadata tables) from tiers 3/4.

## Platform Variants

The benchmark provides platform-specific query variants for optimal performance:

### DuckDB

```sql
PRAGMA table_info('lineitem');
PRAGMA database_list;
```

DuckDB uses `PRAGMA` statements for metadata.

### ClickHouse

```sql
SELECT * FROM system.tables WHERE database = currentDatabase();
SELECT * FROM system.columns WHERE table = 'lineitem';
```

ClickHouse uses system tables.

### Snowflake

```sql
SHOW TABLES;
SHOW COLUMNS IN TABLE lineitem;
DESCRIBE TABLE lineitem;
```

Snowflake uses `SHOW` commands.

### BigQuery

```sql
SELECT * FROM `project.dataset.INFORMATION_SCHEMA.TABLES`;
SELECT * FROM `project.dataset.INFORMATION_SCHEMA.COLUMNS`;
```

BigQuery uses `INFORMATION_SCHEMA` with a project and dataset prefix.

## Result Structure

```python
@dataclass
class MetadataBenchmarkResult:
    total_queries: int = 0
    successful_queries: int = 0
    failed_queries: int = 0
    total_time_ms: float = 0.0
    results: list[MetadataQueryResult] = field(default_factory=list)
    category_summary: dict[str, dict[str, Any]] = field(default_factory=dict)
    acl_mutation_results: list[AclMutationResult] = field(default_factory=list)
    acl_mutation_summary: dict[str, Any] = field(default_factory=dict)

@dataclass
class MetadataQueryResult:
    query_id: str
    category: str
    execution_time_ms: float
    row_count: int = 0
    success: bool = True
    error: str | None = None
```

## Use Cases

### 1. Data Catalog Integration Testing

Measure how quickly catalogs can scan your database:

```python
result = benchmark.run_benchmark(
    connection=conn,
    dialect="snowflake",
    categories=["schema", "column"]
)

assert result.total_time_ms < 5000, "Catalog scan too slow"
```

The run simulates a catalog scan pattern. The assertion checks that the scan meets an SLA, here under 5 seconds.

### 2. IDE Performance Testing

Test autocomplete responsiveness:

```python
column_queries = benchmark.run_benchmark(
    connection=conn,
    dialect="postgresql",
    categories=["column"]
)

avg_time = column_queries.category_summary["column"]["avg_time_ms"]
assert avg_time < 100, f"Column lookup too slow: {avg_time}ms"
```

Column lookup should be fast for autocomplete.

### 3. Schema Migration Validation

Test metadata operations after migrations:

```python
result = benchmark.run_benchmark(conn, "duckdb")

schema_results = [r for r in result.results if r.category == "schema"]
assert all(r.success for r in schema_results), "Schema discovery failed"
```

The run performs full schema discovery, and the assertion verifies that every table is discoverable.

### 4. Access Control Audit

Verify permission introspection:

```python
acl_result = benchmark.run_benchmark(
    conn, "snowflake", categories=["acl"]
)

assert acl_result.successful_queries == acl_result.total_queries
```

The run executes the ACL queries, and the assertion checks that all of them succeed.

## Best Practices

### 1. Start Simple

Run basic categories before complexity testing:

```bash
benchbox run --platform duckdb --benchmark metadata_primitives \
  --benchmark-option categories=schema,column
```

### 2. Use Appropriate Complexity

Match complexity presets to your production environment:

```python
config = "wide_tables"

config = "acl_dense"
```

Use `wide_tables` for a typical OLAP warehouse with denormalized tables. Use `acl_dense` for a data-governance-heavy environment that uses fine-grained permissions.

### 3. Clean Up Test Objects

Always clean up complexity test objects:

```python
benchmark.cleanup_benchmark_objects(conn, "duckdb", prefix="benchbox_")
```

This is a manual cleanup of the objects created under the given prefix.

### 4. Monitor for Regressions

Track metadata performance over time:

```python
results = benchmark.run_benchmark(conn, "duckdb")
baseline_time = results.total_time_ms

new_results = benchmark.run_benchmark(conn, "duckdb")
if new_results.total_time_ms > baseline_time * 1.2:
    print("WARNING: 20%+ performance regression detected")
```

The first run's total time is the saved baseline for comparison with later runs.

## Related Documentation

- [Read Primitives](read-primitives.md) - Data read operation testing
- [Write Primitives](write-primitives.md) - Data write operation testing
- [Transaction Primitives](transaction-primitives.md) - Transaction testing
- [TPC-H](tpc-h.md) - TPC-H benchmark (provides base schema)
- [TPC-DS](tpc-ds.md) - TPC-DS benchmark (provides complex schema)
