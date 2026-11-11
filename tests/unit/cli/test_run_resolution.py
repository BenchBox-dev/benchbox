import os
import sys
from copy import copy
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import Mock

import click
import pytest

from benchbox.cli.benchmarks import BenchmarkConfig
from benchbox.cli.composite_params import (
    CompressionConfig,
    ForceConfig,
    PlanCaptureConfig,
    TableFormatConfig,
    ValidationConfig,
)
from benchbox.cli.run_resolution import ResolvedRunPlan, RunRequest, merge_quick_restart_request, parse_saved_phases

__import__("benchbox.cli.commands.run")
_run_module = sys.modules["benchbox.cli.commands.run"]

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _request(**overrides):
    values = {
        "platform": None,
        "benchmark": None,
        "scale": 1.0,
        "phases": ("load", "power"),
        "queries": None,
        "tuning": "tuned",
        "table_mode": "native",
        "output": None,
        "mode": None,
        "seed": None,
        "compression_enabled": True,
        "compression_type": "zstd",
        "compression_level": None,
    }
    values.update(overrides)
    return RunRequest(**values)


def test_run_request_is_immutable():
    request = _request()

    with pytest.raises(FrozenInstanceError):
        request.seed = 4


def test_resolved_run_plan_contract_is_immutable():
    assert ResolvedRunPlan.__dataclass_params__.frozen is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("load,power", ("load", "power")),
        (["generate", "load"], ("generate", "load")),
        (("power",), ("power",)),
    ],
)
def test_saved_phases_accept_compatible_historical_shapes(raw, expected):
    assert parse_saved_phases(raw) == expected


@pytest.mark.parametrize("raw", [None, {}, [], " , "])
def test_saved_phases_reject_invalid_or_empty_values(raw):
    with pytest.raises(ValueError):
        parse_saved_phases(raw)


def test_saved_request_uses_documented_compatibility_defaults_and_marks_missing_seed():
    merged = merge_quick_restart_request(
        _request(),
        {"database": "duckdb", "benchmark": "tpch", "scale": "0.01"},
        explicit_fields=frozenset(),
    )

    assert merged.platform == "duckdb"
    assert merged.benchmark == "tpch"
    assert merged.scale == 0.01
    assert merged.phases == ("load", "power")
    assert merged.tuning == "tuned"
    assert merged.table_mode == "native"
    assert merged.concurrency is None
    assert merged.exact_replay is False
    assert "saved run did not record a seed" in merged.compatibility_notes
    assert "saved run did not record phases; assumed load,power" in merged.compatibility_notes
    assert "saved run did not record execution mode; used platform default" in merged.compatibility_notes
    assert "saved run did not record concurrency; used the default stream count" in merged.compatibility_notes


def test_explicit_current_values_override_saved_preferences():
    current = _request(
        platform="datafusion",
        scale=2.0,
        phases=("throughput",),
        tuning="auto",
        table_mode="external",
        output="current-output",
        seed=0,
        compression_type="gzip",
        compression_level=6,
    )
    merged = merge_quick_restart_request(
        current,
        {
            "database": "duckdb",
            "benchmark": "tpch",
            "scale": 1,
            "phases": ["power"],
            "tuning_mode": "notuning",
            "table_mode": "native",
            "output": "saved-output",
            "seed": 42,
            "compress_data": False,
            "compression_type": "none",
        },
        explicit_fields=frozenset(
            {"platform", "scale", "phases", "tuning", "table_mode", "output", "seed", "compression"}
        ),
    )

    assert merged.platform == "datafusion"
    assert merged.benchmark == "tpch"
    assert merged.scale == 2.0
    assert merged.phases == ("throughput",)
    assert merged.tuning == "auto"
    assert merged.table_mode == "external"
    assert merged.output == "current-output"
    assert merged.seed == 0
    assert merged.compression_enabled is True
    assert merged.compression_type == "gzip"
    assert merged.compression_level == 6
    assert merged.exact_replay is False
    assert "current CLI overrides saved platform" in merged.compatibility_notes
    assert "current CLI overrides saved compression" in merged.compatibility_notes


