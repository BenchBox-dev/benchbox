from __future__ import annotations

import ast
import itertools
import json
import shutil
import subprocess
import sys
from collections.abc import Iterator, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from benchbox.core.results import corpus_rule as rule
from benchbox.core.results.status import NON_CLEAN_VALIDATION_STATUSES, bundle_non_clean_reason

pytestmark = [pytest.mark.unit, pytest.mark.fast]

MODULE_PATH = Path(rule.__file__)
PROBE_PATH = Path(__file__).resolve().parents[3] / "fixtures" / "corpus_rule_isolated_probe.py"
BENCHMARKS = ("tpch", "tpcds", "tpcdi", "tpch_skew", "ssb", "unregistered_benchmark", None)
SCALES = (None, 0.5, 1.0, 5.0)
STREAMS = (None, 1, 99)
FLAGS = tuple(itertools.product((False, True), repeat=3))
PHASE_NAMES = (rule.PHASE_POWER, rule.PHASE_THROUGHPUT, rule.PHASE_REFRESH, rule.PHASE_OTHER_TIMED)
PHASE_SETS: tuple[frozenset[str] | None, ...] = (
    None,
    *(
        frozenset(name for name, present in zip(PHASE_NAMES, combination, strict=True) if present)
        for combination in itertools.product((False, True), repeat=len(PHASE_NAMES))
    ),
)
REGISTERED_TPC = ("tpch", "tpcds")


def all_facts() -> Iterator[rule.Facts]:
    for benchmark, scale, phases, streams, (gateway, defect, failed) in itertools.product(
        BENCHMARKS, SCALES, PHASE_SETS, STREAMS, FLAGS
    ):
        yield rule.Facts(benchmark, scale, phases, streams, gateway, defect, failed)


def expected_missing(facts: rule.Facts) -> tuple[str, ...]:
    if facts.phases is None:
        return (rule.MISSING_PHASES,)
    if not facts.phases & rule.TIMED_PHASES:
        return ()
    if facts.benchmark is None:
        return (rule.MISSING_BENCHMARK,)
    if facts.scale_factor is None:
        return (rule.MISSING_SCALE_FACTOR,)
    if facts.benchmark == "unregistered_benchmark":
        return (rule.MISSING_SCALE_REGISTRY,)
    if facts.benchmark == "tpcdi":
        return (rule.MISSING_TPCDI_RULE,)
    standard_tpc = facts.benchmark in REGISTERED_TPC and facts.scale_factor in (1.0,)
    needs_streams = rule.PHASE_POWER in facts.phases and rule.PHASE_THROUGHPUT in facts.phases
    if standard_tpc and needs_streams and rule.PHASE_REFRESH in facts.phases and facts.streams is None:
        return (rule.MISSING_STREAMS,)
    return ()


def expected_class(facts: rule.Facts) -> str:
    phases = facts.phases or frozenset()
    if not phases & rule.TIMED_PHASES:
        return rule.CLASS_NONE
    if facts.benchmark == "tpcdi":
        return rule.CLASS_UNDETERMINED
    tpc = facts.benchmark in REGISTERED_TPC
    scale = facts.scale_factor
    if tpc and scale < 1.0:
        return rule.CLASS_SUBSCALE
    if scale in (0.5, 5.0):
        return rule.CLASS_NONSTANDARD_SCALE
    power = rule.PHASE_POWER in phases
    throughput = rule.PHASE_THROUGHPUT in phases
    refresh = rule.PHASE_REFRESH in phases
    if not tpc:
        return rule.CLASS_NONE
    if power and throughput and refresh and facts.streams is not None and facts.streams >= 2:
        return rule.CLASS_FULL_SPEC
    if power and throughput:
        return rule.CLASS_PARTIAL
    if power and refresh:
        return rule.CLASS_PARTIAL
    if power:
        return rule.CLASS_POWER_ONLY
    if throughput:
        return rule.CLASS_THROUGHPUT_ONLY
    return rule.CLASS_PARTIAL


def expected_lanes(facts: rule.Facts, class_name: str) -> tuple[str, ...]:
    phases = facts.phases or frozenset()
    if class_name in (rule.CLASS_UNDETERMINED, rule.CLASS_NONSTANDARD_SCALE):
        return ()
    if not phases & rule.TIMED_PHASES:
        return ()
    if class_name == rule.CLASS_SUBSCALE:
        return (rule.LANE_SUBSCALE,)
    if class_name == rule.CLASS_FULL_SPEC:
        return (rule.LANE_COMBINED,)
    if class_name == rule.CLASS_THROUGHPUT_ONLY:
        return (rule.LANE_THROUGHPUT,)
    if class_name == rule.CLASS_POWER_ONLY:
        return (rule.LANE_POWER_ONLY,)
    if class_name == rule.CLASS_PARTIAL:
        if rule.PHASE_POWER in phases and rule.PHASE_THROUGHPUT in phases:
            return (rule.LANE_POWER_ONLY, rule.LANE_THROUGHPUT)
        return (rule.LANE_POWER_ONLY,) if rule.PHASE_POWER in phases else ()
    return (rule.LANE_OWN,)


