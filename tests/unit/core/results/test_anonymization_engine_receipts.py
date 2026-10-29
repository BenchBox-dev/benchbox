from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchbox.core.results.anonymization import AnonymizationManager
from benchbox.core.results.exporter import ResultExporter
from benchbox.platforms import get_platform_adapter
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]

PRIVATE_HOST = "db.internal.example.com"

DECLARED_ENGINE_NAMES = [
    "auto",
    "in-memory",
    "streaming",
    "duckdb",
    "postgres",
    "threaded",
    "distributed",
    "photon",
    "velox",
    "standard",
]
GATEWAY_ROUTE_NAMES = ["databricks", "snowflake"]
UNDECLARED_NAMES = ["analytics-prod", "warp", "LARGE_WH"]


def _export(tmp_path: Path, result) -> dict:
    exported = ResultExporter(output_dir=tmp_path, anonymize=True).export_result(result, formats=["json"])
    return json.loads(exported["json"].read_text(encoding="utf-8"))


def _anonymize(payload: dict) -> dict:
    return AnonymizationManager().anonymize_result_payload(payload)


@pytest.mark.parametrize("engine", ["duckdb", "postgres"])
def test_pg_duckdb_engine_stays_readable_in_the_public_export(tmp_path: Path, engine: str) -> None:
    adapter = get_platform_adapter("pg-duckdb", execution_engine=engine, host=PRIVATE_HOST)
    result = make_benchmark_results(platform="pg-duckdb", platform_info=adapter.get_platform_info(None))

    platform = _export(tmp_path, result)["platform"]

    assert platform["execution_engine"]["requested"] == engine
    assert platform["execution_engine"]["applied"] == engine
    assert platform["variant"] == f"pg-duckdb+{engine}"
    assert platform["raw_config"]["host"].startswith("host_")
    assert PRIVATE_HOST not in json.dumps(platform)


def test_polars_collect_engine_stays_readable_in_the_public_export(tmp_path: Path) -> None:
    from benchbox.platforms.dataframe.polars_df import PolarsDataFrameAdapter

    adapter = PolarsDataFrameAdapter(execution_engine="streaming", working_dir=tmp_path / "work")
    result = make_benchmark_results(platform="polars-df", platform_info={"configuration": adapter.get_platform_info()})

    receipt = _export(tmp_path / "out", result)["platform"]["execution_engine"]

    assert receipt["requested"] == "streaming"
    assert receipt["applied_native"]["collect_kwargs"]["engine"] == "streaming"


@pytest.mark.parametrize("engine", [*DECLARED_ENGINE_NAMES, *GATEWAY_ROUTE_NAMES, "default"])
def test_declared_engine_names_stay_readable_under_engine_keys_in_receipts(engine: str) -> None:
    payload = {
        "platform": {
            "execution_engine": {"applied_native": {"engine": engine, "session": {"engine": engine}}},
            "raw_config": {"execution_engine": {"applied_native": {"collect_kwargs": {"engine": engine}}}},
        }
    }

    platform = _anonymize(payload)["platform"]

    assert platform["execution_engine"]["applied_native"]["engine"] == engine
    assert platform["execution_engine"]["applied_native"]["session"]["engine"] == engine
    assert platform["raw_config"]["execution_engine"]["applied_native"]["collect_kwargs"]["engine"] == engine


@pytest.mark.parametrize("value", UNDECLARED_NAMES)
def test_undeclared_values_in_receipts_stay_hashed(value: str) -> None:
    payload = {"platform": {"execution_engine": {"applied_native": {"engine": value}}}}

    native = _anonymize(payload)["platform"]["execution_engine"]["applied_native"]

    assert native["engine"].startswith("engine_")


@pytest.mark.parametrize(
    "payload",
    [
        {"config": {"platform_options": {"engine": "duckdb"}}},
        {"config": {"execution_engine": {"applied_native": {"engine": "duckdb"}}}},
        {"platform": {"options": {"execution_engine": {"applied_native": {"engine": "duckdb"}}}}},
        {"platform": {"execution_engine": {"applied_native": {"engine_name": "duckdb"}}}},
    ],
    ids=["platform-option", "config-receipt", "nested-copy", "engine-name-key"],
)
def test_engine_names_outside_receipt_engine_keys_stay_hashed(payload: dict) -> None:
    anonymized = json.dumps(_anonymize(payload))

    assert '"duckdb"' not in anonymized
    assert "engine_" in anonymized


def test_identifiers_inside_a_receipt_are_still_redacted() -> None:
    payload = {
        "platform": {
            "execution_engine": {
                "applied_native": {"database": "postgres", "host": PRIVATE_HOST, "password": "SENTINEL"},
            }
        }
    }

    native = _anonymize(payload)["platform"]["execution_engine"]["applied_native"]

    assert native["database"].startswith("database_")
    assert native["host"].startswith("host_")
    assert "SENTINEL" not in json.dumps(native)


def test_translation_runtime_identity_survives_anonymisation(tmp_path: Path) -> None:
    from benchbox.core.runner.runner import _attach_translation_metadata
    from benchbox.utils.dialect_utils import SqlTranslationOutcome, bundled_sqlglot_runtime_identity

    outcome = SqlTranslationOutcome(
        source_dialect="duckdb",
        target_dialect="sqlite",
        normalized_source_dialect="duckdb",
        normalized_target_dialect="sqlite",
        translator="sqlglot",
        status="success",
        strict_mode=False,
    )
    result = _attach_translation_metadata(make_benchmark_results(execution_metadata={}), [outcome], strict_mode=False)

    runtime = _export(tmp_path, result)["execution"]["translation"]["runtime"]

    assert runtime == bundled_sqlglot_runtime_identity()
