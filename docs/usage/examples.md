<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Examples

```{tags} beginner, guide, python-api
```

Practical code examples for common BenchBox usage patterns, database integrations, and performance testing scenarios.

> ⚠️ **IMPORTANT: Documentation Snippets vs. Runnable Examples**
>
> **This page contains *simplified code snippets* for documentation and explanation purposes.**
>
> - These snippets may be incomplete or use simplified APIs for clarity
> - They are intended to illustrate concepts, not necessarily run as-is
> - Copy-pasting may require adjustments
>
> **For *complete, tested, runnable code*, use the Examples Directory:**
>
> - **[Examples Directory Guide](examples-directory.md)** - 40+ runnable examples organized by difficulty
> - **[Getting Started Examples](../../examples/getting_started/README.md)** - Zero to working in 5 minutes
> - **[Feature Examples](../../examples/features/README.md)** - Learn specific capabilities
> - **[Use Case Patterns](../../examples/use_cases/README.md)** - Real-world solutions
> - **[Notebook Examples](../../examples/notebooks/README.md)** - Interactive platform guides
> - **[Configuration Templates](../../examples/tunings/README.md)** - Platform setup and tuning
> - **[Workflow Patterns](../../examples/programmatic/README.md)** - 8 proven patterns
>
> **Quick Decision:**
>
> - Need to **understand a concept**? → Read snippets on this page
> - Need to **run working code**? → Use files in `examples/` directory

---

## Basic Usage Examples

### Getting Started Scripts

The repository now ships focused "Hello World" scripts under
`examples/getting_started/`. They complement (not replace) the advanced
`examples/unified_runner.py` entry point. Start with the DuckDB scripts
for a zero-configuration success case and move to the cloud examples
when you are ready to supply credentials. Available local examples:

- `duckdb_tpch_power.py` - TPC-H analytical benchmark (22 queries)
- `duckdb_tpcds_power.py` - TPC-DS complex queries (99 queries)
- `duckdb_nyctaxi.py` - NYC Taxi trip analytics (25 queries on real data)
- `duckdb_tsbs_devops.py` - TSBS DevOps time-series (18 monitoring queries)

See `examples/getting_started/README.md` for the progressive walkthrough. All
scripts accept `--dry-run OUTPUT_DIR` so you can export JSON/YAML
previews without generating data or running queries.

### Complete DuckDB Example (Recommended)

This example shows the complete workflow using DuckDB, the recommended database for BenchBox. It uses an in-memory connection for speed, a scale factor of 0.01 (about a 10 MB dataset), DuckDB's CSV reader to load data, and runs the first 5 queries:

```python
import duckdb
from benchbox import TPCH
import time

conn = duckdb.connect(":memory:")

tpch = TPCH(scale_factor=0.01, output_dir="./tpch_data")

print("Generating TPC-H data...")
tpch.generate_data()
print(f"Generated {len(tpch.tables)} tables")

ddl = tpch.get_create_tables_sql()
conn.execute(ddl)

print("Loading data into DuckDB...")
for table, path in tpch.tables.items():
    conn.execute(f"INSERT INTO {table} SELECT * FROM read_csv('{path}', delim='|', header=false)")
    print(f"  Loaded {table}")

print("\nExecuting TPC-H queries:")
for query_id in range(1, 6):
    query_sql = tpch.get_query(query_id)

    start_time = time.time()
    result = conn.execute(query_sql).fetchall()
    execution_time = time.time() - start_time

    print(f"Query {query_id}: {execution_time * 1000:.1f} ms ({len(result)} rows)")

conn.close()
print("Benchmark complete!")
```

### Simple TPC-H Example

The scale factor of 0.01 produces about a 10 MB dataset. The data file sizes are printed in MB:

```python
from benchbox import TPCH
from pathlib import Path
import time

tpch = TPCH(scale_factor=0.01, output_dir="./tpch_data")

print("Generating TPC-H data...")
start_time = time.time()
tpch.generate_data()
generation_time = time.time() - start_time

print(f"Generated {len(tpch.tables)} tables in {generation_time:.2f} seconds")
for table, path in tpch.tables.items():
    file_size = Path(path).stat().st_size / (1024 * 1024)
    print(f"  {table}: {file_size:.2f} MB")

print("\nSample queries:")
for query_id in [1, 3, 6]:
    query = tpch.get_query(query_id)
    print(f"Query {query_id}: {len(query)} characters")
```

### Multiple Benchmarks Comparison

```python
from benchbox import TPCH, SSB, ReadPrimitives

benchmarks = {
    "TPC-H": TPCH(scale_factor=0.01),
    "SSB": SSB(scale_factor=0.01),
    "Primitives": ReadPrimitives(scale_factor=0.01)
}

print("Benchmark Comparison:")
print(f"{'Benchmark':<12} {'Queries':<8} {'Tables':<8} {'Purpose'}")
print("-" * 50)

for name, benchmark in benchmarks.items():
    queries = benchmark.get_queries()
    schema = benchmark.get_schema()
    table_count = len(schema) if isinstance(schema, list) else len(schema.keys())

    if name == "TPC-H":
        purpose = "Decision Support"
    elif name == "SSB":
        purpose = "Star Schema OLAP"
    else:
        purpose = "Primitive Operations"

    print(f"{name:<12} {len(queries):<8} {table_count:<8} {purpose}")
```

### Query Translation Example

This example translates a query to several dialects, using supported platforms:

```python
from benchbox import TPCH

tpch = TPCH(scale_factor=0.01)

original_query = tpch.get_query(1)
print("Original Query (ANSI SQL):")
print(original_query[:200] + "...\n")

dialects = ["duckdb", "clickhouse", "snowflake", "bigquery"]

for dialect in dialects:
    translated = tpch.get_query(1, dialect=dialect)
    print(f"{dialect.upper()} version:")
    print(translated[:150] + "...\n")
```

---

## Selective Query Execution

During development and debugging, running full benchmark suites is time-prohibitive. The `--queries` parameter enables selective execution of specific queries in user-defined order, dramatically accelerating development workflows.

### CLI Usage

The commands below do the following, in order:
- Run a single query for debugging (about 5 seconds, compared with 11 minutes for the full TPC-H suite).
- Run multiple queries in a specific order.
- Debug a failing query with verbose output.
- Test critical production queries only.

```bash
benchbox run --platform duckdb --benchmark tpch --queries "6" --phases power

benchbox run --platform sqlite --benchmark tpch --queries "1,6,17" --phases power

benchbox run --platform duckdb --benchmark tpcds --queries "42" --verbose --phases power

benchbox run --platform clickhouse-local --benchmark tpch --queries "1,6,12,17" --phases power
```

### Programmatic Usage

The CLI `--queries` parameter maps to `query_subset` in the programmatic API (`RunConfig.query_subset`). Query execution itself runs through `benchbox run`; see the Complete DuckDB Example above for the data loading pattern.

### Use Cases

**Rapid Development**: Test single queries during optimization work (130× faster iteration for TPC-H).

**Regression Testing**: Focus on production-critical queries without running entire suites.

**Debugging**: Isolate failing queries with verbose logging and detailed output.

