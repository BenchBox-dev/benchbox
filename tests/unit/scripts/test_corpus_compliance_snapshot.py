from __future__ import annotations

import copy
import dataclasses
import json

import corpus_compliance_snapshot as script
import pytest

from _project.scripts.explorer_pipeline import medians as pipeline_medians
from benchbox.core.results import corpus_rule, corpus_score
from tests.unit.scripts.explorer_pipeline.conftest import combined_bundle

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


@pytest.fixture(scope="module")
def recorded_medians() -> script.IdentityMedians:
    return script.load_identity_medians(SNAPSHOT)


@pytest.fixture(scope="module")
def computed_medians() -> script.IdentityMedians:
    return script.compute_identity_medians(BUNDLES)


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
        assert {"class", "score_source", "ranks"} <= set(entry["today"])
        for rank in entry["today"]["ranks"].values():
            assert {"cohort", "rank", "total", "score", "exclusion_reason"} <= set(rank)
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
            assert today["ranks"].keys() == rule["ranks"].keys(), name
        assert not script.differences({name: entry}), name


def test_a_drifted_today_outcome_fails_and_names_the_result(recorded, computed) -> None:
    drifted = copy.deepcopy(computed)
    name = next(
        name
        for name, entry in drifted.items()
        if any(rank["rank"] is not None for rank in entry["today"].get("ranks", {}).values())
    )
    lane = next(lane for lane, rank in drifted[name]["today"]["ranks"].items() if rank["rank"] is not None)
    drifted[name]["today"]["ranks"][lane]["rank"] += 1

    problems = script.compare(recorded, drifted)

    assert "today outcome" in problems[0]
    assert script.REGENERATE_COMMAND in problems[0]
    assert _changed_files(problems) == {name}


def _partial_run(tmp_path, throughput_ms: dict[str, float]) -> script.Snapshot:
    bundles = tmp_path / "bundles"
    bundles.mkdir(parents=True)
    for platform, qph in throughput_ms.items():
        data = combined_bundle(f"platform-{platform}", qph=qph)
        data["run"]["id"] = platform
        data["phases"]["throughput_test"].pop("refresh_stream")
        (bundles / f"{platform}.json").write_text(json.dumps(data), encoding="utf-8")
    return script.compute_snapshot(bundles)


def test_the_pipeline_rank_equals_the_rules_own_rank_for_every_lane_of_every_result(computed, tmp_path) -> None:
    two_lane = _partial_run(tmp_path / "any", {"a": 250.0, "b": 300.0})
    assert {len(entry["rule"]["ranks"]) for entry in two_lane.values()} == {2}
    ranked = 0
    for snapshot in (computed, two_lane):
        for name, entry in snapshot.items():
            if "skipped" in entry["today"]:
                continue
            ranked += sum(rank["rank"] is not None for rank in entry["today"]["ranks"].values())
            assert script.differences({name: entry}) == [], name
    assert ranked


def _misrank(monkeypatch, metric: str) -> None:
    original = script.rank_platforms

    def misranked(summary):
        cohort = original(summary)
        if cohort.primary_metric != metric:
            return cohort
        rows = [dataclasses.replace(row, rank=None if row.rank is None else row.rank + 1) for row in cohort.rows]
        return dataclasses.replace(cohort, rows=rows)

    monkeypatch.setattr(script, "rank_platforms", misranked)


def test_a_pipeline_misranking_shows_as_a_difference_from_the_rule(computed, monkeypatch) -> None:
    _misrank(monkeypatch, "display_geomean_ms")
    skewed = script.compute_snapshot(BUNDLES)

    changed = {
        name
        for name, entry in skewed.items()
        if "skipped" not in entry["today"] and entry["today"]["ranks"] != computed[name]["today"]["ranks"]
    }
    assert changed
    for name in changed:
        assert skewed[name]["rule"]["ranks"] == computed[name]["rule"]["ranks"]
    assert {row["path"] for row in script.differences(skewed)} == changed


def test_a_misranked_secondary_lane_shows_as_a_difference_from_the_rule(monkeypatch, tmp_path) -> None:
    expected = _partial_run(tmp_path / "clean", {"a": 250.0, "b": 300.0})
    _misrank(monkeypatch, "throughput_at_size")

    skewed = _partial_run(tmp_path / "skewed", {"a": 250.0, "b": 300.0})

    for name, entry in skewed.items():
        assert entry["today"]["ranks"]["power_only"] == expected[name]["today"]["ranks"]["power_only"]
        assert entry["rule"]["ranks"] == expected[name]["rule"]["ranks"]
    rows = script.differences(skewed)
    assert {row["path"] for row in rows} == set(skewed)
    assert all("throughput: rank changes" in row["reason"] and "power_only" not in row["reason"] for row in rows)


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


def test_committed_identity_medians_match_the_snapshot(recorded_medians, computed_medians) -> None:
    assert recorded_medians["today"]
    assert script.compare_identity_medians(recorded_medians, computed_medians) == []


def test_the_published_identity_medians_equal_the_rules_own_for_the_committed_corpus(recorded_medians) -> None:
    assert recorded_medians["today"] == recorded_medians["rule"]
    assert script.identity_median_differences(recorded_medians) == []


def test_every_identity_median_row_records_its_rank_cohort_and_exclusion(recorded_medians) -> None:
    fields = {
        *script.MEDIAN_KEY_FIELDS,
        "version_is_release",
        "run_count",
        "median_score",
        "is_leaderboard_version",
        "rank",
        "total_in_cohort",
        "ranking_exclusion_reason",
    }
    for side in ("today", "rule"):
        for row in recorded_medians[side]:
            assert set(row) == fields
            assert (row["rank"] is None) == (row["ranking_exclusion_reason"] is not None)


def test_a_drifted_median_rank_fails_and_names_the_identity(recorded_medians, computed_medians) -> None:
    drifted = copy.deepcopy(computed_medians)
    row = next(row for row in drifted["today"] if row["rank"] is not None)
    row["rank"] += 1

    problems = script.compare_identity_medians(recorded_medians, drifted)

    assert "identity median" in problems[0]
    assert script.REGENERATE_COMMAND in problems[0]
    assert len(problems) == 2
    assert row["variant_id"] in problems[1]


def test_a_changed_median_ranking_shows_as_a_difference_from_the_rule(recorded_medians, monkeypatch) -> None:
    original = pipeline_medians.rank_leaderboard_versions

    def inverted(medians, *, higher_is_better):
        return original(medians, higher_is_better=not higher_is_better)

    monkeypatch.setattr("_project.scripts.explorer_pipeline.medians.rank_leaderboard_versions", inverted)
    skewed = script.compute_identity_medians(BUNDLES)

    assert skewed["rule"] == recorded_medians["rule"]
    assert script.compare_identity_medians(recorded_medians, skewed)
    assert script.identity_median_differences(skewed)


def test_dropping_unrecorded_version_identities_from_the_leaderboard_is_caught(recorded_medians, monkeypatch) -> None:
    original = pipeline_medians._mark_leaderboard_versions

    def releases_only(medians):
        return original([median for median in medians if median.version_is_release])

    monkeypatch.setattr("_project.scripts.explorer_pipeline.medians._mark_leaderboard_versions", releases_only)
    skewed = script.compute_identity_medians(BUNDLES)

    assert script.identity_median_differences(skewed)
    assert any(
        "version not recorded" in problem for problem in script.compare_identity_medians(recorded_medians, skewed)
    )
