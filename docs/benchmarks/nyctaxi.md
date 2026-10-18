<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# NYC Taxi OLAP Example Dataset

```{tags} intermediate, concept, nyctaxi, custom-benchmark
```

> **CLI name:** `nyctaxi` - use `benchbox run --benchmark nyctaxi`
>
> **Query templates:** [every NYC Taxi query](queries/nyctaxi/index.md)

## Overview

The NYC Taxi OLAP example dataset uses real-world NYC Taxi & Limousine Commission (TLC) trip record data for OLAP analytics testing. It is a bundled example, not a formal benchmark standard. Unlike synthetic benchmarks, it leverages actual transportation data from New York City, providing realistic distributions, seasonal patterns, and geographic analytics opportunities.

The dataset is ideal for testing analytical database performance on real-world data patterns, particularly for organizations dealing with transportation, logistics, or time-series geospatial data.

## Key Features

- **Real-world data** - Uses actual NYC TLC trip records (or realistic synthetic fallback)
- **Multi-dimensional analysis** - Temporal, geographic, and financial dimensions
- **25 OLAP queries** - Comprehensive query coverage across 9 categories
- **Zone-based geography** - 265 NYC taxi zones for geographic analytics
- **Flexible scale factors** - From testing (0.01) to full-dataset (SF=10 ceiling)
- **Date range filtering** - Configurable year and month selection
- **Standard SQL** - Queries work across multiple database platforms

## Data Source

The benchmark uses data from the [NYC TLC Trip Record Data](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page):

- **Format**: Parquet files from TLC data portal
- **Coverage**: Yellow and green taxi trips
- **Time range**: 2019-2025 (configurable)
- **Fallback**: Synthetic data generation when download unavailable

## Schema Description

The NYC Taxi benchmark uses a star schema with a fact table (trips) and dimension table (taxi_zones):

### Tables

| Table | Purpose | Approximate Rows (SF 1) |
|-------|---------|-------------------------|
| **trips** | Trip fact records with fare and location data | ~9,600,000 |
| **taxi_zones** | NYC taxi zone dimension table | 265 |

### taxi_zones Table Structure

| Column | Type | Description |
|--------|------|-------------|
| `location_id` | INTEGER | Unique zone identifier (1-265) |
| `borough` | VARCHAR | NYC borough (Manhattan, Brooklyn, Queens, Bronx, Staten Island, EWR) |
| `zone` | VARCHAR | Zone name (e.g., "Times Sq/Theatre District") |
| `service_zone` | VARCHAR | Service zone type (Yellow Zone, Boro Zone, Airports, EWR, N/A) |

### trips Table Structure

| Column | Type | Description |
|--------|------|-------------|
| `trip_id` | INTEGER | Primary key (synthetic) |
| `pickup_datetime` | TIMESTAMP | Trip start timestamp |
| `dropoff_datetime` | TIMESTAMP | Trip end timestamp |
| `pickup_location_id` | INTEGER | Pickup zone ID (FK to taxi_zones) |
| `dropoff_location_id` | INTEGER | Dropoff zone ID (FK to taxi_zones) |
| `trip_distance` | DECIMAL(10,2) | Trip distance in miles |
| `passenger_count` | INTEGER | Number of passengers |
| `rate_code_id` | INTEGER | Rate code (1-6) |
| `payment_type` | INTEGER | Payment method (1-6) |
| `fare_amount` | DECIMAL(10,2) | Base fare amount |
| `tip_amount` | DECIMAL(10,2) | Tip amount |
| `tolls_amount` | DECIMAL(10,2) | Tolls paid |
| `mta_tax` | DECIMAL(10,2) | MTA tax |
| `improvement_surcharge` | DECIMAL(10,2) | Improvement surcharge |
| `congestion_surcharge` | DECIMAL(10,2) | Congestion pricing surcharge |
| `total_amount` | DECIMAL(10,2) | Total trip cost |
| `vendor_id` | INTEGER | Taxi vendor identifier |

## Query Categories

The benchmark includes 25 queries organized into 9 categories:

### Temporal Queries
Time-based aggregations and patterns:
- [`trips-per-hour`](queries/nyctaxi/trips-per-hour.md): Hourly trip distribution
- [`trips-per-day`](queries/nyctaxi/trips-per-day.md): Daily trip patterns
- [`trips-per-month`](queries/nyctaxi/trips-per-month.md): Monthly aggregations
- [`trips-by-day-of-week`](queries/nyctaxi/trips-by-day-of-week.md): Trip patterns by weekday