def test_fully_recorded_saved_request_is_exact():
    merged = merge_quick_restart_request(
        _request(),
        {
            "database": "duckdb",
            "benchmark": "tpch",
            "scale": 1,
            "phases": ["load", "power"],
            "queries": None,
            "tuning_mode": "notuning",
            "table_mode": "native",
            "mode": "sql",
            "seed": 7,
            "compress_data": True,
            "compression_type": "zstd",
            "compression_level": None,
            "concurrency": None,
            "iterations": None,
            "replay_schema_version": 1,
            "non_replayable_options": [],
        },
        explicit_fields=frozenset(),
    )

    assert merged.exact_replay is True
    assert merged.compatibility_notes == ()


@pytest.mark.parametrize("seed", [True, 1.5, "1.5"])
def test_saved_request_rejects_non_integral_seed(seed):
    with pytest.raises(ValueError, match="seed must be an integer"):
        merge_quick_restart_request(
            _request(),
            {"database": "duckdb", "benchmark": "tpch", "scale": 1, "seed": seed},
            explicit_fields=frozenset(),
        )


def test_saved_power_iterations_are_replayed_exactly():
    merged = merge_quick_restart_request(
        _request(),
        {
            "database": "duckdb",
            "benchmark": "tpch",
            "scale": 1,
            "phases": ["power"],
            "queries": None,
            "tuning_mode": "notuning",
            "table_mode": "native",
            "mode": "sql",
            "seed": 7,
            "compress_data": True,
            "compression_type": "zstd",
            "compression_level": None,
            "concurrency": None,
            "iterations": 5,
            "replay_schema_version": 1,
            "non_replayable_options": [],
        },
        explicit_fields=frozenset(),
    )

    assert merged.iterations == 5
    assert merged.exact_replay is True


def test_explicit_iterations_override_saved_value_and_mark_replay_non_exact():
    merged = merge_quick_restart_request(
        _request(iterations=7),
        {"database": "duckdb", "benchmark": "tpch", "scale": 1, "iterations": 5},
        explicit_fields=frozenset({"iterations"}),
    )

    assert merged.iterations == 7
    assert "current CLI overrides saved iterations" in merged.compatibility_notes


@pytest.mark.parametrize("saved", [{"concurrency": 3}, {}], ids=["overrides-saved", "supplies-legacy-missing"])
def test_explicit_concurrency_wins_and_marks_replay_non_exact(saved):
    merged = merge_quick_restart_request(
        _request(concurrency=7),
        {"database": "duckdb", "benchmark": "tpch", "scale": 1, **saved},
        explicit_fields=frozenset({"concurrency"}),
    )

    assert merged.concurrency == 7
    action = "overrides saved" if saved else "supplies missing saved"
    assert f"current CLI {action} concurrency" in merged.compatibility_notes
    assert merged.exact_replay is False


def test_explicit_invalid_concurrency_still_fails_closed():
    with pytest.raises(ValueError, match="at least one"):
        merge_quick_restart_request(
            _request(concurrency=0),
            {"database": "duckdb", "benchmark": "tpch", "scale": 1, "concurrency": 3},
            explicit_fields=frozenset({"concurrency"}),
        )


def test_explicit_field_detection_is_symmetric_for_persisted_concurrency():
    ctx = Mock()
    ctx.get_parameter_source.side_effect = lambda field: (
        click.core.ParameterSource.COMMANDLINE if field == "concurrency" else click.core.ParameterSource.DEFAULT
    )

    assert _run_module._explicit_run_fields(SimpleNamespace(ctx=ctx)) == frozenset({"concurrency"})


