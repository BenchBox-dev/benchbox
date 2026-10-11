from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from scripts.tuning_evidence import platforms
from scripts.tuning_evidence.harness import ArmHandle, ArmSpec

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SOURCE_DDL = (
    "CREATE TABLE te_src.lineitem\n(\n    `l_orderkey` Int64,\n    `l_shipdate` Date\n)\n"
    "ENGINE = MergeTree\nORDER BY (l_orderkey, l_linenumber)\nSETTINGS index_granularity = 8192"
)


def server_seam(tmp_path: Path, **kwargs: Any) -> platforms.ClickHouseServerSeam:
    return platforms.ClickHouseServerSeam(
        benchmark="tpch", scale_factor=1.0, work_dir=tmp_path, run_tag="t1", settle_sleep=lambda seconds: None, **kwargs
    )


class FakeClient:
    def __init__(self, responses: dict[str, Any] | None = None, failing: tuple[str, ...] = ()) -> None:
        self.responses = responses or {}
        self.failing = failing
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, query: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((query, kwargs))
        if any(query.startswith(prefix) for prefix in self.failing):
            raise RuntimeError("unsupported")
        for prefix, response in self.responses.items():
            if query.startswith(prefix):
                return response() if callable(response) else response
        return []


def test_rewrite_layout_ddl_changes_engine_partition_and_order() -> None:
    rewritten = platforms.rewrite_layout_ddl(
        SOURCE_DDL, "te_dst", "lineitem", platforms.LAYOUTS["sort_year"]["lineitem"]
    )
    assert rewritten.startswith("CREATE TABLE `te_dst`.`lineitem`\n(")
    assert rewritten.endswith(
        "ENGINE = MergeTree\nPARTITION BY toYear(l_shipdate)\nORDER BY (l_shipdate, l_orderkey, l_linenumber)"
    )
    assert "index_granularity" not in rewritten


def test_rewrite_layout_ddl_keeps_tables_without_a_layout() -> None:
    rewritten = platforms.rewrite_layout_ddl(SOURCE_DDL, "te_dst", "lineitem", None)
    assert rewritten == SOURCE_DDL.replace("CREATE TABLE te_src.lineitem", "CREATE TABLE `te_dst`.`lineitem`")


def test_packs_and_layouts_are_ported() -> None:
    assert set(platforms.LAYOUTS) == {
        "none",
        "sort",
        "sort_year",
        "sort_quarter",
        "sort_month",
        "none_year",
        "none_month",
    }
    assert platforms.PACKS["olap"]["join_algorithm"] == "grace_hash"
    assert platforms.PACKS["cand"]["join_algorithm"] == "hash"
    assert "join_algorithm" not in platforms.PACKS["pack"]
    assert platforms.format_setting("hash") == "'hash'"
    assert platforms.format_setting(8) == "8"


def test_tagged_client_sends_the_query_id_once() -> None:
    client = FakeClient()
    tagged = platforms.TaggedClient(client)
    tagged.pending_query_id = "qid-1"
    tagged.execute("SELECT 1")
    tagged.execute("SELECT 2")
    assert client.calls == [("SELECT 1", {"query_id": "qid-1"}), ("SELECT 2", {})]
    assert platforms.raw_connection(tagged) is client


def test_server_seam_tags_queries_for_the_query_log(tmp_path: Path) -> None:
    seam = server_seam(tmp_path)
    tagged = platforms.TaggedClient(FakeClient())
    query_id = seam.tag(tagged)
    assert query_id is not None and query_id.startswith("te-t1-")
    assert tagged.pending_query_id == query_id
    assert seam.tag(object()) is None


def handle_for(client: Any, spec: ArmSpec | None = None) -> ArmHandle:
    return ArmHandle(
        spec=spec or ArmSpec("N"), connection=platforms.TaggedClient(client), database="db", load_seconds=1.0
    )


def test_server_settle_waits_for_merges(tmp_path: Path) -> None:
    snapshots = iter([[(2, 40)], [(0, 12)], [(0, 12)], [(0, 12)], [(0, 12)]])
    client = FakeClient({"SELECT (SELECT count() FROM system.merges": lambda: next(snapshots)})
    record = server_seam(tmp_path).settle(handle_for(client))
    assert record.hook == "clickhouse_merge_settle"
    assert record.settled is True
    assert record.detail["active_parts"] == 12


def test_server_settle_timeout_marks_the_arm_unsettled(tmp_path: Path) -> None:
    client = FakeClient({"SELECT (SELECT count() FROM system.merges": [(3, 50)]})
    record = server_seam(tmp_path, settle_timeout_seconds=0.0).settle(handle_for(client))
    assert record.settled is False
    assert record.detail["timeout_seconds"] == 0.0


def test_local_engines_settle_as_a_no_op(tmp_path: Path) -> None:
    seam = platforms.DuckDBSeam(benchmark="tpch", scale_factor=1.0, work_dir=tmp_path, run_tag="t1")
    record = seam.settle(handle_for(FakeClient()))
    assert (record.hook, record.settled, record.waited_seconds) == ("noop", True, 0.0)


