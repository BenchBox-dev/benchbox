from __future__ import annotations

from types import SimpleNamespace

import pytest

import benchbox.platforms as platform_module

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def setup_function() -> None:
    platform_module._lazy_adapter_cache.clear()
    platform_module._lazy_adapter_diagnostics.clear()


def test_lazy_adapter_diagnostics_missing_dependency(monkeypatch):
    def fake_import_module(module_path: str, package: str):
        raise ModuleNotFoundError(
            "No module named 'snowflake.connector'",
            name="snowflake.connector",
        )

    monkeypatch.setattr(platform_module.importlib, "import_module", fake_import_module)

    assert platform_module._load_lazy_adapter("SnowflakeAdapter") is None

    diagnostics = platform_module.get_lazy_adapter_diagnostics()
    assert diagnostics["SnowflakeAdapter"]["status"] == "missing_optional_dependency"
    assert diagnostics["SnowflakeAdapter"]["error_type"] == "ModuleNotFoundError"


def test_lazy_adapter_diagnostics_missing_adapter_module_is_broken(monkeypatch):
    def fake_import_module(module_path: str, package: str):
        raise ModuleNotFoundError(
            "No module named 'benchbox.platforms.snowflake'",
            name="benchbox.platforms.snowflake",
        )

    monkeypatch.setattr(platform_module.importlib, "import_module", fake_import_module)

    assert platform_module._load_lazy_adapter("SnowflakeAdapter") is None

    diagnostics = platform_module.get_lazy_adapter_diagnostics()
    assert diagnostics["SnowflakeAdapter"]["status"] == "broken_adapter_import"
    assert diagnostics["SnowflakeAdapter"]["error_type"] == "ModuleNotFoundError"


def test_lazy_adapter_diagnostics_native_load_failure(monkeypatch):
    def fake_import_module(module_path: str, package: str):
        raise OSError("dlopen(libdatafusion.dylib): image not found")

    monkeypatch.setattr(platform_module.importlib, "import_module", fake_import_module)

    assert platform_module._load_lazy_adapter("DataFusionAdapter") is None

    diagnostics = platform_module.get_lazy_adapter_diagnostics()
    assert diagnostics["DataFusionAdapter"]["status"] == "native_library_load_failure"


def test_lazy_adapter_diagnostics_broken_adapter_module(monkeypatch):
    def fake_import_module(module_path: str, package: str):
        return SimpleNamespace()

    monkeypatch.setattr(platform_module.importlib, "import_module", fake_import_module)

    assert platform_module._load_lazy_adapter("SnowflakeAdapter") is None

    diagnostics = platform_module.get_lazy_adapter_diagnostics()
    assert diagnostics["SnowflakeAdapter"]["status"] == "broken_adapter_import"
    assert "SnowflakeAdapter" in diagnostics["SnowflakeAdapter"]["error_message"]


def test_lazy_adapter_success_clears_prior_diagnostic(monkeypatch):
    adapter_cls = type("SnowflakeAdapter", (), {})
    platform_module._lazy_adapter_diagnostics["SnowflakeAdapter"] = {
        "status": "missing_optional_dependency",
        "error_message": "stale",
    }

    def fake_import_module(module_path: str, package: str):
        return SimpleNamespace(SnowflakeAdapter=adapter_cls)

    monkeypatch.setattr(platform_module.importlib, "import_module", fake_import_module)

    assert platform_module._load_lazy_adapter("SnowflakeAdapter") is adapter_cls
    assert "SnowflakeAdapter" not in platform_module.get_lazy_adapter_diagnostics()


def test_platform_module_exposes_registry_optional_diagnostics(monkeypatch):
    called = {}

    def fake_diagnostics(platform_names):
        called["platform_names"] = platform_names
        return {"snowflake": {"status": "available"}}

    monkeypatch.setattr(platform_module.PlatformRegistry, "diagnose_optional_adapter_imports", fake_diagnostics)

    assert platform_module.diagnose_optional_adapter_imports(["snowflake"]) == {"snowflake": {"status": "available"}}
    assert called["platform_names"] == ["snowflake"]


def test_eager_optional_adapter_import_attribute_error_propagates(monkeypatch):
    def fake_import_module(module_path: str, package: str):
        raise AttributeError("adapter module bug")

    monkeypatch.setattr(platform_module.importlib, "import_module", fake_import_module)

    with pytest.raises(AttributeError, match="adapter module bug"):
        platform_module._load_optional_adapter(".duckdb", "DuckDBAdapter")


def test_eager_optional_adapter_missing_class_returns_none(monkeypatch):
    def fake_import_module(module_path: str, package: str):
        return SimpleNamespace()

    monkeypatch.setattr(platform_module.importlib, "import_module", fake_import_module)

    assert platform_module._load_optional_adapter(".duckdb", "DuckDBAdapter") is None


def test_eager_optional_adapter_native_load_failure_returns_none(monkeypatch):
    def fake_import_module(module_path: str, package: str):
        raise OSError("dlopen(libduckdb.dylib): image not found")

    monkeypatch.setattr(platform_module.importlib, "import_module", fake_import_module)

    assert platform_module._load_optional_adapter(".duckdb", "DuckDBAdapter") is None