**TPC Compliance Note**: Using `--queries` breaks official TPC-H/TPC-DS compliance by overriding stream permutations. Suitable for development and internal testing, not for official benchmark reporting.

---

## Power Run and Concurrent Query Examples

BenchBox provides advanced execution modes for systematic performance testing with statistical confidence and throughput analysis.

### Power Run Iterations Example

Execute multiple test iterations to gather statistical confidence. Power iterations are a plain loop over stream IDs. Each `TPCHPowerTest` run executes the 22 queries in that stream's permutation against a real connection and reports Power@Size. The loop runs 5 test iterations, and validation is off (`validation=False`) because answer sets exist for stream 0 only:

```python
import statistics

import duckdb

from benchbox import TPCH
from benchbox.core.tpch.power_test import TPCHPowerTest

tpch = TPCH(scale_factor=0.01, output_dir="./tpch_data")

connection = duckdb.connect(":memory:")
tpch.generate_data()
connection.execute(tpch.get_create_tables_sql())
for table, path in tpch.tables.items():
    connection.execute(
        f"INSERT INTO {table} "
        f"SELECT * FROM read_csv('{path}', delim='|', header=false)"
    )

print("Starting power run with multiple iterations...")
power_values = []
for stream_id in range(5):
    power_test = TPCHPowerTest(
        benchmark=tpch,
        connection=connection,
        scale_factor=0.01,
        stream_id=stream_id,
        verbose=True,
        validation=False,
    )
    iteration = power_test.run()
    assert iteration.success, iteration.errors
    power_values.append(iteration.power_at_size)

print(f"\nPower Run Results:")
print(f"Total iterations: {len(power_values)}")
print(f"\nStatistical Analysis:")
print(f"Average Power@Size: {statistics.mean(power_values):.2f}")
print(f"Median Power@Size: {statistics.median(power_values):.2f}")
if len(power_values) > 1:
    print(f"Standard Deviation: {statistics.stdev(power_values):.2f}")
print(f"Min Power@Size: {min(power_values):.2f}")
print(f"Max Power@Size: {max(power_values):.2f}")

print(f"\n Per-Iteration Results:")
for i, power_at_size in enumerate(power_values, 1):
    print(f"Iteration {i}: Power@Size={power_at_size:.2f}")
```

### Concurrent Query Execution Example

Execute queries concurrently to test throughput and scalability. One throughput test owns all of its streams: the connection factory hands each stream its session, and `StreamRunner` executes them concurrently with fail-closed accounting. The example generates and loads the data before opening throughput sessions, and runs 3 concurrent streams (`num_streams=3`):

```python
import duckdb

from benchbox import TPCH
from benchbox.core.tpch.throughput_test import TPCHThroughputTest

tpch = TPCH(scale_factor=0.01, output_dir="./tpch_data")

connection = duckdb.connect(":memory:")

tpch.generate_data()
connection.execute(tpch.get_create_tables_sql())
for table, path in tpch.tables.items():
    connection.execute(
        f"INSERT INTO {table} "
        f"SELECT * FROM read_csv('{path}', delim='|', header=false)"
    )

throughput_test = TPCHThroughputTest(
    benchmark=tpch,
    connection_factory=lambda: connection.cursor(),
    scale_factor=0.01,
    num_streams=3,
    verbose=True,
)

print("Starting concurrent query execution...")
result = throughput_test.run()
assert result.success, result.errors

print(f"\nConcurrent Query Results:")
print(f"Streams: {result.streams_successful}/{result.streams_executed} successful")
print(f"Total duration: {result.total_time:.3f}s")

print(f"\n Per-Stream Results:")
for stream_result in result.stream_results:
    print(f"Stream {stream_result.stream_id}: "
          f"{stream_result.queries_successful}/{stream_result.queries_executed} successful, "
          f"Duration: {stream_result.duration:.3f}s")
```

### Performance Profile Example

Use pre-configured performance profiles for different testing scenarios:

```python
from benchbox.utils import ExecutionConfigHelper

config_helper = ExecutionConfigHelper()

profiles = ['quick', 'standard', 'thorough', 'stress']

for profile_name in profiles:
    profile_config = config_helper.create_performance_profile(profile_name)
    power_config = profile_config['power_run']
    concurrent_config = profile_config['concurrent_queries']

    print(f"\n{profile_name.title()} Profile:")
    print(f"  Power run iterations: {power_config['iterations']} "
          f"(+{power_config['warm_up_iterations']} warm-up)")
    print(f"  Timeout per iteration: {power_config['timeout_per_iteration_minutes']} minutes")
    print(f"  Concurrent queries: {'enabled' if concurrent_config['enabled'] else 'disabled'}")
    if concurrent_config['enabled']:
        print(f"  Max concurrent streams: {concurrent_config['max_concurrent']}")

print(f"\nApplying 'thorough' profile...")
config_helper.apply_performance_profile('thorough')

summary = config_helper.get_execution_summary()
print(f"Current power run settings: {summary['power_run']['total_iterations']} total iterations")
print(f"Current concurrent settings: {summary['concurrent_queries']['max_streams']} max streams")
```

### System Optimization Example

Automatically optimize settings based on system resources:

```python
import psutil
from benchbox.utils import ExecutionConfigHelper

config_helper = ExecutionConfigHelper()

cpu_cores = psutil.cpu_count()
memory_gb = psutil.virtual_memory().total / (1024**3)

print(f"System specs: {cpu_cores} CPU cores, {memory_gb:.1f}GB RAM")

print("Optimizing configuration for system resources...")
config_helper.optimize_for_system(cpu_cores=cpu_cores, memory_gb=memory_gb)

summary = config_helper.get_execution_summary()

print(f"\nOptimized Settings:")
print(f"Power Run:")
print(f"  - Timeout per iteration: {summary['power_run']['settings']['timeout_per_iteration_minutes']} minutes")
print(f"  - Total estimated duration: {summary['power_run']['estimated_duration_minutes']} minutes")

print(f"Concurrent Queries:")
print(f"  - Max concurrent streams: {summary['concurrent_queries']['settings']['max_concurrent']}")
print(f"  - Query timeout: {summary['concurrent_queries']['settings']['query_timeout_seconds']} seconds")

print(f"General:")
print(f"  - Max workers: {summary['general']['max_workers']}")
print(f"  - Memory limit: {summary['general']['memory_limit_gb']}GB")
```

### Configuration Management Example

Load, modify, and save execution configurations:

