from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "_project/scripts/migrate_clickhouse_labels.py"
SPEC = importlib.util.spec_from_file_location("migrate_clickhouse_labels", SCRIPT)
assert SPEC and SPEC.loader
migrate_clickhouse_labels = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = migrate_clickhouse_labels
SPEC.loader.exec_module(migrate_clickhouse_labels)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _write_bundle(directory: Path, stem: str, platform_name: str) -> Path:
    result = directory / f"{stem}.json"
    result.write_text(json.dumps({"platform": {"name": platform_name}, "benchmark": {"id": "tpch"}}))
    (directory / f"{stem}.manifest.json").write_text(json.dumps({"bundle_file": result.name, "bundle_hash": "stale"}))
    return result


def test_discovers_bare_label_and_bare_slug(tmp_path: Path) -> None:
    _write_bundle(tmp_path, "tpch_sf1_clickhouse_sql_20200101_000000_abc123", "clickhouse")
    _write_bundle(tmp_path, "tpch_sf1_duckdb_sql_20200101_000000_def456", "DuckDB")

    hits, _ = migrate_clickhouse_labels.discover_hits(tmp_path)

    assert [hit.result.name for hit in hits] == ["tpch_sf1_clickhouse_sql_20200101_000000_abc123.json"]


def test_first_class_slugs_are_not_hits(tmp_path: Path) -> None:
    for slug, label in [
        ("clickhouse_local", "ClickHouse Local"),
        ("clickhouse_server", "ClickHouse Server"),
        ("clickhouse_cloud", "ClickHouse Cloud"),
    ]:
        _write_bundle(tmp_path, f"tpch_sf1_{slug}_sql_20200101_000000_abc123", label)

    hits, _ = migrate_clickhouse_labels.discover_hits(tmp_path)

    assert hits == []


def test_migrate_rewrites_label_slug_and_sidecar(tmp_path: Path) -> None:
    old = _write_bundle(tmp_path, "tpch_sf1_clickhouse_sql_20200101_000000_abc123", "ClickHouse")

    (hits, _) = migrate_clickhouse_labels.discover_hits(tmp_path)
    new_result, new_manifest = migrate_clickhouse_labels.migrate_hit(hits[0], "clickhouse-local")

    assert new_result.name == "tpch_sf1_clickhouse_local_sql_20200101_000000_abc123.json"
    assert not old.exists()
    payload = json.loads(new_result.read_text(encoding="utf-8"))
    assert payload["platform"]["name"] == "ClickHouse Local"
    sidecar = json.loads(new_manifest.read_text(encoding="utf-8"))
    assert sidecar["bundle_file"] == new_result.name
    assert sidecar["bundle_hash"] == hashlib.sha256(new_result.read_bytes()).hexdigest()


def test_checked_in_corpus_carries_no_bare_labels() -> None:
    bundle_dir = REPO_ROOT / "results-data/bundles"
    if not bundle_dir.is_dir():
        pytest.skip("no checked-in corpus in this checkout")

    hits, _ = migrate_clickhouse_labels.discover_hits(bundle_dir)

    assert hits == []


def test_discovers_nested_bundles(tmp_path: Path) -> None:
    nested = tmp_path / "tpch" / "duckdb"
    nested.mkdir(parents=True)
    result = nested / "tpch_sf1_clickhouse_sql_20200101_000000_abc123.json"
    result.write_text(json.dumps({"platform": {"name": "clickhouse"}}))

    hits, _ = migrate_clickhouse_labels.discover_hits(tmp_path)

    assert [hit.result for hit in hits] == [result]


def test_migrate_refuses_to_overwrite_existing_bundle(tmp_path: Path) -> None:
    import pytest

    old = _write_bundle(tmp_path, "tpch_sf1_clickhouse_sql_20200101_000000_abc123", "clickhouse")
    clash = tmp_path / "tpch_sf1_clickhouse_local_sql_20200101_000000_abc123.json"
    clash.write_text(json.dumps({"platform": {"name": "ClickHouse Local"}}))

    (hits, _) = migrate_clickhouse_labels.discover_hits(tmp_path)
    (hit,) = [hit for hit in hits if hit.result == old]

    with pytest.raises(FileExistsError):
        migrate_clickhouse_labels.migrate_hit(hit, "clickhouse-local")

    assert old.exists()
    assert json.loads(old.read_text(encoding="utf-8"))["platform"]["name"] == "clickhouse"


