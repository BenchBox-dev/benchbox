<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# TPC-DI Benchmark

```{tags} intermediate, concept, tpc-di
```

> **CLI name:** `tpcdi` - use `benchbox run --benchmark tpcdi`

## Overview

The TPC-DI (Transaction Processing Performance Council - Data Integration) benchmark is designed to test data integration and ETL (Extract, Transform, Load) processes used in data warehousing scenarios. TPC-DI simulates a financial services environment with customer data, account information, and trading activities, focusing on the challenges of transforming and loading data from multiple source systems into a data warehouse.

Unlike other TPC benchmarks that primarily test query performance, TPC-DI evaluates the entire data integration process, including data transformation, data quality validation, slowly changing dimensions (SCD), and the loading of both historical and incremental data.

## Key Features

- **Data Integration focus** - Tests ETL processes rather than just query performance
- **Financial services domain** - Models trading, customer, and company data
- **Slowly Changing Dimensions** - Tests SCD Type 1 and Type 2 implementations
- **Data quality validation** - Includes systematic data validation queries
- **Multiple data sources** - Simulates various source file formats (CSV, XML, fixed-width)
- **Historical data loading** - Tests both full loads and incremental updates
- **Complex transformations** - Business rule implementations and data cleansing
- **Audit and lineage tracking** - Data governance and audit trail capabilities

## Schema Description

The TPC-DI schema models a financial services data warehouse with dimension tables and fact tables that track customer trading activities. The schema implements slowly changing dimensions and includes audit fields for data lineage tracking.

### Dimension Tables

| Table | Purpose | SCD Type | Approximate Rows (SF 1) |
|-------|---------|----------|-------------------------|
| **DimCustomer** | Customer master data | Type 2 | 5,000 |
| **DimAccount** | Customer trading accounts | Type 2 | 5,000 |
| **DimSecurity** | Financial instruments/stocks | Type 2 | 6,850 |
| **DimCompany** | Public companies | Type 2 | 5,000 |
| **DimBroker** | Financial brokers | Type 1 | 100 |
| **DimDate** | Calendar dimension | None | 2,557 |
| **DimTime** | Time of day dimension | None | 86,400 |

### Fact Tables

| Table | Purpose | Approximate Rows (SF 1) |
|-------|---------|-------------------------|
| **FactTrade** | Trading transactions | 230,000 |
| **FactCashBalances** | Account cash positions | 46,000 |
| **FactHoldings** | Security holdings | 460,000 |
| **FactMarketHistory** | Daily security prices | 1,095,000 |
| **FactWatches** | Customer watch lists | 11,500 |

### Key Schema Features

**Slowly Changing Dimensions:**
- **Type 1 (Overwrite)**: DimBroker - Simple attribute updates
- **Type 2 (History)**: DimCustomer, DimAccount, DimSecurity, DimCompany
  - `IsCurrent` flag for active records
  - `EffectiveDate` and `EndDate` for temporal validity
  - `BatchID` for data lineage tracking

**Audit Fields:**
- `BatchID`: Identifies the ETL batch that created/updated the record
- `EffectiveDate`: When the record became effective
- `EndDate`: When the record was superseded (NULL for current records)
- `IsCurrent`: Boolean flag indicating the current version

### Schema Relationships

```{mermaid}
erDiagram
    DimDate ||--o{ FactTrade : trade_date
    DimTime ||--o{ FactTrade : trade_time
    DimCustomer ||--o{ DimAccount : owns
    DimCustomer ||--o{ FactTrade : customer
    DimAccount ||--o{ FactTrade : account
    DimSecurity ||--o{ FactTrade : security
    DimCompany ||--o{ DimSecurity : issues
    DimBroker ||--o{ FactTrade : broker
    DimSecurity ||--o{ FactMarketHistory : security
    DimCustomer ||--o{ FactWatches : customer
    DimSecurity ||--o{ FactWatches : security
    DimAccount ||--o{ FactCashBalances : account
    DimAccount ||--o{ FactHoldings : account
    DimSecurity ||--o{ FactHoldings : security
```

## FinWire Source Format