### Geographic Queries
Zone-level spatial analytics:
- [`top-pickup-zones`](queries/nyctaxi/top-pickup-zones.md): Busiest pickup locations
- [`top-dropoff-zones`](queries/nyctaxi/top-dropoff-zones.md): Busiest dropoff locations
- [`top-routes`](queries/nyctaxi/top-routes.md): Most popular pickup-dropoff pairs
- [`borough-summary`](queries/nyctaxi/borough-summary.md): Borough-level aggregations

### Financial Queries
Revenue and tip analysis:
- [`revenue-by-payment-type`](queries/nyctaxi/revenue-by-payment-type.md): Revenue breakdown by payment method
- [`tip-analysis`](queries/nyctaxi/tip-analysis.md): Tip patterns by time and location
- [`fare-distribution`](queries/nyctaxi/fare-distribution.md): Fare amount distributions
- [`surcharge-revenue`](queries/nyctaxi/surcharge-revenue.md): Revenue from surcharges and extras

### Characteristics Queries
Trip attribute analysis:
- [`distance-distribution`](queries/nyctaxi/distance-distribution.md): Distribution of trip distances
- [`passenger-count-analysis`](queries/nyctaxi/passenger-count-analysis.md): Trip patterns by passenger count
- [`trip-duration-analysis`](queries/nyctaxi/trip-duration-analysis.md): Trip duration patterns

### Rate Code Queries
Rate code analysis:
- [`rate-code-summary`](queries/nyctaxi/rate-code-summary.md): Trip statistics by rate code
- [`airport-trips`](queries/nyctaxi/airport-trips.md): Analysis of airport trips (rate codes 2, 3)

### Vendor Queries
Vendor performance comparisons:
- [`vendor-comparison`](queries/nyctaxi/vendor-comparison.md): Vendor-level metrics

### Complex Queries
Multi-dimensional analytics:
- [`hourly-zone-heatmap`](queries/nyctaxi/hourly-zone-heatmap.md): Trip density by hour and zone
- [`weekday-weekend-comparison`](queries/nyctaxi/weekday-weekend-comparison.md): Compare weekday and weekend patterns
- [`rush-hour-analysis`](queries/nyctaxi/rush-hour-analysis.md): Analysis of rush hour patterns
- [`monthly-year-over-year`](queries/nyctaxi/monthly-year-over-year.md): Compare monthly patterns across years

### Point Queries
Single-value lookups:
- [`single-day-summary`](queries/nyctaxi/single-day-summary.md): Summary statistics for a single day
- [`zone-detail`](queries/nyctaxi/zone-detail.md): Detailed statistics for a specific zone

### Baseline Queries
Full table operations:
- [`full-scan-count`](queries/nyctaxi/full-scan-count.md): Count all trips (baseline scan query)

## Usage Examples

### Basic Benchmark Setup

```python
from benchbox import NYCTaxi

nyctaxi = NYCTaxi(scale_factor=1.0, output_dir="nyctaxi_data")

data_files = nyctaxi.generate_data()

queries = nyctaxi.get_queries()
print(f"Generated {len(queries)} NYC Taxi queries")

hourly_query = nyctaxi.get_query("trips-per-hour")
print(hourly_query)
```

### Configuring Data Year and Months

This example uses January through March 2023 only (Q1):

```python
nyctaxi_2023 = NYCTaxi(
    scale_factor=0.1,
    output_dir="nyctaxi_2023",
    year=2023,
    months=[1, 2, 3]
)
data_files = nyctaxi_2023.generate_data()
```

### DuckDB Integration Example

```python
import duckdb
from benchbox import NYCTaxi

nyctaxi = NYCTaxi(scale_factor=0.1, output_dir="nyctaxi_small")
data_files = nyctaxi.generate_data()

conn = duckdb.connect("nyctaxi.duckdb")
schema_sql = nyctaxi.get_create_tables_sql(dialect="duckdb")

for stmt in schema_sql.split(";"):
    if stmt.strip():
        conn.execute(stmt)

zones_file = nyctaxi.tables["taxi_zones"]
trips_file = nyctaxi.tables["trips"]

conn.execute(f"""
    INSERT INTO taxi_zones
    SELECT * FROM read_csv('{zones_file}', header=true)
""")

conn.execute(f"""
    INSERT INTO trips
    SELECT * FROM read_csv('{trips_file}', header=true)
""")

queries = nyctaxi.get_queries()

for query_id, query_sql in list(queries.items())[:5]:
    result = conn.execute(query_sql).fetchall()
    print(f"{query_id}: {len(result)} rows")

conn.close()
```

### Query Categories Example

```python
from benchbox import NYCTaxi

nyctaxi = NYCTaxi(scale_factor=0.01)

temporal_queries = nyctaxi.get_queries_by_category("temporal")
print(f"Temporal queries: {temporal_queries}")

geographic_queries = nyctaxi.get_queries_by_category("geographic")
print(f"Geographic queries: {geographic_queries}")

financial_queries = nyctaxi.get_queries_by_category("financial")
print(f"Financial queries: {financial_queries}")

info = nyctaxi.get_query_info("trips-per-hour")
print(f"Query info: {info}")
```

