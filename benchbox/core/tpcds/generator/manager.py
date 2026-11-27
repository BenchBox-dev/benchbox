from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from benchbox.utils.cloud_storage import CloudStorageGeneratorMixin, create_path_handler
from benchbox.utils.compression_mixin import CompressionMixin
from benchbox.utils.data_validation import BenchmarkDataValidator
from benchbox.utils.file_format import is_tpc_format
from benchbox.utils.generation_marker import track_generation
from benchbox.utils.native_datagen import NativeDatagenMixin, write_native_tables
from benchbox.utils.printing import emit

from ..compliance import TpcdsComplianceClass, validate_tpcds_scale
from .filesystem import FileArtifactMixin
from .runner import DsdgenRunnerMixin
from .streaming import StreamingGenerationMixin


class TPCDSDataGenerator(
    NativeDatagenMixin,
    CompressionMixin,
    CloudStorageGeneratorMixin,
    DsdgenRunnerMixin,
    StreamingGenerationMixin,
    FileArtifactMixin,
):
    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: str | Path | None = None,
        verbose: int | bool = 0,
        quiet: bool = False,
        parallel: int = 1,
        force_regenerate: bool = False,
        **kwargs,
    ) -> None:
        self._data_organization_config = kwargs.pop("data_organization", None)
        if self._data_organization_config is None:
            raw_config = os.getenv("BENCHBOX_DATA_ORGANIZATION_CONFIG_JSON")
            if raw_config:
                try:
                    from benchbox.core.data_organization.config import DataOrganizationConfig

                    self._data_organization_config = DataOrganizationConfig.from_dict(json.loads(raw_config))
                except Exception:
                    self._data_organization_config = None

        super().__init__(**kwargs)

        self.scale_factor = scale_factor
        self.output_dir = create_path_handler(output_dir) if output_dir else Path.cwd()
        if isinstance(verbose, bool):
            self.verbose_level = 1 if verbose else 0
        else:
            self.verbose_level = int(verbose or 0)
        self.verbose = self.verbose_level >= 1 and not quiet
        self.very_verbose = self.verbose_level >= 2 and not quiet
        self.quiet = bool(quiet)
        self.parallel = parallel
        self.force_regenerate = force_regenerate

        self.validator = BenchmarkDataValidator("tpcds", scale_factor)

        self._manifest_entries: dict[str, list[dict[str, str | int]]] = {}
        self._manifest_lock = threading.Lock()

        self._package_root = self._package_root_dir()
        resolved_path = self.resolve_dsdgen_path()

        self.dsdgen_path = resolved_path or (self._package_root / "_sources/tpc-ds/tools")
        self.dsdgen_available = resolved_path is not None
        self._dsdgen_error: Exception | None = None

        self._validate_parameters()

        self.tools_dir = self.dsdgen_path

        try:
            self.dsdgen_exe = self._find_or_build_dsdgen()
            self.dsdgen_available = self.dsdgen_exe.exists()
        except (FileNotFoundError, RuntimeError, PermissionError) as exc:
            self.dsdgen_exe = None
            self.dsdgen_available = False
            self._dsdgen_error = exc

    def _validate_parameters(self) -> None:
        self.compliance_class: TpcdsComplianceClass = validate_tpcds_scale(self.scale_factor)

        if self.parallel < 1:
            raise ValueError(f"Parallel processes must be >= 1, got {self.parallel}")

        if self.parallel > 64:
            raise ValueError(f"Too many parallel processes {self.parallel} (max 64)")

    @classmethod
    def _package_root_dir(cls) -> Path:
        return Path(__file__).parent.parent.parent.parent.parent

    @classmethod
    def _candidate_dsdgen_paths(cls) -> Iterator[Path]:
        package_root = cls._package_root_dir()

        yield package_root / "_sources/tpc-ds/tools"

        yield Path(__file__).parent.parent / "_sources/tpc-ds/tools"

        try:
            import benchbox

            module_path = Path(benchbox.__file__).parent
            yield module_path / "_sources/tpc-ds/tools"
        except ImportError:
            return

    @classmethod
    def resolve_dsdgen_path(cls) -> Path | None:
        for candidate in cls._candidate_dsdgen_paths():
            if candidate.exists():
                return candidate
        return None

    def has_dsdgen_sources(self) -> bool:
        return self.dsdgen_available

    def _known_table_names(self) -> list[str]:
        from benchbox.core.tpcds.constants import TPCDS_TABLE_NAMES

        return [*TPCDS_TABLE_NAMES, "dbgen_version"]

    def _raise_missing_dsdgen(self) -> None:
        message = (
            "TPC-DS native tools are not bundled with this build. "
            "Install the TPC-DS toolkit and place the compiled binaries under "
            f"{self._package_root / '_sources/tpc-ds/tools'} or supply sample data."
        )
        if self._dsdgen_error:
            message += f" Details: {self._dsdgen_error}"
        raise RuntimeError(message)

    def _generate_local(self, output_dir: Path | None = None) -> dict[str, list[Path]]:
        target_dir = self._prepare_output_dir(output_dir)

        should_regenerate, validation_result = self.validator.should_regenerate_data(target_dir, self.force_regenerate)

        if not should_regenerate:
            if self.verbose:
                emit(f"✅ Valid TPC-DS data found for scale factor {self.scale_factor}")
                self.validator.print_validation_report(validation_result, verbose=False)
                emit("   Skipping data generation")
            if self._data_organization_config is not None:
                return self._apply_organization_to_existing(target_dir)
            return self._gather_existing_table_files(target_dir)

        self._log_regeneration_reason(validation_result)

        with track_generation(target_dir):
            return self._regenerate(target_dir)

    def _regenerate(self, target_dir: Path) -> dict[str, list[Path]]:
        removed_stale = self._prune_stale_table_artifacts(target_dir)
        if removed_stale and self.verbose:
            emit(f"🧹 Removed {len(removed_stale)} stale TPC-DS table artifacts before regeneration")

        if self.uses_native_datagen:
            return self._generate_native(target_dir)
        sample_dir = self._get_sample_data_dir()
        if sample_dir is not None:
            result = self._generate_from_sample(sample_dir, target_dir)
            if result is not None:
                if self._data_organization_config is not None:
                    return self._apply_organization_to_existing(target_dir)
                return result

        self._run_dsdgen_native(target_dir)

        if self._data_organization_config is not None:
            return self._apply_organization_to_existing(target_dir)

        self._compress_raw_dat_files(target_dir)

        table_paths = self._gather_existing_table_files(target_dir)
        if self.should_use_compression() and table_paths and self.verbose:
            emit(f"\n📦 Generated {len(table_paths)} tables with streaming {self.compression_type} compression")

        self._validate_file_format_consistency(target_dir)

        self._write_manifest(target_dir, table_paths)

        return table_paths

    def _tables_with_organization(self) -> set[str]:
        config = self._data_organization_config
        organized = set()
        for table_name in self._known_table_names():
            if (
                config.get_sort_columns_for_table(table_name)
                or config.get_partition_columns_for_table(table_name)
                or config.get_cluster_columns_for_table(table_name)
            ):
                organized.add(table_name)
        return organized

    def _ensure_raw_sort_inputs(self, target_dir: Path, tables: set[str]) -> None:
        if not tables or not self.should_use_compression():
            return
        raw_paths = self._gather_existing_table_files(target_dir, use_compression=False)
        if all(raw_paths.get(table) for table in tables):
            return
        compressed_paths = self._gather_existing_table_files(target_dir, use_compression=True)
        compressor = self.get_compressor()
        for table in sorted(tables):
            if raw_paths.get(table):
                continue
            for source in compressed_paths.get(table, []):
                compressor.decompress_file(source)

    def _apply_organization_to_existing(self, target_dir: Path) -> dict[str, list[Path]]:
        organized_tables = self._tables_with_organization()
        self._ensure_raw_sort_inputs(target_dir, organized_tables)
        raw_table_paths = self._gather_existing_table_files(target_dir, use_compression=False)
        table_paths = self._apply_data_organization(target_dir, raw_table_paths)
        if self.should_use_compression():
            compressed_paths = self._gather_existing_table_files(target_dir, use_compression=True)
            for table, files in compressed_paths.items():
                if table not in organized_tables:
                    table_paths[table] = files
        self._write_manifest(target_dir, table_paths)
        return table_paths

    def _apply_data_organization(
        self,
        target_dir: Path,
        table_paths: dict[str, list[Path]],
    ) -> dict[str, list[Path]]:
        from benchbox.core.data_organization.sorting import SortedParquetWriter

        config = self._data_organization_config
        writer = SortedParquetWriter(config, schema_registry=self._build_schema_registry())
        result = dict(table_paths)

        for table_name, source_files in table_paths.items():
            sort_columns = config.get_sort_columns_for_table(table_name)
            partition_columns = config.get_partition_columns_for_table(table_name)
            cluster_columns = config.get_cluster_columns_for_table(table_name)
            if not sort_columns and not partition_columns and not cluster_columns:
                continue

            parquet_path = writer.write_sorted_parquet(
                table_name=table_name,
                source_files=source_files,
                output_dir=target_dir,
            )
            result[table_name] = [parquet_path]

        return result

    def _build_schema_registry(self) -> dict[str, dict[str, Any]]:
        from benchbox.core.tpcds.schema import TABLES

        schema_registry: dict[str, dict[str, Any]] = {}
        for table in TABLES:
            schema_registry[table.name.lower()] = {
                "name": table.name.lower(),
                "columns": [
                    {
                        "name": col.name,
                        "type": col.get_sql_type(),
                    }
                    for col in table.columns
                ],
            }
        return schema_registry

    def _generate_native(self, target_dir: Path) -> dict[str, list[Path]]:
        generation = write_native_tables("tpcds", self.scale_factor, target_dir)
        self.datagen_backend_version = generation.version
        self._manifest_entries = {
            table: [{"path": table_file.path.name, "size_bytes": table_file.size_bytes, "row_count": table_file.rows}]
            for table, table_file in generation.files.items()
        }
        table_paths = {table: [table_file.path] for table, table_file in generation.files.items()}
        self._write_manifest(target_dir, table_paths)
        return table_paths

    def _prepare_output_dir(self, output_dir: Path | None) -> Path:
        if self.uses_native_datagen:
            self.require_native_compatible_options()
        elif not self.dsdgen_available:
            self._raise_missing_dsdgen()

        target_dir = output_dir if output_dir is not None else self.output_dir
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            raise PermissionError(f"Cannot create output directory {target_dir}: {e}") from e

        if not os.access(target_dir, os.W_OK):
            raise PermissionError(f"Output directory {target_dir} is not writable")

        return target_dir

    def _log_regeneration_reason(self, validation_result) -> None:
        if not self.verbose:
            return
        if validation_result is not None and validation_result.issues:
            emit(f"⚠️  Data validation failed for scale factor {self.scale_factor}")
            self.validator.print_validation_report(validation_result, verbose=True)
        else:
            emit("🔄 Force regeneration requested")
        emit("   Generating TPC-DS data...")

    def _generate_from_sample(self, sample_dir: Path, target_dir: Path) -> dict[str, list[Path]] | None:
        if self.verbose:
            emit(f"⚡ Using bundled TPC-DS sample dataset for scale factor {self.scale_factor}")
        self._copy_sample_dataset(sample_dir, target_dir)

        if self.should_use_compression():
            for dat_file in list(target_dir.glob("*.dat")):
                if is_tpc_format(dat_file):
                    try:
                        self.compress_existing_file(dat_file, remove_original=True)
                    except Exception:
                        pass

        table_paths = self._gather_existing_table_files(target_dir)
        if table_paths:
            self._validate_file_format_consistency(target_dir)
            return table_paths
        return None

    def _compress_raw_dat_files(self, target_dir: Path) -> None:
        if not self.should_use_compression():
            return
        for dat_file in list(target_dir.glob("*.dat")) + list(target_dir.glob("*_*.dat")):
            try:
                self.compress_existing_file(dat_file, remove_original=True)
            except Exception:
                pass

    def generate(self) -> dict[str, list[Path]]:
        return self._handle_cloud_or_local_generation(self.output_dir, self._generate_local, self.verbose)

    def generate_tables(self, table_names: list[str]) -> dict[str, list[Path]]:
        if not self.dsdgen_available:
            self._raise_missing_dsdgen()

        known_tables = set(self._known_table_names())
        invalid_tables = [t for t in table_names if t not in known_tables]
        if invalid_tables:
            raise ValueError(f"Invalid table names: {invalid_tables}. Valid TPC-DS tables are: {sorted(known_tables)}")

        target_dir = self.output_dir
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            raise PermissionError(f"Cannot create output directory {target_dir}: {e}") from e

        if not os.access(target_dir, os.W_OK):
            raise PermissionError(f"Output directory {target_dir} is not writable")

        if self.verbose:
            emit(f"\nGenerating {len(table_names)} TPC-DS tables at scale factor {self.scale_factor}...")

        for table_name in table_names:
            if self.verbose:
                emit(f"  - {table_name}")
            self._generate_table_with_streaming(target_dir, table_name)

        table_paths = self._gather_existing_table_files(target_dir)

        filtered_paths = {k: v for k, v in table_paths.items() if k in table_names}

        if self.verbose:
            emit(f"\nGenerated {len(filtered_paths)} tables successfully")

        return filtered_paths


__all__ = ["TPCDSDataGenerator"]
