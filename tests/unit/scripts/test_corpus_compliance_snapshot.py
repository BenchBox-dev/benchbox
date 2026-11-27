from __future__ import annotations

import copy

import corpus_compliance_snapshot as script
import pytest

from benchbox.core.results import corpus_rule, corpus_score

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

BUNDLES = script.CHECKOUT_ROOT / script.BUNDLES_DIR
SNAPSHOT = script.CHECKOUT_ROOT / script.SNAPSHOT_PATH


@pytest.fixture(scope="module")
def recorded() -> script.Snapshot:
    return script.load_snapshot(SNAPSHOT)


@pytest.fixture(scope="module")
def computed() -> script.Snapshot:
    return script.compute_snapshot(BUNDLES)


def _changed_files(problems: list[str]) -> set[str]:
    return {line.split(":", 1)[0].strip() for line in problems if line.startswith("  ")}


def test_committed_corpus_matches_the_snapshot(recorded, computed) -> None:
    assert script.compare(recorded, computed) == []


def test_every_stored_result_has_identity_today_and_rule_outcomes(recorded) -> None:
    assert recorded
    for entry in recorded.values():
        assert set(entry) == {"identity", "today", "rule"}
        if "skipped" in entry["today"]:
            continue
        assert {"class", "cohort", "rank", "total", "score"} <= set(entry["today"])
        assert {"class", "lanes", "score_source", "labels", "public_admission", "maintainer_admission"} <= set(
            entry["rule"]
        )
        assert entry["rule"]["class"] != corpus_rule.CLASS_UNDETERMINED or entry["rule"]["missing_facts"]


def test_the_published_outcome_is_the_rule_outcome_for_every_stored_result(recorded) -> None:
    for name, entry in recorded.items():
        today, rule = entry["today"], entry["rule"]
        if "skipped" in today:
            continue
        assert today["class"] == rule["class"], name
        if rule["lanes"]:
            assert rule["ranks"], name
            assert today["rank"] in {rank["rank"] for rank in rule["ranks"].values()}, name
        assert not script.differences({name: entry}), name


def test_a_drifted_today_outcome_fails_and_names_the_result(recorded, computed) -> None:
    drifted = copy.deepcopy(computed)
    name = next(name for name, entry in drifted.items() if entry["today"].get("rank") is not None)
    drifted[name]["today"]["rank"] += 1

    problems = script.compare(recorded, drifted)

    assert "today outcome" in problems[0]
    assert script.REGENERATE_COMMAND in problems[0]
    assert _changed_files(problems) == {name}


def test_a_drifted_rule_outcome_fails_and_names_the_result(recorded) -> None:
    drifted = copy.deepcopy(recorded)
    name = next(name for name, entry in drifted.items() if entry["rule"].get("lanes"))
    drifted[name]["rule"]["lanes"] = []

    problems = script.compare(recorded, drifted)

    assert "rule outcome" in problems[0]
    assert _changed_files(problems) == {name}


def test_changing_the_rule_table_moves_the_snapshot(recorded, monkeypatch) -> None:
    table = tuple(row for row in corpus_rule.RULE_TABLE if row.name != "tpc_power_only")
    original = corpus_rule.derive_outcome
    monkeypatch.setattr(corpus_rule, "derive_outcome", lambda facts, **kwargs: original(facts, table=table, **kwargs))

    changed = _changed_files(script.compare(recorded, script.compute_snapshot(BUNDLES)))

    assert changed
    assert all("tpc" in name for name in changed)


def test_added_and_removed_bundles_fail_with_the_regenerate_command(recorded) -> None:
    current = dict(recorded)
    removed = next(iter(current))
    del current[removed]
    current["new/bundle.json"] = {"identity": None, "today": {}, "rule": {}}

    text = "\n".join(script.compare(recorded, current))

    assert "new/bundle.json" in text
    assert removed in text
    assert script.REGENERATE_COMMAND in text


def test_difference_report_lists_every_changed_result_and_gate_counts(recorded) -> None:
    rows = script.differences(recorded)
    report = script.render_report(recorded)

    assert not [row for row in rows if "loses rank" in row["reason"]]
    assert sum("becomes ranked" in row["reason"] for row in rows) <= 25
    assert f"- stored results: {len(recorded)}" in report
    assert all(row["path"] in report for row in rows)


def test_throughput_at_size_is_not_scored_for_failed_streams() -> None:
    queries = [{"test_type": "throughput", "run_type": "measurement"}]
    good = {
        "benchmark": {"scale_factor": 1.0},
        "phases": {"throughput_test": {"duration_ms": 1000, "stream_results": [{"success": True}]}},
        "queries": queries,
    }
    bad = {
        "benchmark": {"scale_factor": 1.0},
        "phases": {"throughput_test": {"duration_ms": 1000, "stream_results": [{"success": True}, {"success": False}]}},
        "queries": queries,
    }

    assert corpus_score.throughput_at_size(good) is not None
    assert corpus_score.throughput_at_size(bad) is None
