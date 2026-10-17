<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# API Reference

```{tags} reference, python-api
```

Complete API documentation for BenchBox classes and methods. This page highlights the most common entry points; the full set of hand-authored API pages lives under the [`python-api/`](python-api) section. The full API reference is published at <https://benchbox.dev/docs/reference/python-api/>.

## Core Classes

### BaseBenchmark

Base abstract class that all benchmarks inherit from.

```python
from benchbox.base import BaseBenchmark

class BaseBenchmark(ABC):
    def __init__(self, scale_factor: float = 1.0, output_dir: Optional[Path] = None):
        pass
```

#### Methods

##### `generate_data() -> List[Path]`
Generate benchmark data files.

**Returns**: List of Path objects pointing to generated data files.

**Example**:
```python
from benchbox import TPCH

tpch = TPCH(scale_factor=0.1)
data_files = tpch.generate_data()
for file_path in data_files:
    table_name = file_path.stem
    print(f"Generated {table_name} at {file_path}")
```

`generate_data()` returns a list of paths, such as `Path("customer.tbl")`, `Path("orders.tbl")` and `Path("lineitem.tbl")`.

##### `get_queries() -> Dict[Union[int, str], str]`
Get all queries for this benchmark.

**Returns**: Dictionary mapping query IDs to SQL query strings.

**Example**:
```python
queries = tpch.get_queries()
```

The result is a dictionary such as `{1: "SELECT l_returnflag...", 2: "SELECT s_acctbal...", ...}`.

##### `get_query(query_id: Union[int, str]) -> str`
Get a specific query by ID.

**Parameters**:
- `query_id`: Query identifier (integer or string)

**Returns**: SQL query string.

**Example**:
```python
query_1 = tpch.get_query(1)

primitives_query = primitives.get_query("aggregation_basic")
```

The first call is a basic query by integer ID. String IDs, as in the second call, are used by some benchmarks.

##### `translate_query(query_id: Union[int, str], dialect: str) -> str`
Translate query to specific SQL dialect.

**Parameters**:
- `query_id`: Query identifier
- `dialect`: Target SQL dialect ("postgres", "mysql", "sqlite", "duckdb", etc.)

**Returns**: Translated SQL query string.

**Example**:
```python
postgres_query = tpch.translate_query(1, "postgres")
mysql_query = tpch.translate_query(1, "mysql")
duckdb_query = tpch.translate_query(1, "duckdb")
```

DuckDB is the recommended default dialect.

##### `get_create_tables_sql() -> str`
Get DDL statements to create benchmark tables.

**Returns**: DDL statements as string in standard SQL format.

**Example**:
```python
ddl = tpch.get_create_tables_sql()
import duckdb
conn = duckdb.connect(":memory:")
conn.execute(ddl)
```

This example uses DuckDB (recommended).

##### `get_schema() -> List[Dict[str, Any]]`
Get benchmark schema information.

**Returns**: List of dictionaries describing tables and columns.

**Example**:
```python
schema = tpch.get_schema()
for table in schema:
    print(f"Table: {table['name']}")
    for column in table['columns']:
        print(f"  {column['name']}: {column['type']}")
```

---

## Benchmark Classes

### TPCH

TPC-H Decision Support Benchmark implementation.

```python
from benchbox import TPCH

tpch = TPCH(scale_factor=1.0, output_dir=None)
```

#### Properties

- **Query Count**: 22 analytical queries (Q1-Q22)
- **Tables**: 8 tables (customer, orders, lineitem, part, partsupp, supplier, nation, region)
- **Scale Factors**: 0.01 to 100000
- **Recommended Database**: DuckDB

#### Example Usage

```python
import duckdb
from benchbox import TPCH

conn = duckdb.connect(":memory:")
tpch = TPCH(scale_factor=0.1, output_dir="./tpch_data")

tpch.generate_data()
conn.execute(tpch.get_create_tables_sql())
for table, path in tpch.tables.items():
    conn.execute(f"INSERT INTO {table} SELECT * FROM read_csv('{path}', delim='|', header=false)")

result = conn.execute(tpch.get_query(1)).fetchall()
```

The example generates and loads the data, then runs a query.

### TPCDS

TPC-DS Decision Support Benchmark implementation.

```python
from benchbox import TPCDS

tpcds = TPCDS(scale_factor=1.0, output_dir=None)
```

#### Properties

- **Query Count**: 99 complex analytical queries
- **Tables**: 24 tables (retail data warehouse schema)
- **Features**: Window functions, CTEs, complex joins
- **Recommended Database**: DuckDB

### Primitives

Database Primitives Benchmark for focused operation testing.

```python
from benchbox import ReadPrimitives

read_primitives = ReadPrimitives(scale_factor=1.0, output_dir=None)
```

#### Additional Methods

##### `get_query_categories() -> List[str]`
Get list of available query categories.

**Returns**: List of category names.

**Example**:
```python
categories = read_primitives.get_query_categories()
```

The result is a list such as `["aggregation", "filter", "groupby", "orderby", ...]`.

##### `get_queries_by_category(category: str) -> Dict[str, str]`
Get queries filtered by category.

