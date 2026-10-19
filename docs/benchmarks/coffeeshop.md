<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# CoffeeShop Example Dataset

```{tags} beginner, concept, coffeeshop, custom-benchmark
```

> **CLI name:** `coffeeshop` - use `benchbox run --benchmark coffeeshop`
>
> **Query templates:** [every CoffeeShop query](queries/coffeeshop/index.md)

## Overview

The CoffeeShop example dataset provides a realistic point-of-sale and retail analytics workload based on a coffee shop business model. It is a bundled example, not a formal benchmark standard. It features a compact star schema with temporal, regional, and pricing dynamics that mirror real-world retail operations.

## Key Features

- **Realistic Retail Schema**: Star schema with dimension and fact tables modeling actual coffee shop operations
- **Temporal Dynamics**: Time-series data with seasonal patterns and trends
- **Regional Analysis**: Multi-region support with geographic metadata and regional weights
- **Product Catalog**: Canonical product catalog with seasonal availability
- **Query Suite**: 11 analytical queries across sales, products, trends, and quality checks (`SA*`, `PR*`, `QC*`)

## Schema

### Tables

#### `dim_locations`
Geographic metadata and regional weights for store locations.

**Columns:**
- `record_id` - Surrogate key
- `location_id` - Business key
- `city` - City name
- `state` - State/province
- `region` - Geographic region
- `regional_weight` - Weight for regional sales distribution

#### `dim_products`
Canonical product catalog with seasonal availability.

**Columns:**
- `record_id` - Surrogate key
- `product_id` - Business key
- `name` - Product name
- `subcategory` - Product category (e.g., Coffee, Tea, Pastries)
- `from_date` - Product availability start date
- `to_date` - Product availability end date

#### `order_lines`
Exploded fact table with 1-5 lines per order.

**Columns:**
- `order_id` - Order identifier
- `order_date` - Transaction date
- `location_record_id` - FK to dim_locations
- `product_record_id` - FK to dim_products
- `quantity` - Items ordered
- `unit_price` - Price per item
- `total_price` - Line total (quantity × unit_price)

## Query Categories

### Sales Analysis (SA*)

**[SA1](queries/coffeeshop/sa1.md)**: Daily revenue and order volume by region
**[SA2](queries/coffeeshop/sa2.md)**: Top products by revenue for a given year
**[SA3](queries/coffeeshop/sa3.md)**: Monthly performance metrics
**[SA4](queries/coffeeshop/sa4.md)**: Revenue share by region
**[SA5](queries/coffeeshop/sa5.md)**: Top-performing locations by revenue

### Product Mix (PR*)

**[PR1](queries/coffeeshop/pr1.md)**: Product mix and revenue by subcategory
**[PR2](queries/coffeeshop/pr2.md)**: Price-band distribution across order lines
**PR3**: Product performance ranking

### Trend Analysis (TR*)

**[TR1](queries/coffeeshop/tr1.md)**: Month-over-month revenue trends
**TR2**: Year-over-year comparisons
**TR3**: Seasonal pattern detection

### Time Analysis (TM*)

**[TM1](queries/coffeeshop/tm1.md)**: Peak hour analysis
**TM2**: Weekday vs weekend patterns
**TM3**: Time-of-day revenue distribution

### Quality Checks (QC*)

**[QC1](queries/coffeeshop/qc1.md)**: Data completeness validation
**[QC2](queries/coffeeshop/qc2.md)**: Referential integrity checks
**QC3**: Business rule validation

## Quick Start

```python
from benchbox import CoffeeShop

benchmark = CoffeeShop(scale_factor=0.01, output_dir="./coffeeshop_data")

data_files = benchmark.generate_data()

import duckdb
conn = duckdb.connect(":memory:")
conn.execute(benchmark.get_create_tables_sql())
for table, path in data_files.items():
    conn.execute(f"INSERT INTO {table} SELECT * FROM read_csv('{path}', header=false)")

query = benchmark.get_query("SA1", params={"start_date": "2023-01-01", "end_date": "2023-01-31"})
result = conn.execute(query).fetchdf()
print(result)
```

## Scale Factors

The scale factor controls the volume of data generated. SF=1 targets approximately 1 GB of uncompressed CSV data, consistent with other BenchBox benchmarks.

- **0.001**: ~8,500 orders (development/testing)
- **0.01**: ~85,000 orders (CI/CD, quick validation)
- **0.1**: ~850,000 orders (integration testing)
- **1.0**: ~8,500,000 orders (standard benchmark, ~1 GB)
- **10.0**: ~85,000,000 orders (large-scale testing)

## CLI Usage

The first command generates CoffeeShop data (and runs the benchmark). The second runs specific queries. The third runs the sales analysis queries by listing them, because there is no pattern option.