def test_legacy_saved_request_without_iterations_is_compatible_but_non_exact():
    merged = merge_quick_restart_request(
        _request(iterations=9),
        {"database": "duckdb", "benchmark": "tpch", "scale": 1},
        explicit_fields=frozenset(),
    )

    assert merged.iterations is None
    assert merged.exact_replay is False
    assert "saved run did not record power iterations; used the default" in merged.compatibility_notes
    assert "saved run predates complete execution-option replay accounting" in merged.compatibility_notes


def test_active_unserializable_execution_option_is_named_and_never_claimed_exact():
    merged = merge_quick_restart_request(
        _request(non_replayable_options=("platform_options",)),
        {
            "database": "duckdb",
            "benchmark": "tpch",
            "scale": 1,
            "iterations": None,
            "replay_schema_version": 1,
            "non_replayable_options": ["force"],
        },
        explicit_fields=frozenset(),
    )

    assert merged.exact_replay is False
    assert merged.non_replayable_options == ("force", "platform_options")
    assert "execution option is not persisted for automatic replay: force" in merged.compatibility_notes
    assert "execution option is not persisted for automatic replay: platform_options" in merged.compatibility_notes


def test_current_request_accounts_for_every_active_non_replayable_execution_control():
    state = SimpleNamespace(
        platform="duckdb",
        benchmark="tpch",
        scale=1.0,
        phases="power",
        queries=None,
        tuning="notuning",
        table_mode="native",
        output=None,
        mode="sql",
        seed=7,
        compression=CompressionConfig(),
        concurrency=1,
        iterations=5,
        analyze_plans=False,
        benchmark_option_pairs=(("skew_preset", "heavy"),),
        capture_plans=True,
        force=ForceConfig(datagen=True),
        global_cache=True,
        ignore_memory_warnings=True,
        no_monitoring=True,
        normalize_plan_literals=True,
        official=True,
        plan_config=PlanCaptureConfig(strict=True),
        platform_option_pairs=(("driver_version", "1.2.3"),),
        presort="parquet-sorted",
        show_plans=True,
        sorted_ingestion_method="ctas",
        sorted_ingestion_mode="force",
        stats_per_table_timing=True,
        stats_reset=False,
        strict_translation=True,
        table_format=TableFormatConfig(format="delta"),
        validation=ValidationConfig(mode="full"),
    )

    request = _run_module._current_run_request(state)

    assert request.iterations == 5
    assert request.non_replayable_options == (
        "analyze_plans",
        "benchmark_options",
        "capture_plans",
        "force",
        "global_cache",
        "ignore_memory_warnings",
        "no_monitoring",
        "normalize_plan_literals",
        "official",
        "plan_config",
        "platform_options",
        "presort",
        "show_plans",
        "sorted_ingestion_method",
        "sorted_ingestion_mode",
        "stats_per_table_timing",
        "stats_reset",
        "strict_translation",
        "table_format",
        "validation",
    )


def _live_state(concurrency):
    return SimpleNamespace(
        platform="duckdb",
        benchmark="tpch",
        scale=1.0,
        phases="power",
        queries=None,
        tuning="notuning",
        table_mode="native",
        output=None,
        mode="sql",
        seed=7,
        compression=CompressionConfig(),
        concurrency=concurrency,
        iterations=None,
        comp_config=None,
        compression_enabled=False,
        compression_type=None,
        compression_level=None,
    )


def test_current_request_rejects_zero_concurrency():
    with pytest.raises(ValueError, match="at least one"):
        _run_module._current_run_request(_live_state(0))


def test_current_request_rejects_non_integer_concurrency():
    with pytest.raises(ValueError, match="must be an integer"):
        _run_module._current_run_request(_live_state("lots"))


def test_current_request_leaves_missing_concurrency_unset():
    assert _run_module._current_run_request(_live_state(None)).concurrency is None


def test_current_request_keeps_valid_concurrency():
    assert _run_module._current_run_request(_live_state(4)).concurrency == 4
    assert _run_module._current_run_request(_live_state("3")).concurrency == 3


