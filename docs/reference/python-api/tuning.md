---
myst:
  enable_extensions:
    - attrs_block
---
<!-- markdownlint-disable MD024 -->

# Tuning Configuration API

```{tags} reference, python-api, tuning
```

Complete Python API reference for database tuning and optimization configuration.

## Overview

The tuning configuration API provides a comprehensive system for managing database table optimizations including partitioning, clustering, distribution, sorting, constraints, and platform-specific features. It supports serialization, validation, and platform compatibility checking.

**Key Features**:

- **Table-level tunings** - Partitioning, clustering, distribution, sorting
- **Schema constraints** - Primary keys, foreign keys, unique, check constraints
- **Platform-specific optimizations** - Z-ordering, auto-optimize, bloom filters
- **Validation** - Conflict detection and platform compatibility checks
- **Serialization** - JSON-based configuration persistence
- **Cross-platform support** - 9+ database platforms

## Quick Start

```python
from benchbox.core.tuning.interface import (
    UnifiedTuningConfiguration,
    TuningType
)

config = UnifiedTuningConfiguration()

config.disable_foreign_keys()

config.enable_platform_optimization(
    TuningType.Z_ORDERING,
    columns=["order_date", "customer_key"]
)

errors = config.validate_for_platform("databricks")
if errors:
    print(f"Validation errors: {errors}")
```

Disabling foreign keys speeds up data loading.

## API Reference

### TuningType Enum

Enumeration of supported database tuning types.

```python
class TuningType(Enum):
    PARTITIONING = "partitioning"
    CLUSTERING = "clustering"
    DISTRIBUTION = "distribution"
    SORTING = "sorting"

    PRIMARY_KEYS = "primary_keys"
    FOREIGN_KEYS = "foreign_keys"
    UNIQUE_CONSTRAINTS = "unique_constraints"
    CHECK_CONSTRAINTS = "check_constraints"

    Z_ORDERING = "z_ordering"
    LIQUID_CLUSTERING = "liquid_clustering"
    AUTO_OPTIMIZE = "auto_optimize"
    AUTO_COMPACT = "auto_compact"
    BLOOM_FILTERS = "bloom_filters"
    MATERIALIZED_VIEWS = "materialized_views"
```

The values fall into three groups:

- `PARTITIONING`, `CLUSTERING`, `DISTRIBUTION` and `SORTING` are table-level performance tunings.
- `PRIMARY_KEYS`, `FOREIGN_KEYS`, `UNIQUE_CONSTRAINTS` and `CHECK_CONSTRAINTS` are schema constraint tunings.
- The rest are platform-specific optimizations: `Z_ORDERING` and `LIQUID_CLUSTERING` (Databricks Delta Lake), `AUTO_OPTIMIZE` and `AUTO_COMPACT` (Databricks), `BLOOM_FILTERS` (various platforms) and `MATERIALIZED_VIEWS` (query acceleration).

**Methods**:

- **from_string(value: str)**: Create TuningType from string
- **is_compatible_with_platform(platform: str)**: Check platform compatibility

**Platform Compatibility**:

```python
from benchbox.core.tuning.interface import TuningType

is_supported = TuningType.Z_ORDERING.is_compatible_with_platform("databricks")

is_supported = TuningType.Z_ORDERING.is_compatible_with_platform("duckdb")
```

The first call returns `True` because Databricks supports Z-ordering. The second returns `False` because DuckDB does not.

### TuningColumn Class

Represents a column used in table tuning configurations.

```python
@dataclass
class TuningColumn:
    name: str
    type: str
    order: int
    sort_order: str = "ASC"
    nulls_position: str = "DEFAULT"
    compression: Optional[str] = None
```

`name` is the column name, `type` is the SQL data type (for example `'DATE'` or `'INTEGER'`), and `order` is the column's 1-based position in the tuning. `sort_order` is `"ASC"` or `"DESC"`. `nulls_position` is `"FIRST"`, `"LAST"` or `"DEFAULT"`.

**Methods**:

- **to_dict()**: Convert to dictionary
- **from_dict(data)**: Create from dictionary

**Example**:

```python
from benchbox.core.tuning.interface import TuningColumn

col = TuningColumn(
    name="order_date",
    type="DATE",
    order=1
)

col_dict = col.to_dict()

col_restored = TuningColumn.from_dict(col_dict)
```

`to_dict()` returns `{'name': 'order_date', 'type': 'DATE', 'order': 1}`, which `from_dict` turns back into a `TuningColumn`.

