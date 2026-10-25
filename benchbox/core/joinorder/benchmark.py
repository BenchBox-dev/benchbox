# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import shutil
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

from benchbox.base import BaseBenchmark
from benchbox.core.data_fetch import ExtractionRequiredError, fetch_data, load_manifest
from benchbox.core.query_catalog_base import TranslatableQueryMixin
from benchbox.core.query_utils import get_queries_with_translation
from benchbox.utils.clock import elapsed_seconds, mono_time

from .queries import JoinOrderQueryManager
from .schema import JoinOrderSchema

if TYPE_CHECKING:
    from benchbox.core.dataframe.query import QueryRegistry
    from benchbox.core.tuning import UnifiedTuningConfiguration


class JoinOrderBenchmark(TranslatableQueryMixin, BaseBenchmark):
    _source_dialect = "postgres"

    data_manifest_path = Path(__file__).with_name("data_manifest.toml")

    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Union[str, Path] | None = None,
        queries_dir: str | None = None,
        verbose: int | bool = 0,
        *,
        parallel: int = 1,
        force_regenerate: bool = False,
        **kwargs: Any,
    ) -> None:
        if not isinstance(parallel, int) or parallel < 1:
            raise ValueError(f"parallel must be a positive integer, got {parallel}")
        if abs(float(scale_factor) - 1.0) > 1e-9:
            raise ValueError(
                "joinorder now uses canonical IMDb 2013 data and accepts only scale_factor=1.0; "
                "use joinorder_synthetic for scaled synthetic smoke-test data."
            )

        quiet = kwargs.pop("quiet", False)

        super().__init__(
            scale_factor=scale_factor,
            output_dir=output_dir,
            verbose=verbose,
            quiet=quiet,
        )

        self.parallel = parallel
        self.force_regenerate = force_regenerate

        self.queries_dir = queries_dir
        self._schema = JoinOrderSchema()
        self._query_manager = JoinOrderQueryManager(queries_dir)
        self._data_manifest = load_manifest(self.data_manifest_path)

    def generate_data(self) -> list[Path]:
        self.log_verbose("Ensuring canonical JoinOrder IMDb 2013 data is available...")

        start_time = mono_time()
        if self.force_regenerate:
            self._clear_cached_data()
        data_dir = self._fetch_and_verify_data()
        data_files = [data_dir / table.file for table in self._data_manifest.tables]
        self.tables = {table.name: data_dir / table.file for table in self._data_manifest.tables}
        fetch_time = elapsed_seconds(start_time)

        self.log_verbose(f"Verified {len(data_files)} canonical Parquet files in {fetch_time:.2f}s")

        return data_files

    def _clear_cached_data(self) -> None:
        for table in self._data_manifest.tables:
            (self.output_dir / table.file).unlink(missing_ok=True)
        archive_name = Path(self._data_manifest.url).name or "joinorder-imdb-2013-v1.tar.zst"
        (self.output_dir / archive_name).unlink(missing_ok=True)

    def _fetch_and_verify_data(self) -> Path:
        try:
            return fetch_data("joinorder", self.data_manifest_path, self.output_dir)
        except ExtractionRequiredError as exc:
            self._extract_tar_zst(Path(exc.archive_path), Path(exc.output_dir))
            return fetch_data("joinorder", self.data_manifest_path, self.output_dir)

    @staticmethod
    def _extract_tar_zst(archive_path: Path, output_dir: Path) -> None:
        try:
            import zstandard as zstd
        except ImportError as exc:
            raise RuntimeError("zstandard is required to extract canonical JoinOrder data") from exc

        output_dir.mkdir(parents=True, exist_ok=True)
        output_root = output_dir.resolve()
        with archive_path.open("rb") as raw:
            with zstd.ZstdDecompressor().stream_reader(raw) as reader:
                with tarfile.open(fileobj=reader, mode="r|") as tar:
                    for member in tar:
                        target = (output_dir / member.name).resolve()
                        if output_root not in (target, *target.parents):
                            raise RuntimeError(f"unsafe path in JoinOrder archive: {member.name}")
                        if member.isdir():
                            target.mkdir(parents=True, exist_ok=True)
                            continue
                        if not member.isfile():
                            raise RuntimeError(f"unsupported archive member in JoinOrder archive: {member.name}")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        extracted = tar.extractfile(member)
                        if extracted is None:
                            raise RuntimeError(f"unable to extract JoinOrder archive member: {member.name}")
                        with extracted, target.open("wb") as output:
                            shutil.copyfileobj(extracted, output)

    def get_schema(self) -> dict[str, dict]:
        return self._schema._tables

    def get_create_tables_sql(
        self,
        dialect: str = "sqlite",
        tuning_config: UnifiedTuningConfiguration | None = None,
    ) -> str:
        return self._schema.get_create_tables_sql(dialect)

    def get_table_names(self) -> list[str]:
        return self._schema.get_table_names()

    def get_query(
        self,
        query_id: str,
        *,
        params: dict[str, Any] | None = None,
        dialect: str | None = None,
    ) -> str:
        if params is not None:
            raise ValueError("JoinOrder queries are static and don't accept parameters")
        query = self._query_manager.get_query(query_id)
        if dialect:
            return self.translate_query_text(query, dialect)
        return query

    def get_queries(self, dialect: str | None = None) -> dict[str, str]:
        return get_queries_with_translation(self._query_manager, dialect, self.translate_query_text)

    @property
    def query_manager(self) -> JoinOrderQueryManager:
        return self._query_manager

    def get_all_queries(self) -> dict[str, str]:
        return self._query_manager.get_all_queries()

    def get_query_ids(self) -> list[str]:
        return self._query_manager.get_query_ids()

    def get_query_count(self) -> int:
        return self._query_manager.get_query_count()

    def get_queries_by_complexity(self) -> dict[str, list[str]]:
        return self._query_manager.get_queries_by_complexity()

    def get_queries_by_pattern(self) -> dict[str, list[str]]:
        return self._query_manager.get_queries_by_pattern()

    def load_queries_from_directory(self, queries_dir: str) -> None:
        self._query_manager = JoinOrderQueryManager(queries_dir)

    def get_table_info(self, table_name: str) -> dict[str, Any]:
        return self._schema.get_table_info(table_name)

    def get_relationship_tables(self) -> list[str]:
        return self._schema.get_relationship_tables()

    def get_dimension_tables(self) -> list[str]:
        return self._schema.get_dimension_tables()

    def get_estimated_data_size(self) -> int:
        return sum(
            (self.output_dir / table.file).stat().st_size
            for table in self._data_manifest.tables
            if (self.output_dir / table.file).exists()
        )

    def get_table_row_count(self, table_name: str) -> int:
        try:
            return self._data_manifest.table(table_name).row_count
        except KeyError:
            return 0

    def validate_query(self, query_id: str) -> bool:
        try:
            query = self.get_query(query_id)
            query_upper = query.upper()
            required_keywords = ["SELECT", "FROM"]
            return all(keyword in query_upper for keyword in required_keywords)
        except Exception:
            return False

    def get_benchmark_info(self) -> dict[str, Any]:
        return {
            "benchmark_name": "Join Order Benchmark",
            "description": "Canonical IMDb 2013 Join Order Benchmark",
            "scale_factor": self.scale_factor,
            "output_dir": str(self.output_dir),
            "queries_dir": self.queries_dir,
            "total_queries": self.get_query_count(),
            "total_tables": len(self.get_table_names()),
            "relationship_tables": len(self.get_relationship_tables()),
            "dimension_tables": len(self.get_dimension_tables()),
            "estimated_size_bytes": self.get_estimated_data_size(),
            "dataset_version": self._data_manifest.dataset_version,
            "data_archive_hash": self._data_manifest.data_archive_hash,
            "query_complexity_distribution": self.get_queries_by_complexity(),
            "join_pattern_distribution": self.get_queries_by_pattern(),
            "reference_paper": "How Good Are Query Optimizers, Really? (VLDB 2015)",
            "authors": "Viktor Leis, Andrey Gubichev, Atanas Mirchev, Peter Boncz, Alfons Kemper, Thomas Neumann",
        }

    def get_dataframe_queries(self) -> QueryRegistry:
        from benchbox.core.joinorder.dataframe_queries import get_dataframe_queries

        return get_dataframe_queries()

    def get_dataframe_skip_queries(self) -> list[str]:
        from benchbox.core.joinorder.dataframe_queries import get_untranslated_dataframe_query_ids

        return get_untranslated_dataframe_query_ids()

    def __repr__(self) -> str:
        return f"JoinOrderBenchmark(scale_factor={self.scale_factor}, queries={self.get_query_count()})"


from benchbox.core.hooks.benchmark_hooks import (
    BenchmarkHookRegistry,
    BenchmarkOptionSpec,
)

BenchmarkHookRegistry.register_option_specs(
    "joinorder",
    BenchmarkOptionSpec(
        name="queries_dir",
        help="Directory containing custom query files",
        aliases=("queries-dir",),
    ),
    BenchmarkOptionSpec(
        name="force_regenerate",
        parser=lambda v: v.strip().lower() in ("true", "1", "yes"),
        help=(
            "Refresh the canonical IMDb 2013 cache by deleting, re-downloading "
            "the full ~1.2 GB archive, extracting it, and re-verifying Parquet files"
        ),
        aliases=("force-regenerate",),
    ),
    benchmark_class=JoinOrderBenchmark,
)
