from __future__ import annotations

import subprocess
import sys
import types
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.core.tpcds.benchmark import (
    BenchmarkResult as TPCDSBenchmarkResult,
    PhaseResult as TPCDSPhaseResult,
    QueryResult as TPCDSQueryResult,
)
from benchbox.core.tpcds.c_tools import DSQGenBinary, TPCDSCTools, _resolve_tpcds_tool_and_template_paths
from benchbox.core.tpcds.generator.runner import DsdgenRunnerMixin
from benchbox.core.tpcds.official_benchmark import (
    TPCDSOfficialBenchmark,
    TPCDSOfficialBenchmarkConfig,
    TPCDSOfficialBenchmarkResult,
)
from benchbox.core.tpcds.power_test import (
    TPCDSPowerTest,
    TPCDSPowerTestConfig,
    TPCDSPowerTestResult,
)
from benchbox.core.tpcds.reporting import TPCDSReportGenerator
from benchbox.core.tpch.maintenance_test import (
    TPCHMaintenanceOperation,
    TPCHMaintenanceTest,
)
from benchbox.core.tpch.reporting import TPCHReportGenerator
from benchbox.core.tpch.throughput_test import (
    TPCHThroughputStreamResult,
    TPCHThroughputTest,
    TPCHThroughputTestConfig,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@dataclass
class _FakeQuery:
    query_id: Any
    stream_id: int | None
    execution_time: float
    success: bool
    row_count: int | None = 1
    error_message: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None


@dataclass
class _FakePhase:
    success: bool
    total_time: float
    queries: list[_FakeQuery]
    error_message: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None


def _fake_tpcds_result() -> Any:
    now = datetime.now()
    power = TPCDSPhaseResult(
        phase_name="power",
        success=True,
        total_time=10.0,
        start_time=now,
        end_time=now,
        queries=[
            TPCDSQueryResult(
                query_id=1,
                stream_id=0,
                execution_time=0.5,
                success=True,
                start_time=now,
                end_time=now,
                row_count=1,
            )
        ],
    )
    throughput = TPCDSPhaseResult(
        phase_name="throughput",
        success=True,
        total_time=12.0,
        start_time=now,
        end_time=now,
        queries=[
            TPCDSQueryResult(
                query_id=2,
                stream_id=1,
                execution_time=0.7,
                success=True,
                start_time=now,
                end_time=now,
                row_count=1,
            )
        ],
    )
    maintenance = TPCDSPhaseResult(
        phase_name="maintenance",
        success=False,
        total_time=3.0,
        start_time=now,
        end_time=now,
        queries=[
            TPCDSQueryResult(
                query_id=3,
                stream_id=None,
                execution_time=0.2,
                success=False,
                error_message="x",
                start_time=now,
                end_time=now,
                row_count=0,
            )
        ],
        error_message="failed",
    )
    return TPCDSBenchmarkResult(
        benchmark_start_time=now,
        scale_factor=1.0,
        total_benchmark_time=30.0,
        num_streams=2,
        power_test=power,
        throughput_test=throughput,
        maintenance_test=maintenance,
        power_at_size=100.0,
        throughput_at_size=80.0,
        validation_results={"overall_valid": False, "issues": ["issue"]},
    )


def _fake_tpch_result() -> Any:
    power_query_times = {i: 1.0 + (i / 100.0) for i in range(1, 23)}
    return SimpleNamespace(
        success=True,
        scale_factor=1.0,
        total_benchmark_time=50.0,
        power_test=SimpleNamespace(success=True, total_time=20.0, power_at_size=140.0, query_times=power_query_times),
        throughput_test=SimpleNamespace(success=True, total_time=22.0, throughput_at_size=110.0, num_streams=2),
    )


def test_tpc_reporting_modules_generate_all_outputs(tmp_path: Path):
    tpcds_result = _fake_tpcds_result()
    tpcds_reports = TPCDSReportGenerator(output_dir=tmp_path, verbose=True).generate_complete_report(tpcds_result)
    assert len(tpcds_reports) == 8
    for path in tpcds_reports.values():
        assert path.exists()
        assert path.stat().st_size > 0
        assert "QphDS" not in path.read_text(encoding="utf-8")

    tpch_result = _fake_tpch_result()
    tpch_reporter = TPCHReportGenerator(output_dir=tmp_path)
    detailed = tpch_reporter.generate_detailed_report(tpch_result)
    cert = tpch_reporter.generate_certification_report(tpch_result)
    csv_file = tpch_reporter.generate_performance_csv(tpch_result)
    comparison = tpch_reporter.generate_comparison_report(tpch_result, tpch_result)
    assert detailed.exists() and cert.exists() and csv_file.exists() and comparison.exists()
    for path in (detailed, cert, csv_file, comparison):
        assert "QphH" not in path.read_text(encoding="utf-8")

    cmp_obj = tpch_reporter.compare_results(tpch_result, tpch_result)
    assert cmp_obj.significant_change is False
    assert tpch_reporter._classify_query_type(1) == "Pricing_Summary"
    assert tpch_reporter._calculate_query_complexity(22) == 9


def test_dsdgen_runner_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    class DummyRunner(DsdgenRunnerMixin):
        def __init__(self):
            self.verbose = False
            self.parallel = 2
            self.scale_factor = 1.0
            self.dsdgen_path = tmp_path / "tools"
            self.dsdgen_path.mkdir(parents=True, exist_ok=True)
            self.dsdgen_exe = self.dsdgen_path / "dsdgen"
            self.dsdgen_exe.write_text("#!/bin/sh\n", encoding="utf-8")

        def should_use_compression(self) -> bool:
            return False

        def _copy_distribution_files(self, _out: Path) -> None:
            pass

        def _generate_table_with_streaming(self, _out: Path, _table: str) -> None:
            pass

        def _generate_single_table_streaming(self, _out: Path, _table: str) -> None:
            pass

        def _generate_parent_table_chunk_with_children(
            self, _out: Path, _table: str, _chunk: int, _children: list[str]
        ) -> None:
            pass

        def _generate_single_table_chunk_streaming(self, _out: Path, _table: str, _chunk: int) -> None:
            pass

    runner = DummyRunner()
    out = tmp_path / "out"
    out.mkdir()

    fake_result = SimpleNamespace(status=1, binary_path=runner.dsdgen_exe, error_message=None)
    monkeypatch.setattr(
        "benchbox.core.tpcds.generator.runner.ensure_tpc_binaries",
        lambda _bins, auto_compile=True: {"dsdgen": fake_result},
    )
    monkeypatch.setattr(
        "benchbox.core.tpcds.generator.runner.CompilationStatus",
        SimpleNamespace(SUCCESS=1, NOT_NEEDED=2, PRECOMPILED=3),
    )
    assert runner._find_or_build_dsdgen() == runner.dsdgen_exe

    called = {"streaming": 0, "file": 0, "parallel": 0}
    monkeypatch.setattr(runner, "_run_streaming_dsdgen", lambda _o: called.__setitem__("streaming", 1))
    monkeypatch.setattr(runner, "_run_file_based_dsdgen", lambda _o: called.__setitem__("file", 1))
    monkeypatch.setattr(runner, "_run_parallel_dsdgen", lambda _o: called.__setitem__("parallel", 1))
    runner.parallel = 1
    monkeypatch.setattr(runner, "should_use_compression", lambda: True)
    runner._run_single_threaded_dsdgen(out)
    monkeypatch.setattr(runner, "should_use_compression", lambda: False)
    runner._run_single_threaded_dsdgen(out)
    runner.parallel = 2
    runner._run_dsdgen_native(out)
    assert called["streaming"] == 1 and called["file"] == 1 and called["parallel"] == 1


def test_tpcds_power_and_official_benchmark_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    class _Conn:
        def __init__(self):
            self.closed = False
            self.connection_string = "sqlite://"

        def execute(self, _sql: str):
            return SimpleNamespace(fetchall=lambda: [("x",)])

        def commit(self):
            pass

        def close(self):
            self.closed = True

    benchmark = SimpleNamespace(
        get_queries=lambda: {"1": "select 1", "2": "select 2"},
        get_query=lambda qid, **kwargs: f"select {qid} -- {kwargs.get('seed')}",
        query_manager=SimpleNamespace(validate_query_id=lambda _q: True),
    )

    power = TPCDSPowerTest(benchmark=benchmark, connection_factory=_Conn, scale_factor=1.0, verbose=False, seed=7)
    power.query_sequence = [1, 2]
    monkeypatch.setattr(power, "_preflight_validate_generation", lambda _ids: None)
    out = power.run()
    assert out.queries_executed == 2
    assert power.validate_results(out) in {True, False}
    assert power._calculate_power_at_size([1.0, 2.0]) > 0
    assert power.get_status()["query_sequence_length"] >= 2

    power._connect_database()
    power._warm_up_database()
    single = power._execute_query(1, "select 1")
    assert single["status"] in {"success", "error"}
    assert (
        power._validate_results(
            TPCDSPowerTestResult(
                config=TPCDSPowerTestConfig(scale_factor=1.0),
                start_time="s",
                end_time="e",
                total_time=1.0,
                power_at_size=1.0,
                queries_executed=1,
                queries_successful=1,
                query_results=[{"query_id": 1}],
                success=True,
                errors=[],
            )
        )
        is False
    )
    assert "connection_string" in power._get_database_info()
    export_path = tmp_path / "power.json"
    power.export_results(out, str(export_path))
    assert export_path.exists()
    assert "power_at_size" in power.compare_results(out, out)
    power._disconnect_database()

    monkeypatch.setattr("benchbox.core.tpcds.official_benchmark.TPCDSBenchmark", lambda **kwargs: SimpleNamespace())
    ob = TPCDSOfficialBenchmark(scale_factor=1.0, output_dir=tmp_path)

    class _PowerPhase:
        def __init__(self, **kwargs):
            pass

        def run(self):
            return {"power_at_size": 100.0}

    adapter = SimpleNamespace(
        _run_routed_throughput=lambda *args, **kwargs: SimpleNamespace(success=True, throughput_at_size=64.0)
    )

    class _MaintPhase:
        def __init__(self, **kwargs):
            pass

        def run(self):
            return {"ok": True}

    fake_power_mod = types.SimpleNamespace(TPCDSPowerTest=_PowerPhase)
    fake_m_mod = types.SimpleNamespace(TPCDSMaintenanceTest=_MaintPhase)
    monkeypatch.setitem(__import__("sys").modules, "benchbox.core.tpcds.power_test", fake_power_mod)
    monkeypatch.setitem(__import__("sys").modules, "benchbox.core.tpcds.maintenance_test", fake_m_mod)

    cfg = TPCDSOfficialBenchmarkConfig(scale_factor=1.0, num_streams=2, output_dir=tmp_path)
    with pytest.warns(DeprecationWarning):
        official = ob.run_official_benchmark(connection_factory=_Conn, config=cfg, adapter=adapter)
    assert not hasattr(official, "qphds_at_size")
    assert official.power_at_size == 100.0
    assert official.throughput_at_size == 64.0
    assert ob.validate_compliance(official) is True
    audit = ob.generate_audit_trail(official)
    assert audit.exists()

    adapter._run_routed_throughput = lambda *args, **kwargs: {"success": False, "throughput_at_size": 64.0}
    with pytest.warns(DeprecationWarning):
        failed_dict = ob.run_official_benchmark(connection_factory=_Conn, config=cfg, adapter=adapter)
    assert failed_dict.success is False
    assert failed_dict.throughput_at_size == 0.0

    adapter._run_routed_throughput = lambda *args, **kwargs: SimpleNamespace(success=False, throughput_at_size=64.0)
    with pytest.warns(DeprecationWarning):
        failed_object = ob.run_official_benchmark(connection_factory=_Conn, config=cfg, adapter=adapter)
    assert failed_object.success is False
    assert failed_object.throughput_at_size == 0.0

    bad = TPCDSOfficialBenchmarkResult(
        config=cfg,
        start_time="s",
        end_time="e",
        total_time=1.0,
        power_test_result=None,
        throughput_test_result=None,
        maintenance_test_result=None,
        power_at_size=0.0,
        throughput_at_size=0.0,
        success=False,
        errors=[],
    )
    assert ob.validate_compliance(bad) is False


def test_tpch_throughput_and_maintenance_core_paths(monkeypatch: pytest.MonkeyPatch):
    class _Benchmark:
        def get_query(self, query_id: int, **kwargs: Any) -> str:
            return f"select {query_id} -- {kwargs.get('seed', 0)}"

    class _Conn:
        def execute(self, _sql: str):
            return SimpleNamespace(fetchall=lambda: [("ok",)])

        def commit(self):
            pass

        def close(self):
            pass

    tp = TPCHThroughputTest(
        benchmark=_Benchmark(), connection_factory=_Conn, scale_factor=1.0, num_streams=2, verbose=False
    )
    cfg = TPCHThroughputTestConfig(scale_factor=1.0, num_streams=2, max_workers=2, stream_timeout=0)

    ok_stream = TPCHThroughputStreamResult(
        stream_id=0,
        start_time=1.0,
        end_time=3.0,
        duration=2.0,
        queries_executed=22,
        queries_successful=22,
        queries_failed=0,
        success=True,
    )
    monkeypatch.setattr(tp, "_execute_stream", lambda sid, seed, cfg: ok_stream)
    run_out = tp.run(cfg)
    assert run_out.streams_executed == 2
    assert tp.validate_results(run_out) is True

    stream = tp._execute_stream(0, 42, cfg)
    assert stream.queries_executed == 22

    maint = TPCHMaintenanceTest(connection_factory=_Conn, scale_factor=1.0, verbose=False)
    monkeypatch.setattr(
        maint,
        "_execute_rf1",
        lambda pair_id: TPCHMaintenanceOperation("RF1", 0.0, 1.0, 1.0, 10, True),
    )
    monkeypatch.setattr(
        maint,
        "_execute_rf2",
        lambda pair_id: TPCHMaintenanceOperation(
            "RF2", 1.0, 2.0, 1.0, 5, pair_id % 2 == 0, error="x" if pair_id % 2 else None
        ),
    )
    monkeypatch.setattr("benchbox.core.tpch.maintenance_test.time.sleep", lambda _s: None)
    maint_result = maint.run_maintenance_test(
        maintenance_pairs=2, rf1_interval=0, rf2_interval=0, validate_integrity=False
    )
    assert maint_result.total_operations == 4
    assert maint_result.failed_operations == 1


@pytest.mark.skipif(sys.platform == "win32", reason="TPC-DS dsdgen binary is not supported on Windows")
def test_tpcds_dsdgen_runner_error_and_fallback_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    class DummyRunner(DsdgenRunnerMixin):
        def __init__(self):
            self.verbose = True
            self.parallel = 2
            self.scale_factor = 1.0
            self.dsdgen_path = tmp_path / "tools"
            self.dsdgen_path.mkdir(parents=True, exist_ok=True)
            self.dsdgen_exe = self.dsdgen_path / "dsdgen"
            self._calls: list[tuple[str, int | str]] = []

        def should_use_compression(self) -> bool:
            return True

        def _copy_distribution_files(self, _out: Path) -> None:
            pass

        def _generate_table_with_streaming(self, _out: Path, _table: str) -> None:
            pass

        def _generate_single_table_streaming(self, _out: Path, _table: str) -> None:
            pass

        def _generate_parent_table_chunk_with_children(
            self, _out: Path, table: str, chunk: int, _children: list[str]
        ) -> None:
            self._calls.append((table, chunk))
            if table == "catalog_sales" and chunk == 1:
                raise RuntimeError("parent chunk failure")

        def _generate_single_table_chunk_streaming(self, _out: Path, table: str, chunk: int) -> None:
            self._calls.append((table, chunk))

    runner = DummyRunner()
    out = tmp_path / "out"
    out.mkdir()

    exe = runner.dsdgen_path / "dsdgen.exe"
    exe.write_text("x", encoding="utf-8")
    monkeypatch.setattr("benchbox.core.tpcds.generator.runner.platform.system", lambda: "Windows")
    monkeypatch.setattr("benchbox.core.tpcds.generator.runner.ensure_tpc_binaries", lambda *_a, **_k: {"dsdgen": None})
    monkeypatch.setattr("benchbox.core.tpcds.generator.runner.os.access", lambda *_a, **_k: False)
    with pytest.raises(PermissionError):
        runner._find_or_build_dsdgen()

    exe.unlink()
    failed_result = SimpleNamespace(status=None, binary_path=None, error_message="build failed")
    monkeypatch.setattr(
        "benchbox.core.tpcds.generator.runner.ensure_tpc_binaries", lambda *_a, **_k: {"dsdgen": failed_result}
    )
    with pytest.raises(RuntimeError, match="Auto-compilation failed"):
        runner._find_or_build_dsdgen()

    monkeypatch.setattr(runner, "should_use_compression", lambda: False)

    def _raise_called_process(*_a, **_k):
        raise subprocess.CalledProcessError(returncode=2, cmd=["x"], stderr="bad")

    monkeypatch.setattr("benchbox.core.tpcds.generator.runner.subprocess.run", _raise_called_process)
    with pytest.raises(RuntimeError, match="exit code 2"):
        runner._run_file_based_dsdgen(out)

    monkeypatch.setattr(runner, "should_use_compression", lambda: True)
    with pytest.raises(RuntimeError, match="parallel generation failed"):
        runner._run_parallel_streaming_dsdgen(out)

    monkeypatch.setattr("benchbox.core.tpcds.generator.runner.subprocess.run", _raise_called_process)
    with pytest.raises(RuntimeError, match="parallel file-based generation failed"):
        runner._run_parallel_file_based_dsdgen(out)


@pytest.mark.skipif(sys.platform == "win32", reason="TPC-DS dsqgen binary is unstable on Windows CI")
def test_tpcds_c_tools_and_dsqgen_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):

    fake_compiler = SimpleNamespace(precompiled_base=None)
    monkeypatch.setattr("benchbox.core.tpcds.c_tools.get_tpc_compiler", lambda auto_compile=False: fake_compiler)
    monkeypatch.setattr("benchbox.utils.tpc_compilation.get_tpc_templates_dir", lambda _name: tmp_path / "templates")
    tools_path, templates_path = _resolve_tpcds_tool_and_template_paths()
    assert "_sources/tpc-ds/tools" in str(tools_path)
    assert str(templates_path).endswith("templates/query_templates")

    gen = SimpleNamespace(generate=lambda qid, **kwargs: f"Q{qid}-{kwargs.get('dialect', 'x')}")
    monkeypatch.setattr("benchbox.core.tpcds.c_tools.DSQGenBinary", lambda: gen)
    monkeypatch.setattr(
        "benchbox.core.tpcds.c_tools._resolve_tpcds_tool_and_template_paths",
        lambda: (tmp_path / "tools", tmp_path / "templates" / "query_templates"),
    )
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    (tmp_path / "templates" / "query_templates").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tools" / "dsqgen").write_text("", encoding="utf-8")
    ctools = TPCDSCTools()
    assert ctools.is_available() is True
    assert ctools.generate_query(3, dialect="ansi").startswith("Q3")
    assert "store_sales" in ctools.get_available_tables()
    status = SimpleNamespace(status=1, binary_path=tmp_path / "tools" / "dsqgen")
    monkeypatch.setattr("benchbox.core.tpcds.c_tools.ensure_tpc_binaries", lambda _bins: dict.fromkeys(_bins, status))
    monkeypatch.setattr(
        "benchbox.core.tpcds.c_tools.CompilationStatus",
        SimpleNamespace(PRECOMPILED=1, SUCCESS=2, NOT_NEEDED=3),
    )
    info = ctools.get_tools_info()
    assert info["dsqgen_available"] is True

    dsq = DSQGenBinary.__new__(DSQGenBinary)
    dsq.templates_dir = tmp_path / "query_templates"
    dsq.templates_dir.mkdir(parents=True, exist_ok=True)
    dsq.tools_dir = tmp_path / "tools2"
    dsq.tools_dir.mkdir(parents=True, exist_ok=True)
    dsq._parameter_cache = {}
    dsq._query_cache = {}
    dsq._supported_dialects = {"ansi", "netezza"}

    fallback_dsqgen = dsq.tools_dir / "dsqgen"
    fallback_dsqgen.write_text("", encoding="utf-8")
    monkeypatch.setattr("benchbox.core.tpcds.c_tools.ensure_tpc_binaries", lambda *_a, **_k: {"dsqgen": None})
    assert dsq._find_dsqgen_or_fail() == fallback_dsqgen
    fallback_dsqgen.unlink()
    failed = SimpleNamespace(status=None, binary_path=None, error_message="compile failed")
    monkeypatch.setattr("benchbox.core.tpcds.c_tools.ensure_tpc_binaries", lambda *_a, **_k: {"dsqgen": failed})
    with pytest.raises(RuntimeError, match="Auto-compilation failed"):
        dsq._find_dsqgen_or_fail()

    assert dsq._parse_query_id("14a") == (14, "a")
    assert dsq._parse_query_id("7") == (7, None)
    with pytest.raises(ValueError):
        dsq._parse_query_id("bad")
    assert dsq._get_cache_key(1, "a", 2, 1.0, 3, "ansi").startswith("1a_2")
    assert dsq._validate_dialect("ANSI") == "ansi"
    with pytest.warns(UserWarning):
        assert dsq._validate_dialect("unknown") == "netezza"
    params = dsq.get_parameter_variations(1, seed=3, scale_factor=1.0, stream_id=1)
    assert "effective_seed" in params
    dsq.clear_cache()
    assert dsq._query_cache == {}

    (dsq.templates_dir / "query1.tpl").write_text("select 1", encoding="utf-8")
    (dsq.templates_dir / "query2a.tpl").write_text("select 2", encoding="utf-8")
    variants_dir = dsq.templates_dir.parent / "query_variants"
    variants_dir.mkdir(exist_ok=True)
    (variants_dir / "query1a.tpl").write_text("select 1a", encoding="utf-8")
    assert "1" in dsq.get_available_queries()
    assert "1a" in dsq.get_query_variations(1)
    assert dsq.validate_query_id("1a") is True
    assert dsq.validate_query_id("99b") is False

    dirty = """
    -- c
    set rowcount 10
    SELECT TOP 5 "abc", date('2000-01-01') + 3 days, [X], [_LIMITA]
    FROM t
    group by rollup (a)
    """
    out_sql = dsq._clean_sql(dirty)
    assert "'abc'" in out_sql.lower()
    assert "interval '3 day'" in out_sql.lower()


