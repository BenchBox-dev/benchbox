import gzip
import re
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark
from benchbox.platforms.base.mysql_wire import MySqlWireConnectionWrapper
from benchbox.sql_compat.rules.execution_filter.singlestore_write_primitives import (
    SINGLESTORE_WRITE_PRIMITIVES_OPERATION_SKIPS,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

CSV_BYTES = b"a,b\r\n1,x\r\n2,y\r\n"


@pytest.fixture()
def fast_bench(tmp_path):
    with patch("benchbox.core.write_primitives.benchmark.WritePrimitivesDataGenerator"):
        bench = WritePrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
    return bench


@pytest.fixture()
def bulk_load_ids(fast_bench):
    return sorted(op_id for op_id in fast_bench.get_all_operations() if op_id.startswith("bulk_load_"))


def test_every_bulk_load_operation_has_a_native_singlestore_form_or_a_documented_skip(fast_bench, bulk_load_ids):
    assert len(bulk_load_ids) == 36
    for operation_id in bulk_load_ids:
        operation = fast_bench.get_operation(operation_id)
        sql, skip_reason = fast_bench._get_effective_write_sql(operation, "singlestore")
        if operation_id in SINGLESTORE_WRITE_PRIMITIVES_OPERATION_SKIPS:
            assert sql is None, operation_id
            assert skip_reason.startswith(f"Operation '{operation_id}' is skipped on SingleStore: SingleStore 10.8.1")
            continue
        assert skip_reason is None, operation_id
        override = operation.platform_overrides["singlestore"]
        assert "LOAD DATA LOCAL INFILE" in override, operation_id
        assert not re.search(r"\bCOPY\b|read_csv_auto|MERGE INTO|IGNORE 1 ROWS", override, re.IGNORECASE), operation_id


@pytest.mark.parametrize(
    "operation_id,expected_suffix",
    [
        ("bulk_load_csv_small_uncompressed", "csv_small_1k.csv"),
        ("bulk_load_csv_medium_gzip", "csv_medium_100k.csv.gz"),
        ("bulk_load_parquet_medium_snappy", "parquet_medium_100k_snappy.parquet"),
    ],
)
def test_compression_variants_load_their_own_file(fast_bench, operation_id, expected_suffix):
    operation = fast_bench.get_operation(operation_id)
    assert f"INFILE '{{file_path}}/{expected_suffix}'" in operation.platform_overrides["singlestore"]


def test_csv_loads_use_crlf_terminator_and_header_skip(fast_bench):
    sql = fast_bench.get_operation("bulk_load_csv_small_uncompressed").platform_overrides["singlestore"]

    assert "LINES TERMINATED BY '\\r\\n'" in sql
    assert "IGNORE 1 LINES" in sql


def test_parquet_loads_use_native_parquet_format(fast_bench):
    sql = fast_bench.get_operation("bulk_load_parquet_small_zstd").platform_overrides["singlestore"]

    assert sql.rstrip().endswith("FORMAT PARQUET")
    assert "o_orderkey <- o_orderkey" in sql
    assert "FIELDS TERMINATED BY" not in sql


def test_transformation_and_subset_use_load_data_column_mapping(fast_bench):
    transformation = fast_bench.get_operation("bulk_load_with_transformation").platform_overrides["singlestore"]
    subset = fast_bench.get_operation("bulk_load_column_subset").platform_overrides["singlestore"]
    dates = fast_bench.get_operation("bulk_load_date_format_custom").platform_overrides["singlestore"]

    assert "SET o_orderstatus = UPPER(@order_status)" in transformation
    assert "LEFT(CONCAT('TRANSFORMED: ', @comment), 79)" in transformation
    assert "@skip_comment" in subset
    assert "STR_TO_DATE(@order_date, '%Y/%m/%d')" in dates


def test_null_and_error_handling_use_native_clauses(fast_bench):
    nulls = fast_bench.get_operation("bulk_load_null_handling").platform_overrides["singlestore"]
    errors = fast_bench.get_operation("bulk_load_error_handling_skip_bad_rows").platform_overrides["singlestore"]

    assert "NULL DEFINED BY ''" in nulls
    assert "SKIP ALL ERRORS" in errors


def test_multi_file_load_issues_one_load_per_part(fast_bench):
    sql = fast_bench.get_operation("bulk_load_parallel_multi_file").platform_overrides["singlestore"]

    assert sql.count("LOAD DATA LOCAL INFILE") == 4
    for part in range(1, 5):
        assert f"csv_parallel_part{part}.csv" in sql
    assert "*" not in sql


def test_upsert_loads_stage_then_updates_and_inserts(fast_bench):
    sql = fast_bench.get_operation("bulk_load_upsert_mode").platform_overrides["singlestore"]

    statements = [part.strip() for part in sql.split(";\n") if part.strip()]
    assert [s.split()[0] for s in statements] == ["DROP", "CREATE", "LOAD", "UPDATE", "INSERT", "DROP"]
    assert "INTO TABLE bulk_load_upsert_stage" in statements[2]
    assert "WHERE target.o_orderkey IS NULL" in statements[4]


@pytest.fixture()
def ready_bench(fast_bench, tmp_path, monkeypatch):
    files_dir = tmp_path / "write_primitives_auxiliary"
    files_dir.mkdir()
    fast_bench.data_generator.files_dir = files_dir
    monkeypatch.setattr(fast_bench, "is_setup", lambda conn: True)
    return fast_bench


def _executed_statements(raw):
    return [call.args[0] for call in raw.cursor.return_value.execute.call_args_list]


@pytest.mark.parametrize(
    "operation_id,filename",
    [
        ("bulk_load_csv_small_gzip", "csv_small_1k.csv.gz"),
        ("bulk_load_parquet_small_zstd", "parquet_small_1k_zstd.parquet"),
        ("bulk_load_parquet_small_snappy", "parquet_small_1k_snappy.parquet"),
    ],
)
def test_native_bulk_load_sends_the_original_file_to_the_engine(ready_bench, tmp_path, operation_id, filename):
    files_dir = tmp_path / "write_primitives_auxiliary"
    source = files_dir / filename
    source.write_bytes(gzip.compress(b"a\n") if filename.endswith(".gz") else b"PAR1")
    before = sorted(path.name for path in files_dir.iterdir())
    raw = MagicMock()
    connection = MySqlWireConnectionWrapper(raw)

    ready_bench.execute_operation(operation_id, connection, platform_key="singlestore")

    loads = [sql for sql in _executed_statements(raw) if sql.startswith("LOAD DATA")]
    assert len(loads) == 1
    assert f"INFILE '{source}'" in loads[0]
    assert sorted(path.name for path in files_dir.iterdir()) == before


@pytest.mark.parametrize("operation_id", sorted(SINGLESTORE_WRITE_PRIMITIVES_OPERATION_SKIPS))
def test_codecs_the_engine_cannot_ingest_are_skipped_without_a_timing(ready_bench, tmp_path, operation_id):
    raw = MagicMock()
    connection = MySqlWireConnectionWrapper(raw)

    result = ready_bench.execute_operation(operation_id, connection, platform_key="singlestore")

    assert result.status == "SKIPPED"
    assert result.write_duration_ms == 0.0
    assert "LOAD DATA" not in " ".join(_executed_statements(raw))
    assert "SingleStore 10.8.1" in result.skip_reason