### TableTuning Class

Represents the complete tuning configuration for a database table.

```python
@dataclass
class TableTuning:
    table_name: str
    partitioning: Optional[list[TuningColumn]] = None
    clustering: Optional[list[TuningColumn]] = None
    distribution: Optional[list[TuningColumn]] = None
    sorting: Optional[list[TuningColumn]] = None
```

**Methods**:

- **validate()**: Validate configuration for conflicts
- **has_any_tuning()**: Check if any tuning is configured
- **get_columns_by_type(tuning_type)**: Get columns for specific tuning type
- **get_all_columns()**: Get all column names used in tuning
- **to_dict()**: Convert to dictionary
- **from_dict(data)**: Create from dictionary

**Example**:

```python
from benchbox.core.tuning.interface import TableTuning, TuningColumn

lineitem_tuning = TableTuning(
    table_name="lineitem",
    partitioning=[
        TuningColumn(name="l_shipdate", type="DATE", order=1)
    ],
    sorting=[
        TuningColumn(name="l_orderkey", type="BIGINT", order=1),
        TuningColumn(name="l_linenumber", type="INTEGER", order=2)
    ]
)

errors = lineitem_tuning.validate()
if errors:
    print(f"Validation errors: {errors}")

all_cols = lineitem_tuning.get_all_columns()
```

`get_all_columns()` returns every column used in the tuning, here `{'l_shipdate', 'l_orderkey', 'l_linenumber'}`.

### BenchmarkTunings Class

Manages tuning configurations for all tables in a benchmark.

```python
@dataclass
class BenchmarkTunings:
    benchmark_name: str
    table_tunings: dict[str, TableTuning] = field(default_factory=dict)
    enable_primary_keys: bool = True
    enable_foreign_keys: bool = True
```

**Methods**:

- **add_table_tuning(table_tuning)**: Add table tuning configuration
- **update_table_tuning(table_tuning)**: Update existing table tuning
- **get_table_tuning(table_name)**: Get tuning for specific table
- **remove_table_tuning(table_name)**: Remove table tuning
- **get_table_names()**: Get list of all table names
- **disable_primary_keys()**: Disable PK constraints
- **disable_foreign_keys()**: Disable FK constraints
- **enable_all_constraints()**: Enable all constraints
- **disable_all_constraints()**: Disable all constraints
- **validate_all()**: Validate all table tunings
- **has_valid_tunings()**: Check if all tunings are valid
- **get_configuration_hash()**: Generate SHA-256 hash of configuration
- **to_dict()**: Convert to dictionary
- **from_dict(data)**: Create from dictionary

**Example**:

```python
from benchbox.core.tuning.interface import BenchmarkTunings, TableTuning, TuningColumn

tunings = BenchmarkTunings(benchmark_name="tpch")

orders_tuning = TableTuning(
    table_name="orders",
    partitioning=[TuningColumn("o_orderdate", "DATE", 1)],
    sorting=[TuningColumn("o_orderkey", "BIGINT", 1)]
)
tunings.add_table_tuning(orders_tuning)

tunings.disable_foreign_keys()

validation_results = tunings.validate_all()
for table, errors in validation_results.items():
    if errors:
        print(f"{table}: {errors}")

config_hash = tunings.get_configuration_hash()
print(f"Configuration hash: {config_hash[:16]}...")
```

### UnifiedTuningConfiguration Class

Unified configuration that consolidates all tuning options.

```python
@dataclass
class UnifiedTuningConfiguration:
    primary_keys: PrimaryKeyConfiguration
    foreign_keys: ForeignKeyConfiguration
    unique_constraints: UniqueConstraintConfiguration
    check_constraints: CheckConstraintConfiguration

    platform_optimizations: PlatformOptimizationConfiguration

    table_tunings: dict[str, TableTuning]
```

**Methods**:

- **enable_all_constraints()**: Enable all schema constraints
- **disable_all_constraints()**: Disable all schema constraints
- **enable_primary_keys()**: Enable primary key constraints
- **disable_primary_keys()**: Disable primary key constraints
- **enable_foreign_keys()**: Enable foreign key constraints
- **disable_foreign_keys()**: Disable foreign key constraints
- **enable_platform_optimization(optimization_type, \\*\\*kwargs)**: Enable platform optimization
- **disable_platform_optimization(optimization_type)**: Disable platform optimization
- **get_enabled_tuning_types()**: Get all enabled tuning types
- **validate_for_platform(platform)**: Validate against platform capabilities
- **to_dict()**: Convert to dictionary
- **from_dict(data)**: Create from dictionary
- **merge_with_legacy_config(benchmark_tunings)**: Merge with legacy config
- **to_legacy_config(benchmark_name)**: Convert to legacy format

