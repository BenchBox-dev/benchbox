from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import duckdb
import pytest

from _project.scripts.explorer_pipeline.pipeline import ExplorerPipeline
from tests.unit.scripts.explorer_pipeline.conftest import MINIMAL_BUNDLE

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_BUNDLES = Path(__file__).resolve().parents[4] / "results-data" / "bundles"
SCALE_CORRECTION = "Generated before the TPC-DI scale correction; the data is about ten times the specification's SF1."
SIX_TPCDI_SF1_SIDECARS = [
    "tpcdi_sf1_bigquery_sql_20260920_064220_bc5850d1",
    "tpcdi_sf1_clickhouse_local_sql_20261028_140913_30a5c9d1",
    "tpcdi_sf1_databricks_sql_20261003_201821_dbfb95fb",
    "tpcdi_sf1_datafusion_sql_20261028_100615_ffe633e9",
    "tpcdi_sf1_duckdb_sql_20260826_162542_1eb31887",
    "tpcdi_sf1_snowflake_sql_20261003_134837_b45dfad3",
]


def _bundle(name: str, *, environment: dict, compute: dict | None = None, platform: str = "duckdb") -> dict:
    payload = copy.deepcopy(MINIMAL_BUNDLE)
    payload["run"]["id"] = name
    payload["platform"] = {"name": platform, "version": "1.0", **({"compute": compute} if compute else {})}
    payload["environment"] = environment
    return payload


def _write(directory: Path, name: str, payload: dict, sidecar: dict | None = None) -> None:
    (directory / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    if sidecar is not None:
        (directory / f"{name}.manifest.json").write_text(json.dumps(sidecar), encoding="utf-8")


def _corpus(tmp_path: Path) -> Path:
    bundles = tmp_path / "data" / "bundles"
    bundles.mkdir(parents=True)
    linux = {"os": "Linux 6.8", "arch": "x86_64", "cpu_count": 4, "memory_gb": 16.0, "python": "3.12.0"}
    darwin = {"os": "Darwin 25.0", "arch": "arm64", "memory_gb": 16.0, "python": "3.12.0"}
    m4 = _bundle("m4", environment={**darwin, "cpu_model": "Apple M4", "cpu_count": 10})
    m4["export"] = {"benchbox_version": "0.5.1"}
    _write(bundles, "m4", m4)
    _write(
        bundles,
        "epyc7763",
        _bundle("epyc7763", environment={**linux, "cpu_model": "AMD EPYC 7763 64-Core Processor"}),
    )
    _write(
        bundles,
        "epyc9v74",
        _bundle("epyc9v74", environment={**linux, "cpu_model": "AMD EPYC 9V74 80-Core Processor"}),
    )
    _write(bundles, "nomodel", _bundle("nomodel", environment=dict(darwin)))
    attestation = {
        "attested_by": "maintainer",
        "cpu_identity_provenance": "user_attested",
        "cpu_model": "Apple M4",
        "cpu_vendor": "Apple",
    }
    _write(
        bundles,
        "attested",
        _bundle("attested", environment=dict(darwin)),
        {"bundle_file": "attested.json", "bundle_hash": "h", "cpu_attestation": attestation},
    )
    _write(
        bundles,
        "warehouse",
        _bundle(
            "warehouse",
            platform="snowflake",
            compute={"warehouse_size": "X-Small"},
            environment={
                **darwin,
                "cpu_model": "Apple M4",
                "cpu_count": 10,
                "platform_runtime": {"runtime_type": "managed_cloud", "source": "observed"},
            },
        ),
    )
    return tmp_path / "data"


def test_results_carry_the_hardware_class_per_bundle(tmp_path: Path) -> None:
    out = tmp_path / "out"
    ExplorerPipeline().run(_corpus(tmp_path), out)

    with duckdb.connect(str(out / "results.duckdb"), read_only=True) as con:
        rows = con.execute(
            "SELECT platform, hardware_class, hardware_basis, hardware_table_version, service_size,"
            " cpu_model, cpu_identity_provenance FROM result_detail_metrics ORDER BY result_id"
        ).fetchall()
        version = con.execute("SELECT read_model_version FROM metadata").fetchone()
        versions = con.execute("SELECT benchbox_version, benchbox_version_basis FROM results").fetchall()

    assert version == (19,)
    assert sorted(versions) == [("0.1.5", "inferred_from_run_date")] * 5 + [("0.5.1", "recorded")]
    by_class: dict[str, list[tuple]] = {}
    for row in rows:
        by_class.setdefault(row[1], []).append(row)
    assert sorted(by_class) == ["T1", "T2", "managed", "unclassified"]
    assert len(by_class["T1"]) == 2
    assert {row[3] for row in rows} == {"2026-11-17.1"}
    assert len(by_class["unclassified"]) == 1
    assert by_class["unclassified"][0][2] == "cpu_model_missing"
    managed = by_class["managed"][0]
    assert (managed[0], managed[4]) == ("snowflake", "X-Small")
    t2_models = [(row[5], row[6]) for row in by_class["T2"]]
    assert ("Apple M4", "user_attested") in t2_models
    assert ("Apple M4", None) in t2_models


def test_two_cpu_models_in_one_tier_share_a_group_and_a_faster_tier_splits(tmp_path: Path) -> None:
    out = tmp_path / "out"
    ExplorerPipeline().run(_corpus(tmp_path), out)

    with duckdb.connect(str(out / "results.duckdb"), read_only=True) as con:
        groups = dict(
            con.execute(
                "SELECT hardware_class, count(DISTINCT cpu_model) FROM result_detail_metrics"
                " WHERE hardware_class IN ('T1', 'T2') GROUP BY 1"
            ).fetchall()
        )

    assert groups == {"T1": 2, "T2": 1}


def test_published_sidecars_carry_the_known_defect_reason_into_the_read_model(tmp_path: Path) -> None:
    bundles = tmp_path / "data" / "bundles"
    bundles.mkdir(parents=True)
    for stem in SIX_TPCDI_SF1_SIDECARS:
        for suffix in (".json", ".manifest.json"):
            shutil.copy(REPO_BUNDLES / f"{stem}{suffix}", bundles / f"{stem}{suffix}")
    out = tmp_path / "out"

    ExplorerPipeline().run(tmp_path / "data", out)

    with duckdb.connect(str(out / "results.duckdb"), read_only=True) as con:
        results = con.execute("SELECT known_defect_reason, ranking_exclusion_reason FROM results").fetchall()
        detail = con.execute("SELECT DISTINCT known_defect_reason FROM result_detail_metrics").fetchall()

    assert len(results) == 6
    assert {row[0] for row in results} == {SCALE_CORRECTION}
    assert {row[1] for row in results} == {"known_defective_data"}
    assert detail == [(SCALE_CORRECTION,)]


def test_an_earlier_read_model_still_builds_without_the_new_columns(tmp_path: Path) -> None:
    out = tmp_path / "out"
    ExplorerPipeline().run(_corpus(tmp_path), out, read_model_version=18)

    with duckdb.connect(str(out / "results.duckdb"), read_only=True) as con:
        columns = {row[0] for row in con.execute("DESCRIBE result_detail_metrics").fetchall()}
        assert con.execute("SELECT read_model_version FROM metadata").fetchone() == (18,)

    assert not columns & {"hardware_class", "known_defect_reason", "service_size"}
