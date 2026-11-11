# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchbox.core.publishing.store import (
    PublicationRecord,
    PublicationStore,
    _generate_pub_id,
    build_reference,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestPublicationRecord:
    def test_round_trip_dict(self):
        rec = PublicationRecord(
            pub_id="abc123def456",
            source_path="/tmp/result.json",
            destination="/tmp/published",
            reference="file:///tmp/published/result.json",
            label="maintainer-run",
            published_at="2026-04-01T12:00:00+00:00",
            benchmark="tpch",
            platform="duckdb",
            scale_factor=1.0,
        )
        d = rec.to_dict()
        restored = PublicationRecord.from_dict(d)
        assert restored.pub_id == rec.pub_id
        assert restored.source_path == rec.source_path
        assert restored.reference == rec.reference
        assert restored.benchmark == "tpch"
        assert restored.platform == "duckdb"

    def test_from_dict_ignores_unknown_keys(self):
        d = {
            "pub_id": "abc123",
            "source_path": "/tmp/result.json",
            "destination": "/tmp/published",
            "reference": "file:///tmp/published/result.json",
            "label": "ci",
            "published_at": "2026-04-01T12:00:00+00:00",
            "benchmark": "",
            "platform": "",
            "scale_factor": 1.0,
            "_unknown_future_field": "should-be-ignored",
        }
        rec = PublicationRecord.from_dict({k: v for k, v in d.items() if k != "_unknown_future_field"})
        assert rec.pub_id == "abc123"