TPC-DI's FinWire feed is a proprietary **fixed-width flat file** format that
encodes several record types with distinct layouts. BenchBox parses it in
`benchbox/core/tpcdi/etl/finwire_processor.py`, which recognises four record
types on the single file:

| Record | Purpose |
|--------|---------|
| **CMP** | Company fundamental data (earnings, market cap, S&P rating) |
| **SEC** | Security master data (symbol changes, splits, dividends) |
| **FIN** | Daily OHLC market data plus volume and value |
| **NEWS** | Financial news headlines, analyst ratings, sentiment |

Each record type has a fixed column layout; the processor dispatches on the
record-type prefix and materialises typed `FinWireRecord` rows for
downstream ETL. The FinWire parser itself is pure stdlib (no pandas), but
**pandas is a hard requirement** for the broader TPC-DI ETL pipeline
(`loader.py`, `etl/backend.py`, `etl/scd_processor.py`, etc.), so the
benchmark cannot run without it.

## ETL Process Overview

The TPC-DI benchmark focuses on the complete ETL pipeline rather than just final query performance:

### 1. Source Data Extraction
- **Customer files**: CSV format with demographic and account data
- **Daily Market files**: Fixed-width format with security prices
- **Trade files**: XML format with trading transaction details
- **News articles**: Text files for sentiment analysis
- **Reference data**: Industry classifications, status codes

### 2. Data Transformation
- **Data cleansing**: Handle missing values, format standardization
- **Business rules**: Apply financial industry business logic
- **Data validation**: Referential integrity and business rule validation
- **Derived attributes**: Calculate customer tier, account status changes
- **Slowly changing dimensions**: Track historical changes properly

### 3. Data Loading
- **Dimensional loading**: Load dimension tables with SCD logic
- **Fact loading**: Load fact tables with proper foreign key resolution
- **Incremental processing**: Handle updates and new records efficiently
- **Audit trail**: Maintain data lineage and processing metadata

## Query Characteristics

TPC-DI ships **38 queries** that exercise the integration output: 8 base
queries (in `benchbox/core/tpcdi/queries.py`) plus 30 extended queries
spread across `query_validation.py` (12), `query_analytics.py` (10), and
`query_etl.py` (8).

```{tip}
Each query has a [query template](queries/tpcdi/index.md) with a representative
SQL rendering, plus how to extract the exact statement for your platform and
scale.
```

### Base Validation Queries (V1-V3)

| Query | Purpose | Validation Focus |
|-------|---------|------------------|
| **[V1](queries/tpcdi/v1.md)** | Customer Dimension Validation | SCD Type 2 implementation, `IsCurrent` flag counts on `DimCustomer` |
| **[V2](queries/tpcdi/v2.md)** | Account Dimension Validation | Status tracking, current-flag counts on `DimAccount` |
| **[V3](queries/tpcdi/v3.md)** | Trade Fact Validation | Aggregate quality check on `FactTrade` (counts, totals, fees) |

### Base Analytical Queries (A1-A5)

| Query | Purpose | Business Value |
|-------|---------|----------------|
| **[A1](queries/tpcdi/a1.md)** | Customer Trading Analysis | Customer segmentation by trading activity |
| **[A2](queries/tpcdi/a2.md)** | Company Performance Analysis | Security performance by industry/rating |
| **[A3](queries/tpcdi/a3.md)** | Broker Commission Analysis | Broker performance and commission tracking |
| **[A4](queries/tpcdi/a4.md)** | Portfolio Analysis | Customer portfolio composition and risk |
| **[A5](queries/tpcdi/a5.md)** | Market Trend Analysis | Historical price trends and volatility |

### Extended Data Quality Queries (VQ1-VQ12)

12 validation queries covering referential integrity, completeness, SCD
Type 2 chain checks, cross-dimension consistency, and business-rule
compliance. See `benchbox/core/tpcdi/query_validation.py`.

### Extended Analytical Queries (AQ1-AQ10)

10 analytical queries covering customer profitability, security
performance, broker analysis, market trends, and portfolio analysis.
See `benchbox/core/tpcdi/query_analytics.py`.

### ETL Validation Queries (EQ1-EQ8)

8 queries covering batch processing, incremental loads, transformation
correctness, and quality scoring. See `benchbox/core/tpcdi/query_etl.py`.