def defects(table: Sequence[rule.Row]) -> list[str]:
    found: list[str] = []
    for facts in all_facts():
        outcome = rule.derive_outcome(facts, table=table)
        missing = expected_missing(facts)
        if outcome.missing_facts != missing and not (
            facts.benchmark == "tpcdi" and outcome.missing_facts == (rule.MISSING_TPCDI_RULE,)
        ):
            found.append(f"{facts}: missing {outcome.missing_facts} expected {missing}")
            continue
        if outcome.class_name == rule.CLASS_UNDETERMINED and not outcome.missing_facts:
            found.append(f"{facts}: no row decides it")
            continue
        if missing and outcome.class_name != rule.CLASS_UNDETERMINED:
            found.append(f"{facts}: decided despite missing {missing}")
            continue
        if not missing and outcome.class_name != expected_class(facts):
            found.append(f"{facts}: class {outcome.class_name} expected {expected_class(facts)}")
    return found


def test_every_combination_of_the_inputs_has_an_outcome() -> None:
    combinations = list(all_facts())
    assert len(combinations) == len(BENCHMARKS) * len(SCALES) * len(PHASE_SETS) * len(STREAMS) * len(FLAGS)
    assert defects(rule.RULE_TABLE) == []


def test_undetermined_only_when_a_needed_fact_is_missing() -> None:
    for facts in all_facts():
        outcome = rule.derive_outcome(facts)
        if outcome.class_name == rule.CLASS_UNDETERMINED:
            assert outcome.missing_facts, facts
            assert not outcome.ranked
            if (
                outcome.row != "tpcdi"
                or outcome.held_reason is not None
                or outcome.missing_facts != (rule.MISSING_TPCDI_RULE,)
            ):
                assert not outcome.public_admission and not outcome.maintainer_admission
            else:
                assert outcome.public_admission and not outcome.maintainer_admission
        else:
            assert outcome.missing_facts == (), facts


@pytest.mark.parametrize("row", [row.name for row in rule.RULE_TABLE])
def test_removing_a_table_row_fails_the_enumeration(row: str) -> None:
    reduced = tuple(candidate for candidate in rule.RULE_TABLE if candidate.name != row)

    assert len(reduced) == len(rule.RULE_TABLE) - 1
    assert defects(reduced), f"removing {row} left every combination decided"


def test_lanes_follow_the_class_for_every_decided_combination() -> None:
    for facts in all_facts():
        outcome = rule.derive_outcome(facts)
        if outcome.class_name == rule.CLASS_UNDETERMINED or outcome.held_reason is not None:
            continue
        assert outcome.lanes == expected_lanes(facts, outcome.class_name), facts


def test_gateway_known_defect_and_failed_validation_hold_the_result_out_of_every_lane() -> None:
    held = 0
    for facts in all_facts():
        outcome = rule.derive_outcome(facts)
        flags = (facts.gateway_routed, facts.known_defect, facts.validation_failed)
        timed = bool(facts.phases and facts.phases & rule.TIMED_PHASES)
        if any(flags) and timed and outcome.class_name != rule.CLASS_UNDETERMINED:
            held += 1
            assert not outcome.ranked
            assert outcome.lanes == ()
            assert outcome.score_source is None
            assert outcome.held_reason in rule.HELD_LABELS
    assert held


def test_no_timed_phase_is_not_a_result_on_every_path() -> None:
    for facts in all_facts():
        if facts.phases is not None and not facts.phases & rule.TIMED_PHASES:
            outcome = rule.derive_outcome(facts)
            assert outcome.held_reason == rule.HELD_NO_TIMED_PHASE
            assert not outcome.public_admission
            assert not outcome.maintainer_admission
            assert not outcome.ranked


def test_refresh_without_throughput_is_power_only_ranked_from_query_timings() -> None:
    facts = rule.Facts("tpch", 1.0, frozenset({rule.PHASE_POWER, rule.PHASE_REFRESH}), None)

    outcome = rule.derive_outcome(facts)

    assert outcome.class_name == rule.CLASS_PARTIAL
    assert outcome.lanes == (rule.LANE_POWER_ONLY,)
    assert outcome.score_source == rule.SCORE_POWER_QUERY_TIMINGS_ONLY
    assert outcome.public_admission and outcome.maintainer_admission