```python
from benchbox.cli.config import ConfigManager
from benchbox.utils import ExecutionConfigHelper, create_sample_execution_config
from pathlib import Path

sample_path = Path("sample_benchbox_config.yaml")
print(f"Creating sample configuration at: {sample_path}")
create_sample_execution_config(sample_path)

config_manager = ConfigManager(config_path=sample_path)
config_helper = ExecutionConfigHelper(config_manager)

print("Configuring custom power run settings...")
power_settings = config_helper.get_power_run_settings()
power_settings.iterations = 7
power_settings.warm_up_iterations = 2
power_settings.timeout_per_iteration_minutes = 90
config_helper.update_power_run_settings(power_settings)

print("Configuring custom concurrent query settings...")
concurrent_settings = config_helper.get_concurrent_queries_settings()
concurrent_settings.enabled = True
concurrent_settings.max_concurrent = 4
concurrent_settings.query_timeout_seconds = 450
config_helper.update_concurrent_queries_settings(concurrent_settings)

print("Validating configuration...")
is_valid = config_helper.validate_execution_config()
if is_valid:
    print("✅ Configuration is valid")

    config_helper.save_config()
    print(f"✅ Configuration saved to {sample_path}")
else:
    print("❌ Configuration validation failed")

summary = config_helper.get_execution_summary()
print(f"\nExecution Summary:")
print(f"Power Run: {summary['power_run']['total_iterations']} total iterations, "
      f"{summary['power_run']['estimated_duration_minutes']} min estimated")
print(f"Concurrent: {'enabled' if summary['concurrent_queries']['enabled'] else 'disabled'}, "
      f"{summary['concurrent_queries']['max_streams']} max streams")

sample_path.unlink(missing_ok=True)
```

### Complete Workflow Example

Combine all features for systematic performance testing. The example applies the `standard` profile (balanced testing), then optimizes for the current system. Its three phases are power run testing (multiple iterations for statistical confidence), concurrent query testing (throughput and scalability), and a comprehensive analysis:

```python
import statistics

import duckdb

from benchbox import TPCH
from benchbox.core.tpch.power_test import TPCHPowerTest
from benchbox.core.tpch.throughput_test import TPCHThroughputTest
from benchbox.utils import ExecutionConfigHelper

config_helper = ExecutionConfigHelper()

config_helper.apply_performance_profile('standard')

import psutil
cpu_cores = psutil.cpu_count()
memory_gb = psutil.virtual_memory().total / (1024**3)
config_helper.optimize_for_system(cpu_cores, memory_gb)

tpch = TPCH(scale_factor=0.01, output_dir="./tpch_data")
connection = duckdb.connect(":memory:")
tpch.generate_data()
connection.execute(tpch.get_create_tables_sql())
for table, path in tpch.tables.items():
    connection.execute(
        f"INSERT INTO {table} "
        f"SELECT * FROM read_csv('{path}', delim='|', header=false)"
    )

print("Starting systematic benchmark testing...")

print("\nPhase 1: Power Run Testing")
power_values = []
for stream_id in range(3):
    power_test = TPCHPowerTest(benchmark=tpch, connection=connection,
                               scale_factor=0.01, stream_id=stream_id,
                               validation=False)
    iteration = power_test.run()
    assert iteration.success, iteration.errors
    power_values.append(iteration.power_at_size)

print(f"Power Run Results:")
print(f"  - Average Power@Size: {statistics.mean(power_values):.2f}")
print(f"  - Std Deviation: {statistics.stdev(power_values):.2f}")
print(f"  - Confidence: {len(power_values)}/{len(power_values)} iterations")

print("\nPhase 2: Concurrent Query Testing")
throughput_test = TPCHThroughputTest(benchmark=tpch,
                                     connection_factory=lambda: connection.cursor(),
                                     scale_factor=0.01, num_streams=3, verbose=False)

concurrent_result = throughput_test.run()
assert concurrent_result.success, concurrent_result.errors

successful = sum(s.queries_successful for s in concurrent_result.stream_results)
executed = sum(s.queries_executed for s in concurrent_result.stream_results)

print(f"Concurrent Query Results:")
print(f"  - Streams: {concurrent_result.streams_successful}/{concurrent_result.streams_executed}")
print(f"  - Success Rate: {successful}/{executed}")
print(f"  - Concurrent Streams: {len(concurrent_result.stream_results)}")

print("\n Phase 3: Performance Analysis")
print(f"Benchmark Summary:")
print(f"  - Scale Factor: 0.01")
print(f"  - Single Stream Performance: {statistics.mean(power_values):.2f} Power@Size")
print(f"  - Multi Stream Streams: {concurrent_result.streams_successful}/{concurrent_result.streams_executed}")
print(f"  - Performance Consistency: ±{statistics.stdev(power_values):.2f} Power@Size")

print("\n✅ Comprehensive benchmark testing completed!")
```

---

## Dry Run Examples

The dry run feature allows you to preview benchmark configurations, queries, and resource estimates without executing them. This is essential for development, debugging, and documentation.

### Basic Dry Run Preview

```python
from pathlib import Path
from benchbox.cli.dryrun import DryRunExecutor
from benchbox.cli.system import SystemProfiler
from benchbox.core.config import BenchmarkConfig
from benchbox.core.schemas import DatabaseConfig

dry_run = DryRunExecutor(Path("./preview"))
result = dry_run.execute_dry_run(
    benchmark_config=BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=0.01),
    system_profile=SystemProfiler().get_system_profile(),
    database_config=DatabaseConfig(type="duckdb", name="DuckDB"),
)

print(f"Extracted {len(result.queries)} queries")
print(f"Memory estimate: {result.estimated_resources.get('estimated_memory_usage_mb')} MB")
```

**Full example:** `examples/dry_run/basic_dry_run.py`

### CLI Dry Run Examples

The examples below are a basic TPC-H dry run, a dry run with tuning enabled, and a preview of multiple benchmarks:

```bash
benchbox run --dry-run ./tpch_preview \
  --platform duckdb \
  --benchmark tpch \
  --scale 0.1

benchbox run --dry-run ./tpcds_tuned_preview \
  --platform duckdb \
  --benchmark tpcds \
  --scale 0.01 \
  --tuning tuned

for benchmark in tpch tpcds ssb read_primitives; do
  echo "Previewing $benchmark..."
  benchbox run --dry-run ./preview_${benchmark} \
    --platform duckdb \
    --benchmark $benchmark \
    --scale 0.01
done
```

### Dry Run Output Analysis

Usage: `analyze_dry_run_output("./tpch_preview")`.

```python
import json
from pathlib import Path

def analyze_dry_run_output(dry_run_dir: str):
    candidates = sorted(Path(dry_run_dir).glob("*.json"))
    with open(candidates[0], 'r') as f:
        summary = json.load(f)

    system = summary["system_profile"]
    benchmark = summary["benchmark_config"]

    print(f"System: {system['os_name']} ({system['memory_total_gb']:.1f} GB)")
    print(f"Benchmark: {benchmark['name']} (scale: {benchmark['scale_factor']})")

    query_dirs = sorted(Path(dry_run_dir).glob("*_queries_*"))
    if query_dirs:
        query_files = list(query_dirs[0].glob("*.sql"))

        for query_file in query_files[:3]:
            with open(query_file, 'r') as f:
                content = f.read()
                joins = content.upper().count('JOIN')
                complexity = "High" if joins > 3 else "Medium" if joins > 1 else "Low"
                print(f"  {query_file.name}: {joins} joins, complexity: {complexity}")

```

**Full example:** `examples/dry_run/analyze_dry_run_output.py`

### Query Extraction and Validation

The `lint_with_sqlfluff` function requires `pip install sqlfluff`.

