import contextlib
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Union

from benchbox.core.throughput.containment import check_phase_boundary
from benchbox.core.throughput.entrypoints import (
    require_adapter,
    require_stream_minimum,
    warn_legacy_throughput_api,
)
from benchbox.core.tpc_patterns import generate_official_benchmark_audit_trail
from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tpch.maintenance_test import (
    TPCHMaintenanceTest,
    TPCHMaintenanceTestResult,
)
from benchbox.core.tpch.power_test import TPCHPowerTest, TPCHPowerTestResult
from benchbox.core.tpch.refresh import generate_refresh_update_sets, require_refresh_support
from benchbox.utils.clock import elapsed_seconds, mono_time


@dataclass
class TPCHOfficialBenchmarkConfig:
    scale_factor: float = 1.0
    num_streams: int = 2
    seed: Optional[int] = None
    power_test_enabled: bool = True
    throughput_test_enabled: bool = True
    maintenance_test_enabled: bool = True
    validation_enabled: bool = True
    audit_trail: bool = True
    output_dir: Optional[Path] = None
    verbose: bool = False
    tpch_refresh_functions: bool = False
    tpch_refresh_first_set: int = 1


@dataclass
class TPCHOfficialBenchmarkResult:
    config: TPCHOfficialBenchmarkConfig
    start_time: str
    end_time: str
    total_time: float
    power_test_result: Optional[TPCHPowerTestResult]
    throughput_test_result: Optional[Any]
    maintenance_test_result: Optional[TPCHMaintenanceTestResult]
    power_at_size: float
    throughput_at_size: float
    success: bool
    errors: list[str]
    compliance_validated: bool = False
    audit_trail_saved: bool = False


def _close_quietly(connection: Any) -> None:
    close = getattr(connection, "close", None)
    if callable(close):
        with contextlib.suppress(Exception):
            close()


