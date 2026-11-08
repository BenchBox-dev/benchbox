#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path


def _import_engine() -> dict[str, object]:
    from importlib.metadata import version as dist_version

    import chdb

    return {
        "chdb_module_version": getattr(chdb, "__version__", None),
        "chdb_dist_version": dist_version("chdb"),
        "chdb_core_dist_version": dist_version("chdb-core"),
    }


def _timestamp_probe() -> dict[str, object]:
    import chdb

    connection = chdb.connect(":memory:")
    try:
        connection.query(
            "CREATE TABLE ts_probe (created_at TIMESTAMP NOT NULL) ENGINE = MergeTree() ORDER BY created_at"
        )
        connection.query("INSERT INTO ts_probe VALUES ('2026-11-07 12:34:56')")
        read_back = str(connection.query("SELECT toString(created_at) FROM ts_probe", "CSV")).strip().strip('"')
        column_type = str(connection.query("SELECT type FROM system.columns WHERE table = 'ts_probe'", "CSV")).strip()
    finally:
        connection.close()
    return {"timestamp_column_type": column_type.strip('"'), "timestamp_read_back": read_back}


def _metadata_round_trip(workdir: Path) -> dict[str, object]:
    from benchbox.core.tuning.interface import TableTuning, TuningColumn, UnifiedTuningConfiguration
    from benchbox.core.tuning.metadata import TuningMetadataManager
    from benchbox.platforms.clickhouse_local import ClickHouseLocalAdapter

    adapter = ClickHouseLocalAdapter(database_path=str(workdir / "store"))
    connection = adapter.create_connection()
    try:
        config = UnifiedTuningConfiguration()
        config.table_tunings["lineitem"] = TableTuning(
            table_name="lineitem",
            sorting=[TuningColumn(name="l_orderkey", type="INTEGER", order=1)],
        )
        manager = TuningMetadataManager(adapter, connection=connection)
        started = time.monotonic()
        created = manager.create_metadata_table()
        marker_written = manager.write_tuned_run_marker()
        saved = manager.save_unified_tunings(config)
        loaded = manager.load_unified_tunings()
        marker_present = manager.has_tuned_run_marker()
        elapsed = time.monotonic() - started
        stored = connection.execute(f"SELECT toString(created_at) FROM {manager._metadata_table_name}").fetchall()
        stamps = sorted({str(row[0]) for row in stored})
        parsed = [datetime.fromisoformat(stamp) for stamp in stamps]
    finally:
        adapter.close_connection(connection)
    return {
        "metadata_table_created": bool(created),
        "tuned_run_marker_written": bool(marker_written),
        "tunings_saved": bool(saved),
        "tunings_loaded": loaded is not None and "lineitem" in loaded.table_tunings,
        "tuned_run_marker_present": bool(marker_present),
        "metadata_rows": len(stored),
        "created_at_values_parse": len(parsed) == len(stamps) and len(parsed) > 0,
        "metadata_round_trip_seconds": round(elapsed, 3),
    }


def main() -> int:
    report: dict[str, object] = {"python": sys.version.split()[0], "platform": sys.platform}
    try:
        report.update(_import_engine())
        report.update(_timestamp_probe())
        with tempfile.TemporaryDirectory(prefix="chdb-verify-") as tmp:
            report.update(_metadata_round_trip(Path(tmp)))
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}".splitlines()[0]
        print(json.dumps(report, sort_keys=True))
        return 1
    report["timestamp_ok"] = (
        report["timestamp_read_back"] == "2026-11-07 12:34:56" and report["created_at_values_parse"]
    )
    report["metadata_ok"] = all(
        report[key]
        for key in (
            "metadata_table_created",
            "tuned_run_marker_written",
            "tunings_saved",
            "tunings_loaded",
            "tuned_run_marker_present",
        )
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["timestamp_ok"] and report["metadata_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
