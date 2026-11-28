from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from benchbox.core.execution_engine import MIXED, NOT_CAPTURED
from benchbox.platforms import get_adapter
from benchbox.utils.runtime_env import DriverResolution

pytestmark = [pytest.mark.unit, pytest.mark.fast]

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures" / "observed_engine" / "databricks_query_history.json").read_text(
        encoding="utf-8"
    )
)["queries"]


def _info(name: str) -> Any:
    entry = FIXTURE[name]
    metrics = None if entry["metrics"] is None else types.SimpleNamespace(**entry["metrics"])
    return types.SimpleNamespace(query_id=entry["query_id"], metrics=metrics)


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "benchbox.platforms.ensure_driver_version",
        lambda package_name, requested_version, auto_install=False, install_hint=None, allow_install=True: (
            DriverResolution(
                package=package_name or "",
                requested=requested_version,
                resolved="local-dev",
                actual="local-dev",
                auto_install_used=False,
                runtime_strategy="current-process",
                runtime_path=None,
                runtime_python_executable=sys.executable,
            )
        ),
    )
    monkeypatch.setattr("benchbox.platforms.databricks.adapter.check_platform_dependencies", lambda *_: (True, []))
    return get_adapter(
        "databricks",
        server_hostname="dbc-1.cloud.databricks.com",
        http_path="/sql/1.0/warehouses/abc123",
        access_token="dapi-test",
        benchmark="tpch",
        scale_factor=0.01,
    )


def _serve(monkeypatch, adapter, names: list[str]) -> list[list[str]]:
    asked: list[list[str]] = []

    def query_infos(statement_ids, ready):
        asked.append(list(statement_ids))
        infos = {_info(name).query_id: _info(name) for name in names}
        return {key: info for key, info in infos.items() if key in statement_ids and ready(info)}

    monkeypatch.setattr(adapter, "_query_infos", query_infos)
    return asked


def _receipt(adapter) -> dict[str, Any]:
    return adapter.finalize_query_engine_observation(
        {
            "requested": "default",
            "applied": None,
            "applied_class": None,
            "applied_native": {},
            "resolution": "platform_default",
            "observed": NOT_CAPTURED,
            "observed_source": "none",
            "observed_reason": None,
        }
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("photon_run", "photon"),
        ("photon_zero_with_tasks", "standard"),
        ("photon_zero_without_tasks", None),
        ("photon_field_absent", None),
        ("served_from_cache", None),
        ("metrics_not_yet_available", None),
    ],
)
def test_one_query_is_classified_from_its_own_metrics(name, expected):
    from benchbox.platforms.databricks.adapter import DatabricksAdapter

    assert DatabricksAdapter._engine_from_query_info(_info(name)) == expected


def test_run_record_is_photon_when_every_timed_query_reports_photon_time(monkeypatch, adapter):
    asked = _serve(monkeypatch, adapter, ["photon_run"])
    adapter.record_query_engine_observation(None, "select 1", "q1", ["01f0-photon"])

    receipt = _receipt(adapter)

    assert (receipt["observed"], receipt["observed_source"], receipt["observed_reason"]) == (
        "photon",
        "vendor_log",
        None,
    )
    assert asked == [["01f0-photon"]]


def test_all_timed_queries_are_joined_in_one_history_lookup(monkeypatch, adapter):
    asked = _serve(monkeypatch, adapter, ["photon_run", "photon_zero_with_tasks"])
    adapter.record_query_engine_observation(None, "select 1", "q1", ["01f0-photon"])
    adapter.record_query_engine_observation(None, "select 2", "q2", ["01f0-standard"])

    assert _receipt(adapter)["observed"] == MIXED
    assert asked == [["01f0-photon", "01f0-standard"]]


def test_standard_is_recorded_only_from_metrics_that_show_tasks_without_photon_time(monkeypatch, adapter):
    _serve(monkeypatch, adapter, ["photon_zero_with_tasks"])
    adapter.record_query_engine_observation(None, "select 1", "q1", ["01f0-standard"])
    assert _receipt(adapter)["observed"] == "standard"


