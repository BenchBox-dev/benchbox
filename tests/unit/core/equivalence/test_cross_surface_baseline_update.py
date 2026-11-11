from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from benchbox.core.equivalence.known_divergences_baseline import (
    BaselineUpdateError,
    _dump,
    load_baseline,
    prune_known_divergences,
    update_baseline_file,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SAMPLE: dict[str, dict[str, str]] = {
    "clickbench": {
        "Q18_expression": "documented tie-ambiguous LIMIT",
    },
    "h2odb": {
        "Q9_expression": "h2odb reason",
        "Q9_pandas": "h2odb reason",
    },
    "read_primitives": {
        "approx_a_expression": "approx reason",
        "approx_a_pandas": "approx reason",
        "approx_b_expression": "approx reason 2",
    },
}


COLLIDING_SAMPLE: dict[str, dict[str, str]] = {
    "alpha": {"Q9_expression": "alpha reason"},
    "beta": {"Q9_expression": "beta reason"},
}


def _write(tmp_path: Path, data: dict[str, dict[str, str]]) -> Path:
    target = tmp_path / "baseline.yaml"
    target.write_text(yaml.safe_dump(data), encoding="utf-8")
    return target


def test_load_baseline_reads_mapping(tmp_path: Path) -> None:
    target = _write(tmp_path, SAMPLE)
    assert load_baseline(target) == SAMPLE


def test_load_baseline_empty_file_is_empty_mapping(tmp_path: Path) -> None:
    target = tmp_path / "baseline.yaml"
    target.write_text("", encoding="utf-8")
    assert load_baseline(target) == {}


def test_load_baseline_invalid_yaml_raises(tmp_path: Path) -> None:
    target = tmp_path / "baseline.yaml"
    target.write_text("key: [unclosed", encoding="utf-8")
    with pytest.raises(BaselineUpdateError, match="not valid YAML"):
        load_baseline(target)


def test_load_baseline_non_mapping_raises(tmp_path: Path) -> None:
    target = tmp_path / "baseline.yaml"
    target.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(BaselineUpdateError, match="top-level mapping"):
        load_baseline(target)


def test_prune_removes_only_named_key_from_named_gate() -> None:
    out = prune_known_divergences(SAMPLE, {"approx_a_expression"}, "read_primitives")
    assert out["read_primitives"] == {
        "approx_a_pandas": "approx reason",
        "approx_b_expression": "approx reason 2",
    }

    assert out["clickbench"] is SAMPLE["clickbench"]
    assert out["h2odb"] is SAMPLE["h2odb"]


def test_prune_whole_gate_section_can_empty() -> None:
    out = prune_known_divergences(SAMPLE, {"Q18_expression"}, "clickbench")
    assert out["clickbench"] == {}


def test_prune_no_resolved_keys_is_noop() -> None:
    out = prune_known_divergences(SAMPLE, set(), "read_primitives")
    assert out is SAMPLE


def test_prune_key_absent_from_named_gate_is_noop() -> None:

    out = prune_known_divergences(SAMPLE, {"not_a_key"}, "read_primitives")
    assert out is SAMPLE


def test_prune_unknown_gate_name_is_noop() -> None:
    out = prune_known_divergences(SAMPLE, {"Q18_expression"}, "not_a_real_gate")
    assert out is SAMPLE


def test_prune_is_scoped_to_the_named_gate_not_every_section() -> None:
    out = prune_known_divergences(COLLIDING_SAMPLE, {"Q9_expression"}, "alpha")
    assert out["alpha"] == {}
    assert out["beta"] == {"Q9_expression": "beta reason"}


def test_update_baseline_file_writes_once_then_noops(tmp_path: Path) -> None:
    target = _write(tmp_path, SAMPLE)

    removed = update_baseline_file(target, {"approx_a_expression", "approx_a_pandas"}, "read_primitives")
    assert removed == ["approx_a_expression", "approx_a_pandas"]
    after_first = load_baseline(target)
    assert after_first["read_primitives"] == {"approx_b_expression": "approx reason 2"}

    assert after_first["clickbench"] == SAMPLE["clickbench"]
    assert after_first["h2odb"] == SAMPLE["h2odb"]
    first_text = target.read_text()

    assert update_baseline_file(target, {"approx_a_expression", "approx_a_pandas"}, "read_primitives") == []
    assert target.read_text() == first_text


def test_update_baseline_file_reports_removal_despite_name_collision(tmp_path: Path) -> None:
    target = _write(tmp_path, COLLIDING_SAMPLE)

    removed = update_baseline_file(target, {"Q9_expression"}, "alpha")
    assert removed == ["Q9_expression"]
    data = load_baseline(target)
    assert data["alpha"] == {}

    assert data["beta"] == {"Q9_expression": "beta reason"}


def test_update_baseline_file_writes_data_without_comments(tmp_path: Path) -> None:
    target = _write(tmp_path, SAMPLE)
    update_baseline_file(target, {"Q18_expression"}, "clickbench")
    text = target.read_text()
    assert not any(line.lstrip().startswith("#") for line in text.splitlines())


def test_real_baseline_file_is_supported() -> None:
    path = Path("benchbox/core/equivalence/cross_surface_baseline.yaml")
    data = load_baseline(path)
    assert data == {}

    assert "Q9_expression" not in data.get("h2odb", {})
    assert "Q18_expression" not in data.get("clickbench", {})
    assert "approx_count_distinct_simple_expression" not in data.get("read_primitives", {})


def test_checked_in_baseline_file_is_a_fixed_point_of_the_writer() -> None:
    path = Path("benchbox/core/equivalence/cross_surface_baseline.yaml")
    original = path.read_text(encoding="utf-8")
    assert _dump(load_baseline(path)) == original