## CLI Options (`--benchmark-option`)

Configure NYC Taxi data generation via `--benchmark-option KEY=VALUE`:

| Option | Default | Description |
|--------|---------|-------------|
| `taxi_types` | all | Comma-separated taxi types: `yellow`, `green`, `hvfhv` |
| `year` | `2019` | Year of TLC data to load (2019-2025) |
| `months` | all | Comma-separated months to include (1-12) |
| `seed` | - | Random seed for reproducibility |
| `force_regenerate` | - | Force data regeneration (`true`/`false`) |

Options accept hyphenated aliases (e.g. `taxi-types` for `taxi_types`).

This example loads only yellow and green trips from January through March 2022:

```bash
benchbox run --platform duckdb --benchmark nyctaxi --scale 1 \
  --benchmark-option taxi_types=yellow,green \
  --benchmark-option year=2022 \
  --benchmark-option months=1,2,3
```

## Spatial Queries

The NYC Taxi benchmark includes a geospatial layer
(`benchbox/core/nyctaxi/spatial.py`) that exposes platform-specific spatial
queries against a companion `taxi_zones_spatial` table. These queries are
**not** automatically included in a standard benchmark run - they require a
spatial extension on the target platform and are retrieved via the spatial
API rather than through `--benchmark-option`.

### Platform Support

`check_spatial_support(platform)` returns the matrix of capabilities used to
decide which queries are runnable on a given platform:

| Platform | Extension | Distance fns | Geohash | H3 | Notes |
|----------|-----------|--------------|---------|----|-------|
| DuckDB | `duckdb_spatial` | `ST_Distance`, `ST_Point`, `ST_Centroid`, `ST_Collect` | No | No | Basic Euclidean operations |
| PostgreSQL | PostGIS | Full ST_* including `ST_DWithin`, `ST_ConvexHull`, `geography` | Yes | Via extension | Most complete support |
| ClickHouse | Native geo | `geoDistance` (no `ST_*`) | Yes | Yes | Different API surface |

Platforms not listed return `{"basic_spatial": False}`.

### Query Catalogs

Each platform exposes a subset of spatial query IDs from
`spatial.py` (10 DuckDB / 4 PostGIS / 4 ClickHouse):

| Category | DuckDB | PostGIS | ClickHouse |
|----------|--------|---------|------------|
| `spatial-distance-top-routes` | ✅ | ✅ | ✅ |
| `spatial-radius-search` | ✅ | ✅ | ✅ |
| `spatial-airport-distance` | ✅ | ✅ | - |
| `spatial-borough-centroids` | ✅ | - | - |
| `spatial-cross-borough` | ✅ | - | - |
| `spatial-zone-clustering` | ✅ | - | - |
| `spatial-manhattan-grid` | ✅ | - | - |
| `spatial-boundary-box` | ✅ | - | - |
| `spatial-nearest-zones` | ✅ | - | - |
| `spatial-trip-direction` | ✅ | - | - |
| `spatial-convex-hull` | - | ✅ | - |
| `spatial-geohash-aggregation` | - | - | ✅ |
| `spatial-h3-aggregation` | - | - | ✅ |

### Reference Data

`TAXI_ZONE_CENTROIDS` in `spatial.py` provides representative
`(longitude, latitude)` points for the NYC TLC zones most commonly referenced
by the spatial queries (airports, Manhattan neighborhoods, key Brooklyn and
Queens zones). The full zone geometry is not included - callers materialize
centroids into the `taxi_zones_spatial` table at setup time and the geometry
column is populated per-platform.

### API Reference

```python
from benchbox.core.nyctaxi import (
    get_spatial_queries,
    get_spatial_create_table_sql,
    check_spatial_support,
)
```

- **`get_spatial_queries(platform: str) -> dict`** - returns the spatial query
  catalog for `platform` (`duckdb`, `postgres`/`postgresql`/`postgis`, or any
  ClickHouse variant). Unknown platforms return an empty dict.
- **`get_spatial_create_table_sql(dialect: str = "duckdb") -> str`** -
  returns the `CREATE TABLE taxi_zones_spatial` statement in the requested
  dialect. PostgreSQL output includes a generated `geom` column (PostGIS
  `GEOMETRY(POINT, 4326)`) and a GIST index.
- **`check_spatial_support(platform: str) -> dict[str, bool]`** - capability
  map (keys: `basic_spatial`, `st_distance`, `st_point`, `st_centroid`,
  `geohash`, `h3`, `geography`, …). Use this to filter query catalogs before
  execution.

