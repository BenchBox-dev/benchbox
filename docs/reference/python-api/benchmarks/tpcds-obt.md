# TPCDSOBT Benchmark API

```{tags} reference, python-api, tpcds-obt
```

Python API reference for the TPC-DS One Big Table (OBT) benchmark.

TPCDSOBT flattens the TPC-DS sales and returns facts and their dimensions into one wide table, `tpcds_sales_returns_obt`, and serves TPC-DS queries rewritten to run against that table.

Two limits apply to the 0.4.1 release:

- `get_query()` and `get_queries()` fail with `FileNotFoundError` when BenchBox is installed from PyPI, because the TPC-DS query templates they read are not at the location they look in. See `get_query()` below.
- `generate_data()` needs a scale factor of at least 1, which writes the full TPC-DS source data first.

## `benchbox.TPCDSOBT`

Creates a TPC-DS OBT benchmark that provides a single wide table and its `CREATE TABLE` statement.

**Import:** `from benchbox import TPCDSOBT` · **Extras:** none

### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `scale_factor` | `float` | `1.0` | TPC-DS scale factor. Must be a whole number of at least 1. |
| `output_dir` | `str`, `Path` or `None` | `None` | Directory for the OBT file. When `None`, the directory is `benchmark_runs/datagen/tpcds_obt_<sf token>` under the current directory (for example `tpcds_obt_sf1`), or under `$BENCHBOX_OUTPUT_DIR/datagen` when that variable is set. |
| `**kwargs` | keyword arguments | none | `tpcds_source_dir` is the directory for the TPC-DS source data (default `benchmark_runs/datagen/tpcds_<sf token>`). `output_format` is `"parquet"` (default) or `"dat"`. `dimension_mode` is `"full"` (default) or `"minimal"`. `channels` is a list drawn from `"store"`, `"web"` and `"catalog"` (default: all three). `parallel` (`int`, default `1`), `force_regenerate` (`bool`) and the compression keywords `compress_data`, `compression_type` and `compression_level` are passed to the TPC-DS generator. `verbose` (`bool` or `int`) and `quiet` (`bool`) set the log level. The constructor does not validate their values. |

The constructor creates no files.

### Returns

A `TPCDSOBT` instance. It subclasses `BaseBenchmark`; see {doc}`/reference/python-api/base` for the shared interface.

### Raises

- `ValueError` when `scale_factor` is below 1, including zero and negative values: `TPC-DS-OBT requires scale_factor >= 1.0 to align with TPC-DS generation.`
- `ValueError` when `scale_factor` is above 1 and not a whole number: `Scale factors >= 1 must be whole integers. Got: 1.5. ...`

### Example

```python
from pathlib import Path
from benchbox import TPCDSOBT

for scale_factor in (0.5, 1.5):
    try:
        TPCDSOBT(scale_factor=scale_factor)
    except ValueError as error:
        print(scale_factor, str(error)[:70])
print(TPCDSOBT(scale_factor=1).output_dir.relative_to(Path.cwd()))
custom = TPCDSOBT(scale_factor=1, output_dir="obt_out", tpcds_source_dir="tpcds_src", output_format="dat")
print(custom.output_dir)
```

Output on 0.4.1:

```text
0.5 TPC-DS-OBT requires scale_factor >= 1.0 to align with TPC-DS generatio
1.5 Scale factors >= 1 must be whole integers. Got: 1.5. Use values like 1
benchmark_runs/datagen/tpcds_obt_sf1
obt_out
```

### Compatibility

`benchbox.tpcds_obt.TPCDSOBT` is the same class.

## Methods

### `generate_data()`

`generate_data() -> dict[str, Any]` generates the TPC-DS source data in `tpcds_source_dir`, then transforms it into the OBT table in `output_dir`. It returns a `dict` with the keys `table` (the path of the OBT file) and `manifest` (the path of `tpcds_sales_returns_obt_manifest.json`); `benchmark.tables` maps `tpcds_sales_returns_obt` to the table path.

Scale factor 1 writes about a gigabyte of source data.

### `get_query(query_id)`

`get_query(query_id, **kwargs) -> str` is meant to return one TPC-DS query rewritten for the OBT table. `query_id` is an `int` or a numeric `str`.

On 0.4.1 installed from PyPI it raises `FileNotFoundError` for every query id, because the query templates are not where it looks for them.

```python
from benchbox import TPCDSOBT

benchmark = TPCDSOBT(scale_factor=1)
try:
    benchmark.get_query(1)
except FileNotFoundError as error:
    print(type(error).__name__, str(error).split(" not found at ")[0])
```

Output on 0.4.1 from the PyPI wheel:

```text
FileNotFoundError Template for query 1
```

### `get_queries(dialect=None, base_dialect=None)`

`get_queries(dialect=None, base_dialect=None) -> dict[str, str]` is meant to return every OBT query keyed by its id as a string. On 0.4.1 installed from PyPI it raises the same `FileNotFoundError` as `get_query()`.

### `get_schema()`

`get_schema() -> dict` returns a mapping with one key, `tpcds_sales_returns_obt`, whose value is a table object with these members:

| Member | Meaning |
| --- | --- |
| `name` | `"tpcds_sales_returns_obt"`. |
| `columns` | A list of 715 column objects. Each has `name`, `data_type`, `size`, `nullable`, `primary_key`, `source_table`, `source_column`, `role` and `description`. The first column is `channel`, a `VARCHAR` of size 10 that is not nullable. |
| `get_create_table_sql()` | The `CREATE TABLE` statement, identical to `get_create_tables_sql()`. |
| `get_primary_key()` | The primary-key columns. It returns an empty list: the table has no primary key. |

### `get_create_tables_sql(dialect=None)`

`get_create_tables_sql(dialect=None) -> str` returns one `CREATE TABLE` statement (520 lines) for the OBT table. It has no `tuning_config` parameter: passing one raises `TypeError`.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `dialect` | `str` or `None` | `None` | `None`, `duckdb` and `standard` return the same script. `clickhouse` and `bigquery` each return a different script. |

```python
from benchbox import TPCDSOBT

benchmark = TPCDSOBT(scale_factor=1)
schema = benchmark.get_schema()
table = schema["tpcds_sales_returns_obt"]
print(list(schema), len(table.columns))
print(table.columns[0].name, table.columns[0].data_type.value, table.columns[0].size, table.columns[0].nullable)
sql = benchmark.get_create_tables_sql()
print(sql.count("CREATE TABLE"), len(sql.splitlines()))
print(sql == benchmark.get_create_tables_sql(dialect="duckdb") == benchmark.get_create_tables_sql(dialect="standard"))
print(sql == benchmark.get_create_tables_sql(dialect="clickhouse"))
```

Output on 0.4.1:

```text
['tpcds_sales_returns_obt'] 518
channel VARCHAR 10 False
1 520
True
False
```

## Inherited members

`run_with_platform`, `run_benchmark`, `tables`, `output_dir`, `scale_factor` and every other member come from `BaseBenchmark`. See {doc}`/reference/python-api/base` for what they do.
