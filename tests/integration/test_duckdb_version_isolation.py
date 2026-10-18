from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchbox.utils.runtime_env import discover_isolated_runtime

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


_ISOLATED_PROCESS_CODE = """
import json
import sys
from datetime import datetime

from benchbox.core.results.models import BenchmarkResults
from benchbox.core.results.schema import build_result_payload
from benchbox.platforms.duckdb import DuckDBAdapter

runtime = json.loads(sys.argv[1])

adapter = DuckDBAdapter(
    database_path=":memory:",
    driver_package="duckdb",
    driver_version_requested=runtime["requested_version"],
    driver_version_resolved=runtime["resolved_version"],
    driver_runtime_strategy=runtime["strategy"],
    driver_runtime_path=runtime["runtime_path"],
    driver_runtime_python_executable=runtime["python_executable"],
)
conn = adapter.create_connection()
try:
    conn.execute("SELECT 1").fetchone()
    platform_info = adapter.get_platform_info(conn)
    result = BenchmarkResults(
        benchmark_name="DuckDB Isolation Test",
        _benchmark_id_override="duckdb_isolation_test",
        platform="duckdb",
        scale_factor=0.01,
        execution_id=f"duckdb-isolation-{runtime['requested_version']}",
        timestamp=datetime(2026, 2, 20, 12, 0, 0),
        duration_seconds=0.01,
        total_queries=1,
        successful_queries=1,
        failed_queries=0,
        query_results=[
            {
                "query_id": "Q1",
                "execution_time_ms": 1,
                "rows_returned": 1,
                "status": "SUCCESS",
            }
        ],
        platform_info=platform_info,
        execution_metadata={
            "mode": "sql",
            "driver_package": adapter.driver_package,
            "driver_version_requested": adapter.driver_version_requested,
            "driver_version_resolved": adapter.driver_version_resolved,
            "driver_version_actual": adapter.driver_version_actual,
            "driver_runtime_strategy": adapter.driver_runtime_strategy,
            "driver_runtime_path": adapter.driver_runtime_path,
            "driver_runtime_python_executable": adapter.driver_runtime_python_executable,
        },
        driver_package=adapter.driver_package,
        driver_version_requested=adapter.driver_version_requested,
        driver_version_resolved=adapter.driver_version_resolved,
        driver_version_actual=adapter.driver_version_actual,
        driver_runtime_strategy=adapter.driver_runtime_strategy,
        driver_runtime_path=adapter.driver_runtime_path,
        driver_runtime_python_executable=adapter.driver_runtime_python_executable,
    )
    payload = build_result_payload(result)
    print("JSON_PAYLOAD::" + json.dumps(payload, sort_keys=True))
finally:
    adapter.close_connection(conn)
"""


def _run_isolated_process(version: str) -> dict:
    runtime = discover_isolated_runtime(package_name="duckdb", requested_version=version)
    if runtime is None:
        pytest.skip(f"No isolated runtime found for duckdb=={version}")

    repo_root = Path(__file__).resolve().parents[2]

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            _ISOLATED_PROCESS_CODE,
            json.dumps(
                {
                    "requested_version": version,
                    "resolved_version": runtime.version,
                    "strategy": runtime.strategy,
                    "runtime_path": runtime.runtime_path,
                    "python_executable": runtime.python_executable,
                }
            ),
        ],
        check=True,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    payload_lines = [line for line in completed.stdout.splitlines() if line.startswith("JSON_PAYLOAD::")]
    if not payload_lines:
        raise AssertionError(
            f"Expected JSON payload line, got stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return json.loads(payload_lines[-1].split("::", 1)[1])


@pytest.mark.integration
def test_duckdb_requested_versions_produce_distinct_actual_runtime_versions():
    payload_092 = _run_isolated_process("0.9.2")
    payload_122 = _run_isolated_process("1.2.2")

    assert payload_092["platform"]["version"] == "0.9.2"
    assert payload_122["platform"]["version"] == "1.2.2"
    assert payload_092["platform"]["version"] != payload_122["platform"]["version"]

    assert payload_092["execution"]["driver_version_requested"] == "0.9.2"
    assert payload_122["execution"]["driver_version_requested"] == "1.2.2"
    assert payload_092["execution"]["driver_version_actual"] == "0.9.2"
    assert payload_122["execution"]["driver_version_actual"] == "1.2.2"
    assert payload_092["execution"]["driver_version_actual"] != payload_122["execution"]["driver_version_actual"]


@pytest.mark.parametrize(
    "version, runtime_path, python_executable",
    [
        ("0.9.2", None, None),
        ("1.2.2", '/tmp/runtime "quoted" 雪\nsite-packages', "/tmp/python's path"),
    ],
)
def test_isolated_process_transports_runtime_values_without_source_interpolation(
    monkeypatch: pytest.MonkeyPatch,
    version: str,
    runtime_path: str | None,
    python_executable: str | None,
) -> None:
    runtime = SimpleNamespace(
        version=version,
        strategy="isolated-site-packages",
        runtime_path=runtime_path,
        python_executable=python_executable,
    )
    monkeypatch.setattr(sys.modules[__name__], "discover_isolated_runtime", lambda **kwargs: runtime)
    calls: list[tuple[list[str], dict]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout='JSON_PAYLOAD::{"ok": true}\n', stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert _run_isolated_process(version) == {"ok": True}
    command, kwargs = calls[0]
    assert command[:3] == [sys.executable, "-c", _ISOLATED_PROCESS_CODE]
    assert len(command) == 4
    assert json.loads(command[3]) == {
        "requested_version": version,
        "resolved_version": runtime.version,
        "strategy": runtime.strategy,
        "runtime_path": runtime_path,
        "python_executable": python_executable,
    }
    assert kwargs == {
        "check": True,
        "cwd": Path(__file__).resolve().parents[2],
        "capture_output": True,
        "text": True,
    }
