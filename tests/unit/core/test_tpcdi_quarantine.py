from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from benchbox import TPCDI
from benchbox.cli.app import cli
from benchbox.core.benchmark_registry import get_all_benchmarks
from benchbox.core.dryrun import DryRunExecutor
from benchbox.core.publishing.admission import publish_admission
from benchbox.core.results.builder import BenchmarkInfoInput, ResultBuilder
from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.loader import load_result_file
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.platform_info import PlatformInfoInput
from benchbox.core.results.schema import build_result_payload
from benchbox.core.results.tpcdi_audit import LEGACY_TPCDI_NOTE, TPCDI_AUDIT_BLOCK_KEY
from benchbox.core.run_service import (
    TPCDI_UNAVAILABLE_MESSAGE,
    BenchmarkUnavailableError,
    build_unfinished_result,
    refuse_unavailable_benchmark,
)
from benchbox.core.schemas import BenchmarkConfig
from benchbox.core.tpcdi.benchmark import TPCDIBenchmark
from tests.fixtures.result_dict_fixtures import make_benchmark_results
from tests.uat import matrix
from tests.unit.mcp.public_api import get_tool, get_tool_functions

pytestmark = [pytest.mark.unit, pytest.mark.medium]

OWNER_MESSAGE = (
    "TPC-DI is unavailable while BenchBox reworks it to run the specification's workload "
    "(historical load, two incremental updates, automated audit). Earlier BenchBox TPC-DI results were withdrawn."
)
OWNER_REGISTRY_DESCRIPTION = (
    "TPC-DI data integration benchmark. Unavailable while BenchBox reworks it to run the specification's workload."
)
OWNER_LEGACY_WORDING = "Legacy TPC-DI result from a withdrawn workload; not validated."


def _flat(text: str) -> str:
    return " ".join(text.split())


def _tpcdi_result(**overrides) -> BenchmarkResults:
    fields = {
        "benchmark_id": "tpcdi",
        "benchmark_name": "TPC-DI",
        "platform": "duckdb",
        "validation_status": "PASSED",
    }
    fields.update(overrides)
    return make_benchmark_results(**fields)


def _legacy_bundle(tmp_path: Path, name: str = "legacy.json") -> Path:
    payload = build_result_payload(_tpcdi_result(validation_status="FAILED"))
    payload["summary"]["validation"] = "passed"
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_owner_message_is_the_shared_constant() -> None:
    assert TPCDI_UNAVAILABLE_MESSAGE == OWNER_MESSAGE


@pytest.mark.parametrize("name", ["tpcdi", "TPCDI", "TPC-DI", "tpc_di", " tpcdi "])
def test_refusal_check_accepts_every_spelling_of_the_name(name: str) -> None:
    with pytest.raises(BenchmarkUnavailableError, match="TPC-DI is unavailable"):
        refuse_unavailable_benchmark(name)


@pytest.mark.parametrize("name", ["tpch", "tpcds", None, ""])
def test_refusal_check_leaves_other_benchmarks_alone(name) -> None:
    refuse_unavailable_benchmark(name)


class TestCliRefusal:
    @pytest.mark.parametrize(
        "args",
        [
            ["run", "--benchmark", "tpcdi", "--platform", "duckdb"],
            ["run", "--benchmark", "tpcdi", "--platform", "duckdb", "--official", "--scale", "1", "--seed", "1"],
            ["run", "--benchmark", "TPC-DI", "--platform", "duckdb", "--phases", "generate"],
            ["datagen", "--benchmark", "tpcdi", "--scale", "0.01"],
        ],
        ids=["run", "official", "other-spelling-and-phase", "datagen"],
    )
    def test_entry_point_exits_2_with_the_owner_message(self, args: list[str]) -> None:
        result = CliRunner().invoke(cli, args)

        assert result.exit_code == 2
        assert OWNER_MESSAGE in result.output

    def test_dry_run_exits_2_with_the_owner_message(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            cli, ["run", "--benchmark", "tpcdi", "--platform", "duckdb", "--dry-run", str(tmp_path / "preview")]
        )

        assert result.exit_code == 2
        assert OWNER_MESSAGE in result.output
        assert not (tmp_path / "preview").exists() or not any((tmp_path / "preview").iterdir())

    def test_other_benchmarks_are_not_refused(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            cli, ["run", "--benchmark", "tpch", "--platform", "duckdb", "--dry-run", str(tmp_path / "preview")]
        )

        assert OWNER_MESSAGE not in result.output


