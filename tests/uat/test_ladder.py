from __future__ import annotations

import pytest

from tests.uat import ladder

pytestmark = pytest.mark.medium


def test_decide_next_advances_when_within_budget():
    rung = ladder.LadderRung(scale=0.01, elapsed_s=30.0, passed=True)
    decision = ladder.decide_next(rung)
    assert decision.prune_remaining is False


def test_decide_next_prunes_on_failure():
    rung = ladder.LadderRung(scale=0.01, elapsed_s=10.0, passed=False)
    decision = ladder.decide_next(rung)
    assert decision.prune_remaining is True
    assert "did not pass" in (decision.reason or "")


def test_decide_next_prunes_when_over_budget():
    rung = ladder.LadderRung(scale=0.1, elapsed_s=300.0, passed=True)
    decision = ladder.decide_next(rung, early_stop_after_s=180.0)
    assert decision.prune_remaining is True
    assert "early_stop_after_s" in (decision.reason or "")


def test_plan_ladder_no_observations_yields_full_ladder():
    next_rungs, pruned = ladder.plan_ladder([0.01, 0.1, 1.0], [])
    assert next_rungs == [0.01, 0.1, 1.0]
    assert pruned == []


def test_plan_ladder_prunes_after_failure():
    obs = [ladder.LadderRung(scale=0.01, elapsed_s=10.0, passed=False)]
    next_rungs, pruned = ladder.plan_ladder([0.01, 0.1, 1.0], obs)
    assert next_rungs == []
    assert pruned == [0.1, 1.0]


def test_plan_ladder_prunes_after_slow_rung():
    obs = [ladder.LadderRung(scale=0.01, elapsed_s=200.0, passed=True)]
    next_rungs, pruned = ladder.plan_ladder([0.01, 0.1, 1.0], obs, early_stop_after_s=180.0)
    assert pruned == [0.1, 1.0]


def test_plan_ladder_returns_remaining_after_one_observation():
    obs = [ladder.LadderRung(scale=0.01, elapsed_s=30.0, passed=True)]
    next_rungs, pruned = ladder.plan_ladder([0.01, 0.1, 1.0], obs)
    assert next_rungs == [0.1, 1.0]
    assert pruned == []
