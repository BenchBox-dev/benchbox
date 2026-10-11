from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Any

import pytest

from scripts.tuning_evidence import harness
from scripts.tuning_evidence.harness import ArmHandle, ArmSpec, QueryOutcome, SettleRecord

pytestmark = [pytest.mark.unit, pytest.mark.fast]

QUERIES = {"1": "select 1", "2": "select 2", "3": "select 3"}
ARM_LOAD_READINGS = [1.0] * 4


class FakeSeam:
    def __init__(
        self,
        *,
        speed: Mapping[str, float] | None = None,
        rows: Mapping[tuple[str, str], int] | None = None,
        failing: Iterable[tuple[str, str]] = (),
        settled: bool = True,
    ) -> None:
        self.speed = dict(speed or {})
        self.rows = dict(rows or {})
        self.failing = set(failing)
        self.settled = settled
        self.events: list[tuple[str, ...]] = []
        self.counter = 0

    def prepare(self) -> dict[str, Any]:
        return {"host": {"node": "test"}}

    def load(self, arm: ArmSpec, loaded: Mapping[str, ArmHandle]) -> ArmHandle:
        self.events.append(("load", arm.name))
        return ArmHandle(spec=arm, connection=f"conn-{arm.name}", database=arm.name, load_seconds=2.0)

    def queries(self, handle: ArmHandle) -> dict[str, str]:
        return dict(QUERIES)

    def connect(self, handle: ArmHandle) -> Any:
        self.events.append(("connect", handle.spec.name))
        return f"stream-{handle.spec.name}"

    def release(self, handle: ArmHandle, connection: Any) -> None:
        self.events.append(("release", handle.spec.name))

    def settle(self, handle: ArmHandle) -> SettleRecord:
        self.events.append(("settle", handle.spec.name))
        return SettleRecord(hook="fake", settled=self.settled, waited_seconds=1.0)

    def refresh_session(self, handle: ArmHandle) -> None:
        self.events.append(("refresh", handle.spec.name))

    def run_query(
        self, handle: ArmHandle, connection: Any, query_id: str, sql: str, timeout_seconds: float
    ) -> QueryOutcome:
        arm = handle.spec.name
        self.events.append(("query", arm, query_id))
        self.counter += 1
        if (arm, query_id) in self.failing:
            return QueryOutcome(
                ok=False,
                elapsed_seconds=0.1,
                wall_seconds=0.1,
                error="Code: 241. MEMORY_LIMIT_EXCEEDED",
                error_kind="memory_limit",
            )
        jitter = 1.0 + 0.01 * (self.counter % 3)
        elapsed = int(query_id) * self.speed.get(arm, 1.0) * jitter
        rows = self.rows.get((arm, query_id), 10)
        return QueryOutcome(ok=True, elapsed_seconds=elapsed, wall_seconds=elapsed, rows=rows, checksum=f"sum-{rows}")

    def drop_caches(self, handle: ArmHandle, connection: Any) -> str:
        return "unsupported"

    def cost(self, handle: ArmHandle, engine_query_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        return {}

    def round_evidence(self) -> dict[str, Any]:
        return {"sample": True}

    def run_evidence(self) -> dict[str, Any]:
        return {"blockers": []}

    def close(self, handle: ArmHandle, *, keep: bool) -> None:
        self.events.append(("close", handle.spec.name))


def make_config(arms: Sequence[str] = ("N=notuning", "T=tuned"), **overrides: Any) -> harness.HarnessConfig:
    specs = tuple(harness.parse_arm(arm) for arm in arms)
    values: dict[str, Any] = {
        "platform": "fake",
        "benchmark": "tpch",
        "scale_factor": 0.01,
        "arms": specs,
        "baseline": specs[0].name,
        "rounds": 7,
        "resamples": 100,
        "retry_wait_seconds": 5.0,
    }
    values.update(overrides)
    return harness.HarnessConfig(**values)


def run(
    config: harness.HarnessConfig, seam: FakeSeam, loads: Iterable[float] | None = None
) -> tuple[dict[str, Any], list[float]]:
    readings = iter(loads) if loads is not None else None
    sleeps: list[float] = []
    payload = harness.Harness(
        config,
        seam,
        load_reader=(lambda: next(readings, 1.0)) if readings is not None else (lambda: 1.0),
        sleep=sleeps.append,
        log=lambda message: None,
    ).run()
    return payload, sleeps


def test_settle_runs_for_every_arm_before_any_timed_query() -> None:
    seam = FakeSeam()
    payload, _ = run(make_config(("N=notuning", "T=tuned", "S=load=tuned,session=notuning")), seam)

    first_query = next(index for index, event in enumerate(seam.events) if event[0] == "query")
    settles = [event[1] for event in seam.events[:first_query] if event[0] == "settle"]
    assert sorted(settles) == ["N", "N", "S", "S", "T", "T"]
    assert seam.events[:2] == [("load", "N"), ("settle", "N")]
    for name in ("N", "T", "S"):
        assert payload["arms"][name]["settle"]["settled"] is True
        assert payload["arms"][name]["pre_timing_settle"]["hook"] == "fake"
        assert payload["arms"][name]["load_plus_settle_seconds"] == pytest.approx(3.0)
    assert payload["status"] == "completed"


class SessionConnection:
    def __init__(self) -> None:
        self.settings: dict[str, str] = {}

    def execute(self, sql: str) -> list[tuple[str, str]]:
        name, separator, value = sql.removeprefix("SET ").partition(" = ")
        if separator:
            self.settings[name] = value
        return []


class SessionSeam(FakeSeam):
    def __init__(self) -> None:
        super().__init__()
        self.observed: list[dict[str, str]] = []

    def load(self, arm: ArmSpec, loaded: Mapping[str, ArmHandle]) -> ArmHandle:
        self.events.append(("load", arm.name))
        connection = SessionConnection()
        connection.execute("SET join_use_nulls = 1")
        return ArmHandle(spec=arm, connection=connection, database=arm.name, load_seconds=2.0)

    def refresh_session(self, handle: ArmHandle) -> None:
        self.events.append(("refresh", handle.spec.name))
        handle.connection.execute("SET join_use_nulls = 1")

    def run_query(
        self, handle: ArmHandle, connection: Any, query_id: str, sql: str, timeout_seconds: float
    ) -> QueryOutcome:
        self.observed.append(dict(connection.settings))
        return QueryOutcome(ok=True, elapsed_seconds=0.1, wall_seconds=0.1, rows=10, checksum="sum-10")


def test_power_rounds_reapply_the_session_after_a_silent_reset() -> None:
    seam = SessionSeam()
    cell = harness.Harness(
        make_config(("N=notuning", "T=notuning")),
        seam,
        load_reader=lambda: 1.0,
        sleep=lambda seconds: None,
        log=lambda message: None,
    )
    cell._load_arms()
    cell._prepare_queries()
    cell._power_round(harness.PHASE_WARMUP, 0, 0)
    assert seam.observed and all(snapshot == {"join_use_nulls": "1"} for snapshot in seam.observed)
    seam.observed.clear()
    for handle in cell.handles.values():
        handle.connection.settings.clear()
    cell._power_round(harness.PHASE_TIMED, 0, 0)
    assert seam.observed and all(snapshot == {"join_use_nulls": "1"} for snapshot in seam.observed)
    assert sorted(event[1] for event in seam.events if event[0] == "refresh") == ["N", "N", "T", "T"]


def test_unsettled_arm_blocks_the_verdict() -> None:
    payload, _ = run(make_config(calibration_arm=None), FakeSeam(settled=False))
    blocked = payload["results"]["verdicts"]["T"]["blocked_by"]
    assert any("not settled" in reason for reason in blocked)
    assert payload["results"]["verdicts"]["T"]["label"] == "none"


def test_round_is_retried_when_load_exceeds_the_ceiling() -> None:
    loads = ARM_LOAD_READINGS + [9.5, 1.0, 1.0] + [1.0] * 40
    payload, sleeps = run(make_config(), FakeSeam(), loads)

    timed = [record for record in payload["rounds"] if record["phase"] == "timed"]
    skipped = [record for record in timed if not record["accepted"]]
    assert len(skipped) == 1
    assert skipped[0]["ran"] is False
    assert skipped[0]["round"] == 0
    assert "above ceiling 8.0" in skipped[0]["reason"]
    assert sleeps == [5.0]
    assert sum(record["accepted"] for record in timed) == 7
    assert payload["results"]["accepted_rounds"] == 7


def test_load_after_a_round_is_traced_but_does_not_discard_it() -> None:
    loads = ARM_LOAD_READINGS + [1.0, 8.5, 1.0, 1.0] + [1.0] * 40
    payload, sleeps = run(make_config(), FakeSeam(), loads)

    timed = [record for record in payload["rounds"] if record["phase"] == "timed"]
    assert timed[0]["accepted"] is True
    assert timed[0]["load_after"] == 8.5
    assert sleeps == []
    assert len(timed) == 7


def test_run_aborts_after_three_retries_and_withholds_verdicts() -> None:
    payload, sleeps = run(make_config(calibration_arm=None), FakeSeam(), ARM_LOAD_READINGS + [20.0] * 10)

    assert payload["status"] == "aborted"
    assert "after 3 retries" in payload["abort_reason"]
    assert sleeps == [5.0, 5.0, 5.0]
    attempts = [record for record in payload["rounds"] if record["phase"] == "timed"]
    assert [record["attempt"] for record in attempts] == [0, 1, 2, 3]
    verdict = payload["results"]["verdicts"]["T"]
    assert verdict["label"] == "none"
    assert any("run aborted" in reason for reason in verdict["blocked_by"])


def test_row_count_mismatch_fails_the_comparison() -> None:
    seam = FakeSeam(rows={("T", "2"): 9})
    payload, _ = run(make_config(("N=notuning", "N2=notuning", "T=tuned"), calibration_arm="N2"), seam)

    correctness = payload["results"]["correctness"]
    assert correctness["mismatched_queries"] == {"T": ["2"]}
    assert all(item["expected_rows"] == 10 and item["rows"] == 9 for item in correctness["mismatches"])
    verdict = payload["results"]["verdicts"]["T"]
    assert verdict["label"] == "regression"
    assert any("answers differ" in finding for finding in verdict["findings"])


def test_unstable_answers_in_both_identical_arms_fail_calibration_once_per_query() -> None:
    class UnstableSeam(FakeSeam):
        def run_query(
            self, handle: ArmHandle, connection: Any, query_id: str, sql: str, timeout_seconds: float
        ) -> QueryOutcome:
            outcome = super().run_query(handle, connection, query_id, sql, timeout_seconds)
            if query_id == "2" and self.counter > 2 * len(QUERIES):
                return replace(outcome, rows=9, checksum="sum-9")
            return outcome

    payload, _ = run(make_config(("A=notuning", "B=notuning"), calibration_arm="B"), UnstableSeam())
    calibration = payload["results"]["calibration"]
    assert calibration["passed"] is False
    assert "answers differ between identical arms: ['2']" in calibration["reasons"]


def test_check_answers_compares_rows_and_checksums() -> None:
    def execution(arm: str, query: str, rows: int, checksum: str | None, ok: bool = True) -> dict[str, Any]:
        return {
            "arm": arm,
            "query": query,
            "ok": ok,
            "rows": rows,
            "checksum": checksum,
            "phase": "timed",
            "round": 0,
            "stream": None,
        }

    result = harness.check_answers(
        [
            execution("N", "1", 5, "a"),
            execution("T", "1", 5, "b"),
            execution("N", "2", 5, None),
            execution("T", "2", 5, "c"),
            execution("N", "3", 0, None, ok=False),
            execution("T", "3", 4, "d"),
        ],
        "N",
    )
    assert result["mismatched_queries"] == {"T": ["1"]}
    assert result["row_count_only"] == ["2"]
    assert result["unverified"] == [{"arm": "T", "query": "3"}]


def test_failures_are_named_and_never_dropped() -> None:
    seam = FakeSeam(failing={("T", "3")})
    payload, _ = run(make_config(("N=notuning", "N2=notuning", "T=tuned"), calibration_arm="N2"), seam)

    comparison = payload["results"]["comparisons"]["T"]
    assert list(comparison["candidate_failures"]) == ["3"]
    assert list(comparison["common"]) == ["1", "2"]
    assert comparison["g_penalized"] > comparison["g"]
    totals = payload["results"]["arm_totals"]["T"]
    assert totals["failed_queries"] == ["3"]
    assert totals["failure_kinds"] == ["memory_limit"]
    assert payload["results"]["verdicts"]["T"]["label"] == "regression"
    summary = harness.render_markdown(payload)
    assert "T ['3']" in summary


def test_aa_calibration_from_an_in_run_arm() -> None:
    payload, _ = run(make_config(("A=notuning", "B=notuning"), calibration_arm="B"), FakeSeam())
    calibration = payload["results"]["calibration"]
    assert calibration["source"] == "in-run arm B"
    assert calibration["passed"] is True
    assert set(calibration["noise"]) >= {"g_deviation", "per_query_max_deviation"}
    assert payload["results"]["verdicts"] == {}


def test_comparison_without_calibration_has_no_verdict() -> None:
    payload, _ = run(make_config(), FakeSeam(speed={"T": 0.5}))
    verdict = payload["results"]["verdicts"]["T"]
    assert verdict["label"] == "none"
    assert any("uncalibrated" in reason for reason in verdict["blocked_by"])
    assert payload["results"]["comparisons"]["T"]["g"] == pytest.approx(0.5, rel=0.05)


def test_external_calibration_must_match_the_cell(tmp_path: Any) -> None:
    prior = tmp_path / "aa.json"
    prior.write_text(
        json.dumps(
            {
                "config": {"platform": "fake", "benchmark": "tpch", "scale_factor": 1.0},
                "results": {"calibration": {"passed": True, "g": 1.0, "g_ci_low": 0.99, "g_ci_high": 1.01}},
            }
        )
    )
    payload, _ = run(make_config(calibration_file=str(prior)), FakeSeam(speed={"T": 0.5}))
    calibration = payload["results"]["calibration"]
    assert calibration["passed"] is False
    assert any("scale_factor" in reason for reason in calibration["reasons"])


def test_throughput_streams_use_their_own_connections() -> None:
    seam = FakeSeam(speed={"T": 0.8})
    payload, _ = run(make_config(streams=2), seam)

    assert payload["results"]["throughput"]["T"]["rounds"] == 7
    assert sum(event == ("connect", "T") for event in seam.events) == 14
    assert sum(event == ("release", "T") for event in seam.events) == 14
    streams = {execution["stream"] for execution in payload["executions"] if execution["phase"] == "throughput"}
    assert streams == {0, 1}


def test_cold_mode_records_the_cache_state_of_every_execution() -> None:
    class ColdSeam(FakeSeam):
        def drop_caches(self, handle: ArmHandle, connection: Any) -> str:
            self.events.append(("drop", handle.spec.name))
            return "engine caches dropped"

    seam = ColdSeam()
    payload, _ = run(make_config(cold=True, streams=2), seam)
    assert {execution["cache"] for execution in payload["executions"]} == {"engine caches dropped"}
    power_executions = sum(execution["phase"] != "throughput" for execution in payload["executions"])
    throughput_drops = 2 * 7
    assert sum(event[0] == "drop" for event in seam.events) == power_executions + throughput_drops
    assert payload["config"]["cache_policy"] == "cold"


def test_different_sql_across_arms_withholds_the_verdict() -> None:
    class DialectSeam(FakeSeam):
        def queries(self, handle: ArmHandle) -> dict[str, str]:
            queries = dict(QUERIES)
            if handle.spec.name == "T":
                queries["2"] = "select 2 + 0"
            return queries

    payload, _ = run(make_config(("N=notuning", "N2=notuning", "T=tuned"), calibration_arm="N2"), DialectSeam())
    assert payload["results"]["sql_differs"] == {"T": ["2"]}
    assert payload["results"]["verdicts"]["T"]["label"] == "none"


def test_throughput_failures_are_reported_and_fail_the_comparison() -> None:
    class StreamFailureSeam(FakeSeam):
        def run_query(
            self, handle: ArmHandle, connection: Any, query_id: str, sql: str, timeout_seconds: float
        ) -> QueryOutcome:
            if handle.spec.name == "T" and query_id == "3" and str(connection).startswith("stream"):
                return QueryOutcome(ok=False, elapsed_seconds=0.01, wall_seconds=0.01, error="x", error_kind="error")
            return super().run_query(handle, connection, query_id, sql, timeout_seconds)

    payload, _ = run(
        make_config(("N=notuning", "N2=notuning", "T=tuned"), calibration_arm="N2", streams=2), StreamFailureSeam()
    )
    throughput = payload["results"]["throughput"]["T"]
    assert throughput["failures"] == {"N": [], "T": ["3"]}
    assert throughput["comparable"] is False
    assert payload["results"]["arm_totals"]["T"]["throughput_failed_queries"] == ["3"]
    verdict = payload["results"]["verdicts"]["T"]
    assert verdict["label"] == "regression"
    assert any("concurrent streams" in finding for finding in verdict["findings"])
    assert "not comparable" in harness.render_markdown(payload)


def test_calibration_fails_when_the_run_did_not_complete() -> None:
    payload, _ = run(
        make_config(("A=notuning", "B=notuning"), calibration_arm="B", streams=2),
        FakeSeam(),
        ARM_LOAD_READINGS + [1.0, 1.0] * 7 + [30.0] * 10,
    )
    assert payload["status"] == "aborted"
    calibration = payload["results"]["calibration"]
    assert calibration["passed"] is False
    assert any("run aborted" in reason for reason in calibration["reasons"])


def test_external_calibration_must_come_from_a_completed_run(tmp_path: Any) -> None:
    first, _ = run(make_config(("A=notuning", "B=notuning"), calibration_arm="B"), FakeSeam())
    assert first["results"]["calibration"]["passed"] is True
    prior = tmp_path / "aa.json"
    prior.write_text(json.dumps(first, default=str))
    payload, _ = run(make_config(calibration_file=str(prior)), FakeSeam())
    assert payload["results"]["calibration"]["passed"] is True

    first["status"] = "error"
    prior.write_text(json.dumps(first, default=str))
    payload, _ = run(make_config(calibration_file=str(prior)), FakeSeam())
    assert payload["results"]["calibration"]["passed"] is False


def test_dropped_tuning_and_evidence_blockers_withhold_the_verdict() -> None:
    class DroppingSeam(FakeSeam):
        def load(self, arm: ArmSpec, loaded: Mapping[str, ArmHandle]) -> ArmHandle:
            handle = super().load(arm, loaded)
            if arm.name == "T":
                handle.details["tuning_dropped"] = [{"intent": "sort", "reason": "unsupported"}]
            return handle

        def run_evidence(self) -> dict[str, Any]:
            return {"blockers": ["the container recorded 1 OOM kills during the run"]}

    payload, _ = run(make_config(("N=notuning", "N2=notuning", "T=tuned"), calibration_arm="N2"), DroppingSeam())
    blocked = payload["results"]["verdicts"]["T"]["blocked_by"]
    assert any("dropped requested tuning" in reason for reason in blocked)
    assert any("OOM" in reason for reason in blocked)
    assert payload["results"]["calibration"]["passed"] is False


def test_layout_copy_arms_skip_the_load_settle_rule() -> None:
    class SlowCopySeam(FakeSeam):
        def load(self, arm: ArmSpec, loaded: Mapping[str, ArmHandle]) -> ArmHandle:
            handle = super().load(arm, loaded)
            if arm.layout:
                handle.load_seconds = 100.0
            return handle

    payload, _ = run(
        make_config(("N=notuning", "N2=notuning", "Y=layout=sort,from=N"), calibration_arm="N2"), SlowCopySeam()
    )
    verdict = payload["results"]["verdicts"]["Y"]
    assert verdict["label"] == "neutral"
    assert verdict["notes"]


def test_interrupt_still_records_the_run() -> None:
    class InterruptingSeam(FakeSeam):
        def run_query(
            self, handle: ArmHandle, connection: Any, query_id: str, sql: str, timeout_seconds: float
        ) -> QueryOutcome:
            raise KeyboardInterrupt

    seam = InterruptingSeam()
    payload, _ = run(make_config(), seam)
    assert payload["status"] == "interrupted"
    assert ("close", "N") in seam.events and ("close", "T") in seam.events


def test_verdict_records_its_scope() -> None:
    payload, _ = run(make_config(("N=notuning", "N2=notuning", "T=tuned"), calibration_arm="N2"), FakeSeam())
    scope = payload["results"]["verdicts"]["T"]["scope"]
    assert scope["scale_factor"] == 0.01
    assert scope["date"] == payload["finished_at"]
    assert set(scope["engine_version"]) == {"N", "T"}


def test_rounds_are_shuffled_and_interleaved_with_a_fixed_seed() -> None:
    first = FakeSeam()
    run(make_config(seed=5), first)
    second = FakeSeam()
    run(make_config(seed=5), second)
    queries = [event for event in first.events if event[0] == "query"]
    assert queries == [event for event in second.events if event[0] == "query"]
    first_round = queries[: 2 * len(QUERIES)]
    assert {event[1] for event in first_round[:2]} == {"N", "T"}


def test_load_error_is_recorded_and_loaded_arms_are_closed() -> None:
    class BrokenSeam(FakeSeam):
        def load(self, arm: ArmSpec, loaded: Mapping[str, ArmHandle]) -> ArmHandle:
            if arm.name == "T":
                raise RuntimeError("load failed")
            return super().load(arm, loaded)

    seam = BrokenSeam()
    payload, _ = run(make_config(), seam)
    assert payload["status"] == "error"
    assert "load failed" in payload["error"]
    assert ("close", "N") in seam.events


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("N=notuning", ArmSpec("N")),
        ("T=tuned", ArmSpec("T", load="tuned", session="tuned")),
        ("S=load=tuned,session=notuning", ArmSpec("S", load="tuned", session="notuning")),
        ("P=session=tuned,pack=olap", ArmSpec("P", load="notuning", session="tuned", pack="olap")),
        ("Y=layout=sort_year,from=N", ArmSpec("Y", layout="sort_year", layout_from="N")),
        ("F=custom/file.yaml", ArmSpec("F", load="custom/file.yaml", session="tuned")),
    ],
)
def test_parse_arm(text: str, expected: ArmSpec) -> None:
    assert harness.parse_arm(text) == expected


