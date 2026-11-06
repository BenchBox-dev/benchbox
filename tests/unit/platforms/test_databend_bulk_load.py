import gzip
import hashlib
import re
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.databend.adapter import DatabendAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]


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
        self.methods = []

    def exec(self, sql):
        if not sql.startswith("USE "):
            raise AssertionError(f"unexpected exec: {sql[:80]}")

    def query_iter(self, sql):
        return iter([(len(self.rows),)])

    def load_file(self, sql, fp, method=None, format_options=None, copy_options=None):
        file = fp
        self.methods.append(method)
        self.calls.append((sql, file, dict(format_options)))
        path = Path(file)
        raw = path.read_bytes()
        lines = [line for line in raw.decode("utf-8").split("\n") if line]
        delimiter = format_options["field_delimiter"]
        lines = lines[int(format_options["skip_header"]) :]
        null = format_options.get("null_display", "\\N")
        empty_is_null = format_options.get("empty_field_as") == "null"
        parsed = [line.split(delimiter) for line in lines]
        if len({len(values) for values in parsed}) > 1:
            raise ValueError("column count mismatch")
        loaded = 0
        for values in parsed:
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
        assert file != str(tmp_path / "customer.tbl")
        assert options["field_delimiter"] == "|"
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
        assert "quote" not in options

    def test_crlf_and_blank_lines_are_normalised_before_load(self, adapter, tmp_path):
        benchmark = _benchmark(tmp_path, "t", "1,a\n", ".csv")
        (tmp_path / "t.csv").write_bytes(b"1,a\r\n\r\n2,b\r\n")
        uploaded: list[bytes] = []

        class _Capturing(_NativeConnection):
            def load_file(self, sql, fp, method=None, format_options=None, copy_options=None):
                uploaded.append(Path(fp).read_bytes())
                return super().load_file(sql, fp, method, format_options, copy_options)

        connection = _Capturing()

        stats, _, _ = _run(adapter, connection, benchmark, tmp_path)

        assert stats["t"] == 2
        assert uploaded == [b"1,a\n2,b\n"]
        assert connection.rows == [("1", "a"), ("2", "b")]

    def test_gzip_is_decompressed_locally_before_load(self, adapter, tmp_path):
        benchmark = _benchmark(tmp_path, "t", "1,a\n2,b\n", ".csv.gz", opener=gzip.open)
        connection = _NativeConnection()

        stats, _, _ = _run(adapter, connection, benchmark, tmp_path)

        assert stats["t"] == 2
        assert connection.calls[0][2]["compression"] == "NONE"

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

        def load_file(sql, fp, method=None, format_options=None, copy_options=None):
            original(sql, fp, method, format_options)
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

        with pytest.raises(RuntimeError, match="Could not determine rows loaded"):
            _run(adapter, connection, benchmark, tmp_path)

    def test_positional_format_options_would_fail_against_driver_signature(self, adapter, tmp_path):
        benchmark = _benchmark(tmp_path, "t", "1,a\n", ".csv")
        connection = _NativeConnection()

        _run(adapter, connection, benchmark, tmp_path)

        assert connection.methods == ["stage"]

    def test_row_count_mismatch_with_source_fails_loudly(self, adapter, tmp_path):
        benchmark = _benchmark(tmp_path, "t", "1,a\n2,b\n3,c\n", ".csv")
        connection = _NativeConnection()
        original = connection.load_file

        def dropping(sql, fp, method=None, format_options=None, copy_options=None):
            original(sql, fp, method, format_options)
            connection.rows.pop()
            return _Stats(2)

        connection.load_file = dropping

        with pytest.raises(RuntimeError, match="source has 3, loaded 2"):
            _run(adapter, connection, benchmark, tmp_path)

    def test_quoted_embedded_newline_counts_as_one_source_record(self, adapter, tmp_path):
        path = tmp_path / "q.csv"
        path.write_text('1,"a\nb"\n2,c\n', encoding="utf-8")
        dialect = Mock(has_header=False, quote='"', delimiter=",")

        assert DatabendAdapter._count_source_records(path, dialect) == 2

    def test_ragged_rows_fail_instead_of_loading(self, adapter, tmp_path):
        benchmark = _benchmark(tmp_path, "t", "1|a|\n2|b|c|d|\n", ".tbl")
        connection = _NativeConnection()

        with pytest.raises(RuntimeError, match="column count mismatch"):
            _run(adapter, connection, benchmark, tmp_path)

        assert connection.rows == []

    def test_tpc_empty_fields_load_as_null_and_default_quote_is_left_to_server(self, adapter, tmp_path):
        benchmark = _benchmark(tmp_path, "t", "1|a|\n", ".tbl")
        connection = _NativeConnection()

        _run(adapter, connection, benchmark, tmp_path)

        options = connection.calls[0][2]
        assert options["empty_field_as"] == "null"
        assert "quote" not in options

    def test_null_text_in_any_case_loads_as_null_like_values_path(self, adapter, tmp_path):
        content = "1|NULL|x|\n2|null|Null|\n3||nullable|\n4|nul|NULLS|\n"
        native, values = _NativeConnection(), _ValuesConnection()

        _run(adapter, native, _benchmark(tmp_path, "t", content, ".tbl"), tmp_path)
        _run(adapter, values, _benchmark(tmp_path, "t", content, ".tbl"), tmp_path)

        assert native.rows == [
            ("1", None, "x"),
            ("2", None, None),
            ("3", None, "nullable"),
            ("4", "nul", "NULLS"),
        ]
        assert native.rows == values.rows

    def test_double_quote_in_unquoted_data_uses_values_path(self, adapter, tmp_path):
        benchmark = _benchmark(tmp_path, "t", '1|say "hi"|\n2|plain|\n', ".tbl")
        connection = Mock(spec=["exec", "query_iter", "load_file"])
        connection.query_iter.return_value = iter([(0,)])

        stats, _, _ = _run(adapter, connection, benchmark, tmp_path)

        assert stats["t"] == 2
        connection.load_file.assert_not_called()
        assert any('say "hi"' in str(c) for c in connection.exec.call_args_list)

    def test_dialect_quote_is_passed(self, adapter, tmp_path):
        benchmark = _benchmark(tmp_path, "t", "1,a\n", ".csv")
        benchmark.csv_quote = "'"
        connection = _NativeConnection()
        with patch(
            "benchbox.platforms.base.data_loading.resolve_csv_dialect",
            return_value=Mock(
                delimiter=",", encoding="utf-8", has_header=False, null_marker=None, quote="'", normalize_booleans=False
            ),
        ):
            _run(adapter, connection, benchmark, tmp_path)

        assert connection.calls[0][2]["quote"] == "'"

    def test_unsafe_delimiter_rejected(self, adapter, tmp_path):
        benchmark = _benchmark(tmp_path, "t", "1;a\n", ".csv")
        benchmark.csv_delimiter = ";"
        connection = _NativeConnection()

        with pytest.raises(RuntimeError, match="Unsafe delimiter"):
            _run(adapter, connection, benchmark, tmp_path)

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
