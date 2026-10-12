<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Adding a New DataFrame Platform

```{tags} contributor, guide, dataframe-platform
```

This guide explains how to add support for a new DataFrame platform to BenchBox.

Before opening a PR, run through the [New Platform Acceptance Checklist](new-platform-acceptance-checklist.md). DataFrame platforms need registry metadata, a `support_status`, optional dependency isolation, native query coverage, docs, and parity validation separate from SQL platforms.

## Overview

BenchBox uses a **family-based architecture** that minimizes code duplication when adding new platforms. Most new platforms require:

1. Determine which family the platform belongs to
2. Implement a DataFrameContext subclass
3. Implement a platform adapter
4. Register the platform
5. Add tests

## Step 1: Determine the Family

DataFrame platforms fall into two families based on their API style:

### Expression Family

Use expression objects for column references and operations.

**Members:** Polars, PySpark, DataFusion

This is expression-style syntax:

```python
result = (
    df.filter(col('status') == lit('active'))
    .group_by('category')
    .agg(col('amount').sum().alias('total'))
)
```

**Key characteristics:**
- `col()` function for column references
- `lit()` function for literal values
- Method chaining with expression composition
- Often supports lazy evaluation

### Pandas Family

Use string-based column access and boolean indexing.

**Members:** Pandas, cuDF, Dask, Vaex

This is Pandas-style syntax:

```python
filtered = df[df['status'] == 'active']
result = filtered.groupby('category').agg({'amount': 'sum'})
```

**Key characteristics:**
- String-based column names: `df['column']`
- Boolean indexing for filtering
- Dictionary-based aggregation specifications
- Usually eager evaluation (except Dask)

## Step 2: Implement DataFrameContext

Create a context class that provides table access and family-specific helpers.

### Expression Family Context

Put this class in `benchbox/core/dataframe/context.py`.

```python
class MyPlatformDataFrameContext(DataFrameContext):

    def __init__(self):
        self._tables: dict[str, Any] = {}

    @property
    def family(self) -> str:
        return "expression"

    def get_table(self, name: str) -> Any:
        if name not in self._tables:
            raise KeyError(f"Table '{name}' not registered")
        return self._tables[name]

    def register_table(self, name: str, df: Any) -> None:
        self._tables[name] = df

    @property
    def col(self):
        from myplatform import col
        return col

    @property
    def lit(self):
        from myplatform import lit
        return lit
```

### Pandas Family Context

`get_table` returns a copy for safety. `col` and `lit` return `None` because the pandas family does not use them.

```python
class MyPandasLikeContext(DataFrameContext):

    def __init__(self):
        self._tables: dict[str, Any] = {}

    @property
    def family(self) -> str:
        return "pandas"

    def get_table(self, name: str) -> Any:
        if name not in self._tables:
            raise KeyError(f"Table '{name}' not registered")
        return self._tables[name].copy()

    def register_table(self, name: str, df: Any) -> None:
        self._tables[name] = df

    @property
    def col(self):
        return None

    @property
    def lit(self):
        return None
```

## Step 3: Implement Platform Adapter

Create an adapter that handles data loading and query execution.

For **expression family** platforms, inherit from `ExpressionFamilyAdapter`. Put the adapter in
`benchbox/platforms/dataframe/myplatform_df.py`. In `execute_query`, the adapter collects the result if it is lazy.

```python
from benchbox.platforms.dataframe.expression_family import ExpressionFamilyAdapter

class MyPlatformAdapter(ExpressionFamilyAdapter[MyDF, MyLazyDF, MyExpr]):

    platform_name = "myplatform-df"

    def __init__(self, working_dir: str, **options):
        super().__init__(working_dir)
        self.options = options

    def create_context(self) -> MyPlatformDataFrameContext:
        return MyPlatformDataFrameContext()

    def load_tables(self, ctx: DataFrameContext, data_dir: str) -> None:
        import myplatform as mp
        from pathlib import Path

        data_path = Path(data_dir)
        parquet_dir = data_path / "parquet"

        for table_file in parquet_dir.glob("*.parquet"):
            table_name = table_file.stem
            df = mp.read_parquet(str(table_file))
            ctx.register_table(table_name, df)

    def execute_query(self, ctx: DataFrameContext, query: DataFrameQuery) -> Any:
        impl = query.get_impl_for_family(self.family)
        if impl is None:
            raise ValueError(f"No {self.family} implementation for {query.query_id}")

        result = impl(ctx)

        if hasattr(result, 'collect'):
            result = result.collect()

        return result

    @staticmethod
    def is_available() -> bool:
        try:
            import myplatform
            return True
        except ImportError:
            return False

    @staticmethod
    def get_version() -> str | None:
        try:
            import myplatform
            return myplatform.__version__
        except ImportError:
            return None
```

