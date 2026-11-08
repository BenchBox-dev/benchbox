import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.base.data_loading import CsvDialect
from benchbox.platforms.firebolt import FireboltAdapter, FireboltColumnCountError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

TBL_DIALECT = CsvDialect(delimiter="|", has_header=False, null_marker="", normalize_booleans=False, quote=None)
CSV_DIALECT = CsvDialect(delimiter=",", has_header=False, null_marker=None, normalize_booleans=False, quote=None)


def _adapter(**config):
    try:
        return FireboltAdapter(**config)
    except ImportError:
        pytest.skip("Firebolt SDK not installed")


def _cursor(column_types, counts):
    cursor = Mock()
    cursor.fetchall.return_value = [(t,) for t in column_types]
    cursor.fetchone.side_effect = [(c,) for c in counts]
    return cursor


def _benchmark(path: Path, name: str = "region"):
    benchmark = Mock()
    benchmark.tables = {name: str(path)}
    return benchmark


class TestEligibility:
    def test_core_plain_file_eligible(self, tmp_path):
        f = tmp_path / "region.tbl"
        f.write_text("0|AFRICA|x|\n")
        assert _adapter()._core_native_load_eligible(f, TBL_DIALECT) is True

    def test_disabled_by_config(self, tmp_path):
        f = tmp_path / "region.tbl"
        f.write_text("0|AFRICA|x|\n")
        assert _adapter(core_native_load=False)._core_native_load_eligible(f, TBL_DIALECT) is False

    def test_cloud_mode_not_eligible(self, tmp_path):
        f = tmp_path / "region.tbl"
        f.write_text("0|AFRICA|x|\n")
        adapter = _adapter(client_id="id", client_secret="secret", account_name="acct", engine_name="eng")
        assert adapter._core_native_load_eligible(f, TBL_DIALECT) is False

    def test_compressed_file_not_eligible(self, tmp_path):
        f = tmp_path / "region.tbl.gz"
        f.write_bytes(b"x")
        assert _adapter()._core_native_load_eligible(f, TBL_DIALECT) is False

    def test_quoted_dialect_not_eligible(self, tmp_path):
        f = tmp_path / "a.csv"
        f.write_text("1,2\n")
        quoted = CsvDialect(delimiter=",", has_header=False, null_marker=None, normalize_booleans=False, quote='"')
        assert _adapter()._core_native_load_eligible(f, quoted) is False

    def test_oversize_file_not_eligible(self, tmp_path):
        f = tmp_path / "a.csv"
        f.write_text("1,2\n")
        with patch("benchbox.platforms.firebolt.CORE_UPLOAD_MAX_BYTES", 1):
            assert _adapter()._core_native_load_eligible(f, CSV_DIALECT) is False


class TestGeneratedSql:
    def test_tbl_sql(self):
        sql = FireboltAdapter._build_core_upload_sql('"region"', ["integer", "text"], "benchbox_load", TBL_DIALECT)
        assert sql == (
            'INSERT INTO "region" SELECT '
            "CAST(CASE WHEN LOWER(f0) = 'null' THEN NULL ELSE f0 END AS integer), "
            "CAST(CASE WHEN LOWER(f1) = 'null' THEN NULL ELSE f1 END AS text) "
            "FROM READ_CSV(URL => 'upload://benchbox_load', DELIMITER => '|', HEADER => FALSE, "
            "INFER_SCHEMA => FALSE, EMPTY_FIELD_AS_NULL => TRUE)"
        )

    def test_no_null_marker_keeps_empty_strings(self):
        sql = FireboltAdapter._build_core_upload_sql('"t"', ["text"], "p", CSV_DIALECT)
        assert "EMPTY_FIELD_AS_NULL => FALSE" in sql
        assert "NULL_STRING" not in sql
        assert "CAST(f0 AS text)" in sql

    def test_custom_null_marker(self):
        dialect = CsvDialect(delimiter=",", has_header=False, null_marker="\\N", normalize_booleans=False, quote=None)
        sql = FireboltAdapter._build_core_upload_sql('"t"', ["text"], "p", dialect)
        assert "NULL_STRING => '\\N'" in sql
        assert "EMPTY_FIELD_AS_NULL => FALSE" in sql

    def test_trailing_delimiter_column_ignored(self):
        sql = FireboltAdapter._build_core_upload_sql('"t"', ["integer"], "p", TBL_DIALECT)
        assert "f1" not in sql


