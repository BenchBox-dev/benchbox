# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

if TYPE_CHECKING:
    from benchbox.base import BaseBenchmark

from benchbox.cli.config import DirectoryManager
from benchbox.core.benchmark_loader import (
    get_benchmark_instance,
    get_core_benchmark_class,
    instantiate_benchmark_class,
)
from benchbox.core.config import BenchmarkConfig, RunConfig
from benchbox.core.hooks.platform_hooks import PlatformHookRegistry
from benchbox.core.platform_config import (
    DATABASE_ONLY_PLATFORM_OPTIONS,
    get_platform_config as _core_get_platform_config,
)
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.run_service import execute_run, resolve_lifecycle_phases, resolve_run_config
from benchbox.core.schemas import ExecutionContext
from benchbox.platforms import get_adapter, get_platform_adapter
from benchbox.utils.cloud_storage import is_databricks_path
from benchbox.utils.printing import quiet_console
from benchbox.utils.verbosity import VerbositySettings

console = quiet_console


def resolved_deployment_mode(database_config) -> Optional[str]:
    if not database_config:
        return None
    options = getattr(database_config, "options", None) or {}
    mode = options.get("deployment_mode")
    return mode or None


def _build_failure_result(config: BenchmarkConfig, exc: Exception) -> BenchmarkResults:
    from benchbox.core.results.models import ExecutionPhases, SetupPhase

    return BenchmarkResults(
        benchmark_name=getattr(config, "display_name", config.name.upper()),
        platform="unknown",
        scale_factor=config.scale_factor,
        execution_id=uuid.uuid4().hex[:8],
        timestamp=datetime.now(),
        duration_seconds=0.0,
        total_queries=0,
        successful_queries=0,
        failed_queries=0,
        total_execution_time=0.0,
        average_query_time=0.0,
        query_results=[],
        query_definitions={},
        execution_phases=ExecutionPhases(setup=SetupPhase()),
        validation_status="FAILED",
        validation_details={"error": str(exc)},
        data_loading_time=0.0,
        schema_creation_time=0.0,
        total_rows_loaded=0,
        data_size_mb=0.0,
        table_statistics={},
    )


def _local_datagen_name(benchmark: Any, benchmark_name: str) -> str:
    if getattr(benchmark, "GENERATES_OWN_OUTPUT", False):
        return benchmark_name.lower()
    data_source = getattr(benchmark, "get_data_source_benchmark", lambda: None)()
    return (data_source or benchmark_name).lower()


