from __future__ import annotations

import hashlib
import os
import random
import re
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from benchbox.utils.clock import elapsed_seconds, mono_time, utc_now
from scripts.tuning_evidence import stats

HARNESS_VERSION = "1"
ARM_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
TUNING_MODES = ("notuning", "tuned")
MEMORY_LIMIT = re.compile(r"memory limit( \([^)]*\))? exceeded")
ARM_KEYS = {"load", "session", "layout", "from", "pack"}

MIN_ROUNDS = 7

PHASE_WARMUP = "warmup"
PHASE_TIMED = "timed"
PHASE_THROUGHPUT = "throughput"


class RoundAborted(RuntimeError):
    pass


@dataclass(frozen=True)
class ArmSpec:
    name: str
    load: str = "notuning"
    session: str = "notuning"
    layout: str | None = None
    layout_from: str | None = None
    pack: str | None = None

    def configuration(self) -> tuple[str, str, str | None, str | None, str | None]:
        return (self.load, self.session, self.layout, self.layout_from, self.pack)


@dataclass
class ArmHandle:
    spec: ArmSpec
    connection: Any
    database: str
    load_seconds: float
    details: dict[str, Any] = field(default_factory=dict)
    adapter: Any = None


@dataclass(frozen=True)
class SettleRecord:
    hook: str
    settled: bool
    waited_seconds: float
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QueryOutcome:
    ok: bool
    elapsed_seconds: float
    wall_seconds: float
    rows: int | None = None
    checksum: str | None = None
    error: str | None = None
    error_kind: str | None = None
    engine_query_id: str | None = None


@dataclass(frozen=True)
class HarnessConfig:
    platform: str
    benchmark: str
    scale_factor: float
    arms: tuple[ArmSpec, ...]
    baseline: str
    calibration_arm: str | None = None
    calibration_file: str | None = None
    rounds: int = 9
    warmup_rounds: int = 1
    seed: int = 42
    timeout_seconds: float = 300.0
    load_ceiling: float = 8.0
    round_retries: int = 3
    retry_wait_seconds: float = 60.0
    cold: bool = False
    streams: int = 0
    resamples: int = stats.DEFAULT_RESAMPLES
    confidence: float = stats.DEFAULT_CONFIDENCE
    queries: tuple[str, ...] | None = None
    keep_databases: bool = False
    thresholds: stats.RuleThresholds = field(default_factory=stats.RuleThresholds)


class Seam(Protocol):
    def prepare(self) -> dict[str, Any]: ...

    def load(self, arm: ArmSpec, loaded: Mapping[str, ArmHandle]) -> ArmHandle: ...

    def queries(self, handle: ArmHandle) -> dict[str, str]: ...

    def connect(self, handle: ArmHandle) -> Any: ...

    def release(self, handle: ArmHandle, connection: Any) -> None: ...

    def settle(self, handle: ArmHandle) -> SettleRecord: ...

    def refresh_session(self, handle: ArmHandle) -> None: ...

    def run_query(
        self, handle: ArmHandle, connection: Any, query_id: str, sql: str, timeout_seconds: float
    ) -> QueryOutcome: ...

    def drop_caches(self, handle: ArmHandle, connection: Any) -> str: ...

    def cost(self, handle: ArmHandle, engine_query_ids: Sequence[str]) -> dict[str, dict[str, Any]]: ...

    def round_evidence(self) -> dict[str, Any]: ...

    def run_evidence(self) -> dict[str, Any]: ...

    def close(self, handle: ArmHandle, *, keep: bool) -> None: ...


def parse_arm(text: str) -> ArmSpec:
    name, separator, spec = text.partition("=")
    if not separator or not ARM_NAME.match(name):
        raise ValueError(f"arm must look like NAME=SPEC with a short identifier name: {text!r}")
    fields: dict[str, str] = {}
    if "=" not in spec:
        fields["load"] = spec
    else:
        for item in spec.split(","):
            key, separator, value = item.partition("=")
            if not separator or key not in ARM_KEYS or not value:
                raise ValueError(f"arm {name}: expected KEY=VALUE with KEY in {sorted(ARM_KEYS)}, got {item!r}")
            fields[key] = value
    load = fields.get("load", "notuning")
    session = fields.get("session", "notuning" if load == "notuning" else "tuned")
    if session not in TUNING_MODES:
        raise ValueError(f"arm {name}: session must be one of {TUNING_MODES}, got {session!r}")
    if bool(fields.get("layout")) != bool(fields.get("from")):
        raise ValueError(f"arm {name}: layout and from must be given together")
    return ArmSpec(
        name=name,
        load=load,
        session=session,
        layout=fields.get("layout"),
        layout_from=fields.get("from"),
        pack=fields.get("pack"),
    )


