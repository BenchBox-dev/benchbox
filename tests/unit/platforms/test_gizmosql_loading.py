from __future__ import annotations

import gzip
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import zstandard

from benchbox.platforms.base.data_loading import CsvDialect
from benchbox.platforms.gizmosql_loading import (
    MAX_INGEST_BATCH_BYTES,
    bounded_batches,
    delimited_batches,
    file_batches,
    parquet_batches,
    stream_reader,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

PIPE_DIALECT = CsvDialect(delimiter="|", has_header=False, null_marker="", normalize_booleans=False, quote=None)
CSV_DIALECT = CsvDialect(delimiter=",", has_header=True, null_marker=None, normalize_booleans=False, quote=None)

TPCH_LIKE = pa.schema(
    [
        ("id", pa.int64()),
        ("amount", pa.decimal128(12, 2)),
        ("day", pa.date32()),
        ("note", pa.string()),
    ]
)


def collect(batches) -> pa.Table:
    return pa.Table.from_batches(list(batches))


class TestDelimitedLoading:
    def test_tbl_with_trailing_delimiter(self, tmp_path: Path):
        path = tmp_path / "t.tbl"
        path.write_text("1|10.50|1998-01-02|hello|\n2|0.25|1998-01-03|world|\n")

        table = collect(delimited_batches(path, TPCH_LIKE, PIPE_DIALECT))

        assert table.num_rows == 2
        assert table.column("amount").to_pylist() == [Decimal("10.50"), Decimal("0.25")]
        assert table.column("note").to_pylist() == ["hello", "world"]

    def test_dat_without_trailing_delimiter_and_final_null_field(self, tmp_path: Path):
        path = tmp_path / "t.dat"
        path.write_text("1|10.50|1998-01-02|\n2|0.25|1998-01-03|x\n3||1998-01-04|\n")

        table = collect(delimited_batches(path, TPCH_LIKE, PIPE_DIALECT))

        assert table.num_rows == 3
        assert table.column("note").to_pylist() == [None, "x", None]
        assert table.column("amount").to_pylist()[2] is None

    def test_tpcds_final_null_field_with_trailing_delimiter(self, tmp_path: Path):
        path = tmp_path / "t.dat"
        path.write_text("1|10.50|1998-01-02|abc|\n2|0.25|1998-01-03||\n")

        table = collect(delimited_batches(path, TPCH_LIKE, PIPE_DIALECT))

        assert table.column("note").to_pylist() == ["abc", None]

    def test_null_marker_is_exact_and_literal_null_text_survives(self, tmp_path: Path):
        path = tmp_path / "t.csv"
        path.write_text("id,amount,day,note\n1,1.00,2020-01-01,NULL\n2,\\N,2020-01-02,keep\n")
        dialect = CsvDialect(delimiter=",", has_header=True, null_marker="\\N", normalize_booleans=False, quote=None)

        table = collect(delimited_batches(path, TPCH_LIKE, dialect))

        assert table.column("note").to_pylist() == ["NULL", "keep"]
        assert table.column("amount").to_pylist() == [Decimal("1.00"), None]

    def test_csv_quotes_and_embedded_delimiters(self, tmp_path: Path):
        path = tmp_path / "t.csv"
        path.write_text('id,amount,day,note\n1,1.00,2020-01-01,"a, b"\n2,2.00,2020-01-02,"say ""hi"""\n')

        table = collect(delimited_batches(path, TPCH_LIKE, CSV_DIALECT))

        assert table.column("note").to_pylist() == ["a, b", 'say "hi"']

    def test_boolean_normalisation(self, tmp_path: Path):
        schema = pa.schema([("id", pa.int64()), ("flag", pa.bool_())])
        path = tmp_path / "t.csv"
        path.write_text("1,True\n2,False\n3,1\n4,0\n")
        dialect = CsvDialect(delimiter=",", has_header=False, null_marker=None, normalize_booleans=True, quote=None)

        table = collect(delimited_batches(path, schema, dialect))

        assert table.column("flag").to_pylist() == [True, False, True, False]

    @pytest.mark.parametrize("suffix", [".gz", ".zst"])
    def test_compressed_shards(self, tmp_path: Path, suffix: str):
        payload = b"1|1.00|2020-01-01|a|\n2|2.00|2020-01-02|b|\n"
        path = tmp_path / f"t.tbl{suffix}"
        if suffix == ".gz":
            path.write_bytes(gzip.compress(payload))
        else:
            path.write_bytes(zstandard.ZstdCompressor().compress(payload))

        table = collect(delimited_batches(path, TPCH_LIKE, PIPE_DIALECT))

        assert table.column("id").to_pylist() == [1, 2]

    def test_malformed_row_raises_instead_of_dropping(self, tmp_path: Path):
        path = tmp_path / "t.tbl"
        path.write_text("1|1.00|2020-01-01|a|\nnot-a-number|2.00|2020-01-02|b|\n")

        with pytest.raises(pa.ArrowInvalid):
            collect(delimited_batches(path, TPCH_LIKE, PIPE_DIALECT))

    def test_wide_rows_stay_below_the_flight_message_limit(self, tmp_path: Path):
        schema = pa.schema([("id", pa.int64()), ("payload", pa.string())])
        path = tmp_path / "wide.tbl"
        payload = "x" * 200_000
        with path.open("w") as handle:
            for index in range(200):
                handle.write(f"{index}|{payload}|\n")
        limit = 4 * 1024 * 1024

        batches = list(delimited_batches(path, schema, PIPE_DIALECT, max_batch_bytes=limit))

        assert sum(batch.num_rows for batch in batches) == 200
        assert len(batches) > 1
        assert max(batch.nbytes for batch in batches) <= limit
        assert limit < MAX_INGEST_BATCH_BYTES < 16 * 1024 * 1024

    def test_dispatch_selects_parser_by_extension(self, tmp_path: Path):
        path = tmp_path / "t.tbl"
        path.write_text("1|1.00|2020-01-01|a|\n")

        assert collect(file_batches(path, TPCH_LIKE, PIPE_DIALECT)).num_rows == 1
        with pytest.raises(ValueError, match="No CSV dialect"):
            list(file_batches(path, TPCH_LIKE, None))


class TestBoundedBatches:
    def test_slices_to_the_byte_budget_without_losing_rows(self):
        batch = pa.RecordBatch.from_pydict({"s": ["y" * 1000] * 500})

        slices = list(bounded_batches([batch], 64 * 1024))

        assert sum(piece.num_rows for piece in slices) == 500
        assert all(piece.nbytes <= 64 * 1024 for piece in slices)
        assert pa.Table.from_batches(slices).equals(pa.Table.from_batches([batch]))

    def test_single_oversized_row_is_still_emitted(self):
        batch = pa.RecordBatch.from_pydict({"s": ["z" * 100_000]})

        assert [piece.num_rows for piece in bounded_batches([batch], 1024)] == [1]

    def test_empty_batches_are_dropped(self):
        assert list(bounded_batches([pa.RecordBatch.from_pydict({"s": []})], 1024)) == []


class TestParquetLoading:
    def test_streams_and_casts_to_the_table_schema(self, tmp_path: Path):
        path = tmp_path / "t.parquet"
        source = pa.table(
            {
                "ID": pa.array([1, 2, 3], pa.int32()),
                "amount": pa.array([Decimal("1.10"), None, Decimal("3.30")], pa.decimal128(12, 2)),
                "day": pa.array([18000, 18001, 18002], pa.date32()),
                "note": ["a", None, "c"],
            }
        )
        pq.write_table(source, path)

        table = collect(parquet_batches(path, TPCH_LIKE))

        assert table.schema.names == ["id", "amount", "day", "note"]
        assert table.column("id").type == pa.int64()
        assert table.column("note").to_pylist() == ["a", None, "c"]

    def test_missing_columns_are_reported(self, tmp_path: Path):
        path = tmp_path / "t.parquet"
        pq.write_table(pa.table({"id": [1]}), path)

        with pytest.raises(ValueError, match="missing columns: amount, day, note"):
            list(parquet_batches(path, TPCH_LIKE))

    def test_dispatch_selects_parquet(self, tmp_path: Path):
        path = tmp_path / "t.parquet"
        source = pa.table(
            {
                "id": [1],
                "amount": [Decimal("1.00")],
                "day": pa.array([18000], pa.date32()),
                "note": ["a"],
            }
        )
        pq.write_table(source, path)

        assert collect(file_batches(path, TPCH_LIKE, None)).num_rows == 1


def test_stream_reader_yields_plain_schema():
    schema = pa.schema([pa.field("a", pa.int32(), metadata={"k": "v"})])
    batch = pa.RecordBatch.from_pydict({"a": pa.array([1], pa.int32())})

    reader = stream_reader(schema, [batch])

    assert reader.schema.field("a").metadata is None
    assert reader.read_all().num_rows == 1
