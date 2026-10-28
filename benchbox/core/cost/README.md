# Cost Estimation Framework

## Overview

The cost estimation framework calculates compute costs for benchmark executions across different cloud data platforms. It provides:

- **Platform-specific cost models** for Snowflake, BigQuery, Redshift, and Databricks
- **Automatic cost calculation** integrated into benchmark result export
- **Detailed cost breakdown** by phase (power test, throughput test, maintenance test)
- **Validation and error handling** for robustness

Pricing tables contain selected derived list-price values, not vendor catalog
mirrors. Per-table provenance is authoritative; entries with unknown retrieval
dates are stale-by-default and do not support normalized publication.

## Supported Platforms

| Platform | Cost Model | Pricing Basis | Accuracy |
|----------|------------|---------------|----------|
| **Snowflake** | Credit-based | Credits consumed × price per credit | ±10% |
| **BigQuery** | Per-TiB scanned | Data scanned (TiB) × price per TiB | ±10% |
| **Redshift** | Time-based | Execution time × node count × hourly rate | Marginal cost only* |
| **Databricks** | DBU-based | Execution time × DBU/hour × price per DBU | ±15% |
| **DuckDB** | Zero cost | Local execution | Exact ($0.00) |
| **ClickHouse** | Zero cost | Local execution | Exact ($0.00) |

