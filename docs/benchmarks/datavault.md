<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# TPC-H Data Vault Benchmark

```{tags} advanced, concept, datavault, custom-benchmark
```

> **CLI name:** `datavault` - use `benchbox run --benchmark datavault`

## Overview

The TPC-H Data Vault benchmark tests database performance with Data Vault 2.0 modeling patterns. It transforms TPC-H's 8 normalized tables into 21 Data Vault tables and adapts all 22 TPC-H queries to work with Hub-Link-Satellite joins.

Data Vault 2.0 is an enterprise data warehouse modeling methodology that separates business keys (Hubs), relationships (Links), and descriptive attributes (Satellites). This design enables incremental loading, full auditability, and historical tracking.

## Key Features

- **21 Data Vault tables** - 7 Hubs, 6 Links, 8 Satellites
- **22 adapted queries** - TPC-H queries rewritten for Hub-Link-Satellite patterns
- **MD5 hash keys** - Surrogate keys using 32-character hash values
- **Audit columns** - LOAD_DTS, LOAD_END_DTS, RECORD_SOURCE on all tables
- **HASHDIFF tracking** - Change detection for satellite records
- **TPC-H source data** - Leverages existing TPC-H data generation infrastructure
- **SQL dialect support** - DDL translation via SQLGlot

## Schema Description

### Table Types

| Type | Count | Purpose |
|------|-------|---------|
| **Hubs** | 7 | Business keys with hash surrogates |
| **Links** | 6 | Relationships between Hubs |
| **Satellites** | 8 | Descriptive attributes with temporal tracking |

### Hub Tables (Business Entities)

| Table | Business Key | Source |
|-------|-------------|--------|
| `hub_region` | r_regionkey | REGION |
| `hub_nation` | n_nationkey | NATION |
| `hub_customer` | c_custkey | CUSTOMER |
| `hub_supplier` | s_suppkey | SUPPLIER |
| `hub_part` | p_partkey | PART |
| `hub_order` | o_orderkey | ORDERS |
| `hub_lineitem` | l_orderkey + l_linenumber | LINEITEM |

### Link Tables (Relationships)

| Table | Connects |
|-------|----------|
| `link_nation_region` | Nation → Region |
| `link_customer_nation` | Customer → Nation |
| `link_supplier_nation` | Supplier → Nation |
| `link_part_supplier` | Part ↔ Supplier |
| `link_order_customer` | Order → Customer |
| `link_lineitem` | LineItem → Order, Part, Supplier |

### Satellite Tables (Attributes)

| Table | Parent | Key Attributes |
|-------|--------|----------------|
| `sat_region` | hub_region | r_name, r_comment |
| `sat_nation` | hub_nation | n_name, n_comment |
| `sat_customer` | hub_customer | c_name, c_address, c_acctbal, c_mktsegment |
| `sat_supplier` | hub_supplier | s_name, s_address, s_acctbal |
| `sat_part` | hub_part | p_name, p_mfgr, p_brand, p_type, p_size |
| `sat_partsupp` | link_part_supplier | ps_availqty, ps_supplycost |
| `sat_order` | hub_order | o_orderstatus, o_totalprice, o_orderdate |
| `sat_lineitem` | link_lineitem | l_quantity, l_extendedprice, l_discount, dates |

### Schema Relationships

