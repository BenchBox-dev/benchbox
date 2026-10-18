# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from benchbox.core.benchmark_loader import get_benchmark_instance
from benchbox.core.config import BenchmarkConfig
from benchbox.core.publishing.admission import publish_admission
from benchbox.core.results.loader import load_result_file, reconstruct_benchmark_results
from benchbox.core.results.query_normalizer import normalize_query_result
from benchbox.core.results.result_factory import build_enhanced_benchmark_result
from benchbox.core.results.schema import build_result_payload

pytestmark = [pytest.mark.integration, pytest.mark.medium]

OFFICIAL_SCALE = 1.0
PUBLISH_LABEL = "maintainer-run"


def _clean_query_results() -> list[dict[str, Any]]:
    return [{"query_id": "1", "execution_time": 0.01, "success": True, "row_count": 1}]


def _bundle_payload(*, official: bool, scale_factor: float = OFFICIAL_SCALE) -> dict[str, Any]:
    config = BenchmarkConfig(
        name="tpcds",
        display_name="TPC-DS",
        scale_factor=scale_factor,
        official=official,
    )
    benchmark = get_benchmark_instance(config, None)
    result = build_enhanced_benchmark_result(
        benchmark=benchmark,
        platform="duckdb",
        query_results=_clean_query_results(),
        validation_status="PASSED",
    )
    return build_result_payload(result)


def test_official_run_emits_official_compliance_class() -> None:
    payload = _bundle_payload(official=True)

    assert payload["benchmark"]["compliance_class"] == "official"


def test_official_bundle_survives_a_json_round_trip_and_passes_submit(tmp_path: Path) -> None:
    payload = _bundle_payload(official=True)
    bundle = tmp_path / "tpcds_sf1_duckdb_official.json"
    bundle.write_text(json.dumps(payload, default=str), encoding="utf-8")

    loaded, _raw = load_result_file(bundle)

    assert loaded.compliance_class == "official"
    decision = publish_admission(loaded, PUBLISH_LABEL)
    assert decision.allowed, f"official TPC-DS refused: {decision.code} ({decision.reason})"


@pytest.mark.parametrize(
    ("official", "scale_factor", "expected_class"),
    [
        (False, OFFICIAL_SCALE, "unofficial_nonstandard"),
        (True, 0.5, "unofficial_subscale"),
        (True, 2.0, "unofficial_nonstandard"),
    ],
)
def test_unofficial_runs_are_refused_for_compliance(official: bool, scale_factor: float, expected_class: str) -> None:
    payload = _bundle_payload(official=official, scale_factor=scale_factor)
    loaded = reconstruct_benchmark_results(payload)

    assert loaded.compliance_class == expected_class
    decision = publish_admission(loaded, PUBLISH_LABEL)
    assert not decision.allowed
    assert decision.code == "unofficial_compliance"


def _dataframe_platform_input():
    from benchbox.core.results.result_factory import _build_platform_input

    return _build_platform_input("polars", {"execution_mode": "dataframe"}, {})


def _dataframe_compliance(*, official: bool, scale_factor: float = OFFICIAL_SCALE) -> str | None:
    from benchbox.platforms.dataframe.benchmark_mixin import dataframe_compliance_class

    config = BenchmarkConfig(
        name="tpcds",
        display_name="TPC-DS",
        scale_factor=scale_factor,
        official=official,
    )
    return dataframe_compliance_class(get_benchmark_instance(config, None), config)


@pytest.mark.parametrize(
    ("official", "scale_factor", "expected_class"),
    [
        (True, OFFICIAL_SCALE, "official"),
        (False, OFFICIAL_SCALE, "unofficial_nonstandard"),
        (True, 0.5, "unofficial_subscale"),
        (True, 2.0, "unofficial_nonstandard"),
    ],
)
def test_the_dataframe_path_classifies_identically_to_the_sql_path(
    official: bool, scale_factor: float, expected_class: str
) -> None:
    assert _dataframe_compliance(official=official, scale_factor=scale_factor) == expected_class


def test_the_dataframe_value_is_the_plain_wire_string_not_an_enum_repr() -> None:
    value = _dataframe_compliance(official=True)

    assert value == "official"
    assert "TpcdsComplianceClass" not in str(value)


def test_a_dataframe_official_bundle_is_not_refused_for_compliance(tmp_path: Path) -> None:
    from benchbox.core.results.builder import BenchmarkInfoInput, ResultBuilder

    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(
            name="TPC-DS",
            scale_factor=OFFICIAL_SCALE,
            test_type="power",
            benchmark_id="tpcds",
            display_name="TPC-DS",
            compliance_class=_dataframe_compliance(official=True),
        ),
        platform=_dataframe_platform_input(),
    )
    builder.mark_started()
    builder.set_validation_status("NOT_RUN")
    for query_result in _clean_query_results():
        builder.add_query_result(normalize_query_result(query_result))
    builder.mark_completed()

    bundle = tmp_path / "tpcds_sf1_polars_df_official.json"
    bundle.write_text(json.dumps(build_result_payload(builder.build()), default=str), encoding="utf-8")
    loaded, _raw = load_result_file(bundle)

    assert loaded.compliance_class == "official"
    decision = publish_admission(loaded, PUBLISH_LABEL)
    assert not decision.allowed
    assert decision.code == "non_clean"
    assert decision.reason == "validation_status=not_run"
