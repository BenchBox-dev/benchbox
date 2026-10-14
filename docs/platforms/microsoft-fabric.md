<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Microsoft Fabric

```{tags} intermediate, guide, fabric, cloud-platform
```

BenchBox supports Microsoft Fabric Warehouse for read/write SQL benchmarking, plus separate Fabric Spark and Fabric Lakehouse SQL adapters for Lakehouse workflows.

## Overview

| Property | Value |
|----------|-------|
| **Platform Name** | `fabric-dw` (or legacy `fabric_dw`) |
| **SQL Dialect** | T-SQL (SQL Server subset) |
| **Authentication** | Entra ID only (service principal, default credential, interactive) |
| **Data Loading** | COPY INTO via OneLake staging |
| **Status** | Production-ready for Warehouse items |

### Important Limitations (Warehouse Adapter)

This adapter **ONLY supports Microsoft Fabric Warehouse items**:

| Fabric Item | Support Status | Reason |
|-------------|----------------|--------|
| **Warehouse** | Supported | Full T-SQL DDL/DML support |
| **Lakehouse** | Supported via `fabric-lakehouse` | SQL Analytics Endpoint is READ-ONLY |
| **KQL Database** | Not Supported | Kusto Query Language, not T-SQL |
| **Mirrored Database** | Not Supported | Different architecture |

For Lakehouse benchmarking, use Spark for load (`fabric-spark`) and SQL endpoint for query-only phases (`fabric-lakehouse`).

## Installation

```bash
uv add benchbox --extra fabric

pip install "benchbox[fabric]"
```

The first command installs BenchBox with the Fabric dependencies using uv, and the second does the same with pip.

### Required Dependencies

- `pyodbc` - ODBC driver connectivity
- `azure-identity` - Entra ID authentication
- `azure-storage-file-datalake` - OneLake staging
- **ODBC Driver 18 for SQL Server** - System driver (required)

### System Requirements

**macOS:**
```bash
brew install unixodbc msodbcsql18
```

**Ubuntu/Debian:**
```bash
curl https://packages.microsoft.com/keys/microsoft.asc | sudo apt-key add -
curl https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/prod.list | sudo tee /etc/apt/sources.list.d/mssql-release.list
sudo apt-get update
sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18
```

**Windows:**
Download from [Microsoft Download Center](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server).

## Quick Start

### CLI Usage

```bash
benchbox run --platform fabric-dw --benchmark tpch --scale 0.1 \
  --platform-option workspace=your-workspace-guid \
  --platform-option database=your_warehouse_name

benchbox run --platform fabric-dw --benchmark tpch --scale 1 \
  --platform-option workspace=your-workspace-guid \
  --platform-option database=your_warehouse_name \
  --platform-option auth_method=service_principal \
  --platform-option tenant_id=your-tenant-id \
  --platform-option client_id=your-client-id \
  --platform-option client_secret=your-secret
```

The first command runs TPC-H on a Fabric Warehouse. The second does the same with service principal authentication.

### Programmatic Usage

```python
from benchbox.platforms.fabric_warehouse import FabricWarehouseAdapter
from benchbox import TPCH

adapter = FabricWarehouseAdapter(
    workspace="your-workspace-guid",
    database="your_warehouse_name",
    auth_method="default_credential"
)

benchmark = TPCH(scale_factor=0.1)

benchmark.generate_data()

adapter.load_benchmark_data(benchmark)

results = adapter.run_benchmark(benchmark)
```

The `default_credential` authentication method uses the Azure CLI or a managed identity.

## Authentication Methods

### Default Credential (Recommended)

Uses Azure CLI, managed identity, or environment variables:

```python
adapter = FabricWarehouseAdapter(
    workspace="your-workspace-guid",
    database="your_warehouse_name",
    auth_method="default_credential"
)
```

**Prerequisites:**
- Azure CLI logged in: `az login`
- Or running on Azure with managed identity
- Or set environment variables:
  - `AZURE_CLIENT_ID`
  - `AZURE_CLIENT_SECRET`
  - `AZURE_TENANT_ID`

### Service Principal

For automated/CI environments:

```python
adapter = FabricWarehouseAdapter(
    workspace="your-workspace-guid",
    database="your_warehouse_name",
    auth_method="service_principal",
    tenant_id="your-tenant-id",
    client_id="your-client-id",
    client_secret="your-client-secret"
)
```

**Required Permissions:**
- Fabric workspace member (Contributor or Admin)
- OneLake data access for staging

### Interactive Browser

For development/testing:

```python
adapter = FabricWarehouseAdapter(
    workspace="your-workspace-guid",
    database="your_warehouse_name",
    auth_method="interactive"
)
```

Opens browser for Entra ID login.

## Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `workspace` | str | Required | Fabric workspace GUID |
| `warehouse` | str | Required | Warehouse item name |
| `auth_method` | str | `default_credential` | Authentication method |
| `tenant_id` | str | - | Azure tenant ID (for service principal) |
| `client_id` | str | - | Service principal client ID |
| `client_secret` | str | - | Service principal client secret |
| `staging_path` | str | `benchbox-staging` | OneLake staging path |
| `schema` | str | `dbo` | Default schema |
| `connect_timeout` | int | 30 | Connection timeout (seconds) |
| `query_timeout` | int | 0 | Query timeout (0 = no limit) |
| `disable_result_cache` | bool | True | Disable result caching |
| `driver` | str | `ODBC Driver 18 for SQL Server` | ODBC driver name |

## OneLake Data Staging

Microsoft Fabric Warehouse uses OneLake for data staging during bulk loads:

```
https://onelake.dfs.fabric.microsoft.com/{workspace}/{warehouse}.Warehouse/Files/benchbox-staging/
```

### How Data Loading Works

1. **Upload to OneLake**: Data files uploaded to staging path
2. **COPY INTO**: SQL command loads from OneLake path
3. **Cleanup**: Staging files removed after load

```sql
COPY INTO dbo.lineitem
FROM 'https://onelake.dfs.fabric.microsoft.com/{workspace}/{warehouse}.Warehouse/Files/benchbox-staging/lineitem.csv'
WITH (
    FILE_TYPE = 'CSV',
    FIELDTERMINATOR = '|',
    FIRSTROW = 2
);
```

## SQL Dialect Notes

Fabric uses T-SQL with some limitations compared to SQL Server:

### Supported

- Standard T-SQL queries
- CTEs (Common Table Expressions)
- Window functions
- Aggregate functions
- Joins (including hash, merge, nested loop)

### Not Supported

- **Clustered indexes** - Fabric uses automatic indexing
- **Distribution keys** - Automatic distribution management
- **Partitioning** - Fabric handles internally
- **V-Order optimization** - Spark-only feature (Lakehouse)
- **Some T-SQL features** - Cursor types, certain stored procedures

### Query Translation

BenchBox automatically translates queries for Fabric:

```python
from benchbox import TPCH

benchmark = TPCH(scale_factor=1.0)
query = benchmark.get_query(1)
```

TPC-H queries are translated from the DuckDB dialect, so `get_query` returns a T-SQL compatible query.

## Performance Considerations

### Result Cache

By default, BenchBox disables Fabric's result cache for accurate benchmarking:

```python
adapter = FabricWarehouseAdapter(
    workspace="...",
    database="...",
    disable_result_cache=True
)
```

`disable_result_cache=True` is the default.

To enable (for production-like testing):

```python
adapter = FabricWarehouseAdapter(
    workspace="...",
    database="...",
    disable_result_cache=False
)
```

### Capacity Considerations

Fabric uses Capacity Units (CUs) for billing:

- **F2**: 2 CUs - Development/testing
- **F4-F64**: 4-64 CUs - Production workloads
- **F128+**: Large-scale analytics

**Recommendations:**
- Use F4+ for TPC-H scale factor > 1
- Use F16+ for TPC-DS
- Consider pausing capacity between benchmark runs

### Cost Optimization

```bash
az fabric capacity suspend --capacity-name my-capacity

az fabric capacity resume --capacity-name my-capacity
```

Use the Azure CLI to pause Fabric capacity between runs, and resume it when you are ready to benchmark.

## Troubleshooting

### Connection Errors

**"Login timeout expired"**
```
# Check ODBC driver installation
odbcinst -q -d

# Verify workspace/warehouse names
az fabric workspace list
```

**"Cannot open server"**
- Verify workspace GUID is correct
- Check network connectivity to `*.datawarehouse.fabric.microsoft.com`
- Ensure same-region deployment (cross-region not supported)

### Authentication Errors

**"AADSTS700016: Application not found"**
- Verify service principal exists in correct tenant
- Check client ID is correct

**"AADSTS7000215: Invalid client secret"**
- Regenerate client secret
- Check for expired credentials

### Data Loading Errors

**"Access to OneLake denied"**
- Verify service principal has OneLake access
- Check workspace permissions include data access

**"COPY INTO failed"**
- Verify staging path exists
- Check file format matches data
- Ensure column count matches table schema

## Differences from Azure Synapse Analytics

| Feature | Microsoft Fabric Warehouse | Azure Synapse Analytics |
|---------|----------------------------|-------------------------|
| Authentication | Entra ID only | SQL + Entra ID |
| Storage | OneLake | Azure Blob/ADLS |
| Distribution | Automatic | User-specified |
| Indexing | Automatic | Manual |
| File Format | Delta Lake | Multiple |
| Cross-region | Not supported | Supported |
| Pricing | Capacity Units | DWU/cDWU |

## Related Documentation

- [Azure Platforms Overview](azure-platforms.md)
- [Platform Selection Guide](platform-selection-guide.md)
- [TPC-H Benchmark](../benchmarks/tpc-h.md)
- [TPC-DS Benchmark](../benchmarks/tpc-ds.md)
- [Microsoft Fabric Documentation](https://learn.microsoft.com/en-us/fabric/)
