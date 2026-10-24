from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest

t = importlib.import_module("benchbox.cli.tuning")
tuning_interface = importlib.import_module("benchbox.core.tuning.interface")
TuningType = tuning_interface.TuningType
UnifiedTuningConfiguration = tuning_interface.UnifiedTuningConfiguration

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _DummyWriteConfig:
    def __init__(self, **kwargs):
        self.sort_by = kwargs.get("sort_by", [])
        self.partition_by = kwargs.get("partition_by", [])
        self.row_group_size = kwargs.get("row_group_size")
        self.repartition_count = kwargs.get("repartition_count")
        self.compression_level = kwargs.get("compression_level")


def _seq(values):
    it = iter(values)
    return lambda *a, **k: next(it)


def _enabled(config: UnifiedTuningConfiguration) -> set:
    return config.get_enabled_tuning_types()


def _table_slots(config: UnifiedTuningConfiguration) -> dict[str, set[str]]:
    slots: dict[str, set[str]] = {}
    for name, entry in config.table_tunings.items():
        present = {slot for slot in ("partitioning", "clustering", "distribution", "sorting") if getattr(entry, slot)}
        if present:
            slots[name] = present
    return slots


def test_recommendation_helpers() -> None:
    assert t._get_recommended_threads(8, "duckdb") == 7
    assert t._get_recommended_threads(4, "sqlite") == 1
    assert t._get_recommended_memory_limit(16.0, "duckdb") == pytest.approx(11.2)
    assert t._get_recommended_memory_limit(16.0, "bigquery") is None
    assert t._get_recommended_max_scale(4.0, "tpch") == 0.01
    assert t._get_recommended_max_scale(64.0, "tpcds") == 0.1
    assert t._get_recommended_max_scale(64.0, "clickbench") == 10.0


def test_autofill_defaults_cloud_and_local() -> None:
    profile = SimpleNamespace(cpu_cores_logical=8, memory_total_gb=32.0)
    cloud = t.autofill_defaults(profile, "databricks", benchmark="tpch")
    local = t.autofill_defaults(profile, "duckdb", benchmark="tpcds")

    assert cloud["tuning_mode"] == "tuned"
    assert cloud["enable_photon"] is True
    assert local["tuning_mode"] == "balanced"
    assert local["memory_limit_str"].endswith("GB")
    assert local["max_recommended_sf"] <= 1.0


def test_apply_defaults_to_config() -> None:
    cfg = UnifiedTuningConfiguration()
    out = t._apply_defaults_to_config(cfg, defaults={}, platform="redshift", benchmark="tpch")
    assert out.primary_keys.enabled is True
    assert TuningType.DISTRIBUTION not in _enabled(out)
    assert TuningType.SORTING not in _enabled(out)
    assert not _table_slots(out), "redshift has no packaged template so no layout may be recorded"


def test_apply_defaults_to_config_all_platforms() -> None:
    expected = {
        "snowflake": {TuningType.CLUSTERING},
        "bigquery": {TuningType.PARTITIONING},
        "redshift": set(),
    }
    for platform, types in expected.items():
        cfg = UnifiedTuningConfiguration()
        out = t._apply_defaults_to_config(cfg, defaults={}, platform=platform, benchmark="tpch")
        enabled = _enabled(out)
        layout_types = {TuningType.PARTITIONING, TuningType.CLUSTERING, TuningType.DISTRIBUTION, TuningType.SORTING}
        assert enabled & layout_types == types, f"{platform} enabled {enabled & layout_types}"
        slots = _table_slots(out)
        if types:
            assert slots, f"{platform} defaults must persist table entries"
        else:
            assert not slots, f"{platform} has no template slot so nothing persists"

    databricks_cfg = UnifiedTuningConfiguration()
    databricks_out = t._apply_defaults_to_config(databricks_cfg, defaults={}, platform="databricks")
    assert TuningType.Z_ORDERING in _enabled(databricks_out)
    assert TuningType.AUTO_OPTIMIZE in _enabled(databricks_out)


def test_enable_layout_applies_every_matching_template_table() -> None:
    cfg = UnifiedTuningConfiguration()
    cfg.enable_platform_optimization(TuningType.SORTING, benchmark="tpch")
    slots = _table_slots(cfg)
    assert len(slots) > 1, f"sorting must persist on every tuned table, got {sorted(slots)}"
    assert all("sorting" in present for present in slots.values())


