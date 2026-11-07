import contextlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, Union

from benchbox.core.throughput.containment import check_phase_boundary
from benchbox.core.throughput.entrypoints import (
    require_adapter,
    require_stream_minimum,
    warn_legacy_throughput_api,
)
from benchbox.core.tpc_patterns import generate_official_benchmark_audit_trail
from benchbox.core.tpcds.benchmark import TPCDSBenchmark
from benchbox.utils.clock import elapsed_seconds, mono_time

if TYPE_CHECKING:
    from benchbox.core.tpcds.power_test import TPCDSPowerTestResult
    from benchbox.core.tpcds.throughput_test import TPCDSThroughputTestResult


@dataclass
class TPCDSOfficialBenchmarkConfig:
    scale_factor: float = 1.0
    num_streams: int = 4
    power_test_enabled: bool = True
    throughput_test_enabled: bool = True
    maintenance_test_enabled: bool = True
    validation_enabled: bool = True
    audit_trail: bool = True
    output_dir: Optional[Path] = None
    verbose: bool = False
    seed: Optional[int] = None


@dataclass
class TPCDSOfficialBenchmarkResult:
    config: TPCDSOfficialBenchmarkConfig
    start_time: str
    end_time: str
    total_time: float
    power_test_result: Union["TPCDSPowerTestResult", dict[str, Any], None]
    throughput_test_result: Union["TPCDSThroughputTestResult", dict[str, Any], None]
    maintenance_test_result: Optional[dict[str, Any]]
    power_at_size: float
    throughput_at_size: float
    success: bool
    errors: list[str]
    compliance_validated: bool = False
    audit_trail_saved: bool = False


def _extract_metric(result: Any, attr: str, default: float = 0.0) -> float:
    if hasattr(result, attr):
        val = getattr(result, attr)
        if val is not None:
            return val
    if isinstance(result, dict):
        val = result.get(attr)
        if val is not None:
            return val
    return default


def _close_quietly(connection: Any) -> None:
    close = getattr(connection, "close", None)
    if callable(close):
        with contextlib.suppress(Exception):
            close()


def _phase_succeeded(result: Any) -> bool:
    if isinstance(result, dict):
        return result.get("success") is not False
    return getattr(result, "success", None) is not False


def _power_phase_complete(result: Any) -> bool:
    if not _phase_succeeded(result):
        return False
    return _extract_metric(result, "queries_successful", default=-1) == _extract_metric(
        result, "queries_executed", default=-1
    )


class TPCDSOfficialBenchmark:
    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Optional[Union[str, Path]] = None,
        verbose: bool = False,
        dialect: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        benchmark_kwargs = dict(kwargs)
        benchmark_kwargs.setdefault("official", True)
        self.benchmark = TPCDSBenchmark(
            scale_factor=scale_factor, output_dir=output_dir, verbose=verbose, **benchmark_kwargs
        )

        self.dialect = dialect

        self.config = TPCDSOfficialBenchmarkConfig(
            scale_factor=scale_factor,
            output_dir=Path(output_dir) if output_dir else None,
            verbose=verbose,
            **kwargs,
        )

    def run_official_benchmark(
        self,
        connection_factory: Callable[[], Any],
        config: Optional[TPCDSOfficialBenchmarkConfig] = None,
        *,
        adapter: Any = None,
    ) -> TPCDSOfficialBenchmarkResult:
        if config is None:
            config = self.config

        warn_legacy_throughput_api("TPCDSOfficialBenchmark.run_official_benchmark", "tpcds")
        if config.throughput_test_enabled:
            require_adapter("TPCDSOfficialBenchmark.run_official_benchmark", adapter)
        benchmark_start = mono_time()

        result = TPCDSOfficialBenchmarkResult(
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
            if config.power_test_enabled:
                try:
                    from benchbox.core.tpcds.power_test import TPCDSPowerTest

                    power_test = TPCDSPowerTest(
                        benchmark=self.benchmark,
                        connection_factory=connection_factory,
                        scale_factor=config.scale_factor,
                        verbose=config.verbose,
                        dialect=self.dialect,
                    )

                    power_result = power_test.run()
                    result.power_test_result = power_result
                    if _power_phase_complete(power_result):
                        result.power_at_size = _extract_metric(power_result, "power_at_size")
                    else:
                        result.errors.append("Power Test failed: Power@Size withheld from results.")
                        result.success = False

                except Exception as e:
                    result.errors.append(f"Power Test failed: {e}")
                    result.success = False

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
                            },
                        )
                    finally:
                        _close_quietly(shared_connection)
                    result.throughput_test_result = throughput_result
                    throughput_metric = _extract_metric(throughput_result, "throughput_at_size")
                    if not _phase_succeeded(throughput_result) or throughput_metric <= 0:
                        result.errors.append("Throughput Test failed: Throughput@Size withheld from results.")
                        result.success = False
                    else:
                        result.throughput_at_size = throughput_metric

                except Exception as e:
                    result.errors.append(f"Throughput Test failed: {e}")
                    result.success = False

            if config.maintenance_test_enabled:
                boundary = check_phase_boundary(result.throughput_test_result)
                if not boundary.proceed:
                    refusal = f"Maintenance Test refused: {boundary.reason}"
                    result.errors.append(refusal)
                    result.maintenance_test_result = {
                        "success": False,
                        "status": "contained",
                        "reason": refusal,
                        "outstanding_stream_ids": list(boundary.outstanding_stream_ids),
                    }
                    result.success = False
                else:
                    try:
                        from benchbox.core.tpcds.maintenance_test import (
                            TPCDSMaintenanceTest,
                            TPCDSMaintenanceTestConfig,
                        )

                        maintenance_test = TPCDSMaintenanceTest(
                            benchmark=self.benchmark,
                            connection_factory=connection_factory,
                            scale_factor=config.scale_factor,
                            verbose=config.verbose,
                            dialect=self.dialect,
                        )

                        maintenance_result = maintenance_test.run(
                            TPCDSMaintenanceTestConfig(
                                scale_factor=config.scale_factor,
                                verbose=config.verbose,
                                seed=config.seed,
                            )
                        )
                        result.maintenance_test_result = maintenance_result

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

    def validate_compliance(self, result: TPCDSOfficialBenchmarkResult) -> bool:
        if not result.success:
            return False

        if result.power_at_size <= 0 or result.throughput_at_size <= 0:
            return False

        return True

    def generate_audit_trail(
        self,
        result: TPCDSOfficialBenchmarkResult,
        output_file: Optional[Union[str, Path]] = None,
    ) -> Path:
        return generate_official_benchmark_audit_trail(
            result=result,
            benchmark_title="TPC-DS",
            benchmark_slug="tpcds",
            output_file=output_file,
        )
