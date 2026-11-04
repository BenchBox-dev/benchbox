import gzip
import hashlib
import re
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.databend.adapter import DatabendAdapter

pytestmark = pytest.mark.fast


@pytest.fixture
def adapter():
    with patch("benchbox.platforms.databend.adapter.DATABEND_AVAILABLE", True):
        yield DatabendAdapter(host="localhost", ssl=False, password="")


class _Stats:
    def __init__(self, write_rows):
        self.write_rows = write_rows


class _NativeConnection:
    def __init__(self):
        self.rows = []
        self.calls = []

    def exec(self, sql):
        if not sql.startswith("USE "):
            raise AssertionError(f"unexpected exec: {sql[:80]}")

    def query_iter(self, sql):
        return iter([(len(self.rows),)])

    def load_file(self, sql, file, format_options, copy_options=None):
        self.calls.append((sql, file, dict(format_options)))
        path = Path(file)
        raw = path.read_bytes()
        if format_options["compression"] == "GZIP":
            raw = gzip.decompress(raw)
        lines = [line for line in raw.decode("utf-8").split("\n") if line]
        delimiter = format_options["field_delimiter"]
        lines = lines[int(format_options["skip_header"]) :]
        null = format_options.get("null_display", "\\N")
        empty_is_null = format_options.get("empty_field_as") == "null"
        loaded = 0
        for line in lines:
            values = line.split(delimiter)
            if format_options.get("error_on_column_count_mismatch") == "false" and values[-1] == "":
                values = values[:-1]
            self.rows.append(tuple(None if v == null or (v == "" and empty_is_null) else v for v in values))
            loaded += 1
        return _Stats(loaded)


class _ValuesConnection:
    def __init__(self):
        self.rows = []

    def exec(self, sql):
        if sql.startswith("USE "):
            return
        body = sql.split(" VALUES ", 1)[1]
        for group in re.findall(r"\(((?:'(?:[^']|'')*'|NULL)(?:, (?:'(?:[^']|'')*'|NULL))*)\)", body):
            fields = re.findall(r"'((?:[^']|'')*)'|(NULL)", group)
            self.rows.append(tuple(None if n else v.replace("''", "'") for v, n in fields))

    def query_iter(self, sql):
        return iter([(len(self.rows),)])


def _benchmark(tmp_path, name, content, suffix, opener=None):
    path = tmp_path / f"{name}{suffix}"
    if opener:
        with opener(path, "wt", encoding="utf-8") as f:
            f.write(content)
    else:
        path.write_text(content, encoding="utf-8")
    benchmark = Mock()
    benchmark.tables = {name: str(path)}
    return benchmark


def _checksums(rows):
    columns = zip(*rows, strict=True) if rows else []
    return [hashlib.sha256("\x1f".join("\\N" if v is None else v for v in col).encode()).hexdigest() for col in columns]


def _run(adapter, connection, benchmark, tmp_path):
    return adapter.load_data(benchmark, connection, tmp_path)


TPCH_TBL = "1|Customer#000000001|IVhzIApeRb ot,c,E|15|25-989-741-2988|711.56|BUILDING|regular deposits\n" * 3 + (
    "2|Customer#000000002|XSTf4,NCwDVaWNe6tEgvwfmRchLXak|13|23-768-687-3665|121.65|AUTOMOBILE|special requests\n"
)