@pytest.mark.parametrize(
    ("saved_queries", "expected"),
    [(["Q1", "Q6"], "Q1,Q6"), ((1, 6), "1,6"), ("Q1,Q6", "Q1,Q6"), (None, None)],
)
def test_saved_query_subset_uses_cli_compatible_serialization(saved_queries, expected):
    merged = merge_quick_restart_request(
        _request(),
        {"database": "duckdb", "benchmark": "tpch", "scale": 1, "queries": saved_queries},
        explicit_fields=frozenset(),
    )

    assert merged.queries == expected


@pytest.mark.parametrize(
    ("explicit_field", "saved_key", "current_value"),
    [
        ("platform", "database", "duckdb"),
        ("benchmark", "benchmark", "tpch"),
        ("scale", "scale", 2.0),
    ],
)
def test_explicit_required_selector_satisfies_missing_saved_key(explicit_field, saved_key, current_value):
    saved = {"database": "duckdb", "benchmark": "tpch", "scale": 1}
    saved.pop(saved_key)
    merged = merge_quick_restart_request(
        _request(**{explicit_field: current_value}),
        saved,
        explicit_fields=frozenset({explicit_field}),
    )

    expected_attribute = "platform" if explicit_field == "platform" else explicit_field
    assert getattr(merged, expected_attribute) == current_value
    assert f"current CLI supplies missing saved {explicit_field}" in merged.compatibility_notes


@pytest.mark.parametrize(
    ("field", "saved_key", "current_value"),
    [
        ("platform", "database", None),
        ("benchmark", "benchmark", None),
        ("scale", "scale", 1.0),
    ],
)
def test_missing_required_selector_without_explicit_override_fails(field, saved_key, current_value):
    saved = {"database": "duckdb", "benchmark": "tpch", "scale": 1}
    saved.pop(saved_key)

    with pytest.raises(ValueError, match=saved_key):
        merge_quick_restart_request(
            _request(**{field: current_value}),
            saved,
            explicit_fields=frozenset(),
        )


@pytest.mark.parametrize(
    "saved",
    [
        {"database": "duckdb", "benchmark": "tpch"},
        {"database": "", "benchmark": "tpch", "scale": 1},
        {"database": "duckdb", "benchmark": "", "scale": 1},
        {"database": "duckdb", "benchmark": "tpch", "scale": "large"},
        {"database": "duckdb", "benchmark": "tpch", "scale": 0},
        {"database": "duckdb", "benchmark": "tpch", "scale": 1, "phases": []},
        {"database": "duckdb", "benchmark": "tpch", "scale": 1, "table_mode": "mystery"},
        {"database": "duckdb", "benchmark": "tpch", "scale": 1, "seed": "random"},
        {"database": "duckdb", "benchmark": "tpch", "scale": 1, "compress_data": "false"},
        {
            "database": "duckdb",
            "benchmark": "tpch",
            "scale": 1,
            "compress_data": True,
            "compression_type": "none",
        },
        {
            "database": "duckdb",
            "benchmark": "tpch",
            "scale": 1,
            "compression_type": "gzip",
            "compression_level": 10,
        },
        {"database": "duckdb", "benchmark": "tpch", "scale": 1, "compression_type": "brotli"},
        {"database": "duckdb", "benchmark": "tpch", "scale": 1, "concurrency": 0},
        {"database": "duckdb", "benchmark": "tpch", "scale": 1, "iterations": 0},
        {"database": "duckdb", "benchmark": "tpch", "scale": 1, "iterations": "many"},
        {"database": "duckdb", "benchmark": "tpch", "scale": 1, "iterations": 2.5},
        {"database": "duckdb", "benchmark": "tpch", "scale": 1, "iterations": True},
        {"database": "duckdb", "benchmark": "tpch", "scale": 1, "replay_schema_version": 2},
        {"database": "duckdb", "benchmark": "tpch", "scale": 1, "non_replayable_options": "force"},
        {
            "database": "duckdb",
            "benchmark": "tpch",
            "scale": 1,
            "non_replayable_options": ["unknown-option"],
        },
        {"database": "duckdb", "benchmark": "tpch", "scale": 1, "queries": {"Q1": True}},
    ],
)
def test_invalid_saved_request_fails_closed(saved):
    with pytest.raises(ValueError):
        merge_quick_restart_request(_request(), saved, explicit_fields=frozenset())