```python
from pathlib import Path
import subprocess

def validate_extracted_queries(dry_run_dir: str, target_dialect: str = "duckdb"):
    queries_dir = sorted(Path(dry_run_dir).glob("*_queries_*"))[0]
    valid_queries = 0

    for query_file in queries_dir.glob("*.sql"):
        with open(query_file, 'r') as f:
            content = f.read()

        if (content.strip() and
            'SELECT' in content.upper() and
            content.count('(') == content.count(')')):
            valid_queries += 1
            print(f"✅ {query_file.name}")
        else:
            print(f"❌ {query_file.name} has issues")

    print(f"Valid queries: {valid_queries}/{len(list(queries_dir.glob('*.sql')))}")

def lint_with_sqlfluff(dry_run_dir: str):
    queries_dir = sorted(Path(dry_run_dir).glob("*_queries_*"))[0]
    for query_file in queries_dir.glob("*.sql"):
        result = subprocess.run([
            "sqlfluff", "lint", str(query_file), "--dialect", "duckdb"
        ], capture_output=True)
        status = "✅" if result.returncode == 0 else "⚠️️"
        print(f"{status} {query_file.name}")
```

**Full example:** `examples/dry_run/query_validation.py`

### Documentation Generation with Dry Run

```python
import subprocess
import json
from pathlib import Path

def generate_benchmark_documentation(benchmark_name: str, output_dir: str = "./docs"):
    dry_run_dir = f"./temp_dry_run_{benchmark_name}"

    subprocess.run([
        "benchbox", "run", "--dry-run", dry_run_dir,
        "--platform", "duckdb", "--benchmark", benchmark_name, "--scale", "0.01"
    ], check=True)

    candidates = sorted(Path(dry_run_dir).glob("*.json"))
    with open(candidates[0], 'r') as f:
        summary = json.load(f)

    doc_content = [
        f"# {benchmark_name.upper()} Benchmark Documentation",
        f"Generated from BenchBox dry run analysis.\n",
        "## Overview",
        f"- **Query Count**: {len(summary.get('queries', {}))}",
    ]

    if "estimated_resources" in summary:
        resources = summary["estimated_resources"]
        doc_content.extend([
            "\n## Resource Requirements",
            f"- **Memory**: ~{resources.get('estimated_memory_usage_mb', 'N/A')} MB",
            f"- **Data**: ~{resources.get('estimated_data_size_mb', 'N/A')} MB"
        ])

    if "queries" in summary:
        queries = summary["queries"]
        doc_content.extend(["\n## Queries", ""])
        for query_id in sorted(queries.keys()):
            doc_content.append(f"- Query {query_id}")

    doc_file = Path(output_dir) / f"{benchmark_name.lower()}_benchmark.md"
    doc_file.parent.mkdir(parents=True, exist_ok=True)
    doc_file.write_text('\n'.join(doc_content))

    import shutil
    shutil.rmtree(dry_run_dir)

    return doc_file

for benchmark in ["tpch", "read_primitives", "ssb"]:
    generate_benchmark_documentation(benchmark, "./generated_docs")
```

**Full example:** `examples/dry_run/documentation_generator.py`

### CI/CD Integration with Dry Run

```python
import subprocess
import json
import sys
from pathlib import Path

def validate_benchmark_changes():
    critical_benchmarks = [
        {"name": "tpch", "scale": 0.01},
        {"name": "read_primitives", "scale": 0.01},
        {"name": "ssb", "scale": 0.01}
    ]

    validation_results = []

    for benchmark in critical_benchmarks:
        print(f"Validating {benchmark['name']}...")
        dry_run_dir = f"./ci_validation_{benchmark['name']}"

        try:
            subprocess.run([
                "benchbox", "run", "--dry-run", dry_run_dir,
                "--platform", "duckdb",
                "--benchmark", benchmark['name'],
                "--scale", str(benchmark['scale'])
            ], capture_output=True, text=True, check=True)

            candidates = sorted(Path(dry_run_dir).glob("*.json"))
            with open(candidates[0], 'r') as f:
                summary = json.load(f)

            queries = summary.get('queries', {})
            schema_sql = summary.get('schema_sql', '')

            validation_passed = (
                len(queries) > 0 and
                len(schema_sql) > 0 and
                'estimated_resources' in summary
            )

            validation_results.append({
                "benchmark": benchmark['name'],
                "passed": validation_passed
            })

            status = "✅" if validation_passed else "❌"
            print(f"  {status} {len(queries)} queries")

        except Exception as e:
            validation_results.append({"benchmark": benchmark['name'], "passed": False})
            print(f"  ❌ Error: {e}")
        finally:
            import shutil
            if Path(dry_run_dir).exists():
                shutil.rmtree(dry_run_dir)

    passed = all(r['passed'] for r in validation_results)
    print(f"Validation {'✅ PASSED' if passed else '❌ FAILED'}")
    return passed

if __name__ == "__main__":
    sys.exit(0 if validate_benchmark_changes() else 1)
```

**Full example:** `examples/dry_run/ci_validation.py`

---

## Database Integration Examples

### DuckDB Complete Example

This example uses a scale factor of 0.1 (about a 100 MB dataset):

```python
import duckdb
from benchbox import TPCH
import time

def benchmark_duckdb_tpch():

    conn = duckdb.connect(":memory:")
    tpch = TPCH(scale_factor=0.1, output_dir="./tpch_data")

    print("Setting up TPC-H benchmark with DuckDB...")

    print("1. Generating data...")
    data_files = tpch.generate_data()

    print("2. Creating tables...")
    schema_sql = tpch.get_create_tables_sql()
    for statement in schema_sql.split(';'):
        if statement.strip():
            conn.execute(statement)

    print("3. Loading data...")
    for table, path in tpch.tables.items():
        start_load = time.time()
        conn.execute(f"""
            INSERT INTO {table}
            SELECT * FROM read_csv('{path}', header=false, delim='|')
        """)
        load_time = time.time() - start_load

        row_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"   {table}: {row_count:,} rows ({load_time:.2f}s)")

    print("4. Running TPC-H queries...")
    results = {}

    for query_id in range(1, 23):
        print(f"   Running Query {query_id}...", end="")

        query_sql = tpch.get_query(query_id)
        start_time = time.time()

        try:
            result = conn.execute(query_sql).fetchall()
            execution_time = time.time() - start_time
            results[query_id] = {
                "execution_time": execution_time,
                "row_count": len(result),
                "success": True
            }
            print(f" {execution_time:.3f}s ({len(result)} rows)")

        except Exception as e:
            results[query_id] = {
                "execution_time": None,
                "error": str(e),
                "success": False
            }
            print(f" FAILED: {e}")

    successful_queries = [q for q, r in results.items() if r["success"]]
    total_time = sum(r["execution_time"] for r in results.values() if r["success"])

    print(f"\nSummary:")
    print(f"  Successful queries: {len(successful_queries)}/22")
    print(f"  Total execution time: {total_time:.2f}s")
    print(f"  Average per query: {total_time/len(successful_queries):.3f}s")

    return results

if __name__ == "__main__":
    results = benchmark_duckdb_tpch()
```

> **📁 Complete, tested example:** See [`examples/getting_started/local/duckdb_tpch_missing.py`](../../examples/getting_started/local/duckdb_tpch_missing.py) for teh production-ready DuckDB example with CLI support and error handling.
>
> **📁 Additional benchmark examples:**
>
> - [`examples/getting_started/local/duckdb_nyctaxi.py`](../../examples/getting_started/local/duckdb_nyctaxi.py) - NYC Taxi trip analytics (real-world data patterns)
> - [`examples/getting_started/local/duckdb_tsbs_devops.py`](../../examples/getting_started/local/duckdb_tsbs_devops.py) - TSBS DevOps time-series benchmark