class TestPythonEntryPoints:
    @pytest.fixture
    def core(self, tmp_path: Path) -> TPCDIBenchmark:
        return TPCDIBenchmark(scale_factor=0.01, output_dir=tmp_path / "data")

    @pytest.fixture
    def facade(self, tmp_path: Path) -> TPCDI:
        return TPCDI(scale_factor=0.01, output_dir=tmp_path / "data")

    @pytest.mark.parametrize(
        ("method", "args"),
        [
            ("generate_data", ()),
            ("run_full_benchmark", (object(),)),
            ("run_etl_benchmark", (object(),)),
            ("calculate_official_metrics", (object(), object())),
            ("execute_dataframe_workload", ()),
            ("get_queries", ()),
            ("get_query", ("AQ1",)),
            ("run_benchmark", (object(),)),
            ("run_etl_pipeline", (object(),)),
            ("generate_source_data", ()),
            ("load_data_to_database", (object(),)),
            ("create_schema", (object(),)),
        ],
    )
    def test_core_class_refuses(self, core: TPCDIBenchmark, method: str, args: tuple) -> None:
        with pytest.raises(BenchmarkUnavailableError) as raised:
            getattr(core, method)(*args)

        assert str(raised.value) == OWNER_MESSAGE

    @pytest.mark.parametrize(
        ("method", "args"),
        [
            ("generate_data", ()),
            ("run_full_benchmark", (object(),)),
            ("run_etl_benchmark", (object(),)),
            ("calculate_official_metrics", (object(), object())),
            ("get_queries", ()),
            ("get_query", ("AQ1",)),
            ("run_benchmark", (object(),)),
            ("run_etl_pipeline", (object(),)),
            ("generate_source_data", ()),
        ],
    )
    def test_facade_refuses(self, facade: TPCDI, method: str, args: tuple) -> None:
        with pytest.raises(BenchmarkUnavailableError) as raised:
            getattr(facade, method)(*args)

        assert str(raised.value) == OWNER_MESSAGE

    def test_lifecycle_refuses_before_touching_the_benchmark(self) -> None:
        from benchbox.core.run_service import execute_run

        config = BenchmarkConfig(name="tpcdi", display_name="TPC-DI", scale_factor=0.01)

        with pytest.raises(BenchmarkUnavailableError):
            execute_run(
                config=config,
                benchmark_instance=object(),
                database_config=None,
                system_profile=None,
                platform_config=None,
                output_root=None,
                phases_to_run=["generate"],
                adapter_factory=lambda **_: None,
                verbosity=None,
            )

    def test_dry_run_executor_refuses(self, tmp_path: Path) -> None:
        config = BenchmarkConfig(name="tpcdi", display_name="TPC-DI", scale_factor=0.01)

        with pytest.raises(BenchmarkUnavailableError):
            DryRunExecutor(output_dir=tmp_path).execute_dry_run(config, None, None)


@pytest.fixture(scope="module")
def tools():
    from benchbox.mcp import create_server

    return get_tool_functions(create_server())


