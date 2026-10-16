from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

cfg = importlib.import_module("benchbox.cli.config")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_environment_overrides_apply_and_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHBOX_DATABASE_PREFERRED", "duckdb")
    monkeypatch.setenv("BENCHBOX_SCALE_FACTOR", "0.5")
    monkeypatch.setenv("BENCHBOX_VERBOSE", "yes")
    monkeypatch.setenv("BENCHBOX_MAX_WORKERS", "bad")

    calls = []
    monkeypatch.setattr(cfg.console, "print", lambda *a, **k: calls.append(a[0] if a else ""))

    out = cfg._apply_environment_overrides({})
    assert out["database"]["preferred"] == "duckdb"
    assert out["benchmarks"]["default_scale"] == 0.5
    assert out["execution"]["verbose"] is True
    assert any("Invalid environment variable" in str(c) for c in calls)


def test_load_config_without_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = cfg.ConfigManager(config_path=Path("/tmp/nonexistent-benchbox-config.yaml"))
    monkeypatch.setattr(cfg, "ConfigManager", lambda config_path=None: manager)
    loaded = cfg.load_config(cli_args={"database": {"preferred": "sqlite"}}, validate=False)
    assert loaded.database["preferred"] == "sqlite"


def test_load_config_precedence_cli_over_env_over_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "benchbox.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "database": {"preferred": "sqlite"},
                "benchmarks": {"default_scale": 0.25},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BENCHBOX_DATABASE_PREFERRED", "duckdb")

    loaded = cfg.load_config(
        cli_args={"database": {"preferred": "clickhouse"}},
        config_file=config_path,
        validate=False,
    )

    assert loaded.database["preferred"] == "clickhouse"
    assert loaded.benchmarks["default_scale"] == 0.25


def test_load_config_raises_when_validation_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "benchbox.yaml"
    config_path.write_text(yaml.safe_dump({"database": {"preferred": "duckdb"}}), encoding="utf-8")
    monkeypatch.setattr(cfg.ConfigManager, "validate_config", lambda self: False)

    with pytest.raises(ValueError, match="Configuration validation failed"):
        cfg.load_config(config_file=config_path, validate=True)


def test_directory_manager_honors_output_dir_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    custom_root = tmp_path / "custom_runs"
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(custom_root))
    dm = cfg.DirectoryManager()
    assert dm.base_dir == custom_root
    assert dm.results_dir == custom_root / "results"
    assert dm.datagen_dir == custom_root / "datagen"
    assert dm.databases_dir == custom_root / "databases"


def test_directory_manager_paths_and_cleanup(tmp_path: Path) -> None:
    dm = cfg.DirectoryManager(base_dir=str(tmp_path / "runs"))
    assert dm.results_dir.exists() and dm.datagen_dir.exists() and dm.databases_dir.exists()

    out = dm.get_result_path("tpch", 1.0, "duckdb", "2026-02-09T10:00:00", "abc")
    assert out.parent == dm.results_dir
    db = dm.get_database_path("tpch", 1.0, "duckdb")
    assert db.parent == dm.databases_dir
    dg = dm.get_datagen_path("tpch", 1.0)
    assert "tpch_sf1" in str(dg)

    old_file = dm.results_dir / "old.json"
    old_file.write_text("{}", encoding="utf-8")

    import os as _os

    _os.utime(old_file, (0, 0))
    cleaned = dm.clean_old_files(max_age_days=0)
    assert str(old_file) in cleaned

    listing = dm.list_files()
    assert "results" in listing and "databases" in listing and "datagen" in listing
    sizes = dm.get_directory_sizes()
    assert "total" in sizes


def test_tuning_config_parse_save_and_validate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mgr = cfg.ConfigManager(config_path=tmp_path / "none.yaml")

    tuning_path = tmp_path / "tuning.yaml"
    tuning_path.write_text(
        """
tpch:
  orders:
    partitioning:
      - order_date
    sorting:
      - name: orderkey
        type: INTEGER
        order: 1
""",
        encoding="utf-8",
    )
    tunings = mgr.load_tuning_config(tuning_path)
    assert "tpch" in tunings

    out_path = tmp_path / "saved.yaml"
    mgr.save_tuning_config(tunings, out_path, format="yaml")
    assert out_path.exists()

    with pytest.raises(ValueError):
        mgr._parse_table_tuning("orders", {"partitioning": "not-a-list"})

    u = cfg.UnifiedTuningConfiguration()
    u.primary_keys.enabled = True
    u.foreign_keys.enabled = True
    u.unique_constraints.enabled = False
    u.check_constraints.enabled = False
    unified = tmp_path / "unified.yaml"
    mgr.save_unified_tuning_config(u, unified, format="yaml")
    loaded = mgr.load_unified_tuning_config(unified, platform="duckdb")
    assert loaded.primary_keys.enabled is True

    bare = cfg.UnifiedTuningConfiguration()
    bare_path = tmp_path / "bare.yaml"
    mgr.save_unified_tuning_config(bare, bare_path, format="yaml")
    with pytest.raises(ValueError):
        mgr.load_unified_tuning_config(bare_path, platform="duckdb")


def test_show_config_prints_path_and_yaml(tmp_path: Path) -> None:
    mgr = cfg.ConfigManager(config_path=tmp_path / "config.yaml")

    with patch.object(cfg.console, "print") as mock_print:
        mgr.show_config()

    printed = [call.args[0] for call in mock_print.call_args_list if call.args]
    assert any("Current Configuration" in str(item) for item in printed)
    assert any(str(mgr.config_path) in str(item) for item in printed)


def test_create_sample_config_writes_comments(tmp_path: Path) -> None:
    mgr = cfg.ConfigManager(config_path=tmp_path / "config.yaml")
    sample_path = tmp_path / "sample.yaml"

    with patch.object(cfg.console, "print") as mock_print:
        mgr.create_sample_config(sample_path)

    content = yaml.safe_load(sample_path.read_text(encoding="utf-8"))
    assert "_comments" in content
    assert content["database"]["preferred"] == "duckdb"
    assert any("Sample configuration created" in str(call) for call in mock_print.call_args_list)


def test_instance_apply_environment_overrides_updates_tuning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mgr = cfg.ConfigManager(config_path=tmp_path / "config.yaml")
    mgr.set("tuning.environment_overrides", {"BENCHBOX_TUNING_ENABLED": "enabled", "BENCHBOX_TUNING_PATH": "path"})
    monkeypatch.setenv("BENCHBOX_TUNING_ENABLED", "true")
    monkeypatch.setenv("BENCHBOX_TUNING_PATH", "/tmp/tuning.yaml")

    with patch.object(cfg.console, "print") as mock_print:
        mgr.apply_environment_overrides()

    assert mgr.get("tuning.enabled") is True
    assert mgr.get("tuning.path") == "/tmp/tuning.yaml"
    assert any("Applied environment override" in str(call) for call in mock_print.call_args_list)