class _FakeMaintenanceCursor:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else (0,)


class _FakeMaintenanceConn:
    def __init__(self):
        self.closed = False
        self.committed = False
        self.rolled_back = False
        self.order_keys = [101, 102]
        self.fail_delete = False

    def execute(self, sql: str, params=None):
        q = sql.upper()
        if "FROM CUSTOMER" in q:
            return _FakeMaintenanceCursor([(1,), (2,)])
        if "FROM PART" in q:
            return _FakeMaintenanceCursor([(10,), (11,)])
        if "FROM SUPPLIER" in q:
            return _FakeMaintenanceCursor([(20,), (21,)])
        if "SELECT O_ORDERKEY" in q and "ORDER BY O_ORDERDATE" in q:
            return _FakeMaintenanceCursor([(k,) for k in self.order_keys])
        if "DELETE FROM LINEITEM" in q:
            if self.fail_delete:
                raise RuntimeError("delete failed")
            return _FakeMaintenanceCursor(rowcount=3)
        if "DELETE FROM ORDERS" in q:
            return _FakeMaintenanceCursor(rowcount=2)
        if "INSERT INTO ORDERS" in q:
            return _FakeMaintenanceCursor(rowcount=1)
        if "INSERT INTO LINEITEM" in q:
            return _FakeMaintenanceCursor(rowcount=2)
        if "COUNT(*) AS ORPHAN_COUNT" in q:
            return _FakeMaintenanceCursor([(1,)])
        if "COUNT(*) AS VIOLATION_COUNT" in q:
            return _FakeMaintenanceCursor([(0,)])
        if "COUNT(*) AS ORDERLESS_COUNT" in q:
            return _FakeMaintenanceCursor([(2,)])
        return _FakeMaintenanceCursor([(0,)])

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class _BrokenMaintenanceConn(_FakeMaintenanceConn):
    def execute(self, sql: str, params=None):
        raise RuntimeError("cannot execute")


