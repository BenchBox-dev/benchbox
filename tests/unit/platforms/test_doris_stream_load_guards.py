# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from unittest.mock import Mock

import pytest

from benchbox.platforms.doris import DorisAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _adapter(**overrides):
    try:
        return DorisAdapter(**overrides)
    except ImportError:
        pytest.skip("pymysql not installed")


def _resp(status_code: int, payload: dict) -> Mock:
    resp = Mock()
    resp.status_code = status_code
    resp.text = str(payload)
    resp.json.return_value = payload
    return resp


class TestStreamLoadHeaders:
    def test_csv_headers_carry_expect_format_and_ratio(self) -> None:
        headers = _adapter()._stream_load_headers(format_name="csv", delimiter="|")
        assert headers["Expect"] == "100-continue"
        assert headers["format"] == "csv"
        assert headers["column_separator"] == "|"
        assert "max_filter_ratio" in headers
        assert "trim_double_quotes" not in headers

    def test_tpc_load_trims_double_quotes(self) -> None:
        headers = _adapter()._stream_load_headers(format_name="csv", delimiter="|", is_tpc=True)
        assert headers["trim_double_quotes"] == "true"

    def test_parquet_headers_have_no_separator(self) -> None:
        headers = _adapter()._stream_load_headers(format_name="parquet")
        assert headers["format"] == "parquet"
        assert "column_separator" not in headers


class TestStreamLoadResponse:
    def test_non_200_rejected_with_status(self) -> None:
        with pytest.raises(RuntimeError, match="status 500"):
            _adapter()._handle_stream_load_response(_resp(500, {}), context="Stream Load")

    def test_failed_status_rejected_with_message(self) -> None:
        with pytest.raises(RuntimeError, match="Label Already Exists"):
            _adapter()._handle_stream_load_response(
                _resp(200, {"Status": "Fail", "Message": "Label Already Exists"}),
                context="Stream Load",
            )

    def test_success_returns_result_with_loaded_rows(self) -> None:
        result = _adapter()._handle_stream_load_response(
            _resp(200, {"Status": "Success", "NumberLoadedRows": 60175}),
            context="Stream Load",
        )
        assert result["NumberLoadedRows"] == 60175

    def test_publish_timeout_accepted(self) -> None:
        result = _adapter()._handle_stream_load_response(
            _resp(200, {"Status": "Publish Timeout", "NumberLoadedRows": 10}),
            context="Stream Load",
        )
        assert result["NumberLoadedRows"] == 10

    def test_filtered_rows_refused_at_zero_ratio(self) -> None:
        with pytest.raises(RuntimeError, match="refusing silent partial load"):
            _adapter(stream_load_max_filter_ratio="0")._handle_stream_load_response(
                _resp(200, {"Status": "Success", "NumberLoadedRows": 9, "NumberFilteredRows": 1}),
                context="Stream Load",
            )

    def test_filtered_rows_warn_and_accept_with_ratio(self) -> None:
        result = _adapter(stream_load_max_filter_ratio="0.1")._handle_stream_load_response(
            _resp(200, {"Status": "Success", "NumberLoadedRows": 9, "NumberFilteredRows": 1}),
            context="Stream Load",
        )
        assert result["NumberLoadedRows"] == 9


def test_zstd_data_files_open_without_the_zstd_command(tmp_path, monkeypatch):
    zstandard = pytest.importorskip("zstandard")
    from benchbox.platforms import doris as doris_module
    from benchbox.platforms.base import data_loading

    data_file = tmp_path / "region.tbl.zst"
    data_file.write_bytes(zstandard.ZstdCompressor().compress("0|AFRICA|\n".encode("latin-1")))
    monkeypatch.setattr(data_loading.shutil, "which", lambda _command: None)

    with doris_module.DorisAdapter._open_data_file_binary(data_file) as stream:
        assert stream.read() == b"0|AFRICA|\n"


