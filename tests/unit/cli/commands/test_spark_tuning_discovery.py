from __future__ import annotations

import logging
import sys as _sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner
from rich.console import Console

from benchbox.cli.commands.tuning_group import tuning_group
from benchbox.cli.config import SPARK_SESSION_CONFIG_EXAMPLE, ConfigManager
from benchbox.cli.dryrun import DryRunDisplay
from benchbox.core.tuning.applied_ledger import AppliedTuningLedger
from benchbox.core.tuning.capability_registry import (
    PLATFORM_ALIASES,
    SESSION_CONFIG_CAPABILITIES,
    get_capability,
    resolve_platform_key,
)
from benchbox.core.tuning.interface import (
    TableTuning,
    TuningColumn,
    TuningType,
    UnifiedTuningConfiguration,
)

__import__("benchbox.cli.commands.tuning_group")
_tuning_group_module = _sys.modules["benchbox.cli.commands.tuning_group"]

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _recorder() -> Console:
    return Console(record=True, width=400)


def test_spark_has_its_own_registry_row():
    assert "spark" not in PLATFORM_ALIASES
    assert resolve_platform_key("spark") == "spark"
    assert get_capability("spark", TuningType.LIQUID_CLUSTERING) is None
    assert get_capability("spark", TuningType.Z_ORDERING) is None
    assert get_capability("spark", TuningType.SORTING).mechanism_id == "spark_delta_optimize_zorder"
    assert resolve_platform_key("delta") == "databricks"


def test_spark_stays_out_of_the_known_platform_set():
    assert not TuningType.is_known_platform("spark")


def test_session_config_platforms_match_the_registry():
    assert set(SESSION_CONFIG_CAPABILITIES) == set(UnifiedTuningConfiguration._SPARK_SESSION_CONFIG_PLATFORMS)
    assert {capability.rendered_via for capability in SESSION_CONFIG_CAPABILITIES.values()} == {"session"}


def test_tuning_platforms_lists_spark_with_session_config():
    recorder = _recorder()
    with patch.object(_tuning_group_module, "console", recorder):
        result = CliRunner().invoke(tuning_group, ["platforms"], obj={"config": ConfigManager()})

    assert result.exit_code == 0
    rows = {line.split("│")[1].strip(): line for line in recorder.export_text().splitlines() if line.count("│") >= 3}
    assert "spark session config" in rows["spark"]
    assert "sorting" in rows["spark"]
    assert "liquid_clustering" not in rows["spark"]
    assert "spark session config" in rows["velox"]


def test_custom_spark_sorting_file_still_loads(tmp_path: Path):
    config = UnifiedTuningConfiguration()
    config.disable_all_constraints()
    config.table_tunings["lineitem"] = TableTuning(
        table_name="lineitem", sorting=[TuningColumn(name="l_shipdate", type="DATE", order=1)]
    )
    path = tmp_path / "spark_custom.yaml"
    path.write_text(yaml.safe_dump(config.to_dict()), encoding="utf-8")

    loaded = ConfigManager().load_unified_tuning_config(path, "spark")

    assert loaded.table_tunings["lineitem"].sorting[0].name == "l_shipdate"


@pytest.mark.parametrize("platform", ["spark", "velox"])
def test_tuning_init_writes_a_commented_spark_map_example(tmp_path: Path, platform: str):
    target = tmp_path / f"{platform}.yaml"
    with patch.object(_tuning_group_module, "console", _recorder()):
        result = CliRunner().invoke(
            tuning_group, ["init", "--platform", platform, "--output", str(target)], obj={"config": ConfigManager()}
        )

    assert result.exit_code == 0, result.output
    text = target.read_text(encoding="utf-8")
    assert text.endswith(SPARK_SESSION_CONFIG_EXAMPLE)
    loaded = UnifiedTuningConfiguration.from_dict(yaml.safe_load(text))
    assert loaded.platform_optimizations.spark == {}
    errors, _warnings = loaded.validate_for_platform_detailed(platform)
    assert errors == []
    example = yaml.safe_load(
        "\n".join(line[1:] for line in SPARK_SESSION_CONFIG_EXAMPLE.splitlines() if line.startswith("#   "))
    )
    applied = UnifiedTuningConfiguration.from_dict({"platform_optimizations": example})
    assert applied.platform_optimizations.spark["spark.sql.shuffle.partitions"] == "200"


