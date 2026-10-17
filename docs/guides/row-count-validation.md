# Row Count Validation

```{tags} intermediate, guide, validation
```

**Automatic validation of query results against expected row counts**

---

## Overview

Row count validation is a feature that automatically validates benchmark query execution by comparing actual row counts against expected results from official TPC answer files. This helps ensure:

- **Correctness**: Queries return the expected number of rows
- **Platform Compliance**: Database platforms implement TPC specifications correctly
- **Regression Detection**: Changes to data or queries don't break correctness
- **Confidence**: Results are trustworthy before performance comparisons

## Quick Start

### Basic Usage

Row count validation is **automatically enabled** for supported benchmarks (TPC-H, TPC-DS) when using the standard adapters:

```python
from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox import TPCH

adapter = DuckDBAdapter()
benchmark = TPCH(scale_factor=1.0)

results = adapter.run_benchmark(benchmark)

for query_result in results['queries']:
    print(f"Query {query_result['query_id']}: {query_result.get('row_count_validation_status', 'N/A')}")
```

Validation is enabled by default, and it runs automatically when the benchmark runs. The loop checks the validation status of each query result.

### Validation Output

Each query result includes validation metadata when validation is performed:

```python
{
    "query_id": "1",
    "status": "SUCCESS",
    "execution_time": 0.123,
    "rows_returned": 4,
    "expected_row_count": 4,
    "row_count_validation_status": "PASSED",
}
```

`expected_row_count` is the expected count from the answer files, and `row_count_validation_status` is the validation result. A real result contains other fields as well.

## Validation Statuses

### PASSED

Query returned the exact expected number of rows (or within acceptable range for non-deterministic queries).

```
✅ Query 1: 4 rows (expected: 4) - Validation PASSED
```

### FAILED

Query returned a different number of rows than expected. This indicates a potential correctness issue.

```
❌ Query 2: 250 rows (expected: 460) - Validation FAILED
  Difference: -210 rows (-45.7%)
```

**Possible causes:**
- Incorrect SQL query implementation
- Data loading errors
- Platform-specific behavior differences
- Scale factor mismatch

### SKIPPED

Validation was skipped because no expected result is available for this query/scale factor combination.

```
⚠️ Query 3: 150 rows - Validation SKIPPED
```

**Common reasons:**
- Scale factor other than 1.0 (expected results only available for SF=1.0 currently)
- Query variant not in answer files
- Answer files are unavailable and the provider reports `FileNotFoundError`,
  including a disabled download or an on-demand download that returned no files
  - run `benchbox download-answers` to pre-populate the cache
- Non-standard benchmark

### Missing Expectations and Provider Errors

`SKIPPED` means no row-count comparison was certified. Query execution can
remain `SUCCESS` while its `row_count_validation` block is `SKIPPED` and carries
a warning. An explicit SKIP expectation, an absent answer set or query lookup,
and a nonzero TPC reference stream use this unevaluated result.

A registered EXACT expectation whose count lookup returns `None` also becomes
SKIP with an `EXACT validation mode but no expected count available` warning.
This is the current defensive lookup behavior; it does not establish that the
returned rows are correct. Normal `ExpectedQueryResult` construction requires
an exact count or a formula for EXACT mode, so a constructor rejection is a
separate error, not this downgrade.

Expected-results failures are classified. A provider `FileNotFoundError` is
cached as absent answer data. The on-demand downloader currently converts its
failures to no files, which the loader reports as `FileNotFoundError`; those
failures therefore also become unevaluated skips. Other provider exceptions
produce a failed validation result and may retry on a later request. A waiter
timeout also fails validation while the background load continues. Neither an
invalid SKIP-mode result nor a provider timeout is published as normal SKIPPED
validation: the adapter records a failed query and validation error.

Canonical query-result serialization preserves expected/actual counts and
warning/error text. It downgrades PASSED evidence without an expected count to
SKIPPED. Consequently, a successfully evaluated RANGE check, whose result has
no single expected count, also appears as SKIPPED after this normalization.
Inspect the available evidence; execution success alone is not correctness
certification.

TPC-DS DataFrame runs use a separate validation boundary because their expected
row counts are not seed-aligned. Successful queries receive SKIPPED row-count
evidence and an UNCERTAIN validation summary; execution failures remain visible
as PARTIAL. SQL reference-stream validation does not certify those DataFrame
streams.

## Supported Benchmarks

### TPC-H

