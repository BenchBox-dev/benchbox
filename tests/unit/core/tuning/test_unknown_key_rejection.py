from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import pytest
import yaml

from benchbox.cli.config import ConfigManager
from benchbox.cli.tuning_resolver import DATAFRAME_TUNING_KEYS
from benchbox.core.dataframe.tuning.interface import DataFrameTuningConfiguration
from benchbox.core.tuning.interface import UnifiedTuningConfiguration

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[4]


def _shipped_sql_tuning_files() -> list[Path]:
    tracked = subprocess.run(
        ["git", "ls-files", "*.yaml", "*.yml"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    files = []
    for name in tracked:
        if "tuning" not in name.lower():
            continue
        path = REPO_ROOT / name
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "table_tunings" in data:
            files.append(path)
    return files


def test_table_tuning_typo_fails_naming_key_and_suggestion():
    data = {"table_tuning": {"lineitem": {"table_name": "lineitem", "sorting": []}}}
    with pytest.raises(ValueError) as exc:
        UnifiedTuningConfiguration.from_dict(data)
    message = str(exc.value)
    assert "'table_tuning'" in message
    assert "did you mean 'table_tunings'" in message
    assert "Valid keys:" in message
    assert "platform_optimizations" in message


def test_unknown_key_without_close_match_lists_valid_keys():
    with pytest.raises(ValueError, match="'zzz_qqq'.*Valid keys:"):
        UnifiedTuningConfiguration.from_dict({"zzz_qqq": {}})


@pytest.mark.parametrize(
    ("section", "bad_key"),
    [
        ("primary_keys", "enabld"),
        ("foreign_keys", "enabld"),
        ("unique_constraints", "enabld"),
        ("check_constraints", "enabld"),
        ("platform_optimizations", "z_ordering_enabld"),
    ],
)
def test_unknown_key_inside_known_section_fails(section, bad_key):
    with pytest.raises(ValueError, match=bad_key):
        UnifiedTuningConfiguration.from_dict({section: {bad_key: True}})


def test_unknown_key_inside_table_tuning_and_column_fails():
    with pytest.raises(ValueError, match="'sortng'.*did you mean 'sorting'"):
        UnifiedTuningConfiguration.from_dict({"table_tunings": {"t": {"table_name": "t", "sortng": []}}})
    column = {"name": "a", "type": "INTEGER", "order": 1, "sort_ordr": "DESC"}
    with pytest.raises(ValueError, match="sort_ordr"):
        UnifiedTuningConfiguration.from_dict({"table_tunings": {"t": {"table_name": "t", "sorting": [column]}}})


def test_metadata_key_and_round_trip_still_load():
    config = UnifiedTuningConfiguration.from_dict({"_metadata": {"version": "2.0"}, "primary_keys": {"enabled": True}})
    restored = UnifiedTuningConfiguration.from_dict(config.to_dict())
    assert restored.to_dict() == config.to_dict()


def test_config_manager_load_surfaces_unknown_key_error(tmp_path):
    path = tmp_path / "typo.yaml"
    path.write_text(yaml.safe_dump({"primary_keys": {"enabled": True}, "table_tuning": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="table_tuning.*table_tunings"):
        ConfigManager().load_unified_tuning_config(path)


def test_all_shipped_templates_load_under_strict_rule():
    files = _shipped_sql_tuning_files()
    assert len(files) > 10
    for path in files:
        UnifiedTuningConfiguration.from_dict(yaml.safe_load(path.read_text(encoding="utf-8")))


def test_config_manager_load_accepts_dataframe_tuning_file(tmp_path):
    path = tmp_path / "df.yaml"
    path.write_text(
        yaml.safe_dump({"execution": {"engine_affinity": "streaming"}, "_metadata": {"format": "dataframe_tuning"}}),
        encoding="utf-8",
    )
    config = ConfigManager().load_unified_tuning_config(path)
    assert config.table_tunings == {}


@pytest.mark.parametrize(
    "section",
    sorted(f.name for f in dataclasses.fields(DataFrameTuningConfiguration) if f.name != "metadata"),
)
def test_config_manager_load_accepts_any_single_dataframe_section_file(tmp_path, section):
    path = tmp_path / f"{section}.yaml"
    path.write_text(yaml.safe_dump({section: {}}), encoding="utf-8")
    config = ConfigManager().load_unified_tuning_config(path)
    assert config.table_tunings == {}


def test_dataframe_section_keys_cover_write():
    assert "write" in DATAFRAME_TUNING_KEYS


def test_sql_file_typo_still_fails_next_to_valid_sections(tmp_path):
    path = tmp_path / "sql.yaml"
    path.write_text(yaml.safe_dump({"primary_keys": {"enabled": True}, "writes": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="writes"):
        ConfigManager().load_unified_tuning_config(path)
