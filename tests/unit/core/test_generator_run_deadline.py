from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchbox.core.exceptions import RunTimeoutError
from benchbox.core.tpcds import streams as tpcds_streams
from benchbox.core.tpcds.c_tools import DSQGenBinary
from benchbox.core.tpcds.dataframe_queries import production_binding
from benchbox.core.tpcds.generator import TPCDSDataGenerator
from benchbox.core.tpch import refresh
from benchbox.core.tpch.generator import TPCHDataGenerator
from benchbox.core.tpch.queries import QGenBinary
from benchbox.core.tpch.streams import TPCHStreams
from benchbox.utils import run_deadline
from benchbox.utils.run_deadline import (
    RunDeadline,
    RunTimeoutError,
    active_run_deadline,
    bind_run_deadline,
    current_run_deadline,
)
from benchbox.utils.tpc_compilation import BinaryInfo, TPCCompiler
from tests.unit.core.generator_stubs import make_stub, process_exists

pytestmark = [pytest.mark.unit, pytest.mark.medium]

HANG_SECONDS = 20
RUN_LIMIT_MESSAGE = "1-second limit"


@pytest.fixture
def deadline(monkeypatch: pytest.MonkeyPatch):
    clock = [1000.0]
    monkeypatch.setattr(run_deadline, "mono_time", lambda: clock[0])
    monkeypatch.setattr(run_deadline, "elapsed_seconds", lambda started: clock[0] - started)
    active = RunDeadline(1)
    clock[0] += 0.7
    with active_run_deadline(active):
        yield active


def _assert_stopped(excinfo: pytest.ExceptionInfo[RunTimeoutError], tool: str, started: float) -> None:
    message = str(excinfo.value)
    assert RUN_LIMIT_MESSAGE in message
    assert tool in message
    assert time.monotonic() - started < HANG_SECONDS / 2


def _assert_children_gone(stub) -> None:
    pids = stub.pids()
    assert pids
    assert not any(process_exists(pid) for pid in pids)


@pytest.fixture
def tpcds_generator(tmp_path: Path):
    def build(parallel: int = 1, compress: bool = False) -> TPCDSDataGenerator:
        kwargs = {"compress_data": True, "compression_type": "zstd"} if compress else {}
        return TPCDSDataGenerator(scale_factor=0.01, output_dir=tmp_path / "out", parallel=parallel, **kwargs)

    return build


@pytest.fixture
def tpch_generator(tmp_path: Path):
    def build(stub, parallel: int = 1, compress: bool = False) -> TPCHDataGenerator:
        kwargs = {"compress_data": True, "compression_type": "zstd"} if compress else {}
        generator = TPCHDataGenerator(scale_factor=0.01, output_dir=tmp_path / "out", parallel=parallel, **kwargs)
        (stub.path.parent / "dists.dss").write_text("dists", encoding="utf-8")
        generator._dbgen_exe = stub.path
        generator.dbgen_available = True
        generator.dbgen_path = stub.path.parent
        return generator

    return build


def _hanging_dsdgen(tmp_path, monkeypatch, **spec):
    return make_stub(tmp_path, monkeypatch, "dsdgen", hang=HANG_SECONDS, **spec)


@pytest.mark.parametrize(
    ("parallel", "compress", "method"),
    [
        (1, False, "_run_file_based_dsdgen"),
        (2, False, "_run_parallel_file_based_dsdgen"),
        (1, True, "_run_streaming_dsdgen"),
        (2, True, "_run_parallel_streaming_dsdgen"),
    ],
    ids=["single", "parallel", "streaming", "parallel-streaming"],
)
def test_dsdgen_is_ended_at_the_remaining_time(
    tmp_path, monkeypatch, deadline, tpcds_generator, parallel, compress, method
):
    stub = _hanging_dsdgen(tmp_path, monkeypatch, stdout="1|row\n")
    generator = tpcds_generator(parallel=parallel, compress=compress)
    generator.dsdgen_exe = stub.path
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)

    started = time.monotonic()
    with pytest.raises(RunTimeoutError) as excinfo:
        getattr(generator, method)(out)

    _assert_stopped(excinfo, "dsdgen", started)
    _assert_children_gone(stub)


