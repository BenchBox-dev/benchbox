<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# SingleStore Platform

```{tags} intermediate, guide, singlestore, sql-platform, distributed, olap, htap, cloud
```

SingleStore (formerly MemSQL) is a distributed SQL database designed for real-time analytics and transactions. It offers a unified architecture combining row-store for fast writes and columnstore for analytical queries, with MySQL wire protocol compatibility. BenchBox connects via the MySQL protocol using the official `singlestoredb` Python SDK and uses `LOAD DATA LOCAL INFILE` for high-throughput data ingestion. BenchBox translates queries with the SQLGlot `singlestore` dialect, which writes `:>` casts and `NULLS FIRST` / `NULLS LAST` natively.

SingleStore is deployed as both a fully managed cloud service (Helios) and as a self-managed cluster. It is used in production for financial services, gaming, retail analytics, and real-time operational dashboards.

## Features

- **MySQL wire protocol** - Standard MySQL connectivity via singlestoredb SDK (port 3306)
- **LOAD DATA LOCAL INFILE** - High-throughput bulk loading (>100K rows/sec)
- **Columnstore analytics** - Columnar storage for dramatic TPC query speedups
- **Shard key distribution** - Automatic data distribution across leaf nodes
- **Reference tables** - Fully replicated small dimension tables (nation, region) to eliminate broadcast joins
- **Full TPC-H support** - All 22 queries with row count validation
- **Full TPC-DS support** - All 99 queries with row count validation
- **Helios + self-managed** - Connects to SingleStore Helios or an on-premises cluster by host. BenchBox does not select or record the deployment, so Helios and self-managed runs share one identity

## Quick Start

The commands install the `singlestoredb` dependency (or the SingleStore extra), configure the connection (SingleStore
must be running), and run the TPC-H benchmark.

```bash
uv add singlestoredb

uv add benchbox --extra singlestore

export SINGLESTORE_HOST=localhost
export SINGLESTORE_PORT=3306
export SINGLESTORE_USER=root
export SINGLESTORE_PASSWORD=your_password

benchbox run --platform singlestore --benchmark tpch --scale 0.01
```

### Docker Quick Start (Self-Managed)

The commands start SingleStore with Docker, verify connectivity, and run the benchmark.

```bash
docker run -d --name singlestoredb \
    -e ROOT_PASSWORD="your_password" \
    -p 3306:3306 \
    ghcr.io/singlestore-labs/singlestoredb-dev:latest

mysql -h 127.0.0.1 -P 3306 -u root -p -e "SELECT @@memsql_version"

benchbox run --platform singlestore --benchmark tpch --scale 0.01
```

### Helios (Cloud) Connection

Set the Helios endpoint from your workspace connection string.

```bash
export SINGLESTORE_HOST=xyz123.singlestore.com
export SINGLESTORE_PORT=3306
export SINGLESTORE_USER=admin
export SINGLESTORE_PASSWORD=your_helios_password
export SINGLESTORE_DATABASE=benchbox

benchbox run --platform singlestore --benchmark tpch --scale 1
```

## Connection Configuration

| Parameter | Environment Variable | Default | Description |
|-----------|---------------------|---------|-------------|
| Host | `SINGLESTORE_HOST` | `localhost` | SingleStore node or Helios endpoint |
| Port | `SINGLESTORE_PORT` | `3306` | MySQL protocol port |
| Username | `SINGLESTORE_USER` or `SINGLESTORE_USERNAME` | `root` | Database user |
| Password | `SINGLESTORE_PASSWORD` | *(empty)* | Database password |
| Database | `SINGLESTORE_DATABASE` | *(auto-generated)* | Target database |

## Credential Validation

`benchbox setup --platform singlestore` saves the entered connection settings
even when validation fails. BenchBox marks those credentials invalid and reports
the validation error so that you can correct the server or network problem and
then retry without entering every value again:

```bash
benchbox setup --platform singlestore --validate-only
```

## Columnstore Tables

BenchBox creates all benchmark fact tables as columnstore tables for optimal analytical performance. Columnstore tables use columnar storage on leaf nodes, dramatically improving scan and aggregation performance compared to the default rowstore.

**Shard Keys** - Each fact table has a shard key on its primary join column, distributing rows evenly across leaf nodes and co-locating related rows for efficient distributed joins:

| Table | Shard Key |
|-------|-----------|
| `lineitem` | `l_orderkey` |
| `orders` | `o_orderkey` |
| `customer` | `c_custkey` |
| `part` | `p_partkey` |
| `supplier` | `s_suppkey` |
| `partsupp` | `ps_partkey` |

**Reference Tables** - Small dimension tables (`nation`, `region`) are created as `REFERENCE TABLE` to replicate them to every leaf node, eliminating broadcast joins.

## Bulk Loading

BenchBox uses `LOAD DATA LOCAL INFILE` for bulk data loading, which achieves >100K rows/sec throughput. TPC-format files (with trailing field separator) are pre-processed to remove trailing delimiters before loading.

The `singlestoredb` connection is created with `local_infile=True` to enable local file ingestion.

## Benchmarks

| Benchmark | Status | Notes |
|-----------|--------|-------|
| TPC-H | ✅ Supported | All 22 queries, optimized shard/sort keys per table |
| TPC-DS | ✅ Supported | All 99 queries, random shard distribution (no custom keys) |
| SSB | ✅ Supported | Star Schema Benchmark, random shard distribution |
| ClickBench | ✅ Supported | Web analytics benchmark, random shard distribution |

## Dependencies

```
singlestoredb>=1.0.0
```

Install via:
```bash
uv add benchbox --extra singlestore
```

## Troubleshooting

**`ImportError: singlestoredb not available`** - Install the dependency:
```bash
uv add singlestoredb
```

**Connection refused on port 3306** - Verify SingleStore is running and the host/port are correct:
```bash
mysql -h $SINGLESTORE_HOST -P $SINGLESTORE_PORT -u $SINGLESTORE_USER -p -e "SELECT 1"
```

**`LOAD DATA LOCAL INFILE` disabled** - Ensure the server has `local_infile` enabled:
```sql
SET GLOBAL local_infile = ON;
```

**Helios connection issues** - Confirm your workspace is active in the SingleStore portal and copy the exact hostname from the connection string shown in the portal.

## References

- [SingleStore Documentation](https://docs.singlestore.com/)
- [singlestoredb Python SDK](https://github.com/singlestore-labs/singlestoredb-python)
- [SingleStore Helios](https://www.singlestore.com/product-overview/)
