from __future__ import annotations

import csv
import io
import random
import time
from datetime import date, datetime
from decimal import Decimal

import pyarrow.csv as pacsv
import pytest

from benchbox.utils.arrow_rows import ArrowRowWriter, open_csv_over_stream
from tests.unit.endless_csv_stream import EndlessStream

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _expected(rows, delimiter):
    buffer = io.StringIO(newline="")
    csv.writer(buffer, delimiter=delimiter).writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _actual(rows, delimiter, batch_rows=65536):
    sink = io.BytesIO()
    with ArrowRowWriter(sink, delimiter, batch_rows=batch_rows) as writer:
        writer.writerows(rows)
    return sink.getvalue()


@pytest.mark.parametrize("delimiter", ["|", ","])
def test_matches_csv_writer_for_plain_rows(delimiter):
    rows = [[1, "a", "", None, 2.5, True, date(2020, 1, 2)], [2, "b c", "x", "y", 100.0, False, None]]
    assert _actual(rows, delimiter) == _expected(rows, delimiter)


@pytest.mark.parametrize("delimiter", ["|", ","])
def test_matches_csv_writer_for_special_characters(delimiter):
    rows = [
        [1, 'say "hi"', "a|b", "c,d"],
        [2, "line\nbreak", "cr\rhere", " lead"],
        [3, "", "", ""],
    ]
    assert _actual(rows, delimiter) == _expected(rows, delimiter)


def test_matches_csv_writer_for_single_column_empty_string():
    rows = [[""], ["x"]]
    assert _actual(rows, "|") == _expected(rows, "|")


def test_matches_csv_writer_for_out_of_range_integers_and_mixed_types():
    rows = [[2**70, "a", Decimal("1.50")], [1, 7, datetime(2020, 1, 2, 3, 4, 5, 6)]]
    assert _actual(rows, "|") == _expected(rows, "|")


def test_matches_csv_writer_across_batches():
    rows = [[i, f"name {i}", "" if i % 3 else "x"] for i in range(25)]
    assert _actual(rows, "|", batch_rows=4) == _expected(rows, "|")


def test_randomised_rows_match_csv_writer():
    rng = random.Random(7)
    alphabet = list("ab |,\"'\\\t\n\r\u00e9") + [""]
    makers = [
        lambda: rng.randint(-1000, 1000),
        lambda: rng.random() * 10 ** rng.randint(-8, 20),
        lambda: None,
        lambda: "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 5))),
        lambda: date(2020, 1, 2),
    ]
    for _ in range(100):
        width = rng.randint(2, 6)
        columns = [rng.sample(makers, rng.randint(1, 3)) for _ in range(width)]
        rows = [[rng.choice(column)() for column in columns] for _ in range(rng.randint(1, 30))]
        assert _actual(rows, "|", batch_rows=rng.choice([3, 1000])) == _expected(rows, "|")


def test_exception_in_block_does_not_flush_partial_batch():
    sink = io.BytesIO()
    with pytest.raises(RuntimeError):
        with ArrowRowWriter(sink, "|") as writer:
            writer.writerow([1, "a"])
            raise RuntimeError("boom")
    assert sink.getvalue() == b""


def test_abandoned_reader_never_reads_a_closed_stream():
    late = 0
    for _ in range(10):
        stream = EndlessStream(b"", b"1,2\n")
        with open_csv_over_stream(
            stream, read_options=pacsv.ReadOptions(block_size=1 << 12, column_names=["a", "b"])
        ) as reader:
            assert next(iter(reader)).num_rows > 0
        stream.close()
        time.sleep(0.05)
        late += stream.reads_after_close
    assert late == 0


def test_reader_exception_propagates_and_stream_is_released():
    stream = EndlessStream(b"", b"1,2\n")
    with pytest.raises(RuntimeError, match="boom"):
        with open_csv_over_stream(
            stream, read_options=pacsv.ReadOptions(block_size=1 << 12, column_names=["a", "b"])
        ) as reader:
            next(iter(reader))
            raise RuntimeError("boom")
    stream.close()
    time.sleep(0.05)
    assert stream.reads_after_close == 0
