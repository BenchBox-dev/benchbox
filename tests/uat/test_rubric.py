from __future__ import annotations

import ast

import pytest

from tests.uat import rubric
from tests.uat.rubric_fixtures import (
    ABSORBED,
    CRASH,
    DOWNLOAD,
    OOM,
    PASS,
    SINGLESTORE_1712,
    TIMEOUT,
    WRONG_RESULTS,
    cell_row,
    write_history,
    write_run,
)

pytestmark = pytest.mark.medium

PAIR = ("singlestore", "tpch")
ALL_SCALES = {PAIR: {0.01, 0.1, 1.0}}
ONLY_SF1 = {PAIR: {1.0}}


def _cell(scale: float) -> tuple[str, str, float]:
    return (*PAIR, scale)


def _report(history_dir, gated, **kwargs):
    return rubric.build_envelope_report(rubric.load_history(history_dir), gated, **kwargs)


def _signal(history_dir, gated, **kwargs):
    return rubric.build_signal_report(rubric.load_history(history_dir), gated, **kwargs)


def test_rate_above_twenty_percent_over_ten_runs_proposes_an_envelope_when_another_scale_stays_gated(tmp_path):
    kinds = [PASS] * 7 + [TIMEOUT, OOM, DOWNLOAD]
    write_history(tmp_path, {_cell(1.0): kinds, _cell(0.01): [PASS] * 10})

    report = _report(tmp_path, ALL_SCALES)

    assert len(report["proposals"]) == 1
    proposal = report["proposals"][0]
    assert (proposal["platform"], proposal["benchmark"], proposal["scale"]) == ("singlestore", "tpch", 1.0)
    assert proposal["infrastructure_failures"] == 3
    assert proposal["rate"] == pytest.approx(0.3)
    assert proposal["signatures"] == {"download_error": 1, "memory": 1, "timeout": 1}
    assert proposal["remaining_gated_scales"] == [0.01, 0.1]
    assert report["tracker_item_suggestions"] == []


def test_rate_at_or_below_twenty_percent_proposes_nothing(tmp_path):
    write_history(tmp_path, {_cell(1.0): [PASS] * 8 + [TIMEOUT, TIMEOUT], _cell(0.01): [PASS] * 10})

    report = _report(tmp_path, ALL_SCALES)

    assert report["proposals"] == []
    assert report["tracker_item_suggestions"] == []


def test_last_gated_scale_is_never_proposed_and_gets_a_tracker_item_suggestion(tmp_path):
    write_history(tmp_path, {_cell(1.0): [PASS] * 6 + [TIMEOUT] * 4})

    report = _report(tmp_path, ONLY_SF1)

    assert report["proposals"] == []
    (suggestion,) = report["tracker_item_suggestions"]
    assert suggestion["tracker_item"]["title"].startswith("UAT envelope:")
    assert "uat-rubric" in suggestion["tracker_item"]["links"]
    assert "last gated scale" in suggestion["reason"]


def test_all_scales_flaky_drops_the_largest_first_and_keeps_the_smallest_gated(tmp_path):
    flaky = [PASS] * 6 + [TIMEOUT] * 4
    write_history(tmp_path, {_cell(0.01): list(flaky), _cell(0.1): list(flaky), _cell(1.0): list(flaky)})

    report = _report(tmp_path, ALL_SCALES)

    assert [p["scale"] for p in report["proposals"]] == [1.0, 0.1]
    assert [p["remaining_gated_scales"] for p in report["proposals"]] == [[0.01, 0.1], [0.01]]
    assert [s["scale"] for s in report["tracker_item_suggestions"]] == [0.01]


def test_only_the_last_ten_runs_count(tmp_path):
    write_history(tmp_path, {_cell(1.0): [TIMEOUT] * 5 + [PASS] * 10, _cell(0.01): [PASS] * 15})

    assert _report(tmp_path, ALL_SCALES)["proposals"] == []


