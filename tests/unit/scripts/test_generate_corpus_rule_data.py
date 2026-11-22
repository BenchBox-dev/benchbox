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