def test_dsdgen_refresh_set_is_ended_at_the_remaining_time(tmp_path, monkeypatch, deadline, tpcds_generator):
    stub = _hanging_dsdgen(tmp_path, monkeypatch)
    generator = tpcds_generator()
    generator.dsdgen_exe = stub.path

    started = time.monotonic()
    with pytest.raises(RunTimeoutError) as excinfo:
        generator.generate_refresh_set(1, tmp_path / "refresh")

    _assert_stopped(excinfo, "dsdgen", started)
    _assert_children_gone(stub)


def test_a_pool_thread_sees_the_deadline_of_the_thread_that_started_it(deadline):
    seen: list[RunDeadline | None] = []

    def probe() -> None:
        seen.append(current_run_deadline())

    unbound = threading.Thread(target=probe)
    unbound.start()
    unbound.join()
    bound = threading.Thread(target=bind_run_deadline(probe))
    bound.start()
    bound.join()

    assert seen == [None, deadline]


@pytest.mark.parametrize("parallel", [1, 2], ids=["single", "parallel"])
def test_dbgen_is_ended_at_the_remaining_time(tmp_path, monkeypatch, deadline, tpch_generator, parallel):
    stub = make_stub(tmp_path, monkeypatch, "dbgen", hang=HANG_SECONDS)
    generator = tpch_generator(stub, parallel=parallel)

    started = time.monotonic()
    with pytest.raises(RunTimeoutError) as excinfo:
        generator._run_dbgen_native(tmp_path / "work")

    _assert_stopped(excinfo, "dbgen", started)
    _assert_children_gone(stub)


@pytest.mark.parametrize("parallel", [1, 2], ids=["serial-streaming", "parallel-streaming"])
def test_dbgen_streaming_is_ended_at_the_remaining_time(tmp_path, monkeypatch, deadline, tpch_generator, parallel):
    stub = make_stub(tmp_path, monkeypatch, "dbgen", hang=HANG_SECONDS, stdout="1|row|\n")
    generator = tpch_generator(stub, parallel=parallel, compress=True)

    started = time.monotonic()
    with pytest.raises(RunTimeoutError) as excinfo:
        generator._run_streaming_dbgen(tmp_path / "work")

    _assert_stopped(excinfo, "dbgen", started)
    _assert_children_gone(stub)


def test_dbgen_refresh_set_generation_is_ended_at_the_remaining_time(tmp_path, monkeypatch, deadline):
    stub = make_stub(tmp_path, monkeypatch, "dbgen", hang=HANG_SECONDS)
    (stub.path.parent / "dists.dss").write_text("dists", encoding="utf-8")
    monkeypatch.setattr(refresh, "TPCHDataGenerator", lambda **_: SimpleNamespace(dbgen_exe=stub.path))

    started = time.monotonic()
    with pytest.raises(RunTimeoutError) as excinfo:
        refresh.generate_refresh_update_sets(0.01, 1, 1, tmp_path / "refresh")

    _assert_stopped(excinfo, "dbgen", started)
    assert "refresh sets 1 to 1" in str(excinfo.value)
    _assert_children_gone(stub)


def test_dsqgen_single_query_is_ended_and_the_error_names_query_and_scale(tmp_path, monkeypatch, deadline):
    stub = make_stub(tmp_path, monkeypatch, "dsqgen", hang=HANG_SECONDS)
    dsqgen = DSQGenBinary()
    dsqgen.dsqgen_path = str(stub.path)

    started = time.monotonic()
    with pytest.raises(RunTimeoutError) as excinfo:
        dsqgen.generate(9, seed=7, scale_factor=100)

    _assert_stopped(excinfo, "dsqgen (query 9, scale factor 100)", started)
    _assert_children_gone(stub)


def test_dsqgen_stream_set_is_ended_and_the_error_names_streams_and_scale(tmp_path, monkeypatch, deadline):
    stub = make_stub(tmp_path, monkeypatch, "dsqgen", hang=HANG_SECONDS)
    templates_dir = DSQGenBinary().templates_dir
    monkeypatch.setattr(
        tpcds_streams, "_resolve_dsqgen_binary_and_templates", lambda: (stub.path.parent, templates_dir)
    )

    started = time.monotonic()
    with pytest.raises(RunTimeoutError) as excinfo:
        tpcds_streams.generate_dsqgen_streams(num_streams=3, scale_factor=10, seed=1)

    _assert_stopped(excinfo, "dsqgen -STREAMS 3 (scale factor 10)", started)
    _assert_children_gone(stub)