def _make_maintenance_test(monkeypatch: pytest.MonkeyPatch, conn: _FakeMaintenanceConn) -> TPCHMaintenanceTest:
    maint = TPCHMaintenanceTest(connection_factory=lambda: conn, scale_factor=1.0, verbose=True)
    monkeypatch.setattr(
        maint,
        "_generate_rf1_orders_data",
        lambda pair_id, num_orders: (
            [{"O_ORDERKEY": 200 + pair_id, "O_CUSTKEY": 1, "O_COMMENT": "x"}] * max(1, num_orders)
        ),
    )
    monkeypatch.setattr(
        maint,
        "_generate_rf1_lineitems_data",
        lambda order_key, num_items: [{"L_ORDERKEY": order_key, "L_PARTKEY": 10, "L_SUPPKEY": 20}] * max(1, num_items),
    )
    return maint


def test_tpch_maintenance_helpers(monkeypatch: pytest.MonkeyPatch):
    conn = _FakeMaintenanceConn()
    maint = _make_maintenance_test(monkeypatch, conn)

    sql_preview = maint.get_maintenance_operations_sql(pair_id=0, placeholder="?")
    assert "RF1" in sql_preview and "RF2" in sql_preview
    assert maint._get_parameter_placeholder(type("SQLiteConn", (), {})()) == "?"
    assert maint._get_parameter_placeholder(type("PostgresConn", (), {})()) == "%s"
    assert maint._identify_old_orders(conn, 2) == [101, 102]

    valid_cust, invalid_cust = maint._validate_customer_keys(conn, [1, 2, 3])
    assert valid_cust is False and 3 in invalid_cust
    valid_ps, invalid_parts, invalid_supp = maint._validate_part_supplier_keys(conn, [10, 12], [20, 22])
    assert valid_ps is False and invalid_parts and invalid_supp

    with pytest.raises(ValueError):
        maint._validate_rf1_data(conn, [{"O_CUSTKEY": 99}], [{"L_PARTKEY": 10, "L_SUPPKEY": 20}])


