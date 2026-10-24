# CoffeeShop Benchmark API

```{tags} reference, python-api, coffeeshop
```

Python API reference for the CoffeeShop benchmark.

CoffeeShop is a three-table star schema of a coffee-shop chain (`dim_locations`, `dim_products` and the `order_lines` fact table) with 11 analytical queries.

## `benchbox.CoffeeShop`

Creates a CoffeeShop benchmark that generates three comma-delimited CSV tables and serves 11 queries.

**Import:** `from benchbox import CoffeeShop` · **Extras:** none

### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `scale_factor` | `float` | `1.0` | Data size multiplier. Must be positive. Values of 1 or more must be whole numbers. `order_lines` has 13,260 rows at 0.001 and 132,600 at 0.01. `dim_locations` (1,000 rows) and `dim_products` (26 rows) do not change with the scale factor. |
| `output_dir` | `str`, `Path` or `None` | `None` | Directory for generated data files. When `None`, the directory is `benchmark_runs/datagen/coffeeshop_<sf token>` under the current directory (for example `coffeeshop_sf001` for 0.01), or under `$BENCHBOX_OUTPUT_DIR/datagen` when that variable is set. |
| `**kwargs` | keyword arguments | none | `verbose` (`bool` or `int`) and `quiet` (`bool`) set the log level. `compress_data=True` writes each table as `<table>.csv.zst`; add `compression_type="gzip"` for `.csv.gz`. Other keywords are stored as attributes and are not validated. |

The constructor creates no files. Data is written by `generate_data()`.

### Returns

A `CoffeeShop` instance. It subclasses `BaseBenchmark`; see {doc}`/reference/python-api/base` for the shared interface.

### Raises

`ValueError` when `scale_factor` is zero or negative, or when it is 1 or more and not a whole number.

### Example

```python
from benchbox import CoffeeShop

benchmark = CoffeeShop(scale_factor=0.01, output_dir="coffeeshop_data")
files = benchmark.generate_data()
print(files)
print(list(benchmark.get_queries()))
```

Output on 0.4.1:

```text
{'dim_locations': 'coffeeshop_data/dim_locations.csv', 'dim_products': 'coffeeshop_data/dim_products.csv', 'order_lines': 'coffeeshop_data/order_lines.csv'}
['SA1', 'SA2', 'SA3', 'SA4', 'SA5', 'PR1', 'PR2', 'TR1', 'TM1', 'QC1', 'QC2']
```

### Compatibility

`benchbox.coffeeshop.CoffeeShop` is the same class.

## Methods

### `generate_data()`

`generate_data() -> dict[str, str]` writes `dim_locations.csv`, `dim_products.csv` and `order_lines.csv` to `output_dir`, plus `_datagen_manifest.json`. The files are comma-delimited with no header row. It returns a `dict` that maps table name to file path, and `benchmark.tables` holds the same mapping.

The signature is annotated `list[str | Path]`, but the value is a dict.

### `get_query(query_id, *, params=None)`

`get_query(query_id, *, params=None) -> str` returns one query as SQL text.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `query_id` | `str` | required | One of the 11 ids below. Ids are case-sensitive. An integer is rejected. |
| `params` | `dict` or `None` | `None` | Values that replace the defaults below. Names that the query does not use are ignored. |

Raises `ValueError` for an unknown id: `Query 'sa2' not found`.

| Query | Defaults |
| --- | --- |
| `SA1` | `start_date="2023-01-01"`, `end_date="2023-01-31"` |
| `SA2` | `year=2023`, `limit=15` |
| `SA3` | `year=2023` |
| `SA4` | `start_date="2023-01-01"`, `end_date="2024-12-31"` |
| `SA5` | `start_date="2023-01-01"`, `end_date="2024-12-31"`, `limit=20` |
| `PR1` | `start_date="2023-01-01"`, `end_date="2023-12-31"` |
| `PR2` | `start_date="2023-01-01"`, `end_date="2024-12-31"` |
| `TR1` | `start_year=2023`, `end_year=2024` |
| `TM1` | `region="South"`, `start_date="2023-01-01"`, `end_date="2024-12-31"` |
| `QC1` | `start_date="2023-01-01"`, `end_date="2024-12-31"` |
| `QC2` | `start_year=2023`, `end_year=2024` |

```python
from benchbox import CoffeeShop

benchmark = CoffeeShop(scale_factor=0.01)
print(benchmark.get_query("SA2", params={"year": 2024, "limit": 5}))
try:
    benchmark.get_query("sa2")
except ValueError as error:
    print(error)
```

Output on 0.4.1:

