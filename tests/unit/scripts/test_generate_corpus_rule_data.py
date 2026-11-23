from __future__ import annotations

import generate_corpus_rule_data as script
import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_committed_data_file_matches_the_scale_registry_and_tpc_spec() -> None:
    assert script.drift() is None


def test_a_changed_data_file_fails_with_the_regenerate_command(tmp_path) -> None:
    stale = tmp_path / "corpus_rule_data.json"
    stale.write_text(script.DATA_PATH.read_text(encoding="utf-8").replace('"tpch": {', '"tpch": {"7.0": 9,', 1))

    problem = script.drift(stale)

    assert problem is not None
    assert script.REGENERATE_COMMAND in problem


def test_a_missing_data_file_fails(tmp_path) -> None:
    assert "does not exist" in (script.drift(tmp_path / "absent.json") or "")


def test_a_changed_stream_minimum_in_the_source_changes_the_data(monkeypatch) -> None:
    monkeypatch.setattr(script, "min_streams_for_scale", lambda benchmark, scale: 99)

    assert script.drift() is not None


def test_reference_scales_in_the_data_file_follow_the_shared_loader() -> None:
    from benchbox.core.expected_results import has_row_count_reference
    from benchbox.core.results import corpus_rule

    recorded = corpus_rule.load_data()["row_count_reference_scales"]
    assert recorded == script.row_count_reference_scales()
    for benchmark in ("tpch", "tpcds", "ssb"):
        for scale in (0.01, 0.1, 0.5, 1.0, 3.0, 10.0, 100.0):
            assert corpus_rule.row_count_reference_exists(benchmark, scale) == has_row_count_reference(benchmark, scale)
    assert not corpus_rule.row_count_reference_exists("amplab", 1.0)
    assert not corpus_rule.row_count_reference_exists(None, 1.0)


def test_a_changed_reference_source_changes_the_data(monkeypatch) -> None:
    monkeypatch.setattr(script, "has_row_count_reference", lambda benchmark, scale: False)

    assert script.drift() is not None
