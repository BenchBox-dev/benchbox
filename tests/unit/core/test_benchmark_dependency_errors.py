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


STUB_BENCHMARK_ID = "needsextra"
STUB_MISSING_MODULE = "stubpkg"
STUB_MODULES = frozenset(
    {"benchbox.needs_extra_stub", f"benchbox.core.{STUB_BENCHMARK_ID}", f"benchbox.core.{STUB_BENCHMARK_ID}.benchmark"}
)


class _MissingStubDependency:
    def find_spec(self, name, path=None, target=None):
        if name in STUB_MODULES:
            raise ModuleNotFoundError(f"No module named '{STUB_MISSING_MODULE}'", name=STUB_MISSING_MODULE)
        return None


@pytest.fixture
def extra_gated_benchmark(monkeypatch):
    monkeypatch.setitem(
        benchbox._BENCHMARK_REGISTRY,
        "NeedsExtra",
        benchbox._BenchmarkSpec("needs_extra_stub", "NeedsExtra", ("stubextra",)),
    )
    original_class_name = benchmark_registry.get_benchmark_class_name

    def class_name(benchmark_id):
        if benchmark_id.lower() == STUB_BENCHMARK_ID:
            return "NeedsExtra"
        return original_class_name(benchmark_id)

    monkeypatch.setattr(benchmark_registry, "get_benchmark_class_name", class_name)
    monkeypatch.setattr(sys, "meta_path", [_MissingStubDependency(), *sys.meta_path])
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
    assert STUB_MISSING_MODULE in text
    assert "benchbox[stubextra]" in text or "--extra stubextra" in text


def test_registry_lookup_reports_missing_module_and_extra(extra_gated_benchmark):
    with pytest.raises(BenchmarkDependencyError) as excinfo:
        benchmark_registry.get_public_benchmark_class(STUB_BENCHMARK_ID)

    error = excinfo.value
    assert error.module == STUB_MISSING_MODULE
    assert error.extras == ("stubextra",)
    _assert_names_module_and_install(str(error))
    assert "stubextra" in error.remedy


def test_availability_check_treats_missing_dependency_as_unavailable(extra_gated_benchmark):
    assert benchmark_registry.is_benchmark_available(STUB_BENCHMARK_ID) is False


def test_python_api_error_carries_the_same_guidance(extra_gated_benchmark):
    with pytest.raises(ImportError) as excinfo:
        _ = benchbox.NeedsExtra

    _assert_names_module_and_install(str(excinfo.value))
    assert "not supported yet" not in str(excinfo.value)


def test_cli_loader_reports_missing_module_instead_of_unsupported(extra_gated_benchmark):
    with pytest.raises(BenchmarkDependencyError) as excinfo:
        get_core_benchmark_class(STUB_BENCHMARK_ID)

    _assert_names_module_and_install(str(excinfo.value))
    assert "not supported yet" not in str(excinfo.value)


def test_cli_loader_still_rejects_unknown_benchmarks():
    with pytest.raises(ValueError, match="not supported yet"):
        get_core_benchmark_class("definitely_not_a_benchmark")


def test_mcp_job_worker_returns_dependency_missing_with_install_command(extra_gated_benchmark, tmp_path, monkeypatch):
    from benchbox.mcp import jobs
    from benchbox.mcp.jobs import DurableJobWorker, JobRecord

    monkeypatch.setattr(jobs, "get_all_benchmarks", lambda: {STUB_BENCHMARK_ID: {}})
    job = JobRecord(
        execution_id="mcp_dep",
        principal_id="principal",
        state="running",
        request={"platform": "duckdb", "benchmark": STUB_BENCHMARK_ID, "scale_factor": 0.01},
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

    response = DurableJobWorker._execute_benchmark(job, tmp_path)

    assert response["status"] == "failed"
    assert response["error_code"] == "DEPENDENCY_MISSING"
    assert response["details"]["module"] == STUB_MISSING_MODULE
    assert "benchbox[stubextra]" in response["suggestion"] or "--extra stubextra" in response["suggestion"]


def test_mcp_query_details_report_missing_dependency(extra_gated_benchmark):
    from benchbox.mcp.tools import benchmark as benchmark_tools

    response: dict = {}
    benchmark_tools._populate_sql_query_details(response, STUB_BENCHMARK_ID, "1", None)

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
    error = create_import_error("X", ["stubextra"], ImportError("DLL load failed"))

    assert "benchbox[" not in str(error)
    assert "--extra" not in str(error)


def test_every_declared_benchmark_extra_exists_in_pyproject():
    extras = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]["optional-dependencies"]

    for class_name, spec in benchbox._BENCHMARK_REGISTRY.items():
        for extra in spec.optional_dependencies:
            assert extra in extras, f"{class_name} declares nonexistent extra {extra!r}"
