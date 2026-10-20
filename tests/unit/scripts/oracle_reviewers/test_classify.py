from __future__ import annotations

from collections.abc import Mapping

import pytest

from _project.scripts.oracle_reviewers.classify import (
    Classification,
    _choice,
    _score_to_int,
    apply_rules,
    classify_diff,
    file_state,
    pr_state,
)
from _project.scripts.oracle_reviewers.jev import JevSession

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _answer(choice: str, top: float, others: dict | None = None) -> dict:
    probs = {choice: top}
    probs.update(others or {})
    return {"choice": choice, "probabilities": probs, "confidence": top}


def test_confident_argmax_passes_through() -> None:
    bucket, capability, template, confidence = apply_rules(
        _answer("large", 0.9),
        _answer("deep", 0.8),
        _answer("deep", 0.85),
        [],
    )
    assert (bucket, capability, template, confidence) == ("large", "deep", "deep", 0.9)


def test_low_confidence_size_steps_down_with_small_floor() -> None:
    bucket, _, _, _ = apply_rules(_answer("large", 0.55), _answer("routine", 0.9), _answer("standard", 0.9), [])
    assert bucket == "medium"
    bucket, _, _, _ = apply_rules(_answer("small", 0.4), _answer("routine", 0.9), _answer("standard", 0.9), [])
    assert bucket == "small"


def test_trivial_needs_confidence() -> None:
    bucket, _, _, _ = apply_rules(_answer("trivial", 0.9), _answer("routine", 0.9), _answer("standard", 0.9), [])
    assert bucket == "trivial"
    bucket, _, _, _ = apply_rules(
        _answer("trivial", 0.5, {"small": 0.4}), _answer("routine", 0.9), _answer("standard", 0.9), []
    )
    assert bucket == "small"


def test_low_confidence_capability_falls_to_routine() -> None:
    _, capability, _, _ = apply_rules(_answer("medium", 0.9), _answer("deep", 0.5), _answer("standard", 0.9), [])
    assert capability == "routine"


def test_gate_flags_floor_the_template() -> None:
    _, _, template, _ = apply_rules(_answer("large", 0.9), _answer("deep", 0.9), _answer("standard", 0.95), ["g.yml"])
    assert template == "deep"
    _, _, template, _ = apply_rules(_answer("large", 0.9), _answer("deep", 0.9), _answer("deep", 0.4), ["g.yml"])
    assert template == "gate"
    _, _, template, _ = apply_rules(_answer("large", 0.9), _answer("deep", 0.9), _answer("deep", 0.4), [])
    assert template == "standard"


def test_unknown_labels_raise() -> None:
    with pytest.raises(ValueError):
        apply_rules(_answer("colossal", 0.9), _answer("deep", 0.9), _answer("deep", 0.9), [])


def test_score_rounds_and_clamps() -> None:
    assert _score_to_int({"score": 4.6}) == 5
    assert _score_to_int({"score": 0.2}) == 1
    assert _score_to_int({"score": "high"}) == 1
    choice, probs, confidence = _choice({"choice": "high", "probabilities": {"high": 0.7}, "confidence": 0.4})
    assert (choice, probs, confidence) == ("high", {"high": 0.7}, 0.4)


STATS = [("new.py", 2, 0, "added"), ("old.py", 0, 1, "removed"), ("kept.py", 1, 1, "modified")]


def test_states_carry_the_facts() -> None:
    state = pr_state("o/n", 7, "a" * 40, "b" * 40, STATS, ["g.yml"])
    assert "3 files" in state["text"]
    assert "+3/-2" in state["text"] and "Gate signals: touches" in state["text"]
    single = file_state("new.py", 2, 0, "added", [(p, a, d) for p, a, d, _ in STATS])
    assert "new.py" in single["text"] and "+2/-0" in single["text"]


class _FakeSession(JevSession):
    def __init__(self, answers: list[dict]) -> None:
        super().__init__(api_key="unused")
        self.answers = answers

    def ask(self, state: object, questions: Mapping[str, object]) -> dict:
        return self.answers.pop(0)


def test_classify_diff_applies_rules_and_scores_files() -> None:
    answers = [
        {
            "size": _answer("large", 0.9),
            "capability": _answer("deep", 0.85),
            "template": _answer("deep", 0.8),
        },
        {
            "impact": _answer("high", 0.9),
            "importance": {"score": 4.7},
        },
        {
            "impact": _answer("low", 0.95),
            "importance": {"score": 1.2},
        },
    ]
    result = classify_diff(
        _FakeSession(answers),
        repo="o/n",
        pr=7,
        base_sha="a" * 40,
        head_sha="b" * 40,
        stats=[("new.py", 2, 0, "added"), ("old.py", 0, 1, "removed")],
        gate_flags=[],
    )
    assert (result.size_bucket, result.capability, result.template) == ("large", "deep", "deep")
    assert [(row.path, row.impact, row.score) for row in result.files] == [
        ("new.py", "high", 5),
        ("old.py", "low", 1),
    ]
    assert Classification.from_json(result.to_json()) == result
