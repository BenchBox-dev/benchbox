from __future__ import annotations

import json

import pytest

from benchbox.core.execution_engine import (
    DEFAULT_EXECUTION_ENGINE,
    ExecutionEngineHook,
    ExecutionEngineReceipt,
    UnsupportedExecutionEngineError,
    resolve_requested,
    supported_execution_engines,
)
from benchbox.core.platform_manifest import _load_manifest
from benchbox.core.platform_registry import ExecutionEngineCapability, PlatformCapability, PlatformRegistry
from benchbox.platforms.base import PlatformAdapter
from benchbox.platforms.dataframe.benchmark_mixin import BenchmarkExecutionMixin

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _engine(engine_class: str, *, selectable: bool = True) -> dict[str, object]:
    return {
        "class": engine_class,
        "display_name": engine_class.title(),
        "description": f"{engine_class} engine",
        "dependencies": [],
        "selectable": selectable,
    }


def _entry(key: str, engines: object) -> dict[str, object]:
    return {
        "key": key,
        "aliases": [],
        "display_name": key,
        "description": key,
        "category": "analytical",
        "libraries": [],
        "requirements": [],
        "installation_command": "",
        "adoption": "emerging",
        "supports": [],
        "support_status": "stable",
        "capabilities": {
            "supports_sql": True,
            "sql_dialect": "duckdb",
            "supports_dataframe": False,
            "default_mode": "sql",
            "execution_engines": engines,
        },
    }


def _load(*entries: dict[str, object]) -> None:
    _load_manifest(json.dumps(list(entries)))


def test_manifest_accepts_declared_engines() -> None:
    _load(_entry("alpha", {"photon": _engine("native-vectorized", selectable=False), "standard": _engine("standard")}))


def test_manifest_rejects_unknown_class() -> None:
    with pytest.raises(ValueError, match="unknown class 'turbo'"):
        _load(_entry("alpha", {"fast": _engine("turbo")}))


def test_manifest_rejects_declared_default() -> None:
    with pytest.raises(ValueError, match="cannot declare execution engine 'default'"):
        _load(_entry("alpha", {DEFAULT_EXECUTION_ENGINE: _engine("standard")}))


def test_manifest_rejects_duplicate_engine_name() -> None:
    engine = json.dumps(_engine("standard"))
    payload = json.dumps([_entry("alpha", {"__engines__": None})]).replace(
        '{"__engines__": null}', f'{{"standard": {engine}, "standard": {engine}}}'
    )
    with pytest.raises(ValueError, match="Duplicate platform manifest object keys: standard"):
        _load_manifest(payload)


def test_manifest_rejects_same_name_with_different_classes() -> None:
    with pytest.raises(ValueError, match="'duckdb' is 'delegated' on 'alpha' but 'standard' on 'beta'"):
        _load(_entry("alpha", {"duckdb": _engine("delegated")}), _entry("beta", {"duckdb": _engine("standard")}))


@pytest.mark.parametrize(
    ("engines", "message"),
    [
        ([], "execution_engines must be an object"),
        ({"fast": {"class": "standard"}}, "must have exactly"),
        ({"fast": {**_engine("standard"), "selectable": "yes"}}, "selectable must be a boolean"),
        ({"fast": {**_engine("standard"), "dependencies": "polars"}}, "dependencies must be a list"),
    ],
)
def test_manifest_rejects_malformed_engines(engines: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _load(_entry("alpha", engines))


def test_polars_declares_adaptive_in_memory_and_streaming() -> None:
    engines = supported_execution_engines("polars-df")
    assert {name: engine.engine_class for name, engine in engines.items()} == {
        "auto": "adaptive",
        "in-memory": "in-memory",
        "streaming": "streaming",
    }
    assert all(engine.selectable for engine in engines.values())
    assert PlatformRegistry.get_available_execution_engines("polars") == ["auto", "in-memory", "streaming"]
    assert PlatformRegistry.supports_execution_engine("polars", "streaming")
    assert not PlatformRegistry.supports_execution_engine("polars", "gpu")


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("pg-duckdb", {"duckdb": "delegated", "postgres": "standard"}),
        ("dask-df", {"threaded": "standard", "distributed": "distributed"}),
        ("databricks", {"photon": "native-vectorized", "standard": "standard"}),
        ("velox", {"velox": "native-vectorized", "standard": "standard"}),
    ],
)
def test_existing_platform_engine_declarations(platform: str, expected: dict[str, str]) -> None:
    engines = supported_execution_engines(platform)

    assert {name: engine.engine_class for name, engine in engines.items()} == expected
    assert {name: engine.selectable for name, engine in engines.items()} == {
        name: platform in {"pg-duckdb", "dask-df"} for name in expected
    }