def test_qgen_query_is_ended_and_the_error_names_query_and_scale(tmp_path, monkeypatch, deadline):
    stub = make_stub(tmp_path, monkeypatch, "qgen", hang=HANG_SECONDS)
    qgen = QGenBinary()
    qgen.qgen_path = str(stub.path)

    started = time.monotonic()
    with pytest.raises(RunTimeoutError) as excinfo:
        qgen.generate(5, seed=3, scale_factor=100)

    _assert_stopped(excinfo, "qgen (query 5, scale factor 100)", started)
    _assert_children_gone(stub)


def test_qgen_stream_is_ended_and_the_error_names_stream_and_scale(tmp_path, monkeypatch, deadline):
    stub = make_stub(tmp_path, monkeypatch, "qgen", hang=HANG_SECONDS)
    streams = TPCHStreams(num_streams=1, scale_factor=10, output_dir=tmp_path / "streams")
    streams.output_dir.mkdir()

    started = time.monotonic()
    with pytest.raises(RunTimeoutError) as excinfo:
        streams._generate_stream_queries_qgen(0, stub.path, tmp_path)

    _assert_stopped(excinfo, "qgen (stream 0, scale factor 10)", started)
    _assert_children_gone(stub)


@pytest.mark.parametrize(
    ("binary", "method", "tool"),
    [
        ("dsdgen", "_compile_tpc_ds_binary", "make dsdgen (TPC-DS tools build)"),
        ("dbgen", "_compile_tpc_h_binary", "make dbgen (TPC-H tools build)"),
    ],
)
def test_a_compile_is_ended_at_the_remaining_time(tmp_path, monkeypatch, deadline, binary, method, tool):
    stub = make_stub(tmp_path, monkeypatch, "make", hang=HANG_SECONDS)
    monkeypatch.setenv("PATH", f"{stub.path.parent}:{os.environ['PATH']}")
    source = tmp_path / "source"
    source.mkdir()
    (source / "Makefile").write_text("", encoding="utf-8")
    info = BinaryInfo(name=binary, source_dir=source, binary_path=source / binary)

    started = time.monotonic()
    with pytest.raises(RunTimeoutError) as excinfo:
        getattr(TPCCompiler(auto_compile=True), method)(binary, info)

    _assert_stopped(excinfo, tool, started)
    _assert_children_gone(stub)


def test_a_child_is_not_started_once_the_limit_has_passed(tmp_path, monkeypatch, deadline, tpcds_generator):
    stub = _hanging_dsdgen(tmp_path, monkeypatch)
    generator = tpcds_generator()
    generator.dsdgen_exe = stub.path
    deadline._started -= 10
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)

    with pytest.raises(RunTimeoutError, match="was not started"):
        generator._run_file_based_dsdgen(out)

    assert stub.invocations() == []


def test_no_deadline_runs_the_child_to_completion(tmp_path, monkeypatch, tpcds_generator):
    stub = make_stub(tmp_path, monkeypatch, "dsdgen", files={"call_center.dat": 3})
    generator = tpcds_generator()
    generator.dsdgen_exe = stub.path
    out = tmp_path / "out"
    out.mkdir()

    generator._run_file_based_dsdgen(out)

    assert (out / "call_center.dat").read_text(encoding="utf-8").count("\n") == 3
    assert len(stub.invocations()) == 1


def test_a_failing_child_is_not_reported_as_the_run_limit(tmp_path, monkeypatch, deadline, tpcds_generator):
    failing = tmp_path / "dsdgen"
    failing.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    failing.chmod(0o755)
    generator = tpcds_generator()
    generator.dsdgen_exe = failing
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)

    with pytest.raises(RuntimeError, match="exit code 3"):
        generator._run_file_based_dsdgen(out)


def test_the_tpcds_parameter_binding_pool_sees_the_deadline(monkeypatch, deadline):
    seen: list[RunDeadline | None] = []

    def bind(number, **kwargs):
        seen.append(current_run_deadline())
        return SimpleNamespace(query_id=number, parameters={})

    monkeypatch.setattr(production_binding, "bind_parameters", bind)
    monkeypatch.setattr(production_binding, "is_bound", lambda number: True)
    production_binding.clear_binding_cache()
    try:
        production_binding.bind_power_stream([1, 2], scale_factor=1, seed=1, stream_id=0, dsqgen=object())
    finally:
        production_binding.clear_binding_cache()

    assert seen == [deadline, deadline]