class BenchmarkOrchestrator:
    def __init__(self, base_dir: Optional[str] = None):
        self.console = quiet_console
        self.directory_manager = DirectoryManager(base_dir)
        self.custom_output_dir = None
        self._verbosity = VerbositySettings.default()

    def set_verbosity(self, settings: VerbositySettings) -> None:

        self._verbosity = settings

    def _get_benchmark_class(self, benchmark_name: str):
        return get_core_benchmark_class(benchmark_name)

    def _get_benchmark_instance(self, config: BenchmarkConfig, system_profile):
        benchmark_class = self._get_benchmark_class(config.name)

        opts = getattr(config, "options", {}) or {}
        benchmark_options = dict(opts.get("benchmark_options", {}))

        from benchbox.cli.benchmark_hooks import BenchmarkHookRegistry

        registered_specs = BenchmarkHookRegistry.list_option_specs(config.name)
        if "seed" in registered_specs and "seed" not in benchmark_options and opts.get("seed") is not None:
            benchmark_options["seed"] = opts["seed"]

        construction_output_dir = self._resolve_construction_output_dir(config, benchmark_class)

        benchmark_instance = get_benchmark_instance(
            config,
            system_profile,
            benchmark_class=benchmark_class,
            output_dir=construction_output_dir,
            verbose=self._verbosity.level,
            quiet=self._verbosity.quiet,
            benchmark_options=benchmark_options,
            instantiate_fn=instantiate_benchmark_class,
        )

        if getattr(benchmark_class, "DATA_SOURCE_BENCHMARK", None) is None:
            source_name = _local_datagen_name(benchmark_instance, config.name)
            if source_name != config.name.lower() and self.custom_output_dir is None:
                shared_path = self.directory_manager.get_datagen_path(source_name, config.scale_factor)
                benchmark_instance.output_dir = shared_path

        return benchmark_instance

    def _resolve_construction_output_dir(self, config: BenchmarkConfig, benchmark_class) -> Optional[Union[str, Path]]:
        if self.custom_output_dir:
            from benchbox.utils.cloud_storage import is_cloud_path

            if is_cloud_path(self.custom_output_dir):
                return None
            return self.custom_output_dir

        data_source = getattr(benchmark_class, "DATA_SOURCE_BENCHMARK", None)
        source_name = (data_source or config.name).lower()
        return self.directory_manager.get_datagen_path(source_name, config.scale_factor)

    def _get_platform_config(
        self,
        database_config,
        system_profile,
        benchmark_name: Optional[str] = None,
        scale_factor: Optional[float] = None,
        tuning_config: Optional[Any] = None,
        benchmark: Optional["BaseBenchmark"] = None,
    ) -> dict[str, Any]:
        if benchmark is not None:
            data_source = getattr(benchmark, "get_data_source_benchmark", lambda: None)()
            if data_source:
                benchmark_name = data_source

        return _core_get_platform_config(
            database_config,
            system_profile,
            benchmark_name=benchmark_name,
            scale_factor=scale_factor,
            tuning_config=tuning_config,
        )

    def _create_benchmark_instance(self, config: BenchmarkConfig, system_profile):
        return self._get_benchmark_instance(config, system_profile)

    def set_custom_output_dir(self, output_dir: str) -> None:
        self.custom_output_dir = output_dir

    def execute_benchmark(
        self,
        config: BenchmarkConfig,
        system_profile,
        database_config,
        phases_to_run=None,
        progress=None,
        execution_context: ExecutionContext | None = None,
    ) -> BenchmarkResults:

        self.console.print(f"[blue]Initializing {config.name} benchmark...[/blue]")

        try:
            benchmark = self._get_benchmark_instance(config, system_profile)
            self.console.print(
                f"[green]✅[/green] Loaded benchmark: [cyan]{getattr(benchmark, '_name', config.name)}[/cyan]"
            )
            self._warn_on_variant_comparability_issues(benchmark)

            platform_cfg = (
                self._get_platform_config(
                    database_config,
                    system_profile,
                    benchmark_name=config.name,
                    scale_factor=config.scale_factor,
                    tuning_config=config.options.get("unified_tuning_configuration") if config.options else None,
                    benchmark=benchmark,
                )
                if database_config is not None
                else None
            )

            self._apply_default_cloud_output_dir(database_config)
            output_root = self._resolve_output_root(config, benchmark, platform_cfg)

            self._warn_on_execute_without_load(config, database_config, phases_to_run)

            opts = getattr(config, "options", {}) or {}
            monitor = progress.get_monitor() if progress is not None else None

            def build_adapter(*, execution_mode, output_root, phases):
                return self._build_platform_adapter(
                    database_config, execution_mode, output_root, opts, platform_cfg, benchmark, phases, config
                )

            return execute_run(
                config=config,
                benchmark_instance=benchmark,
                database_config=database_config,
                system_profile=system_profile,
                platform_config=platform_cfg,
                output_root=output_root,
                phases_to_run=phases_to_run,
                adapter_factory=build_adapter,
                verbosity=self._verbosity,
                monitor=monitor,
                execution_context=execution_context,
            )

        except Exception as e:
            if database_config and self._should_offer_credential_setup(database_config, e):
                if self._offer_and_run_credential_setup(database_config.type):
                    self.console.print("[cyan]Retrying benchmark execution with new credentials...[/cyan]\n")
                    return self.execute_benchmark(
                        config=config,
                        database_config=database_config,
                        system_profile=system_profile,
                        phases_to_run=phases_to_run,
                        execution_context=execution_context,
                    )

            self.console.print(f"[red]❌ Benchmark execution failed: {e}[/red]")
            return _build_failure_result(config, e)

    def _warn_on_variant_comparability_issues(self, benchmark) -> None:
        info_getter = getattr(benchmark, "get_benchmark_info", None)
        if info_getter is None:
            return
        try:
            summary = (info_getter() or {}).get("variant_comparability") or {}
        except Exception:
            return
        issue_count = summary.get("issue_count")
        if not issue_count:
            return
        self.console.print(
            "[yellow]⚠️  Read-primitives variant contracts report "
            f"{issue_count} comparability issue(s); cross-dialect comparisons "
            "for the affected queries may not be like-for-like.[/yellow]"
        )

    def _warn_on_execute_without_load(self, config, database_config, phases_to_run) -> None:
        if database_config is None:
            return
        phases = resolve_lifecycle_phases(phases_to_run)
        if not (phases.execute and not phases.load):
            return
        test_execution_type = getattr(config, "test_execution_type", "standard")
        readonly_tests = ["power", "throughput"]
        cloud_platforms = ["databricks", "snowflake", "bigquery", "redshift"]
        if test_execution_type not in readonly_tests and database_config.type.lower() in cloud_platforms:
            self.console.print(
                "[yellow]⚠️  Executing without load phase - assuming data already exists in database[/yellow]"
            )

    def _apply_default_cloud_output_dir(self, database_config) -> None:
        if self.custom_output_dir or not database_config:
            return
        from benchbox.security.credentials import CredentialManager

        deployment_mode = resolved_deployment_mode(database_config)
        if not PlatformRegistry.requires_cloud_storage_for_deployment(database_config.type, deployment_mode):
            return
        cred_manager = CredentialManager()
        if not cred_manager.has_credentials(database_config.type):
            return
        creds = cred_manager.get_platform_credentials(database_config.type)
        default_output = creds.get("default_output_location") if creds else None
        if default_output:
            self.custom_output_dir = default_output
            self.console.print(f"[dim]Using default output location from credentials: {default_output}[/dim]")

    def _resolve_output_root(self, config: BenchmarkConfig, benchmark, platform_cfg):
        if self.custom_output_dir:
            return self._resolve_custom_output_root(config, benchmark, platform_cfg)

        data_source = getattr(benchmark, "get_data_source_benchmark", lambda: None)()
        if data_source:
            return None
        return str(self.directory_manager.get_datagen_path(config.name.lower(), config.scale_factor))

    def _resolve_custom_output_root(self, config: BenchmarkConfig, benchmark, platform_cfg):
        from benchbox.utils.cloud_storage import is_cloud_path

        if not is_cloud_path(self.custom_output_dir):
            return self.custom_output_dir

        source_name = _local_datagen_name(benchmark, config.name)
        local_cache_path = self.directory_manager.get_datagen_path(source_name, config.scale_factor)

        if is_databricks_path(self.custom_output_dir):
            from benchbox.utils.cloud_storage import DatabricksPath

            output_root = DatabricksPath(local_cache_path, self.custom_output_dir)
        else:
            from benchbox.utils.cloud_storage import CloudStagingPath

            output_root = CloudStagingPath(local_cache_path, self.custom_output_dir)
            self.console.print(f"[dim]Using local cache: {local_cache_path}[/dim]")
            self.console.print(f"[dim]Cloud target: {self.custom_output_dir}[/dim]")

        if platform_cfg is not None:
            platform_cfg["staging_root"] = self.custom_output_dir
        return output_root

    def _build_platform_adapter(
        self, database_config, execution_mode, output_root, opts, platform_cfg, benchmark, phases, config
    ):
        if database_config is None or not (phases.load or phases.execute):
            return None
        if execution_mode == "dataframe":
            self.console.print("[cyan]Using DataFrame execution mode[/cyan]")
            registered_option_names = PlatformHookRegistry.list_option_specs(database_config.type)
            dataframe_options = {
                key: value
                for key, value in (database_config.options or {}).items()
                if key in registered_option_names and key not in DATABASE_ONLY_PLATFORM_OPTIONS
            }
            return get_adapter(
                database_config.type,
                mode="dataframe",
                working_dir=output_root,
                verbose=self._verbosity.verbose if self._verbosity else False,
                very_verbose=self._verbosity.very_verbose if self._verbosity else False,
                tuning_config=opts.get("df_tuning_config"),
                execution_engine=getattr(database_config, "execution_engine", None) or "default",
                **dataframe_options,
            )
        adapter = get_platform_adapter(database_config.type, **(platform_cfg or {}))
        if adapter and benchmark:
            adapter.benchmark_instance = benchmark
            adapter.scale_factor = config.scale_factor
        return adapter

    def _prepare_run_config(self, config: BenchmarkConfig, database_config) -> RunConfig:
        tuning_config = None
        if config.options:
            tuning_config = config.options.get("unified_tuning_configuration")

        database_path = self.directory_manager.get_database_path(
            config.name,
            config.scale_factor,
            database_config.type,
            tuning_config=tuning_config,
        )

        return resolve_run_config(config, database_path=database_path, verbosity=self._verbosity)

    def _should_offer_credential_setup(self, database_config, error: Exception) -> bool:
        if not database_config:
            return False

        platform = database_config.type.lower()

        cloud_platforms = ["snowflake", "bigquery", "databricks", "redshift", "singlestore"]
        if platform not in cloud_platforms:
            return False

        error_msg = str(error).lower()
        credential_keywords = [
            "configuration requires",
            "missing credentials",
            "credentials not found",
            "authentication required",
            "requires account",
            "requires username",
            "requires password",
            "no credentials",
        ]

        return any(keyword in error_msg for keyword in credential_keywords)

    def _offer_and_run_credential_setup(self, platform: str) -> bool:
        from rich.prompt import Confirm

        from benchbox.cli.commands.setup import run_platform_credential_setup

        self.console.print(f"\n[yellow]⚠️  {platform.capitalize()} credentials not found[/yellow]")
        self.console.print(f"\nTo use {platform.capitalize()}, you need to configure credentials.")

        if not Confirm.ask("\n🔧 Would you like to set up credentials now?", default=True):
            self.console.print("[yellow]Skipping credential setup[/yellow]")
            self.console.print(f"\n[dim]To set up later, run: benchbox setup --platform {platform}[/dim]")
            return False

        success = run_platform_credential_setup(platform, self.console, show_welcome=True)

        if success:
            self.console.print("\n[green]✅ Credentials configured! Continuing with benchmark...[/green]\n")
            return True
        else:
            self.console.print("\n[red]❌ Credential setup failed[/red]")
            return False
