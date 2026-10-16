# Contributing to BenchBox Examples

Guidelines for adding, maintaining, and organizing examples in the `examples/` directory.

---

## Philosophy

### Documentation vs Examples

**Documentation** (`docs/`) explains concepts with simplified snippets:
- Short, focused code for illustration
- May omit error handling, logging, CLI parsing
- Emphasizes clarity over completeness
- Inline in markdown files

**Examples** (`examples/`) demonstrates real-world usage:
- Complete, runnable programs
- Include error handling, logging, argument parsing
- Production-ready patterns
- Separate `.py` files with tests

**Golden Rule:** If someone would copy-paste it, it belongs in `examples/`, not `docs/`.

---

## When to Create a New Example File

### ✅ Create a file when:

1. **Code exceeds 50 lines** - Long snippets hurt readability
2. **Multiple use cases** - Code could be reused in different contexts
3. **Needs testing** - Verifiable correctness matters
4. **Error handling required** - Production usage expected
5. **CLI arguments helpful** - Users want to customize behavior
6. **Builds on other examples** - Part of a learning progression

### ❌ Keep inline when:

1. **Demonstrating syntax** - Simple API calls (< 10 lines)
2. **Conceptual explanation** - Abstract patterns, not runnable
3. **Quick reference** - Import statements, configuration snippets
4. **One-off examples** - Unlikely to be reused

---

## Example Structure Standards

### File Organization

```
examples/
├── getting_started/    # Beginner-friendly, 5-10 min each
│   ├── local/          # Zero-config (DuckDB)
│   ├── cloud/          # Platform-specific (credentials required)
│   └── intermediate/   # Slightly more advanced
├── features/           # Single-feature demonstrations
├── use_cases/          # Real-world problem solutions
├── programmatic/       # Advanced workflow patterns
├── dry_run/            # Dry-run mode examples
├── notebooks/          # Interactive Jupyter notebooks
└── tunings/            # Configuration templates
```

### Naming Conventions

**Scripts:** `{platform}_{benchmark}_{feature}.py`
- ✅ `duckdb_tpch_power.py`
- ✅ `databricks_tpcds_throughput.py`
- ✅ `query_subset.py` (when platform-agnostic)
- ❌ `example1.py`
- ❌ `test_script.py`

**Notebooks:** `{platform}_{purpose}.ipynb`
- ✅ `duckdb_tpch.ipynb`
- ✅ `analyze_results.ipynb`
- ❌ `notebook.ipynb`

**Configs:** `{platform}_{benchmark}.yaml`
- ✅ `databricks_tpch.yaml`
- ✅ `bigquery_tpcds.yaml`

### File Template

Describe what the example demonstrates, when to use it and its prerequisites
in the directory README. Source files carry no explanatory comments or
docstrings; see the [comment policy](../docs/internal/comment-policy.md).

Every example should follow this structure. The first line of the file is the
shebang, `#!/usr/bin/env python3`; the block below shows everything after it:

```python
# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License.

import argparse
import sys
from pathlib import Path

def main():
    pass

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)
```

### Required Elements

Every example must include:

1. **Shebang line:** `#!/usr/bin/env python3`
2. **README entry:** Description, use cases and prerequisites in the directory README
3. **Copyright notice:** MIT License
4. **CLI support:** `argparse` or `--help` flag
5. **Error handling:** Try-except with user-friendly messages
6. **Clear output:** Status indicators (✅ ❌ ⚠️) and progress updates
7. **Dry-run support:** `--dry-run` flag where applicable
8. **README reference:** Each directory has a README.md

---

## Code Quality Standards

### Formatting and Style

- **Line length:** 120 characters max
- **Formatting tool:** `ruff format`
- **Linting:** `ruff check` must pass
- **Type hints:** Required for function signatures
- **Comments and docstrings:** None; follow the [comment policy](../docs/internal/comment-policy.md)

### Error Handling

Catch the specific error and tell the user what to do:

```python
try:
    result = execute_benchmark(config)
except MissingCredentialsError as e:
    print(f"❌ Credentials required: {e}")
    print("Set DATABRICKS_TOKEN environment variable")
    sys.exit(1)
```

Avoid bare exceptions that hide the cause:

```python
try:
    result = execute_benchmark(config)
except Exception:
    print("Error!")
    sys.exit(1)
```

### Output Formatting