def test_cell_with_fewer_than_ten_runs_is_not_judged(tmp_path):
    write_history(tmp_path, {_cell(1.0): [TIMEOUT] * 5, _cell(0.01): [PASS] * 5})

    assert _report(tmp_path, ALL_SCALES)["proposals"] == []


def test_an_absorbed_retry_counts_as_an_infrastructure_failure(tmp_path):
    write_history(tmp_path, {_cell(1.0): [PASS] * 7 + [ABSORBED] * 3, _cell(0.01): [PASS] * 10})

    (proposal,) = _report(tmp_path, ALL_SCALES)["proposals"]

    assert proposal["signatures"] == {"timeout": 3}


def test_singlestore_1712_is_an_infrastructure_signature_in_history(tmp_path):
    write_history(tmp_path, {_cell(1.0): [PASS] * 7 + [SINGLESTORE_1712] * 3, _cell(0.01): [PASS] * 10})

    (proposal,) = _report(tmp_path, ALL_SCALES)["proposals"]

    assert proposal["signatures"] == {"singlestore_1712": 3}


@pytest.mark.parametrize("product_kind", [WRONG_RESULTS, CRASH])
def test_a_cell_that_ever_caught_a_product_defect_is_held_not_proposed(tmp_path, product_kind):
    kinds = [product_kind] + [PASS] * 6 + [TIMEOUT] * 3
    write_history(tmp_path, {_cell(1.0): kinds, _cell(0.01): [PASS] * 10})

    report = _report(tmp_path, ALL_SCALES)

    assert report["proposals"] == []
    (held,) = report["held_for_product_signal"]
    assert held["product_signatures_ever"]


def test_cell_outside_the_gate_is_not_proposed_again(tmp_path):
    write_history(tmp_path, {_cell(1.0): [PASS] * 6 + [TIMEOUT] * 4, _cell(0.01): [PASS] * 10})

    report = _report(tmp_path, {PAIR: {0.01}})

    assert report["proposals"] == []
    assert [item["scale"] for item in report["not_gated"]] == [1.0]


def test_envelope_entry_lines_are_ready_to_paste_into_the_compatibility_tables(tmp_path):
    write_history(tmp_path, {_cell(1.0): [PASS] * 7 + [TIMEOUT] * 3, _cell(0.01): [PASS] * 10})

    (proposal,) = _report(tmp_path, ALL_SCALES)["proposals"]
    envelope = proposal["envelope"]

    reasons = ast.literal_eval("{" + envelope["reason_line"] + "}")
    evidence = ast.literal_eval("{" + envelope["evidence_line"] + "}")
    assert list(reasons) == list(evidence) == [("singlestore", "tpch", 1.0)]
    assert reasons[("singlestore", "tpch", 1.0)] == envelope["reason"]
    assert "SF0.01" in envelope["reason"] and "3 of the last 10" in envelope["reason"]
    assert envelope["reason_line"] in proposal["tracker_item"]["description"]
    assert proposal["tracker_item"]["title"].startswith("UAT envelope:")
    assert proposal["tracker_item"]["links"][0] == "uat-rubric"


def test_signal_record_lists_defect_classes_by_kind(tmp_path):
    write_history(
        tmp_path,
        {
            _cell(0.01): [PASS, WRONG_RESULTS, CRASH, TIMEOUT, PASS],
            _cell(0.1): [PASS, TIMEOUT, OOM, PASS, PASS],
        },
    )

    cells = {c["scale"]: c for c in _signal(tmp_path, ALL_SCALES)["cells"]}

    assert cells[0.01]["defect_classes_caught"] == ["crash", "query_failure"]
    assert cells[0.01]["product_failures"] == {"crash": 1, "query_failure": 1}
    assert cells[0.01]["infrastructure_failures"] == {"timeout": 1}
    assert cells[0.1]["defect_classes_caught"] == []
    assert cells[0.1]["infrastructure_failures"] == {"memory": 1, "timeout": 1}
    assert cells[0.1]["runs"] == 5 and cells[0.1]["passed"] == 3


