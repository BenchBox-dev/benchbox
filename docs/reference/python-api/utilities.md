---
myst:
  enable_extensions:
    - attrs_block
---
<!-- markdownlint-disable MD024 -->

# Utility Functions API

```{tags} reference, python-api
```

Complete Python API reference for BenchBox utility functions.

## Overview

BenchBox provides utility functions for common tasks like SQL dialect translation. These utilities simplify cross-database benchmarking and platform-specific optimizations. This page covers dialect normalization and query translation; the other utility modules are listed in {doc}`utilities-index`.

**Available Utilities**:

- **Dialect Translation**: SQL dialect normalization and query translation

## Dialect Translation

```{important}
**Dialect Translation vs Platform Adapters**

BenchBox can translate queries to many SQL dialects via SQLGlot (PostgreSQL, MySQL, SQL Server, Oracle, etc.),
but **dialect translation does not mean platform adapters exist** for connecting to those databases.

**Currently supported platforms**: DuckDB, SQLite, PostgreSQL, TimescaleDB, ClickHouse, Databricks SQL, BigQuery, Redshift, Snowflake, Trino, Presto, Amazon Athena, Firebolt, Azure Synapse Analytics, Microsoft Fabric, and many more (see {doc}`/platforms/index`)

**Planned platforms**: MySQL, SQL Server, and others (see {doc}`/development/roadmap`)

The examples below demonstrate dialect translation capabilities - you can use translated queries with your own
database connections, but BenchBox's built-in platform adapters are limited to the supported platforms listed above.
```

### SQL Dialect Normalization

#### `benchbox.utils.dialect_utils.normalize_dialect_for_sqlglot`

<span id="benchbox.utils.dialect_utils.normalize_dialect_for_sqlglot"></span>

Returns the SQLGlot dialect name to use for a database name, mapping a few databases that SQLGlot lacks onto PostgreSQL.

**Import:** `from benchbox.utils.dialect_utils import normalize_dialect_for_sqlglot` · **Extras:** none

##### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `dialect` | `str` | required | The dialect or database name. Matching ignores case but not surrounding spaces. |

##### Returns

`str`: the lower-case form of `dialect`, except for these names, which return `"postgres"`:

| Name | Result |
| --- | --- |
| `netezza` | `postgres` |
| `greenplum` | `postgres` |
| `vertica` | `postgres` |
| `datafusion` | `postgres` |
| `ansi` | `postgres` |
| `standard` | `postgres` |

Every other name is returned lower-cased and unchanged. The function does not check that SQLGlot knows the result: `"postgresql"` and `"not_a_dialect"` pass through as given, and `None` or `""` returns `""`.

##### Raises

Nothing it raises itself. A value that is neither a string nor empty, such as `5`, raises `AttributeError`.

##### Example

```python
from benchbox.utils.dialect_utils import normalize_dialect_for_sqlglot

normalized = normalize_dialect_for_sqlglot("netezza")
assert normalized == "postgres"

normalized = normalize_dialect_for_sqlglot("duckdb")
assert normalized == "duckdb"

normalized = normalize_dialect_for_sqlglot("SNOWFLAKE")
assert normalized == "snowflake"

print(normalize_dialect_for_sqlglot("postgresql"))
print(repr(normalize_dialect_for_sqlglot(None)))
```

Netezza maps to PostgreSQL. Supported dialects pass through unchanged, and matching is case-insensitive.

```text
postgresql
''
```

**Supported Dialects**:

The bundled SQLGlot runtime (based on upstream 30.24.0) registers these dialect names, among others. The function passes them through unchanged:

- **Cloud**: athena, bigquery, databricks, redshift, snowflake
- **Open Source**: clickhouse, duckdb, mysql, postgres, sqlite
- **Enterprise**: oracle, teradata, tsql (SQL Server)
- **Big Data**: drill, druid, hive, presto, spark, spark2, trino
- **Other**: doris, dune, materialize, prql, risingwave, starrocks, tableau

