import io
from unittest.mock import Mock, patch

import pytest

import benchbox.platforms.questdb as questdb_module
from benchbox.platforms.base.data_loading import CsvDialect
from benchbox.platforms.questdb import (
    QuestDBAdapter,
    _check_import_value_errors,
    _locate_invalid_value,
    _value_parses,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _response(errors: dict[str, int], rows: int = 3, types: dict[str, str] | None = None) -> str:
    types = types or {}
    lines = [f"|   Rows handled  |  {rows}  |", f"|  Rows imported  |  {rows}  |"]
    for index, (name, count) in enumerate(errors.items()):
        lines.append(f"|  {index}  |  {name}  |  {types.get(name, 'INT')}  |  {count}  |")
    return "\n".join(lines)


@pytest.mark.parametrize(
    ("value", "type_name", "expected"),
    [
        ("12", "INT", True),
        ("abc", "INT", False),
        ("99999999999", "INT", False),
        ("99999999999", "LONG", True),
        ("", "INT", True),
        ("1.5", "DOUBLE", True),
        ("abc", "DOUBLE", False),
        ("1999-01-02", "DATE", True),
        ("notadate", "DATE", False),
        ("1999-13-45", "TIMESTAMP", False),
        ("anything", "SYMBOL", True),
    ],
)
def test_value_parses(value, type_name, expected):
    assert _value_parses(value, type_name) is expected


def test_locate_invalid_value_skips_header_and_reports_row_and_line():
    stream = io.BytesIO(b"a,b\n1,x\nabc,y\n3,z\n")

    found = _locate_invalid_value(stream, delimiter=",", has_header=True, column_index=0, type_name="INT")

    assert found == (2, 3, "abc")


def test_locate_invalid_value_headerless_pipe():
    stream = io.BytesIO(b"1|2\n3|oops\n")

    found = _locate_invalid_value(stream, delimiter="|", has_header=False, column_index=1, type_name="LONG")

    assert found == (2, 2, "oops")


def test_clean_response_passes():
    _check_import_value_errors(
        _response({"a": 0, "b": 0}), "t", open_source=lambda: io.BytesIO(b""), delimiter=",", has_header=True
    )


def test_error_names_table_column_row_and_value():
    with pytest.raises(RuntimeError) as excinfo:
        _check_import_value_errors(
            _response({"l_orderkey": 1, "l_qty": 0}),
            "lineitem",
            open_source=lambda: io.BytesIO(b"l_orderkey,l_qty\n1,2\nabc,3\n"),
            delimiter=",",
            has_header=True,
        )

    message = str(excinfo.value)
    assert "'lineitem'" in message
    assert "column 'l_orderkey' (INT)" in message
    assert "row 2" in message
    assert "'abc'" in message
    assert "l_qty" not in message.split("column 'l_orderkey'")[1]


def test_row_offset_is_added_for_chunked_parquet_batches():
    with pytest.raises(RuntimeError, match="row 1,002"):
        _check_import_value_errors(
            _response({"a": 1}),
            "t",
            open_source=lambda: io.BytesIO(b"a\n1\nbad\n"),
            delimiter=",",
            has_header=True,
            row_offset=1000,
        )


def test_unlocatable_row_still_fails_with_column():
    with pytest.raises(RuntimeError, match=r"column 'a' \(DOUBLE\).*row not located"):
        _check_import_value_errors(
            _response({"a": 2}, types={"a": "DOUBLE"}),
            "t",
            open_source=lambda: io.BytesIO(b"a\n1.5\n"),
            delimiter=",",
            has_header=True,
        )


def test_atomicity_abort_style_syntax_error_fails():
    with pytest.raises(RuntimeError, match="bad syntax"):
        _check_import_value_errors(
            "bad syntax [line=1, col=0]", "t", open_source=lambda: io.BytesIO(b""), delimiter=",", has_header=True
        )


def test_rest_load_rejects_unparseable_int(tmp_path):
    adapter = QuestDBAdapter(host="localhost", http_port=9000)
    adapter._import_schema = lambda *args, **kwargs: None
    adapter._unpatterned_timestamp_columns = lambda *args, **kwargs: []
    data_file = tmp_path / "t.csv"
    data_file.write_text("a,b\n1,1\nabc,2\n3,3\n", encoding="utf-8")
    response = Mock(status_code=200, text=_response({"a": 1, "b": 0}))

    with patch.object(questdb_module, "_http_post", return_value=response):
        with pytest.raises(RuntimeError, match=r"column 'a' \(INT\).*row 2.*'abc'"):
            adapter._load_table_via_rest_api("t", data_file, CsvDialect(",", True, None, False, None))


def test_rest_load_accepts_clean_response(tmp_path):
    adapter = QuestDBAdapter(host="localhost", http_port=9000)
    adapter._import_schema = lambda *args, **kwargs: None
    adapter._unpatterned_timestamp_columns = lambda *args, **kwargs: []
    data_file = tmp_path / "t.csv"
    data_file.write_text("a,b\n1,1\n2,2\n3,3\n", encoding="utf-8")
    response = Mock(status_code=200, text=_response({"a": 0, "b": 0}))

    with patch.object(questdb_module, "_http_post", return_value=response):
        rows = adapter._load_table_via_rest_api("t", data_file, CsvDialect(",", True, None, False, None))

    assert rows == 3


def test_timestamp_column_nulled_without_pattern_fails_with_row_and_column():
    from benchbox.platforms.questdb import _check_timestamps_not_nulled

    with pytest.raises(RuntimeError, match=r"column 'ts' \(TIMESTAMP\): 1 of 2 .*row 2.*'garbage'"):
        _check_timestamps_not_nulled(
            "t",
            ["ts"],
            [5],
            [6],
            open_source=lambda: io.BytesIO(b"a,ts\n1,2020-01-01\n2,garbage\n"),
            delimiter=",",
        )


def test_timestamp_column_fully_stored_passes():
    from benchbox.platforms.questdb import _check_timestamps_not_nulled

    _check_timestamps_not_nulled(
        "t",
        ["ts"],
        [5],
        [7],
        open_source=lambda: io.BytesIO(b"a,ts\n1,2020-01-01\n2,\n3,2020-01-03\n"),
        delimiter=",",
    )


def test_headered_rest_load_forces_header_detection(tmp_path):
    adapter = QuestDBAdapter(host="localhost", http_port=9000)
    adapter._import_schema = lambda *args, **kwargs: None
    adapter._unpatterned_timestamp_columns = lambda *args, **kwargs: []
    data_file = tmp_path / "t.csv"
    data_file.write_text("a,b\n1,1\n", encoding="utf-8")
    response = Mock(status_code=200, text=_response({"a": 0, "b": 0}, rows=1))

    with patch.object(questdb_module, "_http_post", return_value=response) as post:
        adapter._load_table_via_rest_api("t", data_file, CsvDialect(",", True, None, False, None))

    assert post.call_args.kwargs["params"]["forceHeader"] == "true"