def test_validator_errors_count_as_product_signal(tmp_path):
    write_run(
        tmp_path,
        "run-a",
        "2026-10-01T00:00:00+00:00",
        [cell_row(*PAIR, 0.1, PASS)],
        validator_errors=((*PAIR, 0.1),),
        verdict="red",
    )

    (cell,) = _signal(tmp_path, ALL_SCALES)["cells"]

    assert cell["product_failures"] == {"validation_failure": 1}


def test_drop_list_holds_only_cells_whose_failures_were_all_infrastructure(tmp_path):
    write_history(
        tmp_path,
        {
            _cell(1.0): [PASS] * 8 + [TIMEOUT, OOM],
            _cell(0.1): [PASS] * 8 + [TIMEOUT, WRONG_RESULTS],
            _cell(0.01): [PASS] * 10,
        },
    )

    drop_list = _signal(tmp_path, ALL_SCALES)["drop_list"]

    assert drop_list["status"] == "proposed_for_owner_review"
    assert [c["scale"] for c in drop_list["candidates"]] == [1.0]
    assert drop_list["candidates"][0]["remaining_gated_scales"] == [0.01, 0.1]


def test_drop_list_never_includes_the_last_gated_scale(tmp_path):
    write_history(tmp_path, {_cell(1.0): [PASS] * 8 + [TIMEOUT, OOM]})

    drop_list = _signal(tmp_path, ONLY_SF1)["drop_list"]

    assert drop_list["candidates"] == []
    assert [c["scale"] for c in drop_list["kept_last_gated_scale"]] == [1.0]


def test_drop_list_needs_enough_history(tmp_path):
    write_history(tmp_path, {_cell(1.0): [PASS, TIMEOUT, TIMEOUT], _cell(0.01): [PASS] * 3})

    assert _signal(tmp_path, ALL_SCALES)["drop_list"]["candidates"] == []
    assert len(_signal(tmp_path, ALL_SCALES, min_runs=3)["drop_list"]["candidates"]) == 1


def test_red_shard_product_share_counts_shards_with_product_signatures(tmp_path):
    write_run(tmp_path, "r1", "2026-10-01T00:00:00+00:00", [cell_row(*PAIR, 1.0, WRONG_RESULTS)], verdict="red")
    write_run(tmp_path, "r2", "2026-10-02T00:00:00+00:00", [cell_row(*PAIR, 1.0, TIMEOUT)], verdict="red")
    write_run(tmp_path, "r3", "2026-10-03T00:00:00+00:00", [cell_row(*PAIR, 1.0, CRASH)], verdict="red")
    write_run(tmp_path, "r4", "2026-10-04T00:00:00+00:00", [cell_row(*PAIR, 1.0, PASS)], verdict="green")

    share = _signal(tmp_path, ALL_SCALES)["red_shards"]

    assert share == {"red_shards": 3, "with_product_signature": 2, "share": pytest.approx(2 / 3)}


def test_runs_are_ordered_by_completion_time_and_dry_runs_are_ignored(tmp_path):
    write_run(tmp_path, "z-first", "2026-10-01T00:00:00+00:00", [cell_row(*PAIR, 1.0, PASS)])
    write_run(tmp_path, "a-last", "2026-10-09T00:00:00+00:00", [cell_row(*PAIR, 1.0, TIMEOUT)], verdict="red")
    write_run(tmp_path, "dry", "2026-10-05T00:00:00+00:00", [cell_row(*PAIR, 1.0, CRASH)], dry_run=True)

    runs = rubric.load_history(tmp_path)

    assert [run.run_id for run in runs] == ["z-first", "a-last"]


def test_skipped_cells_are_not_observations(tmp_path):
    skipped = cell_row(*PAIR, 1.0, PASS)
    skipped.update(status="skipped", terminal_state="skipped")
    write_run(tmp_path, "r1", "2026-10-01T00:00:00+00:00", [skipped])

    assert _signal(tmp_path, ALL_SCALES)["cells"] == []