def validate_config(config: HarnessConfig) -> None:
    names = [arm.name for arm in config.arms]
    if len(names) < 2:
        raise ValueError("at least two arms are needed")
    if len(set(names)) != len(names):
        raise ValueError(f"arm names must be unique: {names}")
    if config.baseline not in names:
        raise ValueError(f"baseline arm {config.baseline!r} is not one of {names}")
    by_name = {arm.name: arm for arm in config.arms}
    for position, arm in enumerate(config.arms):
        if arm.layout_from is None:
            continue
        earlier = {other.name for other in config.arms[:position] if other.layout is None}
        if arm.layout_from not in earlier:
            raise ValueError(f"arm {arm.name}: layout source {arm.layout_from!r} must be an earlier non-layout arm")
    if config.calibration_arm is not None:
        if config.calibration_arm not in names or config.calibration_arm == config.baseline:
            raise ValueError("the calibration arm must be a non-baseline arm")
        if by_name[config.calibration_arm].configuration() != by_name[config.baseline].configuration():
            raise ValueError("the calibration arm must have the same configuration as the baseline")
    if config.thresholds.min_rounds < MIN_ROUNDS:
        raise ValueError(f"the minimum rounds threshold cannot be below {MIN_ROUNDS}")
    if config.rounds < config.thresholds.min_rounds:
        raise ValueError(f"rounds must be at least {config.thresholds.min_rounds}, got {config.rounds}")
    if config.streams < 0 or config.round_retries < 0 or config.warmup_rounds < 0:
        raise ValueError("streams, retries and warm-up rounds cannot be negative")
    if config.timeout_seconds <= 0:
        raise ValueError("the per-query timeout must be positive")


def classify_error(message: str | None, *, timed_out: bool = False) -> str | None:
    if timed_out:
        return "timeout"
    if not message:
        return None
    lowered = message.lower()
    if "timeout_exceeded" in lowered or "timeout exceeded" in lowered:
        return "timeout"
    if "memory_limit_exceeded" in lowered or "out of memory" in lowered or MEMORY_LIMIT.search(lowered):
        return "memory_limit"
    if any(
        marker in lowered
        for marker in ("connection refused", "unexpected eof", "connection reset", "networkerror", "socket")
    ):
        return "connection_lost"
    return "error"


def check_answers(executions: Sequence[Mapping[str, Any]], baseline: str) -> dict[str, Any]:
    reference: dict[str, Mapping[str, Any]] = {}
    for execution in executions:
        if execution["arm"] == baseline and execution["ok"] and execution["query"] not in reference:
            reference[execution["query"]] = execution
    mismatches: list[dict[str, Any]] = []
    row_count_only: set[str] = set()
    unverified: set[tuple[str, str]] = set()
    for execution in executions:
        if not execution["ok"]:
            continue
        expected = reference.get(execution["query"])
        if expected is None:
            unverified.add((execution["arm"], execution["query"]))
            continue
        checksums_known = expected["checksum"] is not None and execution["checksum"] is not None
        if not checksums_known:
            row_count_only.add(execution["query"])
        if execution["rows"] != expected["rows"] or (checksums_known and execution["checksum"] != expected["checksum"]):
            mismatches.append(
                {
                    "arm": execution["arm"],
                    "query": execution["query"],
                    "phase": execution["phase"],
                    "round": execution["round"],
                    "stream": execution["stream"],
                    "expected_rows": expected["rows"],
                    "rows": execution["rows"],
                    "expected_checksum": expected["checksum"],
                    "checksum": execution["checksum"],
                }
            )
    return {
        "reference_arm": baseline,
        "mismatches": mismatches,
        "mismatched_queries": {
            arm: sorted({item["query"] for item in mismatches if item["arm"] == arm}, key=stats.query_sort_key)
            for arm in sorted({item["arm"] for item in mismatches})
        },
        "row_count_only": sorted(row_count_only, key=stats.query_sort_key),
        "unverified": [{"arm": arm, "query": query} for arm, query in sorted(unverified)],
    }


def accepted_rounds(payload: Mapping[str, Any], phase: str = PHASE_TIMED) -> list[int]:
    return sorted({record["round"] for record in payload["rounds"] if record["phase"] == phase and record["accepted"]})


def round_samples(
    executions: Sequence[Mapping[str, Any]], arm: str, queries: Sequence[str], rounds: Sequence[int]
) -> dict[str, list[float | None]]:
    cells = {
        (execution["round"], execution["query"]): execution
        for execution in executions
        if execution["phase"] == PHASE_TIMED and execution["accepted"] and execution["arm"] == arm
    }
    samples: dict[str, list[float | None]] = {}
    for query in queries:
        series: list[float | None] = []
        for index in rounds:
            execution = cells.get((index, query))
            series.append(execution["elapsed_seconds"] if execution and execution["ok"] else None)
        samples[query] = series
    return samples


def default_load_reader() -> float:
    return os.getloadavg()[0]