### ClickHouse Integration Example

The example loads data with ClickHouse's optimized CSV loading (`INSERT ... FORMAT CSV`), then optimizes the tables and tests a query.

> **Note**: This example needs a reachable ClickHouse server. For PostgreSQL, use `--platform postgresql`; MySQL has no platform adapter yet.

```python
from clickhouse_driver import Client
from benchbox import TPCH
from pathlib import Path

def setup_clickhouse_tpch(host: str = 'localhost', port: int = 9000):

    client = Client(host=host, port=port)

    tpch = TPCH(scale_factor=0.01, output_dir="./tpch_data")
    try:
        print("Generating TPC-H data...")
        tpch.generate_data()
        print("Creating ClickHouse database...")
        client.execute("CREATE DATABASE IF NOT EXISTS benchbox")
        client.execute("USE benchbox")

        print("Creating ClickHouse schema...")
        schema_sql = tpch.get_create_tables_sql()
        for statement in schema_sql.split(';'):
            if statement.strip():
                client.execute(statement)

        print("Loading data into ClickHouse...")
        for table, path in tpch.tables.items():
            print(f"  Loading {table}...")

            with open(path, 'rb') as f:
                client.execute(
                    f"INSERT INTO {table} FORMAT CSV",
                    f.read()
                )

            row_count = client.execute(f"SELECT COUNT(*) FROM {table}")[0][0]
            print(f"    {row_count:,} rows loaded")

        print("Optimizing tables...")
        for table in tpch.tables:
            client.execute(f"OPTIMIZE TABLE {table} FINAL")

        print("ClickHouse TPC-H setup complete!")

        print("Testing Query 1...")
        query_1 = tpch.get_query(1, dialect="clickhouse")
        result = client.execute(query_1)
        print(f"Query 1 returned {len(result)} rows")

    except Exception as e:
        print(f"Error: {e}")
        raise

if __name__ == "__main__":
    setup_clickhouse_tpch(host='localhost', port=9000)
```

> **📁 Complete, tested example:** For database-specific integration patterns, see platform examples in [`examples/getting_started/cloud/`](../../examples/getting_started/cloud/) (Databricks and BigQuery with credential handling) and platform configuration templates in [`examples/tunings/`](../../examples/tunings/) (production-ready YAML configs).

### SQLite Example with Performance Measurement

The example runs a subset of queries because SQLite may not support all TPC-H features. The queries `[1, 3, 6, 12]` are simpler ones that work well with SQLite:

```python
import sqlite3
from benchbox import TPCH
import time
import csv

def benchmark_sqlite_tpch():

    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()

    tpch = TPCH(scale_factor=0.01, output_dir="./tpch_data")

    print("Setting up TPC-H in SQLite...")
    tpch.generate_data()

    schema_sql = tpch.get_create_tables_sql()
    cursor.executescript(schema_sql)

    total_rows = 0
    for table, path in tpch.tables.items():
        print(f"Loading {table}...", end="")
        start_time = time.time()

        with open(path, 'r') as f:
            reader = csv.reader(f, delimiter='|')

            cursor.execute(f"PRAGMA table_info({table})")
            columns = [col[1] for col in cursor.fetchall()]
            placeholders = ','.join(['?' for _ in columns])

            rows_inserted = 0
            for row in reader:
                row = [None if x == '' else x for x in row]
                cursor.execute(f"INSERT INTO {table} VALUES ({placeholders})", row)
                rows_inserted += 1

            load_time = time.time() - start_time
            total_rows += rows_inserted
            print(f" {rows_inserted:,} rows ({load_time:.2f}s)")

    print(f"Total: {total_rows:,} rows loaded")

    print("Creating indexes...")
    index_commands = [
        "CREATE INDEX idx_lineitem_orderkey ON lineitem(l_orderkey)",
        "CREATE INDEX idx_orders_custkey ON orders(o_custkey)",
        "CREATE INDEX idx_customer_nationkey ON customer(c_nationkey)"
    ]

    for cmd in index_commands:
        cursor.execute(cmd)

    conn.commit()

    test_queries = [1, 3, 6, 12]
    results = {}

    print("Running selected TPC-H queries...")
    for query_id in test_queries:
        print(f"  Query {query_id}...", end="")

        try:
            query_sql = tpch.get_query(query_id, dialect="sqlite")

            start_time = time.time()
            cursor.execute(query_sql)
            result = cursor.fetchall()
            execution_time = time.time() - start_time

            results[query_id] = {
                "execution_time": execution_time,
                "row_count": len(result),
                "success": True
            }
            print(f" {execution_time:.3f}s ({len(result)} rows)")

        except Exception as e:
            results[query_id] = {
                "error": str(e),
                "success": False
            }
            print(f" FAILED: {e}")

    conn.close()
    return results

if __name__ == "__main__":
    results = benchmark_sqlite_tpch()
```

> **📁 Complete, tested example:** For platform-specific examples with CLI support and error handling, see files in [`examples/getting_started/`](../../examples/getting_started/) (beginner-friendly walkthroughs) and [`examples/features/`](../../examples/features/) (focused feature demonstrations).

---

## Performance Testing Examples

### Comprehensive Performance Analysis

In `run_scale_factor_analysis`, the data size is reported in MB. The example generates data, loads it into the connection, and times each step so that `data_load_time` measures real loading.

