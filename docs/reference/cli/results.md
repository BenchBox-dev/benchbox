<!-- markdownlint-disable MD024 -->

# Results Commands

```{tags} reference, cli, validation
```

Commands for exporting, viewing, and comparing benchmark results.

(cli-export)=

## `export` - Export Results

Re-export existing benchmark results in different formats without re-running benchmarks. Useful for sharing results, generating reports, or converting to spreadsheet-friendly formats.

### Options

- `RESULT_FILE`: Path to result JSON file to export (optional argument)
- `--format [json|csv|html]`: Export format(s) - can specify multiple times (default: json)
- `--output-dir TEXT`: Output directory (default: benchmark_runs/results/)
- `--last`: Export most recent result file
- `--benchmark TEXT`: Filter by benchmark name when using --last
- `--platform TEXT`: Filter by platform name when using --last
- `--force`: Overwrite existing files without prompting

### Supported Export Formats

**JSON**, Complete benchmark results in canonical schema format

- Full metadata, metrics, and query results
- Suitable for programmatic analysis and archival
- Default format during benchmark runs

**CSV**, Flattened query results for spreadsheet analysis

- Query-level details: execution times, status, rows returned
- Compatible with Excel, Google Sheets, data analysis tools
- Ideal for performance analysis and charting

**HTML**, Standalone report with formatted tables

- Summary metrics and system information
- Color-coded query results table
- Ready to share with stakeholders

### Usage Examples

```bash
benchbox export --last --format csv

benchbox export results/tpch_sf1_duckdb_sql_<timestamp>_<id>.json --format csv --format html

benchbox export --last --benchmark tpch --format json --format csv --format html

benchbox export --last --platform duckdb --format html

benchbox export --last --format csv --output-dir ./reports/

benchbox export benchmark_runs/results/tpcds_sf10_duckdb_sql_<timestamp>_<id>.json --format html --force
```

The commands, in order, export:

- the most recent result to CSV;
- a specific result file to multiple formats;
- the latest TPC-H result to all formats;
- the latest DuckDB result to HTML;
- the most recent result to a custom directory;
- a specific file to HTML with a forced overwrite.

### Common Workflows

**Share Results with Team:**

```bash
benchbox export --last --format html --output-dir ./team_reports/
```

This exports the recent result as an HTML report. Share the HTML file by email or in documentation.

**Analyze in Spreadsheet:**

```bash
benchbox export --last --format csv --output-dir ~/Downloads/
```

This exports to CSV for Excel or Sheets. Open the CSV there for charting and analysis.

**Archive Benchmarks:**

```bash
benchbox export --last --format json --format csv --format html --output-dir ./archive/
```

This exports all formats for comprehensive archival.

### Notes

- The `export` command loads existing result files from `benchmark_runs/results/`
- Schema versions 2.0, 2.1, 2.2, and 2.3 are supported by current result tooling
- Export preserves all metrics and metadata from original results
- Large result files (TPC-DS at scale 100+) may take a few seconds to process
- The --force flag skips confirmation prompts when overwriting existing files
- Use `benchbox results` to see available result files before exporting

---

(cli-results)=

## `results` - Show Benchmark Results

Display exported benchmark results and execution history.

### Options

- `--limit INTEGER`: Number of results to show (default: 10)
- `--submitted`: Show hosted submission history sidecars instead of exported
  benchmark result files
- `--paths`: Print copyable primary result JSON paths for use with
  `benchbox submit`, `benchbox export`, or `benchbox results show-cli`

`--paths` writes one path per line to stdout (no Rich formatting, no preamble)
so the output is safe to pipe into `xargs` or save with `tee`. Hint text and
overflow notices are written to stderr. The list contains only primary
schema-v2 result JSON files — companion files such as `.plans.json`,
`.tuning.json`, and hosted `.submission.json` sidecars are excluded.

`--paths` and `--submitted` are mutually exclusive: hosted submission history
is a separate sidecar surface from local result discovery.

### Usage Examples

```bash
benchbox results

benchbox results --limit 25

benchbox results --paths

benchbox results --paths --limit 100 | xargs -n1 -I{} benchbox submit {} --output ./submissions

benchbox results --submitted
```

The commands, in order, show recent results, show more results, show the exact result file paths accepted by `submit` and `export` (one per line, so the output is pipeable), pipe those paths into `submit` to package each result in turn, and show hosted submissions and public URLs.

---

(cli-compare)=

## `compare` - Compare Benchmark Results

Compare two or more benchmark result files to analyze performance changes. Displays side-by-side query timing comparisons, geometric means, and regression detection suitable for CI/CD workflows.

### Basic Syntax

```bash
benchbox compare BASELINE.json CURRENT.json [OPTIONS]
```

### Options

- `RESULT_FILES`: Two or more result JSON files to compare (first file is baseline, required)
- `--fail-on-regression THRESHOLD`: Exit with code 1 if any regression exceeds threshold
  - Percentage format: `10%`, `5.5%`
  - Decimal format: `0.1`, `0.05`
- `--min-regression-delta DURATION`: Count a query as regressed only when it exceeds the `--fail-on-regression` threshold and also slows by more than this duration. Requires `--fail-on-regression`; using it alone is a usage error (exit code 2).
  - Milliseconds: `5ms`, `2.5ms`
  - Seconds: `0.005s`
  - Not set by default, so queries are judged by percentage alone. Aggregate metrics such as total time are always judged by percentage alone.
- `--format [text|json|html]`: Output format (default: text)
- `--output FILE`: Save comparison output to file instead of stdout
- `--show-all-queries`: Show all query comparisons (default: only regressions/improvements)

### Output Formats

**Text** (default), Human-readable comparison report

