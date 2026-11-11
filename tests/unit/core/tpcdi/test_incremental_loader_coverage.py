from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from benchbox.core.tpcdi.etl.incremental_loader import (
    ChangeRecord,
    HashBasedChangeDetector,
    IncrementalBatch,
    IncrementalDataLoader,
    IncrementalLoadConfig,
    TimestampBasedChangeDetector,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _Cursor:
    def __init__(self, rows=None, description=None):
        self._rows = rows or []
        self.description = description or []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self):
        self.calls: list[tuple[str, tuple | None]] = []
        self.raise_for_watermark = False

    def execute(self, query: str, params=None):
        self.calls.append((query, params))
        if "FROM ETL_Watermarks" in query and self.raise_for_watermark:
            raise RuntimeError("no watermark table")
        if "FROM ETL_Watermarks" in query:
            return _Cursor(rows=[("DimCustomer", datetime(2026, 1, 1, 0, 0, 0))])
        if "WHERE LastModified >" in query:
            return _Cursor(
                rows=[(1, "v", datetime(2026, 1, 2, 0, 0, 0))],
                description=[("CustomerID",), ("val",), ("LastModified",)],
            )
        return _Cursor(rows=[])


def test_change_detectors_and_batch_lifecycle_paths(monkeypatch: pytest.MonkeyPatch):
    conn = _Conn()
    cfg = IncrementalLoadConfig(enable_change_data_capture=True, use_hash_based_detection=True)
    loader = IncrementalDataLoader(conn, config=cfg)
    assert "timestamp" in loader.change_detectors
    assert "hash" in loader.change_detectors
    assert loader.table_watermarks["DimCustomer"] == datetime(2026, 1, 1, 0, 0, 0)

    batch = loader.create_incremental_batch(datetime(2026, 1, 3), source_system="TPC", batch_type="INCREMENTAL")
    assert batch.batch_id in loader.active_batches
    monkeypatch.setattr(loader, "_get_incremental_tables", lambda: ["DimCustomer"])
    monkeypatch.setattr(
        loader,
        "_process_table_incremental_changes",
        lambda _t, _b: {"changes_detected": 3, "operations": {"INSERT": 2, "UPDATE": 1, "DELETE": 0}},
    )
    out = loader.process_incremental_batch(batch)
    assert out["success"] is True
    assert out["total_changes_detected"] == 3
    assert batch.batch_id not in loader.active_batches
    assert loader.batch_history

    bad_batch = loader.create_incremental_batch(datetime(2026, 1, 4))
    monkeypatch.setattr(
        loader, "_process_table_incremental_changes", lambda *_: (_ for _ in ()).throw(ValueError("boom"))
    )
    failed = loader.process_incremental_batch(bad_batch, tables=["DimCustomer"])
    assert failed["success"] is False
    assert "failed" in failed["error_message"].lower()


def test_timestamp_detector_and_primary_key_extraction_and_errors():
    conn = _Conn()
    det = TimestampBasedChangeDetector(conn, IncrementalLoadConfig())
    rows = list(det.detect_changes("DimCustomer", datetime(2026, 1, 1), batch_id=7))
    assert rows and rows[0].primary_key["CustomerID"] == 1
    assert det._extract_primary_key({"ID": 9}, "Unknown") == {"ID": 9}

    class _ErrConn(_Conn):
        def execute(self, query, params=None):
            raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        list(
            TimestampBasedChangeDetector(_ErrConn(), IncrementalLoadConfig()).detect_changes(
                "DimCustomer", datetime.now(), 1
            )
        )