```python
from pathlib import Path
from benchbox import TPCH, ReadPrimitives
import time
import psutil
import statistics

class PerformanceBenchmark:
    def __init__(self):
        self.results = {}

    def measure_resource_usage(self, func):
        def wrapper(*args, **kwargs):
            process = psutil.Process()
            initial_memory = process.memory_info().rss / 1024 / 1024
            initial_cpu_percent = process.cpu_percent()

            start_time = time.time()
            result = func(*args, **kwargs)
            end_time = time.time()

            final_memory = process.memory_info().rss / 1024 / 1024
            final_cpu_percent = process.cpu_percent()

            return {
                "result": result,
                "execution_time": end_time - start_time,
                "memory_used_mb": final_memory - initial_memory,
                "cpu_percent": final_cpu_percent
            }
        return wrapper

    def run_query_performance_test(self, benchmark, connection, query_ids, iterations=5):
        results = {}

        for query_id in query_ids:
            print(f"Testing Query {query_id} ({iterations} iterations)...")
            query_results = []

            for i in range(iterations):
                query_sql = benchmark.get_query(query_id)

                start_time = time.time()
                cursor = connection.execute(query_sql)
                result = cursor.fetchall()
                execution_time = time.time() - start_time

                query_results.append({
                    "iteration": i + 1,
                    "execution_time": execution_time,
                    "row_count": len(result)
                })

            times = [r["execution_time"] for r in query_results]
            results[query_id] = {
                "iterations": query_results,
                "avg_time": statistics.mean(times),
                "min_time": min(times),
                "max_time": max(times),
                "std_dev": statistics.stdev(times) if len(times) > 1 else 0,
                "row_count": query_results[0]["row_count"]
            }

            print(f"  Avg: {results[query_id]['avg_time']:.3f}s ± {results[query_id]['std_dev']:.3f}s")

        return results

    def run_scale_factor_analysis(self, benchmark_class, scale_factors, connection_factory):
        scale_results = {}

        for sf in scale_factors:
            print(f"Testing scale factor {sf}...")

            benchmark = benchmark_class(scale_factor=sf, output_dir=f"./tpch_data_{sf}")

            start_time = time.time()
            benchmark.generate_data()
            generation_time = time.time() - start_time

            total_size = sum(Path(p).stat().st_size for p in benchmark.tables.values()) / 1024 / 1024

            conn = connection_factory()
            conn.execute(benchmark.get_create_tables_sql())

            start_time = time.time()
            for table, path in benchmark.tables.items():
                conn.execute(f"INSERT INTO {table} SELECT * FROM read_csv('{path}', delim='|', header=false)")
            load_time = time.time() - start_time

            query_results = self.run_query_performance_test(
                benchmark, conn, [1, 3, 6], iterations=3
            )

            scale_results[sf] = {
                "data_generation_time": generation_time,
                "data_size_mb": total_size,
                "data_load_time": load_time,
                "query_performance": query_results
            }

            conn.close()

        return scale_results

def run_systematic_analysis():
    benchmark_tool = PerformanceBenchmark()

    scale_factors = [0.01, 0.1]

    def create_duckdb_connection():
        import duckdb
        return duckdb.connect(":memory:")

    print("Running scale factor analysis...")
    results = benchmark_tool.run_scale_factor_analysis(
        TPCH, scale_factors, create_duckdb_connection
    )

    print("\nScale Factor Analysis Results:")
    print(f"{'Scale Factor':<12} {'Data Size':<10} {'Gen Time':<10} {'Load Time':<10} {'Avg Query':<10}")
    print("-" * 60)

    for sf, data in results.items():
        avg_query_time = statistics.mean([
            q["avg_time"] for q in data["query_performance"].values()
        ])
        print(f"{sf:<12} {data['data_size_mb']:<10.1f} {data['data_generation_time']:<10.2f} {data['data_load_time']:<10.2f} {avg_query_time:<10.3f}")

if __name__ == "__main__":
    run_systematic_analysis()
```

> **📁 Complete, tested example:** See [`examples/features/result_analysis.py`](../../examples/features/result_analysis.py) for programmatic result analysis with metrics aggregation, statistical comparisons, and automated reporting.

### Regression Testing Example

The baseline uses 5 iterations per query, and the regression test uses 3 iterations. The script exits with an error code when regressions are detected, which makes it usable in CI/CD:

```python
from benchbox import ReadPrimitives
import json
import time
from pathlib import Path

class RegressionTester:
    def __init__(self, baseline_file: str = "performance_baseline.json"):
        self.baseline_file = Path(baseline_file)
        self.baseline_data = self.load_baseline()

    def load_baseline(self):
        if self.baseline_file.exists():
            with open(self.baseline_file, 'r') as f:
                return json.load(f)
        return {}

    def save_baseline(self, data):
        with open(self.baseline_file, 'w') as f:
            json.dump(data, f, indent=2)

    def create_baseline(self, connection_factory):
        print("Creating performance baseline...")

        read_primitives = ReadPrimitives(scale_factor=0.01, output_dir="./rp_data")
        baseline_data = {}

        critical_queries = [
            "aggregation_distinct",
            "broadcast_join_two_tables",
            "filter_selective",
            "limit"
        ]

        conn = connection_factory()

        self._setup_primitives_data(read_primitives, conn)

        for query_id in critical_queries:
            print(f"  Measuring {query_id}...")

            times = []
            for _ in range(5):
                query_sql = read_primitives.get_query(query_id)

                start_time = time.time()
                result = conn.execute(query_sql).fetchall()
                execution_time = time.time() - start_time
                times.append(execution_time)

            baseline_data[query_id] = {
                "avg_time": sum(times) / len(times),
                "min_time": min(times),
                "max_time": max(times),
                "row_count": len(result)
            }

            print(f"    Baseline: {baseline_data[query_id]['avg_time']:.3f}s")

        self.baseline_data = baseline_data
        self.save_baseline(baseline_data)
        conn.close()

        print("Baseline created and saved.")
        return baseline_data

    def run_regression_test(self, connection_factory, threshold=0.15):
        if not self.baseline_data:
            raise ValueError("No baseline data available. Create baseline first.")

        print("Running regression test...")

        read_primitives = ReadPrimitives(scale_factor=0.01, output_dir="./rp_data")
        conn = connection_factory()

        self._setup_primitives_data(read_primitives, conn)

        regressions = []
        improvements = []

        for query_id, baseline in self.baseline_data.items():
            print(f"  Testing {query_id}...")

            times = []
            for _ in range(3):
                query_sql = read_primitives.get_query(query_id)

                start_time = time.time()
                result = conn.execute(query_sql).fetchall()
                execution_time = time.time() - start_time
                times.append(execution_time)

            current_avg = sum(times) / len(times)
            baseline_avg = baseline["avg_time"]

            change_percent = (current_avg - baseline_avg) / baseline_avg * 100

            print(f"    Current: {current_avg:.3f}s (baseline: {baseline_avg:.3f}s, change: {change_percent:+.1f}%)")

            if change_percent > threshold * 100:
                regressions.append({
                    "query_id": query_id,
                    "baseline_time": baseline_avg,
                    "current_time": current_avg,
                    "change_percent": change_percent
                })
            elif change_percent < -threshold * 100:
                improvements.append({
                    "query_id": query_id,
                    "baseline_time": baseline_avg,
                    "current_time": current_avg,
                    "change_percent": change_percent
                })

        conn.close()

        print(f"\nRegression Test Results:")
        print(f"  Threshold: ±{threshold*100:.0f}%")

        if regressions:
            print(f"  REGRESSIONS DETECTED ({len(regressions)}):")
            for reg in regressions:
                print(f"    {reg['query_id']}: {reg['change_percent']:+.1f}% slower")

        if improvements:
            print(f"  IMPROVEMENTS DETECTED ({len(improvements)}):")
            for imp in improvements:
                print(f"    {imp['query_id']}: {abs(imp['change_percent']):.1f}% faster")

        if not regressions and not improvements:
            print(f"  ✅ NO SIGNIFICANT CHANGES")

        return {
            "regressions": regressions,
            "improvements": improvements,
            "has_regressions": len(regressions) > 0
        }

    def _setup_primitives_data(self, primitives, conn):
        primitives.generate_data()

        schema_sql = primitives.get_create_tables_sql()
        for statement in schema_sql.split(';'):
            if statement.strip():
                conn.execute(statement)

        for table, path in primitives.tables.items():
            conn.execute(f"""
                INSERT INTO {table}
                SELECT * FROM read_csv('{path}', header=false, delim='|')
            """)

def run_regression_testing():

    def create_duckdb_connection():
        import duckdb
        return duckdb.connect(":memory:")

    tester = RegressionTester()

    if not tester.baseline_data:
        tester.create_baseline(create_duckdb_connection)

    results = tester.run_regression_test(create_duckdb_connection)

    import sys
    if results["has_regressions"]:
        print("REGRESSION TEST FAILED")
        sys.exit(1)
    else:
        print("REGRESSION TEST PASSED")
        sys.exit(0)

if __name__ == "__main__":
    run_regression_testing()
```

