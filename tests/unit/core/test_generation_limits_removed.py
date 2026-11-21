from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from benchbox.core.tpcds import c_tools, streams as tpcds_streams
from benchbox.core.tpcds.c_tools import DSQGenBinary
from benchbox.core.tpch import generator as tpch_generator_module, queries as tpch_queries
from benchbox.core.tpch.generator import TPCHDataGenerator
from benchbox.core.tpch.queries import QGenBinary
from benchbox.utils import tpc_compilation
from benchbox.utils.tpc_compilation import BinaryInfo, TPCCompiler
from tests.unit.core.generator_stubs import make_stub

pytestmark = [pytest.mark.unit, pytest.mark.medium]

STUB_RUNTIME_SECONDS = 700


class _Calls:
    def __init__(self) -> None:
        self.timeouts: list[object] = []


@pytest.fixture
def long_running_stub(monkeypatch: pytest.MonkeyPatch) -> _Calls:
    calls = _Calls()

    def run_child(cmd, *, tool, deadline, **kwargs):
        limit = kwargs.get("timeout")
        calls.timeouts.append(limit)
        if limit is not None and limit < STUB_RUNTIME_SECONDS:
            raise subprocess.TimeoutExpired(cmd, limit)
        return subprocess.CompletedProcess(cmd, 0, stdout="select 1;\n", stderr="")

    for module in (c_tools, tpch_queries, tpc_compilation):
        monkeypatch.setattr(module, "run_child", run_child)
    return calls


def test_dsqgen_query_outliving_the_old_limit_is_not_stopped(long_running_stub):
    dsqgen = DSQGenBinary()

    sql = dsqgen.generate(9, seed=7, scale_factor=1)

    assert sql.startswith("select 1")
    assert long_running_stub.timeouts == [None]


def test_dsqgen_stream_set_outliving_the_old_limit_is_not_stopped(long_running_stub, monkeypatch):
    def run_child(cmd, *, tool, deadline, **kwargs):
        long_running_stub.timeouts.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="stub")

    monkeypatch.setattr(c_tools, "run_child", run_child)

    with pytest.raises(tpcds_streams.DSQGenStreamsError) as excinfo:
        tpcds_streams.generate_dsqgen_streams(num_streams=2, scale_factor=1, seed=1)

    assert "timed out" not in str(excinfo.value)
    assert long_running_stub.timeouts == [None]


def test_qgen_outliving_the_old_limit_is_not_stopped(long_running_stub):
    qgen = QGenBinary()
    long_running_stub.timeouts.clear()

    sql = qgen.generate(5, seed=3, scale_factor=1)

    assert sql
    assert long_running_stub.timeouts == [None]


@pytest.mark.parametrize(
    ("binary", "method"),
    [("dsdgen", "_compile_tpc_ds_binary"), ("dbgen", "_compile_tpc_h_binary")],
)
def test_a_compile_outliving_the_old_limits_is_not_stopped(long_running_stub, tmp_path, binary, method):
    source = tmp_path / "source"
    source.mkdir()
    (source / "Makefile").write_text("", encoding="utf-8")
    info = BinaryInfo(name=binary, source_dir=source, binary_path=source / binary)

    result = getattr(TPCCompiler(auto_compile=True), method)(binary, info)

    assert "timed out" not in (result.error_message or "")
    assert long_running_stub.timeouts == [None]


def test_the_executable_probe_keeps_its_fifteen_second_limit(tmp_path, monkeypatch):
    stub = make_stub(tmp_path, monkeypatch, "probe-target", hang=30)

    started = time.monotonic()
    TPCCompiler(auto_compile=True)._verify_executable(stub.path)

    assert 14.5 <= time.monotonic() - started < 25


def test_the_dbgen_help_probe_keeps_its_five_second_limit(tmp_path, monkeypatch):
    stub = make_stub(tmp_path, monkeypatch, "dbgen", hang=30)
    generator = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path / "out")
    generator._dbgen_exe = stub.path
    generator.dbgen_available = True

    started = time.monotonic()
    supported = generator._check_stdout_support()

    assert supported is False
    assert 4.5 <= time.monotonic() - started < 15


@pytest.mark.parametrize(
    ("module", "removed"),
    [
        (c_tools, ["DSQGEN_QUERY_TIMEOUT_SECONDS", "-second limit", "timeout=timeout", "timeout: int"]),
        (tpcds_streams, ["timeout: int", "timeout=timeout", "timed out after"]),
        (tpch_queries, ["timeout=10"]),
        (
            tpc_compilation,
            ["timeout=300", "timeout=600", "timed out after 5 minutes", "timed out after 10 minutes"],
        ),
        (tpch_generator_module, ["timeout=30", "timeout=300", "timeout=600"]),
    ],
    ids=["c_tools", "tpcds-streams", "tpch-queries", "tpc-compilation", "tpch-generator"],
)
def test_the_strings_for_the_removed_limits_are_gone(module, removed):
    source = Path(module.__file__).read_text(encoding="utf-8")

    assert [text for text in removed if text in source] == []