```{mermaid}
erDiagram
    hub_region ||--o{ link_nation_region : "hk_region"
    hub_nation ||--o{ link_nation_region : "hk_nation"
    hub_nation ||--o{ link_customer_nation : "hk_nation"
    hub_nation ||--o{ link_supplier_nation : "hk_nation"
    hub_customer ||--o{ link_customer_nation : "hk_customer"
    hub_customer ||--o{ link_order_customer : "hk_customer"
    hub_supplier ||--o{ link_supplier_nation : "hk_supplier"
    hub_supplier ||--o{ link_part_supplier : "hk_supplier"
    hub_part ||--o{ link_part_supplier : "hk_part"
    hub_part ||--o{ link_lineitem : "hk_part"
    hub_order ||--o{ link_order_customer : "hk_order"
    hub_order ||--o{ link_lineitem : "hk_order"
    hub_lineitem ||--o{ link_lineitem : "hk_lineitem"

    hub_region ||--|| sat_region : "hk_region"
    hub_nation ||--|| sat_nation : "hk_nation"
    hub_customer ||--|| sat_customer : "hk_customer"
    hub_supplier ||--|| sat_supplier : "hk_supplier"
    hub_part ||--|| sat_part : "hk_part"
    hub_order ||--|| sat_order : "hk_order"
    link_part_supplier ||--|| sat_partsupp : "hk_part_supplier"
    link_lineitem ||--|| sat_lineitem : "hk_lineitem_link"
```

### Loading Order

Tables must be loaded respecting referential integrity:

1. **Hubs** (no dependencies): hub_region → hub_nation → hub_customer → hub_supplier → hub_part → hub_order → hub_lineitem
2. **Links** (depend on Hubs): link_nation_region → link_customer_nation → link_supplier_nation → link_part_supplier → link_order_customer → link_lineitem
3. **Satellites** (depend on Hubs/Links): sat_region → sat_nation → sat_customer → sat_supplier → sat_part → sat_partsupp → sat_order → sat_lineitem

## Query Characteristics

All 22 TPC-H queries are adapted for the Data Vault schema using Hub→Satellite→Link join patterns. Each query filters for current records using `load_end_dts IS NULL`.

```{tip}
Each query has a [query template](queries/datavault/index.md) with a
representative SQL and DataFrame rendering, plus how to extract the exact
statement for your platform and scale.
```

### Query Complexity

| Category | Queries | Pattern |
|----------|---------|---------|
| **Simple** | [Q1](queries/datavault/q1.md), [Q6](queries/datavault/q6.md) | Single link + satellite, aggregation |
| **Medium** | [Q3](queries/datavault/q3.md), [Q4](queries/datavault/q4.md), [Q10](queries/datavault/q10.md), [Q12](queries/datavault/q12.md), [Q14](queries/datavault/q14.md) | 3-5 table joins |
| **Complex** | [Q2](queries/datavault/q2.md), [Q5](queries/datavault/q5.md), [Q7](queries/datavault/q7.md), [Q8](queries/datavault/q8.md), [Q9](queries/datavault/q9.md) | 8+ table chains with subqueries |
| **Advanced** | [Q15](queries/datavault/q15.md), [Q17](queries/datavault/q17.md), [Q20](queries/datavault/q20.md), [Q21](queries/datavault/q21.md) | CTEs, correlated subqueries, EXISTS |

### Example Query (Q1 - Pricing Summary)

```sql
SELECT
    sl.l_returnflag,
    sl.l_linestatus,
    SUM(sl.l_quantity) AS sum_qty,
    SUM(sl.l_extendedprice) AS sum_base_price,
    SUM(sl.l_extendedprice * (1 - sl.l_discount)) AS sum_disc_price,
    AVG(sl.l_discount) AS avg_disc,
    COUNT(*) AS count_order
FROM link_lineitem ll
JOIN sat_lineitem sl ON ll.hk_lineitem_link = sl.hk_lineitem_link
    AND sl.load_end_dts IS NULL
WHERE sl.l_shipdate <= DATE '1998-12-01' - INTERVAL '90' DAY
GROUP BY sl.l_returnflag, sl.l_linestatus
ORDER BY sl.l_returnflag, sl.l_linestatus
```

## Usage Examples

### Basic Usage

```python
from benchbox import DataVault

dv = DataVault(scale_factor=1.0, output_dir="datavault_data")

data_files = dv.generate_data()
print(f"Generated {len(data_files)} tables")

queries = dv.get_queries()
query_1 = dv.get_query(1)
```

`generate_data()` transforms TPC-H data into Data Vault tables.

### DuckDB Integration