def test_conflicting_slug_and_first_class_label_is_skipped(tmp_path: Path) -> None:
    result = tmp_path / "tpch_sf1_clickhouse_sql_20200101_000000_abc123.json"
    result.write_text(json.dumps({"platform": {"name": "ClickHouse Cloud"}}))

    hits, anomalies = migrate_clickhouse_labels.discover_hits(tmp_path)

    assert hits == []
    assert any("conflicting bundle" in anomaly for anomaly in anomalies)


def test_malformed_sidecar_aborts_without_writing(tmp_path: Path) -> None:
    import pytest

    old = _write_bundle(tmp_path, "tpch_sf1_clickhouse_sql_20200101_000000_abc123", "clickhouse")
    (tmp_path / "tpch_sf1_clickhouse_sql_20200101_000000_abc123.manifest.json").write_text("not json{")

    (hits, _) = migrate_clickhouse_labels.discover_hits(tmp_path)

    with pytest.raises(ValueError):
        migrate_clickhouse_labels.migrate_hit(hits[0], "clickhouse-local")

    assert old.exists()
    assert json.loads(old.read_text(encoding="utf-8"))["platform"]["name"] == "clickhouse"


def test_non_dict_sidecar_aborts_without_writing(tmp_path: Path) -> None:

    import pytest

    old = _write_bundle(tmp_path, "tpch_sf1_clickhouse_sql_20200101_000000_abc123", "clickhouse")
    (tmp_path / "tpch_sf1_clickhouse_sql_20200101_000000_abc123.manifest.json").write_text("[1, 2]")

    (hits, _) = migrate_clickhouse_labels.discover_hits(tmp_path)

    with pytest.raises(ValueError, match="malformed manifest sidecar"):
        migrate_clickhouse_labels.migrate_hit(hits[0], "clickhouse-local")

    assert old.exists()


def test_companions_are_not_discovered_as_bundles(tmp_path: Path) -> None:
    old = _write_bundle(tmp_path, "tpch_sf1_clickhouse_sql_20200101_000000_abc123", "clickhouse")
    (tmp_path / "tpch_sf1_clickhouse_sql_20200101_000000_abc123.plans.json").write_text(json.dumps({"plans": []}))

    (hits, anomalies) = migrate_clickhouse_labels.discover_hits(tmp_path)

    assert [hit.result for hit in hits] == [old]
    assert not any("plans.json" in anomaly for anomaly in anomalies)


def test_migrate_carries_companions_and_leaves_no_temp_files(tmp_path: Path) -> None:
    old = _write_bundle(tmp_path, "tpch_sf1_clickhouse_sql_20200101_000000_abc123", "clickhouse")
    companion = tmp_path / "tpch_sf1_clickhouse_sql_20200101_000000_abc123.plans.json"
    companion.write_bytes(b'{"plans": []}')

    (hits, _) = migrate_clickhouse_labels.discover_hits(tmp_path)
    new_result, _ = migrate_clickhouse_labels.migrate_hit(hits[0], "clickhouse-local")

    new_companion = tmp_path / "tpch_sf1_clickhouse_local_sql_20200101_000000_abc123.plans.json"
    assert new_companion.read_bytes() == b'{"plans": []}'
    assert not companion.exists()
    assert not old.exists()
    assert list(tmp_path.glob(".*.tmp-*")) == []
    assert new_result.name == "tpch_sf1_clickhouse_local_sql_20200101_000000_abc123.json"


def test_label_only_rewrite_is_atomic_and_keeps_companions(tmp_path: Path) -> None:

    stem = "tpch_sf1_duckdb_sql_20200101_000000_abc123"
    old = _write_bundle(tmp_path, stem, "clickhouse")
    companion = tmp_path / f"{stem}.tuning.json"
    companion.write_bytes(b"{}")

    (hits, _) = migrate_clickhouse_labels.discover_hits(tmp_path)
    new_result, _ = migrate_clickhouse_labels.migrate_hit(hits[0], "clickhouse-server")

    assert new_result == old
    assert json.loads(old.read_text(encoding="utf-8"))["platform"]["name"] == "ClickHouse Server"
    assert companion.is_file()
    assert list(tmp_path.glob(".*.tmp-*")) == []