### SCD Type 2 Validation

The data-quality validator (`benchbox/core/tpcdi/validation.py`) treats
Slowly Changing Dimension Type 2 correctness as a dedicated rule category
(`category="scd_type2"`). Four dimensions carry the SCD Type 2 trio of
columns - `IsCurrent` (BOOLEAN), `EffectiveDate` (DATE), and `EndDate`
(DATE) - defined in `benchbox/core/tpcdi/schema.py`:

- `DimCustomer`
- `DimAccount`
- `DimSecurity`
- `DimCompany`

The validator enforces the **"exactly one current row per business key"**
invariant by scanning for duplicate `IsCurrent = TRUE` rows:

| Rule | Table | Invariant |
|------|-------|-----------|
| `Customer SCD Type 2 Integrity` | `DimCustomer` | `GROUP BY CustomerID HAVING COUNT(*) > 1 WHERE IsCurrent = TRUE` returns zero rows |
| `Account SCD Type 2 Integrity` | `DimAccount` | `GROUP BY AccountID HAVING COUNT(*) > 1 WHERE IsCurrent = TRUE` returns zero rows |

`DimSecurity` and `DimCompany` carry the same SCD Type 2 columns but do
not yet have dedicated validation rules registered; register custom rules
via `TPCDIValidator.add_validation_rule()` if you need to cover them.
The validator does not (currently) assert date-chain continuity between
`EffectiveDate` and `EndDate` - extend `validation.py` if you need that
level of rigour.

## Usage Examples

### Basic Benchmark Setup

```python
from benchbox import TPCDI

tpcdi = TPCDI(scale_factor=1.0, output_dir="tpcdi_data")

data_files = tpcdi.generate_data()

validation_queries = tpcdi.get_queries()
print(f"Generated {len(validation_queries)} validation queries")

customer_validation = tpcdi.get_query("V1")
print(customer_validation)
```

### ETL Process Simulation

```python
import duckdb
from benchbox import TPCDI

tpcdi = TPCDI(scale_factor=1.0, output_dir="tpcdi_etl")

source_data = tpcdi.generate_source_data()

conn = duckdb.connect(":memory:")
schema_sql = tpcdi.get_create_tables_sql()
conn.execute("BEGIN TRANSACTION")
for statement in schema_sql.split(';'):
    if statement.strip():
        conn.execute(statement)
conn.execute("COMMIT")

transformation_results = tpcdi.run_etl_pipeline(
    connection=conn,
    batch_type="historical",
    validate_data=True,
)

validation_results = {}
for query_id in ["V1", "V2", "V3"]:
    query_sql = tpcdi.get_query(query_id)
    rows = conn.execute(query_sql).fetchall()
    validation_results[query_id] = "PASSED" if rows else "EMPTY"
```

This simulates the ETL process: source files are generated, the pipeline loads them, and the validation queries execute against the loaded warehouse.

### DuckDB Integration Example

```python
import duckdb
from benchbox import TPCDI

tpcdi = TPCDI(scale_factor=0.1, output_dir="tpcdi_small")
data_files = tpcdi.generate_data()

conn = duckdb.connect("tpcdi.duckdb")
schema_sql = tpcdi.get_create_tables_sql()
conn.execute("BEGIN TRANSACTION")
for statement in schema_sql.split(';'):
    if statement.strip():
        conn.execute(statement)
conn.execute("COMMIT")

from pathlib import Path
for table_file in data_files:
    table_name = Path(table_file).stem
    conn.execute(f"""
        INSERT INTO {table_name}
        SELECT * FROM read_csv('{table_file}', delim='|', header=false)
    """)
    print(f"Loaded {table_name}")

validation_queries = ["V1", "V2", "V3"]
for query_id in validation_queries:
    query_sql = tpcdi.get_query(query_id)
    result = conn.execute(query_sql).fetchall()
    print(f"Validation {query_id}: {len(result)} validation records")

analytical_queries = ["A1", "A2"]
for query_id in analytical_queries:
    query_sql = tpcdi.get_query(query_id)
    result = conn.execute(query_sql).fetchall()
    print(f"Analysis {query_id}: {len(result)} analysis records")
```

