from __future__ import annotations

import ast
from pathlib import Path

import pytest

from benchbox.core.config import BenchmarkConfig
from benchbox.core.constants import GENERIC_POWER_DEFAULT_MEASUREMENT_ITERATIONS
from benchbox.core.run_service import (
    SilentVerbosity,
    resolve_run_config,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]
RUN_SERVICE_SOURCE = REPO_ROOT / "benchbox/core/run_service.py"


def _config(**kwargs) -> BenchmarkConfig:
    return BenchmarkConfig(
        name=kwargs.pop("name", "tpch"),
        display_name=kwargs.pop("display_name", "TPC-H"),
        scale_factor=kwargs.pop("scale_factor", 1.0),
        **kwargs,
    )


class TestLayering:
    def test_run_service_does_not_export_unused_request_or_plan_models(self):
        import benchbox.core.run_service as run_service

        assert not hasattr(run_service, "RunRequest")
        assert not hasattr(run_service, "RunPlan")

    def test_run_service_imports_neither_platforms_nor_cli(self):
        tree = ast.parse(RUN_SERVICE_SOURCE.read_text(encoding="utf-8"))
        modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
        modules |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}

        offenders = [m for m in modules if m.startswith(("benchbox.platforms", "benchbox.cli"))]

        assert offenders == []

    def test_the_service_resolves_without_importing_the_cli(self):
        run_config = resolve_run_config(_config(), database_path="/tmp/x.duckdb", verbosity=SilentVerbosity())

        assert run_config.connection["database_path"] == "/tmp/x.duckdb"


class TestResolveRunConfig:
    def test_a_path_object_is_stringified(self):

        database_path = Path("/tmp/db.duckdb")
        run_config = resolve_run_config(_config(), database_path=database_path, verbosity=SilentVerbosity())

        assert run_config.connection["database_path"] == str(database_path)
        assert isinstance(run_config.connection["database_path"], str)

    def test_defaults_match_the_core_constants(self):
        run_config = resolve_run_config(_config(), database_path="x", verbosity=SilentVerbosity())

        assert run_config.iterations == GENERIC_POWER_DEFAULT_MEASUREMENT_ITERATIONS

    def test_silent_verbosity_produces_a_quiet_run_config(self):
        run_config = resolve_run_config(_config(), database_path="x", verbosity=SilentVerbosity())

        assert run_config.quiet is True
        assert run_config.verbose is False
        assert run_config.very_verbose is False
        assert run_config.verbose_level == 0

    def test_the_caller_options_mapping_is_not_mutated(self):
        options = {"seed": 3}
        snapshot = dict(options)

        resolve_run_config(_config(options=options), database_path="x", verbosity=SilentVerbosity())

        assert options == snapshot


class TestOrchestratorDelegatesRatherThanDuplicates:
    def test_orchestrator_produces_the_same_run_config_as_the_service(self, tmp_path):
        from benchbox.cli.orchestrator import BenchmarkOrchestrator

        class _DatabaseConfig:
            type = "duckdb"

        orchestrator = BenchmarkOrchestrator(base_dir=str(tmp_path))
        config = _config(options={"seed": 5, "power_iterations": 2}, queries=["1"])

        via_cli = orchestrator._prepare_run_config(config, _DatabaseConfig())
        database_path = orchestrator.directory_manager.get_database_path(
            config.name, config.scale_factor, "duckdb", tuning_config=None
        )
        via_core = resolve_run_config(config, database_path=database_path, verbosity=orchestrator._verbosity)

        assert via_cli.model_dump() == via_core.model_dump()

    def test_orchestrator_no_longer_defines_its_own_resolution(self):
        source = (REPO_ROOT / "benchbox/cli/orchestrator.py").read_text(encoding="utf-8")

        assert "resolve_run_config" in source

        assert "GENERIC_POWER_DEFAULT_MEASUREMENT_ITERATIONS" not in source


