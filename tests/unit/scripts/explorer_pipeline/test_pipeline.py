from __future__ import annotations

import hashlib
import importlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any

import duckdb
import pytest

from _project.scripts.explorer_pipeline import pipeline as pipeline_module
from _project.scripts.explorer_pipeline.models import DetailResult
from _project.scripts.explorer_pipeline.pipeline import (
    COMMUNITY_TRUST_LABEL,
    SUBMISSION_MANIFEST_FILENAME,
    ExplorerPipeline,
    _build_benchmark_summaries,
    _build_short_ids,
)
from _project.scripts.explorer_pipeline.transformer import BundleTransformer
from _project.scripts.results_explorer_snapshot_invariants import check_snapshot
from benchbox.core.results.anonymization import AnonymizationManager
from benchbox.core.results.canonical_json import canonical_json_bytes
from benchbox.validation.bundle import COMPANION_SUFFIXES
from tests.unit.scripts.explorer_pipeline.conftest import MINIMAL_BUNDLE

pytestmark = [pytest.mark.unit, pytest.mark.medium]


class TestBuildShortIds:
    def test_empty_input_returns_empty_dict(self) -> None:
        assert _build_short_ids([]) == {}

    def test_single_id_returns_8_char_prefix(self) -> None:
        result = _build_short_ids(["tpch-duckdb-sf0.1-20260315-abcdef01"])
        assert len(result) == 1
        short_id = next(iter(result))
        assert len(short_id) == 8

    def test_short_id_is_sha256_prefix(self) -> None:
        rid = "tpch-duckdb-sf0.1-20260315-abcdef01"
        result = _build_short_ids([rid])
        expected_prefix = hashlib.sha256(rid.encode()).hexdigest()[:8]
        assert expected_prefix in result
        assert result[expected_prefix] == rid

    def test_all_values_map_back_to_input_ids(self) -> None:
        rids = [
            "tpch-duckdb-sf0.1-20260315-aaaa0001",
            "tpch-sqlite-sf0.1-20260315-bbbb0002",
            "tpch-polars-sf0.1-20260315-cccc0003",
        ]
        result = _build_short_ids(rids)
        assert set(result.values()) == set(rids)

    def test_no_duplicate_short_ids(self) -> None:
        rids = [f"tpch-platform{i}-sf0.1-20260315-{i:08x}" for i in range(20)]
        result = _build_short_ids(rids)
        assert len(result) == len(rids), "Every input must get a unique short ID"

    def test_collision_extends_to_longer_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rid_a = "tpch-duckdb-sf1-20260101-aaa"
        rid_b = "tpch-sqlite-sf1-20260101-bbb"
        real_sha256 = hashlib.sha256

        digest_a = real_sha256(rid_a.encode()).hexdigest()
        digest_b = real_sha256(rid_b.encode()).hexdigest()

        shared_prefix = "deadbeef"
        fake_digests = {
            rid_a.encode(): shared_prefix + digest_a[8:],
            rid_b.encode(): shared_prefix + digest_b[8:],
        }

        class _FakeHash:
            def __init__(self, data: bytes) -> None:
                self._data = data

            def hexdigest(self) -> str:
                return fake_digests.get(self._data, real_sha256(self._data).hexdigest())

        monkeypatch.setattr(hashlib, "sha256", _FakeHash)

        result = _build_short_ids([rid_a, rid_b])
        assert len(result) == 2
        for short_id in result:
            assert len(short_id) >= 8
        assert all(len(k) > 8 for k in result)


def test_mismatched_query_sets_are_not_ranked(tmp_path: Path) -> None:
    transformer = BundleTransformer()
    source_paths = [
        Path("results-data/bundles/tpchavoc_sf001_clickhouse_local_sql_20260826_171608_ce266327.json"),
        Path("results-data/bundles/tpchavoc_sf001_datafusion_sql_20260826_165844_721d2f5e.json"),
        Path("results-data/bundles/tpchavoc_sf001_duckdb_sql_20260826_163147_d96baca2.json"),
    ]
    data_dir = tmp_path / "input"
    bundle_dir = data_dir / "bundles"
    bundle_dir.mkdir(parents=True)
    bundle_paths = []
    for path in source_paths:
        shutil.copy2(path, bundle_dir / path.name)
        bundle_paths.append(bundle_dir / path.name)
    pairs = []
    for path in bundle_paths:
        entry = transformer.to_manifest_entry(path)
        pairs.append((entry, transformer.to_detail_result(path, entry.result_id)))
    full_to_short = {entry.result_id: entry.result_id[-8:] for entry, _ in pairs}

    summaries = _build_benchmark_summaries({("tpchavoc", 0.01, "power", None): pairs}, full_to_short)

    rows = summaries[0][1].platforms
    assert {len({query_id for query_id, value in row.timings.items() if value is not None}) for row in rows} == {
        197,
        206,
        220,
    }
    assert all(not row.is_ranking_eligible for row in rows)
    assert {row.ranking_exclusion_reason for row in rows} == {"mismatched_query_set"}

    output = tmp_path / "output"
    ExplorerPipeline().run(data_dir, output)

    with duckdb.connect(str(output / "results.duckdb"), read_only=True) as con:
        ranking_reasons = con.execute("SELECT DISTINCT ranking_exclusion_reason FROM benchmark_rankings").fetchall()
        detail_reasons = con.execute("SELECT DISTINCT ranking_exclusion_reason FROM result_detail_metrics").fetchall()
        eligibility = con.execute("SELECT DISTINCT is_ranking_eligible FROM results").fetchall()
    assert ranking_reasons == [("mismatched_query_set",)]
    assert detail_reasons == [("mismatched_query_set",)]
    assert eligibility == [(False,)]