### SCD Type 2 Implementation Example

```python
def process_customer_scd_type2(new_customer_data, existing_dim_customer):
    batch_id = 1001
    effective_date = "2023-06-01"

    scd_logic = """
    -- Close existing records that have changed
    UPDATE DimCustomer
    SET IsCurrent = 0,
        EndDate = '{effective_date}'
    WHERE CustomerID IN (
        SELECT CustomerID
        FROM new_customer_data n
        JOIN DimCustomer d ON n.CustomerID = d.CustomerID
        WHERE d.IsCurrent = 1
        AND (n.LastName != d.LastName
             OR n.FirstName != d.FirstName
             OR n.Address != d.Address)
    );

    -- Insert new records for changed customers
    INSERT INTO DimCustomer (
        CustomerID, TaxID, Status, LastName, FirstName,
        Address, City, StateProv, Country,
        IsCurrent, BatchID, EffectiveDate, EndDate
    )
    SELECT
        n.CustomerID, n.TaxID, n.Status, n.LastName, n.FirstName,
        n.Address, n.City, n.StateProv, n.Country,
        1 as IsCurrent,
        {batch_id} as BatchID,
        '{effective_date}' as EffectiveDate,
        NULL as EndDate
    FROM new_customer_data n
    WHERE CustomerID IN (
        -- Changed customers
        SELECT CustomerID FROM updated_customers
        UNION
        -- New customers
        SELECT CustomerID FROM new_customers
    );
    """.format(
        effective_date=effective_date,
        batch_id=batch_id
    )

    return scd_logic
```

This is a simplified example of the transformation logic for SCD Type 2 processing of the customer dimension. The first statement closes existing records that have changed. The second inserts new records for changed customers and new customers.

`EnhancedSCDType2Processor.process_scd_changes` (`benchbox/core/tpcdi/etl/scd_processor.py`) applies this rule to DimAccount, DimBroker, DimCompany, DimCustomer and DimSecurity through a DB-API connection that accepts `?` placeholders (DuckDB, SQLite). For each natural key whose Type 2 columns changed it sets `IsCurrent = 0` and `EndDate` to the new `EffectiveDate` on the current row, then inserts the new version with `IsCurrent = 1` and `EndDate = 9999-12-31`, as TPC-DI clause 4.4.1.3 requires. Rows whose only changes are non-SCD columns have those columns overwritten in place (Type 1 behaviour) and their `BatchID` updated. All statements run in one transaction with bound parameters, and the returned counts are the rows the database reports as affected. Table names must be one of the five history-tracking dimensions and column names must be plain identifiers; anything else is rejected before any SQL runs. If the current dimension cannot be read, or the transaction cannot be started, the run reports failure before writing anything. When the caller already holds a transaction, the changes run inside a SAVEPOINT that is rolled back on failure; connections without SAVEPOINT support (DuckDB) are refused instead. A non-SCD attribute that is NULL on both sides is not treated as a change.

## Performance Characteristics

### ETL Performance Patterns

**Data Loading Performance:**
- **Dimension loading**: Generally fast, limited by SCD logic complexity
- **Fact loading**: Performance varies by table size and index strategy
- **Incremental loading**: Optimized for changed records only

**Transformation Complexity:**
- **Simple transformations**: Basic data type conversions, formatting
- **Complex transformations**: Business rule application, derived calculations
- **SCD processing**: Most complex, requires historical tracking logic

### Query Performance Patterns

**Validation Queries:**
- **Fast (< 1s)**: [V1](queries/tpcdi/v1.md), [V2](queries/tpcdi/v2.md) - Simple dimension counts and aggregations
- **Medium (1-10s)**: [V3](queries/tpcdi/v3.md) - Fact table aggregations
- **Slower (> 10s)**: VQ-series queries with cross-dimension joins

**Analytical Queries:**
- **Interactive (< 5s)**: [A1](queries/tpcdi/a1.md), [A3](queries/tpcdi/a3.md) - Customer and broker analysis
- **Reporting (5-30s)**: [A2](queries/tpcdi/a2.md), [A4](queries/tpcdi/a4.md) - Company and portfolio analysis
- **Analytical (> 30s)**: [A5](queries/tpcdi/a5.md), AQ-series - Historical trend analysis