- **Scale Factors**: SF=1.0, SF=0.01 and SF=0.1 (with fallback for scale-independent queries at other SFs)
- **Queries**: All 22 official TPC-H queries (Q1-Q22)
- **Query Variants**: Q15 variants supported (15a, 15b, etc.)
- **Scale-Independent Queries**: Q1 (same row count at any SF)

SF=1.0 expected results come from the official TPC-H answer files distributed
with dbgen. TPC publishes no answer files below SF 1, so SF=0.01 and SF=0.1 use
a BenchBox reference in
`benchbox/core/expected_results/reference_row_counts/`: exact counts for qgen's
default parameters, produced from bundled dbgen data and checked to agree on
DuckDB and DataFusion. Regenerate it with `make tpch-subscale-row-counts-regen`.
Each file also lists the queries whose count depends on the substitution
parameters at that scale. SQL and DataFrame runs with other parameters (a
seed) skip those queries and still check the rest, so a seeded run reports
UNCERTAIN unless a checked query fails. Seeded DataFrame runs at SF 1 are not
checked, as before.

### TPC-DS

- **Scale Factors**: SF=1.0 only
- **Queries**: All 99 official TPC-DS queries
- **Query Variants**: Base queries without substitution variants
- **Scale-Independent Queries**: None (all TPC-DS queries are scale-dependent)

Expected results sourced from official TPC-DS answer sets for SF=1.0.

## Advanced Usage

### Query ID Formats

The validation system handles various query ID formats automatically:

```python
validator.validate_query_result("tpch", 1, actual_row_count=4)
validator.validate_query_result("tpch", "1", actual_row_count=4)
validator.validate_query_result("tpch", "Q1", actual_row_count=4)
validator.validate_query_result("tpch", "query1", actual_row_count=4)

validator.validate_query_result("tpch", "15a", actual_row_count=1)
validator.validate_query_result("tpch", "Q15b", actual_row_count=1)
```

The first four calls all map to Query 1: an integer, a string, a `Q` prefix and a `query` prefix. The last two are query variants, which use the base query number and map to Q15.

### Manual Validation

You can validate query results manually using the `QueryValidator`:

```python
from benchbox.core.validation.query_validation import QueryValidator

validator = QueryValidator()

result = validator.validate_query_result(
    benchmark_type="tpch",
    query_id="1",
    actual_row_count=4,
    scale_factor=1.0
)

if result.is_valid:
    print(f"✅ Validation passed: {result.expected_row_count} rows")
else:
    print(f"❌ Validation failed: {result.error_message}")
```

### Scale Factor Handling

#### Scale Factor 1.0

Full validation support with exact expected row counts:

```python
results = adapter.run_benchmark(
    TPCH(scale_factor=1.0),
    validate_row_counts=True
)
```

At SF=1.0 all queries are validated. `validate_row_counts=True` is the default.

#### Other Scale Factors

**Scale-independent queries** (e.g., TPC-H Q1) use SF=1.0 expectations:

```python
validator.validate_query_result("tpch", "1", actual_row_count=4, scale_factor=10.0)

validator.validate_query_result("tpch", "2", actual_row_count=1000, scale_factor=10.0)
```

At SF=10, Q1 still validates because it is scale-independent. It uses the SF=1.0 expectation (4 rows) and the result is PASSED. Q2 is scale-dependent and there are no SF=10.0 expectations, so its validation is SKIPPED.

### Disabling Validation

If you need to disable validation:

```python
results = adapter.run_benchmark(benchmark)
```

There are two options. Option 1, disabling validation at the adapter level, is not yet implemented, because validation is always on. It may be added in a future version if needed. Option 2 is to ignore validation results by not checking the `row_count_validation_status` fields.

## How It Works

### Architecture

```
┌─────────────────┐
│ Platform Adapter│
│  (run_benchmark)│
└────────┬────────┘
         │
         ▼
┌─────────────────────┐     ┌──────────────────┐
│ Execute Query       │     │ Expected Results │
│ (returns row count) │────▶│    Registry      │
└─────────────────────┘     └────────┬─────────┘
                                     │
                                     ▼
                            ┌─────────────────┐
                            │ QueryValidator  │
                            │ • Normalize ID  │
                            │ • Lookup expect │
                            │ • Compare count │
                            └────────┬────────┘
                                     │
                                     ▼
                            ┌─────────────────┐
                            │ValidationResult │
                            │ • is_valid      │
                            │ • status        │
                            │ • difference    │
                            └─────────────────┘
```

### Components

1. **QueryValidator**: Main validation engine
   - Normalizes query IDs (handles various formats)
   - Retrieves expected results from registry
   - Compares actual vs expected counts
   - Returns structured ValidationResult

