# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DS (TPC-DS) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DS specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from benchbox.core.tpcds.streams import (
    DSQGenStreamsError,
    _parse_dsqgen_stream_log,
    _resolve_dsqgen_binary_and_templates,
    _run_dsqgen_streams,
    _split_dsqgen_stream_sql,
    generate_dsqgen_streams,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.tpcds,
]


def _get_dsqgen_path() -> Path | None:
    try:
        tools_dir, templates_dir = _resolve_dsqgen_binary_and_templates()
    except Exception:
        return None

    dsqgen_path = tools_dir / ("dsqgen.exe" if sys.platform == "win32" else "dsqgen")
    if not dsqgen_path.exists() or not templates_dir.exists():
        return None
    return dsqgen_path


PINNED_SEED = 19620718
PINNED_NUM_STREAMS = 2
PINNED_STREAM0_ORDER_HEAD = [
    (96, None),
    (7, None),
    (75, None),
    (44, None),
    (39, "a"),
    (39, "b"),
]
PINNED_STREAM1_ORDER_HEAD = [
    (83, None),
    (32, None),
    (30, None),
    (92, None),
    (66, None),
    (84, None),
]

DSQGEN_NONREPRODUCIBLE_QUERY_IDS = {46}


class TestGenerateDsqgenStreamsRealBinary:
    pytestmark = pytest.mark.skipif(
        _get_dsqgen_path() is None,
        reason="dsqgen binary (or its query templates) not available for this platform",
    )

    @pytest.fixture(autouse=True)
    def _require_dsqgen_binary(self) -> None:
        if _get_dsqgen_path() is None:
            pytest.skip("dsqgen binary (or its query templates) not available for this platform")

    def test_official_ordering_pinned_for_fixed_seed(self):
        streams = generate_dsqgen_streams(num_streams=PINNED_NUM_STREAMS, scale_factor=1.0, seed=PINNED_SEED)

        assert set(streams.keys()) == {0, 1}

        stream0_order = [(q.query_id, q.variant) for q in streams[0]]
        stream1_order = [(q.query_id, q.variant) for q in streams[1]]

        assert stream0_order[:6] == PINNED_STREAM0_ORDER_HEAD
        assert stream1_order[:6] == PINNED_STREAM1_ORDER_HEAD

        assert stream0_order[:6] != stream1_order[:6]

    def test_official_ordering_includes_multi_part_expansion(self):
        streams = generate_dsqgen_streams(num_streams=PINNED_NUM_STREAMS, scale_factor=1.0, seed=PINNED_SEED)

        assert len(streams[0]) == 103
        assert len(streams[1]) == 103

        query_ids = {q.query_id for q in streams[0]}
        assert query_ids == set(range(1, 100))

        variants_for_39 = [q.variant for q in streams[0] if q.query_id == 39]
        assert variants_for_39 == ["a", "b"]

    def test_official_substitution_parameters_baked_into_sql(self):
        streams = generate_dsqgen_streams(num_streams=PINNED_NUM_STREAMS, scale_factor=1.0, seed=PINNED_SEED)
        first_sql = streams[0][0].sql

        assert first_sql is not None
        assert "time_dim.t_hour = 8" in first_sql
        assert "household_demographics.hd_dep_count = 5" in first_sql
        assert "store.s_store_name = 'ese'" in first_sql

    def test_deterministic_for_fixed_num_streams_and_seed(self):
        first = generate_dsqgen_streams(num_streams=2, scale_factor=1.0, seed=PINNED_SEED)
        second = generate_dsqgen_streams(num_streams=2, scale_factor=1.0, seed=PINNED_SEED)

        first_order = [(q.query_id, q.variant) for q in first[0]]
        second_order = [(q.query_id, q.variant) for q in second[0]]
        assert first_order == second_order

        for pos, (q1, q2) in enumerate(zip(first[0], second[0])):
            if q1.query_id in DSQGEN_NONREPRODUCIBLE_QUERY_IDS:
                continue
            assert q1.sql == q2.sql, f"non-deterministic SQL at position {pos} for query {q1.query_id}"

    def test_num_streams_must_be_positive(self):
        with pytest.raises(ValueError, match="num_streams must be >= 1"):
            generate_dsqgen_streams(num_streams=0, seed=PINNED_SEED)


class TestParseDsqgenStreamLog:
    def test_parses_begin_stream_and_template_markers(self):
        log_text = (
            "BEGIN STREAM 0\n"
            "Template: query96.tpl\n"
            "\tHOUR.01 = 8\n"
            "\n"
            "Template: query39.tpl\n"
            "\tSOME.01 = value\n"
            "\n"
            "BEGIN STREAM 1\n"
            "Template: query83.tpl\n"
        )

        parsed = _parse_dsqgen_stream_log(log_text)

        assert parsed[0] == [(96, None), (39, "a"), (39, "b")]
        assert parsed[1] == [(83, None)]

    def test_non_multi_part_template_not_expanded(self):
        log_text = "BEGIN STREAM 0\nTemplate: query1.tpl\n"
        parsed = _parse_dsqgen_stream_log(log_text)
        assert parsed[0] == [(1, None)]

    def test_lines_before_begin_stream_are_ignored(self):
        log_text = "Template: query1.tpl\nBEGIN STREAM 0\nTemplate: query2.tpl\n"
        parsed = _parse_dsqgen_stream_log(log_text)
        assert parsed[0] == [(2, None)]


