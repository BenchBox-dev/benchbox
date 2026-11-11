from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.core.dataframe.tuning.interface import DataFrameTuningConfiguration
from benchbox.core.results.schema import (
    build_applied_ledger_payload,
    build_result_payload,
    build_tuning_payload,
)
from benchbox.core.schemas import BenchmarkConfig
from benchbox.platforms.dataframe.benchmark_mixin import (
    DataFramePhases,
    DataFrameRunOptions,
)
from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _run(adapter, name: str = "tpch"):
    benchmark = SimpleNamespace(name=name, display_name=name.upper(), scale_factor=1.0, tables={})
    config = BenchmarkConfig(name=name, display_name=name.upper(), scale_factor=1.0)
    return adapter.run_benchmark(
        benchmark,
        benchmark_config=config,
        phases=DataFramePhases(load=False, execute=False),
        options=DataFrameRunOptions(ignore_memory_warnings=True, prefer_parquet=False),
    )


@pytest.fixture(autouse=True)
def _polars_environment(_hermetic_state, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.utilities.session_isolation import own_environment

    own_environment(monkeypatch, ["POLARS_MAX_THREADS"])


def _tuned_polars() -> PolarsDataFrameAdapter:
    cfg = DataFrameTuningConfiguration()
    cfg.parallelism.thread_count = 4
    cfg.execution.streaming_mode = True
    return PolarsDataFrameAdapter(tuning_config=cfg)


def test_tuned_df_result_carries_applied_ledger_hash_in_platform_tuning_summary():
    result = _run(_tuned_polars())
    payload = build_result_payload(result)

    tuning = payload["platform"]["tuning"]

    assert tuning["applied_ledger_hash"] == result.applied_ledger_hash
    assert tuning["applied_ledger_hash"] is not None

    assert build_tuning_payload(result)["validation_status"] == "applied_unverified"


def test_tuned_df_result_emits_applied_json_companion():
    result = _run(_tuned_polars())
    companion = build_applied_ledger_payload(result)

    assert companion is not None
    assert companion["status"] == "applied_unverified"
    assert companion["applied_ledger_hash"] == result.applied_ledger_hash
    recorded = {s["statement"] for s in companion["statements"]}
    assert "POLARS_MAX_THREADS=4" in recorded

    assert all(s.get("mechanism") == "dataframe_runtime" for s in companion["statements"])


def test_default_df_result_emits_no_applied_companion_and_not_applicable_status():
    result = _run(PolarsDataFrameAdapter())

    assert result.tuning_validation_status == "not_applicable"
    assert build_applied_ledger_payload(result) is None

    payload = build_result_payload(result)
    assert payload["platform"]["tuning"] == {"validation_status": "not_applicable"}


def test_all_default_config_is_reported_like_no_config():
    result = _run(PolarsDataFrameAdapter(tuning_config=DataFrameTuningConfiguration()))

    assert result.tuning_validation_status == "not_applicable"
    assert build_result_payload(result)["platform"]["tuning"] == {"validation_status": "not_applicable"}


def test_requested_tuning_that_applied_nothing_reports_noop():
    cfg = DataFrameTuningConfiguration()
    cfg.gpu.enabled = True
    adapter = PolarsDataFrameAdapter(tuning_config=cfg)
    assert not cfg.is_default()
    assert not adapter._applied_tuning_ledger.statements

    result = _run(adapter)

    assert result.tuning_validation_status == "noop"
    assert build_applied_ledger_payload(result) is None
    assert build_result_payload(result)["platform"]["tuning"]["validation_status"] == "noop"


