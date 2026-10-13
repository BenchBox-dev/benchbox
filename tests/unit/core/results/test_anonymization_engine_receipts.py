from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchbox.core.platform_manifest import PLATFORM_MANIFEST
from benchbox.core.results.anonymization import AnonymizationManager
from benchbox.core.results.exporter import ResultExporter
from benchbox.platforms import get_platform_adapter
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]

PRIVATE_HOST = "db.internal.example.com"

DECLARED_ENGINES = sorted(
    {(entry.key, name) for entry in PLATFORM_MANIFEST for name in entry.capabilities.get("execution_engines", {})}
)


def _export(tmp_path: Path, result) -> dict:
    exported = ResultExporter(output_dir=tmp_path, anonymize=True).export_result(result, formats=["json"])
    return json.loads(exported["json"].read_text(encoding="utf-8"))


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


@pytest.mark.parametrize(("platform", "engine"), DECLARED_ENGINES)
def test_every_declared_engine_name_survives_inside_a_native_receipt(platform: str, engine: str) -> None:
    payload = {
        "platform": {
            "name": platform,
            "execution_engine": {"requested": engine, "applied_native": {"engine": engine}},
        },
        "config": {"platform_options": {"engine": engine, "compute_resource": "analytics-prod"}},
    }

    anonymized = AnonymizationManager().anonymize_result_payload(payload)

    assert anonymized["platform"]["execution_engine"]["applied_native"]["engine"] == engine
    assert anonymized["config"]["platform_options"]["engine"].startswith("engine_")
    assert "analytics-prod" not in json.dumps(anonymized)


def test_compute_names_inside_a_native_receipt_stay_hashed() -> None:
    payload = {
        "platform": {
            "execution_engine": {"applied_native": {"engine": "analytics-prod", "engine_name": "prod-cluster"}},
        }
    }

    native = AnonymizationManager().anonymize_result_payload(payload)["platform"]["execution_engine"]["applied_native"]

    assert native["engine"].startswith("engine_")
    assert native["engine_name"].startswith("engine_")
