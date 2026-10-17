<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# BenchBox Repository Structure

```{tags} contributor, concept
```

## Top-Level Layout

```
BenchBox/
├── benchbox/              # Main package (see below)
├── tests/                 # Test suite
├── docs/                  # Documentation (Markdown)
├── docker/                # Docker configurations
├── examples/              # Example scripts and configs
├── scripts/               # Development and CI scripts
├── _binaries/             # TPC tool binaries (dsdgen, dsqgen)
├── _sources/              # TPC specification source files
├── pyproject.toml         # Project metadata and dependencies
├── Makefile               # Development task shortcuts
└── CLAUDE.md              # AI assistant instructions
```

## Package Structure (`benchbox/`)

### Root Module Files

Benchmark wrapper classes that re-export core implementations:

```
benchbox/
├── __init__.py            # Package exports
├── base.py                # BaseBenchmark abstract base class
├── tpch.py                # TPC-H wrapper
├── tpcds.py               # TPC-DS wrapper
├── tpcdi.py               # TPC-DI wrapper
├── ssb.py                 # Star Schema Benchmark wrapper
├── clickbench.py          # ClickBench wrapper
├── h2odb.py               # H2ODB wrapper
├── amplab.py              # AMPLab wrapper
├── joinorder.py           # Join Order Benchmark wrapper
├── coffeeshop.py          # CoffeeShop wrapper
├── nyctaxi.py             # NYC Taxi wrapper
├── tsbs_devops.py         # TSBS DevOps wrapper
├── datavault.py           # Data Vault wrapper
├── tpcds_obt.py           # TPC-DS One Big Table wrapper
├── tpch_skew.py           # TPC-H Skew wrapper
├── tpchavoc.py            # TPC-Havoc wrapper
├── read_primitives.py     # Read Primitives wrapper
├── write_primitives.py    # Write Primitives wrapper
├── transaction_primitives.py  # Transaction Primitives wrapper
└── metadata_primitives.py # Metadata Primitives wrapper
```

### `core/` - Core Infrastructure (39 subdirectories)

```
benchbox/core/
├── runner/                # Benchmark lifecycle execution
│   ├── runner.py          #   run_benchmark_lifecycle(), LifecyclePhases
│   └── conversion.py     #   Format conversion orchestration
├── results/               # Result models and serialization
│   ├── models.py          #   BenchmarkResults, ExecutionPhases, QueryExecution
│   ├── builder.py         #   ResultBuilder (centralized aggregation)
│   └── ...                #   loader, exporter, normalizer, etc.
├── schemas.py             # Pydantic models (BenchmarkConfig, DatabaseConfig, etc.)
├── benchmark_registry.py  # Benchmark name → class mapping and metadata
├── platform_registry.py   # Platform capabilities and registration
│
├── validation/            # Data and result validation
├── visualization/         # ASCII chart generation (15+ chart types)
│   ├── result_plotter.py  #   ResultPlotter orchestration
│   ├── templates.py       #   Named chart combinations
│   └── ascii/             #   Chart implementations (bar, box, heatmap, etc.)
├── dataframe/             # DataFrame execution context and profiling
│   ├── context.py         #   DataFrameContext protocol
│   ├── query.py           #   DataFrameQuery (dual pandas/expression impls)
│   ├── profiling.py       #   DataFrameProfiler
│   └── tuning/            #   DataFrame tuning configuration
├── query_plans/           # Query plan capture and analysis
├── tuning/                # Unified SQL+DataFrame tuning system
├── data_organization/     # Sorted ingestion, clustering strategies
├── comparison/            # Cross-run result comparison
├── analysis/              # Statistical analysis
├── cost/                  # Cloud cost estimation
├── contracts/             # Interface contracts
├── manifest/              # Data manifest tracking
├── expected_results/      # Expected query results for validation
├── databases/             # Database metadata
├── operations/            # Complex operation implementations
├── publishing/            # Result publishing
├── primitives/            # Shared primitives infrastructure
│
├── tpch/                  # TPC-H benchmark implementation
├── tpcds/                 # TPC-DS benchmark implementation
├── tpcdi/                 # TPC-DI benchmark implementation
├── ssb/                   # Star Schema Benchmark implementation
├── clickbench/            # ClickBench implementation
├── h2odb/                 # H2ODB implementation
├── amplab/                # AMPLab implementation
├── joinorder/             # Join Order Benchmark implementation
├── coffeeshop/            # CoffeeShop implementation
├── nyctaxi/               # NYC Taxi implementation
├── tsbs_devops/           # TSBS DevOps implementation
├── datavault/             # Data Vault implementation
├── tpcds_obt/             # TPC-DS One Big Table implementation
├── tpch_skew/             # TPC-H Skew implementation
├── tpchavoc/              # TPC-Havoc implementation
├── read_primitives/       # Read Primitives implementation
├── write_primitives/      # Write Primitives implementation
├── transaction_primitives/ # Transaction Primitives implementation
├── metadata_primitives/   # Metadata Primitives implementation
├── ai_primitives/         # AI/ML Primitives implementation
└── utils/                 # Core utilities
```

