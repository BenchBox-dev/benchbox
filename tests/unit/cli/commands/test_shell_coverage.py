from __future__ import annotations

import importlib
import sys
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

mod = importlib.import_module("benchbox.cli.commands.shell")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_platform_extension_mapping() -> None:
    assert mod._get_platform_from_extension(".duckdb") == "duckdb"
    assert mod._get_platform_from_extension(".sqlite") == "sqlite"
    assert mod._get_platform_from_extension(".unknown") == "unknown"


def test_discover_and_filter_databases(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    base = tmp_path / "benchmark_runs"
    datagen = base / "datagen"
    dbdir = base / "databases"
    datagen.mkdir(parents=True)
    dbdir.mkdir(parents=True)
    (datagen / "tpch_sf1_tuned.duckdb").write_text("x", encoding="utf-8")
    (dbdir / "tpch_sf1_none.sqlite").write_text("x", encoding="utf-8")

    class _DM:
        def __init__(self, base_dir=None):
            self.base_dir = Path(base_dir) if base_dir else base
            self.datagen_dir = datagen
            self.databases_dir = dbdir

    monkeypatch.setattr(mod, "DirectoryManager", _DM)
    monkeypatch.setattr(
        mod,
        "parse_database_name",
        lambda name: {"benchmark": "tpch", "scale_factor": 1.0, "tuning_mode": "tuned", "constraints": "none"},
    )

    found = mod.discover_local_databases(base)
    assert len(found) >= 2

    filtered = mod.filter_databases(found, platform="duckdb", benchmark="tpch", scale=1.0)
    assert all(d["platform"] == "duckdb" for d in filtered)


def test_table_and_selection_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    dbs = [
        {
            "path": Path("a.duckdb"),
            "platform": "duckdb",
            "benchmark": "tpch",
            "scale": 1.0,
            "tuning": "tuned",
            "size_mb": 1.2,
            "modified": datetime.now(),
        },
        {
            "path": Path("b.sqlite"),
            "platform": "sqlite",
            "benchmark": "tpch",
            "scale": 1.0,
            "tuning": "none",
            "size_mb": 0.8,
            "modified": datetime.now(),
        },
    ]

    calls = []
    monkeypatch.setattr(mod.console, "print", lambda *a, **k: calls.append(a[0] if a else ""))
    mod.display_database_table(dbs)
    assert calls

    monkeypatch.setattr(mod.Prompt, "ask", lambda *_a, **_k: "2")
    sel = mod.select_database_interactive(dbs)
    assert sel["platform"] == "sqlite"

    monkeypatch.setattr(mod.Prompt, "ask", lambda *_a, **_k: "x")
    assert mod.select_database_interactive(dbs) is None


def test_shell_command_routing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = tmp_path / "x.duckdb"
    db.write_text("x", encoding="utf-8")
    sqlite = tmp_path / "x.sqlite"
    sqlite.write_text("x", encoding="utf-8")

    calls = {}
    monkeypatch.setattr(mod, "_launch_duckdb_shell", lambda p: calls.setdefault("duckdb", p))
    monkeypatch.setattr(mod, "_launch_sqlite_shell", lambda p: calls.setdefault("sqlite", p))
    monkeypatch.setattr(mod, "_launch_clickhouse_shell", lambda *a, **k: calls.setdefault("ch", True))

    assert CliRunner().invoke(mod.shell, ["--database", str(db)]).exit_code == 0
    assert calls["duckdb"] == str(db)
    assert CliRunner().invoke(mod.shell, ["--database", str(sqlite)]).exit_code == 0
    assert calls["sqlite"] == str(sqlite)
    assert CliRunner().invoke(mod.shell, ["--platform", "clickhouse"]).exit_code == 0
    assert calls["ch"] is True


def test_shell_discovery_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    selected = {
        "path": tmp_path / "a.duckdb",
        "platform": "duckdb",
        "benchmark": "tpch",
        "scale": 1.0,
        "tuning": "none",
        "size_mb": 1.0,
        "modified": datetime.now(),
    }
    selected["path"].write_text("x", encoding="utf-8")

    monkeypatch.setattr(mod, "discover_local_databases", lambda *_a, **_k: [selected])
    monkeypatch.setattr(mod, "filter_databases", lambda dbs, *_a, **_k: dbs)
    monkeypatch.setattr(mod, "_launch_duckdb_shell", lambda _p: None)

    assert CliRunner().invoke(mod.shell, ["--list"]).exit_code == 0

    assert CliRunner().invoke(mod.shell, ["--last"]).exit_code == 0

    monkeypatch.setattr(mod, "select_database_interactive", lambda _dbs: None)
    canceled = CliRunner().invoke(mod.shell, [])
    assert canceled.exit_code == 0


def test_duckdb_sqlite_launch_and_info_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _DuckConn:
        def __init__(self):
            self.description = [("c1",)]

        def execute(self, q):
            self._q = q
            return self

        def fetchall(self):
            if "SHOW TABLES" in getattr(self, "_q", ""):
                return [("t1",)]
            if "DESCRIBE" in getattr(self, "_q", ""):
                return [("id", "INTEGER")]
            return [(1,)]

        def fetchone(self):
            return (1,)

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "duckdb", type("D", (), {"connect": lambda *a, **k: _DuckConn()})())
    answers = iter([".tables", ".schema t1", ".info", "select 1", ".quit"])
    monkeypatch.setattr("builtins.input", lambda _p="": next(answers))
    mod._launch_duckdb_shell(str(tmp_path / "x.duckdb"))

    class _Cursor:
        description = [("c1",)]

        def execute(self, q):
            self._q = q
            return self

        def fetchall(self):
            if "sqlite_master" in getattr(self, "_q", ""):
                return [("t1",)]
            if "PRAGMA" in getattr(self, "_q", ""):
                return [(0, "id", "INTEGER")]
            return [(1,)]

        def fetchone(self):
            return (1,)

    class _SqlConn:
        row_factory = None

        def cursor(self):
            return _Cursor()

        def commit(self):
            pass

        def close(self):
            pass

    fake_sqlite = type("S", (), {"connect": lambda *_a, **_k: _SqlConn(), "Row": object})
    monkeypatch.setitem(sys.modules, "sqlite3", fake_sqlite)
    db = tmp_path / "x.db"
    db.write_text("x", encoding="utf-8")
    answers2 = iter([".tables", ".schema t1", ".info", "select 1", ".quit"])
    monkeypatch.setattr("builtins.input", lambda _p="": next(answers2))
    mod._launch_sqlite_shell(str(db))

    mod._display_database_info_duckdb(_DuckConn(), str(db))
    mod._show_tables_duckdb(_DuckConn())
    mod._show_schema_duckdb(_DuckConn(), None)

    cur = _Cursor()
    mod._show_tables_sqlite(cur)
    mod._show_schema_sqlite(cur, None)
    mod._display_database_info_sqlite(_SqlConn(), db)


