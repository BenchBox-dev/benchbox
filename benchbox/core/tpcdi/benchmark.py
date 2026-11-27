# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DI (TPC-DI) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DI specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Union,
)

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import UnifiedTuningConfiguration

from benchbox.base import BaseBenchmark, GeneratorOutputDirMixin
from benchbox.core.results.status import (
    CLI_FAILURE_VALIDATION_STATUSES,
    normalize_validation_status,
    validation_status_is_non_clean,
)
from benchbox.core.results.tpcdi_audit import TPCDI_AUDIT_BLOCK_KEY
from benchbox.core.run_service import refuses_unavailable_benchmark
from benchbox.core.tpcdi.audit import FAILED_STATUS, AuditOutcome, run_audit
from benchbox.core.tpcdi.config import TPCDIConfig
from benchbox.core.tpcdi.etl.staging import prepare_xml_reader
from benchbox.core.tpcdi.phases import PhaseRecord, check_staging, run_initialization
from benchbox.core.tpcdi.schema import (
    TABLES,
    get_all_create_table_sql,
)
from benchbox.core.tpcdi.sources.digen import DigenResult, generate as generate_digen_sources
from benchbox.utils.run_deadline import enter_run_phase, own_run_deadline

DIGEN_SOURCE_DIRECTORY = "digen"