@pytest.mark.parametrize("existing_non_interactive", [None, "owner-value"])
def test_failed_quick_restart_resolution_restores_state_and_environment(monkeypatch, existing_non_interactive):
    ctx = Mock()
    ctx.get_parameter_source.return_value = click.core.ParameterSource.DEFAULT
    state = SimpleNamespace(ctx=ctx, run_request=_request(), non_interactive=False, marker="pre-restart")
    original = copy(vars(state))
    monkeypatch.setenv(_run_module.DATA_ORGANIZATION_ENV, '{"original": true}')
    if existing_non_interactive is None:
        monkeypatch.delenv("BENCHBOX_NON_INTERACTIVE", raising=False)
    else:
        monkeypatch.setenv("BENCHBOX_NON_INTERACTIVE", existing_non_interactive)

    def fail_after_partial_write(subject, *, apply_default_scale, tuning_non_interactive):
        assert apply_default_scale is False
        assert tuning_non_interactive is True
        subject.platform_key = "partially-resolved"
        os.environ[_run_module.DATA_ORGANIZATION_ENV] = '{"partial": true}'
        os.environ["BENCHBOX_NON_INTERACTIVE"] = "partial"
        raise RuntimeError("resolution failed")

    monkeypatch.setattr(_run_module, "_resolve_run_request", fail_after_partial_write)

    with pytest.raises(RuntimeError, match="resolution failed"):
        _run_module._resolve_quick_restart_atomically(
            state,
            {"database": "duckdb", "benchmark": "tpch", "scale": 1, "seed": 7},
        )

    assert vars(state) == original
    assert os.environ[_run_module.DATA_ORGANIZATION_ENV] == '{"original": true}'
    assert os.environ.get("BENCHBOX_NON_INTERACTIVE") == existing_non_interactive


@pytest.mark.parametrize("existing_non_interactive", [None, "owner-value"])
def test_successful_quick_restart_preserves_non_interactive_environment(monkeypatch, existing_non_interactive):
    ctx = Mock()
    ctx.get_parameter_source.return_value = click.core.ParameterSource.DEFAULT
    state = SimpleNamespace(ctx=ctx, run_request=_request(), non_interactive=False)
    monkeypatch.setenv(_run_module.DATA_ORGANIZATION_ENV, '{"original": true}')
    if existing_non_interactive is None:
        monkeypatch.delenv("BENCHBOX_NON_INTERACTIVE", raising=False)
    else:
        monkeypatch.setenv("BENCHBOX_NON_INTERACTIVE", existing_non_interactive)
    resolved_plan = object()

    def resolve_successfully(subject, *, apply_default_scale, tuning_non_interactive):
        assert apply_default_scale is False
        assert tuning_non_interactive is True
        os.environ[_run_module.DATA_ORGANIZATION_ENV] = '{"resolved": true}'
        os.environ["BENCHBOX_NON_INTERACTIVE"] = "partial"
        subject.resolved_run_plan = resolved_plan

    monkeypatch.setattr(_run_module, "_resolve_run_request", resolve_successfully)

    result = _run_module._resolve_quick_restart_atomically(
        state,
        {"database": "duckdb", "benchmark": "tpch", "scale": 1, "seed": 7},
    )

    assert result is resolved_plan
    assert os.environ[_run_module.DATA_ORGANIZATION_ENV] == '{"resolved": true}'
    assert os.environ.get("BENCHBOX_NON_INTERACTIVE") == existing_non_interactive