2. **ExpectedResultsRegistry**: Centralized result storage
   - Lazy-loads expected results per benchmark/scale factor
   - Thread-safe caching for performance
   - Supports scale-independent query fallback
   - Provider pattern for extensibility

3. **Providers**: Benchmark-specific result loaders
   - `tpch_results.py`: Loads TPC-H expected results
   - `tpcds_results.py`: Loads TPC-DS expected results
   - Auto-registered on first validation

### Thread Safety

The validation system is thread-safe for concurrent query execution:

```python
results = adapter.run_throughput_test(
    benchmark=benchmark,
    connection=connection,
    num_streams=4,
)
```

Throughput tests with concurrent queries are safe. The streams share one connection, and each stream gets its own session. `num_streams=4` runs 4 concurrent query streams, and each stream can validate concurrently without conflicts.

**Implementation:**
- Registry uses `threading.Lock` to protect cache and provider registry
- Double-check locking pattern for efficient concurrent access
- Slow provider loads happen outside locks

## Installation and Answer File Availability

### How Answer Files Are Distributed

TPC-H and TPC-DS answer files (~4.2 MB total) are distributed separately from
the BenchBox package:

| Installation method | Answer file availability |
|---------------------|--------------------------|
| `git clone` / `uv sync` (dev) | ✅ Included in `_sources/` |
| `pip install benchbox` (wheel) | ⬇️ Downloaded on-demand |
| `BENCHBOX_NO_DOWNLOAD=1` set | ⚠️ Skipped (validation disabled) |

For wheel installs, BenchBox automatically downloads a cached copy of the answer
files the first time validation is needed. Subsequent runs use the local cache
with no network overhead.

### Cache Location

Downloaded answer files are stored in:

```
$XDG_CACHE_HOME/benchbox/answers/    # if XDG_CACHE_HOME is set
~/.cache/benchbox/answers/            # otherwise (default)
```

Subdirectories:

```
~/.cache/benchbox/answers/
├── tpch/          # q1.out … q22.out
└── tpcds/         # 1.ans … 99.ans + NULLS variants
```

### Pre-Downloading Answer Files

To populate the cache before your first benchmark run (e.g., in a CI
environment without internet access during the run itself):

```bash
benchbox download-answers
benchbox download-answers --benchmark tpch
benchbox download-answers --benchmark tpcds
benchbox download-answers --force
benchbox download-answers --show-cache-dir
```

The commands download both TPC-H and TPC-DS answers, TPC-H only, TPC-DS only, re-download even if cached, and print the cache location and exit.

### Disabling Automatic Downloads

To opt out of all automatic downloads (air-gapped environments, CI pipelines
where networking is blocked):

```bash
export BENCHBOX_NO_DOWNLOAD=1
```

With this set, BenchBox skips the download attempt and logs an INFO message
instead of attempting a network connection. Validation is gracefully skipped for
queries whose answer files are not available locally; the benchmark still runs,
only correctness validation is omitted.

### Using a Custom Answer File URL

To serve answer files from a private CDN or internal mirror:

```bash
export BENCHBOX_ANSWERS_URL=https://your-cdn.example.com/benchbox/answers-v1
```

The downloader will fetch `tpch-answers.tar.gz`, `tpcds-answers.tar.gz`, and
`checksums.sha256` from this URL instead of the default GitHub releases URL.
See `.github/workflows/upload-answers.yml` for the expected archive layout.

---

## Troubleshooting

### Validation Failures at SF=1.0

**Symptom**: Queries fail validation at scale factor 1.0

**Possible causes:**

1. **Data loading error**
   ```
   # Verify data was loaded correctly
   SELECT COUNT(*) FROM lineitem;  -- Should match scale factor
   ```

2. **SQL translation issue**
   ```python
   sql = benchmark.get_query(query_id=1, dialect="duckdb")
   print(sql)
   ```

3. **Platform-specific behavior**
   ```
   # Some platforms may handle edge cases differently
   # Check if difference is consistent across queries
   ```

### Validation Skipped at SF≠1.0

**Symptom**: All validations skipped at SF=10, SF=100, etc.

**This is expected behavior:**
- Expected results are only available for SF=1.0, SF=0.01 and SF=0.1
- Scale-dependent queries cannot be validated at other SFs
- Scale-independent queries (e.g., TPC-H Q1) still validate via fallback

**Solution**: If you need validation at higher SFs, use SF=1.0 for correctness validation, then higher SFs for performance testing.

### "No expected results provider registered"

**Symptom**: Warning message about missing provider

**Cause**: Provider auto-registration failed (rare)