## Configuration Options

### CLI Options (`--benchmark-option`)

Configure TPC-DI via `--benchmark-option KEY=VALUE`:

| Option | Default | Description |
|--------|---------|-------------|
| `enable_parallel` | `false` | Enable parallel processing for ETL (`true`/`false`) |
| `max_workers` | - | Maximum number of parallel workers |

Accepts hyphenated aliases (e.g. `enable-parallel`, `max-workers`).

```bash
benchbox run --platform duckdb --benchmark tpcdi --scale 1 \
  --benchmark-option enable_parallel=true \
  --benchmark-option max_workers=4
```

This enables parallel ETL with 4 workers.

### Scale Factor Guidelines

| Scale Factor | Data Warehouse Size | ETL Complexity | Use Case |
|-------------|---------------------|----------------|----------|
| 0.1 | ~10 MB | Simple | Development, unit testing |
| 1.0 | ~100 MB | Standard | Integration testing |
| 3.0 | ~300 MB | Enhanced | Performance testing |
| 10.0 | ~1 GB | Complex | Stress testing |
| 30.0+ | ~3+ GB | Production-like | Enterprise simulation |

### ETL Configuration

```python
tpcdi = TPCDI(
    scale_factor=1.0,
    output_dir="tpcdi_data",
    batch_size=10000,
    enable_scd=True,
    validate_data=True,
    audit_trail=True,
    parallel_loading=4
)
```

These are ETL-specific options: `batch_size` is the number of records per batch, `enable_scd` enables SCD Type 2 processing, `validate_data` runs data quality checks, `audit_trail` enables audit logging, and `parallel_loading` sets the number of parallel load processes.

## Integration Examples

### Apache Airflow ETL Pipeline

```python
from airflow import DAG
from airflow.operators.python import PythonOperator
from benchbox import TPCDI
from datetime import datetime, timedelta

def extract_source_data(**context):
    tpcdi = TPCDI(scale_factor=1.0)
    source_data = tpcdi.generate_source_data()
    return source_data

def transform_and_load(**context):
    tpcdi = TPCDI(scale_factor=1.0)
    batch_id = context['batch_id']

    results = tpcdi.run_etl_process(
        batch_id=batch_id,
        effective_date=context['ds']
    )
    return results

def validate_data_quality(**context):
    tpcdi = TPCDI(scale_factor=1.0)

    validation_results = {}
    for query_id in ["V1", "V2", "V3", "V4", "V5"]:
        validation_results[query_id] = tpcdi.run_validation_query(query_id)

    return validation_results

dag = DAG(
    'tpcdi_etl_pipeline',
    default_args={
        'owner': 'data-team',
        'depends_on_past': False,
        'start_date': datetime(2023, 1, 1),
        'email_on_failure': True,
        'retries': 1,
        'retry_delay': timedelta(minutes=5)
    },
    description='TPC-DI ETL Pipeline',
    schedule_interval='@daily',
    catchup=False
)

extract_task = PythonOperator(
    task_id='extract_source_data',
    python_callable=extract_source_data,
    dag=dag
)

transform_task = PythonOperator(
    task_id='transform_and_load',
    python_callable=transform_and_load,
    dag=dag
)

validate_task = PythonOperator(
    task_id='validate_data_quality',
    python_callable=validate_data_quality,
    dag=dag
)

extract_task >> transform_task >> validate_task
```

The last line sets the task dependencies: extract runs first, then transform and load, then validation.

### Data Quality Framework

