from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from _project.scripts.results_snowflake_warehouse_size_correction import correct
from benchbox.core.results.canonical_json import canonical_json_bytes

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _bundle(platform: str, config_size: str | None, observed: str | None) -> dict[str, object]:
    compute: dict[str, object] = {"source": "observed"}
    if observed:
        compute["warehouse_size"] = observed
    return {
        "version": "2.0",
        "benchmark": {"id": "tpch", "scale_factor": 0.01, "test_type": "power", "name": "TPC-H"},
        "platform": {
            "name": platform,
            "version": "1",
            "config": {"warehouse_size": config_size},
            "raw_config": {"warehouse_size": config_size},
            "compute": compute,
        },
        "run": {"id": "run-1", "timestamp": "2026-05-01T12:00:00Z", "total_duration_ms": 42},
        "summary": {"validation": "passed", "queries": 1, "tpc_metrics": {}},
        "queries": [{"id": "q1", "ms": 42, "status": "SUCCESS", "run_type": "measurement"}],
    }


def _write(directory: Path, name: str, payload: dict[str, object]) -> tuple[Path, Path]:
    bundle = directory / f"{name}.json"
    raw = canonical_json_bytes(payload)
    bundle.write_bytes(raw)
    sidecar = directory / f"{name}.manifest.json"
    sidecar.write_bytes(
        canonical_json_bytes({"bundle_file": bundle.name, "bundle_hash": hashlib.sha256(raw).hexdigest()})
    )
    return bundle, sidecar


def test_write_sets_observed_size_and_refreshes_sidecar_hash(tmp_path: Path) -> None:
    bundle, sidecar = _write(tmp_path, "sf", _bundle("Snowflake", "MEDIUM", "X-Small"))
    result = correct(bundles_dir=tmp_path, write=True, record_path=tmp_path / "record.json")

    platform = json.loads(bundle.read_text(encoding="utf-8"))["platform"]
    assert platform["config"]["warehouse_size"] == "X-Small"
    assert platform["raw_config"]["warehouse_size"] == "X-Small"
    assert (
        json.loads(sidecar.read_text(encoding="utf-8"))["bundle_hash"]
        == hashlib.sha256(bundle.read_bytes()).hexdigest()
    )
    assert result["totals"]["corrected"] == 1
    assert result["bundles"][0]["previous_warehouse_size"] == {"config": "MEDIUM", "raw_config": "MEDIUM"}
    assert result["bundles"][0]["old_result_id"] != result["bundles"][0]["new_result_id"]
    assert (tmp_path / "record.json").is_file()


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    bundle, _ = _write(tmp_path, "sf", _bundle("Snowflake", "MEDIUM", "X-Small"))
    before = bundle.read_bytes()
    result = correct(bundles_dir=tmp_path, write=False, record_path=tmp_path / "record.json")

    assert result["totals"]["corrected"] == 1
    assert bundle.read_bytes() == before
    assert not (tmp_path / "record.json").exists()


def test_matching_unobserved_and_other_platform_bundles_are_untouched(tmp_path: Path) -> None:
    _write(tmp_path, "match", _bundle("Snowflake", "X-Small", "X-Small"))
    _write(tmp_path, "unobserved", _bundle("Snowflake", "MEDIUM", None))
    _write(tmp_path, "other", _bundle("Databricks", "MEDIUM", "2X-Small"))

    assert correct(bundles_dir=tmp_path, write=False, record_path=tmp_path / "record.json")["totals"]["corrected"] == 0
