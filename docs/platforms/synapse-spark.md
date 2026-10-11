<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Azure Synapse Analytics Spark Platform

```{tags} intermediate, guide, synapse-spark, cloud-platform
```

Azure Synapse Analytics is Microsoft's enterprise analytics platform providing integrated Spark, SQL, and Data Explorer capabilities. BenchBox integrates with Synapse Spark pools via the Livy API for benchmark execution with ADLS Gen2 storage.

## Features

- **Enterprise** - Mature platform with extensive enterprise features
- **ADLS Gen2** - Azure Data Lake Storage integration
- **Spark Pools** - Dedicated pools with configurable sizing
- **Entra ID** - Azure Active Directory authentication
- **Integration** - Native integration with Synapse SQL pools

## Installation

This installs the Azure cloud-Spark dependencies, including `azure-identity`, `azure-storage-file-datalake`, and `requests`.

```bash
uv add benchbox --extra cloud-spark-azure
```

## Prerequisites

1. **Azure Synapse Analytics workspace**
2. **Spark pool** created in the workspace
3. **ADLS Gen2 storage account** linked to workspace
4. **Azure Entra ID** authentication configured:
   - Interactive: `az login`
   - Service principal: Environment variables
   - Managed identity: On Azure VMs

## Configuration

### Environment Variables

`SYNAPSE_WORKSPACE_NAME`, `SYNAPSE_SPARK_POOL`, `SYNAPSE_STORAGE_ACCOUNT` and `SYNAPSE_STORAGE_CONTAINER` are
required. `AZURE_TENANT_ID` and `SYNAPSE_STORAGE_PATH` are optional.

```bash
export SYNAPSE_WORKSPACE_NAME=my-synapse-workspace
export SYNAPSE_SPARK_POOL=sparkpool1
export SYNAPSE_STORAGE_ACCOUNT=mystorageaccount
export SYNAPSE_STORAGE_CONTAINER=benchbox

export AZURE_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
export SYNAPSE_STORAGE_PATH=data/benchbox
```

### CLI Usage

The commands cover basic usage, usage with a storage path, and a dry run that previews queries.

```bash
benchbox run --platform synapse-spark --benchmark tpch --scale 1.0 \
  --platform-option workspace_name=my-synapse-workspace \
  --platform-option compute_resource=sparkpool1 \
  --platform-option storage_account=mystorageaccount \
  --platform-option storage_container=benchbox

benchbox run --platform synapse-spark --benchmark tpch --scale 1.0 \
  --platform-option workspace_name=my-synapse-workspace \
  --platform-option compute_resource=sparkpool1 \
  --platform-option storage_account=mystorageaccount \
  --platform-option storage_container=benchbox \
  --platform-option storage_path=data/benchbox

benchbox run --platform synapse-spark --benchmark tpch --dry-run ./preview \
  --platform-option workspace_name=my-synapse-workspace \
  --platform-option compute_resource=sparkpool1 \
  --platform-option storage_account=mystorageaccount \
  --platform-option storage_container=benchbox
```

### Platform Options

| Option | Default | Description |
|--------|---------|-------------|
| `workspace_name` | *required* | Synapse workspace name |
| `spark_pool_name` | *required* | Spark pool name |
| `storage_account` | *required* | ADLS Gen2 storage account name |
| `storage_container` | *required* | ADLS Gen2 container name |
| `storage_path` | benchbox | Path within container for staging |
| `tenant_id` | - | Azure tenant ID (for service principal) |
| `livy_endpoint` | auto-derived | Custom Livy API endpoint URL |
| `timeout_minutes` | 60 | Statement timeout in minutes |

## Python API

The example initializes the adapter with a workspace and storage (`tenant_id` is optional), verifies the connection,
creates the schema, loads data to ADLS and creates tables, executes a query via Livy, and cleans up the session.

