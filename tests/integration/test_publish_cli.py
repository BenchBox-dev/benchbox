# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from benchbox.cli.commands.publish import publish
from benchbox.core.publishing.store import PublicationStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def schema_v2_bundle(tmp_path) -> Path:

    bundle = tmp_path / "tpch_sf0.01_duckdb_20260401_120000.json"
    payload = {
        "version": "2.0",
        "benchmark": {"name": "tpch", "scale_factor": 0.01},
        "platform": {"name": "duckdb"},
        "run": {"id": "abc123", "timestamp": "2026-04-01T12:00:00"},
        "summary": {"queries": {"total": 22, "passed": 22, "failed": 0}, "validation": "PASSED"},
        "queries": [],
    }
    bundle.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return bundle


@pytest.fixture
def schema_v2_bundle_with_companions(tmp_path) -> Path:

    bundle = tmp_path / "tpch_sf0.01_duckdb_20260401_120000.json"
    payload = {
        "version": "2.0",
        "benchmark": {"name": "tpch", "scale_factor": 0.01},
        "platform": {"name": "duckdb"},
        "run": {"id": "abc123", "timestamp": "2026-04-01T12:00:00"},
        "summary": {"queries": {"total": 22, "passed": 22, "failed": 0}, "validation": "PASSED"},
        "queries": [],
    }
    bundle.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    plans_path = tmp_path / "tpch_sf0.01_duckdb_20260401_120000.plans.json"
    plans_path.write_text(json.dumps({"plans": []}), encoding="utf-8")

    tuning_path = tmp_path / "tpch_sf0.01_duckdb_20260401_120000.tuning.json"
    tuning_path.write_text(json.dumps({"tuning": {}}), encoding="utf-8")

    return bundle


@pytest.fixture
def publish_target(tmp_path) -> Path:
    target = tmp_path / "published"
    target.mkdir()
    return target


@pytest.fixture
def store(tmp_path) -> PublicationStore:
    return PublicationStore(store_path=tmp_path / "published.json")


class TestPublishRun:
    def test_publish_local_bundle_succeeds(self, runner, schema_v2_bundle, publish_target, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(
                publish,
                [
                    "run",
                    str(schema_v2_bundle),
                    "--target",
                    str(publish_target),
                    "--label",
                    "local",
                ],
                env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        assert "Published" in result.output or "file://" in result.output

    def test_publish_copies_bundle_to_target(self, runner, schema_v2_bundle, publish_target, tmp_path):
        runner.invoke(
            publish,
            ["run", str(schema_v2_bundle), "--target", str(publish_target)],
            env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
            catch_exceptions=False,
        )
        dest_file = publish_target / schema_v2_bundle.name
        assert dest_file.exists()

    def test_publish_companion_files_copied(self, runner, schema_v2_bundle_with_companions, publish_target, tmp_path):
        bundle = schema_v2_bundle_with_companions
        runner.invoke(
            publish,
            ["run", str(bundle), "--target", str(publish_target)],
            env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
            catch_exceptions=False,
        )
        stem = bundle.stem
        assert (publish_target / bundle.name).exists()
        assert (publish_target / f"{stem}.plans.json").exists()
        assert (publish_target / f"{stem}.tuning.json").exists()

    def test_publish_reference_is_file_uri(self, runner, schema_v2_bundle, publish_target, tmp_path):
        result = runner.invoke(
            publish,
            ["run", str(schema_v2_bundle), "--target", str(publish_target)],
            env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
            catch_exceptions=False,
        )
        assert "file://" in result.output

    def test_publish_dry_run_does_not_copy(self, runner, schema_v2_bundle, publish_target, tmp_path):
        runner.invoke(
            publish,
            ["run", str(schema_v2_bundle), "--target", str(publish_target), "--dry-run"],
            env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
            catch_exceptions=False,
        )
        dest_file = publish_target / schema_v2_bundle.name
        assert not dest_file.exists()

    def test_publish_dry_run_output_mentions_bundle(self, runner, schema_v2_bundle, publish_target, tmp_path):
        result = runner.invoke(
            publish,
            ["run", str(schema_v2_bundle), "--target", str(publish_target), "--dry-run"],
            env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
            catch_exceptions=False,
        )
        assert "Dry run" in result.output or schema_v2_bundle.name in result.output

    def test_publish_nonexistent_file_exits_nonzero(self, runner, publish_target, tmp_path):
        result = runner.invoke(
            publish,
            ["run", "/nonexistent/result.json", "--target", str(publish_target)],
            env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
        )
        assert result.exit_code != 0 or "Error" in result.output or "not found" in result.output

    def test_publish_with_label(self, runner, schema_v2_bundle, publish_target, tmp_path):
        result = runner.invoke(
            publish,
            ["run", str(schema_v2_bundle), "--target", str(publish_target), "--label", "ci"],
            env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
            catch_exceptions=False,
        )
        assert result.exit_code == 0


class TestPublishList:
    def test_list_empty_store(self, runner, tmp_path):
        result = runner.invoke(
            publish,
            ["list"],
            env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "No publications" in result.output or "publications found" in result.output

    def test_list_shows_published_artifacts(self, runner, schema_v2_bundle, publish_target, tmp_path):

        runner.invoke(
            publish,
            ["run", str(schema_v2_bundle), "--target", str(publish_target)],
            env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
            catch_exceptions=False,
        )

        result = runner.invoke(
            publish,
            ["list"],
            env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "tpch" in result.output or "duckdb" in result.output


class TestPublishShow:
    def test_show_existing_record(self, runner, schema_v2_bundle, publish_target, tmp_path):

        runner.invoke(
            publish,
            ["run", str(schema_v2_bundle), "--target", str(publish_target)],
            env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
            catch_exceptions=False,
        )

        store2 = PublicationStore(store_path=tmp_path / ".benchbox" / "published.json")
        records = store2.list_all()
        if not records:
            pytest.skip("No records in store - HOME env patch may not have taken effect")

        pub_id = records[0].pub_id
        result = runner.invoke(
            publish,
            ["show", pub_id],
            env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert pub_id in result.output

    def test_show_missing_id_exits_nonzero(self, runner, tmp_path):
        result = runner.invoke(
            publish,
            ["show", "nonexistentid1"],
            env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
        )
        assert result.exit_code != 0


class TestPublishRemove:
    def test_remove_with_yes_flag(self, runner, schema_v2_bundle, publish_target, tmp_path):
        runner.invoke(
            publish,
            ["run", str(schema_v2_bundle), "--target", str(publish_target)],
            env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
            catch_exceptions=False,
        )
        store = PublicationStore(store_path=tmp_path / ".benchbox" / "published.json")
        records = store.list_all()
        if not records:
            pytest.skip("No records in store")
        pub_id = records[0].pub_id

        result = runner.invoke(
            publish,
            ["remove", pub_id, "--yes"],
            env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Removed" in result.output

    def test_remove_missing_id_exits_nonzero(self, runner, tmp_path):
        result = runner.invoke(
            publish,
            ["remove", "nonexistentid1", "--yes"],
            env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
        )
        assert result.exit_code != 0


class TestPublishIdempotency:
    def test_publishing_same_bundle_twice_creates_one_record(self, runner, schema_v2_bundle, publish_target, tmp_path):
        store = PublicationStore(store_path=tmp_path / ".benchbox" / "published.json")

        for _ in range(2):
            runner.invoke(
                publish,
                ["run", str(schema_v2_bundle), "--target", str(publish_target)],
                env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
                catch_exceptions=False,
            )

        records = store.list_all()
        assert len(records) == 1, f"Expected 1 record, got {len(records)}"