> **📁 Complete, tested example:** See [`examples/use_cases/ci_regression_test.py`](../../examples/use_cases/ci_regression_test.py) for a production-ready CI/CD testing script with error handling, logging, and CLI support.

---

## Advanced-level Usage Examples

### Custom Query Analysis

```python
from benchbox import TPCDS
import re

FEATURE_PATTERNS = {
    "window": r'\bOVER\s*\(',
    "rollup": r'\bROLLUP\b',
    "cte": r'\bWITH\s+\w+\s+AS\b',
    "union": r'\bUNION\b',
    "case": r'\bCASE\b',
}

def analyze_tpcds_queries():

    tpcds = TPCDS(scale_factor=0.01)
    queries = tpcds.get_queries()

    analysis_results = {}

    for query_id, query_sql in queries.items():
        print(f"Analyzing Query {query_id}...")

        features = [
            name for name, pattern in FEATURE_PATTERNS.items()
            if re.search(pattern, query_sql, re.IGNORECASE)
        ]
        line_count = len(query_sql.split('\n'))
        table_count = len(re.findall(r'\bFROM\s+(\w+)', query_sql, re.IGNORECASE))
        join_count = len(re.findall(r'\bJOIN\b', query_sql, re.IGNORECASE))
        subquery_count = len(re.findall(r'\(\s*SELECT', query_sql, re.IGNORECASE))
        complexity = join_count + 2 * subquery_count + len(features)

        analysis_results[query_id] = {
            "features": features,
            "complexity_score": complexity,
            "line_count": line_count,
            "table_count": table_count,
            "join_count": join_count,
            "subquery_count": subquery_count
        }

    complexities = [r["complexity_score"] for r in analysis_results.values()]
    join_counts = [r["join_count"] for r in analysis_results.values()]

    print(f"\nTPC-DS Query Analysis Summary:")
    print(f"  Total queries: {len(queries)}")
    print(f"  Average complexity: {sum(complexities)/len(complexities):.1f}")
    print(f"  Average joins per query: {sum(join_counts)/len(join_counts):.1f}")

    complex_queries = sorted(
        analysis_results.items(),
        key=lambda x: x[1]["complexity_score"],
        reverse=True
    )[:5]

    print(f"\nMost complex queries:")
    for query_id, analysis in complex_queries:
        print(f"  Query {query_id}: complexity {analysis['complexity_score']}, "
              f"{analysis['join_count']} joins, {analysis['subquery_count']} subqueries")

    return analysis_results

if __name__ == "__main__":
    analyze_tpcds_queries()
```

### Multi-Database Comparison

The comparison runs a subset of queries that works on all databases. Each query runs 3 times to compute an average:
```python
from benchbox import TPCH
import duckdb
import sqlite3
import statistics
import time

def compare_databases():

    def setup_duckdb():
        conn = duckdb.connect(":memory:")
        return conn, "duckdb"

    def setup_sqlite():
        conn = sqlite3.connect(":memory:")
        return conn, "sqlite"

    database_setups = [setup_duckdb, setup_sqlite]

    tpch = TPCH(scale_factor=0.01, output_dir="./tpch_data")
    tpch.generate_data()

    test_queries = [1, 3, 6, 12]

    results = {}

    for setup_func in database_setups:
        conn, db_name = setup_func()
        print(f"Testing {db_name.upper()}...")

        try:
            schema_sql = tpch.get_create_tables_sql()
            if db_name == "sqlite":
                conn.executescript(schema_sql)
            else:
                for statement in schema_sql.split(';'):
                    if statement.strip():
                        conn.execute(statement)

            load_data_for_database(conn, db_name, tpch)


            db_results = {}
            for query_id in test_queries:
                print(f"  Query {query_id}...", end="")

                query_sql = tpch.get_query(query_id, dialect=db_name)

                times = []
                for _ in range(3):
                    start_time = time.time()
                    if db_name == "duckdb":
                        result = conn.execute(query_sql).fetchall()
                    else:
                        cursor = conn.cursor()
                        cursor.execute(query_sql)
                        result = cursor.fetchall()
                    execution_time = time.time() - start_time
                    times.append(execution_time)

                avg_time = statistics.mean(times)
                db_results[query_id] = {
                    "avg_time": avg_time,
                    "row_count": len(result)
                }
                print(f" {avg_time:.3f}s")

            results[db_name] = db_results

        except Exception as e:
            print(f"  Error with {db_name}: {e}")
            results[db_name] = {"error": str(e)}

        finally:
            conn.close()

    print(f"\nDatabase Comparison Results:")
    print(f"{'Query':<8} {'DuckDB':<10} {'SQLite':<10} {'Speedup':<10}")
    print("-" * 40)

    for query_id in test_queries:
        if ("duckdb" in results and query_id in results["duckdb"] and
            "sqlite" in results and query_id in results["sqlite"]):

            duckdb_time = results["duckdb"][query_id]["avg_time"]
            sqlite_time = results["sqlite"][query_id]["avg_time"]
            speedup = sqlite_time / duckdb_time

            print(f"Q{query_id:<7} {duckdb_time:<10.3f} {sqlite_time:<10.3f} {speedup:<10.1f}x")

    return results

def load_data_for_database(conn, db_name, tpch):
    if db_name == "duckdb":
        for table, path in tpch.tables.items():
            conn.execute(f"""
                INSERT INTO {table}
                SELECT * FROM read_csv('{path}', header=false, delim='|')
            """)
    elif db_name == "sqlite":
        import csv
        for table, path in tpch.tables.items():
            with open(path, 'r') as f:
                reader = csv.reader(f, delimiter='|')
                cursor = conn.cursor()
                cursor.execute(f"PRAGMA table_info({table})")
                column_count = len(cursor.fetchall())
                placeholders = ','.join(['?' for _ in range(column_count)])

                for row in reader:
                    row = [None if x == '' else x for x in row]
                    cursor.execute(f"INSERT INTO {table} VALUES ({placeholders})", row)
                conn.commit()

if __name__ == "__main__":
    compare_databases()
```

> **📁 Complete, tested example:** See [`examples/features/multi_platform.py`](../../examples/features/multi_platform.py) for multi-platform benchmarking and [`examples/use_cases/platform_evaluation.py`](../../examples/use_cases/platform_evaluation.py) for comprehensive platform evaluation.

---

## CI/CD Integration Examples

### GitHub Actions Workflow

Save this workflow as `.github/workflows/performance-test.yml`:

```yaml
name: Performance Regression Tests

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  performance-test:
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v3

    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'

    - name: Install dependencies
      run: |
        uv add benchbox duckdb
        uv pip install -r requirements-test.txt

    - name: Run performance regression tests
      run: |
        uv run -- python scripts/run_performance_tests.py

    - name: Upload performance results
      uses: actions/upload-artifact@v3
      if: always()
      with:
        name: performance-results
        path: performance_results.json
```