def test_full_spec_needs_the_stream_minimum_from_the_data_file() -> None:
    phases = frozenset(PHASE_NAMES[:3])

    below = rule.derive_outcome(rule.Facts("tpch", 10.0, phases, 2))
    at_minimum = rule.derive_outcome(rule.Facts("tpch", 10.0, phases, 3))
    tpcds_below = rule.derive_outcome(rule.Facts("tpcds", 1.0, phases, 2))
    tpcds_at = rule.derive_outcome(rule.Facts("tpcds", 1.0, phases, 4))

    assert below.class_name == rule.CLASS_PARTIAL
    assert below.lanes == (rule.LANE_POWER_ONLY, rule.LANE_THROUGHPUT)
    assert at_minimum.class_name == rule.CLASS_FULL_SPEC
    assert tpcds_below.class_name == rule.CLASS_PARTIAL
    assert tpcds_at.class_name == rule.CLASS_FULL_SPEC


def test_subscale_is_refused_on_the_public_path_and_never_nonstandard() -> None:
    for benchmark in REGISTERED_TPC:
        outcome = rule.derive_outcome(rule.Facts(benchmark, 0.1, frozenset({rule.PHASE_POWER}), None))
        assert outcome.class_name == rule.CLASS_SUBSCALE
        assert not outcome.public_admission
        assert outcome.maintainer_admission
        assert outcome.lanes == (rule.LANE_SUBSCALE,)


def test_nonstandard_scale_is_accepted_and_in_no_lane_for_every_benchmark() -> None:
    for benchmark in ("tpch", "tpcds", "ssb", "tpch_skew"):
        outcome = rule.derive_outcome(rule.Facts(benchmark, 5.0, frozenset({rule.PHASE_POWER}), None))
        assert outcome.class_name == rule.CLASS_NONSTANDARD_SCALE
        assert outcome.public_admission and outcome.maintainer_admission
        assert outcome.lanes == ()


def test_tpcdi_is_delegated_and_never_restated() -> None:
    facts = rule.Facts("tpcdi", 1.0, frozenset({rule.PHASE_POWER}), None)
    delegated = rule.Outcome(rule.CLASS_NONE, True, True, (rule.LANE_OWN,), rule.SCORE_OWN, ("delegate",))

    assert rule.derive_outcome(facts).missing_facts == (rule.MISSING_TPCDI_RULE,)
    assert rule.derive_outcome(facts, delegates={rule.DELEGATE_TPCDI: lambda _: delegated}).labels == ("delegate",)


def test_every_ranked_tpc_row_carries_the_derived_wording_and_the_class_in_words() -> None:
    ranked = 0
    for facts in all_facts():
        outcome = rule.derive_outcome(facts)
        if not outcome.ranked:
            continue
        if facts.benchmark in REGISTERED_TPC:
            ranked += 1
            label = "TPC-H" if facts.benchmark == "tpch" else "TPC-DS"
            assert outcome.labels[0] == (
                f"Derived from {label}. These results do not comply with the {label} Specification "
                f"and must not be compared with official {label} results."
            )
            assert outcome.labels[1] == rule.CLASS_WORDS[outcome.class_name]
        elif facts.benchmark == "tpch_skew":
            assert outcome.labels == (
                "Derived from TPC-H. These results do not comply with the TPC-H Specification "
                "and must not be compared with official TPC-H results.",
            )
        else:
            assert outcome.labels == ()
    assert ranked


def test_scale_registry_and_stream_minima_come_from_the_data_file() -> None:
    data = rule.load_data()

    assert data["stream_minimum"]["tpch"]["1.0"] == 2
    assert data["stream_minimum"]["tpcds"]["1.0"] == 4
    assert data["scales"]["ssb"]["standard"] == [0.01, 0.1, 1.0, 10.0]
    assert "tpch_skew" in data["tpc_derived_benchmarks"]


def result(
    *,
    benchmark: str = "tpch",
    scale: float = 1.0,
    power: bool = True,
    throughput_streams: int | None = None,
    refresh: bool = False,
    queries: bool = True,
    extra: dict | None = None,
) -> dict:
    phases: dict = {"data_generation": {"status": "COMPLETED"}, "data_loading": {"status": "COMPLETED"}}
    phases["power_test"] = {"status": "COMPLETED"} if power else {"status": "NOT_RUN"}
    if refresh:
        phases["power_test"]["refresh_functions"] = [
            {"function": "RF1", "status": "SUCCESS"},
            {"function": "RF2", "status": "SUCCESS"},
        ]
    phases["throughput_test"] = (
        {
            "status": "COMPLETED",
            "stream_results": [{"stream_id": n, "success": True} for n in range(throughput_streams)],
        }
        if throughput_streams
        else {"status": "NOT_RUN"}
    )
    if refresh and throughput_streams:
        phases["throughput_test"]["refresh_stream"] = {"status": "COMPLETED", "pairs": [{"status": "SUCCESS"}]}
    data = {
        "benchmark": {"id": benchmark, "scale_factor": scale},
        "phases": phases,
        "platform": {"name": "engine"},
        "run": {"streams": 7},
        "summary": {"validation": "passed", "queries": {"failed": 0}},
        "queries": [{"id": "1", "run_type": "measurement", "ms": 1.0}] if queries else [],
    }
    data.update(extra or {})
    return data