def test_non_rankable_query_gap_does_not_exclude_rankable_peers(tmp_path: Path) -> None:
    transformer = BundleTransformer()
    source = Path("results-data/bundles/tpchavoc_sf001_duckdb_sql_20260826_163147_d96baca2.json")
    path = tmp_path / source.name
    shutil.copy2(source, path)
    entry = transformer.to_manifest_entry(path)
    detail = transformer.to_detail_result(path, entry.result_id)
    peer_entry = entry.model_copy(update={"result_id": "eligible-peer", "platform_id": "eligible-peer"})
    peer_detail = detail.model_copy(update={"result_id": "eligible-peer"})
    incomplete_entry = entry.model_copy(
        update={
            "result_id": "missing-metric",
            "platform_id": "missing-metric",
            "power_score": None,
            "display_geomean_ms": None,
            "ranking_exclusion_reason": "missing_primary_metric",
        }
    )
    incomplete_detail = detail.model_copy(
        update={"result_id": "missing-metric", "display_timings": detail.display_timings[:-1]}
    )
    pairs = [(entry, detail), (peer_entry, peer_detail), (incomplete_entry, incomplete_detail)]

    summaries = _build_benchmark_summaries(
        {("tpchavoc", 0.01, "power", None): pairs},
        {candidate.result_id: candidate.result_id[-8:] for candidate, _ in pairs},
    )

    rows = {row.result_id: row for row in summaries[0][1].platforms}
    assert rows[entry.result_id].is_ranking_eligible is True
    assert rows[peer_entry.result_id].is_ranking_eligible is True
    assert rows[incomplete_entry.result_id].ranking_exclusion_reason == "missing_primary_metric"


def test_known_defective_entry_does_not_vote_in_canonical_query_set(tmp_path: Path) -> None:
    transformer = BundleTransformer()
    source = Path("results-data/bundles/tpchavoc_sf001_duckdb_sql_20260826_163147_d96baca2.json")
    path = tmp_path / source.name
    shutil.copy2(source, path)
    entry = transformer.to_manifest_entry(path)
    detail = transformer.to_detail_result(path, entry.result_id)
    defective_entry = entry.model_copy(
        update={
            "result_id": "known-defective",
            "platform_id": "known-defective",
            "ranking_exclusion_reason": "known_defective_data",
        }
    )
    defective_detail = detail.model_copy(
        update={"result_id": "known-defective", "display_timings": detail.display_timings[:-1]}
    )
    pairs = [(entry, detail), (defective_entry, defective_detail)]

    summaries = _build_benchmark_summaries(
        {("tpchavoc", 0.01, "power", None): pairs},
        {candidate.result_id: candidate.result_id[-8:] for candidate, _ in pairs},
    )

    rows = {row.result_id: row for row in summaries[0][1].platforms}
    assert rows[entry.result_id].is_ranking_eligible is True
    assert rows[defective_entry.result_id].ranking_exclusion_reason == "known_defective_data"


def test_partial_query_set_does_not_poison_complete_majority(tmp_path: Path) -> None:
    transformer = BundleTransformer()
    source = Path("results-data/bundles/tpchavoc_sf001_duckdb_sql_20260826_163147_d96baca2.json")
    path = tmp_path / source.name
    shutil.copy2(source, path)
    entry = transformer.to_manifest_entry(path)
    detail = transformer.to_detail_result(path, entry.result_id)
    peer_entry = entry.model_copy(update={"result_id": "complete-peer", "platform_id": "complete-peer"})
    peer_detail = detail.model_copy(update={"result_id": "complete-peer"})
    partial_entry = entry.model_copy(update={"result_id": "partial-peer", "platform_id": "partial-peer"})
    partial_detail = detail.model_copy(
        update={"result_id": "partial-peer", "display_timings": detail.display_timings[:-1]},
    )

    summaries = _build_benchmark_summaries(
        {
            ("tpchavoc", 0.01, "power", None): [
                (entry, detail),
                (peer_entry, peer_detail),
                (partial_entry, partial_detail),
            ]
        },
        {
            candidate.result_id: candidate.result_id[-8:]
            for candidate, _ in [
                (entry, detail),
                (peer_entry, peer_detail),
                (partial_entry, partial_detail),
            ]
        },
    )

    rows = {row.result_id: row for row in summaries[0][1].platforms}
    assert rows[entry.result_id].is_ranking_eligible is True
    assert rows[peer_entry.result_id].is_ranking_eligible is True
    assert rows[partial_entry.result_id].is_ranking_eligible is False
    assert rows[partial_entry.result_id].ranking_exclusion_reason == "mismatched_query_set"


def test_partial_query_set_exclusion_reaches_every_read_model_consumer(tmp_path: Path) -> None:

    class CohortFixtureTransformer(BundleTransformer):
        def result_id_from_bundle(
            self,
            bundle_path: Path,
            *,
            data: dict[str, Any] | None = None,
            raw: bytes | None = None,
        ) -> str:
            return f"cohort-fixture-{bundle_path.stem}"

        def to_detail_result(
            self,
            bundle_path: Path,
            result_id: str,
            *,
            trust_label: str = "maintainer-run",
            visibility: str = "public-curated",
            bundle_download_url: str = "",
            data: dict[str, Any] | None = None,
        ) -> DetailResult:
            detail = super().to_detail_result(
                bundle_path,
                result_id,
                trust_label=trust_label,
                visibility=visibility,
                bundle_download_url=bundle_download_url,
                data=data,
            )
            if bundle_path.stem == "partial":
                detail = detail.model_copy(update={"display_timings": detail.display_timings[:-1]})
            return detail

    source = Path("results-data/bundles/tpchavoc_sf001_duckdb_sql_20260826_163147_d96baca2.json")
    bundles_dir = tmp_path / "input" / "bundles"
    bundles_dir.mkdir(parents=True)
    for name in ("complete-a", "complete-b", "partial"):
        shutil.copy2(source, bundles_dir / f"{name}.json")

    output = tmp_path / "output"
    ExplorerPipeline(transformer=CohortFixtureTransformer()).run(tmp_path / "input", output)

    with duckdb.connect(str(output / "results.duckdb"), read_only=True) as con:
        for table, columns in (
            ("results", "is_ranking_eligible, ranking_exclusion_reason"),
            ("result_detail_metrics", "ranking_exclusion_reason"),
            ("benchmark_rankings", "is_ranking_eligible, ranking_exclusion_reason"),
            ("cohort_metadata", "ranking_exclusion_reason"),
        ):
            row = con.execute(
                f"SELECT {columns} FROM {table} WHERE result_id = ?",
                ["cohort-fixture-partial"],
            ).fetchone()
            expected = (
                (False, "mismatched_query_set") if "is_ranking_eligible" in columns else ("mismatched_query_set",)
            )
            assert row == expected, table

        peer_rows = con.execute(
            "SELECT result_id, is_ranking_eligible FROM benchmark_rankings "
            "WHERE result_id LIKE 'cohort-fixture-complete-%' ORDER BY result_id"
        ).fetchall()
    assert peer_rows == [("cohort-fixture-complete-a", True), ("cohort-fixture-complete-b", True)]


