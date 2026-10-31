# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

from benchbox.base import BaseBenchmark, GeneratorOutputDirMixin
from benchbox.core.benchmark_mixins import DataGenerationMixin
from benchbox.core.clickbench.generator import ClickBenchDataGenerator
from benchbox.core.clickbench.queries import ClickBenchQueryManager
from benchbox.core.clickbench.schema import TABLES, get_create_table_sql
from benchbox.core.query_utils import get_queries_with_translation
from benchbox.core.simple_benchmark_mixin import SimpleBenchmarkMixin
from benchbox.core.utils.tuning import extract_constraint_flags

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import UnifiedTuningConfiguration


class ClickBenchBenchmark(GeneratorOutputDirMixin, SimpleBenchmarkMixin, DataGenerationMixin, BaseBenchmark):
    _benchmark_label = "ClickBench"
    _table_load_order = ["hits"]

    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Optional[Union[str, Path]] = None,
        **config: Any,
    ):

        config = dict(config)
        quiet = config.pop("quiet", False)

        super().__init__(scale_factor=scale_factor, output_dir=output_dir, quiet=quiet, **config)

        self._name = "ClickBench"
        self._version = "1.0"
        self._description = "ClickBench - Analytics benchmark for DBMS with web analytics workload"

        self.query_manager: ClickBenchQueryManager = ClickBenchQueryManager()
        self.data_generator = ClickBenchDataGenerator(scale_factor, self.output_dir, **config)

        self.tables = {}

    def _get_table_schema(self) -> dict[str, dict]:

        return TABLES

    def get_queries(self, dialect: Optional[str] = None) -> dict[str, str]:

        return get_queries_with_translation(self.query_manager, dialect, self.translate_query_text)

    def translate_query_text(self, query: str, dialect: str) -> str:

        from benchbox.utils.dialect_utils import translate_sql_query

        translated = translate_sql_query(
            query,
            dialect,
            source_dialect="clickhouse",
            identify=False,
            strict=True,
        )
        if "singlestore" in dialect.lower():
            translated = translated.replace("(?:www\\\\.)?([^/]+)", "(www\\\\.)?([^/]+)").replace("'\\\\1'", "'\\\\2'")
        return translated

    def get_query(self, query_id: Union[int, str], *, params: Optional[dict[str, Any]] = None) -> str:

        if params is not None:
            raise ValueError("ClickBench queries are static and don't accept parameters")
        return self.query_manager.get_query(str(query_id))

    def get_all_queries(self) -> dict[str, str]:

        return self.query_manager.get_all_queries()

    def execute_query(
        self,
        query_id: Union[int, str],
        connection: Any,
        params: Optional[dict[str, Any]] = None,
    ) -> Any:

        sql = self.get_query(query_id, params=params)

        if hasattr(connection, "execute"):
            cursor = connection.execute(sql)
            return cursor.fetchall()
        elif hasattr(connection, "cursor"):
            cursor = connection.cursor()
            cursor.execute(sql)
            return cursor.fetchall()
        else:
            raise ValueError("Unsupported connection type")

    def get_schema(self, dialect: str = "standard") -> dict[str, dict]:

        return TABLES

    def get_create_tables_sql(
        self,
        dialect: str = "standard",
        tuning_config: Optional["UnifiedTuningConfiguration"] = None,
    ) -> str:

        enable_primary_keys, enable_foreign_keys = extract_constraint_flags(tuning_config)
        return get_create_table_sql(dialect, enable_primary_keys, enable_foreign_keys)

    def get_query_categories(self) -> dict[str, list[str]]:

        return self.query_manager.get_query_categories()

    @property
    def csv_delimiter(self) -> str:

        return "|"

    @property
    def csv_null_marker(self) -> str:

        return "__NULL__"

    def get_csv_loading_config(self, table_name: str) -> list[str]:

        return [
            "delim='|'",
            "header=false",
            "nullstr='__NULL__'",
            "ignore_errors=true",
            "auto_detect=true",
        ]