@pytest.mark.parametrize(
    "name", ["photon_zero_without_tasks", "photon_field_absent", "served_from_cache", "metrics_not_yet_available"]
)
def test_missing_or_inconclusive_metrics_never_imply_standard(monkeypatch, adapter, name):
    _serve(monkeypatch, adapter, [name])
    adapter.record_query_engine_observation(None, "select 1", "q1", [FIXTURE[name]["query_id"]])

    receipt = _receipt(adapter)

    assert (receipt["observed"], receipt["observed_source"]) == (NOT_CAPTURED, "none")
    assert "no engine for 1 of 1 queries" in receipt["observed_reason"]


def test_a_query_missing_from_history_makes_the_run_not_captured(monkeypatch, adapter):
    _serve(monkeypatch, adapter, ["photon_run"])
    adapter.record_query_engine_observation(None, "select 1", "q1", ["01f0-photon"])
    adapter.record_query_engine_observation(None, "select 2", "q2", ["01f0-unknown"])

    assert _receipt(adapter)["observed"] == NOT_CAPTURED


def test_a_failed_history_lookup_is_not_captured_not_standard(monkeypatch, adapter):
    def failing(statement_ids, ready):
        raise PermissionError("forbidden")

    monkeypatch.setattr(adapter, "_query_infos", failing)
    adapter.record_query_engine_observation(None, "select 1", "q1", ["01f0-photon"])

    assert _receipt(adapter)["observed"] == NOT_CAPTURED


def test_a_query_without_a_statement_id_has_no_evidence(monkeypatch, adapter):
    _serve(monkeypatch, adapter, ["photon_run"])
    adapter.record_query_engine_observation(None, "select 1", "q1", [])
    assert _receipt(adapter)["observed"] == NOT_CAPTURED


def test_gateway_routed_runs_declare_why_nothing_is_observed(monkeypatch, adapter):
    _serve(monkeypatch, adapter, ["photon_run"])
    adapter.gateway = "custom"
    adapter.record_query_engine_observation(None, "select 1", "q1", ["01f0-photon"])

    receipt = _receipt(adapter)

    assert receipt["observed"] == NOT_CAPTURED
    assert "gateway" in receipt["observed_reason"]


def test_the_timed_query_execution_records_its_statement_ids(monkeypatch, adapter):
    _serve(monkeypatch, adapter, ["photon_run"])

    class Cursor:
        query_id = "01f0-photon"

        def execute(self, statement):
            return None

        def fetchall(self):
            return [(1,)]

        def close(self):
            return None

    monkeypatch.setattr(adapter, "_ensure_session_cache_disabled", lambda *args, **kwargs: None)
    monkeypatch.setattr(adapter, "timing_fields_for", lambda *args, **kwargs: {})

    result = adapter.execute_query(Cursor(), "select 1", "q1", validate_row_count=False)

    assert result["status"] == "SUCCESS"
    assert _receipt(adapter)["observed"] == "photon"


def test_observation_state_does_not_leak_between_runs(monkeypatch, adapter):
    _serve(monkeypatch, adapter, ["photon_run", "photon_zero_with_tasks"])
    adapter.record_query_engine_observation(None, "select 1", "q1", [FIXTURE["photon_run"]["query_id"]])
    assert _receipt(adapter)["observed"] == "photon"

    adapter._reset_run_scoped_state()
    adapter.open_engine_observation_window()
    adapter.record_query_engine_observation(None, "select 2", "q2", [FIXTURE["photon_zero_with_tasks"]["query_id"]])
    adapter.close_engine_observation_window()

    assert _receipt(adapter)["observed"] == "standard"


def test_two_receipts_after_a_run_look_up_query_history_once(monkeypatch, adapter):
    asked = _serve(monkeypatch, adapter, ["photon_run"])
    adapter.reset_query_engine_observation()
    adapter.open_engine_observation_window()
    adapter.record_query_engine_observation(None, "select 1", "q1", [FIXTURE["photon_run"]["query_id"]])
    adapter.close_engine_observation_window()

    first = _receipt(adapter)
    second = _receipt(adapter)

    assert len(asked) == 1
    assert first == second
    assert first["observed"] == "photon"