Show clear status indicators and context:

```python
print("==" * 30)
print("Running TPC-H Power Test")
print("==" * 30)
print(f"✅ Data generated: {data_size_mb} MB")
print(f"✅ Schema created: {table_count} tables")
print(f"⚠️  Running 22 queries (this may take a few minutes)...")
```

Avoid unclear output with no visual hierarchy:

```python
print("Running test")
print("Done")
```

---

## Testing Requirements

### Unit Tests

Not required for simple examples, but encouraged for:
- Complex logic (data processing, calculations)
- Reusable utility functions
- Error handling paths

Place tests in `tests/examples/` matching directory structure.

### Manual Testing

Before committing, verify:

1. **Script runs end-to-end:** `python examples/<category>/<file>.py`
2. **Help works:** `python examples/<category>/<file>.py --help`
3. **Dry-run works:** `python examples/<category>/<file>.py --dry-run ./preview`
4. **Error messages clear:** Test with missing credentials, bad inputs
5. **Output readable:** Check alignment, status indicators, formatting

---

## Documentation Integration

### Linking from Docs

When documenting a feature in `docs/`:

1. **Short inline snippet** (< 20 lines) for concept
2. **Link to full example:** "See `examples/features/query_subset.py` for complete code"
3. **Update MAPPING.md:** Add entry mapping docs section to example

Example:

```markdown
## Query Subsetting

You can run specific queries instead of the full benchmark:

\`\`\`python
from benchbox import TPCH

benchmark = TPCH(scale_factor=0.01, query_subset=[1, 6, 12])
\`\`\`

For a complete example with error handling and CLI support, see:
- **[query_subset.py](../examples/features/query_subset.py)** - Feature demonstration
- **[duckdb_tpch_query_subset.py](../examples/getting_started/intermediate/duckdb_tpch_query_subset.py)** - Beginner-friendly version
```

### Updating MAPPING.md

When adding a new example, update `examples/MAPPING.md`:

1. Add row to Quick Reference table
2. Add detailed mapping section if covering new concept
3. Update relevant category section

---

## Categories Explained

### 1. Getting Started (`getting_started/`)

**Purpose:** Zero to working benchmark in 5-10 minutes

**Target audience:** First-time users, tutorial walkthrough

**Characteristics:**
- Step-by-step structure
- Minimal configuration
- Clear progression (local → cloud → intermediate)
- ~50-100 lines each

**Examples:**
- `local/duckdb_tpch_power.py` - "Hello World"
- `cloud/databricks_tpch_power.py` - Cloud quickstart
- `intermediate/duckdb_tpch_query_subset.py` - Next step

### 2. Features (`features/`)

**Purpose:** Learn one specific capability

**Target audience:** Users who completed getting started

**Characteristics:**
- Single feature focus
- Platform-agnostic when possible
- ~100-150 lines each
- Assumes basic knowledge

**Examples:**
- `test_types.py` - Power/Throughput/Maintenance
- `query_subset.py` - Query filtering
- `tuning_comparison.py` - Before/after tuning

### 3. Use Cases (`use_cases/`)

**Purpose:** Real-world problem solutions

**Target audience:** Production users, advanced workflows

**Characteristics:**
- Solves complete problems
- Production-ready patterns
- ~200-300 lines each
- Includes error recovery, logging

**Examples:**
- `ci_smoke_tests.py` - CI/CD integration
- `platform_comparison.py` - Multi-platform evaluation
- `cost_optimization.py` - Cloud cost management

### 4. Programmatic (`programmatic/`)

**Purpose:** Advanced automation and orchestration

**Target audience:** Power users, tool builders

**Characteristics:**
- Complex workflows
- Reusable patterns
- ~300+ lines each
- May include helper modules

**Examples:**
- `unified_runner.py` - Multi-benchmark orchestration
- `orchestrate_benchmarks.py` - Parallel execution
- `generate_report.py` - Automated reporting

### 5. Dry Run (`dry_run/`)

**Purpose:** Preview mode without execution

**Target audience:** All levels, CI/CD, cost-conscious users

**Characteristics:**
- Demonstrates dry-run capabilities
- Analysis and validation patterns
- ~100-200 lines each

**Examples:**
- `basic_dry_run.py` - Simple preview
- `ci_validation.py` - CI/CD integration
- `documentation_generator.py` - Auto-generate docs