class TestMcpSurface:
    @pytest.mark.parametrize("extra", [{}, {"dry_run": True}, {"validate_only": True}, {"mode": "data_only"}])
    def test_run_tool_refuses(self, tools, extra) -> None:
        response = tools["run_benchmark"](platform="duckdb", benchmark="tpcdi", scale_factor=0.01, **extra)

        assert response["status"] == "failed"
        assert OWNER_MESSAGE in json.dumps(response)

    def test_run_tool_description_no_longer_advertises_tpcdi(self) -> None:
        from benchbox.mcp import create_server

        description = get_tool(create_server(), "run_benchmark").description

        assert description
        assert "tpcdi" not in description.lower()
        assert "tpc-di" not in description.lower()

    def test_query_detail_tool_returns_no_query_and_states_status(self, tools) -> None:
        response = tools["get_query_details"](benchmark="tpcdi", query_id="AQ1")

        assert "sql" not in response
        assert OWNER_MESSAGE in json.dumps(response)
        assert response["details"]["support_status"] == "document_only"
        assert response["details"]["query_count"] == 0

    def test_discovery_reports_zero_queries_and_the_status(self) -> None:
        from benchbox.mcp.tools.discovery import _get_benchmark_info_impl, build_benchmark_payload

        payload = build_benchmark_payload("tpcdi")
        info = _get_benchmark_info_impl("tpcdi")

        assert payload["query_ids"] == []
        assert payload["query_count"] == 0
        assert payload["support_status"] == "document_only"
        assert payload["unavailable"] == OWNER_MESSAGE
        assert info["queries"]["count"] == 0
        assert info["queries"]["ids"] == []
        assert info["unavailable"] == OWNER_MESSAGE

    def test_discovery_does_not_flag_other_benchmarks(self) -> None:
        from benchbox.mcp.tools.discovery import build_benchmark_payload

        assert "unavailable" not in build_benchmark_payload("tpch")


class TestRegistry:
    def test_status_description_and_query_count(self) -> None:
        entry = get_all_benchmarks()["tpcdi"]

        assert entry["support_status"] == "document_only"
        assert entry["description"] == OWNER_REGISTRY_DESCRIPTION
        assert entry["num_queries"] == 0

    def test_uat_resolver_omits_tpcdi(self) -> None:
        benchmarks = matrix.load_benchmarks()

        assert "tpcdi" in benchmarks
        assert "tpcdi" not in matrix.resolve_benchmarks(groups=["all"], benchmarks=benchmarks)
        assert "tpcdi" not in matrix.resolve_benchmarks(groups=["tpc"], include=["tpcdi"], benchmarks=benchmarks)
        assert "tpch" in matrix.resolve_benchmarks(groups=["tpc"], benchmarks=benchmarks)


class TestAuditRule:
    def test_direct_construction_without_audit_block_is_not_validated(self) -> None:
        assert _tpcdi_result().validation_status == "NOT_VALIDATED"

    @pytest.mark.parametrize("spelling", ["tpcdi", "TPC-DI", "tpc_di"])
    def test_every_name_spelling_is_covered(self, spelling: str) -> None:
        result = _tpcdi_result(benchmark_id=spelling, benchmark_name=spelling)

        assert result.validation_status == "NOT_VALIDATED"

    def test_publish_admission_is_non_clean(self) -> None:
        decision = publish_admission(_tpcdi_result(), "unofficial-research")

        assert decision.allowed is False
        assert decision.code == "non_clean"
        assert "not_validated" in str(decision.reason)

    def test_later_assignment_cannot_restore_passed(self) -> None:
        result = _tpcdi_result(validation_status="FAILED")

        result.validation_status = "PASSED"

        assert result.validation_status == "NOT_VALIDATED"

    def test_failure_statuses_are_not_softened(self) -> None:
        assert _tpcdi_result(validation_status="FAILED").validation_status == "FAILED"
        assert _tpcdi_result(validation_status="PARTIAL").validation_status == "PARTIAL"

    def test_audit_block_keeps_the_recorded_status(self) -> None:
        result = _tpcdi_result(validation_details={TPCDI_AUDIT_BLOCK_KEY: {"status": "recorded"}})

        assert result.validation_status == "PASSED"

    def test_other_benchmarks_keep_passed(self) -> None:
        result = make_benchmark_results(benchmark_id="tpch", benchmark_name="TPC-H", validation_status="PASSED")

        assert result.validation_status == "PASSED"

    def test_unfinished_result_builder(self) -> None:
        config = BenchmarkConfig(name="tpcdi", display_name="TPC-DI", scale_factor=0.01)

        result = build_unfinished_result(config, platform="duckdb", validation_status="PASSED", validation_details={})

        assert result.validation_status == "NOT_VALIDATED"

    def test_result_builder(self) -> None:
        builder = ResultBuilder(
            benchmark=BenchmarkInfoInput(name="tpcdi", scale_factor=0.01),
            platform=PlatformInfoInput(name="duckdb", execution_mode="sql"),
        )
        builder.set_validation_status("PASSED", {})

        result = builder.build()

        assert result.validation_status == "NOT_VALIDATED"

    def test_enhanced_result_alias_is_the_same_class(self) -> None:
        from benchbox.platforms.base import EnhancedBenchmarkResults

        assert EnhancedBenchmarkResults is BenchmarkResults

    @pytest.mark.parametrize(("benchmark_id", "expected"), [("tpcdi", "NOT_VALIDATED"), ("tpch", "PASSED")])
    def test_stored_report_rows(self, benchmark_id: str, expected: str) -> None:
        from benchbox.core.results.database import StoredResult

        stored = StoredResult(
            id=1,
            execution_id="e",
            platform="duckdb",
            platform_version=None,
            benchmark=benchmark_id,
            scale_factor=0.01,
            timestamp=datetime(2026, 1, 1),
            duration_seconds=1.0,
            total_queries=1,
            successful_queries=1,
            failed_queries=0,
            geometric_mean_ms=None,
            power_at_size=None,
            throughput_at_size=None,
            qph_at_size=None,
            total_cost=None,
            validation_status="PASSED",
            config_hash="h",
            metadata={},
        )

        assert stored.validation_status == expected