```python
class TPCDIDataQualityFramework:
    def __init__(self, tpcdi: TPCDI, connection):
        self.tpcdi = tpcdi
        self.connection = connection

    def run_systematic_validation(self) -> dict:
        results = {
            'validation_queries': {},
            'data_quality_checks': {},
            'referential_integrity': {},
            'business_rules': {}
        }

        for query_id in ["V1", "V2", "V3", "V4", "V5"]:
            query_sql = self.tpcdi.get_query(query_id)
            result = self.connection.execute(query_sql).fetchall()
            results['validation_queries'][query_id] = {
                'status': 'PASSED' if len(result) > 0 else 'FAILED',
                'row_count': len(result)
            }

        dq_checks = {
            'DQ1': self._check_referential_integrity(),
            'DQ2': self._check_temporal_consistency(),
            'DQ3': self._check_business_rules(),
            'DQ4': self._check_data_completeness(),
            'DQ5': self._check_duplicate_detection()
        }

        for check_id, check_result in dq_checks.items():
            results['data_quality_checks'][check_id] = check_result

        return results

    def _check_referential_integrity(self) -> dict:
        checks = [
            ("Customer-Account", "SELECT COUNT(*) FROM DimAccount a LEFT JOIN DimCustomer c ON a.SK_CustomerID = c.SK_CustomerID WHERE c.SK_CustomerID IS NULL"),
            ("Trade-Customer", "SELECT COUNT(*) FROM FactTrade t LEFT JOIN DimCustomer c ON t.SK_CustomerID = c.SK_CustomerID WHERE c.SK_CustomerID IS NULL"),
            ("Trade-Security", "SELECT COUNT(*) FROM FactTrade t LEFT JOIN DimSecurity s ON t.SK_SecurityID = s.SK_SecurityID WHERE s.SK_SecurityID IS NULL")
        ]

        results = {}
        for check_name, check_sql in checks:
            violation_count = self.connection.execute(check_sql).fetchone()[0]
            results[check_name] = {
                'violations': violation_count,
                'status': 'PASSED' if violation_count == 0 else 'FAILED'
            }

        return results

    def _check_temporal_consistency(self) -> dict:
        overlap_check = """
        SELECT COUNT(*) FROM DimCustomer c1
        JOIN DimCustomer c2 ON c1.CustomerID = c2.CustomerID
        AND c1.SK_CustomerID != c2.SK_CustomerID
        WHERE c1.EffectiveDate <= COALESCE(c2.EndDate, '9999-12-31')
        AND COALESCE(c1.EndDate, '9999-12-31') >= c2.EffectiveDate
        """

        overlaps = self.connection.execute(overlap_check).fetchone()[0]
        return {
            'overlapping_ranges': overlaps,
            'status': 'PASSED' if overlaps == 0 else 'FAILED'
        }

    def _check_business_rules(self) -> dict:
        rules = [
            ("Positive Trade Amounts", "SELECT COUNT(*) FROM FactTrade WHERE TradePrice <= 0"),
            ("Valid Customer Tiers", "SELECT COUNT(*) FROM DimCustomer WHERE Tier NOT IN (1,2,3)"),
            ("Account Status Values", "SELECT COUNT(*) FROM DimAccount WHERE Status NOT IN ('Active','Inactive','Closed')")
        ]

        results = {}
        for rule_name, rule_sql in rules:
            violations = self.connection.execute(rule_sql).fetchone()[0]
            results[rule_name] = {
                'violations': violations,
                'status': 'PASSED' if violations == 0 else 'FAILED'
            }

        return results

    def _check_data_completeness(self) -> dict:
        completeness_checks = [
            ("Customer Names", "SELECT COUNT(*) FROM DimCustomer WHERE LastName IS NULL OR FirstName IS NULL"),
            ("Trade Prices", "SELECT COUNT(*) FROM FactTrade WHERE TradePrice IS NULL"),
            ("Security Symbols", "SELECT COUNT(*) FROM DimSecurity WHERE Symbol IS NULL")
        ]

        results = {}
        for check_name, check_sql in completeness_checks.items():
            missing_count = self.connection.execute(check_sql).fetchone()[0]
            results[check_name] = {
                'missing_values': missing_count,
                'status': 'PASSED' if missing_count == 0 else 'WARNING'
            }

        return results

    def _check_duplicate_detection(self) -> dict:
        duplicate_checks = [
            ("Current Customer Records", """
                SELECT CustomerID, COUNT(*)
                FROM DimCustomer
                WHERE IsCurrent = 1
                GROUP BY CustomerID
                HAVING COUNT(*) > 1
            """),
            ("Trade Record Duplicates", """
                SELECT TradeID, COUNT(*)
                FROM FactTrade
                GROUP BY TradeID
                HAVING COUNT(*) > 1
            """)
        ]

        results = {}
        for check_name, check_sql in duplicate_checks:
            duplicates = self.connection.execute(check_sql).fetchall()
            results[check_name] = {
                'duplicate_groups': len(duplicates),
                'status': 'PASSED' if len(duplicates) == 0 else 'FAILED'
            }

        return results

dq_framework = TPCDIDataQualityFramework(tpcdi, conn)
quality_results = dq_framework.run_systematic_validation()
print(f"Data Quality Score: {quality_results}")
```

