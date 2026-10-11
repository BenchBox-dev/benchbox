# TPC Logical Tuning Profiles

BenchBox uses a platform-neutral logical profile for checked-in TPC-H and
TPC-DS tuned templates. The profile lives at
`benchbox/core/tuning/profiles/tpc.yaml`; typed loading, capability mapping,
and template validation live under `benchbox/core/tuning/`.

The profile is the workload evidence source. Platform YAML files are consumers
of that profile, not independent sources of truth. A tuned Databricks run and a
tuned DuckDB run can therefore be compared by asking two separate questions:

- Did both runs request the same logical workload profile?
- Which physical mechanisms did each platform use to represent that profile?

## Logical Profile

Each candidate records:

- benchmark id: `tpch` or `tpcds`
- table and column in canonical all-caps form
- SQL type
- bounded logical roles such as `temporal_partition`, `join_locality`,
  `high_selectivity_filter`, `fact_dimension_join`, `group_order_locality`, and
  `distribution_candidate`
- query evidence: count, query ids, evidence source, acceptance status, and
  rationale

Statuses:

- `existing_baseline`: part of the original checked-in tuning baseline
- `accepted`: added from query-template evidence
- `dropped_low_evidence`: intentionally excluded unless future evidence changes

Dropped candidates are part of the profile so they cannot silently reappear in a
checked-in template.

## Platform Mapping

Logical parity does not require identical physical features.

Databricks guidance:

- Liquid Clustering replaces table partitioning and ZORDER for Delta layout,
  is GA for Delta Lake on Databricks Runtime 15.2 and above, and is recommended
  for new tables.
- Manual Liquid Clustering uses `CREATE TABLE ... CLUSTER BY (...)` or
  `ALTER TABLE ... CLUSTER BY (...)`, supports up to four clustering keys, and
  Databricks recommends converting legacy partition and ZORDER columns into
  clustering keys when migrating.
- Automatic Liquid Clustering uses `CLUSTER BY AUTO`, requires Unity Catalog
  managed Delta tables with Databricks Runtime 15.4 LTS or above for intelligent
  key selection, and relies on predictive optimization and query history.
- `OPTIMIZE` groups files by Liquid keys for clustered tables. `OPTIMIZE FULL`
  can force reclustering on Databricks Runtime 16.0 and above.
- ZORDER cannot be used with Liquid Clustering.

Sources: Databricks Liquid Clustering documentation
(`https://docs.databricks.com/gcp/en/delta/clustering`), Databricks OPTIMIZE
documentation (`https://docs.databricks.com/aws/en/delta/optimize`), and
Databricks data-skipping documentation
(`https://docs.databricks.com/gcp/en/delta/data-skipping`).