```python
from benchbox.platforms.azure import SynapseSparkAdapter

adapter = SynapseSparkAdapter(
    workspace_name="my-synapse-workspace",
    compute_resource="sparkpool1",
    storage_account="mystorageaccount",
    storage_container="benchbox",
    tenant_id="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
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

Synapse Spark executes benchmarks via the Livy API:

1. **Pool Startup** - Spark pool started (or uses running pool)
2. **Session Creation** - Livy session created in pool
3. **Statement Execution** - SQL submitted as Livy statements
4. **Result Retrieval** - Results returned via Livy output
5. **Session Cleanup** - Session closed after completion

## Spark Pool Configuration

### Node Sizes

| Size | vCores | Memory | Use Case |
|------|--------|--------|----------|
| Small | 4 | 32 GB | Development, small datasets |
| Medium | 8 | 64 GB | General benchmarking |
| Large | 16 | 128 GB | Large scale factors |
| XLarge | 32 | 256 GB | Enterprise workloads |
| XXLarge | 64 | 512 GB | Maximum performance |

### Auto-Pause and Auto-Scale

```
Auto-pause: Pool pauses after idle timeout (saves costs)
Auto-scale: Pool scales nodes based on workload
Min nodes: Minimum nodes to keep warm
Max nodes: Maximum nodes for scaling
```

## Authentication

Synapse Spark uses Azure Entra ID (Azure AD) for authentication:

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

Synapse Spark uses vCore-hour billing:

| Node Size | vCores | Price/Hour |
|-----------|--------|------------|
| Small | 4 | ~$0.22 |
| Medium | 8 | ~$0.44 |
| Large | 16 | ~$0.88 |
| XLarge | 32 | ~$1.76 |

| Scale Factor | Data Size | Est. Runtime | Est. Cost* |
|--------------|-----------|--------------|------------|
| 0.01 | ~10 MB | ~15 min | ~$0.30 |
| 1.0 | ~1 GB | ~1 hour | ~$2.00 |
| 10.0 | ~10 GB | ~3 hours | ~$8.00 |
| 100.0 | ~100 GB | ~8 hours | ~$25.00 |

*Estimates based on 3-node Medium pool. Actual costs vary.

## Workspace Setup

### Creating a Spark Pool

1. Go to Synapse Studio
2. Navigate to **Manage** > **Apache Spark pools**
3. Click **New** to create pool
4. Configure:
   - Name: e.g., "sparkpool1"
   - Node size: Medium (8 vCores)
   - Auto-scale: Enable
   - Auto-pause: Enable (15 min idle)

### Linking Storage

1. Go to **Manage** > **Linked services**
2. Click **New** > **Azure Data Lake Storage Gen2**
3. Configure:
   - Name: Primary storage link
   - Authentication: Managed identity
   - Account: Your ADLS Gen2 account

## Troubleshooting

### Common Issues

**Authentication fails:**
- Run `az login` for interactive authentication
- Check service principal credentials
- Verify tenant ID is correct

**Spark pool not starting:**
- Check pool auto-start is enabled
- Verify pool isn't at max capacity
- Review Azure resource quotas

**Storage access denied:**
- Check Storage Blob Data Contributor role
- Verify managed identity is configured
- Check container exists and is accessible

**Session timeout:**
- Increase `timeout_minutes` for large scale factors
- Check pool hasn't auto-paused
- Review session idle timeout settings

## Comparison with Other Azure Platforms

| Aspect | Synapse Spark | Fabric Spark | Databricks |
|--------|--------------|--------------|------------|
| Deployment | PaaS | SaaS | SaaS |
| Storage | ADLS Gen2 | OneLake | DBFS/Unity |
| Billing | vCore-hours | Capacity Units | DBUs |
| Integration | Synapse SQL | Power BI | MLflow |
| Maturity | Established | New | Established |

## Related

- [Microsoft Fabric Spark](fabric-spark.md) - SaaS Spark platform
- [Apache Spark Platform](spark.md) - Local/cluster Spark usage
- [Azure Synapse SQL](azure-platforms.md) - Dedicated SQL Pool
- [Cloud Platforms Overview](cloud-platforms.md) - All cloud platforms
