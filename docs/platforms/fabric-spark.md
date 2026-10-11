<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Microsoft Fabric Spark Platform

```{tags} intermediate, guide, fabric-spark, cloud-platform
```

Microsoft Fabric is Microsoft's unified analytics platform providing SaaS Spark, Data Factory, Power BI, and more. BenchBox integrates with Fabric's Spark pools via the Livy API for benchmark execution with OneLake storage.

## Features

- **SaaS** - Fully managed, no infrastructure to configure
- **OneLake** - Unified storage with automatic lakehouse semantics
- **Delta Lake** - Native Delta format support for tables
- **Entra ID** - Azure Active Directory authentication
- **Livy API** - Apache Livy REST API for Spark session management

## Installation

```bash
uv add benchbox --extra cloud-spark-azure
```

This installs Azure cloud-Spark dependencies, including `azure-identity`, `azure-storage-file-datalake`, and `requests`.

## Prerequisites

1. **Microsoft Fabric Workspace** with Spark capabilities
2. **Lakehouse** created in the workspace
3. **Azure Entra ID** authentication configured:
   - Interactive: `az login`
   - Service principal: Environment variables
   - Managed identity: On Azure VMs

## Configuration

### Environment Variables

`FABRIC_WORKSPACE_ID` and `FABRIC_LAKEHOUSE_ID` are required. `AZURE_TENANT_ID` and `FABRIC_SPARK_POOL` are optional.

```bash
export FABRIC_WORKSPACE_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
export FABRIC_LAKEHOUSE_ID=yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy

export AZURE_TENANT_ID=zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz
export FABRIC_SPARK_POOL=my-spark-pool
```

### CLI Usage

The first command is basic usage. The second adds the tenant ID for service principal authentication. The third is a dry run that previews queries.

```bash
benchbox run --platform fabric-spark --benchmark tpch --scale 1.0 \
  --platform-option workspace_id=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx \
  --platform-option lakehouse_id=yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy

benchbox run --platform fabric-spark --benchmark tpch --scale 1.0 \
  --platform-option workspace_id=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx \
  --platform-option lakehouse_id=yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy \
  --platform-option tenant_id=zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz

benchbox run --platform fabric-spark --benchmark tpch --dry-run ./preview \
  --platform-option workspace_id=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx \
  --platform-option lakehouse_id=yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy
```

### Platform Options

| Option | Default | Description |
|--------|---------|-------------|
| `workspace_id` | *required* | Fabric workspace GUID |
| `lakehouse_id` | *required* | Fabric Lakehouse GUID |
| `tenant_id` | - | Azure tenant ID (for service principal) |
| `livy_endpoint` | auto-derived | Custom Livy API endpoint URL |
| `onelake_path` | auto-derived | OneLake path for data staging |
| `compute_resource` | - | Named Spark pool (deprecated alias: `spark_pool_name`, removed in 0.6.0) |
| `timeout_minutes` | 60 | Statement timeout in minutes |

## Python API

`tenant_id` is optional. `create_schema()` is also optional, because the lakehouse manages schemas automatically. `load_data()` loads data to OneLake and creates Delta tables, `execute_query()` runs the query through Livy, and `close()` cleans up the session.

```python
from benchbox.platforms.azure import FabricSparkAdapter

adapter = FabricSparkAdapter(
    workspace_id="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    lakehouse_id="yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy",
    tenant_id="zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz",
)

adapter.create_connection()

adapter.create_schema("tpch_benchmark")

adapter.load_data(
    tables=["lineitem", "orders", "customer"],
    source_dir="/path/to/tpch/data",
)

result = adapter.execute_query("SELECT COUNT(*) FROM lineitem")

adapter.close()
```

## Execution Model

Fabric Spark executes benchmarks via the Livy API:

1. **Session Creation** - Livy session created in Spark pool
2. **Statement Execution** - SQL submitted as Livy statements
3. **Result Retrieval** - Results returned via Livy output
4. **Data Staging** - OneLake used for data storage
5. **Session Cleanup** - Session closed after completion

## Authentication

Fabric Spark uses Azure Entra ID (Azure AD) for authentication:

### Interactive Login (Development)

```bash
az login
```

### Service Principal (Automation)

```bash
export AZURE_CLIENT_ID=app-client-id
export AZURE_CLIENT_SECRET=app-client-secret
export AZURE_TENANT_ID=tenant-id
```

### Managed Identity (Azure VMs)

No configuration needed - automatically uses VM's managed identity.

## Cost Estimation

Fabric uses Capacity Units (CU) for billing:

| SKU | vCores | Memory | Price/Hour |
|-----|--------|--------|------------|
| F2 | 2 | 10 GB | ~$0.36 |
| F4 | 4 | 20 GB | ~$0.72 |
| F8 | 8 | 40 GB | ~$1.44 |
| F16 | 16 | 80 GB | ~$2.88 |

| Scale Factor | Data Size | Est. Runtime | Est. Cost |
|--------------|-----------|--------------|-----------|
| 0.01 | ~10 MB | ~15 min | ~$0.20 |
| 1.0 | ~1 GB | ~1 hour | ~$1.00 |
| 10.0 | ~10 GB | ~3 hours | ~$5.00 |
| 100.0 | ~100 GB | ~8 hours | ~$15.00 |

*Estimates based on F4 SKU. Actual costs vary by SKU and workload.*

## Workspace Setup

### Creating a Lakehouse

1. Go to your Fabric workspace
2. Click **New** > **Lakehouse**
3. Name your lakehouse (e.g., "benchbox_tpch")
4. Note the Lakehouse ID from the URL or properties

### Finding IDs

**Workspace ID**: Found in the workspace URL:
```
https://app.fabric.microsoft.com/groups/{WORKSPACE_ID}/...
```

**Lakehouse ID**: Found in the lakehouse URL:
```
https://app.fabric.microsoft.com/groups/.../lakehouses/{LAKEHOUSE_ID}
```

Or via PowerShell:
```powershell
Get-FabricWorkspace

Get-FabricLakehouse -WorkspaceId $workspaceId
```

## Troubleshooting

### Common Issues

**Authentication fails:**
- Run `az login` for interactive authentication
- Check service principal credentials if using automation
- Verify tenant ID is correct

**Workspace not found:**
- Verify workspace ID is a valid GUID
- Check you have access to the workspace
- Ensure workspace has Fabric capacity assigned

**Session creation fails:**
- Check Fabric capacity is running (not paused)
- Verify Spark pool is enabled in workspace
- Review Fabric capacity limits

**Query timeout:**
- Increase `timeout_minutes` for large scale factors
- Check for data skew in queries
- Consider using larger capacity SKU

## Comparison with Other Platforms

| Aspect | Fabric Spark | Synapse Spark | Databricks |
|--------|-------------|---------------|------------|
| Deployment | SaaS | PaaS | SaaS |
| Storage | OneLake | ADLS Gen2 | DBFS/Unity |
| Billing | Capacity Units | vCores | DBUs |
| Startup time | Seconds | Minutes | Seconds |
| Integration | Power BI, DF | Azure ecosystem | MLflow, SQL |

## Related

- [Apache Spark Platform](spark.md) - Local/cluster Spark usage
- [Databricks Platform](databricks.md) - Databricks SQL and Spark
- [Cloud Platforms Overview](cloud-platforms.md) - All cloud platforms