def test_server_cost_reads_the_query_log(tmp_path: Path) -> None:
    rows = [("q1", "QueryFinish", 100, 2000, 5_000_000, 12, 3, 40)]
    client = FakeClient({"SELECT query_id, type": rows})
    costs = server_seam(tmp_path).cost(handle_for(client), ["q1", "q2"])
    assert client.calls[0][0] == "SYSTEM FLUSH LOGS"
    assert costs == {
        "q1": {
            "type": "QueryFinish",
            "read_rows": 100,
            "read_bytes": 2000,
            "memory_usage": 5_000_000,
            "query_duration_ms": 12,
            "selected_parts": 3,
            "selected_marks": 40,
        }
    }
    assert server_seam(tmp_path).cost(handle_for(client), []) == {}


def test_server_drop_caches_reports_partial_support(tmp_path: Path) -> None:
    seam = server_seam(tmp_path)
    assert seam.drop_caches(handle_for(FakeClient()), FakeClient()) == "engine caches dropped"
    partial = seam.drop_caches(handle_for(FakeClient()), FakeClient(failing=("SYSTEM DROP QUERY CACHE",)))
    assert partial.startswith("partial: SYSTEM DROP QUERY CACHE")


def test_q21_note_on_the_tuned_session(tmp_path: Path) -> None:
    seam = server_seam(tmp_path)
    assert seam.notes(handle_for(FakeClient(), ArmSpec("T", load="tuned", session="tuned")))
    assert seam.notes(handle_for(FakeClient(), ArmSpec("S", load="tuned", session="notuning"))) == []


class FakeRunner:
    def __init__(self, oom_kill: list[int]) -> None:
        self.oom_kill = iter(oom_kill)
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        self.commands.append(command)
        if command[-1] == "/sys/fs/cgroup/memory.max":
            output = "5637144576\n"
        elif command[-1] == "/sys/fs/cgroup/memory.events":
            output = f"low 0\nhigh 0\nmax 4\noom 1\noom_kill {next(self.oom_kill)}\n"
        elif command[:2] == ["container", "--version"]:
            output = "container CLI version 1.1.0\n"
        elif command[1] == "stats":
            output = "NAME CPU% MEM\nch 10% 1.2GiB / 5.25GiB\n"
        else:
            output = "0.7.2\n"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")


def test_memory_evidence_records_limit_stats_and_oom_kills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeRunner([0, 0, 1])
    probe = platforms.ContainerProbe("mocker", "ch-test", runner=runner)
    seam = server_seam(tmp_path, probe=probe)
    monkeypatch.setattr(platforms.AdapterSeam, "prepare", lambda self: {"platform": "clickhouse-server"})

    environment = seam.prepare()
    assert environment["container"]["memory_max"] == "5637144576"
    assert environment["container"]["container_runtime_version"] == "container CLI version 1.1.0"
    round_sample = seam.round_evidence()
    assert "5.25GiB" in round_sample["stats"]["stdout"]
    evidence = seam.run_evidence()
    assert evidence["oom_kill_delta"] == 1
    assert evidence["oom_killed"] is True
    assert evidence["blockers"] == ["the container recorded 1 OOM kills during the run"]
    assert any("Apple container" in note for note in evidence["notes"])
    assert ["mocker", "exec", "ch-test", "cat", "/sys/fs/cgroup/memory.max"] in runner.commands


def test_missing_container_is_a_verdict_blocker(tmp_path: Path) -> None:
    evidence = server_seam(tmp_path).run_evidence()
    assert evidence["container"] is None
    assert evidence["blockers"]


def test_parse_memory_events() -> None:
    assert platforms.parse_memory_events("oom 2\noom_kill 1\nbroken line here\n") == {"oom": 2, "oom_kill": 1}


class FakeAdapter:
    def __init__(self, rows: list[tuple] | None, status: str = "SUCCESS", error: str | None = None) -> None:
        self.rows = rows
        self.status = status
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def execute_query(self, connection: Any, sql: str, query_id: str, **kwargs: Any) -> dict[str, Any]:
        from benchbox.platforms.base.result_capture import apply_materialized_result_validation

        self.calls.append({"sql": sql, "query_id": query_id, **kwargs})
        result = {"query_id": query_id, "status": self.status, "execution_time_seconds": 0.25, "error": self.error}
        apply_materialized_result_validation(result, query_id, self.rows)
        return result


def duckdb_handle(adapter: FakeAdapter) -> ArmHandle:
    return ArmHandle(spec=ArmSpec("N"), connection=object(), database="db", load_seconds=1.0, adapter=adapter)