class TestExecutionPort:
    @staticmethod
    def _phases(**kwargs):
        from benchbox.core.run_service import resolve_lifecycle_phases

        return resolve_lifecycle_phases(kwargs.get("phases_to_run"))

    def test_no_phase_list_means_the_standard_lifecycle(self):
        from benchbox.core.run_service import resolve_lifecycle_phases

        phases = resolve_lifecycle_phases(None)

        assert (phases.generate, phases.load, phases.execute) == (True, True, True)
        assert phases.statistics is False

    def test_an_empty_phase_list_is_treated_as_absent(self):
        from benchbox.core.run_service import resolve_lifecycle_phases

        assert resolve_lifecycle_phases([]) == resolve_lifecycle_phases(None)

    @pytest.mark.parametrize("query_phase", ["warmup", "power", "throughput", "maintenance"])
    def test_any_query_phase_sets_execute(self, query_phase: str):
        from benchbox.core.run_service import resolve_lifecycle_phases

        assert resolve_lifecycle_phases([query_phase]).execute is True

    def test_statistics_stays_opt_in(self):
        from benchbox.core.run_service import resolve_lifecycle_phases

        assert resolve_lifecycle_phases(["load"]).statistics is False
        assert resolve_lifecycle_phases(["load", "statistics"]).statistics is True

    def test_validation_options_default_to_off(self):
        from benchbox.core.run_service import resolve_validation_options

        options = resolve_validation_options(None)

        assert options.enable_preflight_validation is False
        assert options.enable_postgen_manifest_validation is False
        assert options.enable_postload_validation is False

    def test_validation_options_are_read_from_config_options(self):
        from benchbox.core.run_service import resolve_validation_options

        options = resolve_validation_options({"enable_postload_validation": True})

        assert options.enable_postload_validation is True

    def test_execution_mode_is_none_without_a_database(self):
        from benchbox.core.run_service import resolve_execution_mode

        assert resolve_execution_mode(None) is None

    def test_an_explicit_execution_mode_wins(self):
        from benchbox.core.run_service import resolve_execution_mode

        class _Config:
            execution_mode = "dataframe"
            type = "duckdb"

        assert resolve_execution_mode(_Config()) == "dataframe"

    def test_requested_phases_are_stamped_for_combined_mode(self):
        from benchbox.core.run_service import stamp_requested_phases

        config = _config()
        stamp_requested_phases(config, ["power", "throughput"])

        assert config.options["requested_phases"] == ["power", "throughput"]

    def test_stamping_nothing_leaves_options_alone(self):
        from benchbox.core.run_service import stamp_requested_phases

        config = _config(options={"seed": 1})
        stamp_requested_phases(config, None)

        assert "requested_phases" not in (config.options or {})

    def test_the_adapter_factory_receives_the_resolved_phases(self):
        from unittest.mock import patch

        from benchbox.core.run_service import execute_run

        seen = {}

        def factory(*, execution_mode, output_root, phases):
            seen["execution_mode"] = execution_mode
            seen["output_root"] = output_root
            seen["phases"] = phases
            return "ADAPTER"

        with (
            patch("benchbox.core.run_service.run_benchmark_lifecycle", return_value="RESULT") as lifecycle,
            patch("benchbox.core.run_service.apply_driver_metadata"),
        ):
            result = execute_run(
                config=_config(),
                benchmark_instance=object(),
                database_config=None,
                system_profile=None,
                platform_config=None,
                output_root="/tmp/out",
                phases_to_run=["load", "power"],
                adapter_factory=factory,
                verbosity=SilentVerbosity(),
            )

        assert result == "RESULT"
        assert seen["execution_mode"] is None
        assert seen["output_root"] == "/tmp/out"
        assert seen["phases"].load is True and seen["phases"].execute is True
        assert lifecycle.call_args.kwargs["platform_adapter"] == "ADAPTER"

    def test_a_factory_returning_none_is_honoured(self):
        from unittest.mock import patch

        from benchbox.core.run_service import execute_run

        with (
            patch("benchbox.core.run_service.run_benchmark_lifecycle", return_value="RESULT") as lifecycle,
            patch("benchbox.core.run_service.apply_driver_metadata"),
        ):
            execute_run(
                config=_config(),
                benchmark_instance=object(),
                database_config=None,
                system_profile=None,
                platform_config=None,
                output_root="/tmp/out",
                phases_to_run=["generate"],
                adapter_factory=lambda **_: None,
                verbosity=SilentVerbosity(),
            )

        assert lifecycle.call_args.kwargs["platform_adapter"] is None

    def test_driver_metadata_is_applied_to_every_result(self):
        from unittest.mock import patch

        from benchbox.core.run_service import execute_run

        with (
            patch("benchbox.core.run_service.run_benchmark_lifecycle", return_value="RESULT"),
            patch("benchbox.core.run_service.apply_driver_metadata") as enrich,
        ):
            execute_run(
                config=_config(),
                benchmark_instance=object(),
                database_config=None,
                system_profile=None,
                platform_config=None,
                output_root="/tmp/out",
                phases_to_run=None,
                adapter_factory=lambda **_: "ADAPTER",
                verbosity=SilentVerbosity(),
            )

        enrich.assert_called_once()
        assert enrich.call_args.kwargs["platform_adapter"] == "ADAPTER"


