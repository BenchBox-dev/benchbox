<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# MCP API Formalization Future State

Related TODO: `formalize-mcp-internal-apis`

## Future State

The 3 internal module references that MCP imports directly are promoted to
public exports. MCP code references stable, documented API surfaces instead of
reaching into internal module paths. The existing `benchbox[mcp]` optional
extra remains the install path: no distribution split is needed.

## Why This Is Valuable

- MCP imports become resilient to internal refactoring of core module layout.
- The public API surface is explicit and testable.
- Future distribution split (if ever needed post-v1.0) has a clear foundation.

## How The End State Is Used

MCP install path is unchanged:

```bash
uv add benchbox[mcp]
benchbox-mcp serve
```

MCP code imports from the public API surface:

```python
from benchbox import (
    TPCH_DATAFRAME_QUERIES,
    TPCDS_DATAFRAME_QUERIES,
    DATAFRAME_PLATFORMS,
)
```

Instead of reaching into internal modules (this is the fragile, before-refactor form):

```python
from benchbox.core.tpch.dataframe_queries import TPCH_DATAFRAME_QUERIES
from benchbox.core.tpcds.dataframe_queries import TPCDS_DATAFRAME_QUERIES
from benchbox.platforms.dataframe import DATAFRAME_PLATFORMS
```

## BenchBox After The Refactor

- 3 symbols are re-exported from a public API surface.
- MCP imports use the public path.
- A test asserts the exports exist.
- Everything else is unchanged.

## Non-Goals

- Splitting MCP into a separate distribution (deferred to post-v1.0)
- Changing MCP schemas, prompts, or tool contracts
- Formalizing all 23+ API touchpoints (only the 3 internal refs need attention)
