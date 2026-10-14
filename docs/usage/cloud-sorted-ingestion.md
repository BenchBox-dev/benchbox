# Cloud Sorted Ingestion

BenchBox supports an opt-in sorted-ingestion strategy for cloud data warehouse runs so you can compare:

- Deep sorted ingestion
- Platform-native tuning (for example clustering or Z-ORDER)
- Baseline runs with no sorted-ingestion rewrite

## Strategy Controls

Use unified tuning `platform_optimizations`:

```yaml
platform_optimizations:
  sorted_ingestion_mode: force
  sorted_ingestion_method: ctas
```

`sorted_ingestion_mode`:

- `off`: disable sorted ingestion
- `auto`: enable only when the platform capability matrix supports a method
- `force`: require sorted ingestion, fail if unsupported

`sorted_ingestion_method`:

- `auto`: choose platform default
- `ctas`: use CTAS rewrite flow where available
- `z_order`: Databricks Z-ORDER path
- `liquid_clustering`: Databricks liquid clustering path
- `vacuum_sort`: Redshift vacuum sort path

## CLI Overrides

You can override strategy at runtime:

```bash
benchbox run \
  --platform snowflake \
  --benchmark tpch \
  --scale 1 \
  --tuning ./unified_tuning.yaml \
  --sorted-ingestion-mode force \
  --sorted-ingestion-method ctas
```

## Comparison Workflow

1. Run baseline:

```bash
benchbox run --platform snowflake --benchmark tpch --scale 1 --tuning notuning
```

2. Run platform-native tuning:

```bash
benchbox run --platform snowflake --benchmark tpch --scale 1 --tuning ./snowflake-native.yaml
```

3. Run deep-sorted ingestion:

```bash
benchbox run --platform snowflake --benchmark tpch --scale 1 --tuning ./snowflake-sorted.yaml
```

4. Compare result files:

```bash
benchbox compare <baseline.json> <native.json> <sorted.json>
```

## Platform Notes

- Snowflake: supports `ctas` sorted-ingestion path when mode is enabled.
- Databricks: supports `ctas`, `z_order`, and `liquid_clustering` strategy methods. TPC templates now distinguish legacy `databricks_z_order` from `databricks_liquid_auto`; do not combine Liquid Clustering with ZORDER or per-table partitioning fields.
- Redshift: supports `vacuum_sort`. The `ctas` method is accepted but runs no
  SQL: a CTAS copy would lose the distribution style, distribution key, column
  encodings and constraints, and its `ORDER BY` does not set a sort key. The run
  records the sort as not applied, with that reason.
- Amazon Athena and Azure Synapse Analytics: the `ctas` method is accepted but
  runs no SQL, and the run records the sort as not applied with the reason.
  Athena cannot rewrite a table from itself, and Synapse would need a swap that
  keeps the original distribution and columnstore index.
- BigQuery: forcing sorted ingestion currently returns an actionable error for CTAS-hook execution path.
- ClickHouse Cloud: post-load sorted ingestion is unsupported; use table `ORDER BY` at create time.

## Result Metadata

BenchBox writes sorted-ingestion metadata into `execution_metadata.sorted_ingestion`:

- Configured mode/method
- Resolved mode/method
- Platform capability details
- Applied table list/count
- Total sorted-ingestion apply time
