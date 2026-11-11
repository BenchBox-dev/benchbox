from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from benchbox.cli.commands.plan_history import plan_history
from benchbox.core.query_plans.history import (
    PlanHistory,
    PlanHistoryEntry,
    _parse_history_timestamp,
    create_plan_history,
)
from benchbox.core.results.query_plan_models import (
    FINGERPRINT_VERSION,
    LEGACY_FINGERPRINT_VERSION,
    LogicalOperator,
    LogicalOperatorType,
    QueryPlanDAG,
)
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _qr(query_id: str, execution_time_ms: float = 100.0, query_plan: QueryPlanDAG | None = None) -> dict:
    return {"query_id": query_id, "execution_time_ms": execution_time_ms, "query_plan": query_plan}


def _create_plan_with_fingerprint(query_id: str, fingerprint: str) -> QueryPlanDAG:
    root = LogicalOperator(
        operator_id="1",
        operator_type=LogicalOperatorType.SCAN,
        table_name="test",
        children=[],
    )
    return QueryPlanDAG(
        query_id=query_id,
        platform="test",
        logical_root=root,
        plan_fingerprint=fingerprint,
        raw_explain_output="test",
    )


class TestParseHistoryTimestamp:
    def test_z_suffix_parses_as_utc(self) -> None:
        parsed = _parse_history_timestamp("2024-03-10T02:30:00Z")

        assert parsed == datetime(2024, 3, 10, 2, 30, 0, tzinfo=timezone.utc)
        assert parsed != datetime.min.replace(tzinfo=timezone.utc)

    def test_z_suffix_sorts_correctly_against_offset_timestamps(self) -> None:
        z_time = _parse_history_timestamp("2024-03-10T02:30:00Z")
        earlier_offset_time = _parse_history_timestamp("2024-03-10T01:00:00+00:00")
        later_offset_time = _parse_history_timestamp("2024-03-10T05:00:00+00:00")

        assert earlier_offset_time < z_time < later_offset_time

    def test_malformed_timestamp_still_sorts_first(self) -> None:
        assert _parse_history_timestamp("not-a-timestamp") == datetime.min.replace(tzinfo=timezone.utc)

    def test_z_suffix_is_stripped_before_fromisoformat_regardless_of_python_version(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import benchbox.core.query_plans.history as history_module

        seen: list[str] = []
        real_fromisoformat = datetime.fromisoformat

        class _RecordingDatetime:
            @staticmethod
            def fromisoformat(value: str) -> datetime:
                seen.append(value)
                return real_fromisoformat(value)

        monkeypatch.setattr(history_module, "datetime", _RecordingDatetime)

        history_module._parse_history_timestamp("2024-03-10T02:30:00Z")

        assert seen == ["2024-03-10T02:30:00+00:00"]


class TestPlanHistoryEntry:
    def test_to_dict(self) -> None:

        entry = PlanHistoryEntry(
            run_id="run1",
            timestamp="2024-01-01T00:00:00Z",
            fingerprint="a" * 64,
            estimated_cost=100.0,
            execution_time_ms=50.0,
            platform="duckdb",
        )

        result = entry.to_dict()

        assert result["run_id"] == "run1"
        assert result["fingerprint"] == "a" * 64
        assert result["execution_time_ms"] == 50.0

    def test_from_dict(self) -> None:

        data = {
            "run_id": "run1",
            "timestamp": "2024-01-01T00:00:00Z",
            "fingerprint": "a" * 64,
            "estimated_cost": 100.0,
            "execution_time_ms": 50.0,
            "platform": "duckdb",
        }

        entry = PlanHistoryEntry.from_dict(data)

        assert entry.run_id == "run1"
        assert entry.fingerprint == "a" * 64


class TestPlanHistory:
    def test_add_run(self) -> None:

        with tempfile.TemporaryDirectory() as tmpdir:
            history = PlanHistory(Path(tmpdir))

            plan = _create_plan_with_fingerprint("q1", "a" * 64)
            results = make_benchmark_results(
                execution_id="run1",
                query_results=[_qr("q1", 100.0, plan)],
            )

            history.add_run(results)

            assert (Path(tmpdir) / "run1.json").exists()

            with open(Path(tmpdir) / "run1.json", encoding="utf-8") as f:
                data = json.load(f)
            assert data["run_id"] == "run1"
            assert "q1" in data["plan_fingerprints"]
            assert data["plan_fingerprints"]["q1"]["fingerprint"] == "a" * 64

    def test_add_run_without_execution_id_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history = PlanHistory(Path(tmpdir))

            plan = _create_plan_with_fingerprint("q1", "a" * 64)
            results = make_benchmark_results(
                execution_id="",
                query_results=[_qr("q1", 100.0, plan)],
            )

            history.add_run(results)

            assert history.get_run_count() == 0

    def test_query_plan_history(self) -> None:

        with tempfile.TemporaryDirectory() as tmpdir:
            history = PlanHistory(Path(tmpdir))

            for i in range(3):
                plan = _create_plan_with_fingerprint("q1", "a" * 64)
                results = make_benchmark_results(
                    execution_id=f"run{i}",
                    timestamp=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
                    query_results=[_qr("q1", 100.0 + i * 10, plan)],
                )
                history.add_run(results)

            entries = history.query_plan_history("q1")

            assert len(entries) == 3
            assert entries[0].run_id == "run0"
            assert entries[2].run_id == "run2"

    def test_query_plan_history_sorts_mixed_offset_timestamps_chronologically(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history = PlanHistory(Path(tmpdir))

            stored_timestamps = (
                ("run-a", "2024-03-10T02:30:00+00:00"),
                ("run-b", "2024-03-10T06:30:00+08:00"),
            )
            for exec_id, iso_timestamp in stored_timestamps:
                plan = _create_plan_with_fingerprint("q1", "a" * 64)
                history.add_run(
                    make_benchmark_results(
                        execution_id=exec_id,
                        query_results=[_qr("q1", 100.0, plan)],
                    )
                )
                entry_file = Path(tmpdir) / f"{exec_id}.json"
                with open(entry_file, encoding="utf-8") as f:
                    data = json.load(f)
                data["timestamp"] = iso_timestamp
                with open(entry_file, "w", encoding="utf-8") as f:
                    json.dump(data, f)
            history._cache.clear()

            entries = history.query_plan_history("q1")

            assert [e.run_id for e in entries] == ["run-b", "run-a"]

    def test_query_plan_history_empty(self) -> None:

        with tempfile.TemporaryDirectory() as tmpdir:
            history = PlanHistory(Path(tmpdir))
            entries = history.query_plan_history("nonexistent")
            assert entries == []

    def test_detect_plan_flapping_no_flapping(self) -> None:

        with tempfile.TemporaryDirectory() as tmpdir:
            history = PlanHistory(Path(tmpdir))

            for i in range(5):
                plan = _create_plan_with_fingerprint("q1", "a" * 64)
                results = make_benchmark_results(
                    execution_id=f"run{i}",
                    timestamp=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
                    query_results=[_qr("q1", 100.0, plan)],
                )
                history.add_run(results)

            assert history.detect_plan_flapping("q1") is False

    def test_detect_plan_flapping_single_change(self) -> None:

        with tempfile.TemporaryDirectory() as tmpdir:
            history = PlanHistory(Path(tmpdir))

            fingerprints = ["a" * 64, "a" * 64, "b" * 64, "b" * 64, "b" * 64]
            for i, fp in enumerate(fingerprints):
                plan = _create_plan_with_fingerprint("q1", fp)
                results = make_benchmark_results(
                    execution_id=f"run{i}",
                    timestamp=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
                    query_results=[_qr("q1", 100.0, plan)],
                )
                history.add_run(results)

            assert history.detect_plan_flapping("q1") is False

    def test_detect_plan_flapping_detected(self) -> None:

        with tempfile.TemporaryDirectory() as tmpdir:
            history = PlanHistory(Path(tmpdir))

            fingerprints = ["a" * 64, "b" * 64, "a" * 64, "b" * 64, "a" * 64]
            for i, fp in enumerate(fingerprints):
                plan = _create_plan_with_fingerprint("q1", fp)
                results = make_benchmark_results(
                    execution_id=f"run{i}",
                    timestamp=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
                    query_results=[_qr("q1", 100.0, plan)],
                )
                history.add_run(results)

            assert history.detect_plan_flapping("q1") is True

    def test_detect_plan_flapping_ignores_fingerprint_version_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history = PlanHistory(Path(tmpdir))

            specs = [("v1-a" * 16, 1), ("v2-a" * 16, 2), ("v1-a" * 16, 1), ("v2-a" * 16, 2), ("v1-a" * 16, 1)]
            for i, (fp, version) in enumerate(specs):
                plan = _create_plan_with_fingerprint("q1", fp)
                plan.fingerprint_version = version
                results = make_benchmark_results(
                    execution_id=f"run{i}",
                    timestamp=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
                    query_results=[_qr("q1", 100.0, plan)],
                )
                history.add_run(results)

            assert history.detect_plan_flapping("q1") is False

    def test_detect_plan_flapping_few_runs(self) -> None:

        with tempfile.TemporaryDirectory() as tmpdir:
            history = PlanHistory(Path(tmpdir))

            for i in range(2):
                fp = "a" * 64 if i == 0 else "b" * 64
                plan = _create_plan_with_fingerprint("q1", fp)
                results = make_benchmark_results(
                    execution_id=f"run{i}",
                    timestamp=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
                    query_results=[_qr("q1", 100.0, plan)],
                )
                history.add_run(results)

            assert history.detect_plan_flapping("q1") is False

    def test_detect_plan_flapping_ignores_cross_platform_alternation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history = PlanHistory(Path(tmpdir))

            for i in range(5):
                platform = "duckdb" if i % 2 == 0 else "postgres"
                fingerprint = "a" * 64 if platform == "duckdb" else "b" * 64
                plan = _create_plan_with_fingerprint("q1", fingerprint)
                results = make_benchmark_results(
                    execution_id=f"run{i}",
                    timestamp=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
                    platform=platform,
                    query_results=[_qr("q1", 100.0, plan)],
                )
                history.add_run(results)

            assert history.detect_plan_flapping("q1") is False

    def test_detect_plan_flapping_detects_real_flap_within_one_platform(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history = PlanHistory(Path(tmpdir))

            duckdb_fingerprints = ["a" * 64, "c" * 64, "a" * 64, "c" * 64, "a" * 64]
            for i, fp in enumerate(duckdb_fingerprints):
                plan = _create_plan_with_fingerprint("q1", fp)
                history.add_run(
                    make_benchmark_results(
                        execution_id=f"duckdb-run{i}",
                        timestamp=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
                        platform="duckdb",
                        query_results=[_qr("q1", 100.0, plan)],
                    )
                )
            for i in range(5):
                plan = _create_plan_with_fingerprint("q1", "b" * 64)
                history.add_run(
                    make_benchmark_results(
                        execution_id=f"postgres-run{i}",
                        timestamp=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
                        platform="postgres",
                        query_results=[_qr("q1", 100.0, plan)],
                    )
                )

            assert history.detect_plan_flapping("q1") is True

    def test_get_plan_version_history(self) -> None:

        with tempfile.TemporaryDirectory() as tmpdir:
            history = PlanHistory(Path(tmpdir))

            fingerprints = ["a" * 64, "a" * 64, "b" * 64, "c" * 64, "c" * 64]
            for i, fp in enumerate(fingerprints):
                plan = _create_plan_with_fingerprint("q1", fp)
                results = make_benchmark_results(
                    execution_id=f"run{i}",
                    timestamp=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
                    query_results=[_qr("q1", 100.0, plan)],
                )
                history.add_run(results)

            versions = history.get_plan_version_history("q1")

            assert len(versions) == 5
            assert versions[0] == ("a" * 64, 1)
            assert versions[1] == ("a" * 64, 1)
            assert versions[2] == ("b" * 64, 2)
            assert versions[3] == ("c" * 64, 3)
            assert versions[4] == ("c" * 64, 3)

    def test_get_plan_version_history_ignores_fingerprint_version_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history = PlanHistory(Path(tmpdir))

            specs = [
                ("a" * 64, 1),
                ("a2" * 32, 2),
                ("a2" * 32, 2),
                ("b" * 64, 2),
            ]
            for i, (fp, version) in enumerate(specs):
                plan = _create_plan_with_fingerprint("q1", fp)
                plan.fingerprint_version = version
                results = make_benchmark_results(
                    execution_id=f"run{i}",
                    timestamp=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
                    query_results=[_qr("q1", 100.0, plan)],
                )
                history.add_run(results)

            versions = history.get_plan_version_history("q1")

            assert len(versions) == 4
            assert versions[0] == ("a" * 64, 1)
            assert versions[1] == ("a2" * 32, 1)
            assert versions[2] == ("a2" * 32, 1)
            assert versions[3] == ("b" * 64, 2)

    def test_get_all_query_ids(self) -> None:

        with tempfile.TemporaryDirectory() as tmpdir:
            history = PlanHistory(Path(tmpdir))

            plan1 = _create_plan_with_fingerprint("q1", "a" * 64)
            plan2 = _create_plan_with_fingerprint("q2", "b" * 64)
            results = make_benchmark_results(
                execution_id="run1",
                query_results=[
                    _qr("q1", 100.0, plan1),
                    _qr("q2", 200.0, plan2),
                ],
            )
            history.add_run(results)

            query_ids = history.get_all_query_ids()

            assert query_ids == {"q1", "q2"}

    def test_get_run_count(self) -> None:

        with tempfile.TemporaryDirectory() as tmpdir:
            history = PlanHistory(Path(tmpdir))

            assert history.get_run_count() == 0

            for i in range(3):
                plan = _create_plan_with_fingerprint("q1", "a" * 64)
                results = make_benchmark_results(
                    execution_id=f"run{i}",
                    query_results=[_qr("q1", 100.0, plan)],
                )
                history.add_run(results)

            assert history.get_run_count() == 3

    def test_clear(self) -> None:

        with tempfile.TemporaryDirectory() as tmpdir:
            history = PlanHistory(Path(tmpdir))

            plan = _create_plan_with_fingerprint("q1", "a" * 64)
            results = make_benchmark_results(
                execution_id="run1",
                query_results=[_qr("q1", 100.0, plan)],
            )
            history.add_run(results)

            assert history.get_run_count() == 1

            history.clear()

            assert history.get_run_count() == 0


class TestCreatePlanHistory:
    def test_creates_history_instance(self) -> None:

        with tempfile.TemporaryDirectory() as tmpdir:
            history = create_plan_history(tmpdir)
            assert isinstance(history, PlanHistory)

    def test_creates_directory_if_not_exists(self) -> None:

        with tempfile.TemporaryDirectory() as tmpdir:
            new_dir = Path(tmpdir) / "subdir" / "history"
            create_plan_history(new_dir)
            assert new_dir.exists()


class TestAddRunEndToEndViaCLI:
    def test_real_benchmark_results_recorded_and_shown_by_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history_dir = Path(tmpdir)
            history = PlanHistory(history_dir)

            for i, fp in enumerate(["a" * 64, "a" * 64, "b" * 64]):
                plan = _create_plan_with_fingerprint("1", fp)
                results = make_benchmark_results(
                    execution_id=f"run{i}",
                    timestamp=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
                    platform="duckdb",
                    query_results=[_qr("1", 100.0 + i, plan)],
                )
                history.add_run(results)

            result = CliRunner().invoke(
                plan_history,
                ["--query-id", "1", "--history-dir", str(history_dir)],
            )

            assert result.exit_code == 0, result.output
            assert "Plan History for 1" in result.output
            assert "run0" in result.output
            assert "run2" in result.output
            assert "Unique plans: 2" in result.output


def _write_legacy_history_file(
    tmpdir: str | Path,
    exec_id: str,
    timestamp: str,
    query_id: str,
    fingerprint: str,
    platform: str = "duckdb",
) -> None:
    payload = {
        "run_id": exec_id,
        "timestamp": timestamp,
        "platform": platform,
        "plan_fingerprints": {
            query_id: {
                "fingerprint": fingerprint,
                "estimated_cost": 100.0,
                "execution_time_ms": 50.0,
            }
        },
    }
    with open(Path(tmpdir) / f"{exec_id}.json", "w", encoding="utf-8") as f:
        json.dump(payload, f)


class TestFingerprintVersionLegacy:
    def test_from_dict_legacy_defaults_to_v1_and_round_trips(self) -> None:
        entry = PlanHistoryEntry.from_dict(
            {
                "run_id": "run1",
                "timestamp": "2024-01-01T00:00:00+00:00",
                "fingerprint": "a" * 64,
                "estimated_cost": 100.0,
                "execution_time_ms": 50.0,
                "platform": "duckdb",
            }
        )

        assert entry.fingerprint_version == LEGACY_FINGERPRINT_VERSION
        assert entry.to_dict()["fingerprint_version"] == LEGACY_FINGERPRINT_VERSION
        assert PlanHistoryEntry.from_dict(entry.to_dict()).fingerprint_version == LEGACY_FINGERPRINT_VERSION

    def test_legacy_history_file_loads_as_v1_without_drops(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_legacy_history_file(tmpdir, "run0", "2024-01-01T00:00:00+00:00", "q1", "a" * 64)
            _write_legacy_history_file(tmpdir, "run1", "2024-01-02T00:00:00+00:00", "q1", "b" * 64)

            entries = PlanHistory(Path(tmpdir)).query_plan_history("q1")

            assert [e.run_id for e in entries] == ["run0", "run1"]
            assert all(e.fingerprint_version == LEGACY_FINGERPRINT_VERSION for e in entries)

    def test_legacy_v1_then_v2_boundary_is_neither_flap_nor_bump(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history = PlanHistory(Path(tmpdir))
            _write_legacy_history_file(tmpdir, "run0", "2024-01-01T00:00:00+00:00", "q1", "a" * 64)

            plan = _create_plan_with_fingerprint("q1", "c" * 64)
            assert plan.fingerprint_version == FINGERPRINT_VERSION != LEGACY_FINGERPRINT_VERSION
            for i, exec_id in enumerate(("run1", "run2")):
                history.add_run(
                    make_benchmark_results(
                        execution_id=exec_id,
                        timestamp=datetime(2024, 1, i + 2, tzinfo=timezone.utc),
                        platform="duckdb",
                        query_results=[_qr("q1", 100.0, plan)],
                    )
                )
            _write_legacy_history_file(tmpdir, "run3", "2024-01-04T00:00:00+00:00", "q1", "a" * 64)

            entries = history.query_plan_history("q1")
            assert [e.fingerprint_version for e in entries] == [
                LEGACY_FINGERPRINT_VERSION,
                plan.fingerprint_version,
                plan.fingerprint_version,
                LEGACY_FINGERPRINT_VERSION,
            ]
            assert history.detect_plan_flapping("q1") is False
            assert history.get_plan_version_history("q1") == [
                ("a" * 64, 1),
                ("c" * 64, 1),
                ("c" * 64, 1),
                ("a" * 64, 1),
            ]

    def test_genuine_flap_within_v2_after_boundary_still_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history = PlanHistory(Path(tmpdir))
            _write_legacy_history_file(tmpdir, "run0", "2024-01-01T00:00:00+00:00", "q1", "a" * 64)

            for i, fp in enumerate(["x" * 64, "y" * 64, "x" * 64, "y" * 64, "x" * 64]):
                plan = _create_plan_with_fingerprint("q1", fp)
                history.add_run(
                    make_benchmark_results(
                        execution_id=f"run{i + 1}",
                        timestamp=datetime(2024, 1, i + 2, tzinfo=timezone.utc),
                        platform="duckdb",
                        query_results=[_qr("q1", 100.0, plan)],
                    )
                )

            assert history.detect_plan_flapping("q1") is True
            versions = history.get_plan_version_history("q1")
            assert [v for _, v in versions] == [1, 1, 2, 3, 4, 5]


class TestPlatformPartitionAndVersionAwareMetrics:
    def _add(self, history: PlanHistory, exec_id: str, platform: str, fp: str) -> None:
        plan = _create_plan_with_fingerprint("q1", fp)
        history.add_run(
            make_benchmark_results(
                execution_id=exec_id,
                timestamp=datetime(2024, 1, int(exec_id[-1]) + 1, tzinfo=timezone.utc),
                platform=platform,
                query_results=[_qr("q1", 100.0, plan)],
            )
        )

    def test_version_history_platform_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history = PlanHistory(Path(tmpdir))
            self._add(history, "run0", "duckdb", "a" * 64)
            self._add(history, "run1", "postgres", "b" * 64)
            self._add(history, "run2", "duckdb", "a" * 64)

            assert [v for _, v in history.get_plan_version_history("q1")] == [1, 2, 3]
            duck = history.get_plan_version_history("q1", platform="duckdb")
            assert duck == [("a" * 64, 1), ("a" * 64, 1)]
            pg = history.get_plan_version_history("q1", platform="postgres")
            assert pg == [("b" * 64, 1)]

    def test_mixed_platform_lineage_warns_without_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history_dir = Path(tmpdir)
            history = PlanHistory(history_dir)
            plan = _create_plan_with_fingerprint("q1", "a" * 64)
            history.add_run(
                make_benchmark_results(
                    execution_id="run0",
                    timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    platform="duckdb",
                    query_results=[_qr("q1", 100.0, plan)],
                )
            )
            history.add_run(
                make_benchmark_results(
                    execution_id="run1",
                    timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
                    platform="postgres",
                    query_results=[_qr("q1", 100.0, plan)],
                )
            )
            mixed = CliRunner().invoke(plan_history, ["--query-id", "q1", "--history-dir", str(history_dir)])
            assert mixed.exit_code == 0, mixed.output
            assert "--platform" in mixed.output
            filtered = CliRunner().invoke(
                plan_history, ["--query-id", "q1", "--history-dir", str(history_dir), "--platform", "duckdb"]
            )
            assert filtered.exit_code == 0, filtered.output
            assert "--platform" not in filtered.output

    def test_cli_version_aware_summary_ignores_encoding_bump(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history_dir = Path(tmpdir)
            history = PlanHistory(history_dir)
            plan_v1 = _create_plan_with_fingerprint("1", "a" * 64)
            plan_v1.fingerprint_version = LEGACY_FINGERPRINT_VERSION
            plan_v2 = _create_plan_with_fingerprint("1", "a2" * 32)
            plan_v2.fingerprint_version = FINGERPRINT_VERSION
            for i, plan in enumerate((plan_v1, plan_v2, plan_v2)):
                history.add_run(
                    make_benchmark_results(
                        execution_id=f"run{i}",
                        timestamp=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
                        platform="duckdb",
                        query_results=[_qr("1", 100.0, plan)],
                    )
                )
            result = CliRunner().invoke(plan_history, ["--query-id", "1", "--history-dir", str(history_dir)])
            assert result.exit_code == 0, result.output
            assert "Unique plans: 1" in result.output
            assert "Plan changes: 0" in result.output

    def test_flap_back_counts_two_unique_plans(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history_dir = Path(tmpdir)
            history = PlanHistory(history_dir)
            for i, fp in enumerate(("a" * 64, "b" * 64, "a" * 64)):
                self._add(history, f"run{i}", "duckdb", fp)
            assert [v for _, v in history.get_plan_version_history("q1")] == [1, 2, 3]
            assert history.count_unique_plans("q1") == 2
            result = CliRunner().invoke(plan_history, ["--query-id", "q1", "--history-dir", str(history_dir)])
            assert result.exit_code == 0, result.output
            assert "Unique plans: 2" in result.output
            assert "Plan changes: 2" in result.output