def test_facts_come_from_the_phases_and_stream_results_not_the_configured_stream_count() -> None:
    facts = rule.facts_from_result(result(power=True, throughput_streams=3, refresh=True))

    assert facts.phases == frozenset(PHASE_NAMES[:3])
    assert facts.streams == 3
    assert rule.derive_outcome(facts).class_name == rule.CLASS_FULL_SPEC


@pytest.mark.parametrize("stamp", [None, "official", "unofficial_nonstandard", "unofficial_subscale", "partial"])
@pytest.mark.parametrize("suppressed", [None, True, False])
def test_recorded_class_and_suppression_never_change_the_outcome(stamp: str | None, suppressed: bool | None) -> None:
    base = result()
    stamped = result()
    stamped["benchmark"]["compliance_class"] = stamp
    stamped["benchmark"]["conformance_class"] = stamp
    stamped["summary"]["tpc_metrics"] = {"suppressed": suppressed, "power_at_size": None if suppressed else 1.0}

    assert rule.facts_from_result(stamped) == rule.facts_from_result(base)
    assert rule.derive_outcome(rule.facts_from_result(stamped)) == rule.derive_outcome(rule.facts_from_result(base))


def test_the_module_never_reads_the_stamps_it_replaces() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")

    for name in ("compliance_class", "conformance_class", "suppressed"):
        assert name not in source


def test_load_only_result_is_not_a_result() -> None:
    facts = rule.facts_from_result(result(power=False, queries=False))

    outcome = rule.derive_outcome(facts)

    assert facts.phases == frozenset()
    assert outcome.held_reason == rule.HELD_NO_TIMED_PHASE


def test_result_without_a_phases_block_is_undetermined_not_guessed() -> None:
    data = result()
    del data["phases"]

    outcome = rule.derive_outcome(rule.facts_from_result(data))

    assert outcome.class_name == rule.CLASS_UNDETERMINED
    assert outcome.missing_facts == (rule.MISSING_PHASES,)


@pytest.mark.parametrize("version_key", ["version", "result_schema_version"])
def test_legacy_omitted_phases_with_measured_queries_are_other_timed(version_key: str) -> None:
    data = result()
    del data["phases"]
    data[version_key] = "2.2"
    data["queries"] = [{"id": "1", "ms": 10, "status": "SUCCESS"}]

    assert rule.phases_ran(data) == frozenset({rule.PHASE_OTHER_TIMED})
    assert rule.public_refusal_for_facts(rule.facts_from_result(data)) is None


@pytest.mark.parametrize(
    "row",
    [
        {"ms": 10, "run_type": "warmup"},
        {"ms": 10, "iter": 0},
        {"ms": 10, "status": "NOT_RUN"},
        {"ms": 10, "status": "FAILED"},
        {"ms": None, "status": "SUCCESS"},
        {"ms": float("nan"), "status": "SUCCESS"},
        {"ms": float("inf"), "status": "SUCCESS"},
        {"ms": 0, "status": "SUCCESS"},
    ],
)
def test_legacy_omitted_phases_without_measured_work_remain_unknown(row: dict) -> None:
    data = {"version": "2.2", "queries": [row]}
    assert rule.phases_ran(data) is None
    data["phases"] = {}
    assert rule.phases_ran(data) == frozenset()


@pytest.mark.parametrize("phases", [None, [], "invalid"])
def test_explicit_malformed_phases_are_not_replaced_by_query_evidence(phases) -> None:
    data = {"version": "2.2", "phases": phases, "queries": [{"ms": 1, "status": "SUCCESS"}]}
    assert rule.phases_ran(data) is None


def test_modern_omitted_phases_are_not_inferred_from_queries() -> None:
    data = {"result_schema_version": "2.6", "queries": [{"ms": 1, "status": "SUCCESS"}]}
    assert rule.phases_ran(data) is None


def test_gateway_known_defect_and_validation_come_from_the_file_and_sidecar(tmp_path: Path) -> None:
    data = result(extra={"platform": {"name": "engine", "gateway": {"name": "g", "routed": True}}})
    data["summary"]["validation"] = {"status": "FAILED"}
    path = tmp_path / "r.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    path.with_name("r.manifest.json").write_text(json.dumps({"known_defects": ["d"]}), encoding="utf-8")

    facts = rule.facts_from_path(path)

    assert facts.gateway_routed and facts.known_defect and facts.validation_failed


def test_non_clean_validation_statuses_match_the_project_vocabulary() -> None:
    assert rule.NON_CLEAN_VALIDATION_STATUSES == NON_CLEAN_VALIDATION_STATUSES