def test_tuning_init_skips_the_example_on_other_platforms(tmp_path: Path):
    target = tmp_path / "duckdb.yaml"
    with patch.object(_tuning_group_module, "console", _recorder()):
        CliRunner().invoke(
            tuning_group, ["init", "--platform", "duckdb", "--output", str(target)], obj={"config": ConfigManager()}
        )

    assert "spark.conf.set" not in target.read_text(encoding="utf-8")


def _spark_file(tmp_path: Path) -> Path:
    path = tmp_path / "spark_map.yaml"
    path.write_text(
        yaml.safe_dump({"platform_optimizations": {"spark": {"spark.sql.shuffle.partitions": "64"}}}),
        encoding="utf-8",
    )
    return path


def test_tuning_validate_renders_the_spark_map_as_a_table(tmp_path: Path):
    recorder = _recorder()
    with patch.object(_tuning_group_module, "console", recorder):
        result = CliRunner().invoke(
            tuning_group,
            ["validate", str(_spark_file(tmp_path)), "--platform", "spark"],
            obj={"config": ConfigManager()},
        )

    assert result.exit_code == 0, result.output
    text = recorder.export_text()
    assert "Spark Session Config" in text
    assert "spark.sql.shuffle.partitions" in text and "64" in text
    assert "{'spark" not in text


def test_tuning_show_renders_the_spark_map_as_a_table(tmp_path: Path):
    recorder = _recorder()
    with patch.object(_tuning_group_module, "console", recorder):
        result = CliRunner().invoke(
            tuning_group,
            ["show", str(_spark_file(tmp_path)), "--platform", "spark"],
            obj={"config": ConfigManager()},
        )

    assert result.exit_code == 0, result.output
    text = recorder.export_text()
    assert "Spark Session Config" in text
    assert "spark.sql.shuffle.partitions" in text


def test_dry_run_renders_the_spark_map_as_a_table():
    recorder = _recorder()
    config = UnifiedTuningConfiguration.from_dict(
        {"platform_optimizations": {"spark": {"spark.sql.shuffle.partitions": "64"}}}
    )

    DryRunDisplay(console=recorder)._display_tuning_config(
        {"platform_optimizations": config.to_dict()["platform_optimizations"]}
    )

    text = recorder.export_text()
    assert "Spark Session Config" in text
    assert "spark.sql.shuffle.partitions" in text
    assert "{'spark" not in text


def test_spark_map_on_a_platform_that_ignores_it_is_warned_and_dropped(caplog):
    from benchbox.platforms.duckdb import DuckDBAdapter

    config = UnifiedTuningConfiguration.from_dict(
        {"platform_optimizations": {"spark": {"sql.shuffle.partitions": "64"}}}
    )
    _errors, warnings = config.validate_for_platform_detailed("databricks")
    assert any("not applied by platform" in message for message in warnings)

    adapter = DuckDBAdapter(tuning_enabled=True)
    adapter._applied_tuning_ledger = AppliedTuningLedger()
    with caplog.at_level(logging.WARNING):
        adapter._drop_unhandled_spark_session_config(config)

    assert [item.intent for item in adapter._applied_tuning_ledger.dropped] == [
        "spark_session_config:spark.sql.shuffle.partitions"
    ]
    assert "does not apply the spark session-config map" in caplog.text


def test_spark_family_adapters_do_not_double_record():
    from benchbox.platforms._spark_helpers import SparkLikeAdapterMixin

    assert SparkLikeAdapterMixin.handles_spark_session_config is True