**Solution**:
```python
from benchbox.core.expected_results import register_all_providers
register_all_providers()
```

This triggers provider registration manually.

### Query ID Not Found

**Symptom**: Validation skipped with "No expected row count defined"

**Causes:**
1. Query ID format not recognized
   - Use standard formats: 1, "1", "Q1", "query1"
2. Query variant not in answer files
   - Some TPC-DS query variants may not have answer sets
3. Non-standard query
   - Custom queries won't have expected results

## Technical Details

### Expected Result Models

```python
@dataclass
class ExpectedQueryResult:
    query_id: str
    scale_factor: float | None = None
    expected_row_count: int | None = None
    expected_row_count_min: int | None = None
    expected_row_count_max: int | None = None
    row_count_formula: str | None = None
    validation_mode: ValidationMode = ValidationMode.EXACT
    scale_independent: bool = False
    notes: str | None = None
    reference_parameters_only: bool = False
```

`reference_parameters_only` marks a count that holds only for the reference
substitution parameters; runs with other parameters skip that query.

`expected_row_count_min` and `expected_row_count_max` are for non-deterministic queries. `row_count_formula` holds an expression such as `"SF * 100"`.

### Validation Modes

1. **EXACT**: Row count must match exactly
   ```python
   expected_row_count = 4
   actual_row_count = 4
   actual_row_count = 5
   ```
   An actual count of 4 passes. An actual count of 5 fails.

2. **RANGE**: Row count must be within min/max range (for non-deterministic queries)
   ```python
   expected_row_count_min = 100
   expected_row_count_max = 150
   actual_row_count = 125
   actual_row_count = 200
   ```
   An actual count of 125 passes. An actual count of 200 fails.

3. **SKIP**: Validation is skipped. It is used when no expected result is available.

### Formula-Based Expectations (Future)

For scale-dependent queries, formulas can express expected count:

```python
ExpectedQueryResult(
    query_id="example",
    row_count_formula="SF * 1000",
    scale_independent=False
)
```

The formula scales with the scale factor.

Currently, only exact row counts are used. Formulas are evaluated using safe AST parsing (no `eval()`).

## Best Practices

### 1. Validate at SF=1.0 First

```python
correctness_results = adapter.run_benchmark(
    TPCH(scale_factor=1.0),
    validate_row_counts=True
)

assert all(q['row_count_validation_status'] == 'PASSED' for q in correctness_results['queries'])

performance_results = adapter.run_benchmark(
    TPCH(scale_factor=100),
    validate_row_counts=True
)
```

The first run establishes correctness at SF=1.0, and all queries should PASS. Then scale up for performance testing. Scale-independent queries still validate at the larger scale.

### 2. Check Validation Status in CI/CD

```python
results = adapter.run_benchmark(benchmark)

failed_validations = [
    q for q in results['queries']
    if q.get('row_count_validation_status') == 'FAILED'
]

if failed_validations:
    for q in failed_validations:
        print(f"❌ Query {q['query_id']}: {q.get('row_count_validation_error')}")
    raise AssertionError(f"{len(failed_validations)} queries failed validation")
```

Use this pattern in automated tests.

### 3. Document Validation Skips

```python
print("Running at SF=10.0:")
print("- Scale-independent queries: VALIDATED")
print("- Scale-dependent queries: SKIPPED (no SF=10.0 expectations)")
```

If you run at a scale factor other than 1.0, document that validation is limited.

### 4. Use Validation for Debugging

```python
if query_result['row_count_validation_status'] != 'PASSED':
    print(f"⚠️ Query may be incorrect - investigate before performance tuning")
    print(f"  Expected: {query_result['expected_row_count']} rows")
    print(f"  Actual: {query_result['rows_returned']} rows")
```

When you investigate performance issues, check correctness first.

## Future Enhancements

Planned improvements to row count validation:

1. **Additional Scale Factors**: Expected results for SF=10, SF=100
2. **Custom Expectations**: Allow users to define expected results for custom benchmarks
3. **Result Content Validation**: Validate actual result values, not just row counts
4. **Checksum Validation**: MD5/SHA checksums of result sets
5. **Differential Validation**: Compare results across platforms
6. **Configuration Options**: Toggle validation on/off per query or benchmark

> **Note**: Wheel installs can download answer files on demand. See the
> [Installation and Answer File Availability](#installation-and-answer-file-availability)
> section above.

## References

- TPC-H Specification: https://www.tpc.org/tpch/
- TPC-DS Specification: https://www.tpc.org/tpcds/
- BenchBox Architecture: docs/design/architecture.md
- Issue Tracking: Report validation bugs on GitHub
