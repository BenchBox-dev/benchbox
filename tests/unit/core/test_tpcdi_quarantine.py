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
    unavailable_benchmark_message,
)
from benchbox.core.schemas import BenchmarkConfig
from benchbox.core.tpcdi.benchmark import TPCDIBenchmark
from tests.fixtures.result_dict_fixtures import make_benchmark_results, make_v2_result_dict
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
            ("get_query", ("Q1",)),
            ("run_benchmark", (object(),)),
            ("run_benchmark", (object(), [])),
            ("run_benchmark", (object(), ["Q1"], False, False)),
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
            ("get_queries", ()),
            ("get_query", ("Q1",)),
            ("run_benchmark", (object(),)),
            ("run_benchmark", (object(), [])),
            ("run_benchmark", (object(), ["Q1"], False, False)),
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
        response = tools["get_query_details"](benchmark="tpcdi", query_id="Q1")

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

    def test_refused_benchmarks_are_exactly_the_document_only_ones(self) -> None:
        registry = get_all_benchmarks()

        refused = {benchmark_id for benchmark_id in registry if unavailable_benchmark_message(benchmark_id)}
        document_only = {
            benchmark_id for benchmark_id, entry in registry.items() if entry["support_status"] == "document_only"
        }

        assert refused == document_only == {"tpcdi"}

    def test_group_selected_unavailable_benchmark_is_recorded_as_pruned(self) -> None:
        from tests.uat.config import validate_config
        from tests.uat.phases import enumerate as enum_phase

        config = validate_config(
            {
                "name": "group-accounting",
                "platforms": {"include": ["duckdb"]},
                "benchmarks": {"groups": ["tpc"]},
                "scales": {"rungs": [0.01]},
            }
        )

        result = enum_phase.enumerate_cells_with_pruning(config)

        assert "tpcdi" not in {cell.benchmark for cell in result.cells}
        rows = [row for row in result.compatibility_pruned if row.benchmark == "tpcdi"]
        assert rows
        assert {row.rule_id for row in rows} == {"benchmark-unavailable"}
        assert all(row.reason == OWNER_MESSAGE for row in rows)

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

    def test_top_level_audit_key_does_not_launder_a_withdrawn_bundle(self, tmp_path: Path) -> None:
        from benchbox.core.results.analytics import compare_results

        paths = []
        for name in ("a.json", "b.json"):
            path = _legacy_bundle(tmp_path, name)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["tpcdi_audit"] = True
            path.write_text(json.dumps(payload), encoding="utf-8")
            paths.append(path)

        for comparison in (ResultExporter().compare_results(*paths), compare_results(*paths)):
            assert OWNER_LEGACY_WORDING in comparison["error"]

    def test_comparison_refuses_a_bundle_named_tpcdi_that_cannot_be_reconstructed(self, tmp_path: Path) -> None:
        paths = []
        for name in ("a.json", "b.json"):
            path = tmp_path / name
            path.write_text(json.dumps({"result_schema_version": "2.3", "benchmark": {"name": "TPC-DI"}}), "utf-8")
            paths.append(path)

        assert OWNER_LEGACY_WORDING in ResultExporter().compare_results(*paths)["error"]

    def test_integrity_validator_never_passes_a_withdrawn_bundle(self, tmp_path: Path) -> None:
        from benchbox.core.results.integrity_validator import ResultIntegrityValidator, report_payload

        payload = json.loads(_legacy_bundle(tmp_path).read_text(encoding="utf-8"))

        report = report_payload(ResultIntegrityValidator().validate(payload))

        assert report["overall_status"] == "FAIL"
        assert any(OWNER_MESSAGE in str(check) for check in report["checks"])

    def test_lifecycle_entry_point_refuses(self) -> None:
        from benchbox.core.runner.runner import run_benchmark_lifecycle

        config = BenchmarkConfig(name="tpcdi", display_name="TPC-DI", scale_factor=0.01)

        with pytest.raises(BenchmarkUnavailableError):
            run_benchmark_lifecycle(config, None, None)

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


def _timed_bundle(directory: Path, name: str, benchmark_id: str, benchmark_name: str, stamp: str) -> Path:
    queries = [
        {"id": "Q1", "ms": 100.0, "rows": 1, "iter": 1, "stream": 0, "run_type": "measurement", "status": "SUCCESS"}
    ]
    payload = make_v2_result_dict(
        benchmark_id=benchmark_id, benchmark_name=benchmark_name, queries=queries, timestamp=stamp
    )
    path = directory / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestOtherComparisonPaths:
    @pytest.mark.parametrize(
        ("benchmark_id", "benchmark_name", "expected_runs"),
        [("tpcdi", "TPC-DI", 0), ("tpch", "TPC-H", 2)],
    )
    def test_results_directory_analytics(
        self, tmp_path: Path, benchmark_id: str, benchmark_name: str, expected_runs: int
    ) -> None:
        from benchbox.core.results.analytics import aggregate_results, get_performance_trends

        _timed_bundle(tmp_path, "a.json", benchmark_id, benchmark_name, "2026-01-01T12:00:00")
        _timed_bundle(tmp_path, "b.json", benchmark_id, benchmark_name, "2026-01-02T12:00:00")

        aggregates = aggregate_results(tmp_path)
        trends = get_performance_trends(tmp_path)

        assert aggregates.get("summary", {}).get("total_runs", 0) == expected_runs
        assert len(trends.get("data_points", [])) == expected_runs

    def test_regression_detection_skips_withdrawn_bundles(self, tmp_path: Path) -> None:
        from benchbox.core.results.analytics import detect_regressions

        _timed_bundle(tmp_path, "a.json", "tpcdi", "TPC-DI", "2026-01-01T12:00:00")
        _timed_bundle(tmp_path, "b.json", "tpcdi", "TPC-DI", "2026-01-02T12:00:00")

        report = detect_regressions(tmp_path)

        assert report["status"] != "ok"
        assert report["regressions"] == []

    def test_compare_plans_refuses(self, tmp_path: Path) -> None:
        first = _legacy_bundle(tmp_path, "a.json")
        second = _legacy_bundle(tmp_path, "b.json")

        result = CliRunner().invoke(cli, ["compare-plans", "--run1", str(first), "--run2", str(second)])

        assert result.exit_code == 1
        assert OWNER_LEGACY_WORDING in _flat(result.output)

    def test_platform_comparison_refuses(self, tmp_path: Path) -> None:
        from benchbox.core.analysis.comparison import PlatformComparison

        first = _timed_bundle(tmp_path, "a.json", "tpcdi", "TPC-DI", "2026-01-01T12:00:00")
        second = _timed_bundle(tmp_path, "b.json", "tpcdi", "TPC-DI", "2026-01-02T12:00:00")

        with pytest.raises(ValueError, match="Legacy TPC-DI result"):
            PlatformComparison.from_files([first, second])
