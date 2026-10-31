import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

import benchbox
from benchbox.core import benchmark_registry
from benchbox.core.benchmark_loader import get_core_benchmark_class
from benchbox.mcp.errors import make_benchmark_load_error
from benchbox.utils.dependencies import (
    INSTALLATION_GUIDE_URL,
    BenchmarkDependencyError,
    BenchmarkLoadError,
    find_missing_module,
)
from benchbox.utils.version import create_import_error

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


class _PandasBlocker:
    def find_spec(self, name, path=None, target=None):
        if name == "pandas" or name.startswith("pandas."):
            raise ModuleNotFoundError("No module named 'pandas'", name="pandas")
        return None


@pytest.fixture
def pandas_blocked(monkeypatch):
    for name in list(sys.modules):
        if name == "pandas" or name.startswith(("pandas.", "benchbox.tpcdi", "benchbox.core.tpcdi")):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", [_PandasBlocker(), *sys.meta_path])
    benchbox._clear_lazy_cache()
    yield
    benchbox._clear_lazy_cache()


@pytest.fixture
def broken_benchmark_module(monkeypatch):
    monkeypatch.setattr(benchbox, "_import_module", lambda module_path: SimpleNamespace())
    monkeypatch.setattr(benchmark_registry, "get_core_benchmark_class_name", lambda benchmark_id: None)
    benchbox._clear_lazy_cache()
    yield
    benchbox._clear_lazy_cache()


def _assert_names_module_and_install(text: str) -> None:
    assert "pandas" in text
    assert "benchbox[tpcdi]" in text or "--extra tpcdi" in text


def test_registry_lookup_reports_missing_module_and_extra(pandas_blocked):
    with pytest.raises(BenchmarkDependencyError) as excinfo:
        benchmark_registry.get_public_benchmark_class("tpcdi")

    error = excinfo.value
    assert error.module == "pandas"
    assert error.extras == ("tpcdi",)
    _assert_names_module_and_install(str(error))
    assert "tpcdi" in error.remedy


def test_availability_check_treats_missing_dependency_as_unavailable(pandas_blocked):
    assert benchmark_registry.is_benchmark_available("tpcdi") is False


def test_python_api_error_carries_the_same_guidance(pandas_blocked):
    with pytest.raises(ImportError) as excinfo:
        _ = benchbox.TPCDI

    _assert_names_module_and_install(str(excinfo.value))
    assert "not supported yet" not in str(excinfo.value)


def test_cli_loader_reports_missing_module_instead_of_unsupported(pandas_blocked):
    with pytest.raises(BenchmarkDependencyError) as excinfo:
        get_core_benchmark_class("tpcdi")

    _assert_names_module_and_install(str(excinfo.value))
    assert "not supported yet" not in str(excinfo.value)


def test_cli_loader_still_rejects_unknown_benchmarks():
    with pytest.raises(ValueError, match="not supported yet"):
        get_core_benchmark_class("definitely_not_a_benchmark")


def test_mcp_run_tool_returns_dependency_missing_with_install_command(pandas_blocked, tmp_path):
    from benchbox.mcp.tools import benchmark as benchmark_tools

    response = benchmark_tools._run_benchmark_impl(
        "duckdb", "tpcdi", 0.01, None, "load,power", "sql", results_dir=tmp_path, anonymize=False
    )

    assert response["status"] == "failed"
    assert response["error_code"] == "DEPENDENCY_MISSING"
    assert response["details"]["module"] == "pandas"
    assert response["details"]["extras"] == ["tpcdi"]
    assert "benchbox[tpcdi]" in response["suggestion"] or "--extra tpcdi" in response["suggestion"]


def test_mcp_job_worker_returns_dependency_missing_with_install_command(pandas_blocked, tmp_path):
    from benchbox.mcp.jobs import DurableJobWorker, JobRecord

    job = JobRecord(
        execution_id="mcp_dep",
        principal_id="principal",
        state="running",
        request={"platform": "duckdb", "benchmark": "tpcdi", "scale_factor": 0.01},
        idempotency_key=None,
        attempts=1,
        lease_owner=None,
        lease_expires_at=None,
        lease_version=1,
        lease_generation=1,
        cancel_requested=False,
        artifact_path=None,
        error_code=None,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        completed_at=None,
    )

    response = DurableJobWorker._execute_benchmark_request(job, tmp_path)

    assert response["status"] == "failed"
    assert response["error_code"] == "DEPENDENCY_MISSING"
    assert response["details"]["module"] == "pandas"
    assert "benchbox[tpcdi]" in response["suggestion"] or "--extra tpcdi" in response["suggestion"]


def test_mcp_query_details_report_missing_dependency(pandas_blocked):
    from benchbox.mcp.tools import benchmark as benchmark_tools

    response: dict = {}
    benchmark_tools._populate_sql_query_details(response, "tpcdi", "1", None)

    _assert_names_module_and_install(response["error"])


def test_missing_class_is_an_internal_error_not_a_missing_dependency(broken_benchmark_module):
    with pytest.raises(BenchmarkLoadError) as excinfo:
        benchmark_registry.get_public_benchmark_class("ssb")

    payload = make_benchmark_load_error("ssb", excinfo.value)
    assert payload["error_code"] == "INTERNAL_ERROR"
    assert "requires the Python package" not in payload["message"]


def test_module_without_extra_points_to_installation_guide():
    error = BenchmarkDependencyError("ssb", "somepkg")

    assert "somepkg" in str(error)
    assert INSTALLATION_GUIDE_URL in str(error)
    assert "benchbox[" not in str(error)


def test_find_missing_module_ignores_benchbox_internal_modules():
    internal = ModuleNotFoundError("No module named 'benchbox.core.nope'", name="benchbox.core.nope")
    external = ModuleNotFoundError("No module named 'duckdb'", name="duckdb")
    wrapper = ImportError("wrapped")
    wrapper.__cause__ = external

    assert find_missing_module(internal) is None
    assert find_missing_module(wrapper) == "duckdb"
    assert find_missing_module(ImportError("DLL load failed")) is None


def test_create_import_error_never_advises_extras_without_a_missing_module():
    error = create_import_error("X", ["tpcdi"], ImportError("DLL load failed"))

    assert "benchbox[" not in str(error)
    assert "--extra" not in str(error)


def test_every_declared_benchmark_extra_exists_in_pyproject():
    extras = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]["optional-dependencies"]

    for class_name, spec in benchbox._BENCHMARK_REGISTRY.items():
        for extra in spec.optional_dependencies:
            assert extra in extras, f"{class_name} declares nonexistent extra {extra!r}"