@pytest.mark.parametrize("platform", ["snowflake", "databricks"])
def test_enable_clustering_uses_platform_template(platform: str) -> None:
    cfg = UnifiedTuningConfiguration()
    assert cfg.enable_platform_optimization(TuningType.CLUSTERING, benchmark="tpch", platform=platform) is True
    slots = _table_slots(cfg)
    assert TuningType.CLUSTERING in _enabled(cfg)
    assert any("clustering" in present for present in slots.values())
    assert "sorting" not in set().union(*slots.values())


def test_enable_layout_reports_missing_platform_template() -> None:
    cfg = UnifiedTuningConfiguration()
    assert cfg.enable_platform_optimization(TuningType.DISTRIBUTION, benchmark="tpch", platform="redshift") is False
    assert cfg.table_tunings == {}


def test_enable_layout_declines_benchmark_without_template() -> None:
    cfg = UnifiedTuningConfiguration()
    cfg.enable_platform_optimization(TuningType.CLUSTERING, benchmark="nyctaxi")
    assert cfg.table_tunings == {}, "unknown benchmarks must not borrow TPC-H tables"
    assert TuningType.CLUSTERING not in _enabled(cfg)


def test_run_tuning_wizard_non_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(t, "autofill_defaults", lambda *a, **k: {"threads": 4})
    seen: dict = {}

    def _apply(config, defaults, platform, benchmark="tpch"):
        seen.update({"platform": platform, "benchmark": benchmark})
        return ("applied", config, defaults, platform, benchmark)

    monkeypatch.setattr(t, "_apply_defaults_to_config", _apply)
    out = t.run_tuning_wizard("tpch", "redshift", SimpleNamespace(), interactive=False)
    assert out[0] == "applied"
    assert seen == {"platform": "redshift", "benchmark": "tpch"}