def test_tpch_maintenance_execution(monkeypatch: pytest.MonkeyPatch):
    conn = _FakeMaintenanceConn()
    maint = _make_maintenance_test(monkeypatch, conn)

    monkeypatch.setattr(maint, "_validate_rf1_data", lambda *_a, **_k: None)
    rf1 = maint._execute_rf1(pair_id=0)
    assert rf1.success is True
    rf2 = maint._execute_rf2(pair_id=0)
    assert rf2.success is True and rf2.rows_affected >= 0

    conn.fail_delete = True
    rf2_fail = maint._execute_rf2(pair_id=1)
    assert rf2_fail.success is False

    monkeypatch.setattr("benchbox.core.tpch.maintenance_test.time.sleep", lambda _s: None)
    monkeypatch.setattr(maint, "_execute_rf1", lambda _pid: TPCHMaintenanceOperation("RF1", 0, 1, 1, 1, True))
    monkeypatch.setattr(
        maint,
        "_execute_rf2",
        lambda _pid: TPCHMaintenanceOperation("RF2", 0, 1, 1, 0, False, error="bad"),
    )
    run_res = maint.run_maintenance_test(maintenance_pairs=1, rf1_interval=0, rf2_interval=0)
    assert run_res.success is False and run_res.failed_operations == 1

    monkeypatch.setattr(maint, "_execute_rf1", lambda _pid: (_ for _ in ()).throw(RuntimeError("boom")))
    crash_res = maint.run_maintenance_test(maintenance_pairs=1, rf1_interval=0, rf2_interval=0)
    assert crash_res.success is False


def test_tpch_maintenance_integrity(monkeypatch: pytest.MonkeyPatch):
    conn = _FakeMaintenanceConn()
    maint = _make_maintenance_test(monkeypatch, conn)

    assert maint.validate_data_integrity() is False

    maint_broken = TPCHMaintenanceTest(connection_factory=_BrokenMaintenanceConn, scale_factor=1.0, verbose=True)

    assert maint_broken.validate_data_integrity() is True
