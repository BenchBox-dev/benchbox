# SQL Dialect Translation Guide

```{tags} intermediate, guide, sql-platform
```

## Overview

BenchBox provides powerful SQL dialect translation capabilities via SQLGlot, allowing queries to be translated between different database dialects. However, it's crucial to understand the distinction between **dialect translation** and **platform adapters**.

## Key Distinction

### Dialect Translation (Query Translation)

**What it is**: The ability to convert SQL queries from one dialect to another using SQLGlot.

**What it does**:

- Translates SQL syntax between dialects (e.g., `::DATE` in PostgreSQL to `CAST(... AS DATE)` in BigQuery)
- Handles dialect-specific functions and operators
- Converts data type syntax
- Adapts query structure for target database

**Supported dialects** (via SQLGlot):

- postgres, mysql, sqlite, oracle, mssql (SQL Server)
- duckdb, clickhouse, databricks, snowflake, bigquery, redshift
- athena, trino, hive, presto, and many more

**Example**:

```python
from benchbox import TPCH

tpch = TPCH(scale_factor=0.01)

postgres_query = tpch.translate_query(1, "postgres")

mysql_query = tpch.translate_query(1, "mysql")
```

Both translations work even though PostgreSQL and MySQL adapters do not exist yet.

### Platform Adapters (Database Connectivity)

**What it is**: Complete integration with a specific database platform, including connection management, data loading, query execution, and result collection.

**What it provides**:

- Database connection handling
- Authentication and credential management
- Data loading optimizations
- Query execution with proper error handling
- Performance metrics collection
- Platform-specific tuning

**Currently supported platforms** (adapters available):

*Local/Embedded*:

- ✅ DuckDB - Built-in, no extra dependencies
- ✅ SQLite - Built-in, no extra dependencies
- ✅ DataFusion - Requires `datafusion`
- ✅ ClickHouse - Requires `clickhouse-driver` or `chdb`

*Cloud Data Warehouses*:

- ✅ Snowflake - Requires `snowflake-connector-python`
- ✅ Databricks - Requires `databricks-sql-connector`
- ✅ BigQuery - Requires `google-cloud-bigquery`
- ✅ Redshift - Requires `redshift-connector`
- ✅ MotherDuck - Requires `duckdb` (serverless DuckDB)

*Self-Hosted/Federated*:

- ✅ PostgreSQL - Requires `psycopg` (install with `benchbox[postgresql]`)
- ✅ Trino - Requires `trino`
- ✅ Starburst - Requires `trino` (managed Trino)
- ✅ Presto - Requires `presto-python-client`
- ✅ TimescaleDB - Requires `psycopg` (install with `benchbox[postgresql]`)

*Spark Platforms*:

- ✅ Apache Spark SQL - Requires `pyspark`
- ✅ AWS Glue - Requires `boto3`
- ✅ EMR Serverless - Requires `boto3`
- ✅ Google Cloud Dataproc - Requires `google-cloud-dataproc`

*Azure Platforms*:

- ✅ Azure Synapse Analytics - Requires `pyodbc` + `azure-identity`
- ✅ Microsoft Fabric Warehouse - Requires `pyodbc` + `azure-identity`
- ✅ Athena - Requires `pyathena`
- ✅ Firebolt - Requires `firebolt-sdk`

**Planned platforms** (see [Development Roadmap](../development/roadmap.md)):

- 🔄 MySQL
- 🔄 LakeSail Spark
- 🔄 Apache Doris
- 🔄 StarRocks

## Common Confusion

### ❌ Incorrect Assumption

> "BenchBox can translate queries to MySQL, so I can use `--platform mysql` in the CLI."

**Why this fails**: Dialect translation only converts the SQL syntax. The platform adapter (which handles connections, data loading, etc.) doesn't exist yet for MySQL.

### ✅ Correct Understanding

> "BenchBox can translate queries to MySQL dialect. I can use these translated queries with my own MySQL connection, but BenchBox doesn't have a built-in MySQL adapter yet."

**How to use dialect translation for unsupported platforms**: you provide the connection (here `mysql.connector`) and handle execution yourself.
```python
from benchbox import TPCH
import mysql.connector

tpch = TPCH(scale_factor=0.01)
tpch.generate_data()

query = tpch.translate_query(1, "mysql")

conn = mysql.connector.connect(host="localhost", user="root", database="benchmark")
cursor = conn.cursor()
cursor.execute(query)
results = cursor.fetchall()
```

> **Note**: PostgreSQL *does* have a platform adapter (`--platform postgresql`). The example above uses MySQL to illustrate dialect translation for platforms without adapters.

## Use Cases for Dialect Translation

### 1. Custom Integration

Translate queries for databases where you have your own connection:

```python
from benchbox import TPCH
import mysql.connector

benchmark = TPCH(scale_factor=0.01)
benchmark.generate_data()

mysql_conn = mysql.connector.connect(
    host="localhost",
    user="root",
    database="benchbox"
)

for query_id in range(1, 23):
    query = benchmark.translate_query(query_id, "mysql")

    cursor = mysql_conn.cursor()
    cursor.execute(query)
    results = cursor.fetchall()
    print(f"Query {query_id}: {len(results)} rows")
```