| Platform | Logical roles | Physical mapping |
| --- | --- | --- |
| Databricks `databricks_z_order` | temporal locality | Delta `partitioning` |
| Databricks `databricks_z_order` | joins, filters, group/order locality | `clustering`, consumed by Delta `z_order`/`optimize` operations |
| Databricks `databricks_z_order` | distribution candidates | logical locality hints folded into `z_order`; Databricks has no user-managed distribution key |
| Databricks `databricks_liquid_auto` | all accepted TPC candidates | `liquid_clustering_auto` plus `optimize`; Databricks selects keys asynchronously from workload history |
| Databricks `databricks_liquid_manual` | partitioning, locality, and ZORDER-style candidates | `liquid_clustering` keys, limited to four keys per table |
| DuckDB | temporal locality | `partitioning` hints in unified tuning |
| DuckDB | joins, filters, group/order locality, distribution candidates | `sorting` / sorted layout |
| BigQuery | temporal locality | partitioning when column shape is supported |
| BigQuery | joins, filters, group/order locality | clustering, with the four-column limit explicit; distribution keys are unsupported |
| Redshift | distribution plus locality candidates | dist-key plus sort-key decisions; dist-key remains limited to a single key |
| Redshift | locality candidates | sort-key decisions |
| Snowflake | locality candidates | clustering keys on fact tables only, at most three per table ordered lowest to highest SF1 cardinality (date keys first, then foreign keys by referenced row count, then the table's own key) |
| Snowflake | distribution candidates | unsupported; no user-managed distribution key |

The checked-in ClickHouse templates (`examples/tunings/clickhouse/`) are not
rendered from this profile, and the validator below does not check them.

Platforms without a meaningful physical mechanism must return a structured
unsupported or waived decision. They must not be reported as fully mapped.

## Current TPC Template Matrix

| Platform | Benchmark | Template | Partitioning | Clustering | Sorting | Distribution |
| --- | --- | --- | --- | --- | --- | --- |
| Databricks | TPC-H | `examples/tunings/databricks/tpch_tuned.yaml` | `LINEITEM.L_SHIPDATE`; `ORDERS.O_ORDERDATE` | `LINEITEM.L_ORDERKEY,L_PARTKEY,L_SUPPKEY`; `ORDERS.O_ORDERKEY,O_CUSTKEY`; `PART.P_PARTKEY,P_TYPE,P_SIZE`; `SUPPLIER.S_SUPPKEY,S_NATIONKEY`; `CUSTOMER.C_CUSTKEY,C_NATIONKEY`; `PARTSUPP.PS_PARTKEY,PS_SUPPKEY` | none | `LINEITEM.L_ORDERKEY`; `ORDERS.O_ORDERKEY`; `PART.P_PARTKEY`; `SUPPLIER.S_SUPPKEY`; `CUSTOMER.C_CUSTKEY`; `PARTSUPP.PS_PARTKEY` |
| Databricks | TPC-H | `examples/tunings/databricks/tpch_liquid_tuned.yaml` | none | Liquid AUTO workload-intent columns folded from legacy partitioning, clustering, and distribution candidates | none | none |
| DuckDB | TPC-H | `examples/tunings/duckdb/tpch_tuned.yaml` | `LINEITEM.L_SHIPDATE`; `ORDERS.O_ORDERDATE` | none | `LINEITEM.L_ORDERKEY,L_LINENUMBER,L_PARTKEY,L_SUPPKEY`; `ORDERS.O_ORDERKEY,O_CUSTKEY`; `PART.P_PARTKEY,P_TYPE,P_SIZE`; `SUPPLIER.S_SUPPKEY,S_NATIONKEY`; `CUSTOMER.C_CUSTKEY,C_NATIONKEY`; `PARTSUPP.PS_PARTKEY,PS_SUPPKEY` | none |
| Databricks | TPC-DS | `examples/tunings/databricks/tpcds_tuned.yaml` | `STORE_SALES.SS_SOLD_DATE_SK`; `STORE_RETURNS.SR_RETURNED_DATE_SK`; `CATALOG_SALES.CS_SOLD_DATE_SK`; `CATALOG_RETURNS.CR_RETURNED_DATE_SK`; `WEB_SALES.WS_SOLD_DATE_SK`; `WEB_RETURNS.WR_RETURNED_DATE_SK` | `STORE_SALES.SS_ITEM_SK,SS_CUSTOMER_SK,SS_STORE_SK,SS_PROMO_SK,SS_TICKET_NUMBER`; `STORE_RETURNS.SR_ITEM_SK,SR_CUSTOMER_SK,SR_STORE_SK,SR_TICKET_NUMBER`; `CATALOG_SALES.CS_ITEM_SK,CS_SHIP_MODE_SK`; `CATALOG_RETURNS.CR_ITEM_SK`; `WEB_SALES.WS_ITEM_SK,WS_WEB_PAGE_SK,WS_WEB_SITE_SK,WS_SHIP_MODE_SK`; `WEB_RETURNS.WR_ITEM_SK`; `DATE_DIM.D_DATE_SK,D_YEAR,D_MOY`; `ITEM.I_ITEM_SK,I_CATEGORY,I_CLASS`; `CUSTOMER.C_CUSTOMER_SK,C_CURRENT_ADDR_SK,C_CURRENT_CDEMO_SK` | none | `STORE_SALES.SS_ITEM_SK`; `STORE_RETURNS.SR_ITEM_SK`; `CATALOG_SALES.CS_ITEM_SK`; `CATALOG_RETURNS.CR_ITEM_SK`; `WEB_SALES.WS_ITEM_SK`; `WEB_RETURNS.WR_ITEM_SK`; `DATE_DIM.D_DATE_SK`; `ITEM.I_ITEM_SK`; `CUSTOMER.C_CUSTOMER_SK` |
| Databricks | TPC-DS | `examples/tunings/databricks/tpcds_liquid_tuned.yaml` | none | Liquid AUTO workload-intent columns folded from legacy partitioning, clustering, and distribution candidates | none | none |
| DuckDB | TPC-DS | `examples/tunings/duckdb/tpcds_tuned.yaml` | `STORE_SALES.SS_SOLD_DATE_SK`; `STORE_RETURNS.SR_RETURNED_DATE_SK`; `CATALOG_SALES.CS_SOLD_DATE_SK`; `CATALOG_RETURNS.CR_RETURNED_DATE_SK`; `WEB_SALES.WS_SOLD_DATE_SK`; `WEB_RETURNS.WR_RETURNED_DATE_SK` | none | `STORE_SALES.SS_ITEM_SK,SS_CUSTOMER_SK,SS_STORE_SK,SS_PROMO_SK,SS_TICKET_NUMBER`; `STORE_RETURNS.SR_ITEM_SK,SR_CUSTOMER_SK,SR_STORE_SK,SR_TICKET_NUMBER`; `CATALOG_SALES.CS_ITEM_SK,CS_SHIP_MODE_SK`; `CATALOG_RETURNS.CR_ITEM_SK`; `WEB_SALES.WS_ITEM_SK,WS_WEB_PAGE_SK,WS_WEB_SITE_SK,WS_SHIP_MODE_SK`; `WEB_RETURNS.WR_ITEM_SK`; `DATE_DIM.D_DATE_SK,D_YEAR,D_MOY`; `ITEM.I_ITEM_SK,I_CATEGORY,I_CLASS`; `CUSTOMER.C_CUSTOMER_SK,C_CURRENT_ADDR_SK,C_CURRENT_CDEMO_SK` | none |

BenchBox checks the bundled Databricks and DuckDB templates against this
profile during development. To check that a tuning file you have edited is
valid, run `benchbox tuning validate`:

```bash
benchbox tuning validate examples/tunings/duckdb/tpch_tuned.yaml --platform duckdb
```

## Result Metadata

When a TPC tuned template is applied, result execution metadata includes
`tuning_profile` with:

- `logical_tuning_profile_id`
- `logical_tuning_profile_version`
- `tuning_template_hash`
- `physical_rendering_id`
- `platform_physical_tuning_mechanisms`
- `logical_profile_coverage`
- `unmapped_logical_candidates`
- `validation_status`

The companion tuning payload also includes `logical_profile`, and the platform
tuning summary includes the profile id, version, template hash, coverage, and
physical mechanisms.

Databricks platform metadata also records requested and resolved clustering
strategy plus `applied_layout_operations` and `skipped_layout_operations`.
Result consumers should use those operation lists to distinguish a requested
Liquid/Z-ORDER strategy from statements that were actually attempted and
accepted by the warehouse.

## Comparison Semantics

Treat `tuning_mode == "tuned"` as a request for a logical profile, not as proof
of identical storage layout. Databricks may represent a locality candidate with
legacy Delta Z-ORDER, Databricks Liquid AUTO, or another future rendering, while
DuckDB represents the same logical candidate through sort layout. Same logical
profile plus different `physical_rendering_id` is a cross-mechanism comparison,
not an identical-tuning cohort.

Current Databricks defaults preserve the legacy `*_tuned.yaml` Z-ORDER rendering.
Use `*_liquid_tuned.yaml` to request Liquid AUTO by passing the template as an explicit path (there is no `liquid` keyword; `--tuning tuned` only resolves `<benchmark>_tuned.yaml`):

```bash
benchbox run --platform databricks --benchmark tpch --tuning examples/tunings/databricks/tpch_liquid_tuned.yaml
```

This command works from a source checkout only: `examples/` is not installed with the package, and the packaged templates ship Z-ORDER renderings, not the Liquid variants.

Liquid templates must not enable ZORDER or carry per-table `partitioning` or
per-table `distribution` fields; partition and ZORDER-era candidates are folded
into Liquid workload intent, and automatic clustering does not prove that
Databricks selected every listed column. Explicit
`z_ordering_enabled: false` with empty `z_ordering_columns` is permitted for
schema completeness (as in `examples/tunings/databricks/tpch_liquid_tuned.yaml`).

Do not compare a fully mapped tuned template to a fallback (for example basic
constraints, or ClickHouse's "OLAP session pack") as if both are equivalent.
The fallback means BenchBox could not find a
benchmark-specific tuned template for that platform/benchmark cell; result
consumers should treat that as a coverage gap unless the cell has an explicit
waiver.