class TestDataFrameThroughputIsRejected:
    @pytest.mark.parametrize("phases", [["throughput"], ["power", "throughput"], ["load", "throughput"]])
    def test_dataframe_throughput_raises(self, phases: list[str]):
        from benchbox.core.run_service import reject_unsupported_dataframe_phases

        with pytest.raises(ValueError, match="throughput phase is not supported in DataFrame mode"):
            reject_unsupported_dataframe_phases("dataframe", phases)

    @pytest.mark.parametrize(
        ("mode", "phases"),
        [("dataframe", ["power"]), ("dataframe", None), ("sql", ["power", "throughput"]), (None, ["throughput"])],
    )
    def test_other_requests_pass(self, mode, phases):
        from benchbox.core.run_service import reject_unsupported_dataframe_phases

        reject_unsupported_dataframe_phases(mode, phases)

    def test_execute_run_refuses_before_building_an_adapter(self):
        from unittest.mock import patch

        from benchbox.core.run_service import execute_run

        class _DataFrameDatabase:
            execution_mode = "dataframe"
            type = "polars-df"

        factory_calls = []
        with patch("benchbox.core.run_service.run_benchmark_lifecycle") as lifecycle:
            with pytest.raises(ValueError, match="DataFrame mode"):
                execute_run(
                    config=_config(),
                    benchmark_instance=object(),
                    database_config=_DataFrameDatabase(),
                    system_profile=None,
                    platform_config=None,
                    output_root="/tmp/out",
                    phases_to_run=["load", "throughput"],
                    adapter_factory=lambda **kwargs: factory_calls.append(kwargs),
                    verbosity=SilentVerbosity(),
                )

        assert factory_calls == []
        lifecycle.assert_not_called()


class TestInteractionStaysInTheCli:
    def test_core_emits_no_console_output(self):
        tree = ast.parse(RUN_SERVICE_SOURCE.read_text(encoding="utf-8"))

        called = {
            node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        attribute_calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}

        assert "print" not in called
        assert "print" not in attribute_calls
        assert not any(m.startswith("rich") or m.endswith("printing") for m in modules)

    def test_the_credential_retry_stayed_behind(self):
        tree = ast.parse(RUN_SERVICE_SOURCE.read_text(encoding="utf-8"))
        defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}

        assert not any("credential" in name.lower() for name in defined)

        cli_source = (REPO_ROOT / "benchbox/cli/orchestrator.py").read_text(encoding="utf-8")
        assert "_offer_and_run_credential_setup" in cli_source

    def test_the_cli_warns_on_variant_comparability_issues(self, tmp_path):
        from benchbox.cli.orchestrator import BenchmarkOrchestrator

        printed = []

        class _Console:
            def print(self, message):
                printed.append(str(message))

        class _Benchmark:
            def get_benchmark_info(self):
                return {"variant_comparability": {"comparable": False, "issue_count": 2}}

        orchestrator = BenchmarkOrchestrator(base_dir=str(tmp_path))
        orchestrator.console = _Console()
        orchestrator._warn_on_variant_comparability_issues(_Benchmark())

        assert any("comparability" in message for message in printed)

    def test_the_cli_stays_silent_when_variants_are_comparable(self, tmp_path):
        from benchbox.cli.orchestrator import BenchmarkOrchestrator

        printed = []

        class _Console:
            def print(self, message):
                printed.append(str(message))

        class _Benchmark:
            def get_benchmark_info(self):
                return {"variant_comparability": {"comparable": True, "issue_count": 0}}

        orchestrator = BenchmarkOrchestrator(base_dir=str(tmp_path))
        orchestrator.console = _Console()
        orchestrator._warn_on_variant_comparability_issues(_Benchmark())

        assert printed == []

    def test_the_cli_still_owns_the_load_phase_warning(self):
        source = (REPO_ROOT / "benchbox/cli/orchestrator.py").read_text(encoding="utf-8")

        assert "_warn_on_execute_without_load" in source
        assert "assuming data already exists" in source


class TestTheExportedSurface:
    @staticmethod
    def _module_level_public_names() -> set[str]:
        tree = ast.parse(RUN_SERVICE_SOURCE.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.ClassDef):
                names.add(node.name)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
            elif isinstance(node, ast.Assign):
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        return {name for name in names if not name.startswith("_")}

    def test_no_private_name_is_advertised_as_public(self):
        from benchbox.core import run_service

        assert [name for name in run_service.__all__ if name.startswith("_")] == []

    def test_every_public_definition_is_exported(self):
        from benchbox.core import run_service

        missing = sorted(self._module_level_public_names() - set(run_service.__all__))

        assert missing == [], f"defined but not exported: {missing}"

    def test_every_export_resolves(self):
        from benchbox.core import run_service

        unresolved = sorted(name for name in run_service.__all__ if not hasattr(run_service, name))

        assert unresolved == [], f"exported but undefined: {unresolved}"

    def test_no_surface_imports_a_private_run_service_symbol(self):
        offenders = []
        for surface in ("benchbox/cli", "benchbox/mcp"):
            for path in sorted((REPO_ROOT / surface).rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ImportFrom) or node.module != "benchbox.core.run_service":
                        continue
                    offenders.extend(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno} {alias.name}"
                        for alias in node.names
                        if alias.name.startswith("_")
                    )

        assert offenders == [], f"surfaces importing private core symbols: {offenders}"


