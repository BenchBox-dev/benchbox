from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

from benchbox.utils.cloud_storage import create_path_handler

if TYPE_CHECKING:
    from benchbox.core.results.models import BenchmarkResults
    from benchbox.core.validation import ValidationResult


class BenchmarkResultValidationMixin:
    scale_factor: float
    output_dir: Any

    @property
    def benchmark_name(self) -> str:
        return getattr(self, "_name", type(self).__name__)

    def create_enhanced_benchmark_result(
        self,
        platform: str,
        query_results: list[dict[str, Any]],
        execution_metadata: Optional[dict[str, Any]] = None,
        phases: Optional[dict[str, dict[str, Any]]] = None,
        resource_utilization: Optional[dict[str, Any]] = None,
        performance_characteristics: Optional[dict[str, Any]] = None,
        duration_seconds: Optional[float] = None,
        **kwargs: Any,
    ) -> BenchmarkResults:
        if hasattr(self, "_impl") and hasattr(self._impl, "create_enhanced_benchmark_result"):
            return self._impl.create_enhanced_benchmark_result(
                platform=platform,
                query_results=query_results,
                execution_metadata=execution_metadata,
                phases=phases,
                resource_utilization=resource_utilization,
                performance_characteristics=performance_characteristics,
                duration_seconds=duration_seconds,
                **kwargs,
            )

        from benchbox.core.results.result_factory import build_enhanced_benchmark_result

        result = build_enhanced_benchmark_result(
            benchmark=self,
            platform=platform,
            query_results=query_results,
            execution_metadata=execution_metadata,
            phases=phases,
            resource_utilization=resource_utilization,
            performance_characteristics=performance_characteristics,
            duration_seconds=duration_seconds,
            **kwargs,
        )
        attach_snapshot = getattr(self, "_attach_performance_snapshot", None)
        if callable(attach_snapshot):
            attach_snapshot(result, performance_characteristics, **kwargs)
        return result

    def create_minimal_benchmark_result(
        self,
        *,
        validation_status: str,
        validation_details: Optional[dict[str, Any]] = None,
        duration_seconds: float = 0.0,
        platform: str = "unknown",
        execution_metadata: Optional[dict[str, Any]] = None,
        system_profile: Optional[dict[str, Any]] = None,
        phases: Optional[dict[str, dict[str, Any]]] = None,
        **overrides: Any,
    ) -> BenchmarkResults:

        metadata: dict[str, Any] = {
            "result_type": "minimal",
            "status": validation_status,
        }
        base_identifier = str(getattr(self, "name", None) or self.benchmark_name)
        metadata.setdefault(
            "benchmark_id",
            base_identifier.lower().replace(" ", "_").replace("-", "_"),
        )
        if execution_metadata:
            metadata.update(execution_metadata)

        result = self.create_enhanced_benchmark_result(
            platform=platform,
            query_results=[],
            execution_metadata=metadata,
            phases=self._minimal_result_phases(phases),
            duration_seconds=duration_seconds,
            validation_status=validation_status,
            validation_details=validation_details or {},
            system_profile=system_profile or {},
            **overrides,
        )
        result._benchmark_id_override = metadata["benchmark_id"]
        return result

    def _minimal_result_phases(
        self,
        phases: Optional[dict[str, dict[str, Any]]],
    ) -> Optional[dict[str, dict[str, Any]]]:
        return phases

    def _resolve_output_dir(self, output_dir: Optional[Union[str, Path]] = None) -> Union[Path, Any]:

        candidate = output_dir if output_dir is not None else getattr(self, "output_dir", None)
        if candidate is None:
            raise RuntimeError(
                "Benchmark output directory is not configured. Set 'benchmark.output_dir' "
                "or pass output_root to the lifecycle runner."
            )

        handler = create_path_handler(candidate)
        self.output_dir = handler
        return handler

    def validate_preflight(
        self,
        *,
        output_dir: Optional[Union[str, Path]] = None,
        benchmark_name: Optional[str] = None,
    ) -> ValidationResult:

        from benchbox.core.validation import DataValidationEngine

        resolved_dir = self._resolve_output_dir(output_dir)
        engine = DataValidationEngine()
        benchmark_id = self._validation_benchmark_id(benchmark_name)
        return engine.validate_preflight_conditions(benchmark_id, self.scale_factor, resolved_dir)

    def validate_manifest(
        self,
        *,
        manifest_path: Optional[Union[str, Path]] = None,
        benchmark_name: Optional[str] = None,
    ) -> ValidationResult:

        from benchbox.core.validation import DataValidationEngine, ValidationResult as CoreValidationResult

        resolved_dir = self._resolve_output_dir()
        manifest_candidate = manifest_path
        if manifest_candidate is None and hasattr(resolved_dir, "joinpath"):
            manifest_candidate = resolved_dir.joinpath("_datagen_manifest.json")

        if manifest_candidate is None:
            return CoreValidationResult(
                is_valid=False,
                errors=["Manifest path is not available"],
                warnings=[],
                details={"benchmark": (benchmark_name or self._validation_benchmark_id(None))},
            )

        engine = DataValidationEngine()
        manifest_path_obj = Path(manifest_candidate) if isinstance(manifest_candidate, str) else manifest_candidate
        return engine.validate_generated_data(manifest_path_obj)

    def validate_loaded_data(
        self,
        connection: Any,
        *,
        benchmark_name: Optional[str] = None,
    ) -> ValidationResult:

        from benchbox.core.validation import DatabaseValidationEngine

        engine = DatabaseValidationEngine()
        benchmark_id = self._validation_benchmark_id(benchmark_name)
        return engine.validate_loaded_data(connection, benchmark_id, self.scale_factor)

    def _validation_benchmark_id(self, benchmark_name: Optional[str]) -> str:
        if benchmark_name:
            return benchmark_name.lower()
        getter = getattr(self, "_get_benchmark_name", None)
        if callable(getter):
            return str(getter()).lower()
        return str(getattr(self, "name", self.benchmark_name)).lower()