```text

SELECT
    dp.subcategory,
    dp.name AS product_name,
    SUM(ol.quantity) AS total_quantity,
    SUM(ol.total_price) AS total_revenue
FROM order_lines ol
JOIN dim_products dp ON ol.product_record_id = dp.record_id
WHERE EXTRACT(YEAR FROM ol.order_date) = 2024
GROUP BY dp.subcategory, dp.name
ORDER BY total_revenue DESC
LIMIT 5;

Query 'sa2' not found
```

### `get_queries(dialect=None)`

`get_queries(dialect=None) -> dict[str, str]` returns all 11 queries keyed `"SA1"`, `"SA2"`, `"SA3"`, `"SA4"`, `"SA5"`, `"PR1"`, `"PR2"`, `"TR1"`, `"TM1"`, `"QC1"`, `"QC2"`, rendered with the default parameters.

With a `dialect`, each query is translated with SQLGlot. For `duckdb`, `TM1` becomes `SELECT CASE WHEN EXTRACT(HOUR FROM "ol"."order_time") BETWEEN 5 AND 10 ...`: identifiers are quoted. An unknown dialect name raises `UnknownSqlDialectError`.

### `get_schema()`

`get_schema() -> dict` returns a mapping from table name to a definition with `name`, `description`, `row_count_formula` and `columns`. The keys, in order, are `dim_locations` (6 columns), `dim_products` (9) and `order_lines` (12). Each column is a dict with `name` and `type`. The annotation says `list[dict]`, but the value is a dict.

```python
from benchbox import CoffeeShop

benchmark = CoffeeShop(scale_factor=0.01)
schema = benchmark.get_schema()
for name, table in schema.items():
    print(name, [column["name"] for column in table["columns"]])
```

Output on 0.4.1:

```text
dim_locations ['record_id', 'location_id', 'city', 'state', 'country', 'region']
dim_products ['record_id', 'product_id', 'name', 'category', 'subcategory', 'standard_cost', 'standard_price', 'from_date', 'to_date']
order_lines ['order_id', 'line_number', 'location_record_id', 'location_id', 'product_record_id', 'product_id', 'order_date', 'order_time', 'quantity', 'unit_price', 'total_price', 'region']
```

### `get_create_tables_sql(dialect="standard", tuning_config=None)`

`get_create_tables_sql(dialect="standard", tuning_config=None) -> str` returns the `CREATE TABLE` statements for the three tables, `dim_locations` first.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `dialect` | `str` | `"standard"` | `standard`, `duckdb` and `bigquery` return the same script. `clickhouse` differs in one column: `order_time` is `String` instead of `TIME`. |
| `tuning_config` | `UnifiedTuningConfiguration` or `None` | `None` | With `None`, constraints are emitted: 2 `PRIMARY KEY` clauses (on the two dimension tables) and 2 `FOREIGN KEY` clauses (from `order_lines`). A configuration with `primary_keys.enabled` or `foreign_keys.enabled` false drops the matching clauses; with both false the script has none. |

```python
from benchbox import CoffeeShop

benchmark = CoffeeShop(scale_factor=0.01)
sql = benchmark.get_create_tables_sql()
print(sql.count("PRIMARY KEY"), sql.count("FOREIGN KEY"))
print(benchmark.get_create_tables_sql(dialect="clickhouse").count("order_time String"))
```

Output on 0.4.1:

```text
2 2
1
```

### Loading into DuckDB

This example loads the generated files into an in-memory DuckDB database and runs `SA3`. It needs the `duckdb` package.

```python
import duckdb
from benchbox import CoffeeShop

benchmark = CoffeeShop(scale_factor=0.01, output_dir="coffeeshop_data")
benchmark.generate_data()
connection = duckdb.connect()
connection.execute(benchmark.get_create_tables_sql())
for table, path in benchmark.tables.items():
    connection.execute(f"COPY {table} FROM '{path}' (HEADER false)")
    print(table, connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
print(connection.execute(benchmark.get_query("SA3")).fetchall()[:2])
```

Output on 0.4.1:

```text
dim_locations 1000
dim_products 26
order_lines 132600
[(2023, 1, 2397, 6173, Decimal('24923.26'), 10.397688777638715, 2.575302461410096), (2023, 2, 2280, 5864, Decimal('23674.20'), 10.38342105263158, 2.5719298245614035)]
```

## Inherited members

`run_with_platform`, `run_benchmark`, `tables`, `output_dir`, `scale_factor` and every other member come from `BaseBenchmark`. See {doc}`/reference/python-api/base` for what they do.