def _duckdb_results(output: Path) -> list[dict]:
    with duckdb.connect(str(output / "results.duckdb"), read_only=True) as con:
        rows = con.execute("SELECT * FROM results").fetchall()
        description = con.description
        assert description is not None
        cols = [d[0] for d in description]
    return [dict(zip(cols, row)) for row in rows]


class TestExplorerPipelineRun:
    def test_does_not_emit_manifest_json(self, data_dir: Path, tmp_path: Path) -> None:
        output = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output)

        assert not (output / "manifest.json").exists()

    def test_results_table_populated(self, data_dir: Path, tmp_path: Path) -> None:
        output = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output)

        results = _duckdb_results(output)
        assert len(results) == 1

    def test_one_usable_row_populates_every_required_browser_scan(self, data_dir: Path, tmp_path: Path) -> None:
        output = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output)

        required_scans = (
            "results",
            "platform_index_rows",
            "benchmark_rankings",
            "benchmark_matrix_cells",
            "result_detail_metrics",
        )
        with duckdb.connect(str(output / "results.duckdb"), read_only=True) as con:
            counts = {table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in required_scans}

        assert all(count >= 1 for count in counts.values()), counts

    def test_result_row_fields_from_bundle(self, data_dir: Path, tmp_path: Path) -> None:
        output = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output)

        entry = _duckdb_results(output)[0]
        assert entry["benchmark"] == "tpch"
        assert entry["platform"] == "duckdb"
        assert entry["scale_factor"] == pytest.approx(1.0)
        assert entry["trust_label"] == "maintainer-run"
        assert entry["visibility"] == "public-curated"

    def test_does_not_emit_details_dir(self, data_dir: Path, tmp_path: Path) -> None:
        output = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output)

        assert not (output / "details").exists()

    def test_result_detail_metrics_populated_in_duckdb(self, data_dir: Path, tmp_path: Path) -> None:
        output = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output)

        result_id = _duckdb_results(output)[0]["result_id"]

        with duckdb.connect(str(output / "results.duckdb"), read_only=True) as con:
            wide = con.execute(
                "SELECT result_id FROM result_detail_metrics WHERE result_id = ?",
                [result_id],
            ).fetchone()
            exec_count = con.execute(
                "SELECT COUNT(*) FROM query_executions WHERE result_id = ?",
                [result_id],
            ).fetchone()
        assert wide is not None
        assert exec_count is not None and exec_count[0] == 2

    def test_creates_results_duckdb(self, data_dir: Path, tmp_path: Path) -> None:
        output = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output)

        assert (output / "results.duckdb").exists()

    def test_copies_bundle_file(self, data_dir: Path, tmp_path: Path) -> None:
        output = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output)

        result_id = _duckdb_results(output)[0]["result_id"]
        bundle_copy = output / "bundles" / f"{result_id}.json"
        assert bundle_copy.exists()

    def test_copied_bundle_scrubs_private_paths(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        bundle = json.loads(json.dumps(MINIMAL_BUNDLE))
        bundle["platform"]["working_dir"] = "/Users/alice/private-run"
        source = bundles_dir / "private_path.json"
        source.write_text(json.dumps(bundle), encoding="utf-8")

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output)

        result_id = _duckdb_results(output)[0]["result_id"]
        published = (output / "bundles" / f"{result_id}.json").read_text(encoding="utf-8")
        assert "/Users/alice" not in published
        assert "working_dir" not in published
        assert "private-run" not in published

    def test_result_id_and_bundle_filename_use_public_bytes(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        bundle = json.loads(json.dumps(MINIMAL_BUNDLE))
        bundle["platform"]["working_dir"] = "/Users/alice/private-run"
        source = bundles_dir / "private_path.json"
        source.write_text(json.dumps(bundle), encoding="utf-8")

        transformer = BundleTransformer()
        raw = source.read_bytes()
        public_bundle = AnonymizationManager().anonymize_result_payload(bundle)
        private_result_id = transformer.result_id_from_bundle(source, data=bundle, raw=raw)
        public_result_id = transformer.result_id_from_bundle(
            source, data=public_bundle, raw=canonical_json_bytes(public_bundle)
        )
        assert public_result_id != private_result_id

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output)

        assert (output / "bundles" / f"{public_result_id}.json").exists()
        assert not (output / "bundles" / f"{private_result_id}.json").exists()

    def test_applied_receipt_is_sanitized_before_duckdb_publication(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        source = bundles_dir / "with_receipt.json"
        source.write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        source.with_name("with_receipt.applied.json").write_text(
            json.dumps({"receipt": {"entries": [{"statement": "SET path=/Users/alice/private"}]}}),
            encoding="utf-8",
        )

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output)

        row = _duckdb_results(output)[0]
        assert row["applied_receipt"] is not None
        assert "/Users/alice" not in row["applied_receipt"]

    def test_unexpected_applied_receipt_shape_is_redacted_from_read_model(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        source = bundles_dir / "with_unexpected_receipt.json"
        source.write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        source.with_name("with_unexpected_receipt.applied.json").write_text(
            json.dumps({"receipt": "private_customer_catalog"}),
            encoding="utf-8",
        )

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output)

        expected = {"reason": "unexpected_shape", "redacted": True}
        row = _duckdb_results(output)[0]
        assert json.loads(row["applied_receipt"]) == expected
        with duckdb.connect(str(output / "results.duckdb"), read_only=True) as con:
            projected = con.execute("SELECT applied_receipt FROM result_detail_metrics").fetchone()
        assert projected is not None
        assert json.loads(projected[0]) == expected

    def test_publishes_plans_sidecar_when_present(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        bundle_path = bundles_dir / "with_plans.json"
        bundle_path.write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        bundle_path.with_name("with_plans.plans.json").write_text(
            '{"queries": [{"query_id": "Q1", "plan": "SCAN tpch.lineitem"}]}',
            encoding="utf-8",
        )

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output)

        rows = _duckdb_results(output)
        assert len(rows) == 1
        result_id = rows[0]["result_id"]
        assert rows[0]["plans_published"] is True
        assert (output / "bundles" / f"{result_id}.plans.json").exists()

    def test_plans_published_false_when_no_sidecar(self, data_dir: Path, tmp_path: Path) -> None:
        output = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output)

        rows = _duckdb_results(output)
        assert all(row["plans_published"] is False for row in rows)
        assert not list((output / "bundles").glob("*.plans.json"))

    def test_inline_requested_tuning_sets_has_tuning_without_a_sidecar(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        bundle_path = bundles_dir / "with_tuning.json"
        bundle = json.loads(json.dumps(MINIMAL_BUNDLE))
        bundle["platform"]["tuning"] = {
            "tuning_source": "explicit_file",
            "source_file": "examples/tunings/duckdb/tpch_tuned.yaml",
            "requested": {"table_tunings": {"table_abc123": {"table_name": "table_abc123"}}},
        }
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output)

        row = _duckdb_results(output)[0]
        result_id = row["result_id"]
        assert row["has_tuning"] is True
        assert not (output / "bundles" / f"{result_id}.tuning.json").exists()
        published_bundle = json.loads((output / "bundles" / f"{result_id}.json").read_text(encoding="utf-8"))
        assert published_bundle["platform"]["tuning"]["requested"]

    def test_retired_tuning_companion_is_recognized_but_not_republished(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        bundle_path = bundles_dir / "legacy_tuning.json"
        bundle_path.write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        bundle_path.with_name("legacy_tuning.tuning.json").write_text(
            json.dumps(
                {
                    "version": "2.1",
                    "run_id": "test-exec-001",
                    "source_file": "/Users/alice/private/tuning.yaml",
                    "requested": {"table_tunings": {"lineitem": {"table_name": "lineitem"}}},
                }
            ),
            encoding="utf-8",
        )

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output)

        row = _duckdb_results(output)[0]
        assert row["has_tuning"] is True
        assert not list((output / "bundles").glob("*.tuning.json"))
        published = (output / "bundles" / f"{row['result_id']}.json").read_text(encoding="utf-8")
        assert "/Users/alice" not in published
        assert "lineitem" not in published

    def test_inline_applied_receipt_reaches_the_read_model(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        bundle_path = bundles_dir / "with_applied.json"
        bundle = json.loads(json.dumps(MINIMAL_BUNDLE))
        bundle["platform"]["tuning"] = {
            "applied_ledger_hash": "a" * 64,
            "validation_status": "applied_unverified",
            "applied": {
                "status": "applied_unverified",
                "statements": [{"phase": "ddl", "status": "executed", "statement_redacted": True}],
                "receipt": {"corroborated": False, "entries": [{"verdict": "match", "kind": "index"}]},
            },
        }
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output)

        row = _duckdb_results(output)[0]
        assert json.loads(row["applied_receipt"])["entries"][0]["verdict"] == "match"
        assert not list((output / "bundles").glob("*.applied.json"))

    def test_retired_applied_companion_is_read_but_not_republished(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        bundle_path = bundles_dir / "with_applied.json"
        bundle_path.write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        bundle_path.with_name("with_applied.applied.json").write_text(
            json.dumps(
                {
                    "status": "applied_unverified",
                    "applied_ledger_hash": "a" * 64,
                    "statements": [{"statement": "SET warehouse=/Users/alice/private", "status": "executed"}],
                    "dropped": [{"reason": "private adapter detail"}],
                    "receipt": {"entries": [{"statement": "CREATE INDEX private_table", "verdict": "unknown"}]},
                }
            ),
            encoding="utf-8",
        )

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output)

        row = _duckdb_results(output)[0]
        assert row["applied_receipt"] is not None
        stored = row["applied_receipt"]
        assert "/Users/alice" not in stored
        assert '"statement"' not in stored
        assert not list((output / "bundles").glob("*.applied.json"))

    @pytest.mark.parametrize("suffix", [".tuning.json", ".applied.json"])
    def test_malformed_retired_companion_is_ignored(self, data_dir: Path, tmp_path: Path, suffix: str) -> None:
        bundle = next(data_dir.joinpath("bundles").rglob("*.json"))
        bundle.with_name(f"{bundle.stem}{suffix}").write_text("{not json", encoding="utf-8")

        output = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output)

        row = _duckdb_results(output)[0]
        if suffix == ".tuning.json":
            assert row["has_tuning"] is True
        assert not list((output / "bundles").glob(f"*{suffix}"))

    def test_discovers_nested_bundle_layout(self, tmp_path: Path) -> None:
        nested_dir = tmp_path / "data" / "bundles" / "tpch" / "duckdb" / "sf1.0"
        nested_dir.mkdir(parents=True)
        bundle_path = nested_dir / "nested.json"
        bundle_path.write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output)

        results = _duckdb_results(output)
        assert len(results) == 1
        assert results[0]["result_id"].startswith("tpch-duckdb-sf1.0-")

    def test_skips_companion_files_during_recursive_discovery(self, tmp_path: Path) -> None:
        nested_dir = tmp_path / "data" / "bundles" / "tpch" / "duckdb" / "sf1.0"
        nested_dir.mkdir(parents=True)
        bundle_path = nested_dir / "sample.json"
        bundle_path.write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        bundle_path.with_name("sample.plans.json").write_text("{}", encoding="utf-8")
        bundle_path.with_name("sample.tuning.json").write_text("{}", encoding="utf-8")

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output)

        assert len(_duckdb_results(output)) == 1

    def test_loads_each_bundle_once(self, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        transformer = BundleTransformer()
        load_calls = 0
        real_load_bundle_full = transformer.load_bundle_full

        def counted_load_bundle_full(bundle_path: Path) -> tuple:
            nonlocal load_calls
            load_calls += 1
            return real_load_bundle_full(bundle_path)

        monkeypatch.setattr(transformer, "load_bundle_full", counted_load_bundle_full)

        output = tmp_path / "out"
        ExplorerPipeline(transformer=transformer).run(data_dir, output)

        assert load_calls == 1

    def test_skips_corrupt_bundle_and_keeps_valid_results(self, data_dir: Path, tmp_path: Path) -> None:
        corrupt_dir = data_dir / "bundles" / "tpch" / "duckdb" / "sf0.1"
        corrupt_dir.mkdir(parents=True, exist_ok=True)
        (corrupt_dir / "broken.json").write_text("{not valid json", encoding="utf-8")

        output = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output)

        assert len(_duckdb_results(output)) == 1

    def test_empty_data_dir_is_rejected_before_promotion(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "empty_data"
        data_dir.mkdir()
        output = tmp_path / "out"
        with pytest.raises(ValueError, match="required browser scan"):
            ExplorerPipeline().run(data_dir, output)

        assert not output.exists()

    def test_empty_bundles_dir_is_rejected_before_promotion(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        (data_dir / "bundles").mkdir(parents=True)
        output = tmp_path / "out"
        with pytest.raises(ValueError, match="required browser scan"):
            ExplorerPipeline().run(data_dir, output)

        assert not output.exists()

    def test_all_skipped_corpus_fails_before_promotion(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        bundles_dir = data_dir / "bundles"
        bundles_dir.mkdir(parents=True)
        (bundles_dir / "invalid.json").write_text("{not valid json", encoding="utf-8")
        output = tmp_path / "out"

        with pytest.raises(ValueError, match="required browser scan"):
            ExplorerPipeline().run(data_dir, output)

        assert not output.exists()

    def test_unpublishable_rebuild_preserves_last_known_good_output(self, data_dir: Path, tmp_path: Path) -> None:
        output = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output)
        before_db = (output / "results.duckdb").read_bytes()
        before_bundles = sorted(path.name for path in (output / "bundles").iterdir())

        invalid_data_dir = tmp_path / "invalid_data"
        (invalid_data_dir / "bundles").mkdir(parents=True)
        (invalid_data_dir / "bundles" / "invalid.json").write_text("{not valid json", encoding="utf-8")

        with pytest.raises(ValueError, match="required browser scan"):
            ExplorerPipeline().run(invalid_data_dir, output)

        assert (output / "results.duckdb").read_bytes() == before_db
        assert sorted(path.name for path in (output / "bundles").iterdir()) == before_bundles

    def test_submission_manifest_sidecar_overrides_trust_label(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        bundle_path = bundles_dir / "community_result.json"
        bundle_path.write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        (bundles_dir / SUBMISSION_MANIFEST_FILENAME).write_text(
            json.dumps({"bundle_hash": "abc123", "bundle_file": "community_result.json"}),
            encoding="utf-8",
        )

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output, trust_label="maintainer-run")

        entry = _duckdb_results(output)[0]
        assert entry["trust_label"] == COMMUNITY_TRUST_LABEL

        with duckdb.connect(str(output / "results.duckdb"), read_only=True) as con:
            row = con.execute(
                "SELECT trust_label FROM result_detail_metrics WHERE result_id = ?",
                [entry["result_id"]],
            ).fetchone()
        assert row is not None and row[0] == COMMUNITY_TRUST_LABEL

    def test_no_sidecar_keeps_default_trust_label(self, data_dir: Path, tmp_path: Path) -> None:
        output = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output, trust_label="maintainer-run")

        assert _duckdb_results(output)[0]["trust_label"] == "maintainer-run"

    def test_top_level_vendor_subtree_sets_vendor_label_and_visibility(self, tmp_path: Path) -> None:
        vendor_dir = tmp_path / "data" / "bundles" / "vendor"
        vendor_dir.mkdir(parents=True)
        (vendor_dir / "vendor_result.json").write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output, trust_label="maintainer-run")

        entry = _duckdb_results(output)[0]
        assert entry["trust_label"] == "vendor-supplied"
        assert entry["visibility"] == "public-vendor-reported"

    def test_nested_vendor_dir_does_not_grant_vendor_label(self, tmp_path: Path) -> None:
        nested = tmp_path / "data" / "bundles" / "community" / "vendor"
        nested.mkdir(parents=True)
        (nested / "result.json").write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output, trust_label="maintainer-run")

        assert _duckdb_results(output)[0]["trust_label"] == "maintainer-run"

    def test_funding_flows_from_bundle_provenance(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        funded = {**MINIMAL_BUNDLE, "provenance": {"funding": "free-trial"}}
        (bundles_dir / "funded_result.json").write_text(json.dumps(funded), encoding="utf-8")

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output)

        assert _duckdb_results(output)[0]["funding"] == "free-trial"

    def test_funding_defaults_to_unspecified(self, data_dir: Path, tmp_path: Path) -> None:
        output = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output)

        assert _duckdb_results(output)[0]["funding"] == "unspecified"

    def test_per_bundle_manifest_sidecar_overrides_trust_label(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        bundle_path = bundles_dir / "community_result.json"
        bundle_path.write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        (bundles_dir / "community_result.manifest.json").write_text(
            json.dumps({"bundle_hash": "abc123", "bundle_file": "community_result.json"}),
            encoding="utf-8",
        )

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output, trust_label="maintainer-run")

        assert _duckdb_results(output)[0]["trust_label"] == COMMUNITY_TRUST_LABEL

    def test_per_bundle_manifest_excluded_from_bundle_discovery(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        (bundles_dir / "real_bundle.json").write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        (bundles_dir / "real_bundle.manifest.json").write_text("{}", encoding="utf-8")
        (bundles_dir / "other.manifest.json").write_text("{}", encoding="utf-8")

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output)

        assert len(_duckdb_results(output)) == 1

    def test_submission_manifest_excluded_from_bundle_discovery(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        bundle_path = bundles_dir / "real_bundle.json"
        bundle_path.write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        (bundles_dir / SUBMISSION_MANIFEST_FILENAME).write_text("{}", encoding="utf-8")

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output)

        assert len(_duckdb_results(output)) == 1

    def test_applied_companion_excluded_from_bundle_discovery(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        bundle_path = bundles_dir / "real_bundle.json"
        bundle_path.write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        bundle_path.with_name("real_bundle.applied.json").write_text(
            json.dumps(
                {
                    "status": "applied_verified",
                    "applied_ledger_hash": "a" * 64,
                    "statements": [{"statement": "CREATE INDEX ...", "status": "applied"}],
                    "receipt": {"platform": "duckdb", "corroborated": True, "entries": []},
                }
            ),
            encoding="utf-8",
        )

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output)

        assert len(_duckdb_results(output)) == 1

    def test_discovery_ignores_json_named_directories_and_mixed_case_companions(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        (bundles_dir / "directory.json").mkdir()
        (bundles_dir / "real_bundle.JSON").write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        (bundles_dir / "real_bundle.APPLIED.JSON").write_text("{}", encoding="utf-8")

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output)

        assert len(_duckdb_results(output)) == 1

    def test_applied_receipt_reaches_results_table_and_detail_view(self, tmp_path: Path) -> None:
        receipt = {
            "platform": "duckdb",
            "corroborated": True,
            "entries": [{"statement": "CREATE INDEX ...", "verdict": "corroborated", "table": "lineitem"}],
        }
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        bundle_path = bundles_dir / "receipted.json"
        bundle_path.write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        bundle_path.with_name("receipted.applied.json").write_text(
            json.dumps({"status": "applied_verified", "receipt": receipt}), encoding="utf-8"
        )

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output)

        rows = _duckdb_results(output)
        assert len(rows) == 1
        expected = {
            "platform": "duckdb",
            "corroborated": True,
            "entries": [{"statement_redacted": True, "verdict": "corroborated"}],
        }
        assert json.loads(rows[0]["applied_receipt"]) == expected

        with duckdb.connect(str(output / "results.duckdb"), read_only=True) as con:
            projected = con.execute("SELECT applied_receipt FROM result_detail_metrics").fetchall()
        assert json.loads(projected[0][0]) == expected

    def test_applied_receipt_null_when_no_companion(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        (bundles_dir / "plain.json").write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output)

        assert _duckdb_results(output)[0]["applied_receipt"] is None

    def test_discovery_excludes_every_canonical_companion_suffix(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        (bundles_dir / "real_bundle.json").write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        for suffix in COMPANION_SUFFIXES:
            (bundles_dir / f"real_bundle{suffix}").write_text("{}", encoding="utf-8")

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output)

        assert len(_duckdb_results(output)) == 1

    def test_mixed_bundles_with_and_without_sidecar(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data" / "bundles"
        maintainer_dir = data_dir / "tpch" / "duckdb" / "sf0.1"
        maintainer_dir.mkdir(parents=True)
        (maintainer_dir / "maintainer.json").write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        community_dir = data_dir / "tpch" / "sqlite" / "sf0.1"
        community_dir.mkdir(parents=True)
        community_bundle = {**MINIMAL_BUNDLE, "platform": {"name": "sqlite", "version": "3.45"}}
        (community_dir / "community.json").write_text(json.dumps(community_bundle), encoding="utf-8")
        (community_dir / SUBMISSION_MANIFEST_FILENAME).write_text(
            json.dumps({"bundle_hash": "abc123"}), encoding="utf-8"
        )

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output, trust_label="maintainer-run")

        rows = _duckdb_results(output)
        assert len(rows) == 2
        entries = {e["platform"]: e["trust_label"] for e in rows}
        assert entries["duckdb"] == "maintainer-run"
        assert entries["sqlite"] == COMMUNITY_TRUST_LABEL

    def test_malformed_sidecar_still_triggers_community_trust(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        (bundles_dir / "result.json").write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        (bundles_dir / SUBMISSION_MANIFEST_FILENAME).write_text("NOT VALID JSON", encoding="utf-8")

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output, trust_label="maintainer-run")

        assert _duckdb_results(output)[0]["trust_label"] == COMMUNITY_TRUST_LABEL

    def test_duckdb_snapshot_reflects_overridden_trust_label(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data" / "bundles"
        maintainer_dir = data_dir / "tpch" / "duckdb" / "sf0.1"
        maintainer_dir.mkdir(parents=True)
        (maintainer_dir / "m.json").write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        community_dir = data_dir / "tpch" / "sqlite" / "sf0.1"
        community_dir.mkdir(parents=True)
        community_bundle = {**MINIMAL_BUNDLE, "platform": {"name": "sqlite", "version": "3.45"}}
        (community_dir / "c.json").write_text(json.dumps(community_bundle), encoding="utf-8")
        (community_dir / SUBMISSION_MANIFEST_FILENAME).write_text("{}", encoding="utf-8")

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output, trust_label="maintainer-run")

        with duckdb.connect(str(output / "results.duckdb"), read_only=True) as con:
            rows = con.execute("SELECT platform, trust_label FROM results ORDER BY platform").fetchall()

        trust_by_platform = {row[0]: row[1] for row in rows}
        assert trust_by_platform["duckdb"] == "maintainer-run"
        assert trust_by_platform["sqlite"] == COMMUNITY_TRUST_LABEL

    def test_sidecar_without_recorded_source_fails_safe_to_community(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        (bundles_dir / "result.json").write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        (bundles_dir / SUBMISSION_MANIFEST_FILENAME).write_text("{}", encoding="utf-8")

        output = tmp_path / "out"
        with caplog.at_level(logging.DEBUG, logger="_project.scripts.explorer_pipeline.pipeline"):
            ExplorerPipeline().run(tmp_path / "data", output, trust_label="maintainer-run")

        assert any("community-submission" in rec.message for rec in caplog.records)

    @pytest.mark.parametrize(
        ("result_source", "expected"),
        [
            ("internal", "maintainer-run"),
            ("community", "community-submission"),
            ("vendor", "vendor-supplied"),
        ],
    )
    def test_recorded_result_source_decides_the_trust_label(
        self, tmp_path: Path, result_source: str, expected: str
    ) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        (bundles_dir / "result.json").write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        (bundles_dir / "result.manifest.json").write_text(
            json.dumps({"result_source": result_source}), encoding="utf-8"
        )

        pipeline_module = importlib.import_module("_project.scripts.explorer_pipeline.pipeline")
        assert pipeline_module._manifest_trust_label(bundles_dir / "result.json", "maintainer-run") == expected

    def test_no_sidecar_means_maintainer_committed(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        (bundles_dir / "result.json").write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")

        pipeline_module = importlib.import_module("_project.scripts.explorer_pipeline.pipeline")
        assert pipeline_module._manifest_trust_label(bundles_dir / "result.json", "maintainer-run") == "maintainer-run"

    def test_unreadable_sidecar_fails_safe_to_community(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        (bundles_dir / "result.json").write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        (bundles_dir / "result.manifest.json").write_text("{not json", encoding="utf-8")

        pipeline_module = importlib.import_module("_project.scripts.explorer_pipeline.pipeline")
        assert (
            pipeline_module._manifest_trust_label(bundles_dir / "result.json", "maintainer-run")
            == "community-submission"
        )

    def test_sidecar_in_root_bundles_dir_overrides_all_flat_bundles(self, tmp_path: Path) -> None:
        bundles_dir = tmp_path / "data" / "bundles"
        bundles_dir.mkdir(parents=True)
        (bundles_dir / "a.json").write_text(json.dumps(MINIMAL_BUNDLE), encoding="utf-8")
        bundle_b = {**MINIMAL_BUNDLE, "platform": {"name": "sqlite", "version": "3.45"}}
        (bundles_dir / "b.json").write_text(json.dumps(bundle_b), encoding="utf-8")
        (bundles_dir / SUBMISSION_MANIFEST_FILENAME).write_text("{}", encoding="utf-8")

        output = tmp_path / "out"
        ExplorerPipeline().run(tmp_path / "data", output, trust_label="maintainer-run")

        rows = _duckdb_results(output)
        assert len(rows) == 2
        trust_labels = {e["trust_label"] for e in rows}
        assert trust_labels == {COMMUNITY_TRUST_LABEL}

    def test_custom_trust_label_and_visibility(self, data_dir: Path, tmp_path: Path) -> None:
        output = tmp_path / "out"
        ExplorerPipeline().run(
            data_dir,
            output,
            trust_label="community-submission",
            visibility="public-self-reported",
        )

        entry = _duckdb_results(output)[0]
        assert entry["trust_label"] == COMMUNITY_TRUST_LABEL
        assert entry["visibility"] == "public-self-reported"

    def test_custom_bundle_url_prefix(self, data_dir: Path, tmp_path: Path) -> None:
        output = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output, bundle_url_prefix="/cdn/bundles")

        result_id = _duckdb_results(output)[0]["result_id"]
        with duckdb.connect(str(output / "results.duckdb"), read_only=True) as con:
            row = con.execute(
                "SELECT bundle_download_url FROM result_detail_metrics WHERE result_id = ?",
                [result_id],
            ).fetchone()
        assert row is not None
        assert row[0] == f"/cdn/bundles/{result_id}.json"

    def test_custom_bundle_url_prefix_trailing_slash(self, data_dir: Path, tmp_path: Path) -> None:
        output = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output, bundle_url_prefix="/cdn/bundles/")

        result_id = _duckdb_results(output)[0]["result_id"]
        with duckdb.connect(str(output / "results.duckdb"), read_only=True) as con:
            row = con.execute(
                "SELECT bundle_download_url FROM result_detail_metrics WHERE result_id = ?",
                [result_id],
            ).fetchone()
        assert row is not None
        assert row[0] == f"/cdn/bundles/{result_id}.json"

    def test_permission_error_propagates(self, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        transformer = BundleTransformer()
        monkeypatch.setattr(
            transformer,
            "load_bundle_full",
            lambda _: (_ for _ in ()).throw(PermissionError("access denied")),
        )
        output = tmp_path / "out"
        with pytest.raises(PermissionError):
            ExplorerPipeline(transformer=transformer).run(data_dir, output)

    def test_duckdb_contains_correct_row_count(self, data_dir: Path, tmp_path: Path) -> None:
        output = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output)

        with duckdb.connect(str(output / "results.duckdb"), read_only=True) as con:
            count = con.execute("SELECT COUNT(*) FROM results").fetchone()[0]

        assert count == 1

    def test_does_not_emit_short_ids_json(self, data_dir: Path, tmp_path: Path) -> None:
        output = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output)

        assert not (output / "short_ids.json").exists(), (
            "short_ids.json must not be emitted - short IDs live in the DuckDB short_ids table"
        )

    def test_short_ids_table_maps_short_to_full_result_id(self, data_dir: Path, tmp_path: Path) -> None:
        output = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output)

        result_id = _duckdb_results(output)[0]["result_id"]
        with duckdb.connect(str(output / "results.duckdb"), read_only=True) as con:
            rows = con.execute("SELECT short_id, result_id FROM short_ids").fetchall()
        assert len(rows) == 1
        short_id, mapped_result_id = rows[0]
        assert len(short_id) >= 8, "Short IDs must be at least 8 hex chars"
        assert mapped_result_id == result_id, "short → full mapping must be correct"

    def test_short_id_is_sha256_prefix_of_result_id(self, data_dir: Path, tmp_path: Path) -> None:
        output = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output)

        result_id = _duckdb_results(output)[0]["result_id"]
        with duckdb.connect(str(output / "results.duckdb"), read_only=True) as con:
            short_id = con.execute("SELECT short_id FROM short_ids WHERE result_id = ?", [result_id]).fetchone()[0]

        expected = hashlib.sha256(result_id.encode()).hexdigest()[: len(short_id)]
        assert short_id == expected, "Short ID must be a sha256 prefix of the full result_id"

    def test_benchmark_rankings_short_id_matches_short_ids_table(self, data_dir: Path, tmp_path: Path) -> None:
        import duckdb

        output = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output)

        with duckdb.connect(str(output / "results.duckdb"), read_only=True) as con:
            rows = con.execute(
                "SELECT br.short_id, br.result_id, si.result_id AS resolved"
                " FROM benchmark_rankings br"
                " LEFT JOIN short_ids si ON si.short_id = br.short_id"
            ).fetchall()

        assert rows, "At least one benchmark_rankings row must be written"
        for short_id, result_id, resolved in rows:
            if short_id:
                assert resolved == result_id, (
                    f"short_id {short_id!r} in benchmark_rankings must resolve to {result_id!r}"
                )