class TestPublicationStore:
    def test_empty_store_returns_empty_list(self, tmp_path):
        store = PublicationStore(store_path=tmp_path / "published.json")
        assert store.list_all() == []

    def test_add_creates_record(self, tmp_path):
        store = PublicationStore(store_path=tmp_path / "published.json")
        rec = store.add(
            source_path="/tmp/result.json",
            destination="/tmp/published",
            reference="file:///tmp/published/result.json",
            label="maintainer-run",
            benchmark="tpch",
            platform="duckdb",
            scale_factor=1.0,
        )
        assert rec.pub_id
        assert rec.benchmark == "tpch"
        assert rec.platform == "duckdb"

    def test_add_persists_to_disk(self, tmp_path):
        store_path = tmp_path / "published.json"
        store = PublicationStore(store_path=store_path)
        store.add(
            source_path="/tmp/result.json",
            destination="/tmp/published",
            reference="file:///tmp/published/result.json",
            label="maintainer-run",
        )
        assert store_path.exists()
        data = json.loads(store_path.read_text())
        assert len(data) == 1
        assert data[0]["reference"] == "file:///tmp/published/result.json"

    def test_state_survives_process_restart(self, tmp_path):
        store_path = tmp_path / "published.json"
        store1 = PublicationStore(store_path=store_path)
        store1.add(
            source_path="/tmp/result.json",
            destination="/tmp/published",
            reference="file:///tmp/published/result.json",
            label="maintainer-run",
            benchmark="tpch",
            platform="duckdb",
        )

        store2 = PublicationStore(store_path=store_path)
        records = store2.list_all()
        assert len(records) == 1
        assert records[0].benchmark == "tpch"

    def test_list_all_sorted_newest_first(self, tmp_path):
        store = PublicationStore(store_path=tmp_path / "published.json")
        from unittest.mock import patch

        t1 = "2026-01-01T00:00:00.000000+00:00"
        t2 = "2026-01-01T00:00:00.000001+00:00"
        with patch("benchbox.core.publishing.store._now_iso", side_effect=[t1, t1, t2, t2]):
            store.add(
                source_path="/tmp/a.json",
                destination="/tmp/pub",
                reference="file:///tmp/pub/a.json",
                label="ci",
                benchmark="tpch",
            )
            store.add(
                source_path="/tmp/b.json",
                destination="/tmp/pub",
                reference="file:///tmp/pub/b.json",
                label="ci",
                benchmark="tpcds",
            )
        records = store.list_all()
        assert len(records) == 2
        assert records[0].benchmark == "tpcds"
        assert records[1].benchmark == "tpch"

    def test_get_by_id(self, tmp_path):
        store = PublicationStore(store_path=tmp_path / "published.json")
        rec = store.add(
            source_path="/tmp/result.json",
            destination="/tmp/published",
            reference="file:///tmp/published/result.json",
            label="local",
        )
        found = store.get(rec.pub_id)
        assert found is not None
        assert found.pub_id == rec.pub_id

    def test_get_missing_id_returns_none(self, tmp_path):
        store = PublicationStore(store_path=tmp_path / "published.json")
        assert store.get("nonexistent") is None

    def test_remove_existing_record(self, tmp_path):
        store = PublicationStore(store_path=tmp_path / "published.json")
        rec = store.add(
            source_path="/tmp/result.json",
            destination="/tmp/published",
            reference="file:///tmp/published/result.json",
            label="local",
        )
        removed = store.remove(rec.pub_id)
        assert removed is True
        assert store.get(rec.pub_id) is None
        assert store.list_all() == []

    def test_remove_missing_id_returns_false(self, tmp_path):
        store = PublicationStore(store_path=tmp_path / "published.json")
        assert store.remove("nonexistent") is False

    def test_idempotent_republish_updates_record(self, tmp_path):
        store = PublicationStore(store_path=tmp_path / "published.json")
        rec1 = store.add(
            source_path="/tmp/result.json",
            destination="/tmp/published",
            reference="file:///tmp/published/result.json",
            label="local",
        )
        rec2 = store.add(
            source_path="/tmp/result.json",
            destination="/tmp/published",
            reference="file:///tmp/published/result.json",
            label="maintainer-run",
        )
        assert rec1.pub_id == rec2.pub_id
        assert store.get(rec1.pub_id).label == "maintainer-run"
        assert len(store.list_all()) == 1

    def test_different_destinations_create_separate_records(self, tmp_path):
        store = PublicationStore(store_path=tmp_path / "published.json")
        store.add(
            source_path="/tmp/result.json",
            destination="/tmp/pub-a",
            reference="file:///tmp/pub-a/result.json",
            label="local",
        )
        store.add(
            source_path="/tmp/result.json",
            destination="/tmp/pub-b",
            reference="file:///tmp/pub-b/result.json",
            label="local",
        )
        assert len(store.list_all()) == 2

    def test_corrupted_store_returns_empty_list(self, tmp_path):
        store_path = tmp_path / "published.json"
        store_path.write_text("not valid json", encoding="utf-8")
        store = PublicationStore(store_path=store_path)
        assert store.list_all() == []


class TestBuildReference:
    def test_local_path_produces_file_uri(self, tmp_path):
        destination = str(tmp_path)
        ref = build_reference(destination, "result.json")
        assert ref.startswith("file://")
        assert ref.endswith("result.json")

    def test_s3_path_produces_cloud_uri(self):
        ref = build_reference("s3://my-bucket/benchbox", "result.json")
        assert ref == "s3://my-bucket/benchbox/result.json"

    def test_gcs_path_produces_cloud_uri(self):
        ref = build_reference("gs://my-bucket/prefix", "result.json")
        assert ref == "gs://my-bucket/prefix/result.json"

    def test_trailing_slash_normalised(self):
        ref = build_reference("s3://my-bucket/prefix/", "result.json")
        assert ref == "s3://my-bucket/prefix/result.json"


class TestGeneratePubId:
    def test_returns_12_char_hex(self):
        pub_id = _generate_pub_id("/tmp/result.json")
        assert len(pub_id) == 12
        assert all(c in "0123456789abcdef" for c in pub_id)

    def test_different_inputs_produce_different_ids(self):
        id1 = _generate_pub_id("/tmp/a.json")
        id2 = _generate_pub_id("/tmp/b.json")
        assert isinstance(id1, str)
        assert isinstance(id2, str)