### 6. Notebooks (`notebooks/`)

**Purpose:** Interactive exploration and presentation

**Target audience:** Data analysts, presentation creators

**Characteristics:**
- Jupyter notebooks (.ipynb)
- Markdown cells explain each step
- Visualization and analysis
- Platform-specific and generic

**Examples:**
- `duckdb_tpch.ipynb` - Interactive local benchmark
- `analyze_results.ipynb` - Results visualization
- `compare_platforms.ipynb` - Cross-platform comparison

### 7. Tunings (`tunings/`)

**Purpose:** Configuration templates and tuning files

**Target audience:** All levels, reference material

**Characteristics:**
- YAML configuration files
- Copy-paste ready
- The directory README explains each setting
- Platform-specific and benchmark-specific

**Structure:**
```
tunings/
├── platforms/        # Platform connection configs
│   ├── databricks.yaml
│   ├── bigquery.yaml
│   └── ...
└── benchmarks/       # Benchmark tuning configs
    ├── tpch_databricks.yaml
    ├── tpcds_bigquery.yaml
    └── ...
```

---

## Checklist for New Examples

Before submitting a pull request:

- [ ] File follows naming convention
- [ ] Description, use cases and prerequisites in the directory README
- [ ] Copyright notice included
- [ ] Shebang line (`#!/usr/bin/env python3`)
- [ ] CLI arguments supported (`argparse`)
- [ ] `--help` flag works
- [ ] `--dry-run` flag supported (where applicable)
- [ ] Error handling with user-friendly messages
- [ ] Status indicators (✅ ❌ ⚠️) in output
- [ ] Code formatted with `ruff format`
- [ ] Linting passes (`ruff check`)
- [ ] Type hints on function signatures
- [ ] README.md updated in example category directory
- [ ] `examples/MAPPING.md` updated
- [ ] Documentation links to example (if applicable)
- [ ] Manually tested end-to-end
- [ ] Tested with invalid inputs/missing credentials

---

## Common Patterns

### CLI Argument Parsing

```python
def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", type=float, default=0.01, help="Scale factor")
    parser.add_argument("--dry-run", metavar="DIR", help="Dry-run mode output directory")
    parser.add_argument("--force", action="store_true", help="Force regeneration")
    return parser.parse_args()
```

### Dry-Run Support

```python
if args.dry_run:
    from benchbox.cli.dryrun import DryRunExecutor
    dry_run = DryRunExecutor(Path(args.dry_run))
    result = dry_run.execute_dry_run(benchmark_config, system_profile, database_config)
    print(f"✅ Dry-run artifacts saved to: {args.dry_run}")
else:
    result = executor.run_benchmark(config)
```

### Platform Credential Validation

```python
import os

def validate_databricks_credentials():
    required = ["DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_HTTP_PATH"]
    missing = [var for var in required if not os.getenv(var)]

    if missing:
        print(f"❌ Missing required environment variables: {', '.join(missing)}")
        print("\nSet them like this:")
        print("  export DATABRICKS_HOST='https://your-workspace.cloud.databricks.com'")
        print("  export DATABRICKS_TOKEN='your-token'")
        print("  export DATABRICKS_HTTP_PATH='/sql/1.0/warehouses/your-warehouse-id'")
        sys.exit(1)
```

---

## Getting Help

**Questions about examples?**
- Check `examples/README.md` for overview
- Check `examples/MAPPING.md` for doc-to-example mapping
- Search existing examples for similar patterns

**Found a bug or unclear example?**
- Open issue at https://github.com/joeharris76/benchbox/issues
- Include: example filename, command run, error message

**Want to contribute a new example?**
- Follow this guide
- Open pull request with clear description
- Reference related documentation or issues

---

## Maintenance Guidelines

### Regular Updates

- **Quarterly:** Review all examples for API changes
- **On release:** Test getting_started examples end-to-end
- **On platform changes:** Update relevant tunings/ configs

### Deprecation Process

When removing or significantly changing an example:

1. **Add deprecation notice** to the directory README (1 release cycle)
2. **Update documentation** to point to replacement
3. **Remove file** and update MAPPING.md
4. **Update navigation** in relevant READMEs

---

## Questions?

Contact: Joe Harris (joe@joeworksllc.com)
Repository: https://github.com/joeharris76/benchbox
License: MIT
