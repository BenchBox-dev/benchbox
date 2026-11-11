from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.base.data_loading import DataSource
from benchbox.platforms.base.spark_execution_mixin import SparkDataLoadMixin

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class _FakeDataFrame:
    columns: list[str] = []

    def __init__(self) -> None:
        self.write = Mock()
        self.write.mode.return_value = self.write
        self.write.insertInto.return_value = None

    def cache(self):
        return self

    def count(self) -> int:
        return 1

    def unpersist(self) -> None:
        return None


class _FakeReader:
    def __init__(self) -> None:
        self.options: list[tuple[str, str]] = []

    def option(self, key: str, value: str):
        self.options.append((key, value))
        return self

    def csv(self, _path: str) -> _FakeDataFrame:
        return _FakeDataFrame()


class _FakeSpark:
    def __init__(self) -> None:
        self.read = _FakeReader()


class _Adapter(SparkDataLoadMixin):
    platform_name = "spark"
    table_mode = "native"
    platform_config = None
    requested_table_format = None
    _df_caching_supported = True
    _csv_extension_required = False
    _csv_compression_codecs = frozenset()

    def __init__(self) -> None:
        self.logger = Mock()

    def log_verbose(self, _message: str) -> None:
        return None

    def log_operation_start(self, _operation: str, _details: str) -> None:
        return None

    def log_operation_complete(self, _operation: str, _duration: float, _details: str) -> None:
        return None

    def _get_table_schema(self, _spark, _table_name: str):
        return None

    def _detect_spark_table_format(self, _file_path: Path):
        return None

    def _safe_row_count(self, _spark, _table_name: str) -> int:
        return 0

    def _normalize_and_validate_file_paths(self, file_paths):
        return [Path(path) for path in file_paths]


def _run_load_with_metadata(tmp_path: Path, metadata: dict[str, object]) -> list[tuple[str, str]]:
    data_file = tmp_path / "lineitem.csv"
    data_file.write_text("1|ok|\n")
    source = DataSource(
        source_type="manifest_v2",
        tables={"lineitem": [data_file]},
        table_metadata={"lineitem": metadata},
    )
    benchmark = SimpleNamespace()
    spark = _FakeSpark()

    with (
        patch("benchbox.platforms.base.data_loading.DataSourceResolver") as resolver_cls,
        patch("benchbox.platforms.base.utils.detect_file_format", return_value=SimpleNamespace(format_type="csv")),
    ):
        resolver_cls.return_value.resolve.return_value = source
        _Adapter()._load_data_spark(benchmark, tmp_path, spark)

    return spark.read.options


def test_spark_csv_reader_uses_manifest_delimiter_and_header(tmp_path: Path) -> None:
    options = _run_load_with_metadata(
        tmp_path,
        {"csv_delimiter": "|", "csv_has_header": True, "csv_null_marker": None},
    )

    assert ("delimiter", "|") in options
    assert ("header", "true") in options
    assert not any(key == "nullValue" for key, _value in options)


def test_spark_csv_reader_sets_null_value_only_when_declared(tmp_path: Path) -> None:
    options = _run_load_with_metadata(
        tmp_path,
        {"csv_delimiter": "|", "csv_has_header": False, "csv_null_marker": ""},
    )

    assert ("delimiter", "|") in options
    assert ("header", "false") in options
    assert ("nullValue", "") in options


