from __future__ import annotations

import copy
import json
from pathlib import Path

import duckdb
import pytest

from _project.scripts.explorer_pipeline.duckdb_builder import DuckDBSnapshotBuilder
from _project.scripts.explorer_pipeline.models import ManifestEntry
from _project.scripts.explorer_pipeline.pipeline import (
    ExplorerPipeline,
    PrivacyRejectionError,
    _public_override_display,
)
from _project.scripts.explorer_pipeline.transformer import BundleTransformer, _override_display
from tests.unit.scripts.explorer_pipeline.conftest import MINIMAL_BUNDLE

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _override_payload(**overrides) -> dict:
    payload: dict = {
        "rules": [{"rule": "timing-plateau", "rule_version": "1"}],
        "reason": "plateau matches the archived September reference runs",
        "evidence": "https://example.test/pr/1",
        "expires": "2099-01-01",
        "approver": "reviewer",
    }
    payload.update(overrides)
    return payload


def _write_bundle(tmp_path: Path, stem: str = "overridden") -> Path:
    bundle = tmp_path / f"{stem}.json"
    bundle.write_text(json.dumps(copy.deepcopy(MINIMAL_BUNDLE)), encoding="utf-8")
    return bundle


def _write_override(bundle: Path, payload: dict) -> Path:
    companion = bundle.with_name(f"{bundle.stem}.override.json")
    companion.write_text(json.dumps(payload), encoding="utf-8")
    return companion


class TestOverrideDisplay:
    def test_valid_companion_returns_rules_and_audit_fields(self, tmp_path: Path) -> None:
        bundle = _write_bundle(tmp_path)
        _write_override(bundle, _override_payload())

        display = _override_display(bundle)

        assert display["override_rules"] == ["timing-plateau"]
        assert display["override_evidence"] == "https://example.test/pr/1"
        assert display["override_approver"] == "reviewer"
        assert display["override_expires"] == "2099-01-01"

    def test_missing_companion_yields_empty_display(self, tmp_path: Path) -> None:
        bundle = _write_bundle(tmp_path)

        assert _override_display(bundle) == {
            "override_rules": [],
            "override_evidence": None,
            "override_approver": None,
            "override_expires": None,
        }

    def test_malformed_companion_yields_empty_display(self, tmp_path: Path) -> None:
        bundle = _write_bundle(tmp_path)
        bundle.with_name(f"{bundle.stem}.override.json").write_text("{not valid json", encoding="utf-8")

        assert _override_display(bundle)["override_rules"] == []

    def test_expired_companion_yields_empty_display(self, tmp_path: Path) -> None:
        bundle = _write_bundle(tmp_path)
        _write_override(bundle, _override_payload(expires="2000-01-01"))

        assert _override_display(bundle)["override_rules"] == []

    def test_unknown_rule_yields_empty_display(self, tmp_path: Path) -> None:
        bundle = _write_bundle(tmp_path)
        _write_override(bundle, _override_payload(rules=[{"rule": "no-such-rule", "rule_version": "1"}]))

        assert _override_display(bundle)["override_rules"] == []

    def test_entry_and_detail_carry_override_fields(self, tmp_path: Path) -> None:
        bundle = _write_bundle(tmp_path)
        _write_override(bundle, _override_payload())

        transformer = BundleTransformer()
        entry = transformer.to_manifest_entry(bundle)
        detail = transformer.to_detail_result(bundle, entry.result_id)

        assert isinstance(entry, ManifestEntry)
        assert entry.override_rules == ["timing-plateau"]
        assert entry.override_evidence == "https://example.test/pr/1"
        assert entry.override_approver == "reviewer"
        assert entry.override_expires == "2099-01-01"
        assert detail.override_rules == ["timing-plateau"]
        assert detail.override_evidence == "https://example.test/pr/1"

    def test_entry_without_companion_has_empty_override_fields(self, tmp_path: Path) -> None:
        bundle = _write_bundle(tmp_path)

        entry = BundleTransformer().to_manifest_entry(bundle)

        assert entry.override_rules == []
        assert entry.override_evidence is None
        assert entry.override_approver is None
        assert entry.override_expires is None