- Color-coded indicators for performance changes
- Geometric mean calculation across all queries
- Per-query breakdown sorted by severity
- Suitable for terminal viewing and logs

**JSON**, Machine-readable comparison data

- Full comparison metrics and query-level details
- Suitable for programmatic analysis and dashboards
- Includes `performance_changes`, `query_comparisons`, and `summary` sections

**HTML**, Standalone comparison report

- Formatted tables with color-coded severity
- Summary statistics and per-query breakdown
- Ready to share with stakeholders or archive

### Usage Examples

**Basic Comparison:**

```bash
benchbox compare baseline.json current.json

benchbox compare baseline.json current.json --show-all-queries
```

The first command compares two result files. The second also shows all queries, not just the changes.

**CI/CD Integration:**

```bash
benchbox compare baseline.json current.json --fail-on-regression 10%

benchbox compare baseline.json current.json --fail-on-regression 5%

benchbox compare baseline.json current.json --fail-on-regression 0.1
```

The first command fails the pipeline if any query regresses by more than 10%. The second uses a stricter threshold, for critical paths. The third gives the same 10% threshold in decimal notation.

Queries that run for a few milliseconds can change by more than 10% from scheduling noise alone. Add a floor so that only slowdowns larger than a fixed duration count:

```bash
benchbox compare baseline.json current.json --fail-on-regression 10% --min-regression-delta 7ms
```

A query now fails the gate only when it is more than 10% slower and more than 7 ms slower. A 10 ms query that becomes 12 ms passes. A 5 s query that becomes 5.6 s still fails.

**Export Comparison Reports:**

```bash
benchbox compare baseline.json current.json --format json --output comparison.json

benchbox compare baseline.json current.json --format html --output report.html

benchbox compare baseline.json current.json --output comparison.txt
```

The commands, in order, export the comparison as JSON for dashboards, generate an HTML report for stakeholders, and save a text report to a file.

### Comparison Output

The comparison report includes:

**Summary Section:**

- Total queries compared
- Count of improved, regressed, and unchanged queries
- Overall assessment (improved/regressed/mixed)

**Performance Metrics:**

- Average query time change
- Total execution time change
- Per-metric improvement indicators

**Geometric Mean:**

- Baseline and current geometric means (standard benchmark metric)
- Percentage change with severity indicator

**Per-Query Breakdown:**

- Query ID, baseline time, current time, percentage change
- Severity classification:
  - `CRITICAL`: >50% regression
  - `MAJOR`: >25% regression
  - `MINOR`: >10% regression
  - `SLIGHT`: >1% regression
  - `FASTER`: Any improvement

### Severity Indicators

| Indicator | Meaning | Threshold |
| ----------- | --------- | ----------- |
| `🟢` | Improved (faster) | Any negative change |
| `⚪` | Unchanged | <1% change |
| `🟡` | Minor regression | 1-10% slower |
| `🔴` | Major regression | >10% slower |

### Common Workflows

**Regression Testing in CI/CD:**

```bash
benchbox run --platform duckdb --benchmark tpch --scale 0.1 \
  --output ./baseline-results

benchbox run --platform duckdb --benchmark tpch --scale 0.1 \
  --output ./current-results

benchbox compare \
  baseline-results/results/*.json \
  current-results/results/*.json \
  --fail-on-regression 10%
```

The first command runs the baseline benchmark (for example, on the main branch). The second runs the current benchmark (for example, on a feature branch). The `compare` command fails if the current results regress.

**Before/After Optimization Analysis:**

```bash
benchbox run --platform snowflake --benchmark tpch --scale 1 \
  --tuning notuning --output ./baseline

benchbox run --platform snowflake --benchmark tpch --scale 1 \
  --tuning tuned --output ./optimized

benchbox compare \
  baseline/results/tpch_*.json \
  optimized/results/tpch_*.json \
  --format html --output tuning-analysis.html
```

The first command runs without tuning and the second runs with tuning. The `compare` command compares the two sets of results.

**Cross-Platform Comparison:**

```bash
benchbox compare \
  duckdb-results/results/tpch_sf1.json \
  clickhouse-results/results/tpch_sf1.json \
  --show-all-queries
```

### Exit Codes

| Code | Meaning                                                                       |
| ---- | ----------------------------------------------------------------------------- |
| `0`  | Comparison completed successfully (no regression above threshold)             |
| `1`  | Regression detected above `--fail-on-regression` threshold, or error occurred |
| `2`  | Invalid option combination, such as `--min-regression-delta` without `--fail-on-regression` |

### Notes

- The first result file is always treated as the baseline
- Both result files must use a schema version accepted by the current result loader (schema 2.0 through 2.3).
- Results should be from the same benchmark and scale factor for meaningful comparison
- When more than two result paths are supplied, only the first two are compared.
- Use `benchbox results` to find available result files for comparison

### Python API

For programmatic comparison, use the `ResultExporter` class:

```python
from pathlib import Path
from benchbox.core.results.exporter import ResultExporter

exporter = ResultExporter()

comparison = exporter.compare_results(
    Path("baseline.json"),
    Path("current.json")
)

perf = comparison['performance_changes']['average_query_time']
print(f"Average query time: {perf['change_percent']:.2f}% change")

if perf['improved']:
    print("Performance improved!")

report_path = exporter.export_comparison_report(comparison)
print(f"Report saved to: {report_path}")
```

The example compares two result files, checks the overall performance change, and exports the comparison as an HTML report.

See [Result Analysis API](../python-api/result-analysis.md) for complete API documentation.

## Related

- [Workflows](workflows.md) - Common usage patterns including performance analysis
- [Result Schema](../result-schema-v1.md) - JSON schema specification for result files
