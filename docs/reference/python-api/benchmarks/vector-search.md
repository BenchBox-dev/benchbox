# Vector Search API

```{tags} reference, python-api, custom-benchmark
```

<!-- markdownlint-disable MD024 -->

Python API reference for the Vector Search benchmark.

## Overview

Vector Search measures similarity-search queries over a table of synthetic embeddings. It generates two tables, `vectors` (the corpus) and `vector_queries` (100 query vectors), and serves six queries: exact kNN by cosine similarity and by L2 distance, filtered kNN, a large-k query for recall ground truth, an approximate-search query and a multi-category filtered query. The queries use each platform's own vector functions, so the benchmark gives a different SQL text per dialect.

| Query | What it returns |
| --- | --- |
| `Q1` | Top 10 by cosine similarity |
| `Q2` | Top 10 by L2 distance, ascending |
| `Q3` | Top 10 by cosine similarity within one category |
| `Q4` | Top 100 by cosine similarity |
| `Q5` | Top 10 by cosine similarity; the SQL is the same as `Q1`'s, and it is approximate only when the platform has a vector index |
| `Q6` | Top 20 by cosine similarity within three categories |

## `benchbox.VectorSearch`

<span id="benchbox.vector_search.VectorSearch"></span>

Creates a Vector Search benchmark that generates synthetic embedding data and serves six similarity-search queries.

**Import:** `from benchbox import VectorSearch` · **Extras:** none

### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `scale_factor` | `float` | `1.0` | Corpus size: `1_000_000 * scale_factor` vectors, so 0.01 gives 10,000 and 0.1 gives 100,000. Must be positive. Values of 1 or more must be whole numbers. |
| `output_dir` | `str`, `Path` or `None` | `None` | Directory for generated data files. When `None`, the directory is `benchmark_runs/datagen/vector_search_<sf token>` under the current directory (for example `vector_search_sf001` for 0.01), or under `$BENCHBOX_OUTPUT_DIR/datagen` when that variable is set. |
| `dimensions` | `int` | `128` | Length of each embedding. Keyword-only, at least 1. |
| `**kwargs` | keyword arguments | none | `verbose` (`bool` or `int`) and `quiet` (`bool`) set the log level. |

The constructor creates no files. Data is written by `generate_data()`.

### Returns

A `VectorSearch` instance. It subclasses `BaseBenchmark`; see {doc}`/reference/python-api/base` for the shared interface. The dimension count is used by the generator and by `get_create_tables_sql()`; the instance has no `dimensions` attribute.

### Raises

- `ValueError` when `scale_factor` is zero or negative (`Scale factor must be positive`), is 1 or more and not a whole number, or when `dimensions` is below 1 (`dimensions must be >= 1, got 0`).
- `TypeError` when `scale_factor` is not a number, or `dimensions` is passed by position.

### Example

```python
from benchbox import VectorSearch
from benchbox.platforms.duckdb import DuckDBAdapter

bench = VectorSearch(scale_factor=0.01, output_dir="vs_data", dimensions=8)
print(bench.generate_data())
adapter = DuckDBAdapter(database=":memory:")
conn = adapter.create_connection()
adapter.create_schema(bench, conn)
adapter.load_data(bench, conn, bench.output_dir)
rows = conn.execute(bench.get_query("Q1")).fetchall()
bench.validate_query_result("Q1", rows)
print(len(rows), sorted(bench.get_queries()))
```

Output on 0.4.1, after the adapter's progress lines:

```text
{'vectors': 'vs_data/vectors.csv', 'vector_queries': 'vs_data/vector_queries.csv'}
10 ['Q1', 'Q2', 'Q3', 'Q4', 'Q5', 'Q6']
```

The DuckDB adapter needs the `duckdb` package.

### Compatibility

`benchbox.vector_search.VectorSearch` is the same class. `get_data_source_benchmark()` returns `None`: the benchmark generates its own data.

## Constructor

<span id="benchbox.vector_search.VectorSearch.__init__"></span>

`VectorSearch(scale_factor=1.0, output_dir=None, *, dimensions=128, **kwargs)`. The arguments are in the Parameters table above.

## Data methods

### generate_data(tables=None, output_format="memory")

<span id="benchbox.vector_search.VectorSearch.generate_data"></span>

`generate_data(tables=None, output_format="memory") -> dict` writes the data files to `output_dir` and returns a `dict` that maps table name to file path (a `str`), plus `_datagen_manifest.json`.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `tables` | `list[str]` or `None` | `None` | Subset of `"vectors"` and `"vector_queries"`. `None` generates both. An unknown name generates nothing and returns `{}`. |
| `output_format` | `str` | `"memory"` | Ignored: files are always written. |