## Step 4: Register the Platform

Add the platform to the DataFrame adapter registry and `PlatformRegistry` metadata. DataFrame-only platforms must set `supports_sql=False`, `supports_dataframe=True`, and exactly one `support_status`.
The registry lives in `benchbox/platforms/dataframe/__init__.py`. The `myplatform-df` entry is the new platform.

```python
from benchbox.platforms.dataframe.myplatform import MyPlatformAdapter

DATAFRAME_ADAPTERS = {
    "polars-df": PolarsDataFrameAdapter,
    "pandas-df": PandasDataFrameAdapter,
    "myplatform-df": MyPlatformAdapter,
}
```

Before implementation, maintainers can print the expected file plan with the
platform scaffold script (`--kind dataframe`).

Declare execution engines when the platform offers more than one way to execute a query, following the same
manifest `capabilities.execution_engines` block, closed class list, value-naming rule and reserved-word ban as
SQL platforms (see `docs/development/adding-new-platforms.md`). The worked example is `polars-df`, which
declares `auto` (adaptive), `in-memory` and `streaming` under the platform's own documented names. DataFrame
adapters inherit `resolve_execution_engine` from the adapter base and map the resolved engine into their
collect or compute call; unsupported values fail closed.

## Step 5: Add Tests

### Unit Tests

Put the unit tests in `tests/unit/platforms/test_myplatform_adapter.py`. `test_is_available` passes whether or not
`myplatform` is installed, because the result is `True` only when it is installed. The `family` assertion expects
`"expression"` for an expression family platform and `"pandas"` for a pandas family platform.

```python
import pytest
from benchbox.platforms.dataframe.myplatform import MyPlatformAdapter

class TestMyPlatformAdapter:

    def test_is_available(self):
        result = MyPlatformAdapter.is_available()
        assert isinstance(result, bool)

    @pytest.mark.skipif(
        not MyPlatformAdapter.is_available(),
        reason="myplatform not installed"
    )
    def test_create_context(self, tmp_path):
        adapter = MyPlatformAdapter(str(tmp_path))
        ctx = adapter.create_context()
        assert ctx.family == "expression"

    @pytest.mark.skipif(
        not MyPlatformAdapter.is_available(),
        reason="myplatform not installed"
    )
    def test_query_execution(self, tmp_path, sample_data):
        adapter = MyPlatformAdapter(str(tmp_path))
        ctx = adapter.create_context()
        adapter.load_tables(ctx, sample_data)

        from benchbox.core.tpch.dataframe_queries import get_query
        query = get_query("Q1")
        result = adapter.execute_query(ctx, query)

        assert len(result) > 0
```

### Integration Tests

Put the integration tests in `tests/integration/test_myplatform_tpch.py`.

```python
import pytest
from benchbox.platforms.dataframe.myplatform import MyPlatformAdapter

@pytest.mark.integration
@pytest.mark.skipif(
    not MyPlatformAdapter.is_available(),
    reason="myplatform not installed"
)
class TestMyPlatformTPCH:

    def test_all_tpch_queries(self, tpch_data_dir):
        from benchbox.core.tpch.dataframe_queries import TPCH_DATAFRAME_QUERIES

        adapter = MyPlatformAdapter(str(tpch_data_dir))
        ctx = adapter.create_context()
        adapter.load_tables(ctx, str(tpch_data_dir))

        for query in TPCH_DATAFRAME_QUERIES.get_all_queries():
            result = adapter.execute_query(ctx, query)
            assert result is not None
```

## Platform-Specific Considerations

### PySpark

- Requires Spark session management
- Use `spark.createDataFrame()` for table registration
- Handle distributed execution semantics

### cuDF (GPU)

- Requires CUDA-enabled GPU
- Memory limited to GPU VRAM
- Similar to Pandas family API

### Dask

- Supports larger-than-memory datasets
- Lazy evaluation like expression family
- Uses Pandas-style API

## Testing Checklist

Before submitting a PR:

- [ ] Unit tests pass: `uv run -- python -m pytest tests/unit/platforms/test_myplatform_adapter.py`
- [ ] Integration tests pass (if platform installed)
- [ ] Platform availability check works correctly
- [ ] Version detection works
- [ ] All TPC-H queries execute successfully
- [ ] `support_status` and registry capabilities match the platform family
- [ ] Documentation updated
- [ ] Example script created in `examples/dataframe/`

## Related Documentation

- [DataFrame Platforms Overview](../platforms/dataframe.md)
- [DataFrameContext API](../platforms/dataframe.md#api-reference)
- [TPC-H Query Implementations](../guides/tpc/tpc-h-official-guide.md)
- [New Platform Acceptance Checklist](new-platform-acceptance-checklist.md)