def test_run_tuning_wizard_baseline_and_simple(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(t, "autofill_defaults", lambda *a, **k: {"threads": 4})
    monkeypatch.setattr(t.console, "print", lambda *a, **k: None)
    monkeypatch.setattr(t, "_prompt_save_config", lambda *a, **k: None)

    monkeypatch.setattr(t.Prompt, "ask", _seq(["3"]))
    baseline = t.run_tuning_wizard("tpch", "duckdb", SimpleNamespace(), interactive=True)
    assert isinstance(baseline, UnifiedTuningConfiguration)
    assert baseline.primary_keys.enabled is False

    monkeypatch.setattr(t.Prompt, "ask", _seq(["1"]))
    monkeypatch.setattr(t, "_run_simple_wizard", lambda c, *_a, **_k: c)
    simple = t.run_tuning_wizard("tpch", "duckdb", SimpleNamespace(), interactive=True)
    assert isinstance(simple, UnifiedTuningConfiguration)


def test_run_tuning_wizard_advanced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(t, "autofill_defaults", lambda *a, **k: {"threads": 4})
    monkeypatch.setattr(t.console, "print", lambda *a, **k: None)
    monkeypatch.setattr(t, "_prompt_save_config", lambda *a, **k: None)
    monkeypatch.setattr(t.Prompt, "ask", _seq(["2"]))
    monkeypatch.setattr(t, "_run_advanced_wizard", lambda c, *_a, **_k: c)
    out = t.run_tuning_wizard("tpch", "snowflake", SimpleNamespace(), interactive=True)
    assert isinstance(out, UnifiedTuningConfiguration)


def test_simple_wizard_objectives_and_platform_feature_toggles(monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = {"enable_z_ordering": True, "enable_clustering": True}
    monkeypatch.setattr(t.console, "print", lambda *a, **k: None)
    monkeypatch.setattr(t, "_show_simple_summary", lambda *a, **k: None)

    monkeypatch.setattr(t.Prompt, "ask", _seq(["1"]))
    monkeypatch.setattr(t.Confirm, "ask", _seq([True, True]))
    out1 = t._run_simple_wizard(UnifiedTuningConfiguration(), defaults, "databricks", "tpch", SimpleNamespace())
    assert TuningType.Z_ORDERING in _enabled(out1)

    monkeypatch.setattr(t.Prompt, "ask", _seq(["2"]))
    out2 = t._run_simple_wizard(UnifiedTuningConfiguration(), defaults, "duckdb", "tpch", SimpleNamespace())
    assert out2.primary_keys.enabled is True
    assert out2.foreign_keys.enabled is False

    monkeypatch.setattr(t.Prompt, "ask", _seq(["3"]))
    out3 = t._run_simple_wizard(UnifiedTuningConfiguration(), defaults, "bigquery", "tpch", SimpleNamespace())
    assert out3.primary_keys.enabled is True


def test_simple_wizard_persists_table_layout_choices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(t.console, "print", lambda *a, **k: None)
    monkeypatch.setattr(t, "_show_simple_summary", lambda *a, **k: None)

    monkeypatch.setattr(t.Prompt, "ask", _seq(["3"]))
    monkeypatch.setattr(t.Confirm, "ask", _seq([True]))
    snowflake_out = t._run_simple_wizard(
        UnifiedTuningConfiguration(), {"enable_clustering": True}, "snowflake", "tpch", SimpleNamespace()
    )
    assert TuningType.CLUSTERING in _enabled(snowflake_out)
    snowflake_slots = _table_slots(snowflake_out)
    assert snowflake_slots
    assert all("clustering" in present for present in snowflake_slots.values())

    monkeypatch.setattr(t.Prompt, "ask", _seq(["3"]))
    monkeypatch.setattr(t.Confirm, "ask", _seq([True]))
    bigquery_out = t._run_simple_wizard(
        UnifiedTuningConfiguration(), {"enable_clustering": True}, "bigquery", "tpch", SimpleNamespace()
    )
    assert TuningType.PARTITIONING in _enabled(bigquery_out)
    assert TuningType.CLUSTERING not in _enabled(bigquery_out)
    bigquery_slots = _table_slots(bigquery_out)
    assert any("partitioning" in present for present in bigquery_slots.values())

    monkeypatch.setattr(t.Prompt, "ask", _seq(["3"]))
    monkeypatch.setattr(t.Confirm, "ask", _seq([True]))
    redshift_out = t._run_simple_wizard(UnifiedTuningConfiguration(), {}, "redshift", "tpch", SimpleNamespace())
    assert TuningType.DISTRIBUTION not in _enabled(redshift_out)
    assert TuningType.SORTING not in _enabled(redshift_out)
    assert not _table_slots(redshift_out)


def test_wizard_messages_match_what_was_written(monkeypatch: pytest.MonkeyPatch) -> None:
    printed: list[str] = []
    monkeypatch.setattr(t.console, "print", lambda *a, **k: printed.append(str(a[0]) if a else ""))

    monkeypatch.setattr(t.Confirm, "ask", _seq([True]))
    snowflake_cfg = UnifiedTuningConfiguration()
    t._configure_snowflake_optimizations(snowflake_cfg)
    assert TuningType.CLUSTERING in _enabled(snowflake_cfg)
    assert any("Clustering keys enabled" in line for line in printed)

    printed.clear()
    monkeypatch.setattr(t.Confirm, "ask", _seq([True, True]))
    redshift_cfg = UnifiedTuningConfiguration()
    t._configure_redshift_optimizations(redshift_cfg)
    assert not redshift_cfg.table_tunings
    assert not any(" enabled" in line and "not enabled" not in line for line in printed)
    assert sum("not enabled" in line for line in printed) == 2


def test_advanced_wizard_and_platform_specific_configurators(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = UnifiedTuningConfiguration()
    monkeypatch.setattr(t.console, "print", lambda *a, **k: None)
    monkeypatch.setattr(t.Confirm, "ask", _seq([True, False, True, True]))
    monkeypatch.setattr(
        t, "_configure_databricks_optimizations", lambda c: c.enable_platform_optimization(TuningType.Z_ORDERING)
    )
    monkeypatch.setattr(t, "render_tuning_summary", lambda *a, **k: None)
    out = t._run_advanced_wizard(cfg, {}, "databricks", "tpch", SimpleNamespace())
    assert out.primary_keys.enabled is True
    assert out.unique_constraints.enabled is True
    assert TuningType.Z_ORDERING in _enabled(out)

    cfg2 = UnifiedTuningConfiguration()
    monkeypatch.setattr(t.Confirm, "ask", _seq([True, True, False, False]))
    monkeypatch.setattr(
        t,
        "_configure_snowflake_optimizations",
        lambda c, b="tpch": c.enable_platform_optimization(TuningType.CLUSTERING, platform="snowflake"),
    )
    out2 = t._run_advanced_wizard(cfg2, {}, "snowflake", "tpch", SimpleNamespace())
    assert TuningType.CLUSTERING in _enabled(out2)
    assert any("clustering" in present for present in _table_slots(out2).values())


def test_platform_config_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(t.console, "print", lambda *a, **k: None)
    monkeypatch.setattr(
        t.Confirm,
        "ask",
        _seq([True, True, True, True, True, True, True, True, True, True, True, True]),
    )
    cfg = UnifiedTuningConfiguration()
    t._configure_databricks_optimizations(cfg)
    t._configure_snowflake_optimizations(cfg)
    t._configure_bigquery_optimizations(cfg)
    t._configure_redshift_optimizations(cfg)
    t._configure_duckdb_optimizations(cfg, {"memory_limit_str": "8GB", "threads": 4})
    t._configure_clickhouse_optimizations(cfg)
    enabled = _enabled(cfg)
    assert TuningType.Z_ORDERING in enabled
    assert TuningType.PARTITIONING in enabled
    assert TuningType.SORTING in enabled
    assert TuningType.CLUSTERING in enabled
    assert TuningType.DISTRIBUTION not in enabled
    slots = _table_slots(cfg)
    assert slots, "platform configurators must persist table entries"
    for tuning_type in (
        TuningType.PARTITIONING,
        TuningType.SORTING,
        TuningType.CLUSTERING,
    ):
        assert any(tuning_type.value in present for present in slots.values()), (
            f"{tuning_type} choice was not persisted to table_tunings"
        )


def test_render_summary_and_simple_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = UnifiedTuningConfiguration()
    cfg.enable_all_constraints()
    cfg.enable_platform_optimization(TuningType.CLUSTERING)
    monkeypatch.setattr(t.console, "print", lambda *a, **k: None)
    tbl = t.render_tuning_summary(cfg, "snowflake")
    assert tbl.row_count > 0
    t._show_simple_summary(cfg, {}, "snowflake")


def test_prompt_save_config_success_and_failure(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    cfg = UnifiedTuningConfiguration()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(t.console, "print", lambda *a, **k: None)

    monkeypatch.setattr(t.Confirm, "ask", _seq([False]))
    t._prompt_save_config(cfg, "duckdb", "tpch")

    monkeypatch.setattr(t.Confirm, "ask", _seq([True]))
    monkeypatch.setattr(t.Prompt, "ask", _seq([str(tmp_path / "x.yaml")]))
    monkeypatch.setattr(
        "benchbox.core.config_utils.save_config_file", lambda d, p, f: p.write_text("ok\n", encoding="utf-8")
    )
    t._prompt_save_config(cfg, "duckdb", "tpch")
    assert (tmp_path / "x.yaml").exists()

    monkeypatch.setattr(t.Confirm, "ask", _seq([True]))
    monkeypatch.setattr(t.Prompt, "ask", _seq([str(tmp_path / "y.yaml")]))
    monkeypatch.setattr(
        "benchbox.core.config_utils.save_config_file", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    t._prompt_save_config(cfg, "duckdb", "tpch")


def test_run_dataframe_write_wizard_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tuning = SimpleNamespace(
        DataFrameWriteConfiguration=_DummyWriteConfig,
        PartitionColumn=lambda name, strategy: SimpleNamespace(name=name, strategy=strategy),
        PartitionStrategy=lambda v: SimpleNamespace(value=v),
        SortColumn=lambda name, order: SimpleNamespace(name=name, order=order),
        get_platform_write_capabilities=lambda _p: {
            "sort_by": True,
            "partition_by": True,
            "repartition_count": True,
            "row_group_size": True,
        },
    )
    monkeypatch.setitem(sys.modules, "benchbox.core.dataframe.tuning", fake_tuning)
    monkeypatch.setattr(t.console, "print", lambda *a, **k: None)

    out_non_interactive = t.run_dataframe_write_wizard("duckdb", interactive=False)
    assert out_non_interactive is None

    monkeypatch.setattr(t.Confirm, "ask", _seq([False]))
    out_skip = t.run_dataframe_write_wizard("duckdb", interactive=True)
    assert out_skip is None

    monkeypatch.setattr(t.Confirm, "ask", _seq([True, True, True, True, True, True]))
    monkeypatch.setattr(
        t.Prompt,
        "ask",
        _seq(["l_shipdate", "asc", "day", "date_day"]),
    )
    monkeypatch.setattr(t.IntPrompt, "ask", _seq([1000, 8, 3]))
    cfg = t.run_dataframe_write_wizard("duckdb", benchmark="tpch", interactive=True)
    assert cfg is not None
    assert cfg.row_group_size == 1000

    t._show_dataframe_write_summary(cfg, "duckdb")