class TPCHOfficialBenchmark:
    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Optional[Union[str, Path]] = None,
        verbose: bool = False,
        **kwargs: Any,
    ) -> None:
        benchmark_kwargs = dict(kwargs)
        benchmark_kwargs.setdefault("official", True)
        self.benchmark = TPCHBenchmark(
            scale_factor=scale_factor, output_dir=output_dir, verbose=verbose, **benchmark_kwargs
        )

        self.config = TPCHOfficialBenchmarkConfig(
            scale_factor=scale_factor,
            output_dir=Path(output_dir) if output_dir else None,
            verbose=verbose,
            **kwargs,
        )

    def _run_power_phase(
        self,
        connection_factory: Callable[[], Any],
        config: TPCHOfficialBenchmarkConfig,
        result: TPCHOfficialBenchmarkResult,
        adapter: Any,
    ) -> None:
        if config.power_test_enabled:
            try:
                refresh_temp_directory = None
                connection = None
                try:
                    refresh_update_set = None
                    if config.tpch_refresh_functions:
                        require_adapter("TPCHOfficialBenchmark.refresh_functions", adapter)
                        require_refresh_support(adapter)
                        refresh_temp_directory = tempfile.TemporaryDirectory(prefix="benchbox-tpch-refresh-")
                        first_set = config.tpch_refresh_first_set
                        refresh_update_set = generate_refresh_update_sets(
                            config.scale_factor,
                            first_set,
                            1,
                            Path(refresh_temp_directory.name),
                        )[first_set]

                    connection = connection_factory()
                    refresh_connection_factory = None
                    if refresh_update_set is not None:

                        def refresh_connection_factory():
                            refresh_connection = connection_factory()
                            if refresh_connection is connection:
                                raise RuntimeError("TPC-H refresh functions require a separate database session")
                            return refresh_connection

                    power_result = TPCHPowerTest(
                        benchmark=self.benchmark,
                        connection=connection,
                        scale_factor=config.scale_factor,
                        seed=config.seed,
                        verbose=config.verbose,
                        refresh_update_set=refresh_update_set,
                        refresh_connection_factory=refresh_connection_factory,
                    ).run()
                finally:
                    _close_quietly(connection)
                    if refresh_temp_directory is not None:
                        refresh_temp_directory.cleanup()
                result.power_test_result = power_result
                if power_result.success and power_result.queries_successful == power_result.queries_executed > 0:
                    result.power_at_size = power_result.power_at_size
                else:
                    result.errors.append("Power Test failed: Power@Size withheld from results.")
                    result.success = False

            except Exception as e:
                result.errors.append(f"Power Test failed: {e}")
                result.success = False

    def _run_throughput_phase(
        self,
        connection_factory: Callable[[], Any],
        config: TPCHOfficialBenchmarkConfig,
        result: TPCHOfficialBenchmarkResult,
        adapter: Any,
    ) -> None:
        if config.throughput_test_enabled:
            try:
                require_stream_minimum(config.num_streams, "num_streams")
                shared_connection = connection_factory()
                try:
                    throughput_result = adapter._run_routed_throughput(
                        self.benchmark,
                        shared_connection,
                        {
                            "num_streams": config.num_streams,
                            "scale_factor": config.scale_factor,
                            "verbose": config.verbose,
                            "tpch_refresh_functions": config.tpch_refresh_functions,
                            "tpch_refresh_first_set": config.tpch_refresh_first_set,
                            **({"seed": config.seed} if config.seed is not None else {}),
                        },
                    )
                finally:
                    _close_quietly(shared_connection)
                result.throughput_test_result = throughput_result
                if throughput_result.success and throughput_result.throughput_at_size:
                    result.throughput_at_size = throughput_result.throughput_at_size
                else:
                    result.errors.append("Throughput Test failed: Throughput@Size withheld from results.")
                    result.success = False

            except Exception as e:
                result.errors.append(f"Throughput Test failed: {e}")
                result.success = False

    def run_official_benchmark(
        self,
        connection_factory: Callable[[], Any],
        config: Optional[TPCHOfficialBenchmarkConfig] = None,
        *,
        adapter: Any = None,
        _warn_deprecated: bool = True,
    ) -> TPCHOfficialBenchmarkResult:
        if config is None:
            config = self.config
        if type(config.tpch_refresh_functions) is not bool:
            raise TypeError("tpch_refresh_functions must be a boolean")
        if isinstance(config.tpch_refresh_first_set, bool) or not isinstance(config.tpch_refresh_first_set, int):
            raise TypeError("tpch_refresh_first_set must be an integer")
        if config.tpch_refresh_first_set < 1:
            raise ValueError(f"tpch_refresh_first_set must be positive, got {config.tpch_refresh_first_set}")

        if _warn_deprecated:
            warn_legacy_throughput_api("TPCHOfficialBenchmark.run_official_benchmark", "tpch")
        if config.throughput_test_enabled:
            require_adapter("TPCHOfficialBenchmark.run_official_benchmark", adapter)
        if adapter is not None and (config.maintenance_test_enabled or config.tpch_refresh_functions is True):
            require_refresh_support(adapter)
        benchmark_start = mono_time()

        result = TPCHOfficialBenchmarkResult(
            config=config,
            start_time=datetime.now().isoformat(),
            end_time="",
            total_time=0.0,
            power_test_result=None,
            throughput_test_result=None,
            maintenance_test_result=None,
            power_at_size=0.0,
            throughput_at_size=0.0,
            success=True,
            errors=[],
        )

        try:
            self._run_power_phase(connection_factory, config, result, adapter)

            self._run_throughput_phase(connection_factory, config, result, adapter)

            maintenance_first_set = config.tpch_refresh_first_set
            if config.tpch_refresh_functions:
                if config.throughput_test_enabled:
                    maintenance_first_set += config.num_streams + 1
                elif config.power_test_enabled:
                    maintenance_first_set += 1
            if config.maintenance_test_enabled:
                boundary = check_phase_boundary(result.throughput_test_result)
                if not boundary.proceed:
                    refusal = f"Maintenance Test refused: {boundary.reason}"
                    result.errors.append(refusal)
                    result.success = False
                else:
                    try:
                        maintenance_result = TPCHMaintenanceTest(
                            connection_factory=connection_factory,
                            scale_factor=config.scale_factor,
                            output_dir=config.output_dir,
                            verbose=config.verbose,
                            first_set=maintenance_first_set,
                        ).run_maintenance_test(concurrent_with_queries=False, rf1_interval=0.0, rf2_interval=0.0)
                        result.maintenance_test_result = maintenance_result
                        if not maintenance_result.success:
                            result.errors.append("Maintenance Test failed: " + "; ".join(maintenance_result.errors))
                            result.success = False

                    except Exception as e:
                        result.errors.append(f"Maintenance Test failed: {e}")
                        result.success = False

            result.total_time = elapsed_seconds(benchmark_start)
            result.end_time = datetime.now().isoformat()

            return result

        except Exception as e:
            result.total_time = elapsed_seconds(benchmark_start)
            result.end_time = datetime.now().isoformat()
            result.success = False
            result.errors.append(f"Benchmark execution failed: {e}")
            return result

    def validate_compliance(self, result: TPCHOfficialBenchmarkResult) -> bool:
        if not result.success:
            return False

        if result.power_at_size <= 0 or result.throughput_at_size <= 0:
            return False

        return True

    def generate_audit_trail(
        self,
        result: TPCHOfficialBenchmarkResult,
        output_file: Optional[Union[str, Path]] = None,
    ) -> Path:
        return generate_official_benchmark_audit_trail(
            result=result,
            benchmark_title="TPC-H",
            benchmark_slug="tpch",
            output_file=output_file,
        )
