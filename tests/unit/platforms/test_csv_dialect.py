from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from benchbox.core.flightdata.benchmark import FlightDataBenchmark
from benchbox.platforms.base.data_loading import CsvDialect, DataSource, resolve_csv_dialect
from tests.unit.platforms.csv_dialect_test_helpers import (
    benchmark_stub as _benchmark_stub,
    resolver_data_source as _resolver_data_source,
    unsafe_plain_mock_benchmark as _unsafe_plain_mock_benchmark,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _make_data_source(table_metadata: dict | None = None) -> DataSource:
    return DataSource(
        source_type="manifest_v2",
        tables={},
        table_metadata=table_metadata or {},
    )


@dataclass
class _Benchmark:
    csv_delimiter: str | None = None
    csv_has_header: bool | None = None
    csv_normalize_booleans: bool | None = None
    csv_null_marker: str | None = None


class _EmptyBenchmark:
    pass


def test_manifest_metadata_wins_over_benchmark_attribute(caplog: pytest.LogCaptureFixture) -> None:

    ds = _make_data_source({"customer": {"csv_has_header": True, "csv_delimiter": "\t"}})
    benchmark = _Benchmark(
        csv_delimiter=",",
        csv_has_header=False,
        csv_normalize_booleans=True,
        csv_null_marker="",
    )
    file_path = Path("customer.csv")

    with caplog.at_level(logging.WARNING):
        dialect = resolve_csv_dialect(ds, "customer", file_path, benchmark)

    assert dialect.delimiter == "\t"
    assert dialect.has_header is True
    assert dialect.normalize_booleans is True
    assert dialect.null_marker == ""

    assert not caplog.records


def test_manifest_metadata_wins_over_format_default(caplog: pytest.LogCaptureFixture) -> None:

    ds = _make_data_source({"lineitem": {"csv_has_header": True, "csv_null_marker": None}})
    file_path = Path("lineitem.tbl")

    with caplog.at_level(logging.WARNING):
        dialect = resolve_csv_dialect(ds, "lineitem", file_path, _EmptyBenchmark())

    assert dialect.has_header is True
    assert dialect.null_marker is None
    assert not caplog.records


def test_manifest_metadata_normalize_booleans(caplog: pytest.LogCaptureFixture) -> None:

    ds = _make_data_source({"dbo_dimaccount": {"csv_normalize_booleans": True, "csv_delimiter": ","}})
    file_path = Path("dbo_dimaccount.csv")

    with caplog.at_level(logging.WARNING):
        dialect = resolve_csv_dialect(ds, "dbo_dimaccount", file_path, _EmptyBenchmark())

    assert dialect.normalize_booleans is True
    assert not caplog.records


def test_benchmark_attribute_wins_over_format_default(caplog: pytest.LogCaptureFixture) -> None:

    ds = _make_data_source()
    benchmark = _Benchmark(csv_delimiter="|", csv_has_header=True, csv_normalize_booleans=False)
    file_path = Path("hits.csv")

    with caplog.at_level(logging.WARNING):
        dialect = resolve_csv_dialect(ds, "hits", file_path, benchmark)

    assert dialect.delimiter == "|"
    assert dialect.has_header is True
    assert dialect.normalize_booleans is False

    assert any("hits" in r.message for r in caplog.records)


def test_benchmark_attribute_partial_override(caplog: pytest.LogCaptureFixture) -> None:

    ds = _make_data_source()
    benchmark = _Benchmark(csv_has_header=True)
    file_path = Path("rides.csv")

    with caplog.at_level(logging.WARNING):
        dialect = resolve_csv_dialect(ds, "rides", file_path, benchmark)

    assert dialect.has_header is True
    assert dialect.delimiter == ","
    assert caplog.records


def test_benchmark_attribute_csv_null_marker_empty_string(caplog: pytest.LogCaptureFixture) -> None:

    ds = _make_data_source()
    benchmark = _Benchmark(csv_delimiter=",", csv_null_marker="")
    file_path = Path("title.csv")

    with caplog.at_level(logging.WARNING):
        dialect = resolve_csv_dialect(ds, "title", file_path, benchmark)

    assert dialect.null_marker == ""
    assert dialect.delimiter == ","
    assert caplog.records


def test_benchmark_attribute_csv_null_marker_sentinel_preserved(caplog: pytest.LogCaptureFixture) -> None:

    ds = _make_data_source()
    benchmark = _Benchmark(csv_delimiter="|", csv_null_marker="__NULL__")
    file_path = Path("hits.csv.gz")

    with caplog.at_level(logging.WARNING):
        dialect = resolve_csv_dialect(ds, "hits", file_path, benchmark)

    assert dialect.delimiter == "|"
    assert dialect.null_marker == "__NULL__"
    assert caplog.records


def test_flightdata_declares_empty_csv_fields_as_null(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:

    ds = _make_data_source()
    benchmark = FlightDataBenchmark(scale_factor=0.01, output_dir=tmp_path)

    with caplog.at_level(logging.WARNING):
        dialect = resolve_csv_dialect(ds, "flights", Path("flights.csv"), benchmark)

    assert dialect.delimiter == ","
    assert dialect.has_header is True
    assert dialect.null_marker == ""
    assert caplog.records


def test_format_default_tbl(caplog: pytest.LogCaptureFixture) -> None:

    ds = _make_data_source()
    file_path = Path("lineitem.tbl")

    with caplog.at_level(logging.WARNING):
        dialect = resolve_csv_dialect(ds, "lineitem", file_path, _EmptyBenchmark())

    assert dialect.delimiter == "|"
    assert dialect.has_header is False
    assert dialect.null_marker == ""
    assert dialect.normalize_booleans is False
    assert caplog.records


def test_format_default_dat(caplog: pytest.LogCaptureFixture) -> None:

    ds = _make_data_source()
    file_path = Path("store_sales.dat")

    with caplog.at_level(logging.WARNING):
        dialect = resolve_csv_dialect(ds, "store_sales", file_path, _EmptyBenchmark())

    assert dialect.delimiter == "|"
    assert dialect.null_marker == ""
    assert caplog.records


def test_format_default_csv(caplog: pytest.LogCaptureFixture) -> None:

    ds = _make_data_source()
    file_path = Path("hits.csv")

    with caplog.at_level(logging.WARNING):
        dialect = resolve_csv_dialect(ds, "hits", file_path, _EmptyBenchmark())

    assert dialect.delimiter == ","
    assert dialect.has_header is False
    assert dialect.null_marker is None
    assert dialect.normalize_booleans is False
    assert caplog.records


def test_format_default_compressed_tbl(caplog: pytest.LogCaptureFixture) -> None:

    ds = _make_data_source()
    file_path = Path("lineitem.tbl.zst")

    with caplog.at_level(logging.WARNING):
        dialect = resolve_csv_dialect(ds, "lineitem", file_path, _EmptyBenchmark())

    assert dialect.delimiter == "|"
    assert dialect.null_marker == ""
    assert caplog.records


def test_no_warning_when_manifest_metadata_present(caplog: pytest.LogCaptureFixture) -> None:

    ds = _make_data_source({"customer": {"csv_has_header": True}})
    file_path = Path("customer.csv")

    with caplog.at_level(logging.WARNING):
        resolve_csv_dialect(ds, "customer", file_path, _EmptyBenchmark())

    assert not caplog.records


def test_warning_emitted_on_benchmark_fallback(caplog: pytest.LogCaptureFixture) -> None:

    ds = _make_data_source()
    benchmark = _Benchmark(csv_has_header=True)
    file_path = Path("hits.csv")

    with caplog.at_level(logging.WARNING):
        resolve_csv_dialect(ds, "hits", file_path, benchmark)

    assert any("hits" in r.message for r in caplog.records)


def test_warning_emitted_on_format_fallback(caplog: pytest.LogCaptureFixture) -> None:

    ds = _make_data_source()
    file_path = Path("lineitem.tbl")

    with caplog.at_level(logging.WARNING):
        resolve_csv_dialect(ds, "lineitem", file_path, _EmptyBenchmark())

    assert any("lineitem" in r.message for r in caplog.records)


def test_metadata_lookup_is_case_insensitive(caplog: pytest.LogCaptureFixture) -> None:

    ds = _make_data_source({"customer": {"csv_has_header": True, "csv_delimiter": ","}})
    file_path = Path("Customer.csv")

    with caplog.at_level(logging.WARNING):
        dialect = resolve_csv_dialect(ds, "Customer", file_path, _EmptyBenchmark())

    assert dialect.has_header is True
    assert dialect.delimiter == ","
    assert not caplog.records


def test_shared_helper_manifest_metadata_beats_suffix(caplog: pytest.LogCaptureFixture) -> None:

    file_path = Path("lineitem.csv")
    ds = _resolver_data_source("lineitem", file_path, {"csv_delimiter": "|", "csv_null_marker": ""})

    with caplog.at_level(logging.WARNING):
        dialect = resolve_csv_dialect(ds, "lineitem", file_path, _benchmark_stub({"lineitem": file_path}))

    assert dialect.delimiter == "|"
    assert dialect.null_marker == ""
    assert not caplog.records


def test_shared_helper_preserves_explicit_none_null_marker(caplog: pytest.LogCaptureFixture) -> None:

    file_path = Path("hits.tbl")
    ds = _resolver_data_source("hits", file_path, {"csv_delimiter": "|", "csv_null_marker": None})

    with caplog.at_level(logging.WARNING):
        dialect = resolve_csv_dialect(ds, "hits", file_path, _benchmark_stub({"hits": file_path}))

    assert dialect.delimiter == "|"
    assert dialect.null_marker is None
    assert not caplog.records


def test_plain_mock_benchmark_does_not_pollute_dialect(caplog: pytest.LogCaptureFixture) -> None:

    file_path = Path("lineitem.tbl")
    ds = _make_data_source()

    with caplog.at_level(logging.WARNING):
        dialect = resolve_csv_dialect(ds, "lineitem", file_path, _unsafe_plain_mock_benchmark({"lineitem": file_path}))

    assert dialect.delimiter == "|"
    assert dialect.has_header is False
    assert dialect.null_marker == ""
    assert any("file extension heuristic" in r.message for r in caplog.records)


_MIGRATED_ADAPTER_PATHS = (
    "benchbox/platforms/duckdb.py",
    "benchbox/platforms/clickhouse/workload.py",
    "benchbox/platforms/starrocks/workload.py",
    "benchbox/platforms/databend/adapter.py",
    "benchbox/platforms/firebolt.py",
    "benchbox/platforms/redshift.py",
    "benchbox/platforms/bigquery.py",
    "benchbox/platforms/databricks/adapter.py",
    "benchbox/platforms/base/spark_execution_mixin.py",
    "benchbox/platforms/dataframe/pandas_df.py",
    "benchbox/platforms/dataframe/cudf_df.py",
    "benchbox/platforms/dataframe/dask_df.py",
    "benchbox/platforms/dataframe/pyspark_df.py",
    "benchbox/platforms/dataframe/lakesail_df.py",
)

_LEGACY_CSV_HEURISTIC_RE = re.compile(
    r"is_tpc_format"
    r"|get_delimiter_for_file"
    r"|getattr\([^)\n]*csv_(?:delimiter|null_marker|has_header|normalize_booleans|quote)"
)


def test_migrated_adapter_load_paths_do_not_use_legacy_csv_heuristics() -> None:

    repo_root = Path(__file__).resolve().parents[3]
    offenders: list[str] = []
    for rel_path in _MIGRATED_ADAPTER_PATHS:
        target = repo_root / rel_path
        for lineno, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
            if _LEGACY_CSV_HEURISTIC_RE.search(line):
                offenders.append(f"{rel_path}:{lineno}: {line.strip()}")

    assert not offenders, "Migrated adapters reintroduced legacy CSV heuristics:\n" + "\n".join(offenders)