def test_run_query_checksums_materialized_rows_independent_of_order(tmp_path: Path) -> None:
    from benchbox.core.results.result_digest import compute_result_digest

    seam = platforms.AdapterSeam(benchmark="tpch", scale_factor=1.0, work_dir=tmp_path, run_tag="t1")
    first = seam.run_query(duckdb_handle(FakeAdapter([(2, "b"), (1, "a")])), None, "1", "select", 10.0)
    second = seam.run_query(duckdb_handle(FakeAdapter([(1, "a"), (2, "b")])), None, "1", "select", 10.0)
    assert first.ok and first.rows == 2
    assert first.elapsed_seconds == 0.25
    assert first.checksum == second.checksum == compute_result_digest([(1, "a"), (2, "b")])


def test_run_query_skips_checksum_above_the_row_limit(tmp_path: Path) -> None:
    seam = platforms.AdapterSeam(
        benchmark="tpch", scale_factor=1.0, work_dir=tmp_path, run_tag="t1", max_checksum_rows=1
    )
    adapter = FakeAdapter([(1,), (2,)])
    outcome = seam.run_query(duckdb_handle(adapter), None, "4", "select", 10.0)
    assert outcome.ok and outcome.rows == 2 and outcome.checksum is None
    assert adapter.calls[0]["validate_row_count"] is False
    assert adapter.calls[0]["benchmark_type"] == "tpch"


def test_run_query_reports_failures_with_a_kind(tmp_path: Path) -> None:
    seam = platforms.AdapterSeam(benchmark="tpch", scale_factor=1.0, work_dir=tmp_path, run_tag="t1")
    outcome = seam.run_query(
        duckdb_handle(FakeAdapter(None, status="FAILED", error="Code: 159. Timeout exceeded")), None, "21", "x", 10.0
    )
    assert not outcome.ok
    assert outcome.error_kind == "timeout"
    assert outcome.rows is None


def test_duckdb_timeout_interrupts_the_connection(tmp_path: Path) -> None:
    class Connection:
        interrupted = False

        def interrupt(self) -> None:
            self.interrupted = True

    seam = platforms.DuckDBSeam(benchmark="tpch", scale_factor=1.0, work_dir=tmp_path, run_tag="t1")
    connection = Connection()
    timer, fired = seam.arm_timeout(connection, 0.01)
    assert timer is not None
    timer.join(1.0)
    assert fired.is_set() and connection.interrupted

    quiet = Connection()
    timer, fired = seam.arm_timeout(quiet, 30.0)
    assert timer is not None
    timer.cancel()
    time.sleep(0.01)
    assert not fired.is_set() and not quiet.interrupted


def test_duckdb_database_files_are_removed(tmp_path: Path) -> None:
    seam = platforms.DuckDBSeam(benchmark="tpch", scale_factor=1.0, work_dir=tmp_path, run_tag="t1")
    path = seam.database_path("te_t1_n")
    path.write_text("x")
    path.with_name(path.name + ".wal").write_text("x")
    seam.remove_database(None, "te_t1_n")
    assert not path.exists() and not path.with_name(path.name + ".wal").exists()
    assert seam.database_name(ArmSpec("N2")) == "te_t1_n2"


def test_git_state_reports_commit_and_dirty_tree(tmp_path: Path) -> None:
    def runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        output = "abc123\n" if command[1] == "rev-parse" else " M file.py\n"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    assert platforms.git_state(tmp_path, runner) == {"commit": "abc123", "dirty": True}


class SessionAdapter:
    def __init__(self) -> None:
        self.configured: list[str] = []

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        self.configured.append(benchmark_type)
        connection.execute("SET join_use_nulls = 1")


def test_server_refresh_session_reapplies_the_adapter_session(tmp_path: Path) -> None:
    adapter = SessionAdapter()
    client = FakeClient()
    handle = ArmHandle(
        spec=ArmSpec("N"),
        connection=platforms.TaggedClient(client),
        database="db",
        load_seconds=1.0,
        adapter=adapter,
    )
    server_seam(tmp_path).refresh_session(handle)
    assert adapter.configured == ["olap"]
    assert ("SET join_use_nulls = 1", {}) in client.calls


def test_server_refresh_session_reapplies_the_settings_pack(tmp_path: Path) -> None:
    adapter = SessionAdapter()
    client = FakeClient(
        {"SELECT name, value FROM system.settings WHERE name IN": [("max_bytes_in_join", "4294967296")]}
    )
    handle = ArmHandle(
        spec=ArmSpec("M", pack="mbj"),
        connection=platforms.TaggedClient(client),
        database="db",
        load_seconds=1.0,
        adapter=adapter,
    )
    server_seam(tmp_path).refresh_session(handle)
    assert ("SET max_bytes_in_join = 4294967296", {}) in client.calls


def test_other_seams_leave_the_session_alone(tmp_path: Path) -> None:
    seam = platforms.DuckDBSeam(benchmark="tpch", scale_factor=1.0, work_dir=tmp_path, run_tag="t1")
    assert seam.refresh_session(handle_for(FakeClient())) is None