**Example**:

```python
from benchbox.core.tuning.interface import UnifiedTuningConfiguration, TuningType

config = UnifiedTuningConfiguration()

config.disable_foreign_keys()
config.primary_keys.enforce_uniqueness = True

config.enable_platform_optimization(
    TuningType.Z_ORDERING,
    columns=["order_date", "customer_key"]
)
config.enable_platform_optimization(TuningType.AUTO_OPTIMIZE)

errors = config.validate_for_platform("databricks")
if not errors:
    print("Configuration valid for Databricks")

enabled = config.get_enabled_tuning_types()
print(f"Enabled tunings: {[t.value for t in enabled]}")
```

### Constraint Configurations

#### PrimaryKeyConfiguration

```python
@dataclass
class PrimaryKeyConfiguration:
    enabled: bool = True
    enforce_uniqueness: bool = True
    nullable: bool = False
```

#### ForeignKeyConfiguration

```python
@dataclass
class ForeignKeyConfiguration:
    enabled: bool = True
    enforce_referential_integrity: bool = True
    on_delete_action: str = "RESTRICT"
    on_update_action: str = "RESTRICT"
```

`on_delete_action` accepts `RESTRICT`, `CASCADE`, `SET NULL` or `SET DEFAULT`.

#### UniqueConstraintConfiguration

```python
@dataclass
class UniqueConstraintConfiguration:
    enabled: bool = True
    ignore_nulls: bool = False
```

#### CheckConstraintConfiguration

```python
@dataclass
class CheckConstraintConfiguration:
    enabled: bool = True
    enforce_on_insert: bool = True
    enforce_on_update: bool = True
```

### Platform Optimization Configuration

```python
@dataclass
class PlatformOptimizationConfiguration:
    z_ordering_enabled: bool = False
    z_ordering_columns: list[str] = field(default_factory=list)
    auto_optimize_enabled: bool = False
    auto_compact_enabled: bool = False
    bloom_filters_enabled: bool = False
    bloom_filter_columns: list[str] = field(default_factory=list)
    materialized_views_enabled: bool = False
```

## Contract Reference

Contract sections for the four tuning classes that other pages import. They describe what each class does when called; the sections above show how the classes fit together.

### `benchbox.core.tuning.interface.TuningType`

<span id="benchbox.core.tuning.interface.TuningType"></span>

An enumeration of the 14 tuning types BenchBox can request, with checks for platform support.

**Import:** `from benchbox.core.tuning.interface import TuningType` · **Extras:** none