def test_hash_detector_and_simple_detection_paths():
    det = HashBasedChangeDetector(_Conn(), IncrementalLoadConfig())
    out = list(det.detect_changes("DimCustomer", datetime(2026, 1, 1), batch_id=5))
    assert out and out[0].operation == "UPDATE"

    loader = IncrementalDataLoader(_Conn(), config=IncrementalLoadConfig(enable_watermarks=False))
    loader.change_detectors["DimCustomer"] = SimpleNamespace(
        detect_changes=lambda *_args, **_kwargs: iter(
            [ChangeRecord("DimCustomer", "INSERT", {"k": 1}, {"k": 1}, datetime.now(), 1)]
        )
    )
    assert loader.detect_changes_simple("DimCustomer", datetime.now(), 1)[0]["table_name"] == "DimCustomer"
    assert loader.detect_changes_simple("Missing", datetime.now(), 1) == []

    loader.change_detectors["DimCustomer"] = SimpleNamespace(
        detect_changes=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError())
    )
    assert loader.detect_changes_simple("DimCustomer", datetime.now(), 1) == []


def test_auxiliary_helpers_and_status_paths(monkeypatch: pytest.MonkeyPatch):
    conn = _Conn()
    loader = IncrementalDataLoader(conn, config=IncrementalLoadConfig(enable_watermarks=False))

    b = IncrementalBatch(batch_id=1, batch_date=datetime.now(), source_system="TPC", batch_type="INCREMENTAL")
    loader.active_batches[1] = b
    assert loader.get_batch_status(1)["is_active"] is True

    b.status = "COMPLETED"
    b.start_time = datetime.now() - timedelta(seconds=2)
    b.end_time = datetime.now()
    b.actual_record_count = 4
    b.changes_by_table = {"DimCustomer": 4}
    loader.batch_history.append(b)
    del loader.active_batches[1]
    hist = loader.get_batch_status(1)
    assert hist and hist["records_processed"] == 4 and hist["is_active"] is False
    assert loader.get_batch_status(9999) is None

    old = IncrementalBatch(
        batch_id=2, batch_date=datetime.now() - timedelta(days=45), source_system="TPC", batch_type="INCREMENTAL"
    )
    loader.batch_history.append(old)
    assert loader.cleanup_old_batches(retention_days=30) >= 1

    stats = loader.get_incremental_statistics()
    assert "total_batches_processed" in stats and "by_table" in stats
    assert loader._get_last_watermark("Missing").year == 1900
    assert loader.get_watermark("Missing").year == 1900

    detector_ok = SimpleNamespace(
        detect_changes=lambda *_a, **_k: [ChangeRecord("DimCustomer", "INSERT", {"k": 1}, {}, datetime.now(), 1)]
    )
    detector_fail = SimpleNamespace(detect_changes=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("nope")))
    loader.change_detectors = {"ok": detector_ok, "bad": detector_fail}
    detected = loader.detect_changes("DimCustomer", datetime.now(), 7)
    assert len(detected) == 1

    assert loader._load_data_batch([], "DimCustomer")["records_loaded"] == 0
    mixed = [
        ChangeRecord("DimCustomer", "INSERT", {}, {}, datetime.now(), 1),
        ChangeRecord("DimCustomer", "UPDATE", {}, {}, datetime.now(), 1),
        ChangeRecord("DimCustomer", "DELETE", {}, {}, datetime.now(), 1),
    ]
    assert loader._load_data_batch(mixed, "DimCustomer")["records_loaded"] == 3

    monkeypatch.setattr(loader, "_load_data_batch", lambda *_a, **_k: {"success": True, "records_loaded": 2})
    assert loader.load_incremental_batch("DimCustomer", [{"a": 1}], 11)["records_loaded"] == 2

    monkeypatch.setattr(loader, "_load_data_batch", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")))
    assert loader.load_incremental_batch("DimCustomer", [{"a": 1}], 12)["success"] is False

    assert loader._deduplicate_data([], ["id"]) == []
    rows = [{"id": 1, "v": "a"}, {"id": 1, "v": "b"}, {"id": 2, "v": "c"}]
    deduped = loader._deduplicate_data(rows, ["id"])
    assert len(deduped) == 2
    assert loader._deduplicate_data(rows, []) == rows