class Harness:
    def __init__(
        self,
        config: HarnessConfig,
        seam: Seam,
        *,
        load_reader: Callable[[], float] = default_load_reader,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = print,
    ) -> None:
        validate_config(config)
        self.config = config
        self.seam = seam
        self.load_reader = load_reader
        self.sleep = sleep
        self.log = log
        self.rng = random.Random(config.seed)
        self.handles: dict[str, ArmHandle] = {}
        self.sql: dict[str, dict[str, str]] = {}
        self.query_ids: list[str] = []
        self.payload: dict[str, Any] = {
            "harness": {"name": "tuning_evidence", "version": HARNESS_VERSION},
            "status": "running",
            "started_at": utc_now().isoformat(),
            "config": _config_payload(config),
            "environment": {},
            "arms": {},
            "rounds": [],
            "executions": [],
            "throughput_rounds": [],
            "diagnostics": {},
            "evidence": {},
        }

    def run(self) -> dict[str, Any]:
        try:
            self.payload["environment"] = self.seam.prepare()
            self._load_arms()
            self._prepare_queries()
            for index in range(self.config.warmup_rounds):
                self._record_round(PHASE_WARMUP, index, 0, self._power_round(PHASE_WARMUP, index, 0), True)
            for index in range(self.config.rounds):
                self._timed(PHASE_TIMED, index, self._power_round)
            for index in range(self.config.rounds if self.config.streams else 0):
                self._timed(PHASE_THROUGHPUT, index, self._throughput_round)
            self._collect_diagnostics()
            self.payload["status"] = "completed"
        except RoundAborted as exc:
            self.payload["status"] = "aborted"
            self.payload["abort_reason"] = str(exc)
        except KeyboardInterrupt:
            self.payload["status"] = "interrupted"
            self.payload["error"] = "interrupted by the operator"
        except Exception as exc:
            self.payload["status"] = "error"
            self.payload["error"] = f"{type(exc).__name__}: {exc}"
            self.payload["traceback"] = traceback.format_exc()
        finally:
            self._finish()
        return self.payload

    def _load_arms(self) -> None:
        for arm in self.config.arms:
            self.log(f"loading arm {arm.name}")
            load_before = self.load_reader()
            handle = self.seam.load(arm, self.handles)
            self.handles[arm.name] = handle
            settle = self.seam.settle(handle)
            self.payload["arms"][arm.name] = {
                "spec": asdict(arm),
                "database": handle.database,
                "load_seconds": handle.load_seconds,
                "settle": asdict(settle),
                "load_plus_settle_seconds": handle.load_seconds + settle.waited_seconds,
                "host_load_before": load_before,
                "host_load_after": self.load_reader(),
                **handle.details,
            }
        for name, handle in self.handles.items():
            self.payload["arms"][name]["pre_timing_settle"] = asdict(self.seam.settle(handle))

    def _prepare_queries(self) -> None:
        sets = {name: self.seam.queries(handle) for name, handle in self.handles.items()}
        baseline_ids = set(sets[self.config.baseline])
        for name, queries in sets.items():
            if set(queries) != baseline_ids:
                raise ValueError(f"arm {name} has a different query set from {self.config.baseline}")
        selected = sorted(baseline_ids, key=stats.query_sort_key)
        if self.config.queries:
            missing = [query for query in self.config.queries if query not in baseline_ids]
            if missing:
                raise ValueError(f"unknown queries {missing}; available: {selected}")
            selected = [query for query in selected if query in self.config.queries]
        self.query_ids = selected
        self.sql = {name: {query: queries[query] for query in selected} for name, queries in sets.items()}
        self.payload["queries"] = selected
        self.payload["query_sql_sha256"] = {
            name: {query: hashlib.sha256(sql.encode()).hexdigest()[:16] for query, sql in queries.items()}
            for name, queries in self.sql.items()
        }

    def _execute(
        self,
        arm: str,
        connection: Any,
        query: str,
        phase: str,
        index: int,
        attempt: int,
        stream: int | None,
        cache: str | None = None,
    ) -> dict[str, Any]:
        handle = self.handles[arm]
        if cache is None:
            cache = self.seam.drop_caches(handle, connection) if self.config.cold else "warm"
        outcome = self.seam.run_query(handle, connection, query, self.sql[arm][query], self.config.timeout_seconds)
        return {
            "phase": phase,
            "round": index,
            "attempt": attempt,
            "arm": arm,
            "query": query,
            "stream": stream,
            "cache": cache,
            "accepted": False,
            **asdict(outcome),
        }

    def _power_round(self, phase: str, index: int, attempt: int) -> tuple[list[dict[str, Any]], dict[str, float]]:
        executions = []
        for handle in self.handles.values():
            self.seam.refresh_session(handle)
        order = list(self.query_ids)
        self.rng.shuffle(order)
        for query in order:
            arms = list(self.handles)
            self.rng.shuffle(arms)
            for arm in arms:
                executions.append(self._execute(arm, self.handles[arm].connection, query, phase, index, attempt, None))
        return executions, {}

    def _throughput_round(self, phase: str, index: int, attempt: int) -> tuple[list[dict[str, Any]], dict[str, float]]:
        executions: list[dict[str, Any]] = []
        totals: dict[str, float] = {}
        arms = list(self.handles)
        self.rng.shuffle(arms)
        for arm in arms:
            handle = self.handles[arm]
            connections: list[Any] = []
            orders = []
            for _ in range(self.config.streams):
                order = list(self.query_ids)
                self.rng.shuffle(order)
                orders.append(order)
            try:
                for _ in range(self.config.streams):
                    connections.append(self.seam.connect(handle))
                cache = self.seam.drop_caches(handle, handle.connection) if self.config.cold else "warm"

                def stream(position: int, arm: str = arm, cache: str = cache) -> list[dict[str, Any]]:
                    return [
                        self._execute(arm, connections[position], query, phase, index, attempt, position, cache)
                        for query in orders[position]
                    ]

                started = mono_time()
                with ThreadPoolExecutor(max_workers=self.config.streams) as pool:
                    results = list(pool.map(stream, range(self.config.streams)))
                totals[arm] = elapsed_seconds(started)
            finally:
                for connection in connections:
                    self.seam.release(handle, connection)
            for result in results:
                executions.extend(result)
        return executions, totals

    def _timed(
        self, phase: str, index: int, run_round: Callable[..., tuple[list[dict[str, Any]], dict[str, float]]]
    ) -> None:
        ceiling = self.config.load_ceiling
        for attempt in range(self.config.round_retries + 1):
            before = self.load_reader()
            if before > ceiling:
                self._record_skip(phase, index, attempt, before, f"host load {before:.2f} above ceiling {ceiling}")
                self._wait_or_abort(phase, index, attempt)
                continue
            started = mono_time()
            executions, totals = run_round(phase, index, attempt)
            seconds = elapsed_seconds(started)
            after = self.load_reader()
            self._record_round(
                phase,
                index,
                attempt,
                (executions, totals),
                True,
                load_before=before,
                load_after=after,
                seconds=seconds,
            )
            self.log(f"{phase} round {index} accepted (load {before:.2f} before, {after:.2f} after)")
            return
        raise RoundAborted(
            f"{phase} round {index}: host load stayed above {ceiling} after {self.config.round_retries} retries"
        )

    def _wait_or_abort(self, phase: str, index: int, attempt: int) -> None:
        if attempt < self.config.round_retries:
            self.log(f"{phase} round {index} attempt {attempt} skipped; waiting {self.config.retry_wait_seconds}s")
            self.sleep(self.config.retry_wait_seconds)

    def _record_skip(self, phase: str, index: int, attempt: int, load: float, reason: str) -> None:
        self.payload["rounds"].append(
            {
                "phase": phase,
                "round": index,
                "attempt": attempt,
                "accepted": False,
                "ran": False,
                "load_before": load,
                "load_after": None,
                "seconds": 0.0,
                "reason": reason,
                "evidence": {},
            }
        )

    def _record_round(
        self,
        phase: str,
        index: int,
        attempt: int,
        result: tuple[list[dict[str, Any]], dict[str, float]],
        accepted: bool,
        *,
        load_before: float | None = None,
        load_after: float | None = None,
        seconds: float | None = None,
        reason: str | None = None,
    ) -> None:
        executions, totals = result
        for execution in executions:
            execution["accepted"] = accepted
        self.payload["executions"].extend(executions)
        if totals:
            self.payload["throughput_rounds"].append(
                {"round": index, "attempt": attempt, "accepted": accepted, "arm_seconds": totals}
            )
        self.payload["rounds"].append(
            {
                "phase": phase,
                "round": index,
                "attempt": attempt,
                "accepted": accepted,
                "ran": True,
                "load_before": load_before,
                "load_after": load_after,
                "seconds": seconds,
                "reason": reason,
                "evidence": self.seam.round_evidence(),
            }
        )

    def _collect_diagnostics(self) -> None:
        for arm, handle in self.handles.items():
            ids = [
                execution["engine_query_id"]
                for execution in self.payload["executions"]
                if execution["arm"] == arm and execution["phase"] == PHASE_TIMED and execution["engine_query_id"]
            ]
            try:
                costs = self.seam.cost(handle, ids)
            except Exception as exc:
                self.payload["diagnostics"][arm] = {"error": f"{type(exc).__name__}: {exc}"}
                continue
            if not costs:
                continue
            for execution in self.payload["executions"]:
                if execution["arm"] == arm and execution["engine_query_id"] in costs:
                    execution["cost"] = costs[execution["engine_query_id"]]
            self.payload["diagnostics"][arm] = summarize_costs(self.payload["executions"], arm, self.query_ids)

    def _finish(self) -> None:
        try:
            self.payload["evidence"] = self.seam.run_evidence()
        except Exception as exc:
            self.payload["evidence"] = {
                "error": f"{type(exc).__name__}: {exc}",
                "blockers": ["evidence capture failed"],
            }
        for handle in self.handles.values():
            try:
                self.seam.close(handle, keep=self.config.keep_databases)
            except Exception as exc:
                self.payload.setdefault("cleanup_errors", []).append(f"{handle.spec.name}: {exc}")
        self.payload["finished_at"] = utc_now().isoformat()
        try:
            self.payload["results"] = build_results(self.payload, self.config)
        except Exception as exc:
            self.payload["results"] = {"error": f"{type(exc).__name__}: {exc}"}