The spatial layer is not currently wired to a `--benchmark-option` flag; it
is accessed programmatically via the functions above.

## Scale Factor Guidelines

| Scale Factor | Trips | Data Size | Memory Usage | Use Case |
|-------------|-------|-----------|--------------|----------|
| 0.01 | ~96K | ~10 MB | < 100 MB | Quick testing |
| 0.1 | ~960K | ~96 MB | < 500 MB | Development |
| 1.0 | ~9.6M | ~0.96 GB | < 4 GB | Standard benchmark |
| 10.0 | ~96M | ~9.6 GB | < 20 GB | Performance testing (ceiling) |
| 100.0 | ~96M | ~9.6 GB | < 20 GB | ⚠️ Saturated - same as SF=10 |

> **Note:** Each taxi type's sample rate saturates at 1.0 when SF ≥ 10. Beyond SF=10, the full
> available dataset is used and no additional scaling occurs. This applies independently to each
> taxi type (Yellow, Green, HVFHV).
>
> **Why not extend beyond the ceiling with synthetic data?** NYC Taxi is a *real-data* benchmark -
> its value comes from actual trip distributions, fare structures, and temporal patterns that
> synthetic generators cannot faithfully replicate. Mixing synthetic rows into real data would
> compromise the benchmark's core purpose: evaluating database performance against authentic
> workload characteristics. For larger synthetic datasets, use TPC-H or CoffeeShop instead.

## Performance Characteristics

### Query Performance Patterns

**Temporal Queries:**
- **Bottleneck**: Date/time extraction and grouping
- **Optimization**: Temporal indexes, date partitioning
- **Typical performance**: Fast (seconds)

**Geographic Queries:**
- **Bottleneck**: Join with taxi_zones dimension table
- **Optimization**: Zone ID indexes, denormalization
- **Typical performance**: Fast to medium

**Financial Queries:**
- **Bottleneck**: Aggregation over numeric columns
- **Optimization**: Columnar storage, SIMD operations
- **Typical performance**: Fast

**Complex Queries:**
- **Bottleneck**: Multi-dimensional grouping, joins
- **Optimization**: Materialized views, query caching
- **Typical performance**: Medium to slow

## Data Characteristics

The NYC Taxi data exhibits realistic patterns:

- **Temporal patterns**: Peak hours (7-9am, 5-7pm), weekday/weekend differences
- **Geographic clusters**: Manhattan Yellow Zones dominate, airport traffic patterns
- **Fare distributions**: Right-skewed with peak around $10-15
- **Tip patterns**: Strong correlation with fare amount, payment type
- **Seasonal variations**: Holiday effects, summer vs winter patterns

## Best Practices

### Data Generation
1. **Start small** - Use SF=0.01 for initial testing
2. **Choose appropriate year** - Match your analysis timeframe
3. **Consider months** - Use specific months for seasonal analysis

### Query Optimization
1. **Index zone IDs** - For geographic join performance
2. **Partition by date** - For temporal query efficiency
3. **Materialize zones** - Denormalize frequently-joined columns

### Performance Testing
1. **Warm-up queries** - Run queries multiple times
2. **Monitor resources** - Track CPU, memory, I/O
3. **Compare categories** - Different query types stress different components

## Common Issues and Solutions

### Data Download Failures

**Issue**: Unable to download TLC data (network restrictions, rate limiting)
```python
nyctaxi = NYCTaxi(scale_factor=0.1)
data_files = nyctaxi.generate_data()
```

**Solution**: The synthetic data fallback is automatic. If the download fails, the benchmark generates synthetic data.

### Memory Issues with Large Scale Factors

**Issue**: Out of memory during data generation
```python
for month in [1, 2, 3]:
    nyctaxi = NYCTaxi(
        scale_factor=10.0,
        year=2019,
        months=[month]
    )
    data_files = nyctaxi.generate_data()
```

**Solution**: Process the data in smaller chunks by month, and unload each month's data before generating the next.

### Query Date Range Issues

**Issue**: Queries return no results
```python
nyctaxi = NYCTaxi(year=2023, months=[1])
```

**Solution**: Ensure the query date parameters match the generated data. Here the data covers January 2023, so queries are parameterized for January 1-31, 2023.

## Related Documentation

- [H2ODB Benchmark](h2odb.md) - Synthetic taxi data benchmark
- [ClickBench](clickbench.md) - Analytics-focused benchmark
- [SSB](ssb.md) - Star schema benchmark
- [Read Primitives](read-primitives.md) - Basic database operations

## External Resources

- [NYC TLC Trip Record Data](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page) - Official data source
- [NYC Taxi Zones](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page) - Zone geography
- [TLC Data Dictionary](https://www.nyc.gov/assets/tlc/downloads/pdf/data_dictionary_trip_records_yellow.pdf) - Column definitions