class TestLoadSelection:
    def test_native_path_used_and_executemany_skipped(self, tmp_path):
        f = tmp_path / "region.tbl"
        f.write_text("0|AFRICA|x|\n1|AMERICA|y|\n")
        adapter = _adapter()
        cursor = _cursor(["integer", "text", "text"], [0, 2])
        connection = Mock()
        connection.cursor.return_value = cursor

        with patch.object(FireboltAdapter, "_post_core_upload") as post:
            stats, _, _ = adapter._load_data_via_insert(_benchmark(f), connection, tmp_path)

        assert stats == {"region": 2}
        assert not cursor.executemany.called
        sql, part, path = post.call_args[0]
        assert sql.startswith('INSERT INTO "region" SELECT')
        assert part == "benchbox_load"
        assert path == f

    def test_fallback_when_post_fails(self, tmp_path):
        f = tmp_path / "region.tbl"
        f.write_text("0|AFRICA|x|\n1|AMERICA|y|\n")
        adapter = _adapter()
        cursor = _cursor(["integer", "text", "text"], [0, 0])
        connection = Mock()
        connection.cursor.return_value = cursor

        with patch.object(FireboltAdapter, "_post_core_upload", side_effect=RuntimeError("boom")):
            stats, _, _ = adapter._load_data_via_insert(_benchmark(f), connection, tmp_path)

        assert stats == {"region": 2}
        assert cursor.executemany.called

    def test_fallback_when_schema_lookup_fails(self, tmp_path):
        f = tmp_path / "region.tbl"
        f.write_text("0|AFRICA|x|\n")
        adapter = _adapter()
        cursor = Mock()
        cursor.fetchall.side_effect = RuntimeError("no info schema")
        connection = Mock()
        connection.cursor.return_value = cursor

        with patch.object(FireboltAdapter, "_post_core_upload") as post:
            stats, _, _ = adapter._load_data_via_insert(_benchmark(f), connection, tmp_path)

        assert not post.called
        assert stats == {"region": 1}
        assert cursor.executemany.called

    def test_disabled_uses_executemany_only(self, tmp_path):
        f = tmp_path / "region.tbl"
        f.write_text("0|AFRICA|x|\n")
        adapter = _adapter(core_native_load=False)
        cursor = Mock()
        connection = Mock()
        connection.cursor.return_value = cursor

        with patch.object(FireboltAdapter, "_post_core_upload") as post:
            adapter._load_data_via_insert(_benchmark(f), connection, tmp_path)

        assert not post.called
        assert cursor.executemany.called


class TestParity:
    def test_native_inputs_match_executemany_parameters(self, tmp_path):
        lines = ["1|alpha|10.5|", "2||20.0|", "3|NULL|30.25|", "4|delta|40|"]
        f = tmp_path / "t.tbl"
        f.write_text("\n".join(lines) + "\n")
        adapter = _adapter()

        legacy_cursor = Mock()
        legacy_rows = []
        legacy_cursor.executemany.side_effect = lambda sql, rows: legacy_rows.extend(rows)
        count = adapter._load_file_via_insert(legacy_cursor, "?", '"t"', f, TBL_DIALECT)

        sql = FireboltAdapter._build_core_upload_sql('"t"', ["integer", "text", "numeric"], "p", TBL_DIALECT)

        assert count == len(lines) == len(legacy_rows)
        assert legacy_rows == [
            ("1", "alpha", "10.5"),
            ("2", None, "20.0"),
            ("3", None, "30.25"),
            ("4", "delta", "40"),
        ]
        assert sql.count("CAST(") == len(legacy_rows[0])
        assert "EMPTY_FIELD_AS_NULL => TRUE" in sql
        assert "LOWER(f1) = 'null'" in sql

    def test_native_row_count_is_server_delta(self, tmp_path):
        f = tmp_path / "t.tbl"
        f.write_text("1|a|\n2|b|\n3|c|\n")
        adapter = _adapter()
        cursor = _cursor(["integer", "text"], [10, 13])
        with patch.object(FireboltAdapter, "_post_core_upload"):
            rows = adapter._load_file_via_core_upload(cursor, "t", '"t"', f, TBL_DIALECT)
        assert rows == 3

    def test_post_core_upload_sends_multipart_to_server(self, tmp_path):
        f = tmp_path / "t.tbl"
        f.write_bytes(b"1|a|\n")
        captured = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured["path"] = self.path
                captured["content_type"] = self.headers["Content-Type"]
                captured["body"] = self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            adapter = _adapter(url=f"http://127.0.0.1:{server.server_port}", database="db1")
            adapter._post_core_upload("SELECT 1", "benchbox_load", f)
        finally:
            server.shutdown()
            server.server_close()

        assert "database=db1" in captured["path"]
        assert captured["content_type"].startswith("multipart/form-data")
        assert b'name="sql"' in captured["body"]
        assert b"SELECT 1" in captured["body"]
        assert b'name="benchbox_load"; filename="t.tbl"' in captured["body"]
        assert b"1|a|\n" in captured["body"]

    def test_post_core_upload_http_error_raises(self, tmp_path):
        f = tmp_path / "t.tbl"
        f.write_text("1|a|\n")

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(400)
                self.send_header("Content-Length", "3")
                self.end_headers()
                self.wfile.write(b"bad")

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            adapter = _adapter(url=f"http://127.0.0.1:{server.server_port}")
            with pytest.raises(RuntimeError, match="HTTP 400"):
                adapter._post_core_upload("SELECT 1", "benchbox_load", f)
        finally:
            server.shutdown()
            server.server_close()


