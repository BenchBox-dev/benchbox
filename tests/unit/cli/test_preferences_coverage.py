from __future__ import annotations

import importlib
from datetime import datetime, timedelta
from pathlib import Path

import pytest

prefs = importlib.import_module("benchbox.cli.preferences")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

NOW = datetime(2026, 1, 15, 12, 0, 0)


@pytest.fixture(autouse=True)
def _preferences_wallclock(monkeypatch: pytest.MonkeyPatch) -> None:

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is None else NOW.replace(tzinfo=tz)

    monkeypatch.setattr(prefs, "datetime", FixedDatetime)


def test_safe_yaml_load_valid_and_invalid_types(tmp_path: Path) -> None:
    p = tmp_path / "ok.yaml"
    p.write_text("a: 1\n", encoding="utf-8")
    assert prefs._safe_yaml_load(p) == {"a": 1}

    bad = tmp_path / "bad.yaml"
    bad.write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Expected dictionary"):
        prefs._safe_yaml_load(bad)


def test_safe_yaml_load_rejects_large_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p = tmp_path / "big.yaml"
    p.write_text("a: 1\n", encoding="utf-8")

    class _Stat:
        st_size = prefs.MAX_YAML_SIZE_BYTES + 1

    monkeypatch.setattr(Path, "stat", lambda _self: _Stat())
    with pytest.raises(ValueError, match="too large"):
        prefs._safe_yaml_load(p)


def test_last_run_save_load_clear_cycle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(prefs, "get_preferences_dir", lambda: tmp_path)

    prefs.save_last_run_config(
        database="duckdb",
        benchmark="tpch",
        scale=0.01,
        tuning_mode="tuned",
        phases=["load", "power"],
        queries=["Q1", "Q6"],
        mode="dataframe",
        iterations=5,
        non_replayable_options=["force", "platform_options", "force"],
        additional_options={"x_opt": "1", "table_mode": "external"},
    )

    loaded = prefs.load_last_run_config()
    assert loaded is not None
    assert loaded["database"] == "duckdb"
    assert loaded["x_opt"] == "1"
    assert loaded["table_mode"] == "external"
    assert loaded["queries"] == ["Q1", "Q6"]
    assert loaded["mode"] == "dataframe"
    assert loaded["iterations"] == 5
    assert loaded["replay_schema_version"] == 1
    assert loaded["non_replayable_options"] == ["force", "platform_options"]

    prefs.clear_last_run_config()
    assert prefs.load_last_run_config() is None


def test_last_run_load_invalid_structure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(prefs, "get_preferences_dir", lambda: tmp_path)
    (tmp_path / "last_run.yaml").write_text("foo: bar\n", encoding="utf-8")
    assert prefs.load_last_run_config() is None


def test_safe_tuning_path_checks_extension_and_bounds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    examples = cwd / "examples"
    outside = tmp_path / "outside"
    cwd.mkdir()
    examples.mkdir()
    outside.mkdir()

    in_cwd = cwd / "cfg.yaml"
    in_cwd.write_text("x: 1\n", encoding="utf-8")
    in_examples = examples / "cfg.yml"
    in_examples.write_text("x: 1\n", encoding="utf-8")
    out = outside / "cfg.yaml"
    out.write_text("x: 1\n", encoding="utf-8")

    monkeypatch.setattr(Path, "cwd", lambda: cwd)

    assert prefs._is_safe_tuning_path(str(in_cwd)) is True
    assert prefs._is_safe_tuning_path(str(in_examples)) is True
    assert prefs._is_safe_tuning_path(str(out)) is False
    assert prefs._is_safe_tuning_path(str(cwd / "cfg.txt")) is False


def test_format_last_run_summary_paths_and_age(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tune = tmp_path / "custom.yaml"
    tune.write_text("x: 1\n", encoding="utf-8")

    monkeypatch.setattr(prefs, "_is_safe_tuning_path", lambda p: p == str(tune))

    config = {
        "benchmark": "tpch",
        "database": "duckdb",
        "scale": 0.1,
        "tuning_mode": str(tune),
        "phases": ["load", "power"],
        "table_mode": "external",
        "concurrency": 4,
        "iterations": 5,
        "timestamp": (NOW - timedelta(minutes=2)).isoformat(),
    }
    out = prefs.format_last_run_summary(config)
    assert "TPCH on DUCKDB" in out
    assert "custom config" in out
    assert "tables: external" in out
    assert "4 streams" in out
    assert "5 power iterations" in out


def test_favorites_crud(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(prefs, "get_preferences_dir", lambda: tmp_path)

    prefs.save_favorite_config(
        name="quick",
        database="duckdb",
        benchmark="tpch",
        scale=0.01,
        tuning_mode="tuned",
        description="quick run",
    )

    fav = prefs.load_favorite_config("quick")
    assert fav is not None
    assert fav["benchmark"] == "tpch"

    all_favs = prefs.list_favorite_configs()
    assert "quick" in all_favs

    assert prefs.delete_favorite_config("quick") is True
    assert prefs.delete_favorite_config("missing") is False