```python
import duckdb
from benchbox import DataVault

dv = DataVault(scale_factor=0.1, output_dir="dv_test")
data_files = dv.generate_data()

conn = duckdb.connect("datavault.duckdb")
conn.execute(dv.get_create_tables_sql())

for table_name in dv.get_table_loading_order():
    file_path = data_files.get(table_name)
    if file_path:
        conn.execute(f"""
            INSERT INTO {table_name}
            SELECT * FROM read_csv('{file_path}', delim='|', header=false)
        """)

for query_id in range(1, 23):
    result = conn.execute(dv.get_query(query_id)).fetchall()
    print(f"Q{query_id}: {len(result)} rows")
```

The example generates the Data Vault data, creates the schema, loads the tables in loading order, and then runs all 22 queries.

### CLI Usage

```bash
benchbox run --platform duckdb --benchmark datavault --scale 1.0 --phases generate

benchbox run --platform duckdb --benchmark datavault --scale 1.0 --phases power

benchbox run --dry-run ./preview --platform duckdb --benchmark datavault --scale 0.1
```

The commands generate data, run the power test, and do a dry run that previews the queries.

## Configuration Options

### CLI Options (`--benchmark-option`)

Configure Data Vault via `--benchmark-option KEY=VALUE`:

| Option | Default | Description |
|--------|---------|-------------|
| `hash_algorithm` | `md5` | Hash algorithm for hub/link keys. Only `md5` is accepted by the benchmark runner (`SUPPORTED_HASH_ALGORITHMS = ("md5",)` in `datavault/benchmark.py`); the `VARCHAR(32)` hash-key column constrains the schema to 32-hex-char outputs. The lower-level `datavault/etl/hash_functions.py` helper also accepts `sha1` and (truncated) `sha256`, but those are only reachable from library code, not the CLI. |
| `record_source` | `TPCH` | Record source identifier for audit columns |
| `force_regenerate` | - | Force data regeneration (`true`/`false`) |

Accepts hyphenated aliases (e.g. `hash-algorithm`, `record-source`).

```bash
benchbox run --platform duckdb --benchmark datavault --scale 1 \
  --benchmark-option record_source=MY_SOURCE
```

This sets a custom record source. The hash algorithm stays `md5`, because the CLI rejects SHA-256.

### Initialization Parameters

```python
DataVault(
    scale_factor=1.0,
    output_dir="output",
    parallel=4,
    force_regenerate=False,
    hash_algorithm="md5",
    record_source="TPCH",
    compress_data=False,
    compression_type="gzip"
)
```

- `scale_factor`: TPC-H scale factor (1.0 is about 1 GB of source data).
- `output_dir`: directory for generated files.
- `parallel`: parallel workers for TPC-H generation.
- `force_regenerate`: regenerate even if data exists.
- `hash_algorithm`: hash algorithm (only `md5` is supported).
- `record_source`: source identifier for audit columns.
- `compress_data`: enable file compression.
- `compression_type`: compression type (`gzip` or `zstd`).

### TPC-H Source File Discovery

The Data Vault transformer (`datavault/etl/transformer.py`) reads the upstream
TPC-H `.tbl` files and supports three on-disk layouts for each source table,
probed in order:

1. **Compressed, sharded** - `customer.tbl.1.zst`, `customer.tbl.2.zst`, …
   (also `.gz`). Preferred when present.
2. **Compressed, single file** - `customer.tbl.zst` or `customer.tbl.gz`.
3. **Uncompressed, sharded** - `customer.tbl.1`, `customer.tbl.2`, … Matched
   when the final dot-separated segment is numeric; returned as a glob pattern
   so DuckDB reads all shards.
4. **Uncompressed, single file** - `customer.tbl` (fallback).

Shard numbering is a flat integer suffix (no zero padding); any non-numeric
suffix is treated as a compression extension rather than a shard index.

### Row-Count Validation API

`benchbox.core.datavault.validate_row_counts` returns a
`DataVaultValidationReport` dataclass that summarises whether every generated
Data Vault table matches its expected row count for the given TPC-H scale
factor (`datavault/validation.py`).