Both files are pipe-delimited with a header row. `vectors` has the columns `id`, `embedding`, `category` and `doc_id`; `category` is one of `category_01` to `category_10`. `vector_queries` has `query_id` and `query_vector`, with 100 rows. An embedding is written as a bracketed list such as `[0.106148,-0.362278,...]`. At scale factor 0.01 with 8 dimensions, `vectors.csv` has 10,000 rows and is about 1 MB.

The returned dict is not stored: `benchmark.tables` stays empty.

### get_schema(dialect="duckdb")

<span id="benchbox.vector_search.VectorSearch.get_schema"></span>

`get_schema(dialect="duckdb") -> dict` returns a mapping from table name to a definition with `name` and `columns`. The keys are `vectors` and `vector_queries`. `dialect` has no effect, and the embedding column has the type `FLOAT_ARRAY` whatever the dimensions.

### get_create_tables_sql(dialect="duckdb", tuning_config=None)

<span id="benchbox.vector_search.VectorSearch.get_create_tables_sql"></span>

`get_create_tables_sql(dialect="duckdb", tuning_config=None) -> str` returns the `CREATE TABLE` statements for both tables, with the embedding column sized to `dimensions`. The embedding column type per dialect:

| Dialect | Type for 8 dimensions |
| --- | --- |
| `duckdb` | `REAL[8]` |
| `postgresql` | `VECTOR(8)` |
| `snowflake` | `VECTOR(FLOAT, 8)` |
| `clickhouse` | `Array(Float32)` |
| `bigquery` | `ARRAY<FLOAT64>` |
| `spark`, `databricks`, `doris`, `starrocks`, `lakesail`, `pyspark`, `velox` | `ARRAY<REAL>` |
| `mysql` | `VECTOR(8, F32)` |
| other | `REAL[8]` |

With `tuning_config=None`, no keys are emitted. A `UnifiedTuningConfiguration()` with its default primary-key setting adds `PRIMARY KEY` to `vectors.id` and `vector_queries.query_id`.

## Query methods

### get_query(query_id, \*, params=None)

<span id="benchbox.vector_search.VectorSearch.get_query"></span>

`get_query(query_id, *, params=None) -> str` returns one query as DuckDB SQL.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `query_id` | `str` | required | `"Q1"` to `"Q6"`, case-insensitive (`"q1"` works). An integer is rejected, although the signature allows `int`. |
| `params` | `None` | `None` | Queries are static. Any value other than `None`, even `{}`, raises `ValueError`. |

Raises `ValueError` for an unknown id (`Invalid query ID: 'Q9'. Available: Q1, Q2, Q3, Q4, Q5, Q6`) and for `params`. The wrapper has no `dialect` argument: `get_query("Q1", dialect="postgresql")` raises `TypeError`. Get dialect text from `get_queries()`.

### get_queries(dialect=None)

<span id="benchbox.vector_search.VectorSearch.get_queries"></span>

`get_queries(dialect=None) -> dict[str, str]` returns all six queries keyed `"Q1"` to `"Q6"`. `None` returns DuckDB SQL. These dialects return rewritten SQL for every query: `postgresql` (`1 - (v.embedding <=> q.query_vector)`), `clickhouse` (`cosineDistance`), `snowflake` (`VECTOR_COSINE_SIMILARITY`), `spark`, `databricks`, `velox`, `starrocks` and `doris`. Any other name, including `postgres` and `bigquery`, returns the DuckDB SQL.

### get_all_queries()

<span id="benchbox.vector_search.VectorSearch.get_all_queries"></span>

`get_all_queries() -> dict[str, str]` returns the DuckDB SQL of all six queries, the same as `get_queries()`.

### validate_query_result(query_id, rows)

<span id="benchbox.vector_search.VectorSearch.validate_query_result"></span>

`validate_query_result(query_id, rows) -> None` checks the structure of rows that a query returned, for use after you execute a query yourself. It does not check which vectors were returned.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `query_id` | `str` | required | `"Q1"` to `"Q6"`. |
| `rows` | sequence of sequences | required | Rows as `(id, metric)`: the id and the similarity or distance. |

It passes when there are at most as many rows as the query's limit (10 for `Q1`, `Q2`, `Q3` and `Q5`, 100 for `Q4`, 20 for `Q6`), each row has exactly two columns, the ids are unique, every metric is a finite number, and the metrics are descending (ascending for `Q2`). An empty list passes.

Raises `ValueError` otherwise, for example `Q1 similarity results are not descending`, `Q1 returned duplicate id 3` or `Unknown vector-search query: Q9`.

## Inherited members

Every other member comes from `BaseBenchmark`: platform runs (`run_with_platform`), logging, `output_dir`, `scale_factor` and the loading configuration. See {doc}`/reference/python-api/base`.