@pytest.mark.parametrize(
    ("platform", "engine"),
    [
        ("databricks", "photon"),
        ("databricks", "standard"),
        ("velox", "velox"),
        ("velox", "standard"),
    ],
)
def test_observed_platform_engines_reject_selection(platform: str, engine: str) -> None:
    with pytest.raises(UnsupportedExecutionEngineError):
        resolve_requested(platform, engine)


def test_platform_without_engines_declares_none() -> None:
    assert dict(supported_execution_engines("duckdb")) == {}
    assert PlatformRegistry.get_available_execution_engines("duckdb") == []


@pytest.mark.parametrize("platform", ["duckdb", "polars-df", "polars", "snowflake", "unknown-platform"])
def test_default_is_accepted_everywhere(platform: str) -> None:
    assert resolve_requested(platform, DEFAULT_EXECUTION_ENGINE) == DEFAULT_EXECUTION_ENGINE


@pytest.mark.parametrize("value", ["auto", "in-memory", "streaming"])
def test_polars_accepts_declared_names(value: str) -> None:
    assert resolve_requested("polars-df", value) == value


def test_platform_without_engines_rejects_names() -> None:
    with pytest.raises(UnsupportedExecutionEngineError, match="Allowed: default$"):
        resolve_requested("duckdb", "streaming")


def test_unknown_value_lists_allowed_names() -> None:
    with pytest.raises(UnsupportedExecutionEngineError, match="'gpu'. Allowed: default, auto, in-memory, streaming$"):
        resolve_requested("polars-df", "gpu")


def test_observed_only_engine_is_not_selectable(monkeypatch: pytest.MonkeyPatch) -> None:
    capability = PlatformCapability(
        supports_sql=True,
        execution_engines={
            "photon": ExecutionEngineCapability(engine_class="native-vectorized", selectable=False),
            "standard": ExecutionEngineCapability(engine_class="standard", selectable=False),
        },
    )
    monkeypatch.setattr(PlatformRegistry, "get_platform_capabilities", classmethod(lambda cls, name: capability))
    with pytest.raises(UnsupportedExecutionEngineError, match="'photon'. Allowed: default$"):
        resolve_requested("databricks", "photon")


def test_adapter_bases_share_the_hook() -> None:
    assert issubclass(PlatformAdapter, ExecutionEngineHook)
    assert issubclass(BenchmarkExecutionMixin, ExecutionEngineHook)


def test_adapter_without_engines_returns_platform_default_receipt() -> None:
    from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter
    from benchbox.platforms.duckdb import DuckDBAdapter
    from benchbox.platforms.polars_platform import PolarsAdapter

    for adapter in (DuckDBAdapter(), PolarsAdapter(), PandasDataFrameAdapter()):
        assert adapter.resolve_execution_engine(DEFAULT_EXECUTION_ENGINE) == ExecutionEngineReceipt(
            requested="default",
            applied=None,
            applied_class=None,
            resolution="platform_default",
            observed="not_captured",
            observed_source="none",
        )

    with pytest.raises(UnsupportedExecutionEngineError, match="'DuckDB' does not support execution engine 'streaming'"):
        DuckDBAdapter().resolve_execution_engine("streaming")