@pytest.mark.parametrize(
    ("compression_cli_set", "namespace_compression", "wizard_compression", "expected_compression"),
    [
        (
            False,
            CompressionConfig(enabled=True, type="zstd", level=3),
            CompressionConfig(enabled=True, type="gzip", level=6),
            (True, "gzip", 6),
        ),
        (
            False,
            CompressionConfig(enabled=True, type="zstd", level=3),
            CompressionConfig(enabled=False, type="none", level=None),
            (False, "none", None),
        ),
        (
            True,
            CompressionConfig(enabled=True, type="zstd", level=5),
            CompressionConfig(enabled=False, type="none", level=None),
            (True, "zstd", 5),
        ),
    ],
    ids=["wizard-gzip-adopted", "wizard-disabled-adopted", "explicit-cli-compression-wins"],
)
def test_normal_interactive_finalization_refreshes_plan_when_selectors_and_phases_are_unchanged(
    monkeypatch,
    compression_cli_set,
    namespace_compression,
    wizard_compression,
    expected_compression,
):
    old_compression = namespace_compression
    old_unified_config = object()
    state = SimpleNamespace(
        platform="duckdb",
        platform_key="duckdb",
        benchmark="tpch",
        scale=1.0,
        phases="load,power",
        phases_to_run=["load", "power"],
        queries=None,
        queries_to_run=None,
        tuning="tuned",
        table_mode="native",
        output=None,
        mode=None,
        seed=11,
        compression=old_compression,
        comp_config=old_compression,
        compress_data=namespace_compression.enabled,
        compression_type=namespace_compression.type,
        compression_level=namespace_compression.level,
        compression_cli_set=compression_cli_set,
        concurrency=1,
        test_execution_type="power",
        execution_mode="power",
        resolved_mode="sql",
        tuning_resolution=SimpleNamespace(canonical_mode="tuned"),
        tuning_enabled=True,
        tuning_config_file=None,
        use_auto_tuning=False,
        loaded_unified_config=old_unified_config,
        data_organization_payload={"table_configs": {"lineitem": {"sort_by": ["l_orderkey"]}}},
        df_tuning_config=object(),
        benchmark_config=BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=1.0,
            queries=None,
            concurrency=1,
            options={"seed": 11},
        ),
        database_config=SimpleNamespace(type="duckdb", execution_mode="sql"),
        stats_reset=None,
        stats_per_table_timing=False,
    )
    state.run_request = _run_module._current_run_request(state)
    original_plan = _run_module._capture_resolved_run_plan(state)
    state.resolved_run_plan = original_plan

    state.seed = 99
    state.tuning = "notuning"
    state.tuning_resolution = SimpleNamespace(canonical_mode="notuning")
    state.tuning_enabled = False
    state.loaded_unified_config = None
    state.table_mode = "external"
    state.resolved_mode = "dataframe"
    state.data_organization_payload = {"table_configs": {"lineitem": {"partition_by": ["l_shipdate"]}}}
    state.df_tuning_config = None
    state.benchmark_config.queries = ["Q1", "Q6"]
    state.benchmark_config.concurrency = 4
    state.benchmark_config.compress_data = wizard_compression.enabled
    state.benchmark_config.compression_type = wizard_compression.type
    state.benchmark_config.compression_level = wizard_compression.level

    _run_module._finalize_normal_interactive_plan(state)

    plan = state.resolved_run_plan
    assert (plan.platform_key, plan.benchmark, plan.scale, plan.phases) == (
        original_plan.platform_key,
        original_plan.benchmark,
        original_plan.scale,
        original_plan.phases,
    )
    assert plan is not original_plan
    assert plan.queries == ("Q1", "Q6")
    assert plan.seed == 99
    assert (plan.compression_enabled, plan.compression_type, plan.compression_level) == expected_compression
    assert (
        plan.request.compression_enabled,
        plan.request.compression_type,
        plan.request.compression_level,
    ) == expected_compression
    assert plan.concurrency == 4
    assert plan.request.concurrency == 4
    assert plan.table_mode == "external"
    assert plan.canonical_tuning_mode == "notuning"
    assert plan.tuning_enabled is False
    assert plan.loaded_unified_config is None
    assert plan.resolved_mode == "dataframe"
    assert plan.data_organization == state.data_organization_payload
    assert plan.dataframe_tuning_config is None

    assert state.benchmark_config.queries == ["Q1", "Q6"]
    assert state.benchmark_config.options["seed"] == 99
    assert state.benchmark_config.options["table_mode"] == "external"
    assert state.benchmark_config.options["tuning_enabled"] is False
    assert state.benchmark_config.options["unified_tuning_configuration"] is None
    assert state.benchmark_config.options["data_organization"] == state.data_organization_payload
    assert "df_tuning_config" not in state.benchmark_config.options
    assert (
        state.benchmark_config.compress_data,
        state.benchmark_config.compression_type,
        state.benchmark_config.compression_level,
    ) == expected_compression
    assert state.benchmark_config.concurrency == 4
    assert state.database_config.execution_mode == "dataframe"

    state.force = None
    state.plan_config = None
    state.platform_option_pairs = ()
    state.official = False
    state.capture_plans = False
    state.analyze_plans = False
    state.validation_mode = None
    state.verbosity_settings = SimpleNamespace(level=0)
    state.presort = None
    state.sorted_ingestion_mode = None
    state.sorted_ingestion_method = None
    state.global_cache = False
    state.strict_translation = None
    state.benchmark_option_pairs = ()
    preview = Mock()
    monkeypatch.setattr("benchbox.cli.dryrun.display_interactive_preview", preview)
    _run_module._interactive_show_preview(state)
    assert preview.call_args.kwargs["phases"] == list(plan.phases)
    assert preview.call_args.kwargs["table_mode"] == plan.table_mode
    assert preview.call_args.kwargs["tuning"] is None
    assert preview.call_args.kwargs["seed"] == plan.seed

    state.val_config = ValidationConfig()
    state.force_config = ForceConfig()
    state.strict_plan_capture = False
    state.non_interactive = False
    execution_context = _run_module._build_execution_context_from_plan(state)
    assert execution_context.phases == list(plan.phases)
    assert execution_context.query_subset == list(plan.queries)
    assert execution_context.seed == plan.seed
    assert (
        execution_context.compression_enabled,
        execution_context.compression_type,
        execution_context.compression_level,
    ) == (plan.compression_enabled, plan.compression_type, plan.compression_level)
    assert execution_context.mode == plan.resolved_mode
    assert execution_context.tuning_mode == plan.canonical_tuning_mode