class TestSafetyChecks:
    def test_quoted_data_falls_back_without_upload(self, tmp_path):
        f = tmp_path / "t.tbl"
        f.write_text('1|"a|b"|\n')
        cursor = _cursor(["integer", "text"], [])
        with patch.object(FireboltAdapter, "_post_core_upload") as post:
            rows = _adapter()._load_file_via_core_upload(cursor, "t", '"t"', f, TBL_DIALECT)
        assert rows is None
        assert not post.called

    @pytest.mark.parametrize("content", ["1|a|b|\n", "1|\n"])
    def test_field_count_mismatch_falls_back_without_upload(self, tmp_path, content):
        f = tmp_path / "t.tbl"
        f.write_text(content)
        cursor = _cursor(["integer", "text"], [])
        with patch.object(FireboltAdapter, "_post_core_upload") as post:
            rows = _adapter()._load_file_via_core_upload(cursor, "t", '"t"', f, TBL_DIALECT)
        assert rows is None
        assert not post.called

    def test_scan_handles_crlf_and_missing_final_newline(self, tmp_path):
        f = tmp_path / "t.tbl"
        f.write_bytes(b"\r\n1|a|\r\n\r\n2|b|")
        assert FireboltAdapter._scan_core_upload_file(f) == (2, False, "1|a|")

    def test_no_fallback_when_failed_upload_changed_table(self, tmp_path):
        f = tmp_path / "t.tbl"
        f.write_text("1|a|\n")
        cursor = _cursor(["integer", "text"], [0, 1])
        with (
            patch.object(FireboltAdapter, "_post_core_upload", side_effect=RuntimeError("timeout")),
            pytest.raises(RuntimeError, match="after modifying"),
        ):
            _adapter()._load_file_via_core_upload(cursor, "t", '"t"', f, TBL_DIALECT)

    def test_row_count_mismatch_raises(self, tmp_path):
        f = tmp_path / "t.tbl"
        f.write_text("1|a|\n2|b|\n\n")
        cursor = _cursor(["integer", "text"], [0, 1])
        with (
            patch.object(FireboltAdapter, "_post_core_upload"),
            pytest.raises(RuntimeError, match="expected 2"),
        ):
            _adapter()._load_file_via_core_upload(cursor, "t", '"t"', f, TBL_DIALECT)

    @pytest.mark.parametrize("content", ["1|a|\n2|\n3|c|\n", "1|a|\n2|b|c|\n"])
    def test_malformed_row_raises_column_count_error_before_upload(self, tmp_path, content):
        f = tmp_path / "t.tbl"
        f.write_text(content)
        cursor = _cursor(["integer", "text"], [])
        with (
            patch.object(FireboltAdapter, "_post_core_upload") as post,
            pytest.raises(FireboltColumnCountError, match="line 2"),
        ):
            _adapter()._load_file_via_core_upload(cursor, "t", '"t"', f, TBL_DIALECT)
        assert not post.called

    def test_malformed_file_is_not_reported_as_zero_row_load(self, tmp_path):
        f = tmp_path / "region.tbl"
        f.write_text("0|AFRICA|\n1|\n")
        cursor = _cursor(["integer", "text"], [])
        connection = Mock()
        connection.cursor.return_value = cursor
        with pytest.raises(FireboltColumnCountError, match="Inconsistent column count"):
            _adapter()._load_data_via_insert(_benchmark(f), connection, tmp_path)

    def test_insert_fallback_inconsistent_rows_raise_column_count_error(self, tmp_path):
        f = tmp_path / "region.tbl"
        f.write_text("0|AFRICA|\n1|\n")
        cursor = Mock()
        with pytest.raises(FireboltColumnCountError):
            _adapter()._load_file_via_insert(cursor, "?", '"region"', f, TBL_DIALECT)

    def test_scan_counts_non_blank_rows(self, tmp_path):
        f = tmp_path / "t.tbl"
        f.write_text("1|a|\n\n2|b|\n")
        assert FireboltAdapter._scan_core_upload_file(f) == (2, False, "1|a|")