{#tuning-type-members}

#### Members

| Group | Members (value is the lower-case member name) |
| --- | --- |
| Table layout | `PARTITIONING`, `CLUSTERING`, `DISTRIBUTION`, `SORTING` |
| Constraints | `PRIMARY_KEYS`, `FOREIGN_KEYS`, `UNIQUE_CONSTRAINTS`, `CHECK_CONSTRAINTS` |
| Platform-specific | `Z_ORDERING`, `LIQUID_CLUSTERING`, `AUTO_OPTIMIZE`, `AUTO_COMPACT`, `BLOOM_FILTERS`, `MATERIALIZED_VIEWS` |

`str(TuningType.SORTING)` is `'sorting'`.

{#tuning-type-parameters}

#### Parameters

`TuningType("sorting")` looks a member up by value, as for any `Enum`. The class methods and instance method below are the supported way to build and test members.

| Method | Returns |
| --- | --- |
| `from_string(value: str)` (class method) | The member whose value matches `value`, ignoring case. |
| `is_compatible_with_platform(platform: str)` | `bool`: whether the platform supports this tuning type. The platform is a canonical platform key such as `"duckdb"`; case is ignored. A platform that is not known to BenchBox gets `False` for every type. |
| `is_known_platform(platform: str)` (class method) | `bool`: whether the platform has an entry in the compatibility data. `"duckdb"` is known; `"starrocks"` and `"clickhouse-local"` are not. |

{#tuning-type-returns}

#### Returns

A `TuningType` member.

{#tuning-type-raises}

#### Raises

`ValueError` from `from_string` (`Invalid tuning type: nope`) and from `TuningType("nope")`.

{#tuning-type-example}

#### Example

```python
from benchbox.core.tuning.interface import TuningType

print(len(TuningType), TuningType.from_string("Z_Ordering"), str(TuningType.SORTING))
print(TuningType.Z_ORDERING.is_compatible_with_platform("databricks"), TuningType.Z_ORDERING.is_compatible_with_platform("duckdb"))
print(TuningType.is_known_platform("duckdb"), TuningType.is_known_platform("starrocks"))
try:
    TuningType.from_string("nope")
except ValueError as exc:
    print(exc)
```

Output on 0.4.1:

```text
14 z_ordering sorting
True False
True False
Invalid tuning type: nope
```

### `benchbox.core.tuning.interface.TuningColumn`

<span id="benchbox.core.tuning.interface.TuningColumn"></span>

One column that takes part in a table tuning, with its SQL type, its position and optional sort settings. It is a dataclass that validates its values when it is created.

**Import:** `from benchbox.core.tuning.interface import TuningColumn` · **Extras:** none

{#tuning-column-parameters}

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | `str` | required | The column name. Must start with a letter or underscore and contain only letters, digits and underscores. |
| `type` | `str` | required | The SQL type, such as `"DATE"` or `"DECIMAL(15,2)"`. It is not checked. |
| `order` | `int` | required | The 1-based position among the columns of one tuning type. Must be a positive integer. |
| `sort_order` | `"ASC"` or `"DESC"` | `"ASC"` | Sort direction. |
| `nulls_position` | `"FIRST"`, `"LAST"` or `"DEFAULT"` | `"DEFAULT"` | Where NULLs sort. `"DEFAULT"` leaves it to the platform. |
| `compression` | `str` or `None` | `None` | Platform-specific compression or encoding name. |

{#tuning-column-returns}

#### Returns

A `TuningColumn`.

- `to_dict()` returns a `dict` with `name`, `type` and `order`, plus `sort_order`, `nulls_position` and `compression` only when they differ from their defaults.
- `from_dict(data)` (class method) builds a column from such a dict; the three optional keys may be absent.

{#tuning-column-raises}

#### Raises

`ValueError` for an empty or badly formed `name`, an `order` that is not a positive integer, a `sort_order` or `nulls_position` outside the listed values, and, from `from_dict`, a dict missing `name`, `type` or `order` (`Missing required fields: {...}`).

{#tuning-column-example}

#### Example

```python
from benchbox.core.tuning.interface import TuningColumn

column = TuningColumn(name="o_orderdate", type="DATE", order=1)
print(column.to_dict())
print(TuningColumn("amount", "DECIMAL(15,2)", 2, sort_order="DESC", nulls_position="LAST").to_dict())
print(TuningColumn.from_dict(column.to_dict()) == column)
for kwargs in ({"name": "1a", "type": "INT", "order": 1}, {"name": "a", "type": "INT", "order": 0}, {"name": "a", "type": "INT", "order": 1, "sort_order": "asc"}):
    try:
        TuningColumn(**kwargs)
    except ValueError as exc:
        print(exc)
```

Output on 0.4.1:

```text
{'name': 'o_orderdate', 'type': 'DATE', 'order': 1}
{'name': 'amount', 'type': 'DECIMAL(15,2)', 'order': 2, 'sort_order': 'DESC', 'nulls_position': 'LAST'}
True
Invalid column name format: '1a'. Column names must start with a letter or underscore and contain only letters, numbers, and underscores.
Column order must be a positive integer
Invalid sort_order: 'asc'. Must be 'ASC' or 'DESC'.
```

### `benchbox.core.tuning.interface.TableTuning`

<span id="benchbox.core.tuning.interface.TableTuning"></span>

The tuning of one table: the columns used for partitioning, clustering, distribution and sorting. It is a dataclass.

**Import:** `from benchbox.core.tuning.interface import TableTuning` · **Extras:** none

{#table-tuning-parameters}

#### Parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `table_name` | `str` | required | The table name. Must start with a letter or underscore and contain only letters, digits and underscores. |
| `partitioning` | `list[TuningColumn]` or `None` | `None` | Partition columns. |
| `clustering` | `list[TuningColumn]` or `None` | `None` | Clustering columns. |
| `distribution` | `list[TuningColumn]` or `None` | `None` | Distribution columns. |
| `sorting` | `list[TuningColumn]` or `None` | `None` | Sort columns. |

{#table-tuning-returns}

#### Returns

A `TableTuning`. Creating one checks the name and that every list item is a `TuningColumn`. It does not raise for an empty tuning, duplicate orders or a column used in two tuning types: `validate()` reports those.

| Method | Returns |
| --- | --- |
| `validate()` | `list[str]` of problems, empty when there are none: no tuning at all, duplicate `order` values within one list, and a column name used in more than one tuning type. |
| `has_any_tuning()` | `bool`: `True` when at least one list is not empty. |
| `get_columns_by_type(tuning_type: TuningType)` | `list[TuningColumn]` for `PARTITIONING`, `CLUSTERING`, `DISTRIBUTION` or `SORTING`; an empty list for the others. |
| `get_all_columns()` | `set[str]` of the column names in all four lists. |
| `to_dict()` | `dict` with `table_name` and one key per list that is not `None`. |
| `from_dict(data)` (class method) | A `TableTuning` built from such a dict. |

{#table-tuning-raises}

#### Raises

`ValueError` for an empty or badly formed `table_name`, for a list item that is not a `TuningColumn` (`sorting column at index 0 must be a TuningColumn instance`), and, from `from_dict`, for a dict without `table_name`.

{#table-tuning-example}

#### Example

```python
from benchbox.core.tuning.interface import TableTuning, TuningColumn, TuningType

lineitem = TableTuning(
    table_name="lineitem",
    partitioning=[TuningColumn("l_shipdate", "DATE", 1)],
    sorting=[TuningColumn("l_orderkey", "BIGINT", 1), TuningColumn("l_linenumber", "INTEGER", 2)],
)
print(lineitem.validate(), lineitem.has_any_tuning(), sorted(lineitem.get_all_columns()))
print([c.name for c in lineitem.get_columns_by_type(TuningType.SORTING)], lineitem.get_columns_by_type(TuningType.CLUSTERING))
print(TableTuning.from_dict(lineitem.to_dict()) == lineitem)

conflicting = TableTuning("t", partitioning=[TuningColumn("a", "INT", 1)], clustering=[TuningColumn("a", "INT", 1)])
print(conflicting.validate())
print(TableTuning("empty").validate())
try:
    TableTuning("bad name")
except ValueError as exc:
    print(exc)
```

Output on 0.4.1:

```text
[] True ['l_linenumber', 'l_orderkey', 'l_shipdate']
['l_orderkey', 'l_linenumber'] []
True
["Column 'a' is used in multiple tuning types: ['partitioning', 'clustering']"]
['Table tuning must specify at least one tuning configuration']
Invalid table name format: 'bad name'. Table names must start with a letter or underscore and contain only letters, numbers, and underscores.
```

### `benchbox.core.tuning.interface.UnifiedTuningConfiguration`

<span id="benchbox.core.tuning.interface.UnifiedTuningConfiguration"></span>

One object that holds the constraint settings, platform-specific optimizations and per-table tunings for a run, and checks them against a platform. It is a dataclass.

**Import:** `from benchbox.core.tuning.interface import UnifiedTuningConfiguration` · **Extras:** none

{#unified-tuning-parameters}

#### Parameters

All parameters are optional and default to a new object of their type.

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `primary_keys` | `PrimaryKeyConfiguration` | enabled | Primary-key settings: `enabled`, `enforce_uniqueness`, `nullable`. |
| `foreign_keys` | `ForeignKeyConfiguration` | enabled | Foreign-key settings: `enabled`, `enforce_referential_integrity`, `on_delete_action`, `on_update_action`. |
| `unique_constraints` | `UniqueConstraintConfiguration` | enabled | Unique-constraint settings: `enabled`, `ignore_nulls`. |
| `check_constraints` | `CheckConstraintConfiguration` | enabled | Check-constraint settings: `enabled`, `enforce_on_insert`, `enforce_on_update`. |
| `platform_optimizations` | `PlatformOptimizationConfiguration` | all off | Z-ordering, liquid clustering, auto-optimize, auto-compact, bloom filters and materialized views, each with an `..._enabled` flag. |
| `table_tunings` | `dict[str, TableTuning]` | `{}` | Per-table tunings, keyed by table name. |

{#unified-tuning-returns}

#### Returns

A `UnifiedTuningConfiguration`. A new one has the four constraint types enabled and nothing else.

| Method | Returns |
| --- | --- |
| `enable_all_constraints()`, `disable_all_constraints()` | `None`. Switch all four constraint types. |
| `enable_primary_keys()`, `disable_primary_keys()`, `enable_foreign_keys()`, `disable_foreign_keys()` | `None`. Switch one constraint type. |
| `enable_platform_optimization(optimization_type: TuningType, **kwargs)` | `None`. Turns on `Z_ORDERING`, `LIQUID_CLUSTERING`, `AUTO_OPTIMIZE`, `AUTO_COMPACT`, `BLOOM_FILTERS` or `MATERIALIZED_VIEWS`. The keyword `columns=[...]` sets the columns for Z-ordering, liquid clustering and bloom filters. Any other `TuningType` does nothing. |
| `disable_platform_optimization(optimization_type: TuningType)` | `None`. Turns the optimization off; for Z-ordering it also clears the columns. |
| `get_enabled_tuning_types()` | `set[TuningType]`: the enabled constraints and optimizations, plus every layout type that some table in `table_tunings` uses. |
| `validate_for_platform(platform: str)` | `list[str]` of errors for tuning types the platform does not support. |
| `validate_for_platform_detailed(platform: str)` | `(errors, warnings)`, both `list[str]`. Constraint types that a platform does not support, and every unsupported type on a platform that BenchBox has no compatibility data for, are warnings; other unsupported types are errors. Platform-specific layout rules can add further errors. |
| `to_dict()` | `dict` with the six settings above, ready for JSON. |
| `get_configuration_hash()` | `str`: a 64-character SHA-256 hex digest of the `to_dict()` content. Equal configurations give equal hashes. |
| `from_dict(data)` (class method) | A configuration built from such a dict; missing keys keep their defaults. An unknown key, at the top level (only `_metadata` is allowed besides the six settings), in a constraint section, in `platform_optimizations`, in a table tuning or in a column entry, raises `ValueError` that names the key, suggests the nearest valid key and lists the valid keys. |

{#unified-tuning-raises}

#### Raises

Nothing it raises itself, apart from what building `TableTuning` objects in `from_dict` raises. Validation problems are returned, not raised.

{#unified-tuning-example}

#### Example

```python
from benchbox.core.tuning.interface import TableTuning, TuningColumn, TuningType, UnifiedTuningConfiguration

config = UnifiedTuningConfiguration()
print(sorted(t.value for t in config.get_enabled_tuning_types()))

config.disable_foreign_keys()
config.enable_platform_optimization(TuningType.Z_ORDERING, columns=["order_date", "customer_key"])
print(sorted(t.value for t in config.get_enabled_tuning_types()))
print(config.validate_for_platform("duckdb"))
print(config.validate_for_platform("databricks"))

config.table_tunings["lineitem"] = TableTuning("lineitem", sorting=[TuningColumn("l_orderkey", "BIGINT", 1)])
print(config.validate_for_platform_detailed("databricks"))
errors, warnings = config.validate_for_platform_detailed("starrocks")
print(errors, len(warnings))

restored = UnifiedTuningConfiguration.from_dict(config.to_dict())
print(restored.to_dict() == config.to_dict(), len(config.get_configuration_hash()))
```

Output on 0.4.1:

```text
['check_constraints', 'foreign_keys', 'primary_keys', 'unique_constraints']
['check_constraints', 'primary_keys', 'unique_constraints', 'z_ordering']
["Tuning type 'z_ordering' is not supported by platform 'duckdb'"]
[]
(["Tuning type 'sorting' is not supported by platform 'databricks'"], [])
[] 5
True 64
```

## Usage Examples

### Basic Tuning Configuration

```python
from benchbox.core.tuning.interface import (
    UnifiedTuningConfiguration,
    TuningType
)
from benchbox.tpch import TPCH
from benchbox.platforms.duckdb import DuckDBAdapter

benchmark = TPCH(scale_factor=1.0)

config = UnifiedTuningConfiguration()

config.disable_foreign_keys()

schema_sql = benchmark.get_create_tables_sql(
    dialect="duckdb",
    tuning_config=config
)

adapter = DuckDBAdapter()
conn = adapter.create_connection()
conn.execute(schema_sql)
```

### Table-Specific Tuning

```python
from benchbox.core.tuning.interface import TableTuning, TuningColumn

lineitem_tuning = TableTuning(
    table_name="lineitem",
    partitioning=[
        TuningColumn("l_shipdate", "DATE", 1)
    ],
    sorting=[
        TuningColumn("l_orderkey", "BIGINT", 1),
        TuningColumn("l_linenumber", "INTEGER", 2)
    ]
)

orders_tuning = TableTuning(
    table_name="orders",
    partitioning=[
        TuningColumn("o_orderdate", "DATE", 1)
    ],
    sorting=[
        TuningColumn("o_orderkey", "BIGINT", 1)
    ]
)

config = UnifiedTuningConfiguration()
config.table_tunings["lineitem"] = lineitem_tuning
config.table_tunings["orders"] = orders_tuning

errors = lineitem_tuning.validate()
if errors:
    print(f"Lineitem tuning errors: {errors}")
```

### Databricks Delta Lake Optimization

```python
from benchbox.core.tuning.interface import UnifiedTuningConfiguration, TuningType
from benchbox.tpch import TPCH
from benchbox.platforms.databricks import DatabricksAdapter

benchmark = TPCH(scale_factor=10.0)

config = UnifiedTuningConfiguration()

config.enable_platform_optimization(
    TuningType.Z_ORDERING,
    columns=["l_shipdate", "l_orderkey"]
)

config.enable_platform_optimization(TuningType.AUTO_OPTIMIZE)
config.enable_platform_optimization(TuningType.AUTO_COMPACT)

config.enable_platform_optimization(
    TuningType.BLOOM_FILTERS,
    columns=["l_orderkey", "l_partkey"]
)

errors = config.validate_for_platform("databricks")
if errors:
    print(f"Configuration errors: {errors}")
else:
    print("Configuration valid for Databricks")

adapter = DatabricksAdapter(...)
schema_sql = benchmark.get_create_tables_sql(
    dialect="databricks",
    tuning_config=config
)
```

### Snowflake Clustering Configuration

```python
from benchbox.core.tuning.interface import TableTuning, TuningColumn
from benchbox.tpch import TPCH
from benchbox.platforms.snowflake import SnowflakeAdapter

benchmark = TPCH(scale_factor=10.0)

lineitem_tuning = TableTuning(
    table_name="lineitem",
    clustering=[
        TuningColumn("l_shipdate", "DATE", 1),
        TuningColumn("l_orderkey", "BIGINT", 2)
    ]
)

orders_tuning = TableTuning(
    table_name="orders",
    clustering=[
        TuningColumn("o_orderdate", "DATE", 1),
        TuningColumn("o_custkey", "BIGINT", 2)
    ]
)

config = UnifiedTuningConfiguration()
config.table_tunings["lineitem"] = lineitem_tuning
config.table_tunings["orders"] = orders_tuning

errors = config.validate_for_platform("snowflake")
if not errors:
    adapter = SnowflakeAdapter(...)
    schema_sql = benchmark.get_create_tables_sql(
        dialect="snowflake",
        tuning_config=config
    )
```

### Redshift Distribution and Sort Keys

```python
from benchbox.core.tuning.interface import TableTuning, TuningColumn
from benchbox.tpch import TPCH

lineitem_tuning = TableTuning(
    table_name="lineitem",
    distribution=[
        TuningColumn("l_orderkey", "BIGINT", 1)
    ],
    sorting=[
        TuningColumn("l_shipdate", "DATE", 1),
        TuningColumn("l_orderkey", "BIGINT", 2)
    ]
)

orders_tuning = TableTuning(
    table_name="orders",
    distribution=[
        TuningColumn("o_orderkey", "BIGINT", 1)
    ],
    sorting=[
        TuningColumn("o_orderdate", "DATE", 1)
    ]
)

config = UnifiedTuningConfiguration()
config.table_tunings["lineitem"] = lineitem_tuning
config.table_tunings["orders"] = orders_tuning

errors = config.validate_for_platform("redshift")
if not errors:
    benchmark = TPCH(scale_factor=10.0)
    schema_sql = benchmark.get_create_tables_sql(
        dialect="redshift",
        tuning_config=config
    )
```

### Constraint Management

```python
from benchbox.core.tuning.interface import (
    UnifiedTuningConfiguration,
    PrimaryKeyConfiguration,
    ForeignKeyConfiguration
)

config = UnifiedTuningConfiguration()

config.primary_keys = PrimaryKeyConfiguration(
    enabled=True,
    enforce_uniqueness=True,
    nullable=False
)

config.foreign_keys = ForeignKeyConfiguration(
    enabled=True,
    enforce_referential_integrity=True,
    on_delete_action="CASCADE",
    on_update_action="CASCADE"
)

config.disable_all_constraints()

print(f"Primary keys enabled: {config.primary_keys.enabled}")
print(f"Foreign keys enabled: {config.foreign_keys.enabled}")
```

`disable_all_constraints()` is the alternative to configuring each constraint when loading in bulk.

### Configuration Serialization

```python
from benchbox.core.tuning.interface import UnifiedTuningConfiguration, TuningType
import json

config = UnifiedTuningConfiguration()
config.disable_foreign_keys()
config.enable_platform_optimization(
    TuningType.Z_ORDERING,
    columns=["order_date", "customer_key"]
)

config_dict = config.to_dict()
config_json = json.dumps(config_dict, indent=2)

with open("tuning_config.json", "w") as f:
    f.write(config_json)

with open("tuning_config.json", "r") as f:
    loaded_dict = json.load(f)

restored_config = UnifiedTuningConfiguration.from_dict(loaded_dict)

assert restored_config.foreign_keys.enabled == config.foreign_keys.enabled
assert restored_config.platform_optimizations.z_ordering_enabled == config.platform_optimizations.z_ordering_enabled
```

### Platform Compatibility Validation

```python
from benchbox.core.tuning.interface import UnifiedTuningConfiguration, TuningType

config = UnifiedTuningConfiguration()
config.enable_platform_optimization(TuningType.Z_ORDERING, columns=["date_col"])
config.enable_platform_optimization(TuningType.AUTO_OPTIMIZE)
config.enable_platform_optimization(TuningType.BLOOM_FILTERS, columns=["id_col"])

platforms = ["duckdb", "databricks", "snowflake", "bigquery", "redshift"]

print("Platform Compatibility:")
print("=" * 60)

for platform in platforms:
    errors = config.validate_for_platform(platform)

    if errors:
        print(f"\n{platform.upper()}:")
        print("  Status: ✗ NOT COMPATIBLE")
        print("  Errors:")
        for error in errors:
            print(f"    - {error}")
    else:
        print(f"\n{platform.upper()}:")
        print("  Status: ✓ COMPATIBLE")

        enabled = config.get_enabled_tuning_types()
        print(f"  Supported features: {', '.join(t.value for t in enabled)}")
```

## Best Practices

1. **Disable foreign keys for bulk loading**: Improves load performance significantly. Load the data after disabling them and re-enable them afterwards if needed

   ```python
   config = UnifiedTuningConfiguration()
   config.disable_foreign_keys()
   ```

2. **Validate before applying**: Always validate configuration for target platform

   ```python
   errors = config.validate_for_platform("databricks")
   if errors:
       print(f"Fix these issues before applying: {errors}")
       return
   ```

3. **Use platform-specific optimizations**: Take advantage of platform strengths, such as Z-ordering on Databricks for selective queries, clustering on Snowflake for large tables and distribution keys on Redshift for joins

   ```python
   config.enable_platform_optimization(
       TuningType.Z_ORDERING,
       columns=["date", "customer_id"]
   )

   ```

4. **Partition large tables by date**: Improves query performance and maintenance

   ```python
   tuning = TableTuning(
       table_name="fact_sales",
       partitioning=[TuningColumn("sale_date", "DATE", 1)]
   )
   ```

5. **Sort by frequently filtered columns**: Enables zone maps and skip scanning

   ```python
   tuning = TableTuning(
       table_name="lineitem",
       sorting=[
           TuningColumn("l_shipdate", "DATE", 1),
           TuningColumn("l_orderkey", "BIGINT", 2)
       ]
   )
   ```

## See Also

- {doc}`base` - Base benchmark interface
- {doc}`platforms/databricks` - Databricks platform adapter (Z-ordering)
- {doc}`platforms/snowflake` - Snowflake platform adapter (clustering)
- {doc}`platforms/redshift` - Redshift platform adapter (distribution, sort keys)
- {doc}`/usage/configuration` - Configuration guide
- {doc}`/advanced/performance` - Performance optimization guide

### External Resources

- [Databricks Delta Lake Optimization](https://docs.databricks.com/aws/en/optimizations)
- [Snowflake Clustering](https://docs.snowflake.com/en/user-guide/tables-clustering-keys)
- [Redshift Distribution Styles](https://docs.aws.amazon.com/redshift/latest/dg/c_choosing_dist_sort.html)
- [BigQuery Partitioning](https://docs.cloud.google.com/bigquery/docs/partitioned-tables)