class TestLegacyBundles:
    def test_old_bundle_loads_as_legacy_and_not_validated(self, tmp_path: Path) -> None:
        result, _raw = load_result_file(_legacy_bundle(tmp_path))

        assert result.validation_status == "NOT_VALIDATED"
        assert result.execution_metadata["legacy_result_note"] == OWNER_LEGACY_WORDING
        assert LEGACY_TPCDI_NOTE == OWNER_LEGACY_WORDING

    def test_other_bundles_are_not_marked_legacy(self, tmp_path: Path) -> None:
        payload = build_result_payload(make_benchmark_results(benchmark_id="tpch", benchmark_name="TPC-H"))
        path = tmp_path / "tpch.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

        result, _raw = load_result_file(path)

        assert "legacy_result_note" not in (result.execution_metadata or {})

    def test_publish_admission_refuses_a_legacy_result(self, tmp_path: Path) -> None:
        result, _raw = load_result_file(_legacy_bundle(tmp_path))

        decision = publish_admission(result, "unofficial-research")

        assert decision.allowed is False
        assert decision.code == "legacy_tpcdi"
        assert OWNER_LEGACY_WORDING in str(decision.reason)

    def test_compare_refuses(self, tmp_path: Path) -> None:
        first = _legacy_bundle(tmp_path, "a.json")
        second = _legacy_bundle(tmp_path, "b.json")

        result = CliRunner().invoke(cli, ["compare", str(first), str(second)])

        assert result.exit_code == 4
        assert OWNER_LEGACY_WORDING in _flat(result.output)

    def test_exporter_comparison_refuses(self, tmp_path: Path) -> None:
        first = _legacy_bundle(tmp_path, "a.json")
        second = _legacy_bundle(tmp_path, "b.json")

        comparison = ResultExporter().compare_results(first, second)

        assert OWNER_LEGACY_WORDING in comparison["error"]

    def test_publish_refuses(self, tmp_path: Path) -> None:
        bundle = _legacy_bundle(tmp_path)

        result = CliRunner().invoke(cli, ["publish", "run", str(bundle), "--label", "unofficial-research"])

        assert result.exit_code != 0
        assert OWNER_LEGACY_WORDING in _flat(result.output)

    def test_submit_refuses(self, tmp_path: Path) -> None:
        bundle = _legacy_bundle(tmp_path)

        result = CliRunner().invoke(cli, ["submit", str(bundle), "--dry-run"])

        assert result.exit_code == 1
        assert OWNER_LEGACY_WORDING in _flat(result.output)
