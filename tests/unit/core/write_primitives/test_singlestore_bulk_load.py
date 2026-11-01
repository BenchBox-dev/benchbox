import bz2
import gzip
import re
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark
from benchbox.platforms.singlestore import SingleStoreConnectionWrapper, _stage_local_infiles

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


def test_every_bulk_load_operation_has_a_native_singlestore_form(fast_bench, bulk_load_ids):
    assert len(bulk_load_ids) == 36
    for operation_id in bulk_load_ids:
        operation = fast_bench.get_operation(operation_id)
        override = operation.platform_overrides["singlestore"]
        assert override is not None, operation_id
        assert "LOAD DATA LOCAL INFILE" in override, operation_id
        assert not re.search(r"\bCOPY\b|read_csv_auto|MERGE INTO|IGNORE 1 ROWS", override, re.IGNORECASE), operation_id


@pytest.mark.parametrize(
    "operation_id,expected_suffix",
    [
        ("bulk_load_csv_small_uncompressed", "csv_small_1k.csv"),
        ("bulk_load_csv_medium_gzip", "csv_medium_100k.csv.gz"),
        ("bulk_load_csv_large_zstd", "csv_large_1m.csv.zst"),
        ("bulk_load_csv_small_bzip2", "csv_small_1k.csv.bz2"),
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


def test_parquet_loads_use_staged_text_format(fast_bench):
    sql = fast_bench.get_operation("bulk_load_parquet_small_zstd").platform_overrides["singlestore"]

    assert "FIELDS TERMINATED BY '\\t' ESCAPED BY '\\\\'" in sql
    assert "IGNORE" not in sql


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


@pytest.mark.parametrize(
    "name,opener",
    [("data.csv.gz", gzip.open), ("data.csv.bz2", bz2.open)],
)
def test_compressed_infile_is_staged_verbatim_and_removed(tmp_path, name, opener):
    source = tmp_path / name
    with opener(source, "wb") as handle:
        handle.write(CSV_BYTES)
    sql = f"LOAD DATA LOCAL INFILE '{source}' INTO TABLE t"

    with ExitStack() as stack:
        staged_sql = _stage_local_infiles(sql, stack)
        staged_path = Path(re.search(r"INFILE '([^']+)'", staged_sql).group(1))

        assert staged_path != source
        assert staged_path.read_bytes() == CSV_BYTES

    assert not staged_path.exists()
    assert source.exists()


def test_plain_infile_is_left_untouched(tmp_path):
    source = tmp_path / "data.csv"
    source.write_bytes(CSV_BYTES)
    sql = f"LOAD DATA LOCAL INFILE '{source}' INTO TABLE t"

    with ExitStack() as stack:
        assert _stage_local_infiles(sql, stack) == sql


def test_parquet_infile_is_converted_to_escaped_text(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    source = tmp_path / "data.parquet"
    pq.write_table(pa.table({"id": [1, None], "name": ["a\tb", "c"]}), source)
    sql = f"LOAD DATA LOCAL INFILE '{source}' INTO TABLE t"

    with ExitStack() as stack:
        staged_sql = _stage_local_infiles(sql, stack)
        staged_path = Path(re.search(r"INFILE '([^']+)'", staged_sql).group(1))

        assert staged_path.read_text(encoding="utf-8") == "1\ta\\tb\n\\N\tc\n"

    assert not staged_path.exists()


def test_singlestore_connection_stages_compressed_file_for_the_load_and_removes_it(tmp_path):
    source = tmp_path / "csv_small_1k.csv.gz"
    with gzip.open(source, "wb") as handle:
        handle.write(CSV_BYTES)
    seen: list[tuple[str, bool, bytes]] = []

    def record(statement, *args, **kwargs):
        match = re.search(r"LOAD DATA LOCAL INFILE '([^']+)'", statement)
        staged = Path(match.group(1))
        seen.append((statement, staged.exists(), staged.read_bytes()))
        return MagicMock()

    raw = MagicMock()
    raw.cursor.return_value.execute.side_effect = record
    wrapper = SingleStoreConnectionWrapper(raw)

    load_sql = f"LOAD DATA LOCAL INFILE '{source}' INTO TABLE t FIELDS TERMINATED BY ','"
    with wrapper.prepare_write(load_sql) as prepared:
        wrapper.execute(prepared)

    ((statement, existed, content),) = seen
    assert existed
    assert content == CSV_BYTES
    assert ".csv.gz" not in statement
    assert not list(tmp_path.glob("tmp*"))


def test_singlestore_connection_leaves_other_statements_untouched():
    raw = MagicMock()
    wrapper = SingleStoreConnectionWrapper(raw)

    wrapper.execute("SELECT 1")

    raw.cursor.return_value.execute.assert_called_once_with("SELECT 1", None)


def test_write_timing_excludes_infile_staging(tmp_path, fast_bench):
    import time

    source = tmp_path / "csv_small_1k.csv.gz"
    with gzip.open(source, "wb") as handle:
        handle.write(CSV_BYTES)
    delay = 0.3
    original = _stage_local_infiles
    timed: dict[str, float] = {}

    def slow_stage(sql, stack):
        time.sleep(delay)
        return original(sql, stack)

    raw = MagicMock()
    wrapper = SingleStoreConnectionWrapper(raw)
    sql = f"LOAD DATA LOCAL INFILE '{source}' INTO TABLE t FIELDS TERMINATED BY ','"
    with patch("benchbox.platforms.singlestore._stage_local_infiles", slow_stage):
        start = time.perf_counter()
        with fast_bench._untimed_write_preparation(wrapper, sql) as prepared:
            prep_elapsed = time.perf_counter() - start
            t0 = time.perf_counter()
            wrapper.execute(prepared)
            timed["write"] = time.perf_counter() - t0

    assert prep_elapsed >= delay
    assert timed["write"] < delay
    assert ".csv.gz" not in raw.cursor.return_value.execute.call_args.args[0]
    assert not list(tmp_path.glob("tmp*"))