class TPCDIBenchmark(GeneratorOutputDirMixin, BaseBenchmark):
    SKIP_DATA_LOADING = True

    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Optional[Union[str, Path]] = None,
        config: Optional[TPCDIConfig] = None,
        **kwargs: Any,
    ):
        kwargs = dict(kwargs)
        quiet = kwargs.pop("quiet", False)

        super().__init__(scale_factor, quiet=quiet, **kwargs)

        self._name = "TPC-DI Benchmark"
        self._version = "1.0"
        self._description = "TPC-DI (Data Integration) Benchmark - Tests ETL and data integration performance"

        self.csv_normalize_booleans: bool = True

        if config is None:
            self.config = TPCDIConfig(
                scale_factor=scale_factor,
                output_dir=Path(output_dir) if isinstance(output_dir, str) else output_dir,
            )
        else:
            self.config = config

        self.output_dir = self.config.output_dir
        self.config.create_directories()
        assert self.output_dir is not None

        self.scale_factor = self.config.scale_factor

        self.tables: dict[str, Any] = {}
        self._digen_staging_dir: Optional[Path] = None
        self._initialization: Optional[PhaseRecord] = None
        self._measured_phases: Optional[tuple[PhaseRecord, tuple[PhaseRecord, ...]]] = None
        self._power_phases: Optional[tuple[PhaseRecord, tuple[PhaseRecord, ...], AuditOutcome]] = None

    def _sync_output_dir_to_generators(self, path: Any) -> None:
        super()._sync_output_dir_to_generators(path)
        config = getattr(self, "config", None)
        if config is None:
            return
        config.output_dir = path
        self.source_dir = config.source_dir
        self.staging_dir = config.staging_dir
        self.warehouse_dir = config.warehouse_dir

    @property
    def digen_staging_dir(self) -> Path:
        return self._digen_staging_dir or self.config.source_dir / DIGEN_SOURCE_DIRECTORY

    @digen_staging_dir.setter
    def digen_staging_dir(self, value: Union[str, Path]) -> None:
        self._digen_staging_dir = Path(value)

    def run_skipped_data_load(self, adapter: Any, connection: Any) -> dict[str, int]:
        self._measured_phases = None
        self._power_phases = None
        check_staging(self.digen_staging_dir, scale_factor=self.scale_factor)
        platform = adapter.get_target_dialect()
        prepare_xml_reader(
            connection,
            allow_third_party_extensions=bool(getattr(adapter, "allow_third_party_extensions", False)),
            platform=platform,
        )
        self._initialization = run_initialization(connection, platform=platform)
        return {}

    def take_initialization_record(self) -> Optional[PhaseRecord]:
        record, self._initialization = self._initialization, None
        return record

    def record_measured_phases(self, initialization: PhaseRecord, phases: tuple[PhaseRecord, ...]) -> None:
        self._measured_phases = (initialization, phases)

    def complete_power_test(self, adapter: Any, connection: Any) -> Optional[tuple[str, str, int]]:
        if self._measured_phases is None:
            return None
        (initialization, phases), self._measured_phases = self._measured_phases, None
        last = phases[-1]
        outcome = run_audit(
            connection, self.digen_staging_dir, latest_batch=last.batch_id, platform=adapter.get_target_dialect()
        )
        self.record_power_phases(initialization, phases, outcome)
        return (
            initialization.completed_at.isoformat(),
            last.completed_at.isoformat(),
            int((last.completed_mono - initialization.completed_mono) * 1000),
        )

    def record_power_phases(
        self, initialization: PhaseRecord, phases: tuple[PhaseRecord, ...], outcome: AuditOutcome
    ) -> None:
        self._power_phases = (initialization, phases, outcome)

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
    ) -> Any:
        if self._power_phases is not None:
            (initialization, measured, outcome), self._power_phases = self._power_phases, None
            incoming = normalize_validation_status(kwargs.get("validation_status"))
            if incoming not in CLI_FAILURE_VALIDATION_STATUSES and (
                outcome.status == FAILED_STATUS or not validation_status_is_non_clean(incoming)
            ):
                kwargs["validation_status"] = outcome.status
            kwargs["validation_details"] = {
                **(kwargs.get("validation_details") or {}),
                TPCDI_AUDIT_BLOCK_KEY: {
                    **outcome.as_block(),
                    "phases": [record.as_dict() for record in (initialization, *measured)],
                },
            }
        return super().create_enhanced_benchmark_result(
            platform,
            query_results,
            execution_metadata,
            phases,
            resource_utilization,
            performance_characteristics,
            duration_seconds,
            **kwargs,
        )

    @refuses_unavailable_benchmark("tpcdi")
    def generate_data(
        self,
        tables: Optional[list[str]] = None,
        output_format: str = "csv",
        seed: Optional[int] = None,
    ) -> list[Union[str, Path]]:
        raise NotImplementedError("TPC-DI source data is produced by generate_digen_source_data")

    def get_queries(self) -> dict[str, str]:
        return {}

    @refuses_unavailable_benchmark("tpcdi")
    def get_query(self, query_id: Union[int, str], *, params: Optional[dict[str, Any]] = None) -> str:
        raise ValueError(f"TPC-DI defines no BenchBox queries: {query_id}")

    @refuses_unavailable_benchmark("tpcdi")
    def run_benchmark(
        self,
        connection: Any,
        query_ids: Optional[list[Union[int, str]]] = None,
        fetch_results: bool = False,
        setup_database: bool = True,
    ) -> dict[str, Any]:
        raise ValueError("TPC-DI defines no BenchBox queries to run")

    def get_schema(self, dialect: str = "standard") -> dict[str, dict[str, Any]]:
        return TABLES

    def get_create_tables_sql(
        self,
        dialect: str = "standard",
        tuning_config: Optional["UnifiedTuningConfiguration"] = None,
    ) -> str:
        if tuning_config is not None:
            try:
                enable_primary_keys = tuning_config.primary_keys.enabled
                enable_foreign_keys = tuning_config.foreign_keys.enabled
            except AttributeError as e:
                raise RuntimeError(
                    f"Invalid tuning_config object (missing primary_keys or foreign_keys attributes): {e}"
                ) from e
            if enable_primary_keys or enable_foreign_keys:
                self.log_very_verbose(
                    "TPC-DI declares no primary or foreign keys, so the requested key constraints add nothing "
                    f"to the schema: primary_keys={enable_primary_keys}, foreign_keys={enable_foreign_keys}"
                )
        return get_all_create_table_sql(dialect)

    def generate_digen_source_data(self, timeout_seconds: Optional[int] = None) -> DigenResult:
        with own_run_deadline(timeout_seconds):
            enter_run_phase("generate")
            return generate_digen_sources(self.scale_factor, self.config.source_dir / DIGEN_SOURCE_DIRECTORY)