class TestPublicOverrideDisplay:
    def test_clean_override_passes_through(self, tmp_path: Path) -> None:
        bundle = _write_bundle(tmp_path)
        _write_override(bundle, _override_payload())

        display = _public_override_display(bundle)

        assert display["override_rules"] == ["timing-plateau"]
        assert display["override_approver"] == "reviewer"

    def test_private_path_in_evidence_fails_closed(self, tmp_path: Path) -> None:
        bundle = _write_bundle(tmp_path)
        _write_override(bundle, _override_payload(evidence="see /Users/alice/notes.txt for context"))

        with pytest.raises(PrivacyRejectionError, match="public override display privacy check failed"):
            _public_override_display(bundle)

    def test_no_companion_passes_through_empty(self, tmp_path: Path) -> None:
        bundle = _write_bundle(tmp_path)

        assert _public_override_display(bundle)["override_rules"] == []


def _make_entry(**overrides) -> ManifestEntry:
    defaults: dict = {
        "result_id": "tpch-duckdb-sf0.1-20260315-abcd1234",
        "benchmark": "tpch",
        "scale_factor": 0.1,
        "platform": "duckdb",
        "driver_version": "1.2.0",
        "run_date": "2026-03-15",
        "power_score": 1234.56,
        "total_duration_s": 45.0,
        "query_count": 2,
        "trust_label": "maintainer-run",
        "visibility": "public-curated",
    }
    defaults.update(overrides)
    return ManifestEntry(**defaults)


class TestOverrideDuckDBRoundTrip:
    def test_results_row_and_view_expose_override_columns(self) -> None:
        entry = _make_entry(
            override_rules=["timing-plateau"],
            override_evidence="https://example.test/pr/1",
            override_approver="reviewer",
            override_expires="2099-01-01",
        )
        builder = DuckDBSnapshotBuilder()
        con = duckdb.connect(":memory:")
        try:
            builder._create_schema(con)
            builder._populate_results(con, [entry], {}, "https://example.test/bundles", {})
            row = con.execute(
                "SELECT override_rules, override_evidence, override_approver, override_expires"
                " FROM results WHERE result_id = ?",
                [entry.result_id],
            ).fetchone()
            assert row is not None
            assert json.loads(row[0]) == ["timing-plateau"]
            assert row[1] == "https://example.test/pr/1"
            assert row[2] == "reviewer"
            assert row[3] == "2099-01-01"
            view_row = con.execute(
                "SELECT override_rules, override_approver FROM result_detail_metrics WHERE result_id = ?",
                [entry.result_id],
            ).fetchone()
            assert view_row is not None
            assert json.loads(view_row[0]) == ["timing-plateau"]
            assert view_row[1] == "reviewer"
        finally:
            con.close()

    def test_full_pipeline_run_publishes_override_columns(self, tmp_path: Path) -> None:

        data_dir = tmp_path / "data"
        bundles_dir = data_dir / "bundles"
        bundles_dir.mkdir(parents=True)
        bundle = bundles_dir / "overridden.json"
        bundle.write_text(json.dumps(copy.deepcopy(MINIMAL_BUNDLE)), encoding="utf-8")
        _write_override(bundle, _override_payload())

        output_dir = tmp_path / "out"
        ExplorerPipeline().run(data_dir, output_dir, bundle_url_prefix="/results/data/bundles")

        con = duckdb.connect(str(output_dir / "results.duckdb"), read_only=True)
        try:
            row = con.execute(
                "SELECT override_rules, override_evidence, override_approver, override_expires FROM results"
            ).fetchone()
            assert row is not None
            assert json.loads(row[0]) == ["timing-plateau"]
            assert row[1] == "https://example.test/pr/1"
            assert row[2] == "reviewer"
            assert row[3] == "2099-01-01"
        finally:
            con.close()

    def test_row_without_override_stores_nulls(self) -> None:
        entry = _make_entry()
        builder = DuckDBSnapshotBuilder()
        con = duckdb.connect(":memory:")
        try:
            builder._create_schema(con)
            builder._populate_results(con, [entry], {}, "https://example.test/bundles", {})
            row = con.execute(
                "SELECT override_rules, override_evidence, override_approver, override_expires"
                " FROM results WHERE result_id = ?",
                [entry.result_id],
            ).fetchone()
            assert row == (None, None, None, None)
        finally:
            con.close()


