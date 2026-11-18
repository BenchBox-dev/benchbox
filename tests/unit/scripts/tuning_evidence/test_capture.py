from __future__ import annotations

from typing import Any

import pytest

from scripts.tuning_evidence import capture

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_strip_settings_removes_the_trailing_clause() -> None:
    assert capture.strip_settings("SELECT 1 FROM t SETTINGS a = 1, b = 'x';") == "SELECT 1 FROM t"
    assert capture.strip_settings("SELECT 1 FROM t;") == "SELECT 1 FROM t"


def test_join_algorithms_reads_plan_lines() -> None:
    lines = ["  Join", "    Type: INNER Strictness: ALL Algorithm: ConcurrentHashJoin", "Algorithm: GraceHashJoin"]
    assert capture.join_algorithms(lines) == ["ConcurrentHashJoin", "GraceHashJoin"]


def test_coerce_turns_integers_into_numbers() -> None:
    assert capture.coerce("8") == 8
    assert capture.coerce("hash") == "hash"


class FakeClient:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str, *args: Any, **kwargs: Any) -> list[Any]:
        self.statements.append(statement)
        if "grace_hash" in statement:
            raise RuntimeError("Code: 241 memory limit exceeded")
        if "FROM system.query_log" in statement:
            query_id = kwargs.get("query_id") or args[0]["ids"][0]
            return [(qid, "QueryFinish", 0, "", 1024, 5, 7, {}, {}, "q") for qid in args[0]["ids"]] or [query_id]
        return [(1,)] * 100


def test_sweep_records_pass_and_fail_per_candidate() -> None:
    client = FakeClient()
    results = capture.sweep(
        client,
        "db",
        "SELECT 1 SETTINGS join_algorithm = 'hash'",
        {"hash": {"join_algorithm": "hash"}, "grace": {"join_algorithm": "grace_hash"}},
        "t",
    )
    assert results["hash"]["ok"] is True and results["hash"]["rows"] == 100
    assert results["grace"]["ok"] is False and "241" in results["grace"]["error"]
    assert results["hash"]["peak_memory_bytes"] == 1024
