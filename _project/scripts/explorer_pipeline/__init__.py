from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from _project.scripts.explorer_pipeline.duckdb_builder import DuckDBSnapshotBuilder
    from _project.scripts.explorer_pipeline.models import (
        DetailResult,
        ManifestEntry,
        QueryTiming,
    )
    from _project.scripts.explorer_pipeline.pipeline import BuildStats, ExplorerPipeline
    from _project.scripts.explorer_pipeline.transformer import BundleTransformer

_EXPORTS = {
    "BuildStats": "pipeline",
    "BundleTransformer": "transformer",
    "DetailResult": "models",
    "DuckDBSnapshotBuilder": "duckdb_builder",
    "ExplorerPipeline": "pipeline",
    "ManifestEntry": "models",
    "QueryTiming": "models",
}

__all__ = [
    "BuildStats",
    "BundleTransformer",
    "DetailResult",
    "DuckDBSnapshotBuilder",
    "ExplorerPipeline",
    "ManifestEntry",
    "QueryTiming",
]


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted([*globals(), *_EXPORTS])