The framework runs the validation queries first, then the data quality checks. `_check_temporal_consistency` looks for overlapping date ranges in SCD Type 2 tables.

## Best Practices

### ETL Development
1. **Incremental processing** - Design for efficient incremental updates
2. **SCD implementation** - Use proper Type 1 and Type 2 patterns
3. **Data validation** - Implement systematic quality checks
4. **Error handling** - Design robust error handling and recovery
5. **Audit trails** - Maintain complete data lineage tracking

### Performance Optimization
1. **Batch processing** - Use appropriate batch sizes for loading
2. **Parallel processing** - Use parallel ETL where possible
3. **Index strategy** - Design indexes for both loading and querying
4. **Staging tables** - Use staging areas for complex transformations
5. **Monitoring** - Implement ETL performance monitoring

### Data Quality
1. **Validation queries** - Run all validation queries after ETL
2. **Business rules** - Implement domain-specific validation
3. **Referential integrity** - Maintain FK relationships properly
4. **Temporal consistency** - Ensure valid SCD date ranges
5. **Completeness checks** - Validate critical data presence

## Common Issues and Solutions

### ETL Performance Issues

**Issue: Slow SCD Type 2 processing**

Use merge or upsert patterns instead of separate UPDATE and INSERT statements.

```sql
MERGE DimCustomer AS target
USING customer_staging AS source
ON target.CustomerID = source.CustomerID AND target.IsCurrent = 1
WHEN MATCHED AND (target.LastName != source.LastName OR target.Address != source.Address) THEN
    UPDATE SET IsCurrent = 0, EndDate = CURRENT_DATE
WHEN NOT MATCHED THEN
    INSERT (CustomerID, LastName, FirstName, Address, IsCurrent, EffectiveDate)
    VALUES (source.CustomerID, source.LastName, source.FirstName, source.Address, 1, CURRENT_DATE);
```

**Issue: Memory issues during large batch loading**

Use smaller batch sizes and streaming. `batch_size=5000` gives smaller batches, and `streaming_load=True` streams large files.

```python
tpcdi = TPCDI(
    scale_factor=1.0,
    batch_size=5000,
    streaming_load=True
)
```

### Data Quality Issues

**Issue: Referential integrity violations**

Load dimensions before facts and validate foreign keys. The query uses `-1` as a default surrogate key for missing dimension rows.

```sql
INSERT INTO FactTrade (SK_CustomerID, SK_SecurityID, ...)
SELECT COALESCE(c.SK_CustomerID, -1),
       COALESCE(s.SK_SecurityID, -1),
       ...
FROM staging_trade st
LEFT JOIN DimCustomer c ON st.CustomerID = c.CustomerID AND c.IsCurrent = 1
LEFT JOIN DimSecurity s ON st.Symbol = s.Symbol AND s.IsCurrent = 1;
```

## Related Documentation

- [TPC-H Benchmark](tpc-h.md) - Decision support queries
- [TPC-DS Benchmark](tpc-ds.md) - Complex analytical queries
- [Architecture Guide](../design/architecture.md) - BenchBox design principles
- [Advanced-level Usage](../advanced/index.md) - Complex benchmark scenarios

## External Resources

- [TPC-DI Specification](http://www.tpc.org/tpcdi/) - Official TPC-DI documentation
- [TPC-DI Tools](http://www.tpc.org/tpc_documents_current_versions/current_specifications.asp) - Official benchmark tools
- [Data Warehousing ETL Best Practices](http://www.tpc.org/information/white_papers.asp) - Industry guidelines
- [Slowly Changing Dimensions](https://en.wikipedia.org/wiki/Slowly_changing_dimension) - SCD implementation patterns