def test_finalization_propagates_the_wizard_official_choice_into_the_config() -> None:
    compression = CompressionConfig(enabled=False, type="none", level=None)
    state = SimpleNamespace(
        official=True,
        platform="duckdb",
        platform_key="duckdb",
        benchmark="tpcds",
        scale=1.0,
        phases="load,power",
        phases_to_run=["load", "power"],
        queries=None,
        queries_to_run=None,
        tuning="tuned",
        table_mode="native",
        output=None,
        mode=None,
        seed=11,
        compression=compression,
        comp_config=compression,
        compress_data=compression.enabled,
        compression_type=compression.type,
        compression_level=compression.level,
        compression_cli_set=True,
        concurrency=1,
        test_execution_type="power",
        execution_mode="power",
        resolved_mode="sql",
        tuning_resolution=SimpleNamespace(canonical_mode="tuned"),
        tuning_enabled=True,
        tuning_config_file=None,
        use_auto_tuning=False,
        loaded_unified_config=None,
        data_organization_payload=None,
        df_tuning_config=None,
        benchmark_config=BenchmarkConfig(
            name="tpcds",
            display_name="TPC-DS",
            scale_factor=1.0,
            queries=None,
            concurrency=1,
            official=False,
        ),
        database_config=SimpleNamespace(type="duckdb", execution_mode="sql"),
        stats_reset=None,
        stats_per_table_timing=False,
    )
    assert state.benchmark_config.official is False

    _run_module._finalize_normal_interactive_plan(state)

    assert state.benchmark_config.official is True