**Parameters**:
- `category`: Category name ("aggregation", "filter", "groupby", etc.)

**Returns**: Dictionary mapping query IDs to SQL strings.

**Example**:
```python
agg_queries = read_primitives.get_queries_by_category("aggregation")
filter_queries = read_primitives.get_queries_by_category("filter")
```

### Other Benchmarks

- **SSB**: Star Schema Benchmark
- **AMPLab**: Big data benchmark
- **H2ODB**: Data science benchmark
- **ClickBench**: Analytical benchmark
- **JoinOrder**: Canonical IMDb 2013 JOB benchmark for join-order optimization, fixed at `scale_factor=1.0`
- **TPCDI**: Data integration benchmark (`from benchbox import TPCDI`)
- **WritePrimitives**: Write operation benchmark (INSERT, UPDATE, DELETE, MERGE, etc.)
- **TPCHavoc**: TPC-H syntax variants (`from benchbox import TPCHavoc`)

All benchmarks follow the same basic interface as shown above.

---

## Configuration

### Constructor Parameters

All benchmarks accept these parameters:

| Parameter      | Type             | Default | Description             |
| -------------- | ---------------- | ------- | ----------------------- |
| `scale_factor` | `float`          | `1.0`   | Dataset size multiplier |
| `output_dir`   | `Optional[Path]` | `None`  | Data output directory   |

`JoinOrder` is the fixed-size exception: the public benchmark accepts only
`scale_factor=1.0` because it uses the canonical IMDb 2013 JOB dataset.

### Scale Factor Guidelines

| Scale Factor | Data Size | Use Case          |
| ------------ | --------- | ----------------- |
| 0.001        | ~1MB      | Unit tests        |
| 0.01         | ~10MB     | Development       |
| 0.1          | ~100MB    | Integration tests |
| 1.0          | ~1GB      | Benchmarking      |

### Example Configuration

```python
from pathlib import Path
from benchbox import TPCH

tpch_dev = TPCH(
    scale_factor=0.01,
    output_dir=Path("./benchmark_data")
)

tpch_prod = TPCH(
    scale_factor=1.0,
    output_dir=Path("/var/lib/benchbox")
)
```

The first instance is a development configuration and the second is a production configuration.

---

## Database Integration

### DuckDB (Recommended)

DuckDB is the recommended database for BenchBox:

```python
import duckdb
from benchbox import TPCH

conn = duckdb.connect(":memory:")
tpch = TPCH(scale_factor=0.1)

data_files = tpch.generate_data()
ddl = tpch.get_create_tables_sql()
conn.execute(ddl)

for file_path in data_files:
    table_name = file_path.stem
    conn.execute(f"""
        INSERT INTO {table_name}
        SELECT * FROM read_csv('{file_path}', delimiter='|', header=false)
    """)

for query_id in range(1, 6):
    result = conn.execute(tpch.get_query(query_id)).fetchall()
    print(f"Query {query_id}: {len(result)} rows")
```

DuckDB uses ANSI SQL by default. The loop runs the first five queries.

### Other Databases

For other databases, use SQL dialect translation:

```python
postgres_query = tpch.translate_query(1, "postgres")

mysql_query = tpch.translate_query(1, "mysql")

sqlite_query = tpch.translate_query(1, "sqlite")
```

SQLite is fully supported.

> **Note on Dialect Translation**: BenchBox can translate queries to many SQL dialects via SQLGlot, but this doesn't mean platform adapters exist for connecting to those databases. Currently supported platforms include: DuckDB, SQLite, PostgreSQL, ClickHouse, Databricks SQL, BigQuery, Redshift, Snowflake, Trino, Presto, Amazon Athena, Firebolt, Azure Synapse Analytics, Microsoft Fabric, and more. See the [Platform Documentation](../platforms/index.md) for the full list.

---

## Common Patterns

### Basic Benchmark Execution

```python
from benchbox import TPCH
import time

tpch = TPCH(scale_factor=0.1)

data_files = tpch.generate_data()

queries = tpch.get_queries()
for query_id, query_sql in list(queries.items())[:3]:
    start_time = time.time()
    execution_time = time.time() - start_time
    print(f"Query {query_id}: {execution_time:.3f}s")
```

The loop runs the first three queries. Execute each query with your own database connection between the two `time.time()` calls; as written, the example measures no work.

### Multi-Database Testing

```python
from benchbox import TPCH

tpch = TPCH(scale_factor=0.01)
data_files = tpch.generate_data()

dialects = ["duckdb", "clickhouse", "sqlite"]
for dialect in dialects:
    translated_query = tpch.translate_query(1, dialect)
    print(f"{dialect}: {len(translated_query)} chars")
```

This tests different SQL dialects. Translation is not the same as having a platform adapter, so use supported platforms.

---

## See Also

- [Getting Started](../usage/cli-quick-start.md) - Basic usage tutorial
- [Examples](../usage/examples/index.md) - Practical code examples
- [Configuration](../usage/configuration.md) - Configuration guide
- [Benchmarks](../benchmarks/index.md) - Individual benchmark documentation

---

*This API reference covers the core BenchBox functionality. For implementation details, see the source code documentation.*
