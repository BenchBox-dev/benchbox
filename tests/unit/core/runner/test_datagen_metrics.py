from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from benchbox.base import BaseBenchmark
from benchbox.core.results.schema import build_result_payload
from benchbox.core.runner import run_benchmark_lifecycle
from benchbox.core.schemas import BenchmarkConfig, SystemProfile

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _mk_system_profile() -> SystemProfile:
    return SystemProfile(
        os_name="Linux",
        os_version="6.6",
        architecture="x86_64",
        cpu_model="Test CPU",
        cpu_cores_physical=4,
        cpu_cores_logical=8,
        memory_total_gb=16.0,
        memory_available_gb=12.0,
        python_version="3.11",
        disk_space_gb=256.0,
        timestamp=datetime.now(),
        hostname="host",
    )


class _DatagenBenchmark(BaseBenchmark):
    def __init__(self, benchmark_id: str, output_dir: Path):
        super().__init__(scale_factor=0.01, output_dir=output_dir)
        self._name = benchmark_id.upper()
        self.tables = None

    def generate_data(self) -> list[Path]:
        return []

    def get_queries(self) -> dict[str, str]:
        return {}

    def get_query(self, query_id, *, params=None) -> str:
        raise ValueError(f"Query {query_id} not found")


def _run_data_only(
    benchmark_id: str,
    output_dir: Path,
    ensure_data_generated_side_effect,
):
    benchmark = _DatagenBenchmark(benchmark_id, output_dir)
    config = BenchmarkConfig(
        name=benchmark_id,
        display_name=benchmark_id.upper(),
        scale_factor=0.01,
        test_execution_type="data_only",
    )

    with patch(
        "benchbox.core.runner.runner._ensure_data_generated",
        side_effect=ensure_data_generated_side_effect,
    ):
        return run_benchmark_lifecycle(
            benchmark_config=config,
            database_config=None,
            system_profile=_mk_system_profile(),
            benchmark_instance=benchmark,
            output_root=str(output_dir),
            enable_resource_monitoring=False,
        )


def _sleeping_ensure_data_generated(*_args, **_kwargs) -> tuple[bool, bool]:
    time.sleep(0.02)
    return True, False


def _write_manifest_v1(output_dir: Path, benchmark_id: str) -> None:
    manifest = {
        "benchmark": benchmark_id,
        "scale_factor": 0.01,
        "tables": {
            "lineitem": [
                {"path": "lineitem.tbl", "size_bytes": 120, "row_count": 10},
                {"path": "lineitem_2.tbl", "size_bytes": 80, "row_count": 6},
            ],
            "orders": [
                {"path": "orders.tbl", "size_bytes": 50, "row_count": 4},
            ],
        },
    }
    (output_dir / "_datagen_manifest.json").write_text(json.dumps(manifest))


def _write_manifest_v2(output_dir: Path, benchmark_id: str) -> None:
    manifest = {
        "version": 2,
        "benchmark": benchmark_id,
        "scale_factor": 0.01,
        "tables": {
            "lineitem": {
                "formats": {
                    "tbl": [{"path": "lineitem.tbl", "size_bytes": 120, "row_count": 10}],
                    "parquet": [{"path": "lineitem.parquet", "size_bytes": 60, "row_count": 10}],
                }
            },
            "orders": {
                "formats": {
                    "tbl": [{"path": "orders.tbl", "size_bytes": 50, "row_count": 4}],
                }
            },
        },
    }
    (output_dir / "_datagen_manifest.json").write_text(json.dumps(manifest))


def test_datagen_duration_in_run_block(tmp_path: Path):
    result = _run_data_only("tpch", tmp_path, _sleeping_ensure_data_generated)
    payload = build_result_payload(result)

    assert payload["run"]["total_duration_ms"] > 0


def test_datagen_phase_has_duration_ms(tmp_path: Path):
    result = _run_data_only("tpch", tmp_path, _sleeping_ensure_data_generated)
    phase = result.execution_metadata["phase_status"]["data_generation"]
    payload = build_result_payload(result)
    payload_phase = payload["phases"]["data_generation"]

    assert phase["duration_ms"] > 0
    assert payload_phase["duration_ms"] > 0


@pytest.mark.parametrize("benchmark_id", ["tpch", "tpcds", "ssb"])
def test_datagen_phase_has_table_stats_when_manifest_present(tmp_path: Path, benchmark_id: str):
    _write_manifest_v1(tmp_path, benchmark_id)
    result = _run_data_only(benchmark_id, tmp_path, lambda *_args, **_kwargs: (False, True))
    phase = result.execution_metadata["phase_status"]["data_generation"]
    payload = build_result_payload(result)
    payload_phase = payload["phases"]["data_generation"]

    assert phase["tables_generated"] == 2
    assert phase["total_rows"] == 20
    assert phase["total_size_bytes"] == 250
    assert payload_phase["tables_generated"] == 2
    assert payload_phase["total_rows"] == 20
    assert payload_phase["total_size_bytes"] == 250


def test_datagen_phase_gracefully_degrades_without_manifest(tmp_path: Path):
    result = _run_data_only("tpch", tmp_path, _sleeping_ensure_data_generated)
    phase = result.execution_metadata["phase_status"]["data_generation"]
    payload = build_result_payload(result)
    payload_phase = payload["phases"]["data_generation"]

    assert phase["status"] == "COMPLETED"
    assert phase["duration_ms"] > 0
    assert "tables_generated" not in phase
    assert "total_rows" not in phase
    assert "total_size_bytes" not in phase
    assert payload_phase["status"] == "COMPLETED"
    assert "duration_ms" in payload_phase
    assert "tables_generated" not in payload_phase
    assert "total_rows" not in payload_phase
    assert "total_size_bytes" not in payload_phase


def test_datagen_phase_has_table_stats_from_v2_manifest(tmp_path: Path):
    _write_manifest_v2(tmp_path, "tpch")
    result = _run_data_only("tpch", tmp_path, lambda *_args, **_kwargs: (False, True))
    phase = result.execution_metadata["phase_status"]["data_generation"]
    payload = build_result_payload(result)
    payload_phase = payload["phases"]["data_generation"]

    assert phase["tables_generated"] == 2
    assert phase["total_rows"] == 14
    assert phase["total_size_bytes"] == 170
    assert payload_phase["tables_generated"] == 2
    assert payload_phase["total_rows"] == 14
    assert payload_phase["total_size_bytes"] == 170