BenchBox ships its pinned pure-Python SQLGlot wheel from [BenchBox-dev/benchbox-sqlglot](https://github.com/BenchBox-dev/benchbox-sqlglot) inside the BenchBox wheel and sdist. No separate SQLGlot installation, Git fetch or custom package index is required. Runtime imports use `benchbox._vendor.sqlglot`; a public `sqlglot` installation does not select BenchBox's translator. See the [SQLGlot ADR](../../development/adr/adr-sqlglot-use-and-non-use.md) for pin refresh and rollback.

**Usage in Benchmarks**:

```python
from benchbox.tpch import TPCH
from benchbox.utils.dialect_utils import normalize_dialect_for_sqlglot

benchmark = TPCH(scale_factor=1.0)

target_dialect = normalize_dialect_for_sqlglot("netezza")
query_netezza = benchmark.get_query(1, dialect=target_dialect)
```

The example gets Q1 translated for Netezza, which uses the PostgreSQL dialect.

### Query Translation

The `translate_query` method is available on all benchmark classes via `BaseBenchmark`.

**Method Signature**:

```python
def translate_query(
    self,
    query_id: Union[int, str],
    dialect: str
) -> str: ...
```

**Parameters**:

- **query_id** (int|str): Query identifier. TPC-H and TPC-DS take an integer; ClickBench takes a string such as `"Q1"`, and SSB takes strings such as `"Q1.1"`.
- **dialect** (str): Target SQL dialect

**Returns**: Translated query string

**Raises**:

- **ValueError**: If query_id is out of range (TPC-H accepts 1 to 22)
- **ValueError**: If dialect is not supported by SQLGlot (for example `Unknown dialect 'nonsense_dialect'`)
- **TypeError**: If a TPC-H or TPC-DS query_id is not an integer

**Basic Translation**:

The example translates TPC-H Query 1 to several dialects.

```python
from benchbox.tpch import TPCH

benchmark = TPCH(scale_factor=1.0)

q1_duckdb = benchmark.translate_query(1, "duckdb")
q1_postgres = benchmark.translate_query(1, "postgres")
q1_bigquery = benchmark.translate_query(1, "bigquery")
q1_snowflake = benchmark.translate_query(1, "snowflake")

print("DuckDB:")
print(q1_duckdb)
print("\nPostgreSQL:")
print(q1_postgres)
```

**Batch Translation**:

TPC-H has 22 queries. The example translates all of them to Snowflake.

```python
from benchbox.tpch import TPCH

benchmark = TPCH(scale_factor=1.0)
target_dialect = "snowflake"

translated_queries = {}

for query_id in range(1, 23):
    try:
        translated = benchmark.translate_query(query_id, target_dialect)
        translated_queries[f"Q{query_id}"] = translated
    except Exception as e:
        print(f"Failed to translate Q{query_id}: {e}")

print(f"Successfully translated {len(translated_queries)} queries to {target_dialect}")
```

```text
Successfully translated 22 queries to snowflake
```

**Cross-Platform Validation**:

The example tests the translation of one query across multiple platforms and then prints the results.

```python
from benchbox.clickbench import ClickBench

benchmark = ClickBench(scale_factor=0.01)

dialects_to_test = ["duckdb", "postgres", "mysql", "bigquery", "snowflake"]
query_id = "Q1"

translation_results = {}

for dialect in dialects_to_test:
    try:
        translated = benchmark.translate_query(query_id, dialect)
        translation_results[dialect] = {
            "success": True,
            "query_length": len(translated),
            "query": translated[:100] + "..." if len(translated) > 100 else translated
        }
    except Exception as e:
        translation_results[dialect] = {
            "success": False,
            "error": str(e)
        }

print("Translation Results:")
for dialect, result in translation_results.items():
    if result["success"]:
        print(f"  {dialect:15s}: SUCCESS ({result['query_length']} chars)")
    else:
        print(f"  {dialect:15s}: FAILED - {result['error']}")
```

```text
Translation Results:
  duckdb         : SUCCESS (27 chars)
  postgres       : SUCCESS (27 chars)
  mysql          : SUCCESS (27 chars)
  bigquery       : SUCCESS (27 chars)
  snowflake      : SUCCESS (27 chars)
```

### Execution-Time Rewrites

#### `benchbox.utils.dialect_utils.apply_rewrite`

```python
def apply_rewrite(name: str, sql_or_expression, dialect: str, **args): ...
```

Applies one named rewrite from the bundled SQLGlot runtime to SQL that is already translated for `dialect`. It is the only sanctioned entry for rewrites that adapters run at execution time; no other BenchBox module imports the runtime's rewrite functions.

**Parameters**:

- `name`: a public expression rewrite in `benchbox._vendor.sqlglot.benchbox` (`safe_division`, `deduplicate_output_aliases`, `flag_comparisons_to_boolean`, `fold_between_literal_arithmetic`, `date_integer_arithmetic_to_interval`, and the engine, compatibility and subquery rewrites). An unknown name raises `ValueError`.
- `sql_or_expression`: a SQL string, parsed and printed with `dialect`, or a parsed expression. The result has the same type as the input.
- `dialect`: the dialect of the SQL, normalized like `translate_sql_query` (see `normalize_dialect_for_sqlglot`). A rewrite that takes a `dialect` argument receives the normalized name automatically.
- `**args`: the rewrite's own arguments. Gates such as column and table names are always arguments; the runtime holds no benchmark data.

```python
from benchbox.utils.dialect_utils import apply_rewrite

print(
    apply_rewrite(
        "fold_between_literal_arithmetic",
        "SELECT 1 FROM lineitem WHERE l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01",
        "sqlite",
        columns=["l_discount"],
        tables=["lineitem"],
    )
)
```

```text
SELECT 1 FROM lineitem WHERE l_discount BETWEEN 0.05 AND 0.07
```

## Execution Configuration

### `benchbox.utils.ExecutionConfigHelper`

<span id="benchbox.utils.ExecutionConfigHelper"></span>

Reads and changes the power-run and concurrent-query execution settings that are stored in a configuration provider, and applies named performance profiles to them.

**Import:** `from benchbox.utils import ExecutionConfigHelper` · **Extras:** none

{#execution-config-helper-parameters}

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `config_manager` | object with `get(key, default)` and `set(key, value)` or `None` | `None` | The provider that holds the settings. `None` uses the process-wide provider, or a new in-memory provider with the defaults below when none has been registered. Each helper created that way has its own settings. |

{#execution-config-helper-returns}

#### Returns

An `ExecutionConfigHelper` with the provider in `config_manager`. Its methods return these values.

| Method | Returns |
| --- | --- |
| `get_power_run_settings()` | A `PowerRunSettings` dataclass with `iterations` (default 4), `warm_up_iterations` (0), `timeout_per_iteration_minutes` (60), `fail_fast` (`False`) and `collect_metrics` (`True`). |
| `get_concurrent_queries_settings()` | A `ConcurrentQueriesSettings` dataclass with `enabled` (`False`), `max_concurrent` (2), `query_timeout_seconds` (300), `stream_timeout_seconds` (`None`, not set), `retry_failed_queries` (`True`), `max_retries` (3) and `cancel_on_timeout` (`False`). |
| `update_power_run_settings(settings)` and `update_concurrent_queries_settings(settings)` | `None`. Write every field of the settings object to the provider, except a `stream_timeout_seconds` of `None`, which is left unwritten so the benchmark default applies. |
| `enable_power_run_iterations(iterations: int = 3, warm_up_iterations: int = 1)` | `None`. Sets the two counts. |
| `enable_concurrent_queries(max_concurrent: int = 2)` | `None`. Sets `enabled` to `True` and `max_concurrent`. |
| `disable_concurrent_queries()` | `None`. Sets `enabled` to `False`. |
| `optimize_for_system(cpu_cores: int, memory_gb: float)` | `None`. Sets `max_concurrent` to `cpu_cores // 4`, at least 2 and at most 8. Below 8 GB it sets the iteration timeout to 120 minutes and the query timeout to 600 seconds; above 16 GB, to 45 minutes and 180 seconds. From 8 to 16 GB the timeouts stay as they are. It never writes a stream timeout. |
| `create_performance_profile(profile_name: str)` | `dict` with `name`, `power_run` and `concurrent_queries`, without changing any setting. The profiles are `quick`, `standard`, `thorough` and `stress`, see below. |
| `apply_performance_profile(profile_name: str)` | `None`. Writes the profile's settings to the provider. Profiles do not set a stream timeout. |
| `get_execution_summary()` | `dict` with `power_run` (`enabled`, `total_iterations`, `estimated_duration_minutes`, `settings`), `concurrent_queries` (`enabled`, `max_streams`, `estimated_stream_duration_minutes`, which is `None` when no stream timeout is set or it is 0, and `settings`) and `general` (`max_workers`, `memory_limit_gb`, `parallel_queries`). |
| `save_config()` and `validate_execution_config()` | Call `save_config()` and `validate_config()` on the provider and return what `validate_config()` returns. |

Performance profiles:

| Profile | Iterations | Warm-up | Iteration timeout (min) | Concurrent queries | Max concurrent |
| --- | --- | --- | --- | --- | --- |
| `quick` | 1 | 0 | 30 | off | 2 |
| `standard` | 3 | 1 | 60 | on | 2 |
| `thorough` | 5 | 2 | 120 | on | 4 |
| `stress` | 10 | 3 | 180 | on | 8 |

{#execution-config-helper-raises}

#### Raises

- `create_performance_profile` and `apply_performance_profile`: `ValueError` for an unknown profile (`Unknown performance profile: fast. Available: ['quick', 'standard', 'thorough', 'stress']`).
- `save_config` and `validate_execution_config`: `AttributeError` when the provider has no `save_config` or `validate_config` method, which is the case for the default in-memory provider.

{#execution-config-helper-example}

#### Example

```python
from benchbox.utils import ExecutionConfigHelper

helper = ExecutionConfigHelper()
print(helper.get_power_run_settings().iterations, helper.get_concurrent_queries_settings().enabled)

helper.apply_performance_profile("thorough")
print(helper.get_power_run_settings().to_dict())

helper.optimize_for_system(cpu_cores=16, memory_gb=32)
print(helper.get_power_run_settings().timeout_per_iteration_minutes, helper.get_concurrent_queries_settings().max_concurrent)

summary = helper.get_execution_summary()
print(summary["power_run"]["total_iterations"], summary["general"])

try:
    helper.create_performance_profile("fast")
except ValueError as exc:
    print(exc)

try:
    helper.save_config()
except AttributeError as exc:
    print(exc)
```

Output on 0.4.1:

```text
4 False
{'iterations': 5, 'warm_up_iterations': 2, 'timeout_per_iteration_minutes': 120, 'fail_fast': False, 'collect_metrics': True}
45 4
7 {'max_workers': 4, 'memory_limit_gb': 8, 'parallel_queries': False}
Unknown performance profile: fast. Available: ['quick', 'standard', 'thorough', 'stress']
'SimpleConfigProvider' object has no attribute 'save_config'
```

{#execution-config-helper-compatibility}

#### Compatibility

`PowerRunSettings` and `ConcurrentQueriesSettings` are importable from `benchbox.utils` and `benchbox.utils.config_helpers`. A helper created without a provider does not share settings with other helpers: changes stay in that helper's own provider.

## Usage Examples

### Multi-Dialect Benchmark

Run benchmarks across multiple SQL dialects to test query compatibility. The example needs the DuckDB package (`pip install duckdb`). It tests query translation for different target databases. It translates each query and records whether the translation succeeded. The translated SQL is not executed, because it may not run on DuckDB. A summary follows the results.

```python
from benchbox.ssb import SSB
from benchbox.platforms.duckdb import DuckDBAdapter
import time

benchmark = SSB(scale_factor=0.01)
benchmark.generate_data()

adapter = DuckDBAdapter()
conn = adapter.create_connection()
adapter.create_schema(benchmark, conn)
adapter.load_data(benchmark, conn, benchmark.output_dir)

target_dialects = ["postgres", "mysql", "bigquery"]
query_ids = ["Q1.1", "Q1.2", "Q1.3"]

dialect_results = {}

for dialect in target_dialects:
    print(f"\nTesting {dialect} translations:")
    dialect_results[dialect] = []

    for query_id in query_ids:
        try:
            translated_query = benchmark.translate_query(query_id, dialect)

            result = {
                "query_id": query_id,
                "translated": True,
                "length": len(translated_query)
            }

            print(f"  {query_id}: Translated ({len(translated_query)} chars)")

        except Exception as e:
            result = {
                "query_id": query_id,
                "translated": False,
                "error": str(e)
            }
            print(f"  {query_id}: Failed - {e}")

        dialect_results[dialect].append(result)

print("\nTranslation Summary:")
for dialect, results in dialect_results.items():
    success_count = sum(1 for r in results if r["translated"])
    print(f"  {dialect}: {success_count}/{len(results)} successful")
```

```text
  ✅ Loaded 300 rows into customer from 1 shard(s)
  ✅ Loaded 2,557 rows into date from 1 shard(s)
  ✅ Loaded 60,000 rows into lineorder from 1 shard(s)
  ✅ Loaded 2,000 rows into part from 1 shard(s)
  ✅ Loaded 20 rows into supplier from 1 shard(s)

Testing postgres translations:
  Q1.1: Translated (196 chars)
  Q1.2: Translated (219 chars)
  Q1.3: Translated (235 chars)

Testing mysql translations:
  Q1.1: Translated (196 chars)
  Q1.2: Translated (219 chars)
  Q1.3: Translated (235 chars)

Testing bigquery translations:
  Q1.1: Translated (196 chars)
  Q1.2: Translated (219 chars)
  Q1.3: Translated (235 chars)

Translation Summary:
  postgres: 3/3 successful
  mysql: 3/3 successful
  bigquery: 3/3 successful
```

### Automated Dialect Testing

Validate SQL dialect translation quality. The `validate_dialect_support` method checks whether a dialect is supported and working. It translates the first 5 queries, and the example prints the first 3 errors for each dialect:

```python
from benchbox.tpch import TPCH
from benchbox.utils.dialect_utils import normalize_dialect_for_sqlglot

class DialectValidator:
    def __init__(self, benchmark):
        self.benchmark = benchmark

    def validate_dialect_support(self, dialect: str) -> dict:
        normalized = normalize_dialect_for_sqlglot(dialect)

        results = {
            "dialect": dialect,
            "normalized": normalized,
            "supported": True,
            "translated_queries": 0,
            "failed_queries": 0,
            "errors": []
        }

        sample_queries = list(range(1, 6))

        for query_id in sample_queries:
            try:
                translated = self.benchmark.translate_query(query_id, normalized)
                results["translated_queries"] += 1
            except Exception as e:
                results["failed_queries"] += 1
                results["errors"].append({
                    "query_id": query_id,
                    "error": str(e)
                })

        results["supported"] = results["failed_queries"] == 0

        return results

benchmark = TPCH(scale_factor=0.01)
validator = DialectValidator(benchmark)

dialects_to_validate = [
    "duckdb", "postgres", "mysql", "bigquery",
    "snowflake", "redshift", "clickhouse", "netezza"
]

print("Dialect Validation Results:")
print("=" * 70)

for dialect in dialects_to_validate:
    result = validator.validate_dialect_support(dialect)

    status = "✓ SUPPORTED" if result["supported"] else "✗ ISSUES"
    print(f"\n{dialect.upper()} → {result['normalized']}: {status}")
    print(f"  Translated: {result['translated_queries']}/{result['translated_queries'] + result['failed_queries']}")

    if result["errors"]:
        print(f"  Errors:")
        for error in result["errors"][:3]:
            print(f"    Q{error['query_id']}: {error['error'][:60]}...")
```

The last block of the output, where `netezza` is normalized to `postgres`:

```text
NETEZZA → postgres: ✓ SUPPORTED
  Translated: 5/5
```

### Custom Dialect Handling

Handle custom or proprietary database dialects. The handler maps custom dialects to the closest SQLGlot-supported dialect. Exasol ships its own SQLGlot dialect, so the identity entry (a native dialect with no fallback) keeps the generic fallback branch from defaulting it to postgres. Vertica uses PostgreSQL syntax, Greenplum is PostgreSQL-based, Yellowbrick uses PostgreSQL syntax, and MonetDB has PostgreSQL compatibility. `get_fallback_dialect` first tries the official normalization, then checks the custom mappings when no official mapping was found. The example translates one query for Vertica and one for Greenplum:

```python
from benchbox.tpcds import TPCDS
from benchbox.utils.dialect_utils import normalize_dialect_for_sqlglot

class CustomDialectHandler:
    CUSTOM_DIALECT_MAP = {
        "exasol": "exasol",
        "vertica": "postgres",
        "greenplum": "postgres",
        "yellowbrick": "postgres",
        "monetdb": "postgres",
    }

    @classmethod
    def get_fallback_dialect(cls, dialect: str) -> str:
        normalized = normalize_dialect_for_sqlglot(dialect)

        if normalized == dialect.lower():
            return cls.CUSTOM_DIALECT_MAP.get(dialect.lower(), "postgres")

        return normalized

    @classmethod
    def translate_for_custom_dialect(
        cls,
        benchmark,
        query_id: int,
        target_dialect: str
    ) -> str:
        fallback_dialect = cls.get_fallback_dialect(target_dialect)

        print(f"Translating {query_id} for {target_dialect} "
              f"(using {fallback_dialect} dialect)")

        return benchmark.translate_query(query_id, fallback_dialect)

benchmark = TPCDS(scale_factor=0.1)

q1_vertica = CustomDialectHandler.translate_for_custom_dialect(
    benchmark, 1, "vertica"
)

q2_greenplum = CustomDialectHandler.translate_for_custom_dialect(
    benchmark, 2, "greenplum"
)
```

```text
Translating 1 for vertica (using postgres dialect)
Translating 2 for greenplum (using postgres dialect)
```

TPC-DS query IDs are integers, so the example passes `1` and `2`; a string such as `"Q1"` raises `TypeError`.

## Best Practices

### Dialect Translation

1. **Always validate translations**: Test translated queries on target platform before production use. The example validates a translated query by running it on the target platform.

   ```python
   translated = benchmark.translate_query(1, "postgres")

   try:
       result = postgres_conn.execute(translated)
       print("Translation validated successfully")
   except Exception as e:
       print(f"Translation needs adjustment: {e}")
   ```

2. **Use dialect normalization**: Normalize dialects before translation. The example normalizes the dialect before use.

   ```python
   from benchbox.utils.dialect_utils import normalize_dialect_for_sqlglot

   target_dialect = normalize_dialect_for_sqlglot(user_input_dialect)
   query = benchmark.translate_query(1, target_dialect)
   ```

3. **Handle translation failures gracefully**: Not all SQL features translate perfectly. If the dialect is not supported, fall back to a compatible dialect.

   ```python
   try:
       translated = benchmark.translate_query(query_id, dialect)
   except ValueError as e:
       print(f"Dialect not supported: {e}")
       translated = benchmark.translate_query(query_id, "postgres")
   ```

4. **Cache translated queries**: Translation can be expensive for large query sets

   ```python
   from functools import lru_cache

   class CachedTranslator:
       @lru_cache(maxsize=1000)
       def translate_cached(self, benchmark_name, query_id, dialect):
           benchmark = self.get_benchmark(benchmark_name)
           return benchmark.translate_query(query_id, dialect)
   ```

5. **Document dialect limitations**: Track which features don't translate well

   ```python
   DIALECT_LIMITATIONS = {
       "mysql": [
           "No support for FULL OUTER JOIN",
           "Limited window function support in older versions"
       ],
       "sqlite": [
           "No RIGHT JOIN or FULL OUTER JOIN",
           "Limited date/time function support"
       ]
   }
   ```

## Common Issues

### Unsupported Dialect

**Problem**: `ValueError: Error translating to dialect 'xyz': Unknown dialect 'xyz'.`

**Solutions**: (1) check whether the dialect needs normalization (`normalize_dialect_for_sqlglot("netezza")` returns `"postgres"`); (2) fall back to PostgreSQL, the most compatible dialect; (3) check the SQLGlot documentation for supported dialects at [sqlglot.com/sqlglot/dialects.html](https://sqlglot.com/sqlglot/dialects.html).

```python
from benchbox.utils.dialect_utils import normalize_dialect_for_sqlglot

normalized = normalize_dialect_for_sqlglot("netezza")

try:
    query = benchmark.translate_query(1, "custom_db")
except ValueError:
    query = benchmark.translate_query(1, "postgres")
```

### Translation Quality Issues

**Problem**: Translated query produces incorrect results or fails to execute

**Solutions**: (1) compare the original and translated queries; (2) test with a smaller dataset first; (3) make manual adjustments for platform-specific features, such as BigQuery-specific adjustments.

```python
original = benchmark.get_query(1)
translated = benchmark.translate_query(1, "bigquery")

print("Original:")
print(original)
print("\nTranslated:")
print(translated)

small_benchmark = TPCH(scale_factor=0.01)
translated = small_benchmark.translate_query(1, "bigquery")

if "bigquery" in target_dialect:
    translated = translated.replace("::DATE", "")
```

## Timing and UTC Boundaries

### `benchbox.utils.clock.mono_time`

<span id="benchbox.utils.clock.mono_time"></span>

Returns a `perf_counter` timestamp in seconds, for elapsed-time calculations.

**Import:** `from benchbox.utils.clock import mono_time` · **Extras:** none

```python
def mono_time() -> float: ...
```

#### Parameters

None.

#### Returns

`float`: the timestamp. Its origin is unspecified, so it is not a UTC timestamp.

#### Raises

Nothing it raises itself.

### `benchbox.utils.clock.elapsed_seconds`

<span id="benchbox.utils.clock.elapsed_seconds"></span>

Returns the seconds between two monotonic timestamps.

**Import:** `from benchbox.utils.clock import elapsed_seconds` · **Extras:** none

```python
def elapsed_seconds(start: float, end: float | None = None) -> float: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `start` | `float` | required | The start timestamp from `mono_time()`. |
| `end` | `float \| None` | `None` | The end timestamp. `None` samples `mono_time()` at the time of the call. |

#### Returns

`float`: `end - start`. Both supplied timestamps must come from the same monotonic clock. A negative difference is returned unchanged.

#### Raises

Nothing it raises itself.

### `benchbox.utils.clock.utc_now`

<span id="benchbox.utils.clock.utc_now"></span>

Returns the current time as a timezone-aware UTC `datetime`, for event metadata.

**Import:** `from benchbox.utils.clock import utc_now` · **Extras:** none

```python
def utc_now() -> datetime: ...
```

#### Parameters

None.

#### Returns

`datetime`: the current UTC time.

#### Raises

Nothing it raises itself.

### `benchbox.utils.clock.Stopwatch`

<span id="benchbox.utils.clock.Stopwatch"></span>
<span id="benchbox.utils.clock.Stopwatch.__init__"></span>

Stores a monotonic start timestamp and a UTC start boundary.

**Import:** `from benchbox.utils.clock import Stopwatch` · **Extras:** none

```python
class Stopwatch:
    def __init__(self, start_mono: float, start_utc: datetime) -> None: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `start_mono` | `float` | required | The monotonic start timestamp. |
| `start_utc` | `datetime` | required | The UTC start boundary. |

#### Returns

A `Stopwatch`. Both constructor fields are required, and direct construction does not sample either clock. Use `Stopwatch.start()` to sample both.

**Methods:**

<span id="benchbox.utils.clock.Stopwatch.start"></span>
`Stopwatch.start() -> Stopwatch` (class method) constructs a new stopwatch by sampling the monotonic and UTC clocks.

<span id="benchbox.utils.clock.Stopwatch.elapsed_seconds"></span>
`elapsed_seconds() -> float` returns the seconds since `start_mono`, measured with the monotonic clock.

<span id="benchbox.utils.clock.Stopwatch.elapsed_ms"></span>
`elapsed_ms() -> float` returns the elapsed seconds multiplied by 1,000.

<span id="benchbox.utils.clock.Stopwatch.finish"></span>
`finish() -> tuple[datetime, float]` returns the current UTC boundary and the elapsed seconds. It does not stop or freeze the stopwatch, so each later call samples again.

#### Raises

Nothing it raises itself.

### `benchbox.utils.clock.measure_elapsed`

<span id="benchbox.utils.clock.measure_elapsed"></span>

A context manager that yields a newly started `Stopwatch`.

**Import:** `from benchbox.utils.clock import measure_elapsed` · **Extras:** none

```python
def measure_elapsed() -> Iterator[Stopwatch]: ...
```

#### Parameters

None.

#### Returns

A `Stopwatch` for the body of the `with` block. Leaving the block does not stop the stopwatch and does not store a finish value. Call its elapsed methods or `finish()` when you reach the boundary you want to measure.

#### Raises

Nothing it raises itself. Exceptions raised in the body of the `with` block propagate.

## Cloud URL Components

### `benchbox.utils.cloud_urls.parse_cloud_url`

<span id="benchbox.utils.cloud_urls.parse_cloud_url"></span>

Splits a cloud URL into a bucket and a key prefix at the first slash after the scheme.

**Import:** `from benchbox.utils.cloud_urls import parse_cloud_url` · **Extras:** none

```python
def parse_cloud_url(url: str) -> tuple[str, str]: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `url` | `str` | required | The cloud URL, such as `s3://bucket/path/`. |

#### Returns

`tuple[str, str]`: the bucket and the key prefix. For `s3://bucket/path/` the result is `("bucket", "path/")`, and for `s3://bucket/` it is `("bucket", "")`. Remaining slashes, query-looking suffixes and percent escapes are kept as they are.

The function only splits the string. It does not validate the scheme, the bucket or credentials. The named schemes are `s3`, `gs` and `az`, and other schemes are split with the same prefix-length rule. Pass a correctly formed URL.

#### Raises

Nothing it raises itself.

### `benchbox.utils.cloud_urls.parse_s3_url`

<span id="benchbox.utils.cloud_urls.parse_s3_url"></span>

Splits an S3 URL by calling `parse_cloud_url`.

**Import:** `from benchbox.utils.cloud_urls import parse_s3_url` · **Extras:** none

```python
def parse_s3_url(s3_url: str) -> tuple[str, str]: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `s3_url` | `str` | required | The S3 URL. |

#### Returns

`tuple[str, str]`: the same result as `parse_cloud_url`. The function does not enforce the S3 scheme.

#### Raises

Nothing it raises itself.

### `benchbox.utils.cloud_urls.parse_gcs_url`

<span id="benchbox.utils.cloud_urls.parse_gcs_url"></span>

Splits a Google Cloud Storage URL by calling `parse_cloud_url`.

**Import:** `from benchbox.utils.cloud_urls import parse_gcs_url` · **Extras:** none

```python
def parse_gcs_url(gcs_url: str) -> tuple[str, str]: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `gcs_url` | `str` | required | The GCS URL. |

#### Returns

`tuple[str, str]`: the same result as `parse_cloud_url`. The function does not enforce the GCS scheme.

#### Raises

Nothing it raises itself.

## Display Formatting

### `benchbox.utils.formatting.format_duration`

<span id="benchbox.utils.formatting.format_duration"></span>

Formats a duration for display.

**Import:** `from benchbox.utils.formatting import format_duration` · **Extras:** none

```python
def format_duration(seconds: float) -> str: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `seconds` | `float` | required | The duration in seconds. |

#### Returns

`str`: values below one second are shown as milliseconds with one decimal, values from one second to below sixty seconds as seconds with three decimals, and values of sixty seconds or more as minutes with one decimal. The function does not check the range. For example, `0.123` produces `"123.0ms"` and `1.5` produces `"1.500s"`.

#### Raises

Nothing it raises itself.

### `benchbox.utils.formatting.format_bytes`

<span id="benchbox.utils.formatting.format_bytes"></span>

Formats a byte count for display.

**Import:** `from benchbox.utils.formatting import format_bytes` · **Extras:** none

```python
def format_bytes(bytes_val: int | float) -> str: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `bytes_val` | `int` or `float` | required | The number of bytes. It is converted to `float`. |

#### Returns

`str`: the value divided repeatedly by 1,024, with the unit `B`, `KB`, `MB`, `GB` or `TB` chosen while the value is below the next boundary, and `PB` for larger values. The result always has two decimal places and a space before the unit. Despite the unit labels, the conversion uses binary factors.

#### Raises

Nothing it raises itself.

### `benchbox.utils.formatting.format_memory_usage`

<span id="benchbox.utils.formatting.format_memory_usage"></span>

Formats a memory amount given in megabytes.

**Import:** `from benchbox.utils.formatting import format_memory_usage` · **Extras:** none

```python
def format_memory_usage(memory_mb: float) -> str: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `memory_mb` | `float` | required | The memory amount in binary megabytes. |

#### Returns

`str`: the input multiplied by `1024 * 1024` and passed to `format_bytes`. For example, `512.5` produces `"512.50 MB"`. The input unit is binary megabytes, and the output has two decimals.

#### Raises

Nothing it raises itself.

### `benchbox.utils.formatting.format_number`

<span id="benchbox.utils.formatting.format_number"></span>

Formats a number with comma thousands separators.

**Import:** `from benchbox.utils.formatting import format_number` · **Extras:** none

```python
def format_number(value: int | float, precision: int = 2) -> str: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `value` | `int` or `float` | required | The number to format. |
| `precision` | `int` | `2` | The number of decimal places for values that are not integers. |

#### Returns

`str`: the number with comma thousands separators. Integer inputs keep integer formatting whatever `precision` is. Other numeric inputs use `precision` decimal places.

#### Raises

Nothing it raises itself. Python formatting errors propagate.

## SQL Identifier and Parenthesis Checks

### `benchbox.utils.sql_identifier.is_valid_sql_identifier`

<span id="benchbox.utils.sql_identifier.is_valid_sql_identifier"></span>

Checks that a name looks like a plain ASCII SQL identifier within a length cap.

**Import:** `from benchbox.utils.sql_identifier import is_valid_sql_identifier` · **Extras:** none

```python
def is_valid_sql_identifier(identifier: str, *, max_length: int) -> bool: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `identifier` | `str` | required | The name to check. |
| `max_length` | `int` | required | The longest name the caller accepts. It is keyword-only and has no default. |

#### Returns

`bool`: `False` for an empty or non-string input and for a name longer than `max_length`. Otherwise the result is whether the name matches the ASCII pattern `^[a-zA-Z_][a-zA-Z0-9_]*$` under Python's regular-expression matcher.

The function does not reject reserved words, does not quote a name and does not infer an engine-specific length cap. Because `$` also matches the position before a final newline, do not treat the pattern as a full-string validation guarantee.

BenchBox callers currently use caps of 63 for PostgreSQL, TimescaleDB, pg_duckdb, pg_mooncake and CedarDB; 127 for QuestDB; and 128 for Spark, LakeSail, Velox, the Hive metastore and the MySQL-wire family (Doris and SingleStore). These are project caller settings, not a universal statement about those engines.

#### Raises

Nothing it raises itself.

### `benchbox.utils.sql_parsing.find_matching_parenthesis`

<span id="benchbox.utils.sql_parsing.find_matching_parenthesis"></span>

Finds the closing parenthesis that matches an opening parenthesis.

**Import:** `from benchbox.utils.sql_parsing import find_matching_parenthesis` · **Extras:** none

```python
def find_matching_parenthesis(text: str, open_index: int) -> int: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `text` | `str` | required | The text to scan. |
| `open_index` | `int` | required | The index of the opening parenthesis. A valid index is a precondition the caller must meet. |

#### Returns

`int`: the index of the matching closing parenthesis. The scan tracks nested parentheses. Parentheses inside single-quoted text are ignored, and doubled single quotes are recognized. Backslash escapes, comments and double-quoted identifiers are not parsed.

#### Raises

`ValueError`: the scan reaches the end of the text without closing the group.

## Data-Generation Provenance

### `benchbox.utils.datagen_version.DATA_GENERATION_VERSION`

<span id="benchbox.utils.datagen_version.DATA_GENERATION_VERSION"></span>

The generation-logic version that is stored in manifest stamps. Maintainers must raise it when a change to generator code invalidates previously generated data. Benchmarks that are backed by a specification must also register the files that influence them in the benchmark specification mapping. Otherwise a specification edit cannot invalidate that benchmark's cached data.

**Import:** `from benchbox.utils.datagen_version import DATA_GENERATION_VERSION` · **Extras:** none

#### Returns

An `int` constant. The current value is `1`.

### `benchbox.utils.datagen_version.compute_base_constants_hash`

<span id="benchbox.utils.datagen_version.compute_base_constants_hash"></span>

Returns a digest of the generation version and the registered specification files of a benchmark.

**Import:** `from benchbox.utils.datagen_version import compute_base_constants_hash` · **Extras:** none

```python
def compute_base_constants_hash(benchmark: str | None = None) -> str: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `benchmark` | `str \| None` | `None` | The benchmark name. It is lowercased before lookup. |

#### Returns

`str`: a SHA-256 hex digest seeded by `DATA_GENERATION_VERSION` and extended with the raw bytes of the registered specification files. The registered names are `tpch`, `tpch_skew` and `tsbs_devops` (also `tsbs`). Skewed TPC-H includes both the base TPC-H and the skew specifications. An unknown name uses only the version marker.

Unreadable specification files are skipped. The digest therefore cannot certify that those files are present, and it cannot detect edits to specification files that are not registered.

#### Raises

Nothing it raises itself.

### `benchbox.utils.datagen_version.current_datagen_stamp`

<span id="benchbox.utils.datagen_version.current_datagen_stamp"></span>

Returns the stamp to store in newly generated manifests.

**Import:** `from benchbox.utils.datagen_version import current_datagen_stamp` · **Extras:** none

```python
def current_datagen_stamp(benchmark: str | None = None) -> dict[str, Any]: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `benchmark` | `str \| None` | `None` | The benchmark name. |

#### Returns

`dict[str, Any]` with `data_generation_version` and `base_constants_hash`. The current generation version is `1`.

#### Raises

Nothing it raises itself.

### `benchbox.utils.datagen_version.compute_datagen_identity_hash`

<span id="benchbox.utils.datagen_version.compute_datagen_identity_hash"></span>

Returns a hash that identifies a generation setup.

**Import:** `from benchbox.utils.datagen_version import compute_datagen_identity_hash` · **Extras:** none

```python
def compute_datagen_identity_hash(benchmark: str | None, configuration: Mapping[str, Any] | None = None) -> str: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `benchmark` | `str \| None` | required | The benchmark name. |
| `configuration` | `Mapping[str, Any] \| None` | `None` | The effective configuration. A shallow dictionary copy is hashed. |

#### Returns

`str`: the hash of a compact, key-sorted JSON object that holds the generation version, the base-constants hash and the optional configuration. `None` and an empty configuration produce different identities.

#### Raises

`TypeError`: the configuration values cannot be encoded as JSON.

### `benchbox.utils.datagen_version.manifest_datagen_is_current`

<span id="benchbox.utils.datagen_version.manifest_datagen_is_current"></span>

Tells whether a manifest carries the current generation stamp.

**Import:** `from benchbox.utils.datagen_version import manifest_datagen_is_current` · **Extras:** none

```python
def manifest_datagen_is_current(manifest: Mapping[str, Any] | None, benchmark: str | None = None) -> bool: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `manifest` | `Mapping[str, Any] \| None` | required | The manifest to check. |
| `benchmark` | `str \| None` | `None` | The benchmark name. An explicit value takes precedence over `manifest["benchmark"]`. |

#### Returns

`bool`: `True` when the manifest is a mapping with the current generation version and the expected base-constants hash. The function checks neither the generated data files nor the identity of the effective configuration.

#### Raises

Nothing it raises itself.

### `benchbox.utils.datagen_version.describe_datagen_staleness`

<span id="benchbox.utils.datagen_version.describe_datagen_staleness"></span>

Explains why a manifest is not current.

**Import:** `from benchbox.utils.datagen_version import describe_datagen_staleness` · **Extras:** none

```python
def describe_datagen_staleness(manifest: Mapping[str, Any] | None, benchmark: str | None = None) -> str | None: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `manifest` | `Mapping[str, Any] \| None` | required | The manifest to check. |
| `benchmark` | `str \| None` | `None` | The benchmark name. |

#### Returns

`str | None`: `None` when the stamp is current. Otherwise a reason: the manifest is missing or unreadable, it has no version stamp, the generation version differs, or the base constants changed. The function does not regenerate data.

#### Raises

Nothing it raises itself.

## Data Format Selection

### `benchbox.utils.format_selection.FormatSelector`

<span id="benchbox.utils.format_selection.FormatSelector"></span>

Selects a data format and inspects which formats are available locally. The class holds no state, and its methods are static methods.

**Import:** `from benchbox.utils.format_selection import FormatSelector` · **Extras:** none

#### Parameters

None. Call the static methods on the class.

#### Returns

The methods below return these values.

<span id="benchbox.utils.format_selection.FormatSelector.select_format"></span>
`FormatSelector.select_format(platform_name: str, available_formats: list[str], user_preference: str | None = None) -> str` returns the format to use. It returns `"tbl"` immediately when no formats are available, even when a user preference was supplied. Otherwise a nonempty preference must be both available and supported, or it raises `ValueError`. Without a preference, it uses the platform's preferred-format resolver.

<span id="benchbox.utils.format_selection.FormatSelector.get_fallback_chain"></span>
`FormatSelector.get_fallback_chain(platform_name: str, available_formats: list[str]) -> list[str]` returns the formats to try in order. The list starts with the available, supported formats in the platform's preference order. It then appends every remaining available format once, in input order. The appended formats are not filtered for platform support, so the list does not certify that every attempt is supported.

<span id="benchbox.utils.format_selection.FormatSelector.detect_available_formats"></span>
`FormatSelector.detect_available_formats(data_dir: Path, table_name: str, manifest_data: dict[str, Any] | None = None) -> list[str]` returns the formats found for a table. It prefers nonempty manifest format metadata: the top-level `formats`, followed by the keys of `tables[table_name]["formats"]`, without duplicates and in the order found. That path does not check that files exist. Otherwise it inspects local table-name patterns for `tbl` and `dat`, CSV and Parquet files, then the `_delta_log` directory for Delta and the Iceberg layout. It returns `["tbl"]` when nothing is detected. It does no cloud storage discovery.

#### Raises

`ValueError`, from `select_format`, when a nonempty preference is not both available and supported.

## Dataset Names and Output Paths

These helpers construct names and paths. Except for `ensure_directory`, they do not create directories and do not validate remote storage access. Names are compatibility labels, not unique dataset identities.

Scale-label formatting and its precision and identity limits are described in {doc}`additional-utilities`.

### `benchbox.utils.output_path.normalize_output_root`

<span id="benchbox.utils.output_path.normalize_output_root"></span>

Adds a benchmark and scale suffix to an output root unless the root already ends with it.

**Import:** `from benchbox.utils.output_path import normalize_output_root` · **Extras:** none

```python
def normalize_output_root(output_root: str | None, benchmark: str, scale: float) -> str | None: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `output_root` | `str \| None` | required | The output root, a local path or a remote URI. |
| `benchmark` | `str` | required | The benchmark name. It is stripped and lowercased. An empty name leaves only the scale suffix. |
| `scale` | `float` | required | The scale factor used to build the scale suffix. |

#### Returns

`str | None`: a falsy root is returned unchanged. Otherwise trailing slashes are stripped from the root, and the suffix is appended only when the last slash-separated component does not already match it, ignoring letter case. An existing component keeps its case. Local paths and remote URIs get the same string operation, and no scheme or file system validation happens.

#### Raises

Nothing it raises itself.

### `benchbox.utils.path_utils.get_default_data_directory`

<span id="benchbox.utils.path_utils.get_default_data_directory"></span>

Returns the default data directory.

**Import:** `from benchbox.utils.path_utils import get_default_data_directory` · **Extras:** none

```python
def get_default_data_directory() -> Path: ...
```

#### Parameters

None.

#### Returns

`Path`: a nonempty `BENCHBOX_DATA_DIR` used as given, otherwise `Path.cwd() / "data"`. The function does not strip whitespace and does not expand `~`.

#### Raises

Nothing it raises itself.

### `benchbox.utils.path_utils.find_work_tree_root`

<span id="benchbox.utils.path_utils.find_work_tree_root"></span>

Finds the root of the enclosing Git work tree.

**Import:** `from benchbox.utils.path_utils import find_work_tree_root` · **Extras:** none

```python
def find_work_tree_root(start: Path | None = None) -> Path | None: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `start` | `Path \| None` | `None` | The directory to start from. An explicit value is resolved first. `None` starts at `Path.cwd()`. |

#### Returns

`Path | None`: the first directory, starting from `start` and moving up through its parents, that holds an existing `.git` entry. Both clone directories and linked-work-tree files count. It returns `None` when no entry exists. The search does not launch Git, so it also works without a Git executable while a benchmark is being constructed.

#### Raises

Nothing it raises itself.

### `benchbox.utils.path_utils.default_benchmark_runs_root`

<span id="benchbox.utils.path_utils.default_benchmark_runs_root"></span>

Returns the default `benchmark_runs` root.

**Import:** `from benchbox.utils.path_utils import default_benchmark_runs_root` · **Extras:** none

```python
def default_benchmark_runs_root(start: Path | None = None) -> Path: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `start` | `Path \| None` | `None` | The directory to start from. |

#### Returns

`Path`: the result of the shared runtime path resolution. It is `benchmark_runs` beside the enclosing Git work tree, or beneath the starting directory outside Git. A work tree is identified by an existing `.git` entry, not by its directory type.

#### Raises

Nothing it raises itself.

### `benchbox.utils.path_utils.resolve_benchmark_runs_dir`

<span id="benchbox.utils.path_utils.resolve_benchmark_runs_dir"></span>

Returns the directory that holds benchmark run output.

**Import:** `from benchbox.utils.path_utils import resolve_benchmark_runs_dir` · **Extras:** none

```python
def resolve_benchmark_runs_dir() -> Path: ...
```

#### Parameters

None.

#### Returns

`Path`: a nonblank `BENCHBOX_OUTPUT_DIR`, trimmed and with `~` expanded. Otherwise the shared work-tree-sibling rule of `default_benchmark_runs_root` applies. This is separate from the `BENCHBOX_DATA_DIR` rule used by `get_default_data_directory`.

#### Raises

Nothing it raises itself.

### `benchbox.utils.path_utils.get_benchmark_runs_datagen_path`

<span id="benchbox.utils.path_utils.get_benchmark_runs_datagen_path"></span>

Returns the generated-data directory for a benchmark and scale factor.

**Import:** `from benchbox.utils.path_utils import get_benchmark_runs_datagen_path` · **Extras:** none

```python
def get_benchmark_runs_datagen_path(benchmark_name: str, scale_factor: float, base_dir: str | Path | None = None) -> Path: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `benchmark_name` | `str` | required | The benchmark name. It is not sanitized. |
| `scale_factor` | `float` | required | The scale factor, formatted into the directory name. |
| `base_dir` | `str`, `Path` or `None` | `None` | An explicit datagen root. `None` uses `resolve_benchmark_runs_dir() / "datagen"`. |

#### Returns

`Path`: `<benchmark>_<formatted scale>` appended to the datagen root. An explicit root does not get another `datagen` component, and it is not expanded or resolved.

#### Raises

Nothing it raises itself.

### `benchbox.utils.path_utils.get_benchmark_runs_databases_path`

<span id="benchbox.utils.path_utils.get_benchmark_runs_databases_path"></span>

Returns the database directory for a benchmark and scale factor.

**Import:** `from benchbox.utils.path_utils import get_benchmark_runs_databases_path` · **Extras:** none

```python
def get_benchmark_runs_databases_path(benchmark_name: str, scale_factor: float, base_dir: str | Path | None = None) -> Path: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `benchmark_name` | `str` | required | The benchmark name. It is not sanitized. |
| `scale_factor` | `float` | required | The scale factor, formatted into the directory name. |
| `base_dir` | `str`, `Path` or `None` | `None` | An explicit databases root. `None` uses `resolve_benchmark_runs_dir() / "databases"`. |

#### Returns

`Path`: the same rule as `get_benchmark_runs_datagen_path`, with `databases` as the default subdirectory.

#### Raises

Nothing it raises itself.

### `benchbox.utils.path_utils.get_benchmark_runs_dataframe_path`

<span id="benchbox.utils.path_utils.get_benchmark_runs_dataframe_path"></span>

Returns the directory for DataFrame data.

**Import:** `from benchbox.utils.path_utils import get_benchmark_runs_dataframe_path` · **Extras:** none

```python
def get_benchmark_runs_dataframe_path(base_dir: str | Path | None = None) -> Path: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `base_dir` | `str`, `Path` or `None` | `None` | An explicit root. |

#### Returns

`Path`: an explicit root as a `Path`, otherwise the shared `datagen` directory that SQL generation uses. No dataset suffix is appended.

#### Raises

Nothing it raises itself.

### `benchbox.utils.path_utils.get_results_path`

<span id="benchbox.utils.path_utils.get_results_path"></span>

Returns the results path for a benchmark run.

**Import:** `from benchbox.utils.path_utils import get_results_path` · **Extras:** none

```python
def get_results_path(benchmark_name: str, timestamp: str, base_dir: str | Path | None = None) -> Path: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `benchmark_name` | `str` | required | The benchmark name. It is not sanitized. |
| `timestamp` | `str` | required | The run timestamp. It is not sanitized. |
| `base_dir` | `str`, `Path` or `None` | `None` | An explicit root. `None` uses the default data directory. |

#### Returns

`Path`: `results/<benchmark>_<timestamp>` appended to the root. The default root comes from `BENCHBOX_DATA_DIR`, not from `BENCHBOX_OUTPUT_DIR`.

#### Raises

Nothing it raises itself.

### `benchbox.utils.path_utils.ensure_directory`

<span id="benchbox.utils.path_utils.ensure_directory"></span>

Creates a directory and any missing parents.

**Import:** `from benchbox.utils.path_utils import ensure_directory` · **Extras:** none

```python
def ensure_directory(path: str | Path) -> Path: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `path` | `str` or `Path` | required | The directory to create. |

#### Returns

`Path`: the directory as a `Path`, not resolved. The directory is created with `exist_ok=True`.

#### Raises

Nothing it raises itself. File system errors propagate.

## Runtime Toggles and Output

### `benchbox.utils.toggles.is_probe_requested`

<span id="benchbox.utils.toggles.is_probe_requested"></span>

Normalizes an opt-out toggle into a boolean. It decides intent only and runs no probe.

**Import:** `from benchbox.utils.toggles import is_probe_requested` · **Extras:** none

```python
def is_probe_requested(value: Any) -> bool: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `value` | `Any` | required | The toggle value. |

#### Returns

`bool`:

- `None` means requested (`True`).
- Booleans pass through, and numbers use their truth value.
- Strings are stripped and lowercased. `0`, `false`, `no` and `off` mean `False`. `1`, `true`, `yes` and `on` mean `True`.
- Any other string, including an empty one, logs a warning and returns `False`. An ambiguous input never enables a probe that could be billable.
- Other objects use `bool(value)`.

#### Raises

Nothing it raises itself.

Quiet-aware output helpers provide a shared runtime channel. Use `emit` for text or Rich renderables. When you need Rich keyword arguments, use `quiet_console.print` or inject `quiet_console` into display classes.

### `benchbox.utils.printing.set_quiet`

<span id="benchbox.utils.printing.set_quiet"></span>

Sets the process-wide quiet state.

**Import:** `from benchbox.utils.printing import set_quiet` · **Extras:** none

```python
def set_quiet(enabled: bool) -> None: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `enabled` | `bool` | required | Converted with `bool()`. The initial state is `False`. |

#### Returns

`None`. The new state affects later helper calls and later attribute lookups on `quiet_console`.

#### Raises

Nothing it raises itself.

### `benchbox.utils.printing.is_quiet`

<span id="benchbox.utils.printing.is_quiet"></span>

Returns the process-wide quiet state.

**Import:** `from benchbox.utils.printing import is_quiet` · **Extras:** none

```python
def is_quiet() -> bool: ...
```

#### Parameters

None.

#### Returns

`bool`: the current global quiet state.

#### Raises

Nothing it raises itself.

### `benchbox.utils.printing.get_console`

<span id="benchbox.utils.printing.get_console"></span>

Returns a Rich console for the requested channel and quiet setting.

**Import:** `from benchbox.utils.printing import get_console` · **Extras:** none

```python
def get_console(quiet: bool | None = None, *, stderr: bool = False) -> Console: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `quiet` | `bool \| None` | `None` | `None` uses the global state. An explicit boolean overrides it, so `False` can force visible output while global quiet is on. |
| `stderr` | `bool` | `False` | Use the standard error channel instead of standard output. It is keyword-only. |

#### Returns

`Console`: the stdout and stderr consoles are created lazily and cached separately. Quiet calls share one sink console that writes to an in-memory stream, whatever channel was requested.

#### Raises

Nothing it raises itself.

### `benchbox.utils.printing.get_quiet_console`

<span id="benchbox.utils.printing.get_quiet_console"></span>

Returns the quiet sink console.

**Import:** `from benchbox.utils.printing import get_quiet_console` · **Extras:** none

```python
def get_quiet_console() -> Console: ...
```

#### Parameters

None.

#### Returns

`Console`: the sink console, whatever the global quiet state is. The call does not change that state.

#### Raises

Nothing it raises itself.

### `benchbox.utils.printing.emit`

<span id="benchbox.utils.printing.emit"></span>

Prints a message or Rich renderable unless output is quiet.

**Import:** `from benchbox.utils.printing import emit` · **Extras:** none

```python
def emit(msg: Any = "", *, quiet: bool | None = None, stderr: bool = False) -> None: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `msg` | `Any` | `""` | The text or Rich renderable. An omitted message prints a blank line. |
| `quiet` | `bool \| None` | `None` | Same precedence as `get_console`: `None` uses the global state and an explicit boolean overrides it. It is keyword-only. |
| `stderr` | `bool` | `False` | Print to standard error. It is keyword-only. |

#### Returns

`None`. A quiet call returns without printing. A visible call passes the single message or renderable to Rich's `Console.print` on the requested channel. The function does not accept Rich styling keyword arguments.

#### Raises

Nothing it raises itself.

### `benchbox.utils.printing.info`

<span id="benchbox.utils.printing.info"></span>

Prints an informational message.

**Import:** `from benchbox.utils.printing import info` · **Extras:** none

```python
def info(msg: str) -> None: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `msg` | `str` | required | The message. |

#### Returns

`None`. The call goes to `emit` on standard output under the global quiet state. These helpers do not apply logging levels and do not use a separate verbosity gate.

#### Raises

Nothing it raises itself.

### `benchbox.utils.printing.warn`

<span id="benchbox.utils.printing.warn"></span>

Prints a warning message.

**Import:** `from benchbox.utils.printing import warn` · **Extras:** none

```python
def warn(msg: str) -> None: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `msg` | `str` | required | The message. |

#### Returns

`None`. The call goes to `emit` on standard output under the global quiet state. These helpers do not apply logging levels and do not use a separate verbosity gate.

#### Raises

Nothing it raises itself.

### `benchbox.utils.printing.debug`

<span id="benchbox.utils.printing.debug"></span>

Prints a debug message.

**Import:** `from benchbox.utils.printing import debug` · **Extras:** none

```python
def debug(msg: str) -> None: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `msg` | `str` | required | The message. |

#### Returns

`None`. The call goes to `emit` on standard output under the global quiet state. These helpers do not apply logging levels and do not use a separate verbosity gate.

#### Raises

Nothing it raises itself.

### `benchbox.utils.printing.error`

<span id="benchbox.utils.printing.error"></span>

Prints an error message.

**Import:** `from benchbox.utils.printing import error` · **Extras:** none

```python
def error(msg: str) -> None: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `msg` | `str` | required | The message. |

#### Returns

`None`. The call goes to `emit` on standard error under the global quiet state. It does not apply a logging level.

#### Raises

Nothing it raises itself.

### `benchbox.utils.printing.silence_output`

<span id="benchbox.utils.printing.silence_output"></span>

A context manager that temporarily replaces `sys.stdout` and `sys.stderr` with in-memory streams.

**Import:** `from benchbox.utils.printing import silence_output` · **Extras:** none

```python
def silence_output(enabled: bool = True) -> Iterator[None]: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `enabled` | `bool` | `True` | `False` makes the context manager do nothing. |

#### Returns

A context manager that yields `None`. Both streams are restored in a `finally` block. It changes the process-wide stream bindings, not file descriptors or output streams that other code holds independently. It is not task-local isolation, and it does not change the global quiet state.

#### Raises

Nothing it raises itself.

### `benchbox.utils.printing.QuietConsoleProxy`

<span id="benchbox.utils.printing.QuietConsoleProxy"></span>

Forwards attribute lookups and context-manager calls to the console that the quiet-aware helpers currently select.

**Import:** `from benchbox.utils.printing import QuietConsoleProxy` · **Extras:** none

```python
class QuietConsoleProxy: ...
```

#### Parameters

None.

#### Returns

A proxy object. `quiet_console` in the same module is the shared instance. Changing the quiet state affects later lookups on it. Its representation reports `quiet` or `verbose` according to the global state.

#### Raises

Nothing it raises itself.

## Verbosity Settings and Logging

### `benchbox.utils.verbosity.VerbositySettings`

<span id="benchbox.utils.verbosity.VerbositySettings"></span>
<span id="benchbox.utils.verbosity.VerbositySettings.__init__"></span>

Frozen settings that describe how verbose output is.

**Import:** `from benchbox.utils.verbosity import VerbositySettings` · **Extras:** none

```python
class VerbositySettings:
    def __init__(self, level: int = 0, verbose_enabled: bool = False, very_verbose: bool = False, quiet: bool = False) -> None: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `level` | `int` | `0` | The verbosity level. |
| `verbose_enabled` | `bool` | `False` | Whether verbose output is on. |
| `very_verbose` | `bool` | `False` | Whether very verbose output is on. |
| `quiet` | `bool` | `False` | Whether output is quiet. |

#### Returns

A frozen `VerbositySettings`. Direct construction does not normalize inconsistent fields.

<span id="benchbox.utils.verbosity.VerbositySettings.verbose"></span>
`verbose` (property) is `verbose_enabled and not quiet`.

<span id="benchbox.utils.verbosity.VerbositySettings.to_config"></span>
`to_config() -> dict[str, Any]` returns `verbose_level`, `verbose_enabled`, `very_verbose`, `quiet` and the computed `verbose`.

<span id="benchbox.utils.verbosity.VerbositySettings.from_flags"></span>
`VerbositySettings.from_flags(verbose: int | bool | None, quiet: bool | None) -> VerbositySettings` (class method) builds settings from command-line style flags. A boolean `True` means level 2, and `False` or an unset value means level 0. Other values use `int(verbose or 0)`. Negative levels are clamped to 0, and a truthy `quiet` forces level 0. Verbose is enabled at level 1 and very verbose at level 2, unless `quiet` is set. Conversion errors propagate.

<span id="benchbox.utils.verbosity.VerbositySettings.from_mapping"></span>
`VerbositySettings.from_mapping(data: Mapping[str, Any] | None) -> VerbositySettings` (class method) builds settings from a mapping. Empty or absent input returns the default settings. `quiet` is read by truth value, and `verbose_level` is read before the fallback `level`, then `int(value or 0)` is applied. Quiet forces level 0, and a negative level is otherwise kept. Explicit `verbose_enabled` and `very_verbose` values use their truth value and override the defaults derived from the level and quiet. These fields can remain true when quiet is set. The `verbose` property and the mixin logging methods still respect quiet. Conversion errors propagate.

<span id="benchbox.utils.verbosity.VerbositySettings.default"></span>
`VerbositySettings.default() -> VerbositySettings` (class method) returns the default settings shown in the signature.

#### Raises

Nothing it raises itself. The class methods let conversion errors propagate.

### `benchbox.utils.verbosity.compute_verbosity`

<span id="benchbox.utils.verbosity.compute_verbosity"></span>

Computes verbosity settings from flags. It calls `VerbositySettings.from_flags`.

**Import:** `from benchbox.utils.verbosity import compute_verbosity` · **Extras:** none

```python
def compute_verbosity(verbose: int | bool | None, quiet: bool | None) -> VerbositySettings: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `verbose` | `int`, `bool` or `None` | required | The verbose flag or level. |
| `quiet` | `bool \| None` | required | The quiet flag. |

#### Returns

`VerbositySettings`: the result of `VerbositySettings.from_flags`.

#### Raises

Nothing it raises itself.

### `benchbox.utils.verbosity.VerbosityMixin`

<span id="benchbox.utils.verbosity.VerbosityMixin"></span>

A mixin that gives a class verbosity-aware logging methods.

**Import:** `from benchbox.utils.verbosity import VerbosityMixin` · **Extras:** none

```python
class VerbosityMixin: ...
```

#### Parameters

None.

#### Returns

A mixin class. Consumers set `logger` to a `logging.Logger` before a logging method uses it. Reading an unset logger raises `AttributeError`, and assigning another type raises `TypeError`. The verbosity fields start at level 0 with false flags.

<span id="benchbox.utils.verbosity.VerbosityMixin.apply_verbosity"></span>
`apply_verbosity(settings)` copies the four settings fields and updates the legacy `verbose` attribute from the computed property.

<span id="benchbox.utils.verbosity.VerbosityMixin.verbosity_settings"></span>
`verbosity_settings` (property) returns a snapshot of the current four fields.

Logging methods return without logging when `quiet` is true. They log through Python logging, so logger levels and handlers also control visibility.

<span id="benchbox.utils.verbosity.VerbosityMixin.log_verbose"></span>
`log_verbose(message)` logs at INFO when `verbose_enabled` is true.

<span id="benchbox.utils.verbosity.VerbosityMixin.log_very_verbose"></span>
`log_very_verbose(message)` logs at DEBUG when `very_verbose` is true.

<span id="benchbox.utils.verbosity.VerbosityMixin.log_notice"></span>
`log_notice(message)` logs at INFO with no verbosity requirement.

<span id="benchbox.utils.verbosity.VerbosityMixin.log_operation_start"></span>
`log_operation_start(operation, details="")` logs at DEBUG with the details when very verbose. Otherwise it logs at INFO when verbose is enabled.

<span id="benchbox.utils.verbosity.VerbosityMixin.log_operation_complete"></span>
`log_operation_complete(operation, duration=None, details="")` logs at DEBUG when very verbose, including any details. Otherwise it logs at INFO when verbose is enabled and leaves out the details. A supplied duration appears in seconds with two decimal places.

<span id="benchbox.utils.verbosity.VerbosityMixin.log_debug_info"></span>
`log_debug_info(context="Debug")` requires very verbose. It logs version, Python and platform information at DEBUG. If the version report fails, it falls back to basic version information or an unavailable message.

<span id="benchbox.utils.verbosity.VerbosityMixin.log_error_with_debug_info"></span>
`log_error_with_debug_info(error, context="Error")` logs at ERROR. When very verbose, it adds debug context and the traceback of the current exception.

<span id="benchbox.utils.verbosity.VerbosityMixin.log_version_warning"></span>
`log_version_warning()` checks version consistency and logs any inconsistency at WARNING. When very verbose, it adds source details at DEBUG. Failures in the version check are suppressed.

#### Raises

`AttributeError` when `logger` is read before it is set, and `TypeError` when `logger` is assigned something other than a `logging.Logger`.

### `benchbox.utils.verbosity.create_debug_logger`

<span id="benchbox.utils.verbosity.create_debug_logger"></span>

Returns the named logger with its level set from the verbosity settings.

**Import:** `from benchbox.utils.verbosity import create_debug_logger` · **Extras:** none

```python
def create_debug_logger(name: str, verbose_level: int = 0, quiet: bool = False) -> logging.Logger: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | `str` | required | The logger name. |
| `verbose_level` | `int` | `0` | The verbosity level. |
| `quiet` | `bool` | `False` | Whether output is quiet. |

#### Returns

`logging.Logger`: the named Python logger. Its level is ERROR for quiet, DEBUG for level 2 or higher, INFO for level 1, and WARNING otherwise. The function changes the shared named logger and adds no handlers.

At level 2 or higher it also checks version consistency and logs a warning if it is inconsistent, even when quiet was requested. The logger's ERROR level normally filters that warning in quiet mode. Failures in the version check are suppressed.

#### Raises

Nothing it raises itself.

### `benchbox.utils.verbosity.log_debug_context`

<span id="benchbox.utils.verbosity.log_debug_context"></span>

Logs a title and each key and value of a context dictionary at DEBUG.

**Import:** `from benchbox.utils.verbosity import log_debug_context` · **Extras:** none

```python
def log_debug_context(logger: logging.Logger, context: dict[str, Any], title: str = "Debug Context") -> None: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `logger` | `logging.Logger` | required | The logger to write to. |
| `context` | `dict[str, Any]` | required | The values to log. |
| `title` | `str` | `"Debug Context"` | The title line. |

#### Returns

`None`. The function does not consult a separate verbosity or quiet state.

#### Raises

Nothing it raises itself.

### `benchbox.utils.verbosity.log_import_debug`

<span id="benchbox.utils.verbosity.log_import_debug"></span>

Logs the success or failure of an import at DEBUG.

**Import:** `from benchbox.utils.verbosity import log_import_debug` · **Extras:** none

```python
def log_import_debug(logger: logging.Logger, module_name: str, error: Exception | None = None) -> None: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `logger` | `logging.Logger` | required | The logger to write to. |
| `module_name` | `str` | required | The name of the imported module. |
| `error` | `Exception \| None` | `None` | A truthy error selects the failure output. |

#### Returns

`None`. A failure logs the available version and Python context. Failures of the version report are suppressed. The function does not consult a separate verbosity or quiet state.

#### Raises

Nothing it raises itself.

## Configuration Providers and Execution Settings

The [Execution Configuration](#execution-configuration) section above describes `ExecutionConfigHelper`. This section describes the providers and settings classes behind it.

### `benchbox.utils.config_interface.ConfigInterface`

<span id="benchbox.utils.config_interface.ConfigInterface"></span>

The abstract provider interface for configuration values. Providers may offer richer behavior than the built-in in-memory implementation.

**Import:** `from benchbox.utils.config_interface import ConfigInterface` · **Extras:** none

```python
class ConfigInterface: ...
```

#### Parameters

None. It is an abstract class.

#### Returns

An abstract base class with two methods that every provider must implement: `get(key, default=None)` and `set(key, value)`.

#### Raises

Nothing it raises itself.

### `benchbox.utils.config_interface.SimpleConfigProvider`

<span id="benchbox.utils.config_interface.SimpleConfigProvider"></span>
<span id="benchbox.utils.config_interface.SimpleConfigProvider.__init__"></span>

The built-in in-memory configuration provider.

**Import:** `from benchbox.utils.config_interface import SimpleConfigProvider` · **Extras:** none

```python
class SimpleConfigProvider:
    def __init__(self, defaults: dict | None = None) -> None: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `defaults` | `dict \| None` | `None` | Values to store. They are shallow-copied, and keys that are absent are filled with the built-in defaults. Supplied values, including `None`, take precedence. |

#### Returns

A `SimpleConfigProvider`. Keys are flat dotted strings, not nested-path lookups.

<span id="benchbox.utils.config_interface.SimpleConfigProvider.get"></span>
`get(key, default=None)` returns the stored value, or the caller's default for an absent key.

<span id="benchbox.utils.config_interface.SimpleConfigProvider.set"></span>
`set(key, value)` stores a value without validation or persistence.

<span id="benchbox.utils.config_interface.SimpleConfigProvider.update"></span>
`update(config_dict)` stores several values without validation or persistence.

The built-in `execution.power_run` defaults are `iterations=4`, `warm_up_iterations=0`, `timeout_per_iteration_minutes=60` and `concurrent_streams=1`. The `execution.throughput_test` defaults are `duration_minutes=60`, `concurrent_streams=4` and `warm_up_minutes=5`. The other execution defaults are `timeout_minutes=120`, `memory_limit_gb=8` and `enable_profiling=False`.

#### Raises

Nothing it raises itself.

### `benchbox.utils.config_interface.set_config_provider`

<span id="benchbox.utils.config_interface.set_config_provider"></span>

Registers a process-wide configuration provider.

**Import:** `from benchbox.utils.config_interface import set_config_provider` · **Extras:** none

```python
def set_config_provider(provider: ConfigInterface | None) -> None: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `provider` | `ConfigInterface \| None` | required | The provider to register. `None` clears the registration. |

#### Returns

`None`. Higher layers supply their user configuration through this interface, which keeps the utilities independent of CLI imports.

#### Raises

Nothing it raises itself.

### `benchbox.utils.config_interface.get_config_provider`

<span id="benchbox.utils.config_interface.get_config_provider"></span>

Returns the registered configuration provider, or a built-in provider when none is registered.

**Import:** `from benchbox.utils.config_interface import get_config_provider` · **Extras:** none

```python
def get_config_provider() -> ConfigInterface: ...
```

#### Parameters

None.

#### Returns

`ConfigInterface`: the registered object when there is one. Otherwise the result of `get_default_config_provider`. This fallback supports library and MCP consumers that run without CLI startup. Changes made to an unregistered fallback do not persist across later calls.

#### Raises

Nothing it raises itself.

### `benchbox.utils.config_interface.get_default_config_provider`

<span id="benchbox.utils.config_interface.get_default_config_provider"></span>

Creates a built-in configuration provider.

**Import:** `from benchbox.utils.config_interface import get_default_config_provider` · **Extras:** none

```python
def get_default_config_provider() -> ConfigInterface: ...
```

#### Parameters

None.

#### Returns

`ConfigInterface`: a new built-in provider on every call.

#### Raises

Nothing it raises itself.

### `benchbox.utils.config_helpers.PowerRunSettings`

<span id="benchbox.utils.config_helpers.PowerRunSettings"></span>

A mutable dataclass for the power-run execution settings.

**Import:** `from benchbox.utils.config_helpers import PowerRunSettings` · **Extras:** none

```python
class PowerRunSettings: ...
```

#### Parameters

Every field is required when the class is constructed directly. The defaults in the table are the fallback values that `from_config_manager` uses when the manager has no value.

| Name | Type | Fallback | Meaning |
| --- | --- | --- | --- |
| `iterations` | `int` | `4` | The number of measured iterations. |
| `warm_up_iterations` | `int` | `0` | The number of warm-up iterations. |
| `timeout_per_iteration_minutes` | `int` | `60` | The timeout for each iteration, in minutes. |
| `fail_fast` | `bool` | `False` | Whether to stop on the first failure. |
| `collect_metrics` | `bool` | `True` | Whether to collect metrics. |

#### Returns

A `PowerRunSettings`. It reads and writes its fields under the dotted prefix `execution.power_run`. The fallback values are `iterations=4`, `warm_up_iterations=0`, `timeout_per_iteration_minutes=60`, `fail_fast=False`, `collect_metrics=True`.

<span id="benchbox.utils.config_helpers.PowerRunSettings.from_config_manager"></span>
`from_config_manager(manager)` reads each field from its matching key under the prefix.

<span id="benchbox.utils.config_helpers.PowerRunSettings.apply_to_config_manager"></span>
`apply_to_config_manager(manager)` writes the fields through the manager's `set`.

<span id="benchbox.utils.config_helpers.PowerRunSettings.to_dict"></span>
`to_dict()` returns the field names and their current values.

None of these operations coerce or validate values.

#### Raises

Nothing it raises itself.

### `benchbox.utils.config_helpers.ConcurrentQueriesSettings`

<span id="benchbox.utils.config_helpers.ConcurrentQueriesSettings"></span>

A mutable dataclass for the concurrent-query execution settings.

**Import:** `from benchbox.utils.config_helpers import ConcurrentQueriesSettings` · **Extras:** none

```python
class ConcurrentQueriesSettings: ...
```

#### Parameters

Every field is required when the class is constructed directly. The defaults in the table are the fallback values that `from_config_manager` uses when the manager has no value.

| Name | Type | Fallback | Meaning |
| --- | --- | --- | --- |
| `enabled` | `bool` | `False` | Whether concurrent queries are on. |
| `max_concurrent` | `int` | `2` | The maximum number of concurrent queries. |
| `query_timeout_seconds` | `int` | `300` | The timeout for each query, in seconds. |
| `stream_timeout_seconds` | `int` | `3600` | The timeout for each stream, in seconds. |
| `retry_failed_queries` | `bool` | `True` | Whether failed queries are retried. |
| `max_retries` | `int` | `3` | The maximum number of retries. |

#### Returns

A `ConcurrentQueriesSettings`. It reads and writes its fields under the dotted prefix `execution.concurrent_queries`. The fallback values are `enabled=False`, `max_concurrent=2`, `query_timeout_seconds=300`, `stream_timeout_seconds=3600`, `retry_failed_queries=True`, `max_retries=3`.

<span id="benchbox.utils.config_helpers.ConcurrentQueriesSettings.from_config_manager"></span>
`from_config_manager(manager)` reads each field from its matching key under the prefix.

<span id="benchbox.utils.config_helpers.ConcurrentQueriesSettings.apply_to_config_manager"></span>
`apply_to_config_manager(manager)` writes the fields through the manager's `set`.

<span id="benchbox.utils.config_helpers.ConcurrentQueriesSettings.to_dict"></span>
`to_dict()` returns the field names and their current values.

None of these operations coerce or validate values.

#### Raises

Nothing it raises itself.

### `benchbox.utils.config_helpers.ExecutionConfigHelper`

<span id="benchbox.utils.config_helpers.ExecutionConfigHelper"></span>

Reads and changes execution settings through a configuration provider. The parameters, methods, profiles and examples are in the [Execution Configuration](#execution-configuration) section.

The helper keeps the manager it was given. Without one, it takes the registered or default provider at construction, and later registrations do not replace that captured object.

### `benchbox.utils.config_helpers.create_sample_execution_config`

<span id="benchbox.utils.config_helpers.create_sample_execution_config"></span>

Writes a sample YAML execution configuration file.

**Import:** `from benchbox.utils.config_helpers import create_sample_execution_config` · **Extras:** none

```python
def create_sample_execution_config(output_path: str | Path) -> None: ...
```

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `output_path` | `str` or `Path` | required | The file to write. |

#### Returns

`None`. The file is written in UTF-8 and replaces an existing file. Parent directories are not created. The sample holds execution settings and profile descriptions. It does not register a provider and does not apply settings.

#### Raises

Nothing it raises itself. YAML import, conversion and file system errors propagate.

## See Also

- {doc}`base` - Base benchmark interface
- {doc}`platforms` - Platform adapter documentation
- {doc}`benchmarks/index` - Benchmark API overview
- {doc}`/usage/configuration` - Configuration guide
- {doc}`/usage/troubleshooting` - Troubleshooting guide

### External Resources

- [SQLGlot Documentation](https://sqlglot.com/) - SQL dialect translation library
- [SQLGlot Dialects](https://sqlglot.com/sqlglot/dialects.html) - Supported SQL dialects
- [SQL Standards](https://www.iso.org/standard/63555.html) - ISO SQL standards