### 2. Query Portability Testing

Test how queries translate across different dialects:

```python
from benchbox import TPCH

benchmark = TPCH(scale_factor=0.01)

dialects = ["postgres", "mysql", "oracle", "mssql"]

for dialect in dialects:
    translated = benchmark.translate_query(1, dialect)
    print(f"\n=== {dialect.upper()} ===")
    print(translated[:200] + "...")
```

### 3. SQL Compatibility Analysis

Understand dialect differences before migration:

```python
from benchbox import TPCH

benchmark = TPCH(scale_factor=0.01)

source_dialect = "postgres"
target_dialect = "mysql"

differences = []
for query_id in range(1, 23):
    source_query = benchmark.translate_query(query_id, source_dialect)
    target_query = benchmark.translate_query(query_id, target_dialect)

    if source_query != target_query:
        differences.append({
            "query_id": query_id,
            "source_length": len(source_query),
            "target_length": len(target_query)
        })

print(f"Found {len(differences)} queries with dialect differences")
```

## Using Platform Adapters (When Available)

For supported platforms, use the built-in adapters for complete integration:

```python
from benchbox import TPCH
from benchbox.platforms import DuckDBAdapter

benchmark = TPCH(scale_factor=0.01)

adapter = DuckDBAdapter()
results = benchmark.run_with_platform(adapter)

print(f"Completed {results.successful_queries}/{results.total_queries} queries")
```

**Advantages of platform adapters**:

- ✅ Automatic connection management
- ✅ Optimized data loading
- ✅ Built-in error handling
- ✅ Performance metrics collection
- ✅ Platform-specific optimizations
- ✅ Credential management
- ✅ Result validation

## Translation Limitations

### What Dialect Translation Cannot Do

1. **Execute queries**: Translation only converts syntax, doesn't run queries
2. **Load data**: No built-in data loading to the target database
3. **Handle authentication**: You must manage credentials yourself
4. **Optimize for platform**: Generic translation may not be optimal
5. **Handle platform-specific features**: Custom extensions may not translate

### Translation Quality

Not all SQL features translate perfectly. Most queries translate successfully, but some complex queries may fail. In that case, fall back to a more compatible dialect:

```python
from benchbox import TPCH

benchmark = TPCH(scale_factor=0.01)

try:
    query = benchmark.translate_query(1, "mysql")
    print("✅ Translation succeeded")
except Exception as e:
    print(f"❌ Translation failed: {e}")
    query = benchmark.translate_query(1, "postgres")
```

**Best practices**:

- Always test translated queries on the target platform
- Be aware of platform-specific limitations
- Use fallback dialects for complex cases
- Validate results against source database

## When to Use Each Approach

### Use Dialect Translation When

- ✅ You have your own database connection
- ✅ The platform adapter doesn't exist yet
- ✅ You need custom control over execution
- ✅ You're analyzing SQL portability
- ✅ You're testing query compatibility

### Use Platform Adapters When

- ✅ The adapter exists for your database
- ✅ You want automated data loading
- ✅ You need comprehensive benchmarking
- ✅ You want optimized performance
- ✅ You need consistent result collection

## API Reference

### Translation Methods

All benchmark classes inherit from `BaseBenchmark` and provide `translate_query()`. It takes a query identifier and a target SQL dialect (`postgres`, `mysql`, and so on), and returns the translated SQL query string. It raises `ValueError` if the query ID or dialect is invalid, and `ImportError` if sqlglot is not installed.

```python
def translate_query(
    self,
    query_id: Union[int, str],
    dialect: str
) -> str:
    pass
```

### Alternative: get_query with dialect

Both methods are equivalent:

```python
query1 = benchmark.translate_query(1, "mysql")
query2 = benchmark.get_query(1, dialect="mysql")

assert query1 == query2
```

## See Also

- [Development Roadmap](../development/roadmap.md) - Planned platform and benchmark additions
- [API Reference](../reference/api-reference.md) - Complete API documentation
- [Utilities](../reference/python-api/utilities.md) - Dialect translation utilities
- [Adding New Platforms](../development/adding-new-platforms.md) - Build custom adapters

## Summary

| Capability                | Dialect Translation         | Platform Adapters      |
| ------------------------- | --------------------------- | ---------------------- |
| **SQL Syntax Conversion** | ✅ Yes                      | ✅ Yes (automatic)     |
| **Database Connection**   | ❌ No (you provide)         | ✅ Yes (built-in)      |
| **Data Loading**          | ❌ No (manual)              | ✅ Yes (optimized)     |
| **Query Execution**       | ❌ No (you handle)          | ✅ Yes (automated)     |
| **Error Handling**        | ❌ No (your responsibility) | ✅ Yes (comprehensive) |
| **Performance Metrics**   | ❌ No                       | ✅ Yes (detailed)      |
| **Supported Databases**   | 🌐 Many (via SQLGlot)       | 🎯 22+ platforms       |

**Key Takeaway**: Dialect translation is powerful but limited. For production benchmarking, use platform adapters when available. For unsupported platforms, use dialect translation with your own integration code while waiting for official adapters.