### Performance Test Script for CI/CD

This script is a performance regression test for CI/CD pipelines. Run it with `uv run -- python scripts/run_performance_tests.py`. `CI_SCALE_FACTOR = 0.01` is small for fast CI runs, and `REGRESSION_THRESHOLD = 0.20` is a 20% regression threshold. Each query is warmed up and then measured over 3 iterations. The script loads a baseline if one exists, and on a regression it saves the results and exits with failure:

```python
import json
import sys
import time
from pathlib import Path
from benchbox import ReadPrimitives
import duckdb

def run_performance_tests():

    CI_SCALE_FACTOR = 0.01
    REGRESSION_THRESHOLD = 0.20
    BASELINE_FILE = "baseline_performance.json"
    RESULTS_FILE = "performance_results.json"

    print("Starting BenchBox performance regression tests...")

    read_primitives = ReadPrimitives(scale_factor=CI_SCALE_FACTOR, output_dir="./rp_data")

    conn = duckdb.connect(":memory:")
    setup_database(read_primitives, conn)

    critical_queries = [
        "aggregation_distinct",
        "broadcast_join_two_tables",
        "filter_selective",
        "limit"
    ]

    current_results = {}
    print(f"Running {len(critical_queries)} critical queries...")

    for query_id in critical_queries:
        print(f"  Testing {query_id}...", end="")

        query_sql = read_primitives.get_query(query_id)
        conn.execute(query_sql).fetchall()

        times = []
        for _ in range(3):
            start_time = time.time()
            result = conn.execute(query_sql).fetchall()
            execution_time = time.time() - start_time
            times.append(execution_time)

        avg_time = sum(times) / len(times)
        current_results[query_id] = {
            "avg_time": avg_time,
            "row_count": len(result),
            "timestamp": time.time()
        }

        print(f" {avg_time:.3f}s ✅")

    conn.close()

    baseline_path = Path(BASELINE_FILE)
    if baseline_path.exists():
        with open(baseline_path, 'r') as f:
            baseline_results = json.load(f)

        print(f" Comparing against baseline from {baseline_path}...")
        regressions = check_regressions(current_results, baseline_results, REGRESSION_THRESHOLD)

        if regressions:
            print(f"PERFORMANCE REGRESSIONS DETECTED:")
            for reg in regressions:
                print(f"  - {reg['query_id']}: {reg['change_percent']:+.1f}% slower")

            save_results(current_results, RESULTS_FILE, regressions)
            sys.exit(1)
        else:
            print(f"✅ No significant performance regressions detected")
    else:
        print(f"No baseline found. Creating new baseline at {baseline_path}")
        with open(baseline_path, 'w') as f:
            json.dump(current_results, f, indent=2)

    save_results(current_results, RESULTS_FILE)
    print(f" Performance tests completed successfully!")

def setup_database(primitives, conn):
    primitives.generate_data()

    schema_sql = primitives.get_create_tables_sql()
    for statement in schema_sql.split(';'):
        if statement.strip():
            conn.execute(statement)

    for table, path in primitives.tables.items():
        conn.execute(f"""
            INSERT INTO {table}
            SELECT * FROM read_csv('{path}', header=false, delim='|')
        """)

def check_regressions(current_results, baseline_results, threshold):
    regressions = []

    for query_id, current in current_results.items():
        if query_id in baseline_results:
            baseline = baseline_results[query_id]

            current_time = current["avg_time"]
            baseline_time = baseline["avg_time"]

            change_percent = (current_time - baseline_time) / baseline_time * 100

            if change_percent > threshold * 100:
                regressions.append({
                    "query_id": query_id,
                    "baseline_time": baseline_time,
                    "current_time": current_time,
                    "change_percent": change_percent
                })

    return regressions

def save_results(results, filename, regressions=None):
    output = {
        "results": results,
        "summary": {
            "total_queries": len(results),
            "avg_execution_time": sum(r["avg_time"] for r in results.values()) / len(results),
            "has_regressions": regressions is not None and len(regressions) > 0
        }
    }

    if regressions:
        output["regressions"] = regressions

    with open(filename, 'w') as f:
        json.dump(output, f, indent=2)

if __name__ == "__main__":
    run_performance_tests()
```

> **📁 Complete, tested example:** See [`examples/use_cases/ci_regression_test.py`](../../examples/use_cases/ci_regression_test.py) for a production-ready CI/CD performance testing script with baseline comparison, threshold detection, and exit code reporting.

---

## Custom Benchmark Examples

### Simple Custom Benchmark

```python
from benchbox.base import BaseBenchmark
from pathlib import Path
from typing import Dict, Union
import csv

class SimpleBenchmark(BaseBenchmark):

    def __init__(self, scale_factor: float = 1.0, output_dir: Path = None):
        super().__init__(scale_factor, output_dir)
        self._queries = {
            "simple_count": "SELECT COUNT(*) FROM test_table",
            "simple_sum": "SELECT SUM(value) FROM test_table",
            "simple_avg": "SELECT AVG(value) FROM test_table WHERE category = 'A'"
        }

    def generate_data(self) -> Dict[str, Path]:
        self.output_dir.mkdir(parents=True, exist_ok=True)

        test_file = self.output_dir / "test_table.csv"
        row_count = int(1000 * self.scale_factor)

        with open(test_file, 'w', newline='') as f:
            writer = csv.writer(f)

            for i in range(row_count):
                category = 'A' if i % 3 == 0 else 'B' if i % 3 == 1 else 'C'
                value = i * 1.5
                writer.writerow([i, category, value])

        return {"test_table": test_file}

    def get_queries(self) -> Dict[Union[int, str], str]:
        return self._queries.copy()

    def get_query(self, query_id: Union[int, str]) -> str:
        if query_id not in self._queries:
            raise ValueError(f"Query '{query_id}' not found")
        return self._queries[query_id]

    def get_create_tables_sql(self, dialect: str = "standard") -> str:
        return """
        CREATE TABLE test_table (
            id INTEGER,
            category VARCHAR(10),
            value DOUBLE
        );
        """

def test_custom_benchmark():
    import duckdb

    benchmark = SimpleBenchmark(scale_factor=0.1)

    data_files = benchmark.generate_data()
    print(f"Generated {len(data_files)} data files")

    conn = duckdb.connect(":memory:")

    ddl = benchmark.get_create_tables_sql("duckdb")
    conn.execute(ddl)

    for table_name, file_path in data_files.items():
        conn.execute(f"""
            INSERT INTO {table_name}
            SELECT * FROM read_csv('{file_path}', header=false)
        """)

    queries = benchmark.get_queries()
    for query_id, query_sql in queries.items():
        result = conn.execute(query_sql).fetchall()
        print(f"{query_id}: {result[0][0]}")

    conn.close()

if __name__ == "__main__":
    test_custom_benchmark()
```

---

## See Also

- [Getting Started](getting-started.md) - Basic setup and usage
- [API Reference](../reference/api-reference.md) - Complete API documentation
- [Configuration](configuration.md) - Configuration options
- [Benchmarks](../benchmarks/index.md) - Individual benchmark documentation

---

*These examples provide practical patterns for using BenchBox in real-world scenarios. Adapt them to your specific needs and requirements.*
