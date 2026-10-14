window.__BENCHBOX_PROMPT_CATALOG__ = {
  "benchmarks": [
    {
      "id": "tpch",
      "interfaces": [
        "sql",
        "dataframe"
      ],
      "label": "TPC-H"
    },
    {
      "id": "tpcds",
      "interfaces": [
        "sql",
        "dataframe"
      ],
      "label": "TPC-DS",
      "requires_bundled_dsdgen_below": "1.0"
    },
    {
      "id": "clickbench",
      "interfaces": [
        "sql"
      ],
      "label": "ClickBench"
    },
    {
      "id": "ssb",
      "interfaces": [
        "sql"
      ],
      "label": "SSB"
    }
  ],
  "defaults": {
    "benchmark": "tpch",
    "deployment": "local",
    "goal": "test_one",
    "interface": "sql",
    "platform": "duckdb",
    "scale": "0.01",
    "surface": "cli"
  },
  "deployments": [
    {
      "id": "local",
      "label": "Local"
    },
    {
      "id": "self-hosted",
      "label": "Self-hosted"
    },
    {
      "id": "managed",
      "label": "Managed cloud"
    }
  ],
  "goals": [
    {
      "id": "test_one",
      "label": "Test one platform"
    },
    {
      "id": "compare",
      "label": "Compare platforms"
    }
  ],
  "interfaces": [
    {
      "id": "sql",
      "label": "SQL"
    },
    {
      "id": "dataframe",
      "label": "DataFrame"
    }
  ],
  "mcp": {
    "analysis_tool": "analyze_results",
    "list_tool": "list_available",
    "plan_tool": "get_query_plan",
    "prompts": {
      "analyze_results": "analyze_results",
      "benchmark_run": "benchmark_run",
      "compare_platforms": "compare_platforms"
    },
    "run_tool": "run_benchmark",
    "system_profile_tool": "system_profile"
  },
  "platforms": [
    {
      "cost_class": "paid_credits",
      "credential_deployments": [
        "managed"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform athena",
      "dependency_check_platform": "athena",
      "deployments": [
        "managed"
      ],
      "id": "athena",
      "install_command": "uv add pyathena boto3",
      "interfaces": [
        "sql"
      ],
      "label": "Amazon Athena",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "paid_compute",
      "credential_deployments": [
        "managed"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform athena-spark",
      "dependency_check_platform": "athena-spark",
      "deployments": [
        "managed"
      ],
      "id": "athena-spark",
      "install_command": "uv add boto3",
      "interfaces": [
        "sql",
        "dataframe"
      ],
      "label": "Amazon Athena for Apache Spark",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "paid_credits",
      "credential_deployments": [
        "managed"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform bigquery",
      "dependency_check_platform": "bigquery",
      "deployments": [
        "managed"
      ],
      "id": "bigquery",
      "install_command": "uv add google-cloud-bigquery google-cloud-storage",
      "interfaces": [
        "sql"
      ],
      "label": "Google BigQuery",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "credential_deployments": [
        "self-hosted"
      ],
      "deployments": [
        "self-hosted"
      ],
      "id": "cedardb",
      "install_command": "uv add 'psycopg[binary]'",
      "interfaces": [
        "sql"
      ],
      "label": "CedarDB",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "credential_deployments": [
        "self-hosted"
      ],
      "deployments": [
        "self-hosted"
      ],
      "id": "citus",
      "install_command": "uv add 'psycopg[binary]'",
      "interfaces": [
        "sql"
      ],
      "label": "citus",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "credential_deployments": [
        "self-hosted"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform clickhouse",
      "dependency_check_platform": "clickhouse",
      "deployments": [
        "local",
        "self-hosted"
      ],
      "id": "clickhouse",
      "install_command": "uv add clickhouse-driver",
      "interfaces": [
        "sql"
      ],
      "label": "ClickHouse",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "paid_compute",
      "credential_deployments": [
        "managed"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform clickhouse-cloud",
      "dependency_check_platform": "clickhouse-cloud",
      "deployments": [
        "managed"
      ],
      "id": "clickhouse-cloud",
      "install_command": "uv add benchbox --extra clickhouse-cloud",
      "interfaces": [
        "sql"
      ],
      "label": "ClickHouse Cloud",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "dependency_check_command": "uv run benchbox check-deps --platform clickhouse-local",
      "dependency_check_platform": "clickhouse-local",
      "deployments": [
        "local"
      ],
      "id": "clickhouse-local",
      "install_command": "uv add benchbox --extra clickhouse-local",
      "interfaces": [
        "sql"
      ],
      "label": "ClickHouse Local (chDB)"
    },
    {
      "cost_class": "free",
      "credential_deployments": [
        "self-hosted"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform clickhouse-server",
      "dependency_check_platform": "clickhouse-server",
      "deployments": [
        "self-hosted"
      ],
      "id": "clickhouse-server",
      "install_command": "uv add benchbox --extra clickhouse-server",
      "interfaces": [
        "sql"
      ],
      "label": "ClickHouse Server",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "deployments": [
        "local"
      ],
      "id": "cudf",
      "install_command": "pip install cudf-cu12 (requires NVIDIA GPU)",
      "interfaces": [
        "dataframe"
      ],
      "label": "cuDF"
    },
    {
      "cost_class": "free",
      "deployments": [
        "local"
      ],
      "id": "dask",
      "install_command": "uv add dask[distributed]",
      "interfaces": [
        "dataframe"
      ],
      "label": "Dask"
    },
    {
      "cost_class": "paid_compute",
      "credential_deployments": [
        "self-hosted",
        "managed"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform databend",
      "dependency_check_platform": "databend",
      "deployments": [
        "self-hosted",
        "managed"
      ],
      "id": "databend",
      "install_command": "uv add databend-driver",
      "interfaces": [
        "sql"
      ],
      "label": "Databend",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "paid_credits",
      "credential_deployments": [
        "managed"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform databricks",
      "dependency_check_platform": "databricks",
      "deployments": [
        "managed"
      ],
      "id": "databricks",
      "install_command": "uv add databricks-sql-connector",
      "interfaces": [
        "sql",
        "dataframe"
      ],
      "label": "Databricks SQL",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "paid_credits",
      "credential_deployments": [
        "managed"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform databricks-df",
      "dependency_check_platform": "databricks-df",
      "deployments": [
        "managed"
      ],
      "id": "databricks-df",
      "install_command": "uv add databricks-sql-connector databricks-connect",
      "interfaces": [
        "sql",
        "dataframe"
      ],
      "label": "Databricks DataFrame",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "deployments": [
        "local"
      ],
      "id": "datafusion",
      "install_command": "uv add datafusion",
      "interfaces": [
        "sql",
        "dataframe"
      ],
      "label": "DataFusion"
    },
    {
      "cost_class": "paid_compute",
      "credential_deployments": [
        "managed"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform dataproc",
      "dependency_check_platform": "dataproc",
      "deployments": [
        "managed"
      ],
      "id": "dataproc",
      "install_command": "uv add google-cloud-dataproc google-cloud-storage",
      "interfaces": [
        "sql",
        "dataframe"
      ],
      "label": "Google Cloud Dataproc",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "paid_compute",
      "credential_deployments": [
        "managed"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform dataproc-serverless",
      "dependency_check_platform": "dataproc-serverless",
      "deployments": [
        "managed"
      ],
      "id": "dataproc-serverless",
      "install_command": "uv add google-cloud-dataproc google-cloud-storage",
      "interfaces": [
        "sql",
        "dataframe"
      ],
      "label": "Google Cloud Dataproc Serverless",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "credential_deployments": [
        "self-hosted"
      ],
      "deployments": [
        "self-hosted"
      ],
      "id": "doris",
      "install_command": "uv add pymysql",
      "interfaces": [
        "sql"
      ],
      "label": "Apache Doris",
      "platform_option_hints": [
        "--platform-option port=9030",
        "--platform-option http_port=8030"
      ],
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "deployments": [
        "local"
      ],
      "id": "duckdb",
      "install_command": "uv add 'duckdb>=1.5,<2'",
      "interfaces": [
        "sql"
      ],
      "label": "DuckDB"
    },
    {
      "cost_class": "free",
      "credential_deployments": [
        "self-hosted"
      ],
      "deployments": [
        "local",
        "self-hosted"
      ],
      "id": "ducklake",
      "install_command": "uv add 'benchbox[ducklake]'",
      "interfaces": [
        "sql"
      ],
      "label": "DuckLake",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "paid_compute",
      "credential_deployments": [
        "managed"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform emr-serverless",
      "dependency_check_platform": "emr-serverless",
      "deployments": [
        "managed"
      ],
      "id": "emr-serverless",
      "install_command": "uv add boto3",
      "interfaces": [
        "sql",
        "dataframe"
      ],
      "label": "Amazon EMR Serverless",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "paid_compute",
      "credential_deployments": [
        "managed"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform fabric",
      "dependency_check_platform": "fabric",
      "deployments": [
        "managed"
      ],
      "id": "fabric-lakehouse",
      "install_command": "uv add benchbox --extra fabric",
      "interfaces": [
        "sql"
      ],
      "label": "Microsoft Fabric Lakehouse SQL",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "paid_compute",
      "credential_deployments": [
        "managed"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform fabric-spark",
      "dependency_check_platform": "fabric-spark",
      "deployments": [
        "managed"
      ],
      "id": "fabric-spark",
      "install_command": "uv add azure-identity azure-storage-file-datalake requests",
      "interfaces": [
        "sql",
        "dataframe"
      ],
      "label": "Microsoft Fabric Spark",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "paid_compute",
      "credential_deployments": [
        "managed"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform fabric",
      "dependency_check_platform": "fabric",
      "deployments": [
        "managed"
      ],
      "id": "fabric_dw",
      "install_command": "uv add pyodbc azure-identity azure-storage-file-datalake",
      "interfaces": [
        "sql"
      ],
      "label": "Microsoft Fabric Warehouse",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "paid_compute",
      "credential_deployments": [
        "managed"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform firebolt",
      "dependency_check_platform": "firebolt",
      "deployments": [
        "local",
        "managed"
      ],
      "id": "firebolt",
      "install_command": "uv add firebolt-sdk",
      "interfaces": [
        "sql"
      ],
      "label": "Firebolt",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "paid_compute",
      "credential_deployments": [
        "managed"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform glue",
      "dependency_check_platform": "glue",
      "deployments": [
        "managed"
      ],
      "id": "glue",
      "install_command": "uv add boto3",
      "interfaces": [
        "sql",
        "dataframe"
      ],
      "label": "AWS Glue",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "credential_deployments": [
        "self-hosted",
        "managed"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform influxdb",
      "dependency_check_platform": "influxdb",
      "deployments": [
        "self-hosted",
        "managed"
      ],
      "id": "influxdb",
      "install_command": "uv add influxdb3-python",
      "interfaces": [
        "sql"
      ],
      "label": "InfluxDB",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "dependency_check_command": "uv run benchbox check-deps --platform lakesail",
      "dependency_check_platform": "lakesail",
      "deployments": [
        "local",
        "self-hosted"
      ],
      "id": "lakesail",
      "install_command": "uv add pyspark",
      "interfaces": [
        "sql",
        "dataframe"
      ],
      "label": "LakeSail Sail"
    },
    {
      "cost_class": "paid_credits",
      "credential_deployments": [
        "managed"
      ],
      "deployments": [
        "managed"
      ],
      "id": "motherduck",
      "install_command": "uv add duckdb",
      "interfaces": [
        "sql"
      ],
      "label": "MotherDuck",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "deployments": [
        "local"
      ],
      "id": "pandas",
      "install_command": "uv add pandas",
      "interfaces": [
        "dataframe"
      ],
      "label": "Pandas"
    },
    {
      "cost_class": "free",
      "credential_deployments": [
        "self-hosted"
      ],
      "deployments": [
        "self-hosted"
      ],
      "id": "paradedb",
      "install_command": "uv add 'psycopg[binary]'",
      "interfaces": [
        "sql"
      ],
      "label": "paradedb",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "paid_credits",
      "credential_deployments": [
        "self-hosted",
        "managed"
      ],
      "deployments": [
        "self-hosted",
        "managed"
      ],
      "id": "pg-duckdb",
      "install_command": "uv add 'psycopg[binary]'",
      "interfaces": [
        "sql"
      ],
      "label": "pg_duckdb",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "credential_deployments": [
        "self-hosted"
      ],
      "deployments": [
        "self-hosted"
      ],
      "id": "pg-mooncake",
      "install_command": "uv add 'psycopg[binary]'",
      "interfaces": [
        "sql"
      ],
      "label": "pg_mooncake",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "deployments": [
        "local"
      ],
      "id": "polars",
      "install_command": "uv add polars",
      "interfaces": [
        "dataframe"
      ],
      "label": "Polars"
    },
    {
      "cost_class": "free",
      "credential_deployments": [
        "self-hosted"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform postgresql",
      "dependency_check_platform": "postgresql",
      "deployments": [
        "self-hosted"
      ],
      "id": "postgresql",
      "install_command": "uv add 'psycopg[binary]'",
      "interfaces": [
        "sql"
      ],
      "label": "PostgreSQL",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "credential_deployments": [
        "self-hosted"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform presto",
      "dependency_check_platform": "presto",
      "deployments": [
        "self-hosted"
      ],
      "id": "presto",
      "install_command": "uv add presto-python-client",
      "interfaces": [
        "sql"
      ],
      "label": "PrestoDB",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "deployments": [
        "local"
      ],
      "id": "pyspark",
      "install_command": "uv add pyspark",
      "interfaces": [
        "sql",
        "dataframe"
      ],
      "label": "PySpark"
    },
    {
      "cost_class": "paid_compute",
      "credential_deployments": [
        "managed"
      ],
      "deployments": [
        "managed"
      ],
      "id": "quanton",
      "install_command": "uv add requests boto3",
      "interfaces": [
        "sql",
        "dataframe"
      ],
      "label": "Onehouse Quanton",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "credential_deployments": [
        "self-hosted"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform questdb",
      "dependency_check_platform": "questdb",
      "deployments": [
        "self-hosted"
      ],
      "id": "questdb",
      "install_command": "uv add benchbox --extra questdb",
      "interfaces": [
        "sql"
      ],
      "label": "QuestDB",
      "platform_option_hints": [
        "--platform-option http_port=9000  # REST endpoint, distinct from SQL port 8812"
      ],
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "paid_compute",
      "credential_deployments": [
        "managed"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform redshift",
      "dependency_check_platform": "redshift",
      "deployments": [
        "managed"
      ],
      "id": "redshift",
      "install_command": "uv add redshift-connector boto3",
      "interfaces": [
        "sql"
      ],
      "label": "Amazon Redshift",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "paid_compute",
      "credential_deployments": [
        "self-hosted"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform singlestore",
      "dependency_check_platform": "singlestore",
      "deployments": [
        "self-hosted"
      ],
      "id": "singlestore",
      "install_command": "uv add singlestoredb",
      "interfaces": [
        "sql"
      ],
      "label": "SingleStore",
      "platform_option_hints": [
        "--platform-option port=3306  # adapter default; override if local Docker maps a different host port",
        "--platform-option password=<value>  # local/self-managed password"
      ],
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "paid_credits",
      "credential_deployments": [
        "managed"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform snowflake",
      "dependency_check_platform": "snowflake",
      "deployments": [
        "managed"
      ],
      "id": "snowflake",
      "install_command": "uv add snowflake-connector-python",
      "interfaces": [
        "sql"
      ],
      "label": "Snowflake",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "paid_credits",
      "credential_deployments": [
        "managed"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform snowpark-connect",
      "dependency_check_platform": "snowpark-connect",
      "deployments": [
        "managed"
      ],
      "id": "snowpark-connect",
      "install_command": "uv add snowflake-snowpark-python",
      "interfaces": [
        "sql",
        "dataframe"
      ],
      "label": "Snowpark Connect for Spark",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "credential_deployments": [
        "self-hosted"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform spark",
      "dependency_check_platform": "spark",
      "deployments": [
        "local",
        "self-hosted"
      ],
      "id": "spark",
      "install_command": "uv add pyspark",
      "interfaces": [
        "sql"
      ],
      "label": "Apache Spark",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "deployments": [
        "local"
      ],
      "id": "sqlite",
      "install_command": "Built-in Python library",
      "interfaces": [
        "sql"
      ],
      "label": "SQLite"
    },
    {
      "cost_class": "paid_compute",
      "credential_deployments": [
        "managed"
      ],
      "deployments": [
        "managed"
      ],
      "id": "starburst",
      "install_command": "uv add trino",
      "interfaces": [
        "sql"
      ],
      "label": "Starburst",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "credential_deployments": [
        "self-hosted"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform starrocks",
      "dependency_check_platform": "starrocks",
      "deployments": [
        "self-hosted"
      ],
      "id": "starrocks",
      "install_command": "uv add pymysql",
      "interfaces": [
        "sql"
      ],
      "label": "StarRocks",
      "platform_option_hints": [
        "--platform-option port=9030",
        "--platform-option http_port=8040"
      ],
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "paid_compute",
      "credential_deployments": [
        "managed"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform synapse",
      "dependency_check_platform": "synapse",
      "deployments": [
        "managed"
      ],
      "id": "synapse",
      "install_command": "uv add pyodbc azure-storage-blob azure-identity",
      "interfaces": [
        "sql"
      ],
      "label": "Azure Synapse Analytics",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "paid_compute",
      "credential_deployments": [
        "managed"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform synapse-spark",
      "dependency_check_platform": "synapse-spark",
      "deployments": [
        "managed"
      ],
      "id": "synapse-spark",
      "install_command": "uv add azure-identity azure-storage-file-datalake requests",
      "interfaces": [
        "sql",
        "dataframe"
      ],
      "label": "Azure Synapse Analytics Spark",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "credential_deployments": [
        "self-hosted",
        "managed"
      ],
      "deployments": [
        "self-hosted",
        "managed"
      ],
      "id": "timescaledb",
      "install_command": "uv add 'psycopg[binary]'",
      "interfaces": [
        "sql"
      ],
      "label": "TimescaleDB",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "credential_deployments": [
        "self-hosted"
      ],
      "dependency_check_command": "uv run benchbox check-deps --platform trino",
      "dependency_check_platform": "trino",
      "deployments": [
        "self-hosted"
      ],
      "id": "trino",
      "install_command": "uv add trino",
      "interfaces": [
        "sql"
      ],
      "label": "Trino",
      "safety_terms": {
        "dependency": "Check platform SDK and connector dependencies before any live run.",
        "dry_run": "Use a dry run to inspect commands before any live run.",
        "no_secrets": "Configure platform connection credentials in your shell env or config files. Do NOT paste credentials in chat."
      }
    },
    {
      "cost_class": "free",
      "dependency_check_command": "uv run benchbox check-deps --platform velox",
      "dependency_check_platform": "velox",
      "deployments": [
        "local",
        "self-hosted"
      ],
      "id": "velox",
      "install_command": "uv add benchbox --extra velox",
      "interfaces": [
        "sql"
      ],
      "label": "Apache Gluten + Velox",
      "platform_option_hints": [
        "--platform-option endpoint=sc://host:50051  # Spark Connect; select with --platform velox:remote"
      ]
    }
  ],
  "runtime_hints": {
    "log_dir": "/tmp",
    "log_slug_template": "bench_{platform}_{benchmark}_{scale}",
    "long_run_threshold_scale": "0.1"
  },
  "scales": [
    "0.01",
    "0.1",
    "1.0",
    "10.0"
  ],
  "surfaces": [
    {
      "id": "cli",
      "label": "CLI"
    },
    {
      "id": "mcp",
      "label": "MCP"
    }
  ],
  "templates": {
    "cli": {
      "capture_plans_footer": "To capture EXPLAIN plans, insert `--capture-plans` in the benchbox command before `2>&1 | tee ...` (for example: `set -o pipefail; uv run benchbox run ... --capture-plans 2>&1 | tee <log>`), then inspect with `benchbox show-plan --run <result-json> --query-id Q1`.",
      "compare": "uv run benchbox compare --platform {platform_a} --platform {platform_b} --benchmark {benchmark} --scale {scale} --non-interactive",
      "dependency_check": "uv run benchbox check-deps --platform {platform}",
      "dry_run": "uv run benchbox run --dry-run {dry_run_dir} --platform {platform} --benchmark {benchmark} --scale {scale}",
      "force_datagen_footer": "If the load phase fails partway, re-run with `--force datagen` appended.",
      "provenance_snapshot": "mkdir -p <bundle-dir>/_provenance\nuv run benchbox --version-json > <bundle-dir>/_provenance/version.json\nuv run benchbox profile > <bundle-dir>/_provenance/system.txt\necho \"<exact command line used>\" > <bundle-dir>/_provenance/command.txt\n",
      "results_paths": "uv run benchbox results --paths --limit 1",
      "show_cli": "uv run benchbox results show-cli <result-json>",
      "test_one": "uv run benchbox run --platform {platform} --benchmark {benchmark} --scale {scale} --non-interactive"
    }
  }
};