class TestStagedOutputGuards:
    @staticmethod
    def _assert_no_staging_leftovers(output: Path) -> None:
        leftovers = [p.name for p in output.parent.iterdir() if p.name.startswith(f".{output.name}.")]
        assert leftovers == [], f"Staging directory leaked after a failed build: {leftovers}"

    def test_promoted_db_removes_exact_stale_wal_sibling(self, data_dir: Path, tmp_path: Path) -> None:
        output = tmp_path / "out"
        output.mkdir()
        stale_wal = output / "results.duckdb.wal"
        stale_wal.write_bytes(b"stale sidecar")

        ExplorerPipeline().run(data_dir, output)

        assert not stale_wal.exists()
        assert check_snapshot(output / "results.duckdb") == []
        with duckdb.connect(str(output / "results.duckdb"), read_only=True) as con:
            assert con.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 1

    def test_wal_cleanup_failure_is_visible_after_db_promotion(
        self, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = tmp_path / "out"
        output.mkdir()
        stale_wal = output / "results.duckdb.wal"
        stale_wal.write_bytes(b"stale sidecar")
        real_unlink = Path.unlink

        def deny_published_wal(path: Path, *, missing_ok: bool = False) -> None:
            if path == stale_wal:
                raise PermissionError("WAL is locked")
            real_unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", deny_published_wal)

        with pytest.raises(OSError, match="could not remove stale published WAL"):
            ExplorerPipeline().run(data_dir, output)

        assert (output / "results.duckdb").is_file(), "DB promotion must happen before WAL cleanup"
        assert stale_wal.is_file(), "Cleanup failure must leave the cause visible"

    def test_symlinked_bundles_destination_is_rejected(self, data_dir: Path, tmp_path: Path) -> None:
        output = tmp_path / "out"
        output.mkdir()
        unrelated = tmp_path / "unrelated"
        unrelated.mkdir()
        bystander = unrelated / "keep-me.json"
        bystander.write_text('{"keep": true}', encoding="utf-8")
        (output / "bundles").symlink_to(unrelated, target_is_directory=True)

        with pytest.raises(ValueError, match="must be a real directory"):
            ExplorerPipeline().run(data_dir, output)

        assert bystander.exists(), "A build must never sweep files inside a symlink target"

    def test_staging_dir_is_removed_when_a_pre_build_step_fails(
        self, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = tmp_path / "out"

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("input vanished mid-build")

        monkeypatch.setattr(pipeline_module, "_build_short_ids", _boom)

        with pytest.raises(OSError, match="input vanished"):
            ExplorerPipeline().run(data_dir, output)

        leftovers = [p.name for p in output.parent.iterdir() if p.name.startswith(f".{output.name}.")]
        assert leftovers == [], f"Staging directory leaked after a pre-build failure: {leftovers}"

    def test_staging_dir_is_removed_when_staged_bundle_copy_fails(
        self, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = tmp_path / "out"
        real_write_bytes = Path.write_bytes

        def _copy_boom(path: Path, data: bytes) -> int:
            if path.parent.name == "bundles" and path.parent.parent.name.startswith(f".{output.name}."):
                raise OSError("staged bundle copy failed")
            return real_write_bytes(path, data)

        monkeypatch.setattr(Path, "write_bytes", _copy_boom)

        with pytest.raises(OSError, match="staged bundle copy failed"):
            ExplorerPipeline().run(data_dir, output)

        self._assert_no_staging_leftovers(output)

    def test_staging_dir_is_removed_when_bundle_transform_fails(
        self, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = tmp_path / "out"
        transformer = BundleTransformer()

        def _transform_boom(*_args: object, **_kwargs: object) -> tuple:
            raise OSError("bundle transform failed")

        monkeypatch.setattr(transformer, "load_bundle_full", _transform_boom)

        with pytest.raises(OSError, match="bundle transform failed"):
            ExplorerPipeline(transformer=transformer).run(data_dir, output)

        self._assert_no_staging_leftovers(output)

    def test_staging_dir_is_removed_when_summary_build_fails(
        self, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = tmp_path / "out"

        def _summary_boom(*_args: object, **_kwargs: object) -> list:
            raise OSError("summary build failed")

        monkeypatch.setattr(pipeline_module, "_build_benchmark_summaries", _summary_boom)

        with pytest.raises(OSError, match="summary build failed"):
            ExplorerPipeline().run(data_dir, output)

        self._assert_no_staging_leftovers(output)

    def test_staging_dir_is_removed_when_promotion_fails(
        self, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = tmp_path / "out"

        def _promotion_boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("snapshot promotion failed")

        monkeypatch.setattr(pipeline_module, "_promote_staged_output", _promotion_boom)

        with pytest.raises(OSError, match="snapshot promotion failed"):
            ExplorerPipeline().run(data_dir, output)

        self._assert_no_staging_leftovers(output)