def test_no_rule_label_or_test_names_a_platform() -> None:
    forbidden = ("duckdb", "polars", "spark", "snowflake", "bigquery", "datafusion", "clickhouse", "sqlite")
    text = MODULE_PATH.read_text(encoding="utf-8").lower() + MODULE_PATH.with_name("corpus_rule_data.json").read_text(
        encoding="utf-8"
    )

    assert not [name for name in forbidden if name in text.lower()]


def test_module_imports_only_the_standard_library() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and not node.level
    }

    assert imported <= set(sys.stdlib_module_names)


def test_module_runs_with_no_project_packages_installed(tmp_path: Path) -> None:
    for name in ("corpus_rule.py", "corpus_rule_data.json", "query_status.py"):
        shutil.copy(MODULE_PATH.with_name(name), tmp_path / name)
    completed = subprocess.run(
        [sys.executable, str(PROBE_PATH), str(tmp_path)], capture_output=True, text=True, check=False, cwd=tmp_path
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "power_only ('power_only',) [] ['pydantic', 'yaml']"


def _tpcds_result(*, streams: int = 4, second: int | None = 4, maintenance: int = 2) -> dict:
    data = result(benchmark="tpcds", throughput_streams=streams)
    if second is not None:
        data["phases"]["throughput_test_2"] = {
            "status": "COMPLETED",
            "stream_results": [{"stream_id": n, "success": True} for n in range(second)],
        }
    for name in ("maintenance_test", "maintenance_test_2")[:maintenance]:
        data["phases"][name] = {"status": "COMPLETED", "operations": [{"status": "SUCCESS"}]}
    return data


def _validation_failed(data: dict) -> bool:
    return rule.facts_from_result(data).validation_failed


def _with(data: dict, mutate) -> dict:
    mutate(data)
    return data


def _fixture_variants() -> list[dict]:
    clean = result()
    clean["queries"] = [{"id": "1", "run_type": "measurement", "status": "SUCCESS"}]
    variants = [clean]
    variants.append(
        _with(json.loads(json.dumps(clean)), lambda d: d["summary"].update(queries={"total": 3, "passed": 1}))
    )
    variants.append(
        _with(
            json.loads(json.dumps(clean)),
            lambda d: d["summary"].update(queries={"total": 3, "passed": 2, "skipped": 1}),
        )
    )
    variants.append(
        _with(json.loads(json.dumps(clean)), lambda d: d["queries"].append({"id": "2", "status": "FAILED"}))
    )
    variants.append(
        _with(
            json.loads(json.dumps(clean)),
            lambda d: d["queries"].append({"id": "2", "run_type": "warmup", "status": "FAILED"}),
        )
    )
    variants.append(
        _with(
            json.loads(json.dumps(clean)),
            lambda d: d["queries"][0].update(row_count_validation={"status": "FAILED"}),
        )
    )
    variants.append(
        _with(
            json.loads(json.dumps(clean)),
            lambda d: d["queries"][0].update(row_count_validation={"status": "PASSED"}),
        )
    )
    for translation in ("fallback", "failed", "ok", {"status": "FALLBACK"}):
        variants.append(
            _with(json.loads(json.dumps(clean)), lambda d, t=translation: d.update(execution={"translation": t}))
        )
    for status in ("failed", "passed", "unknown", "PARTIAL", {"status": "error"}, None):
        variants.append(_with(json.loads(json.dumps(clean)), lambda d, v=status: d["summary"].update(validation=v)))
    return variants


@pytest.mark.parametrize("index", range(len(_fixture_variants())))
def test_validation_hold_matches_the_bundle_cleanliness_predicate_on_fixtures(index: int) -> None:
    data = _fixture_variants()[index]

    assert _validation_failed(data) == (bundle_non_clean_reason(data) is not None)


def test_validation_hold_matches_the_bundle_cleanliness_predicate_on_every_stored_result() -> None:
    root = Path(__file__).resolve().parents[4] / "results-data" / "bundles"
    paths = sorted(root.rglob("*.json"))
    checked = 0
    for path in paths:
        if path.name.endswith(".manifest.json"):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "phases" not in data:
            continue
        checked += 1
        assert _validation_failed(data) == (bundle_non_clean_reason(data) is not None), path
    assert checked


def test_validation_hold_covers_count_row_validation_and_translation_gaps() -> None:
    clean = _fixture_variants()
    assert not _validation_failed(clean[0])
    assert _validation_failed(clean[1])
    assert _validation_failed(clean[3])
    assert _validation_failed(clean[5])
    assert _validation_failed(clean[7])


def test_a_routed_gateway_holds_the_result_with_or_without_a_name() -> None:
    for gateway in ({"routed": True}, {"name": "g", "routed": True}, {"name": "g", "routed": 1}):
        data = result(extra={"platform": {"name": "engine", "gateway": gateway}})
        outcome = rule.derive_outcome(rule.facts_from_result(data))
        assert outcome.held_reason == rule.HELD_GATEWAY_ROUTED
    for gateway in ({"name": "g"}, {"name": "g", "routed": False}, {}, None):
        data = result(extra={"platform": {"name": "engine", "gateway": gateway}})
        assert not rule.facts_from_result(data).gateway_routed


def _broken(data: dict, mutate) -> dict:
    copy = json.loads(json.dumps(data))
    mutate(copy)
    return copy


def test_refresh_that_ran_and_failed_counts_as_run_but_incomplete_refresh_does_not() -> None:
    clean = result(throughput_streams=3, refresh=True)
    assert rule.PHASE_REFRESH in rule.phases_ran(clean)

    failed = [
        _broken(clean, lambda d: d["phases"]["power_test"]["refresh_functions"][1].update(status="FAILED")),
        _broken(clean, lambda d: d["phases"]["throughput_test"]["refresh_stream"].update(pairs=[{"status": "FAILED"}])),
        _broken(clean, lambda d: d["phases"]["throughput_test"]["refresh_stream"].update(status="FAILED")),
    ]
    for data in failed:
        assert rule.PHASE_REFRESH in rule.phases_ran(data)
        assert rule.derive_outcome(rule.facts_from_result(data)).lanes == (rule.LANE_COMBINED,)

    incomplete = [
        _broken(clean, lambda d: d["phases"]["power_test"]["refresh_functions"].pop()),
        _broken(clean, lambda d: d["phases"]["throughput_test"].pop("refresh_stream")),
    ]
    for data in incomplete:
        assert rule.PHASE_REFRESH not in rule.phases_ran(data)
        assert rule.derive_outcome(rule.facts_from_result(data)).class_name != rule.CLASS_FULL_SPEC


def test_tpcds_failed_maintenance_counts_as_run_and_missing_maintenance_does_not() -> None:
    assert rule.PHASE_REFRESH in rule.phases_ran(_tpcds_result())
    assert rule.PHASE_REFRESH not in rule.phases_ran(_tpcds_result(maintenance=1))
    failed = _tpcds_result()
    failed["phases"]["maintenance_test_2"]["operations"][0]["status"] = "FAILED"
    assert rule.PHASE_REFRESH in rule.phases_ran(failed)
    phase_failed = _tpcds_result()
    phase_failed["phases"]["maintenance_test"]["status"] = "FAILED"
    assert rule.PHASE_REFRESH in rule.phases_ran(phase_failed)
    not_run = _tpcds_result()
    not_run["phases"]["maintenance_test_2"]["status"] = "NOT_RUN"
    assert rule.PHASE_REFRESH not in rule.phases_ran(not_run)


def test_stream_count_counts_only_successful_streams() -> None:
    data = result(throughput_streams=4)
    data["phases"]["throughput_test"]["stream_results"][0]["success"] = False
    data["phases"]["throughput_test"]["stream_results"][1].pop("success")

    assert rule.executed_stream_count(data) == 2
    assert rule.derive_outcome(
        rule.facts_from_result(result(throughput_streams=4, refresh=True, scale=10.0))
    ).class_name
    failed = result(throughput_streams=3, refresh=True, scale=10.0)
    failed["phases"]["throughput_test"]["stream_results"][0]["success"] = False
    assert rule.derive_outcome(rule.facts_from_result(failed)).class_name != rule.CLASS_FULL_SPEC


def test_tpcds_stream_count_considers_both_throughput_tests() -> None:
    assert rule.executed_stream_count(_tpcds_result()) == 4
    assert rule.executed_stream_count(_tpcds_result(second=None)) == 0
    short = _tpcds_result()
    short["phases"]["throughput_test_2"]["stream_results"][0]["success"] = False
    assert rule.executed_stream_count(short) == 3
    assert rule.derive_outcome(rule.facts_from_result(short)).class_name != rule.CLASS_FULL_SPEC
    assert rule.derive_outcome(rule.facts_from_result(_tpcds_result())).class_name == rule.CLASS_FULL_SPEC


def evidenced(statuses: Sequence[str]) -> dict:
    rows = [
        {"id": str(index), "run_type": "measurement", "ms": 1.0, "row_count_validation": {"status": status}}
        for index, status in enumerate(statuses, start=1)
    ]
    return result(extra={"queries": rows})


@pytest.mark.parametrize(
    ("statuses", "labelled"),
    [
        (["PASSED", "PASSED"], False),
        (["PASSED", "SKIPPED"], False),
        (["PASSED", "UNVALIDATED"], True),
        (["UNVALIDATED", "UNVALIDATED"], True),
        (["SKIPPED"], True),
        ([], True),
    ],
)
def test_row_counts_not_checked_label_follows_per_query_evidence_and_never_the_rank(
    statuses: Sequence[str], labelled: bool
) -> None:
    data = evidenced(statuses)
    outcome = rule.derive_outcome(rule.facts_from_result(data))
    baseline = rule.derive_outcome(rule.Facts("tpch", 1.0, frozenset({rule.PHASE_POWER}), None))

    assert (rule.ROW_COUNTS_NOT_CHECKED_LABEL in outcome.labels) is labelled
    assert outcome.ranked
    assert (outcome.class_name, outcome.lanes, outcome.score_source, outcome.public_admission) == (
        baseline.class_name,
        baseline.lanes,
        baseline.score_source,
        baseline.public_admission,
    )


def test_label_comes_from_the_file_not_from_the_recorded_validation_status() -> None:
    unevidenced = evidenced(["SKIPPED"])
    unevidenced["summary"]["validation"] = "passed"
    no_reference = evidenced(["UNVALIDATED"])
    no_reference["summary"]["validation"] = "no_reference"

    for data in (unevidenced, no_reference):
        outcome = rule.derive_outcome(rule.facts_from_result(data))
        assert outcome.ranked
        assert rule.ROW_COUNTS_NOT_CHECKED_LABEL in outcome.labels


def test_a_held_result_is_not_labelled_and_a_failed_validation_is_still_refused() -> None:
    data = evidenced(["FAILED"])
    data["summary"]["validation"] = "failed"
    outcome = rule.derive_outcome(rule.facts_from_result(data))

    assert outcome.held_reason == rule.HELD_FAILED_VALIDATION
    assert not outcome.ranked
    assert rule.ROW_COUNTS_NOT_CHECKED_LABEL not in outcome.labels


def test_no_reference_status_is_not_a_failed_validation() -> None:
    assert "no_reference" not in rule.NON_CLEAN_VALIDATION_STATUSES
    assert "no_reference" not in NON_CLEAN_VALIDATION_STATUSES


def test_power_refresh_without_throughput_uses_partial_rule() -> None:
    outcome = rule.derive_outcome(rule.facts_from_result(result(refresh=True)))
    assert outcome.class_name == rule.CLASS_PARTIAL
    assert outcome.score_source == rule.SCORE_POWER_QUERY_TIMINGS_ONLY


def test_hold_precedes_missing_stream_evidence_and_refuses_public_failure() -> None:
    facts = rule.Facts("tpch", 1.0, frozenset(PHASE_NAMES[:3]), gateway_routed=True)
    assert rule.derive_outcome(facts).held_reason == rule.HELD_GATEWAY_ROUTED
    failed = rule.derive_outcome(rule.Facts("tpch", 1.0, frozenset({rule.PHASE_POWER}), validation_failed=True))
    assert not failed.public_admission


@pytest.mark.parametrize(
    ("scale_factor", "flags", "public", "maintainer", "refusal"),
    [
        (1.0, {"gateway_routed": True}, False, False, rule.HELD_GATEWAY_ROUTED),
        (0.01, {"gateway_routed": True}, False, False, rule.HELD_GATEWAY_ROUTED),
        (1.0, {"validation_failed": True}, False, False, rule.HELD_FAILED_VALIDATION),
        (0.01, {"validation_failed": True}, False, False, rule.HELD_FAILED_VALIDATION),
        (1.0, {"gateway_routed": True, "validation_failed": True}, False, False, rule.HELD_FAILED_VALIDATION),
        (0.01, {"gateway_routed": True, "validation_failed": True}, False, False, rule.HELD_FAILED_VALIDATION),
        (1.0, {"known_defect": True}, True, True, None),
        (0.01, {"known_defect": True}, False, True, rule.CLASS_SUBSCALE),
        (1.0, {"known_defect": True, "validation_failed": True}, False, False, rule.HELD_FAILED_VALIDATION),
        (0.01, {"known_defect": True, "validation_failed": True}, False, False, rule.HELD_FAILED_VALIDATION),
    ],
)
def test_hold_admission_matches_the_two_paths(
    scale_factor: float, flags: dict, public: bool, maintainer: bool, refusal: str | None
) -> None:
    facts = rule.Facts("tpch", scale_factor, frozenset({rule.PHASE_POWER}), **flags)
    outcome = rule.derive_outcome(facts)
    assert (outcome.public_admission, outcome.maintainer_admission) == (public, maintainer)
    assert not outcome.ranked
    assert rule.public_refusal_for_facts(facts) == refusal


def test_a_known_defect_changes_ranking_only_for_every_combination() -> None:
    checked = 0
    for facts in all_facts():
        if not facts.known_defect or facts.gateway_routed:
            continue
        for public_validation_refused in (False, True):
            held = replace(facts, public_validation_refused=public_validation_refused)
            clean = replace(held, known_defect=False, validation_failed=False)
            outcome = rule.derive_outcome(held)
            unheld = rule.derive_outcome(clean)
            if unheld.class_name == rule.CLASS_UNDETERMINED or unheld.held_reason is not None:
                continue
            checked += 1
            assert not outcome.ranked, held
            assert outcome.lanes == () and outcome.score_source is None, held
            if facts.validation_failed:
                assert not outcome.public_admission and not outcome.maintainer_admission, held
                assert rule.public_refusal_for_facts(held) is not None, held
                continue
            assert outcome.public_admission == unheld.public_admission, held
            assert outcome.maintainer_admission == unheld.maintainer_admission, held
            assert rule.public_refusal_for_facts(held) == rule.public_refusal_for_facts(clean), held
    assert checked


def test_failed_validation_is_the_public_refusal_whatever_else_refuses() -> None:
    checked = 0
    for facts in all_facts():
        if facts.phases is None:
            continue
        for public_validation_refused in (False, True):
            candidate = replace(facts, public_validation_refused=public_validation_refused)
            if candidate.validation_failed or candidate.public_validation_refused:
                checked += 1
                assert rule.public_refusal_for_facts(candidate) == rule.HELD_FAILED_VALIDATION, candidate
    assert checked


ITERATION_COUNTS = (None, 1, 2, 3, 4)


def test_single_iteration_is_a_label_only_for_every_combination() -> None:
    labelled = 0
    for facts in all_facts():
        for row_counts_checked, count in itertools.product((True, False), ITERATION_COUNTS):
            baseline = rule.derive_outcome(replace(facts, row_counts_checked=row_counts_checked))
            outcome = rule.derive_outcome(
                replace(facts, row_counts_checked=row_counts_checked, measured_iterations=count)
            )
            expected = (
                count is not None
                and count < 3
                and baseline.held_reason is None
                and baseline.class_name != rule.CLASS_UNDETERMINED
            )
            labels = (*baseline.labels, rule.SINGLE_ITERATION_LABEL) if expected else baseline.labels
            labelled += expected
            assert outcome == replace(baseline, labels=labels), (facts, count)
            assert rule.public_refusal_for_facts(replace(facts, measured_iterations=count)) == (
                rule.public_refusal_for_facts(facts)
            )
    assert labelled


def iteration_rows(*rows: dict) -> dict:
    return result(extra={"queries": [{"id": "1", "run_type": "measurement", "ms": 1.0, **row} for row in rows]})


@pytest.mark.parametrize(
    ("rows", "count"),
    [
        ((), None),
        (({},), 1),
        (({"iter": 1}, {"iter": 1}), 1),
        (({"iter": 1}, {"iter": 2}), 2),
        (({"iter": 1}, {"iter": 2}, {"iter": 3}), 3),
        (({"iteration": 1}, {"iteration": 2}, {"iteration": 3}), 3),
        (({"iter": 0, "run_type": "warmup"}, {"iter": 1}, {"iter": 2}), 2),
        (({"iter": 1}, {"iter": 2, "run_type": "warmup"}, {"iter": 3, "run_type": "warmup"}), 1),
        (({"iter": 1}, {"iter": 2}, {"iter": 3, "status": "SKIPPED"}), 2),
        (({"iter": 1}, {"iter": 2}, {"iter": 3, "ms": 0}), 2),
        (({"iter": 1}, {"iter": 2}, {"iter": 3, "ms": None}), 2),
        (({"iter": 1}, {"iter": 2}, {"iter": True}), 2),
        (({"iter": 1}, {"iter": 2}, {"iter": -3}), 2),
        (({"iter": 1}, {"iter": 2}, {"iter": "3"}), 2),
        (({"iter": 1, "stream": 1}, {"iter": 1, "stream": 2}, {"iter": 1, "stream": 3}), 1),
        (({"iter": 1}, {"iter": 2}, {"iter": 3}, {"iter": 4}), 4),
    ],
)
def test_measured_iterations_come_from_the_timed_query_rows(rows: tuple[dict, ...], count: int | None) -> None:
    data = iteration_rows(*rows) if rows else result(queries=False)
    facts = rule.facts_from_result(data)
    outcome = rule.derive_outcome(facts)
    baseline = rule.derive_outcome(replace(facts, measured_iterations=None))

    assert facts.measured_iterations == count
    assert (rule.SINGLE_ITERATION_LABEL in outcome.labels) is (count is not None and count < 3)
    assert outcome.ranked and baseline.ranked
    assert (outcome.class_name, outcome.lanes, outcome.score_source, outcome.public_admission) == (
        baseline.class_name,
        baseline.lanes,
        baseline.score_source,
        baseline.public_admission,
    )


def test_a_held_single_iteration_result_is_not_labelled() -> None:
    data = iteration_rows({"iter": 1})
    data["summary"]["validation"] = "failed"
    outcome = rule.derive_outcome(rule.facts_from_result(data))

    assert outcome.held_reason == rule.HELD_FAILED_VALIDATION
    assert rule.SINGLE_ITERATION_LABEL not in outcome.labels