```python
from pathlib import Path
from benchbox.core.datavault import validate_row_counts

report = validate_row_counts(
    data_dir=Path("datavault_data"),
    scale_factor=1.0,
    use_manifest=True,
    tolerance_pct=1.0,
)

print(report)
print(report.is_valid)
print(report.tables_passed,
      report.tables_failed,
      report.tables_validated)
report.to_dict()
```

`use_manifest=True` prefers `_datagen_manifest.json` when it is present, and `tolerance_pct=1.0` is the default variance allowance. `print(report)` gives a human-readable summary, and `report.is_valid` is `True` when every table is within tolerance. The three counts are populated by `__post_init__`. `to_dict()` returns a serializable form for JSON output.

Fields on `DataVaultValidationReport`:

| Field | Type | Description |
|-------|------|-------------|
| `scale_factor` | `float` | TPC-H scale factor used for generation |
| `results` | `list[ValidationResult]` | Per-table actual vs expected counts and variance |
| `tables_validated` | `int` | Total tables inspected (derived) |
| `tables_passed` | `int` | Tables within tolerance (derived) |
| `tables_failed` | `int` | Tables outside tolerance (derived) |
| `is_valid` | `bool` | `True` iff `tables_failed == 0` (derived) |

Lineitem-derived tables use a minimum tolerance of 1% to absorb the natural
variance of TPC-H lineitem generation.

### Scale Factor Guidelines

| Scale Factor | Source Data | Data Vault Tables | Use Case |
|-------------|-------------|-------------------|----------|
| 0.01 | ~10 MB | ~15 MB | Development |
| 0.1 | ~100 MB | ~150 MB | Integration testing |
| 1.0 | ~1 GB | ~1.5 GB | Standard benchmark |
| 10 | ~10 GB | ~15 GB | Performance testing |

## Performance Characteristics

### Join Patterns

Data Vault queries typically involve more joins than equivalent TPC-H queries due to the Hub-Link-Satellite structure. A simple TPC-H 3-table join may become a 6-8 table join in Data Vault.

**Performance Implications:**
- **Hash join heavy** - Most queries benefit from hash join optimization
- **Index candidates** - Hash key columns (hk_*) are primary join keys
- **Filter pushdown** - `load_end_dts IS NULL` filters should be pushed to satellites

### Optimization Recommendations

1. **Index hash keys** - Create indexes on all hk_* columns
2. **Partition satellites** - Consider partitioning large satellites by load_dts
3. **Materialize current views** - Pre-filter `load_end_dts IS NULL` for hot paths
4. **Statistics** - Ensure optimizer has accurate cardinality estimates for links

## Data Vault 2.0 Concepts

### Hash Keys

All surrogate keys use MD5 hashes stored as VARCHAR(32):
- **Hub hash keys**: MD5 of business key (e.g., `MD5(c_custkey)`)
- **Link hash keys**: MD5 of concatenated hub hash keys
- **HASHDIFF**: MD5 of concatenated attribute values for change detection

### Audit Columns

Every table includes:
- `load_dts` (TIMESTAMP): When the record was loaded
- `record_source` (VARCHAR): Source system identifier

Satellites additionally include:
- `load_end_dts` (TIMESTAMP, nullable): End date for historical records (NULL = current)
- `hashdiff` (VARCHAR(32)): Hash of attributes for change detection

## Comparison with TPC-H

| Aspect | TPC-H | Data Vault |
|--------|-------|------------|
| **Tables** | 8 | 21 |
| **Schema Type** | 3NF | Hub-Link-Satellite |
| **Surrogate Keys** | None | MD5 hash keys |
| **Audit Trail** | None | Full (load_dts, record_source) |
| **Historical Data** | None | Satellite versioning |
| **Join Complexity** | Lower | Higher |
| **Query Count** | 22 | 22 (adapted) |

## See Also

- [TPC-H Benchmark](tpc-h.md) - Source data and original queries
- [TPC-DS Benchmark](tpc-ds.md) - More complex decision support benchmark
- [SSB Benchmark](ssb.md) - Star schema variant of TPC-H