### `platforms/` - Database Adapters

```
benchbox/platforms/
├── base/                  # Base adapter classes
│   ├── adapter.py         #   PlatformAdapter (abstract base, ~5000 lines)
│   ├── sql_execution.py   #   SQL execution engine
│   ├── data_loading.py    #   Data loading and staging
│   ├── models.py          #   Phase data models
│   └── ...                #   Mixins, validation, format capabilities
│
├── duckdb.py              # DuckDB adapter
├── sqlite.py              # SQLite adapter
├── postgresql.py          # PostgreSQL adapter
├── snowflake.py           # Snowflake adapter
├── bigquery.py            # BigQuery adapter
├── databricks/            # Databricks adapter (subpackage)
├── redshift.py            # Redshift adapter
├── athena.py              # Athena adapter
├── clickhouse/            # ClickHouse adapter (subpackage)
├── clickhouse_cloud.py    # ClickHouse Cloud adapter
├── datafusion.py          # DataFusion SQL adapter
├── trino.py               # Trino adapter
├── presto.py              # Presto adapter
├── motherduck.py          # MotherDuck adapter
├── pg_duckdb.py           # pg_duckdb extension adapter
├── pg_mooncake.py         # pg_mooncake extension adapter
├── firebolt.py            # Firebolt adapter
├── doris.py               # Apache Doris adapter
├── databend/              # Databend adapter (subpackage)
├── starrocks/             # StarRocks adapter (subpackage)
├── timescaledb.py         # TimescaleDB adapter
├── questdb.py             # QuestDB adapter
├── influxdb/              # InfluxDB adapter (subpackage)
├── azure_synapse.py       # Azure Synapse adapter
├── fabric_warehouse.py    # Microsoft Fabric Warehouse adapter
├── fabric_lakehouse.py    # Microsoft Fabric Lakehouse adapter
├── fabric_spark.py        # Microsoft Fabric Spark adapter
├── starburst.py           # Starburst adapter
├── snowpark_connect.py    # Snowpark Connect adapter
├── lakesail.py            # LakeSail adapter
├── spark.py               # Generic Spark adapter
├── pyspark/               # PySpark SQL adapter (subpackage)
├── polars_platform.py     # Polars SQL adapter
├── adapter_factory.py     # Unified adapter factory (get_adapter)
│
└── dataframe/             # DataFrame platform adapters
    ├── expression_family.py #  ExpressionFamilyAdapter base class
    ├── pandas_family.py   #   PandasFamilyAdapter base class
    ├── benchmark_mixin.py #   BenchmarkExecutionMixin (run_benchmark)
    ├── polars_df.py       #   Polars DataFrame adapter
    ├── pandas_df.py       #   Pandas DataFrame adapter
    ├── pyspark_df.py      #   PySpark DataFrame adapter
    ├── datafusion_df.py   #   DataFusion DataFrame adapter
    ├── cudf_df.py         #   cuDF DataFrame adapter
    ├── dask_df.py         #   Dask DataFrame adapter
    ├── lakesail_df.py     #   LakeSail DataFrame adapter
    ├── shared_loading.py  #   Shared data loading logic
    └── platform_checker.py #  Platform capability detection
```

### `cli/` - Command-Line Interface

```
benchbox/cli/
├── main.py                # CLI entry point and command registration
├── orchestrator.py        # BenchmarkOrchestrator (run lifecycle)
├── commands/              # Click command implementations
│   ├── run.py             #   benchbox run (main command)
│   ├── compare.py         #   benchbox compare
│   ├── visualize.py       #   benchbox visualize
│   ├── report.py          #   benchbox report (group: rankings, trends, etc.)
│   ├── metrics.py         #   benchbox metrics (group: qphh)
│   ├── aggregate.py       #   benchbox aggregate
│   ├── datagen.py         #   benchbox datagen
│   ├── setup.py           #   benchbox setup (cloud credentials)
│   ├── shell.py           #   benchbox shell
│   ├── convert.py         #   benchbox convert
│   ├── tuning_group.py    #   benchbox tuning (init, validate, defaults)
│   ├── show_plan.py       #   benchbox show-plan
│   ├── plan_history.py    #   benchbox plan-history
│   ├── download_answers.py #  benchbox download-answers
│   └── ...                #   export, results, profile, checks, etc.
├── config.py              # CLI configuration
├── display.py             # Output formatting
├── progress.py            # Progress bars
├── onboarding.py          # Interactive onboarding
├── presentation/          # Output presentation layer
└── ...                    # Validation, platform hooks, etc.
```

### Other Top-Level Packages

```
benchbox/
├── mcp/                   # MCP server for AI assistant integration
├── utils/                 # Shared utilities (file I/O, formatting, clock)
├── monitoring/            # Performance monitoring and profiling
├── security/              # Credential management
├── experimental/          # Experimental features
├── data/                  # Static data resources
├── examples/              # Example configurations
├── release/               # Release management
└── _binaries/             # TPC tool binaries (platform-specific)
```