class TestNativeLoadPath:
    def test_tbl_trailing_delimiter_uses_load_file_without_row_handling(self, adapter, tmp_path):
        content = "".join(line.rstrip("\n") + "|\n" for line in TPCH_TBL.splitlines())
        benchmark = _benchmark(tmp_path, "customer", content, ".tbl")
        connection = _NativeConnection()

        stats, _, _ = _run(adapter, connection, benchmark, tmp_path)

        assert stats["customer"] == 4
        sql, file, options = connection.calls[0]
        assert sql == "INSERT INTO `customer` VALUES"
        assert file == str(tmp_path / "customer.tbl")
        assert options["field_delimiter"] == "|"
        assert options["error_on_column_count_mismatch"] == "false"
        assert options["skip_header"] == "0"
        assert options["compression"] == "NONE"
        assert all(len(r) == 8 for r in connection.rows)

    def test_csv_keeps_strict_column_count(self, adapter, tmp_path):
        benchmark = _benchmark(tmp_path, "t", "1,a\n2,b\n", ".csv")
        connection = _NativeConnection()

        stats, _, _ = _run(adapter, connection, benchmark, tmp_path)

        assert stats["t"] == 2
        options = connection.calls[0][2]
        assert options["field_delimiter"] == ","
        assert "error_on_column_count_mismatch" not in options

    def test_gzip_compression_is_passed_to_server(self, adapter, tmp_path):
        benchmark = _benchmark(tmp_path, "t", "1,a\n2,b\n", ".csv.gz", opener=gzip.open)
        connection = _NativeConnection()

        stats, _, _ = _run(adapter, connection, benchmark, tmp_path)

        assert stats["t"] == 2
        assert connection.calls[0][2]["compression"] == "GZIP"

    def test_multiple_files_accumulate(self, adapter, tmp_path):
        one = tmp_path / "a.csv"
        two = tmp_path / "b.csv"
        one.write_text("1,a\n2,b\n", encoding="utf-8")
        two.write_text("3,c\n", encoding="utf-8")
        benchmark = Mock()
        benchmark.tables = {"t": [str(one), str(two)]}
        connection = _NativeConnection()

        stats, _, _ = _run(adapter, connection, benchmark, tmp_path)

        assert stats["t"] == 3
        assert len(connection.calls) == 2

    def test_row_count_uses_count_delta(self, adapter, tmp_path):
        benchmark = _benchmark(tmp_path, "t", "1,a\n2,b\n", ".csv")
        connection = _NativeConnection()
        original = connection.load_file

        def load_file(sql, file, options, copy_options=None):
            original(sql, file, options)
            return _Stats(0)

        connection.load_file = load_file

        stats, _, _ = _run(adapter, connection, benchmark, tmp_path)

        assert stats["t"] == 2

    def test_row_count_uses_stats_when_count_unavailable(self, adapter, tmp_path):
        benchmark = _benchmark(tmp_path, "t", "1,a\n2,b\n", ".csv")
        connection = _NativeConnection()
        connection.query_iter = Mock(side_effect=RuntimeError("no count"))

        stats, _, _ = _run(adapter, connection, benchmark, tmp_path)

        assert stats["t"] == 2

    def test_row_count_unknown_is_a_failure(self, adapter, tmp_path):
        benchmark = _benchmark(tmp_path, "t", "1,a\n2,b\n", ".csv")
        connection = _NativeConnection()
        connection.query_iter = Mock(side_effect=RuntimeError("no count"))
        connection.load_file = Mock(return_value=_Stats(0))

        stats, _, _ = _run(adapter, connection, benchmark, tmp_path)

        assert stats["t"] == 0

    def test_tbl_without_trailing_delimiter_keeps_strict_column_count(self, adapter, tmp_path):
        benchmark = _benchmark(tmp_path, "t", "1|a|b\n2|c|d\n", ".tbl")
        connection = _NativeConnection()

        _run(adapter, connection, benchmark, tmp_path)

        assert "error_on_column_count_mismatch" not in connection.calls[0][2]

    def test_tpc_empty_fields_load_as_null_and_quote_is_explicit(self, adapter, tmp_path):
        benchmark = _benchmark(tmp_path, "t", "1|a|\n", ".tbl")
        connection = _NativeConnection()

        _run(adapter, connection, benchmark, tmp_path)

        options = connection.calls[0][2]
        assert options["empty_field_as"] == "null"
        assert options["quote"] == '"'

    def test_unsafe_delimiter_rejected(self, adapter, tmp_path):
        benchmark = _benchmark(tmp_path, "t", "1;a\n", ".csv")
        benchmark.csv_delimiter = ";"
        connection = _NativeConnection()

        stats, _, _ = _run(adapter, connection, benchmark, tmp_path)

        assert stats["t"] == 0
        assert connection.calls == []

    def test_connection_without_load_file_uses_values_fallback(self, adapter, tmp_path):
        benchmark = _benchmark(tmp_path, "t", "1,a\n2,b\n", ".csv")
        connection = Mock(spec=["exec", "query_iter"])

        stats, _, _ = _run(adapter, connection, benchmark, tmp_path)

        assert stats["t"] == 2
        assert any("INSERT INTO" in str(c) for c in connection.exec.call_args_list)

    def test_non_utf8_encoding_uses_values_fallback(self, adapter, tmp_path):
        benchmark = _benchmark(tmp_path, "t", "1,a\n", ".csv")
        benchmark.csv_encoding = "latin-1"
        connection = Mock(spec=["exec", "query_iter", "load_file"])
        with patch(
            "benchbox.platforms.base.data_loading.resolve_csv_dialect",
            return_value=Mock(delimiter=",", encoding="latin-1", has_header=False, null_marker=None),
        ):
            stats, _, _ = _run(adapter, connection, benchmark, tmp_path)

        assert stats["t"] == 1
        connection.load_file.assert_not_called()
        assert connection.exec.called


class TestNativeValuesParity:
    @pytest.mark.parametrize(
        ("suffix", "content"),
        [
            (".tbl", "".join(line.rstrip("\n") + "|\n" for line in TPCH_TBL.splitlines())),
            (".csv", "1,alpha,x y\n2,beta,z\n3,gamma,w\n"),
            (".tbl", "1|a||\n2||c|\n"),
            (".tbl", "1|a|b\n2||c\n"),
        ],
    )
    def test_row_count_and_column_checksums_match(self, adapter, tmp_path, suffix, content):
        benchmark = _benchmark(tmp_path, "t", content, suffix)
        native = _NativeConnection()
        values = _ValuesConnection()

        native_stats, _, _ = _run(adapter, native, benchmark, tmp_path)
        values_stats, _, _ = _run(adapter, values, benchmark, tmp_path)

        assert native_stats == values_stats
        assert len(native.rows) == len(values.rows)
        assert _checksums(native.rows) == _checksums(values.rows)
        assert native.rows == values.rows