```bash
benchbox run --benchmark coffeeshop --platform duckdb --scale 1.0

benchbox run --benchmark coffeeshop --platform duckdb --queries SA1,SA2,PR1

benchbox run --benchmark coffeeshop --platform duckdb --queries SA1,SA2,SA3
```

## Use Cases

### Retail Analytics
Test queries for:
- Sales performance tracking
- Product mix optimization
- Regional performance comparison
- Trend analysis and forecasting

### Hybrid Workloads
The benchmark combines:
- **Transactional patterns**: Order entry and updates
- **Analytical queries**: Aggregations and trends
- **Mixed complexity**: Simple lookups to complex analytics

### Real-World Validation
Validate performance with:
- Realistic data distributions
- Seasonal patterns
- Regional variations
- Product lifecycle dynamics

## Query Examples

### Sales Analysis

[SA1](queries/coffeeshop/sa1.md): daily revenue and order volume by region.

```sql
SELECT
    ol.order_date,
    dl.region,
    COUNT(DISTINCT ol.order_id) AS order_count,
    SUM(ol.total_price) AS gross_revenue,
    SUM(ol.total_price) / NULLIF(COUNT(DISTINCT ol.order_id), 0) AS avg_order_value
FROM order_lines ol
JOIN dim_locations dl ON ol.location_record_id = dl.record_id
WHERE ol.order_date BETWEEN DATE '2023-01-01' AND DATE '2023-01-31'
GROUP BY ol.order_date, dl.region
ORDER BY ol.order_date, dl.region;
```

### Product Analysis

[PR1](queries/coffeeshop/pr1.md): product mix and revenue by subcategory.

```sql
SELECT
    dp.subcategory,
    COUNT(DISTINCT dp.product_id) AS active_products,
    SUM(ol.quantity) AS quantity_sold,
    SUM(ol.total_price) AS revenue
FROM order_lines ol
JOIN dim_products dp ON ol.product_record_id = dp.record_id
WHERE ol.order_date BETWEEN DATE '2023-01-01' AND DATE '2023-12-31'
  AND ol.order_date BETWEEN DATE(dp.from_date) AND DATE(dp.to_date)
GROUP BY dp.subcategory
ORDER BY revenue DESC;
```

## Platform Support

### Fully Supported
- DuckDB
- SQLite
- ClickHouse
- Snowflake
- BigQuery
- Redshift
- Databricks

### Testing Recommendations

**Development**: Use DuckDB with scale factor 0.001-0.01 for fast iteration

**CI/CD**: Use scale factor 0.01 with key queries ([SA1](queries/coffeeshop/sa1.md), [SA2](queries/coffeeshop/sa2.md), [PR1](queries/coffeeshop/pr1.md)) for regression testing

**Production Evaluation**: Use scale factor 1.0+ on target platform for realistic performance testing

## Performance Characteristics

### Expected Query Times (Scale Factor 1.0, DuckDB)

| Query Type | Simple Queries | Complex Queries |
|------------|----------------|-----------------|
| Sales Analysis | 10-50ms | 50-200ms |
| Product Mix | 20-100ms | 100-500ms |
| Trend Analysis | 50-200ms | 200ms-1s |

### Data Sizes

| Scale Factor | Orders | Order Lines | Data Size |
|--------------|--------|-------------|-----------|
| 0.001 | ~8.5K | ~13K | ~1 MB |
| 0.01 | ~85K | ~133K | ~10 MB |
| 0.1 | ~850K | ~1.3M | ~100 MB |
| 1.0 | ~8.5M | ~13.3M | ~1 GB |
| 10.0 | ~85M | ~133M | ~10 GB |

## API Reference

### CoffeeShop Class

```python
class CoffeeShop(BaseBenchmark):

    def __init__(self, scale_factor: float = 1.0, output_dir: Optional[str] = None, **kwargs):
        pass

    def generate_data(self) -> list[Path]:
        pass

    def get_queries(self, dialect: Optional[str] = None) -> dict[str, str]:
        pass

    def get_query(self, query_id: str, *, params: Optional[dict] = None) -> str:
        pass

    def get_schema(self) -> list[dict]:
        pass

    def get_create_tables_sql(self, dialect: str = "standard") -> str:
        pass
```

## Related Documentation

- [Benchmarks Overview](index.md) - All available benchmarks
- [TPC-H](tpc-h.md) - Industry standard decision support benchmark
- [Read Primitives](read-primitives.md) - Fundamental operation testing
- [Usage Guide](../usage/getting-started.md) - General BenchBox usage

## License

Copyright 2026 Joe Harris / BenchBox Project

Licensed under the MIT License.