@pytest.mark.parametrize("text", ["notuning", "1bad=tuned", "X=session=maybe", "X=layout=sort", "X=colour=red"])
def test_parse_arm_rejects_bad_specs(text: str) -> None:
    with pytest.raises(ValueError):
        harness.parse_arm(text)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"rounds": 6}, "at least 7"),
        ({"thresholds": harness.stats.RuleThresholds(min_rounds=3), "rounds": 3}, "cannot be below 7"),
        ({"baseline": "X"}, "baseline"),
        ({"calibration_arm": "T"}, "same configuration"),
        ({"arms": (ArmSpec("N"),)}, "two arms"),
        ({"arms": (ArmSpec("N"), ArmSpec("Y", layout="sort", layout_from="Z"))}, "layout source"),
    ],
)
def test_validate_config(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        harness.validate_config(replace(make_config(), **overrides))


@pytest.mark.parametrize(
    ("message", "timed_out", "kind"),
    [
        (None, True, "timeout"),
        ("Code: 159. DB::Exception: Timeout exceeded: elapsed 300 seconds", False, "timeout"),
        ("Code: 241. DB::Exception: Memory limit (total) exceeded", False, "memory_limit"),
        ("MEMORY_LIMIT_EXCEEDED while executing", False, "memory_limit"),
        ("Code: 210. Connection refused (localhost:9000)", False, "connection_lost"),
        ("Binder Error: column not found", False, "error"),
        (None, False, None),
    ],
)
def test_classify_error(message: str | None, timed_out: bool, kind: str | None) -> None:
    assert harness.classify_error(message, timed_out=timed_out) == kind


def test_markdown_summary_lists_load_trace_and_calibration() -> None:
    payload, _ = run(make_config(("A=notuning", "B=notuning"), calibration_arm="B"), FakeSeam())
    summary = harness.render_markdown(payload)
    assert "## Calibration" in summary
    assert "## Host load per round" in summary
    assert "| A | notuning | notuning |" in summary