def summarize_costs(executions: Sequence[Mapping[str, Any]], arm: str, queries: Sequence[str]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for query in queries:
        costs = [
            execution["cost"]
            for execution in executions
            if execution["arm"] == arm
            and execution["query"] == query
            and execution["phase"] == PHASE_TIMED
            and execution["accepted"]
            and execution.get("cost")
        ]
        if not costs:
            continue
        read_rows = [cost["read_rows"] for cost in costs if cost.get("read_rows") is not None]
        memory = [cost["memory_usage"] for cost in costs if cost.get("memory_usage") is not None]
        summary[query] = {
            "samples": len(costs),
            "read_rows_median": stats.median(read_rows) if read_rows else None,
            "peak_memory_bytes_max": max(memory) if memory else None,
        }
    return summary


def _config_payload(config: HarnessConfig) -> dict[str, Any]:
    payload = asdict(config)
    payload["arms"] = [asdict(arm) for arm in config.arms]
    payload["cache_policy"] = "cold" if config.cold else "warm"
    return payload


def _unsettled(payload: Mapping[str, Any], arms: Sequence[str]) -> list[str]:
    unsettled = []
    for arm in arms:
        record = payload["arms"].get(arm)
        if record is None:
            unsettled.append(arm)
            continue
        settles = [record.get("settle"), record.get("pre_timing_settle")]
        if any(settle is None or not settle["settled"] for settle in settles):
            unsettled.append(arm)
    return unsettled


def _failed_queries(payload: Mapping[str, Any], arm: str, phase: str) -> list[str]:
    return sorted(
        {
            execution["query"]
            for execution in payload["executions"]
            if execution["arm"] == arm and execution["phase"] == phase and execution["accepted"] and not execution["ok"]
        },
        key=stats.query_sort_key,
    )


def _arm_totals(payload: Mapping[str, Any], arm: str) -> dict[str, Any]:
    timed = [
        execution
        for execution in payload["executions"]
        if execution["arm"] == arm and execution["phase"] == PHASE_TIMED and execution["accepted"]
    ]
    failed = [execution for execution in timed if not execution["ok"]]
    return {
        "executions": len(timed),
        "total_elapsed_seconds": sum(execution["elapsed_seconds"] for execution in timed),
        "failed_executions": len(failed),
        "failed_queries": _failed_queries(payload, arm, PHASE_TIMED),
        "throughput_failed_queries": _failed_queries(payload, arm, PHASE_THROUGHPUT),
        "failure_kinds": sorted({execution["error_kind"] for execution in failed if execution["error_kind"]}),
    }


def _external_calibration(path: str, config: HarnessConfig, queries: Sequence[str]) -> dict[str, Any]:
    import json

    with open(path, encoding="utf-8") as handle:
        prior = json.load(handle)
    prior_config = prior.get("config", {})
    reasons = []
    if prior.get("status") != "completed":
        reasons.append(f"the calibration run status is {prior.get('status')!r}, not 'completed'")
    for key in ("platform", "benchmark", "scale_factor"):
        if prior_config.get(key) != getattr(config, key):
            reasons.append(f"calibration {key} {prior_config.get(key)!r} does not match {getattr(config, key)!r}")
    if prior_config.get("cache_policy") != ("cold" if config.cold else "warm"):
        reasons.append("the calibration run used a different cache policy")
    if prior_config.get("thresholds") != asdict(config.thresholds):
        reasons.append("the calibration run used different rule thresholds")
    if list(prior.get("queries", [])) != list(queries):
        reasons.append("the calibration run used a different query set")
    calibration = (prior.get("results") or {}).get("calibration") or {}
    if not calibration.get("passed"):
        reasons.append("the calibration run did not pass")
    return {
        "source": path,
        "passed": not reasons,
        "g": calibration.get("g"),
        "g_ci_low": calibration.get("g_ci_low"),
        "g_ci_high": calibration.get("g_ci_high"),
        "reasons": reasons + list(calibration.get("reasons", [])),
    }


def _run_blockers(payload: Mapping[str, Any], config: HarnessConfig) -> list[str]:
    blockers = []
    if payload["status"] != "completed":
        blockers.append(f"run {payload['status']}: {payload.get('abort_reason') or payload.get('error')}")
    blockers.extend((payload.get("evidence") or {}).get("blockers", []))
    differing = _differing_sql(payload, config.baseline)
    if differing:
        blockers.append(f"arms ran different SQL text: {differing}")
    return blockers


def _arm_blockers(payload: Mapping[str, Any], arms: Sequence[str]) -> list[str]:
    blockers = []
    unsettled = _unsettled(payload, arms)
    if unsettled:
        blockers.append(f"arms not settled: {unsettled}")
    for arm in arms:
        dropped = payload["arms"].get(arm, {}).get("tuning_dropped") or []
        if dropped:
            blockers.append(f"arm {arm} dropped requested tuning: {dropped}")
    return blockers


def _throughput_results(payload: Mapping[str, Any], config: HarnessConfig, arms: Sequence[str]) -> dict[str, Any]:
    rounds = [record for record in payload["throughput_rounds"] if record["accepted"]]
    if not rounds:
        return {}
    results = {}
    base_failures = _failed_queries(payload, config.baseline, PHASE_THROUGHPUT)
    for arm in arms:
        if arm == config.baseline:
            continue
        failures = {config.baseline: base_failures, arm: _failed_queries(payload, arm, PHASE_THROUGHPUT)}
        comparison = stats.compare_totals(
            config.baseline,
            [record["arm_seconds"][config.baseline] for record in rounds],
            arm,
            [record["arm_seconds"][arm] for record in rounds],
            seed=config.seed,
            resamples=config.resamples,
            confidence=config.confidence,
        )
        results[arm] = {
            **asdict(comparison),
            "failures": failures,
            "comparable": not any(failures.values()),
        }
    return results


def build_results(payload: Mapping[str, Any], config: HarnessConfig) -> dict[str, Any]:
    queries = payload.get("queries", [])
    arm_names = [arm.name for arm in config.arms if arm.name in payload["arms"]]
    correctness = check_answers(payload["executions"], config.baseline)
    mismatched = correctness["mismatched_queries"]
    rounds = accepted_rounds(payload)
    samples = {arm: round_samples(payload["executions"], arm, queries, rounds) for arm in arm_names}
    results: dict[str, Any] = {
        "thresholds": asdict(config.thresholds),
        "correctness": correctness,
        "arm_totals": {arm: _arm_totals(payload, arm) for arm in arm_names},
        "accepted_rounds": len(rounds),
        "sql_differs": _differing_sql(payload, config.baseline),
        "comparisons": {},
        "calibration": None,
        "verdicts": {},
        "slow_queries": {},
        "throughput": _throughput_results(payload, config, arm_names),
    }
    if config.baseline not in samples or not queries:
        return results

    comparisons = {
        arm: stats.compare(
            config.baseline,
            samples[config.baseline],
            arm,
            samples[arm],
            seed=config.seed,
            resamples=config.resamples,
            confidence=config.confidence,
            penalty_seconds=config.timeout_seconds,
        )
        for arm in arm_names
        if arm != config.baseline
    }
    results["comparisons"] = {arm: _comparison_payload(comparison) for arm, comparison in comparisons.items()}

    run_blockers = _run_blockers(payload, config)
    calibration: dict[str, Any] | None = None
    if config.calibration_arm and config.calibration_arm in comparisons:
        pair = [config.baseline, config.calibration_arm]
        checked = stats.calibration_check(
            comparisons[config.calibration_arm],
            config.thresholds,
            answer_mismatches=sorted(
                set(mismatched.get(config.calibration_arm, [])) | set(mismatched.get(config.baseline, [])),
                key=stats.query_sort_key,
            ),
            unsettled_arms=_unsettled(payload, pair),
            blockers=run_blockers
            + [blocker for blocker in _arm_blockers(payload, pair) if "not settled" not in blocker],
        )
        calibration = {"source": f"in-run arm {config.calibration_arm}", **asdict(checked)}
        calibration["noise"] = _noise(comparisons[config.calibration_arm])
    elif config.calibration_file:
        calibration = _external_calibration(config.calibration_file, config, queries)
    results["calibration"] = calibration

    calibrated = bool(calibration and calibration["passed"])
    base_failures = set(_failed_queries(payload, config.baseline, PHASE_TIMED)) | set(
        _failed_queries(payload, config.baseline, PHASE_THROUGHPUT)
    )
    for arm, comparison in comparisons.items():
        if arm == config.calibration_arm:
            continue
        layout_copy = payload["arms"][arm]["spec"].get("layout") is not None
        outcome = stats.verdict(
            comparison,
            config.thresholds,
            calibrated=calibrated,
            answer_mismatches=sorted(
                set(mismatched.get(arm, [])) | set(mismatched.get(config.baseline, [])), key=stats.query_sort_key
            ),
            base_load_settle_seconds=payload["arms"][config.baseline]["load_plus_settle_seconds"],
            candidate_load_settle_seconds=None if layout_copy else payload["arms"][arm]["load_plus_settle_seconds"],
            blockers=run_blockers + _arm_blockers(payload, [config.baseline, arm]),
            extra_failures=[
                query
                for query in _failed_queries(payload, arm, PHASE_THROUGHPUT)
                if query not in base_failures and query not in comparison.candidate_failures
            ],
        )
        record = {**asdict(outcome), "scope": _scope(payload, config, arm)}
        if layout_copy:
            record["notes"] = ["load plus settle rule not applied: this arm was copied from another arm, not loaded"]
        results["verdicts"][arm] = record
        results["slow_queries"][arm] = [
            {**asdict(query), "cause": "unproven"} for query in stats.slow_queries(comparison, config.thresholds)
        ]
    return results


def _differing_sql(payload: Mapping[str, Any], baseline: str) -> dict[str, list[str]]:
    hashes = payload.get("query_sql_sha256") or {}
    reference = hashes.get(baseline, {})
    differing = {
        arm: sorted(
            (query for query, digest in digests.items() if reference.get(query) != digest), key=stats.query_sort_key
        )
        for arm, digests in hashes.items()
    }
    return {arm: queries for arm, queries in differing.items() if queries}


def _scope(payload: Mapping[str, Any], config: HarnessConfig, arm: str) -> dict[str, Any]:
    evidence = payload.get("evidence") or {}
    return {
        "platform": config.platform,
        "benchmark": config.benchmark,
        "scale_factor": config.scale_factor,
        "engine_version": {name: payload["arms"][name].get("engine_version") for name in (config.baseline, arm)},
        "memory_max": evidence.get("memory_max"),
        "thresholds": asdict(config.thresholds),
        "date": payload.get("finished_at"),
        "notes": list(evidence.get("notes", [])),
    }


def _comparison_payload(comparison: stats.Comparison) -> dict[str, Any]:
    payload = asdict(comparison)
    payload["per_query"] = [asdict(query) for query in comparison.per_query]
    return payload


def _noise(comparison: stats.Comparison) -> dict[str, Any]:
    if not comparison.per_query:
        return {}
    deviations = [abs(query.ratio - 1.0) for query in comparison.per_query]
    half_widths = [(query.ci_high - query.ci_low) / 2.0 for query in comparison.per_query]
    worst = max(comparison.per_query, key=lambda query: abs(query.ratio - 1.0))
    return {
        "g_deviation": abs((comparison.g or 1.0) - 1.0),
        "per_query_median_deviation": stats.median(deviations),
        "per_query_max_deviation": max(deviations),
        "per_query_max_deviation_query": worst.query,
        "per_query_median_ci_half_width": stats.median(half_widths),
    }


def _number(value: Any, digits: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}"
    return str(value)


def _table(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines.extend("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows)
    return lines


def _ratio(numerator: Any, denominator: Any) -> str:
    if numerator is None or not denominator:
        return "-"
    return f"{numerator / denominator:.2f}"


def render_markdown(payload: Mapping[str, Any]) -> str:
    config = payload["config"]
    environment = payload.get("environment") or {}
    results = payload.get("results") or {}
    host = environment.get("host") or {}
    git = environment.get("git") or {}
    accepted = results.get("accepted_rounds", 0)
    thresholds = results.get("thresholds") or config.get("thresholds") or {}
    lines = [
        f"# Tuning evidence: {config['platform']} {config['benchmark']} SF{config['scale_factor']}",
        "",
        f"- Status: {payload['status']}"
        + (f" ({payload.get('abort_reason') or payload.get('error')})" if payload["status"] != "completed" else ""),
        f"- Started {payload.get('started_at')}, finished {payload.get('finished_at')}",
        f"- Harness version {payload['harness']['version']}, commit {git.get('commit', '-')}"
        + (" (dirty tree)" if git.get("dirty") else ""),
        f"- Host: {host.get('node', '-')}, {host.get('platform', '-')}, {host.get('cpu_count', '-')} CPUs, "
        f"{_number(host.get('memory_total_gb'), 1)} GB",
        f"- Cache policy: {config['cache_policy']} (cold-cache support: {environment.get('cold_cache_support', '-')})",
        f"- Rounds: {config['rounds']} requested, {accepted} accepted, warm-up {config['warmup_rounds']}, "
        f"seed {config['seed']}, {config['resamples']} bootstrap resamples, {config['confidence']:.0%} CI",
        f"- Per-query timeout {config['timeout_seconds']}s, host load ceiling {config['load_ceiling']} "
        f"with {config['round_retries']} retries, throughput streams {config['streams'] or 'off'}",
        f"- Data: {environment.get('data_dir', '-')}; {environment.get('query_source', '')}",
        "- Rule thresholds: " + ", ".join(f"{key} {value}" for key, value in thresholds.items()),
        "",
        "## Arms",
        "",
    ]
    arm_rows = []
    for name, arm in payload.get("arms", {}).items():
        spec = arm["spec"]
        settle = arm["settle"]
        pre = arm.get("pre_timing_settle") or {}
        arm_rows.append(
            [
                name,
                spec["load"] if not spec.get("layout") else f"layout {spec['layout']} from {spec['layout_from']}",
                spec["session"] + (f" + pack {spec['pack']}" if spec.get("pack") else ""),
                (arm.get("tuning") or {}).get("template") or (arm.get("tuning") or {}).get("mode", "-"),
                arm.get("engine_version", "-"),
                _number(arm["load_seconds"], 1),
                f"{_number(settle['waited_seconds'], 1)} ({settle['hook']})",
                "yes" if settle["settled"] and pre.get("settled") else "NO",
                _number(arm["load_plus_settle_seconds"], 1),
                "; ".join(arm.get("notes") or []) or "-",
            ]
        )
    lines += _table(
        ["Arm", "Load", "Session", "Template", "Engine", "Load s", "Settle s", "Settled", "Load+settle s", "Notes"],
        arm_rows,
    )
    waited = [name for name, arm in payload.get("arms", {}).items() if arm.get("load_includes_adapter_settle")]
    if waited:
        lines += ["", f"Load time for {', '.join(waited)} includes the adapter's own post-load merge wait."]

    evidence = payload.get("evidence") or {}
    if evidence.get("container") or evidence.get("blockers"):
        lines += ["", "## Container memory evidence", ""]
        if evidence.get("container"):
            cli_version = (evidence.get("cli_version") or {}).get("stdout", "-")
            lines += [
                f"- Container {evidence['container']} via {evidence.get('cli')} {cli_version}; "
                f"runtime {evidence.get('container_runtime_version', '-')}",
                f"- Guest memory.max: {evidence.get('memory_max', '-')}",
                f"- OOM kills during the run: {_number(evidence.get('oom_kill_delta'), 0)}",
            ]
        lines += [f"- {note}" for note in evidence.get("notes", [])]
        lines += [f"- Blocker: {blocker}" for blocker in evidence.get("blockers", [])]

    calibration = results.get("calibration")
    lines += ["", "## Calibration", ""]
    if calibration is None:
        lines.append("No A/A calibration in this run or supplied; every verdict is withheld.")
    else:
        lines.append(
            f"- Source: {calibration['source']}; passed: {'yes' if calibration['passed'] else 'NO'}; "
            f"G {_number(calibration.get('g'), 4)} [{_number(calibration.get('g_ci_low'), 4)}, "
            f"{_number(calibration.get('g_ci_high'), 4)}]"
        )
        lines += [f"- {reason}" for reason in calibration.get("reasons", [])]
        noise = calibration.get("noise") or {}
        if noise:
            lines.append(
                f"- Noise: |G-1| {_number(noise['g_deviation'], 4)}; per-query |ratio-1| median "
                f"{_number(noise['per_query_median_deviation'], 3)}, max {_number(noise['per_query_max_deviation'], 3)} "
                f"(Q{noise['per_query_max_deviation_query']}); median per-query CI half-width "
                f"{_number(noise['per_query_median_ci_half_width'], 3)}"
            )

    totals = results.get("arm_totals") or {}
    diagnostics = payload.get("diagnostics") or {}
    for arm, comparison in (results.get("comparisons") or {}).items():
        base = comparison["base"]
        lines += [
            "",
            f"## {arm} vs {base}",
            "",
            f"- G over {len(comparison['common'])} queries passing in both arms: {_number(comparison['g'], 4)} "
            f"[{_number(comparison['g_ci_low'], 4)}, {_number(comparison['g_ci_high'], 4)}]",
            f"- G with each failed query at {comparison['penalty_seconds']}s: {_number(comparison['g_penalized'], 4)}",
            f"- Failures: {base} {list(comparison['base_failures']) or 'none'}; "
            f"{arm} {list(comparison['candidate_failures']) or 'none'}",
            f"- Sum of medians (common set): {base} {_number(comparison['base_total_median_seconds'])}s, "
            f"{arm} {_number(comparison['candidate_total_median_seconds'])}s",
            f"- Total elapsed over accepted rounds: {base} {_number(totals.get(base, {}).get('total_elapsed_seconds'))}s, "
            f"{arm} {_number(totals.get(arm, {}).get('total_elapsed_seconds'))}s",
            f"- Answer mismatches: {(results.get('correctness') or {}).get('mismatched_queries', {}).get(arm) or 'none'}",
        ]
        verdict_record = (results.get("verdicts") or {}).get(arm)
        if verdict_record:
            lines.append(f"- Verdict: {verdict_record['label']}")
            scope = verdict_record.get("scope") or {}
            if scope:
                lines.append(
                    f"  - scope: SF{scope['scale_factor']}, engine {scope['engine_version']}, "
                    f"memory.max {scope.get('memory_max') or '-'}, {scope.get('date')}"
                )
            lines += [f"  - finding: {finding}" for finding in verdict_record["findings"]]
            lines += [f"  - note: {note}" for note in verdict_record.get("notes", [])]
            lines += [f"  - withheld because: {reason}" for reason in verdict_record["blocked_by"]]
        slow = (results.get("slow_queries") or {}).get(arm)
        if slow:
            lines.append(
                "- Named slow queries: "
                + ", ".join(f"Q{item['query']} {item['ratio']:.2f}x ({item['cause']})" for item in slow)
            )
        rows = []
        base_costs = diagnostics.get(base, {})
        arm_costs = diagnostics.get(arm, {})
        for query in comparison["per_query"]:
            base_cost = base_costs.get(query["query"], {})
            arm_cost = arm_costs.get(query["query"], {})
            rows.append(
                [
                    query["query"],
                    _number(query["base_median"] * 1000, 1),
                    _number(query["candidate_median"] * 1000, 1),
                    f"{query['ratio']:.3f}",
                    f"[{query['ci_low']:.3f}, {query['ci_high']:.3f}]",
                    _ratio(arm_cost.get("read_rows_median"), base_cost.get("read_rows_median")),
                    _ratio(arm_cost.get("peak_memory_bytes_max"), base_cost.get("peak_memory_bytes_max")),
                ]
            )
        lines += [""] + _table(
            ["Q", f"{base} median ms", f"{arm} median ms", "Ratio", "95% CI", "Rows read ratio", "Peak memory ratio"],
            rows,
        )

    for arm, throughput in (results.get("throughput") or {}).items():
        lines += [
            "",
            f"## Throughput {arm} vs {throughput['base']}",
            "",
            f"- {config['streams']} streams, {throughput['rounds']} rounds: median {throughput['base']} "
            f"{_number(throughput['base_median_seconds'])}s, {arm} {_number(throughput['candidate_median_seconds'])}s, "
            f"ratio {_number(throughput['ratio'], 4)} [{_number(throughput['ci_low'], 4)}, {_number(throughput['ci_high'], 4)}]",
            f"- Failures under streams: {throughput.get('failures')}"
            + ("" if throughput.get("comparable", True) else "; the ratio is not comparable because queries failed"),
        ]

    correctness = results.get("correctness") or {}
    lines += ["", "## Correctness", ""]
    lines.append(
        f"- Reference arm {correctness.get('reference_arm')}; mismatching executions: "
        f"{len(correctness.get('mismatches', []))}"
    )
    if correctness.get("row_count_only"):
        lines.append(f"- Compared by row count only (result too large to checksum): {correctness['row_count_only']}")
    if correctness.get("unverified"):
        lines.append(f"- No reference answer (the reference arm failed): {correctness['unverified']}")
    mismatch_rows = [
        [item["arm"], item["query"], item["phase"], item["round"], item["expected_rows"], item["rows"]]
        for item in correctness.get("mismatches", [])[:50]
    ]
    if mismatch_rows:
        lines += [""] + _table(["Arm", "Q", "Phase", "Round", "Expected rows", "Rows"], mismatch_rows)

    lines += ["", "## Host load per round", ""]
    lines += _table(
        ["Phase", "Round", "Attempt", "Load before", "Load after", "Accepted", "Reason"],
        [
            [
                record["phase"],
                record["round"],
                record["attempt"],
                _number(record["load_before"], 2),
                _number(record["load_after"], 2),
                "yes" if record["accepted"] else "no",
                record.get("reason") or "",
            ]
            for record in payload.get("rounds", [])
        ],
    )
    return "\n".join(lines) + "\n"