\* See [Redshift Cost Model Clarifications](#redshift-cost-model-clarifications) below.

## Architecture

```
BenchmarkResults (from execution)
    ↓
add_cost_estimation_to_results()
    ↓
┌─────────────────────────────────────┐
│ 1. Extract platform config          │
│ 2. Validate configuration            │
│ 3. Calculate per-query costs         │
│ 4. Aggregate by phase                │
│ 5. Calculate total benchmark cost    │
└─────────────────────────────────────┘
    ↓
BenchmarkResults with cost_summary
    ↓
JSON export (schema v1.1)
```

### Key Components

1. **`integration.py`** - Main entry point, orchestrates cost calculation
2. **`calculator.py`** - Platform-specific cost calculation logic
3. **`pricing_data.yaml`** - Pricing tables with version tracking (loaded by `pricing.py`)
4. **`models.py`** - Data models (QueryCost, PhaseCost, BenchmarkCost)
5. **`storage.py`** - Storage cost estimation

## Output Schema

The cost estimation framework adds a `cost_summary` object to benchmark results with the following structure:

```json
{
  "cost_summary": {
    "total_cost": 17.60,
    "currency": "USD",
    "cost_model": "actual",
    "warnings": [
      "Storage cost estimate: $0.1534 for 4.87 TB over 1.0 hours. On-demand storage pricing..."
    ],
    "storage_cost": 0.1534,
    "phase_costs": [
      {
        "phase_name": "power_test",
        "total_cost": 5.20,
        "query_count": 22,
        "currency": "USD",
        "wall_clock_duration_seconds": 450.0,
        "concurrent_streams": 1,
        "effective_cost_per_hour": 41.60
      },
      {
        "phase_name": "throughput_test",
        "total_cost": 12.40,
        "query_count": 88,
        "currency": "USD",
        "wall_clock_duration_seconds": 300.0,
        "concurrent_streams": 4,
        "effective_cost_per_hour": 148.80
      }
    ],
    "platform_details": {
      "platform": "snowflake",
      "edition": "standard",
      "cloud": "aws",
      "region": "us-east-1",
      "pricing_version": "2026.09",
      "pricing_date": "unknown",
      "storage_estimate": {
        "storage_cost": 0.1534,
        "storage_tb": 4.87,
        "price_per_tb_month": 23.00,
        "duration_hours": 1.0,
        "note": "On-demand storage pricing. Time Travel and Fail-safe may incur additional costs."
      }
    }
  }
}
```

### Output Fields

**Top-level fields**:
- `total_cost` - Total compute cost across all phases (USD)
- `currency` - Always "USD"
- `cost_model` - Type of cost calculation:
  - `"actual"` - Represents actual cloud billing (Snowflake, BigQuery, Databricks SQL)
  - `"marginal"` - Per-query incremental cost, excludes cluster idle time (Redshift, Databricks all-purpose)
  - `"estimated"` - Rough estimate for unknown platforms
- `warnings` - Array of user-facing warnings about cost limitations
- `storage_cost` - Estimated storage cost (optional, only if data was loaded)
- `phase_costs` - Array of costs broken down by benchmark phase
- `platform_details` - Platform-specific configuration and pricing metadata

**Phase cost fields**:
- `phase_name` - "power_test", "throughput_test", or "data_maintenance"
- `total_cost` - Total cost for all queries in this phase
- `query_count` - Number of queries executed
- `wall_clock_duration_seconds` - Actual wall clock time (optional)
- `concurrent_streams` - Number of concurrent query streams (optional)
- `effective_cost_per_hour` - Computed as `total_cost / (wall_clock_duration_seconds / 3600)` (optional)

**Platform details fields**:
- `pricing_version` - Semantic version of pricing data (e.g., "2026.09")
- `pricing_date` - Last validation date in ISO 8601 format
- `storage_estimate` - Detailed storage cost breakdown (optional)

### Cost Model Types

**actual** - Cost directly tied to query execution:
- Snowflake: Credits consumed
- BigQuery: Data scanned
- Databricks SQL Warehouses: DBUs consumed
- Auto-scaling platforms where concurrent execution is properly reflected

**marginal** - Per-query incremental cost (excludes cluster idle time):
- Redshift: Execution time × cluster capacity
- Databricks All-Purpose Clusters: Execution time × cluster DBUs
- Use for query optimization and cost attribution
- **Warning**: Total marginal cost ≠ cluster TCO for concurrent queries

**estimated** - Rough estimate for unknown or unsupported platforms

### Warnings System

The framework provides user-facing warnings in the `warnings` array:

1. **Cost Model Warnings**:
   - Redshift: Explains marginal cost vs cluster TCO
   - Databricks all-purpose: Clarifies idle time exclusion

2. **Pricing Staleness Warnings**:
   - Triggered when pricing data is >90 days old
   - Includes pricing age and last update date

3. **Storage Cost Warnings**:
   - Explains estimate limitations
   - Notes platform-specific storage considerations

4. **Configuration Warnings**:
   - Missing required configuration fields
   - Incomplete platform metadata

### Timing Context

Phase costs now include timing information for better cost analysis:

```python
phase_cost = {
    "phase_name": "throughput_test",
    "total_cost": 12.40,
    "query_count": 88,
    "wall_clock_duration_seconds": 300.0,
    "concurrent_streams": 4,
    "effective_cost_per_hour": 148.80
}
```

In this example, `total_cost` is the sum of all 88 queries (4 streams × 22
queries), and the phase took 5 minutes of wall-clock time. The four streams ran
concurrently. `effective_cost_per_hour` is $12.40 / (300/3600) = $148.80/hr.

Use `effective_cost_per_hour` for:
- Budgeting and forecasting
- Comparing different scale factors
- Understanding actual spending rate

### Pricing Metadata

All cost estimates include pricing version tracking:

```python
platform_details = {
    "pricing_version": "2026.09",
    "pricing_date": "unknown",
}
```

- Pricing is validated monthly
- Staleness warning after 90 days
- Target accuracy: ±5% for major regions

`pricing_date` is `"unknown"` because `pricing_data.yaml` no longer carries a
file-level refresh date (a previous date asserted a refresh that never
occurred). The per-table provenance blocks in
[`pricing_data.yaml`](./pricing_data.yaml) — source URL, retrieved date,
method, verified regions — are authoritative instead. `pricing_version` still
tracks the table version (`YYYY.MM`).

### Storage Cost Estimation

When benchmark data is loaded, storage costs are automatically estimated:

```python
storage_estimate = {
    "storage_cost": 0.1534,
    "storage_tb": 4.87,
    "price_per_tb_month": 23.00,
    "duration_hours": 1.0,
    "note": "Platform-specific notes..."
}
```

**Limitations**:
- Estimates only, not actual charges
- Assumes standard storage tier
- Doesn't include: compression ratios, replication, snapshots
- Minimum duration: 1 hour

## Cost Calculation Models

### Snowflake

**Formula**: `credits_used × price_per_credit`, or when metered credits are
absent, `(execution_seconds / 3600) × credits_per_hour(warehouse_size) × price_per_credit`

**Resource Usage Required** (one runtime signal):
- `credits_used` - Metered warehouse credits for the query, when available.
  QUERY_HISTORY only exposes `CREDITS_USED_CLOUD_SERVICES`, which covers the
  cloud-services layer and is reported separately as
  `credits_used_cloud_services`; it is never priced as warehouse compute.
- `execution_time_seconds` / `execution_time_ms` / `total_elapsed_time_ms` -
  Measured runtime used to estimate warehouse credits. Server-side execution
  time is preferred; total elapsed includes queueing and compilation.
- `warehouse_size` (optional per query) - Observed size, falling back to the
  platform config.

The estimate is a marginal per-query cost: warehouse idle time between queries
and multi-cluster scaling are excluded.

**Configuration Required**:
- `edition` - standard | enterprise | business_critical | vps. The edition is
  not queryable from the service, so the operator must set it
  (`--platform-option edition=...`); normalized cost stays `unavailable`
  without it.
- `cloud` - aws | azure | gcp
- `region` - e.g., us-east-1, eu-west-1
- `warehouse_size` - e.g., X-Small, Medium (for estimation; read from observed
  warehouse metadata when available)

**Pricing** (list prices; the calculator reads
[`pricing_data.yaml`](./pricing_data.yaml), which is the source of truth):
- Standard: $2.00/credit (US tier), $2.50/credit (EU tier), $2.60/credit (AP tier)
- Enterprise: $3.00/credit (US tier), $3.75/credit (EU tier), $3.90/credit (AP tier)
- Business Critical: $4.00/credit (US tier), $5.00/credit (EU tier), $5.20/credit (AP tier)
- VPS: $6.00/credit (US tier), $7.50/credit (EU tier), $7.80/credit (AP tier)

Tier values are approximations: within a tier, Snowflake list prices vary by
region. Per the Snowflake Service Consumption Table (effective 2026-09-16),
Standard is $2.40 in AWS Stockholm, $2.60 in AWS Dublin/Frankfurt, Azure West
Europe/North Europe, and GCP Netherlands/Frankfurt, and $2.70 in London and GCP
London — so the previous README figure of "$2.40 EU" was Stockholm's price
mislabeled as the EU-wide rate, and the table's `eu: 2.5` splits the common
$2.40–$2.60 range. The same applies to the other tiers (e.g. Enterprise EU is
$3.60 Stockholm / $3.90 Dublin/Frankfurt / $4.00 London). For exact regions, see
the vendor table; for what the calculator charges, see `pricing_data.yaml`.

**Example**:
```python
resource_usage = {"credits_used": 0.5}
config = {"edition": "standard", "cloud": "aws", "region": "us-east-1"}
```

Cost: 0.5 credits × $2.00 per credit = $1.00

### BigQuery

**Formula**: `bytes_billed / (1024^4) × price_per_TiB`

**Resource Usage Required**:
- `bytes_billed` (preferred) OR `bytes_processed`

**Configuration Required**:
- `location` - us | eu | asia-northeast1 | etc.

**Pricing** (on-demand per-TiB rates from
[`pricing_data.yaml`](./pricing_data.yaml), the source of truth):
- US / EU / Asia multi-region: $6.25/TiB
- US single-regions: $6.25/TiB
- EU / Asia single-regions: $6.875/TiB, with published regional overrides in
  `pricing_data.yaml` (for example, Europe West 1 is $7.50/TiB).
- Australia: $7.50/TiB by bucket and $8.125/TiB for the published Australia
  regional entries; Middle East: $7.50/TiB.
- South America: $7.8125/TiB; other locations: $6.875/TiB only when the
  location is explicitly priced, otherwise the result is marked as fallback.

**Example**:
```python
resource_usage = {"bytes_billed": 1024**4}
config = {"location": "us"}
```

Cost: 1 TiB × $6.25 per TiB = $6.25

**No free-tier modeling**: BenchBox charges the on-demand list rate from byte
zero and does not model the first 1 TiB per month free tier. The credit is
monthly and account-level, so no per-run attribution is principled, and
modeling it would break cross-platform comparability. Most benchmark runs
scan under 1 TiB, so do not read a BenchBox BigQuery figure as a Google bill.

### Billing units (`NormalizedCost.billing_unit`)

Scan-priced platforms report the unit actually billed, per
<https://benchbox.dev/docs/development/adr/adr-billing-unit-tb-tib-contract.html>:

- BigQuery: `tib_scanned` (tebibyte, 2^40 bytes — vendor-confirmed).
- Athena and Synapse serverless: `tb_scanned` (decimal terabyte, 10^12
  bytes — the SI reading of the bare "TB" both vendors print; neither
  publishes a divisor).
- Other platforms: `credit` (Snowflake), `node_hour` (Redshift),
  `dbu` (Databricks), `dwu_hour` (Synapse dedicated), `cu_hour`
  (Fabric), `fbu` (Firebolt).

### Redshift

**Formula**: `(execution_time_seconds / 3600) × node_count × price_per_node_hour`

**Resource Usage Required**:
- `execution_time_seconds` (required)

**Configuration Required**:
- `node_type` - dc2.large | ra3.4xlarge | etc.
- `node_count` - Number of nodes in cluster
- `region` - AWS region

**Pricing** (on-demand, varies by region; full table in
[`pricing_data.yaml`](./pricing_data.yaml), the source of truth):
- dc2.large: $0.25/node-hour (us-east-1)
- ra3.xlplus: $1.086/node-hour
- ra3.4xlarge: $3.26/node-hour
- ra3.16xlarge: $13.04/node-hour

**Example**:
```python
resource_usage = {"execution_time_seconds": 3600}
config = {"node_type": "ra3.4xlarge", "node_count": 4, "region": "us-east-1"}
```

Cost: 1 hour × 4 nodes × $3.26 per node-hour = $13.04

### Databricks

**Formula**: `(execution_time_seconds / 3600) × cluster_size_dbu_per_hour × price_per_dbu`

**Resource Usage Required**:
- `dbu_consumed` (preferred) OR `execution_time_seconds`

**Configuration Required**:
- `cloud` - aws | azure | gcp
- `tier` - standard | premium | enterprise
- `workload_type` - all_purpose | sql_compute | serverless_sql | jobs_compute
- `cluster_size_dbu_per_hour` - Extracted from warehouse size or provided

**Warehouse Size to DBU Mapping**:
- 2X-Small: 1 DBU/hour
- X-Small: 2 DBU/hour
- Small: 4 DBU/hour
- Medium: 8 DBU/hour
- Large: 16 DBU/hour
- X-Large: 32 DBU/hour
- 2X-Large: 64 DBU/hour
- 3X-Large: 128 DBU/hour
- 4X-Large: 256 DBU/hour

**Pricing** (AWS Premium tier; full per-cloud table in
[`pricing_data.yaml`](./pricing_data.yaml), the source of truth):
- All-purpose compute: $0.55/DBU
- Jobs compute: $0.20/DBU
- SQL Classic / warehouse: $0.22/DBU
- SQL Pro (`sql_compute`): $0.55/DBU
- Serverless SQL: $0.70/DBU

**Example**:
```python
resource_usage = {"execution_time_seconds": 1800}
config = {
    "cloud": "aws",
    "tier": "premium",
    "workload_type": "sql_compute",
    "cluster_size_dbu_per_hour": 8.0,
}
```

Here 1800 seconds is 30 minutes, `sql_compute` is billed at the SQL Pro rate, and
8 DBU/hour is a Medium warehouse.

Cost: 0.5 hours × 8 DBU/hour × $0.55 per DBU = $2.20

## Concurrent Query Cost Semantics

### Understanding Cost Calculations for Concurrent Execution

The cost framework calculates **per-query marginal costs**, which sum to the **total cost incurred by all queries**. This is the correct approach for understanding the total spend on a benchmark run.

#### Sequential Execution

For sequential query execution (e.g., power test):

```
Query 1: 10 seconds → Cost A
Query 2: 15 seconds → Cost B
Query 3: 20 seconds → Cost C

Total Time: 45 seconds (wall clock)
Total Cost: A + B + C
```

The wall clock time and total cost align - you pay for the sum of query execution times.

#### Concurrent Execution

For concurrent query execution (e.g., throughput test with multiple streams):

```
Stream 1: Query 1 (10s) → Cost A
Stream 2: Query 2 (15s) → Cost B  } Running concurrently
Stream 3: Query 3 (20s) → Cost C

Total Time: 20 seconds (wall clock - longest query)
Total Cost: A + B + C (sum of all queries)
```

**Key Points**:

1. **Total Cost = Sum of Individual Query Costs**
   - This is accurate for understanding total spend
   - Each query consumes resources independently

2. **Wall Clock Time ≠ Sum of Query Times**
   - Concurrent queries overlap in execution time
   - Wall clock time is shorter than sum of individual times
   - This is the expected behavior for parallel execution

3. **Cost Per Hour Calculation**
   - If you want "cost per hour of wall clock time", divide total cost by wall clock duration
   - This shows the effective rate during concurrent execution periods
   - Example: $10 total cost in 20 seconds wall clock = $1800/hour effective rate

#### Platform-Specific Behavior

**Snowflake**:
- Metered `credits_used`: Snowflake attributes shared-warehouse cost across
  concurrent queries exactly — ✅ Accurate representation
- Runtime-estimated queries: each estimate prices exclusive warehouse use, so
  concurrent streams sharing one warehouse sum above the warehouse's
  wall-clock spend — ⚠️ disclosed in the bundle warnings; sequential runs
  are exact

**BigQuery**:
- Charged per TiB scanned
- Concurrent queries scan data independently
- Total cost = sum of data scanned × price per TiB
- ✅ Accurate representation

**Redshift** (dedicated cluster):
- Cluster runs continuously
- Per-query costs show **marginal cost** (incremental cost of running that query)
- **Total cluster cost** includes idle time not captured here
- ⚠️ See [Redshift Cost Model Clarifications](#redshift-cost-model-clarifications)

**Databricks**:
- DBUs consumed based on execution time
- Concurrent queries consume DBUs in parallel
- Warehouse auto-scales to handle concurrency
- Total cost = sum of DBU consumption
- ✅ Accurate for SQL warehouses
- ⚠️ For all-purpose clusters, see note below

#### Important Caveats

**1. Databricks All-Purpose Clusters**:
- Cluster runs continuously (like Redshift)
- Concurrent queries don't multiply cluster cost
- Our calculation shows **per-query resource attribution**
- For accurate cluster TCO, see dedicated cluster note below

**2. Dedicated Clusters (Redshift, Databricks All-Purpose)**:
- Framework calculates **marginal cost per query**
- Does **not** include cluster idle time
- Total cluster cost = cluster runtime × hourly rate
- Use these metrics for:
  - ✅ Query cost attribution
  - ✅ Workload cost comparison
  - ✅ Query optimization ROI
  - ❌ Total cluster TCO (need to add idle time)

**3. Auto-Scaling Platforms (BigQuery, Snowflake, Databricks SQL)**:
- Cost directly tied to query execution
- No idle cluster time
- Concurrent execution properly reflected in cost
- ✅ Our calculations represent true total cost

### Example: TPC-H Throughput Test

**Scenario**: 4 concurrent streams, each running 22 queries

**Snowflake** (X-Small warehouse, 2 DBU/hour equivalent):
```
Each query: ~0.1 credits
Total queries: 88 (4 streams × 22 queries)
Wall clock: 300 seconds (5 minutes)

Total cost: 88 × 0.1 credits × $2.00 = $17.60
Cost per query: $0.20
Cost per hour: $17.60 / (300s / 3600s) = $211.20/hour
```

**Redshift** (ra3.4xlarge, 4 nodes):
```
Each query: ~15 seconds
Total queries: 88
Wall clock: 45 seconds (queries complete concurrently)

Marginal cost: 88 × (15s / 3600s) × 4 nodes × $3.26 = $4.77
Cluster cost (5 min): 4 nodes × (300s / 3600s) × $3.26 = $1.09
Note: Marginal > Cluster because concurrent queries overlap
```

This discrepancy highlights why we call Redshift costs "marginal" - they represent the incremental cost if queries were run sequentially, not the actual cluster cost during concurrent execution.

### Best Practices

1. **For Query Optimization**:
   - Use per-query costs to identify expensive queries
   - Compare costs before/after optimizations
   - Track cost trends over time

2. **For Budget Planning**:
   - Auto-scaling platforms: Use our total cost
   - Dedicated clusters: Add cluster runtime × hourly rate
   - Consider concurrency patterns in your workload

3. **For Reporting**:
   - Report "Total Compute Cost" for all platforms
   - For dedicated clusters, add note about cluster runtime
   - Show "Cost per Query" and "Effective Cost per Hour" as supplementary metrics

4. **For Comparisons**:
   - Compare like-for-like: same concurrency, same scale
   - Normalize by query count or wall clock time
   - Consider platform auto-scaling behavior

## Redshift Cost Model Clarifications

### What We Calculate

The framework calculates **per-query marginal cost**:
- Cost = execution_time × node_count × hourly_rate
- Represents incremental cost of running that specific query
- Useful for query optimization and cost attribution

### What We Don't Calculate

**Cluster Total Cost of Ownership (TCO)**:
- Full cluster runtime (24/7 for on-demand, or reservation period)
- Cluster idle time between queries
- Cluster startup/shutdown overhead
- Reserved instance discounts

### When to Use This Cost Model

✅ **Good for**:
- Query performance optimization (cost reduction correlates with time reduction)
- Comparing query costs within same cluster configuration
- Understanding which queries are most expensive
- Benchmarking query cost trends

❌ **Not suitable for**:
- Total cluster cost estimation (need cluster uptime)
- Cross-platform cost comparisons (Redshift requires dedicated cluster)
- Budget planning (need to add cluster base cost)

### Example Clarification

**Scenario**: Run TPC-H power test (22 queries) on ra3.4xlarge (4 nodes) in us-east-1

**Our Calculation** (marginal cost):
```
Query 1: 30s → 30/3600 × 4 × $3.26 = $0.11
Query 2: 45s → 45/3600 × 4 × $3.26 = $0.16
...
Total: $5.50 (example sum of all queries)
```

**Actual Cluster Cost** (if cluster runs for 1 hour):
```
Cluster runtime: 1 hour
Total cost: 4 nodes × 1 hour × $3.26 = $13.04
```

**Interpretation**:
- $5.50 = cost attributed to benchmark queries
- $7.54 = cluster idle time (1 hour - total query time)
- $13.04 = total cluster cost for that hour

### Future Enhancement

For dedicated clusters, we could add:
- `cluster_uptime_seconds` parameter
- Full cluster TCO calculation
- Idle vs. active cost breakdown

This would provide complete cost picture for Redshift and Databricks all-purpose clusters.

## Usage

### Automatic Cost Estimation

Cost estimation is automatically added during result export:

```python
from benchbox.core.results.exporter import ResultExporter

exporter = ResultExporter()
exporter.export_result(benchmark_results, output_file)
```

### Manual Cost Estimation

You can also manually add cost estimation:

```python
from benchbox.core.cost.integration import add_cost_estimation_to_results

updated_results = add_cost_estimation_to_results(benchmark_results)

platform_config = {
    "edition": "enterprise",
    "cloud": "azure",
    "region": "eu-west-1",
}
updated_results = add_cost_estimation_to_results(
    benchmark_results,
    platform_config=platform_config
)
```

### Configuration Override

Override platform configuration for what-if scenarios:

```python
snowflake_config = {
    "edition": "standard",
    "cloud": "aws",
    "region": "us-east-1",
    "warehouse_size": "X-LARGE",
}

databricks_config = {
    "cloud": "aws",
    "tier": "enterprise",
    "workload_type": "sql_compute",
    "cluster_size_dbu_per_hour": 16.0,
}
```

## Output Schema

Cost information is included in the JSON export (schema v1.1). Each query's
`cost` is its cost in USD:

```json
{
  "schema_version": "1.1",
  "results": {
    "queries": {
      "details": [
        {
          "id": "Q1",
          "execution_time": 1.5,
          "cost": 0.25,
          "resource_usage": {
            "credits_used": 0.125
          }
        }
      ]
    }
  },
  "cost_summary": {
    "total_cost": 10.50,
    "currency": "USD",
    "phase_costs": [
      {
        "phase_name": "power_test",
        "total_cost": 6.30,
        "query_count": 22,
        "currency": "USD"
      },
      {
        "phase_name": "throughput_test",
        "total_cost": 4.20,
        "query_count": 88,
        "currency": "USD"
      }
    ],
    "platform_details": {
      "platform": "snowflake",
      "region": "us-east-1",
      "warehouse_size": "LARGE",
      "edition": "standard"
    }
  }
}
```

## Validation

### Resource Usage Validation

Platform adapters must populate `resource_usage` with required fields. The
blocks below show Snowflake, BigQuery, Redshift and Databricks, in that order:

- Snowflake: `credits_used` is required. `bytes_scanned` is optional.
- BigQuery: `bytes_billed` is required (`bytes_processed` is accepted instead).
- Redshift: `execution_time_seconds` is required.
- Databricks: `execution_time_seconds` is required (`dbu_consumed` is accepted
  instead).

```python
resource_usage = {
    "credits_used": 0.5,
    "bytes_scanned": 1024000,
}

resource_usage = {
    "bytes_billed": 1024**4,
}

resource_usage = {
    "execution_time_seconds": 3600,
}

resource_usage = {
    "execution_time_seconds": 1800,
}
```

Validation automatically logs warnings for missing required fields.

### Platform Configuration Validation

Configuration is validated before cost calculation. Each block below omits a
required field, and validation warns about it. They are, in order:

- Snowflake requires `edition`, `cloud` and `region`. The block is missing
  `region`.
- BigQuery requires `location`. The block is empty, so `location` is missing.
- Redshift requires `node_type`, `node_count` and `region`. The block is
  missing `region`.
- Databricks requires `cloud`, `tier`, `workload_type` and
  `cluster_size_dbu_per_hour`. The block is missing `workload_type` and
  `cluster_size_dbu_per_hour`.

```python
config = {
    "edition": "standard",
    "cloud": "aws",
}

config = {
}

config = {
    "node_type": "ra3.4xlarge",
    "node_count": 4,
}

config = {
    "cloud": "aws",
    "tier": "premium",
}
```

## Error Handling

The framework gracefully handles errors:

1. **Missing platform**: Skips cost estimation
2. **Missing resource_usage**: Returns `None` for that query
3. **Invalid configuration**: Logs warnings, attempts calculation with available data
4. **Calculation errors**: Logged with full context, doesn't fail benchmark

All errors are logged with platform/phase context for debugging.

## Limitations and Assumptions

### What's Included
- ✅ Compute costs (query execution)
- ✅ Platform-specific pricing models
- ✅ Regional pricing variations

### What's Excluded
- ❌ Storage costs
- ❌ Data transfer/network costs
- ❌ Enterprise/volume discounts
- ❌ Reserved capacity pricing
- ❌ Idle cluster time (for dedicated clusters)

### Assumptions
1. **List Prices**: All pricing based on public list prices (see the per-table provenance blocks in `pricing_data.yaml` for retrieval dates)
2. **USD Only**: No currency conversion
3. **On-Demand**: No reserved instance or savings plan discounts
4. **Compute Only**: Storage, network, and other costs not included
5. **Marginal Cost**: Dedicated clusters (Redshift, Databricks all-purpose) show per-query marginal cost, not total cluster TCO
6. **No free tiers**: List rate from byte zero, including BigQuery's first 1 TiB/month (see BigQuery section)
7. **Decimal TB for "TB"-printed scan pricing**: Athena and Synapse serverless divide by 10^12 bytes per the billing-unit ADR; BigQuery divides by 2^40

## Pricing Data Maintenance

Pricing tables live in [`pricing_data.yaml`](./pricing_data.yaml) (loaded by
`pricing.py`); it is the single source of truth for every rate the calculator
charges and every rate this document quotes:

- Source: Public cloud provider pricing pages (per-table provenance blocks in
  the YAML record source URL, retrieved date, method, and verified regions)
- Update frequency: Review quarterly or on major pricing changes

To update pricing:
1. Review cloud provider pricing pages
2. Update the tables in `pricing_data.yaml` (and their provenance blocks)
3. Run tests to ensure calculations still work
4. Update this documentation if pricing models change — or better, replace
   restated constants with a pointer to the YAML table so figures cannot drift
   silently again

### Cost model version in stored results

Result bundles embed `cost_model_version` (currently `"2026.09"`, matching the
`pricing_data.yaml` metadata version). The stamp records which table produced
the estimate; nothing cross-checks it. Decision: when the pricing version is
bumped, historical bundles keep the version stamped at export time — no
backfill or migration. Old bundles will read as soft-stale against the new
table, which is drift rather than breakage, and rewriting history would destroy
the audit trail of what rate actually produced each estimate.

## Testing

The framework has comprehensive test coverage:

The first command runs all cost framework tests. The rest run one suite each:
`test_calculator.py` covers cost calculations, `test_integration.py` covers
integration, `test_validation.py` covers resource usage validation, and
`test_config_validation.py` covers configuration validation.

```bash
pytest tests/unit/core/cost/

pytest tests/unit/core/cost/test_calculator.py
pytest tests/unit/core/cost/test_integration.py
pytest tests/unit/core/cost/test_validation.py
pytest tests/unit/core/cost/test_config_validation.py
```

Test coverage includes:
- Platform-specific cost calculations
- Edge cases (missing data, malformed inputs)
- Validation logic
- Configuration override
- Error handling
- Schema completeness

## Future Enhancements

Potential improvements for future versions:

1. **Currency Support**: Add multi-currency support with conversion rates
2. **Storage Costs**: Model storage costs for data retention
3. **Network Costs**: Include data transfer and egress charges
4. **Discount Models**: Support for enterprise agreements and reserved capacity
5. **Cost Forecasting**: Predict costs for different scale factors
6. **Cost Optimization**: Suggest configuration changes to reduce costs
7. **Cluster TCO**: Full cost modeling for dedicated clusters including idle time

## See Also

- [Cost Models Documentation](./models.py) - Data structures
- [Pricing Tables](./pricing_data.yaml) - Platform pricing data
- [Calculator Implementation](./calculator.py) - Cost calculation logic
- [Integration Layer](./integration.py) - Result integration
- [Test Suite](../../../tests/unit/core/cost/) - Comprehensive tests
