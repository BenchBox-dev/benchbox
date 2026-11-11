from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from benchbox.core.config import BenchmarkConfig
from benchbox.core.constants import (
    GENERIC_POWER_DEFAULT_MEASUREMENT_ITERATIONS,
    GENERIC_POWER_DEFAULT_WARMUP_ITERATIONS,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@dataclass(frozen=True)
class _DatabaseConfig:
    type: str = "duckdb"


def _orchestrator(tmp_path):
    from benchbox.cli.orchestrator import BenchmarkOrchestrator

    orchestrator = BenchmarkOrchestrator(base_dir=str(tmp_path))
    return orchestrator


def _prepare(tmp_path, **config_kwargs: Any):
    config = BenchmarkConfig(
        name=config_kwargs.pop("name", "tpch"),
        display_name=config_kwargs.pop("display_name", "TPC-H"),
        scale_factor=config_kwargs.pop("scale_factor", 1.0),
        **config_kwargs,
    )
    return _orchestrator(tmp_path)._prepare_run_config(config, _DatabaseConfig())


class TestIterationDefaults:
    def test_absent_options_use_the_core_defaults(self, tmp_path):
        run_config = _prepare(tmp_path)

        assert run_config.iterations == GENERIC_POWER_DEFAULT_MEASUREMENT_ITERATIONS
        assert run_config.warm_up_iterations == GENERIC_POWER_DEFAULT_WARMUP_ITERATIONS

    def test_explicit_iterations_win(self, tmp_path):
        run_config = _prepare(tmp_path, options={"power_iterations": 7, "power_warmup_iterations": 2})

        assert run_config.iterations == 7
        assert run_config.warm_up_iterations == 2

    @pytest.mark.parametrize("falsy", [None, 0])
    def test_falsy_iteration_options_fall_back_to_the_default(self, tmp_path, falsy):
        run_config = _prepare(tmp_path, options={"power_iterations": falsy})

        assert run_config.iterations == GENERIC_POWER_DEFAULT_MEASUREMENT_ITERATIONS

    def test_iterations_are_clamped_to_at_least_one(self, tmp_path):
        run_config = _prepare(tmp_path, options={"power_iterations": -5})

        assert run_config.iterations == 1

    def test_warmups_are_clamped_to_at_least_zero(self, tmp_path):
        run_config = _prepare(tmp_path, options={"power_warmup_iterations": -3})

        assert run_config.warm_up_iterations == 0

    def test_string_iteration_values_are_coerced(self, tmp_path):
        run_config = _prepare(tmp_path, options={"power_iterations": "4"})

        assert run_config.iterations == 4


class TestPassthroughFields:
    def test_query_subset_and_concurrency(self, tmp_path):
        run_config = _prepare(tmp_path, queries=["1", "6"], concurrency=3)

        assert run_config.query_subset == ["1", "6"]
        assert run_config.concurrent_streams == 3

    def test_scale_factor_is_carried_through(self, tmp_path):
        run_config = _prepare(tmp_path, scale_factor=10.0)

        assert run_config.scale_factor == 10.0

    def test_plan_capture_flags(self, tmp_path):
        run_config = _prepare(tmp_path, capture_plans=True, strict_plan_capture=True, analyze_plans=True)

        assert run_config.capture_plans is True
        assert run_config.strict_plan_capture is True
        assert run_config.analyze_plans is True

    def test_execution_type_defaults_to_standard(self, tmp_path):
        assert _prepare(tmp_path).test_execution_type == "standard"

    def test_execution_type_is_carried_through(self, tmp_path):
        assert _prepare(tmp_path, test_execution_type="power").test_execution_type == "power"

    def test_power_fail_fast_defaults_off(self, tmp_path):
        assert _prepare(tmp_path).power_fail_fast is False

    def test_power_fail_fast_is_read_from_options(self, tmp_path):
        assert _prepare(tmp_path, options={"power_fail_fast": True}).power_fail_fast is True

    def test_client_link_fields_flow_into_run_config(self, tmp_path):
        run_config = _prepare(
            tmp_path,
            client_region="us-east-1",
            client_cloud="aws",
            link_probe=False,
        )
        assert run_config.client_region == "us-east-1"
        assert run_config.client_cloud == "aws"
        assert run_config.link_probe is False


class TestSeedResolution:
    def test_absent_seed_is_none(self, tmp_path):
        assert _prepare(tmp_path).seed is None

    def test_seed_is_coerced_to_int(self, tmp_path):
        assert _prepare(tmp_path, options={"seed": "42"}).seed == 42

    def test_zero_seed_is_preserved_not_treated_as_absent(self, tmp_path):
        assert _prepare(tmp_path, options={"seed": 0}).seed == 0


class TestDatabasePathResolution:
    def test_connection_carries_a_database_path(self, tmp_path):
        run_config = _prepare(tmp_path)

        assert "database_path" in run_config.connection
        assert run_config.connection["database_path"]

    def test_the_path_is_a_string_not_a_path_object(self, tmp_path):
        assert isinstance(_prepare(tmp_path).connection["database_path"], str)

    def test_scale_factor_participates_in_the_database_path(self, tmp_path):
        one = _prepare(tmp_path, scale_factor=1.0).connection["database_path"]
        ten = _prepare(tmp_path, scale_factor=10.0).connection["database_path"]

        assert one != ten

    def test_benchmark_name_participates_in_the_database_path(self, tmp_path):
        tpch = _prepare(tmp_path, name="tpch").connection["database_path"]
        tpcds = _prepare(tmp_path, name="tpcds").connection["database_path"]

        assert tpch != tpcds

    def test_a_bare_string_tuning_config_does_not_change_the_path(self, tmp_path):
        plain = _prepare(tmp_path).connection["database_path"]
        stringy = _prepare(tmp_path, options={"unified_tuning_configuration": "sorted-keys"}).connection[
            "database_path"
        ]

        assert plain == stringy

    def test_a_tuned_configuration_does_change_the_path(self, tmp_path):
        plain = _prepare(tmp_path).connection["database_path"]
        tuned = _prepare(
            tmp_path,
            options={"unified_tuning_configuration": {"_metadata": {"configuration_type": "tuned"}}},
        ).connection["database_path"]

        assert plain != tuned
        assert "notuning" in plain
        assert "tuned" in tuned

    def test_an_arbitrary_dict_does_not_change_the_path(self, tmp_path):
        plain = _prepare(tmp_path).connection["database_path"]
        arbitrary = _prepare(tmp_path, options={"unified_tuning_configuration": {"something": "else"}}).connection[
            "database_path"
        ]

        assert plain == arbitrary


class TestVerbosityResolution:
    def test_defaults_are_quiet_false_verbose_false(self, tmp_path):
        run_config = _prepare(tmp_path)

        assert run_config.verbose is False
        assert run_config.quiet is False
        assert run_config.very_verbose is False

    def test_verbosity_settings_flow_into_the_run_config(self, tmp_path):
        from benchbox.utils.verbosity import VerbositySettings

        orchestrator = _orchestrator(tmp_path)
        orchestrator.set_verbosity(VerbositySettings(level=2, verbose_enabled=True, quiet=False))
        config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=1.0)

        run_config = orchestrator._prepare_run_config(config, _DatabaseConfig())

        assert run_config.verbose_enabled is True
        assert run_config.verbose_level == 2
        assert run_config.quiet is False


class TestResolutionIsPure:
    def test_repeated_resolution_is_stable(self, tmp_path):
        first = _prepare(tmp_path, options={"seed": 7}, queries=["1"])
        second = _prepare(tmp_path, options={"seed": 7}, queries=["1"])

        assert first.model_dump() == second.model_dump()

    def test_resolution_does_not_mutate_the_input_options(self, tmp_path):
        options = {"seed": 7, "power_iterations": 3}
        snapshot = dict(options)

        _prepare(tmp_path, options=options)

        assert options == snapshot