class TestSplitDsqgenStreamSql:
    def test_splits_on_semicolon_newline_boundary(self):
        sql_text = "select 1;\nselect 2;\nselect 3;\n"
        statements = _split_dsqgen_stream_sql(sql_text)
        assert statements == ["select 1;", "select 2;", "select 3;"]

    def test_multiline_statement_preserved_as_one_entry(self):
        sql_text = "select 1\nfrom t\nwhere x = 1;\nselect 2;\n"
        statements = _split_dsqgen_stream_sql(sql_text)
        assert len(statements) == 2
        assert statements[0].startswith("select 1")
        assert statements[0].endswith(";")

    def test_empty_input_returns_empty_list(self):
        assert _split_dsqgen_stream_sql("") == []
        assert _split_dsqgen_stream_sql("   \n  ") == []


class TestGenerateDsqgenStreamsErrors:
    def test_missing_binary_raises_dsqgen_streams_error(self, monkeypatch):
        import benchbox.core.tpcds.streams as streams_mod

        def _fake_resolve():
            from pathlib import Path

            return Path("/nonexistent/tools"), Path("/nonexistent/templates")

        monkeypatch.setattr(streams_mod, "_resolve_dsqgen_binary_and_templates", _fake_resolve)

        with pytest.raises(DSQGenStreamsError, match="dsqgen binary not found"):
            generate_dsqgen_streams(num_streams=1, seed=PINNED_SEED)


class TestRunDsqgenStreams:
    _CITIES_OVERRUN = (
        "Runtime ERROR: Distribution over-run/under-run\n"
        "Check distribution definitions and usage for cities.\n"
        "index = -1, length=1000."
    )

    @staticmethod
    def _completed(returncode: int, stderr: str = "") -> CompletedProcess[str]:
        return CompletedProcess(args=["dsqgen"], returncode=returncode, stdout="", stderr=stderr)

    def test_windows_cities_overrun_retries_after_removing_partial_output(self, monkeypatch, tmp_path):
        import benchbox.core.tpcds.streams as streams_mod

        partial_sql = tmp_path / "query_0.sql"
        partial_log = tmp_path / "stream_params.log"
        partial_sql.write_text("partial", encoding="utf-8")
        partial_log.write_text("partial", encoding="utf-8")
        results = [self._completed(1, self._CITIES_OVERRUN), self._completed(0)]

        monkeypatch.setattr(streams_mod.sys, "platform", "win32")
        monkeypatch.setattr(streams_mod.subprocess, "run", lambda *args, **kwargs: results.pop(0))

        result = _run_dsqgen_streams(["dsqgen.exe"], temp_path=tmp_path, timeout=30, env={})

        assert result.returncode == 0
        assert not results
        assert not partial_sql.exists()
        assert not partial_log.exists()

    def test_windows_unknown_failure_is_not_retried(self, monkeypatch, tmp_path):
        import benchbox.core.tpcds.streams as streams_mod

        calls = 0

        def _fail_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            return self._completed(1, "different dsqgen failure")

        monkeypatch.setattr(streams_mod.sys, "platform", "win32")
        monkeypatch.setattr(streams_mod.subprocess, "run", _fail_once)

        result = _run_dsqgen_streams(["dsqgen.exe"], temp_path=tmp_path, timeout=30, env={})

        assert result.returncode == 1
        assert calls == 1

    def test_persistent_windows_cities_overrun_stops_at_retry_cap(self, monkeypatch, tmp_path):
        import benchbox.core.tpcds.streams as streams_mod

        calls = 0

        def _always_overrun(*args, **kwargs):
            nonlocal calls
            calls += 1
            return self._completed(1, self._CITIES_OVERRUN)

        monkeypatch.setattr(streams_mod.sys, "platform", "win32")
        monkeypatch.setattr("benchbox.core.tpcds.c_tools._WINDOWS_DSQGEN_MAX_ATTEMPTS", 3)
        monkeypatch.setattr(streams_mod.subprocess, "run", _always_overrun)

        result = _run_dsqgen_streams(["dsqgen.exe"], temp_path=tmp_path, timeout=30, env={})

        assert result.returncode == 1
        assert calls == 3

    def test_non_windows_cities_overrun_is_not_retried(self, monkeypatch, tmp_path):
        import benchbox.core.tpcds.streams as streams_mod

        calls = 0

        def _fail_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            return self._completed(1, self._CITIES_OVERRUN)

        monkeypatch.setattr(streams_mod.sys, "platform", "linux")
        monkeypatch.setattr(streams_mod.subprocess, "run", _fail_once)

        result = _run_dsqgen_streams(["dsqgen"], temp_path=tmp_path, timeout=30, env={})

        assert result.returncode == 1
        assert calls == 1
