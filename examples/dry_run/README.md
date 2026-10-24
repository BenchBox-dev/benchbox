# Dry-Run Examples

Examples demonstrating BenchBox dry-run mode for previewing benchmarks without executing queries.

## Overview

Dry-run mode allows you to:
- Preview benchmark queries and schemas
- Estimate resource requirements
- Validate configurations
- Generate documentation
- Test CI/CD integration

All without executing queries or incurring costs.

## Examples

### 1. basic_dry_run.py
**Purpose:** Basic dry-run execution and result inspection

**Run it:**
```bash
python basic_dry_run.py
```

**What it does:**
- Executes dry-run for TPC-H SF=0.01
- Displays query count and resource estimates
- Shows where artifacts are saved

### 2. analyze_dry_run_output.py
**Purpose:** Programmatic analysis of dry-run output

**Run it:**
First run a dry run, then analyze it.

```bash
benchbox run --dry-run ./my_preview --platform duckdb --benchmark tpch --scale 0.01

python analyze_dry_run_output.py ./my_preview
```

**What it does:**
- Loads dry-run summary.json
- Analyzes query complexity (join counts)
- Reports system and benchmark information
- Estimates resource requirements

### 3. query_validation.py
**Purpose:** Validate extracted SQL queries

**Run it:**
```bash
python query_validation.py ./dry_run_preview
```

**What it does:**
- Validates SQL syntax
- Checks parentheses matching
- Optionally lints with sqlfluff (if installed)
- Reports validation results

### 4. documentation_generator.py
**Purpose:** Auto-generate benchmark documentation from dry-run

**Run it:**
```bash
python documentation_generator.py tpch tpcds ssb
```

**What it does:**
- Runs dry-run for each benchmark
- Extracts query counts, table info, resource estimates
- Generates markdown documentation
- Saves to `./generated_docs/`

### 5. ci_validation.py
**Purpose:** CI/CD integration for automated validation

**Run it:**
```bash
python ci_validation.py
```

**What it does:**
- Validates critical benchmarks (tpch, ssb)
- Uses dry-run to avoid query execution
- Reports pass/fail for CI/CD
- Exits with appropriate code (0=pass, 1=fail)

## Dry-Run Artifacts

Each dry-run creates a directory with:

```
preview_directory/
├── summary.json       # Benchmark metadata and estimates
├── queries/           # Extracted SQL queries
│   ├── query1.sql
│   ├── query2.sql
│   └── ...
├── schema.sql         # DDL for creating tables
└── config.yaml        # Benchmark configuration
```

## CLI Dry-Run Examples

```bash
benchbox run --dry-run ./preview --platform duckdb --benchmark tpch --scale 0.1

for bm in tpch tpcds ssb; do
  benchbox run --dry-run ./preview_$bm --platform duckdb --benchmark $bm --scale 0.01
done

benchbox run --dry-run ./preview_tuned --platform duckdb --benchmark tpch --scale 0.1 --tuning
```

The commands run, in order: a basic dry run, a dry run for multiple benchmarks, and a dry run with a tuning preview.

## When to Use Dry-Run

**Use dry-run when:**
- Testing new benchmarks or platforms
- Estimating cloud costs before execution
- Generating documentation
- Validating configurations in CI/CD
- Debugging query generation
- Comparing benchmark complexity

**Don't use dry-run when:**
- You need actual performance results
- Testing data loading
- Measuring query execution times
- Validating query results

## See Also

- **CLI Dry-Run Guide:** <https://benchbox.dev/docs/usage/dry-run.html>
- **Dry-Run Samples:** [../dry_run_samples/](../dry_run_samples/) - Example output artifacts
- **CI/CD Integration:** <https://benchbox.dev/docs/advanced/ci-cd-integration.html>
