from __future__ import annotations

import pytest

from benchbox.core.results.query_plan_models import (
    DEFAULT_RAW_OUTPUT_MAX_BYTES,
    DEFAULT_RAW_OUTPUT_POLICY,
    RAW_OUTPUT_NONE,
    RAW_OUTPUT_TRUNCATED,
    LogicalOperator,
    LogicalOperatorType,
    QueryPlanDAG,
    normalize_raw_output_policy,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_plan(raw: str | None) -> QueryPlanDAG:
    root = LogicalOperator(
        operator_type=LogicalOperatorType.SCAN,
        operator_id="scan_1",
        table_name="orders",
    )
    return QueryPlanDAG(
        query_id="q1",
        platform="duckdb",
        logical_root=root,
        raw_explain_output=raw,
    )


class TestNormalizeRawOutputPolicy:
    @pytest.mark.parametrize("value", ["full", "truncated", "none", "FULL", " Truncated "])
    def test_known_policies_pass_through(self, value):
        assert normalize_raw_output_policy(value) == value.strip().lower()

    def test_none_input_uses_default(self):
        assert normalize_raw_output_policy(None) == DEFAULT_RAW_OUTPUT_POLICY

    def test_unknown_policy_falls_back_to_default(self):
        assert normalize_raw_output_policy("compressed") == DEFAULT_RAW_OUTPUT_POLICY


class TestPlanRawOutputPolicy:
    def test_plan_raw_output_full_retains_text(self):
        raw = "x" * 50_000
        plan = _make_plan(raw)
        plan.apply_raw_output_policy("full", max_bytes=128)
        assert plan.raw_explain_output == raw

    def test_plan_raw_output_none_drops_text(self):
        plan = _make_plan("EXPLAIN ... lots of text")
        plan.apply_raw_output_policy("none")
        assert plan.raw_explain_output is None

    def test_plan_raw_output_truncated_caps_large_text(self):
        raw = "A" * 10_000
        plan = _make_plan(raw)
        plan.apply_raw_output_policy("truncated", max_bytes=256)
        out = plan.raw_explain_output
        assert out is not None

        assert out.startswith("A" * 256)

        assert "truncated" in out
        assert "10000 bytes" in out

    def test_plan_raw_output_truncated_keeps_small_text_unchanged(self):
        raw = "short plan"
        plan = _make_plan(raw)
        plan.apply_raw_output_policy("truncated", max_bytes=DEFAULT_RAW_OUTPUT_MAX_BYTES)
        assert plan.raw_explain_output == raw

    def test_truncated_at_exactly_cap_is_unchanged(self):
        raw = "B" * 100
        plan = _make_plan(raw)
        plan.apply_raw_output_policy("truncated", max_bytes=100)
        assert plan.raw_explain_output == raw

    def test_truncated_non_positive_cap_drops_text(self):
        plan = _make_plan("some text")
        plan.apply_raw_output_policy("truncated", max_bytes=0)
        assert plan.raw_explain_output is None

    def test_truncation_handles_multibyte_boundary(self):

        raw = "€" * 1000
        plan = _make_plan(raw)
        plan.apply_raw_output_policy("truncated", max_bytes=100)
        out = plan.raw_explain_output
        assert out is not None

        out.encode("utf-8")
        assert "�" not in out

        assert "retained 99 of 3000 bytes" in out

    def test_unknown_policy_falls_back_to_truncated(self):
        raw = "C" * 10_000
        plan = _make_plan(raw)
        plan.apply_raw_output_policy("bogus", max_bytes=256)

        assert plan.raw_explain_output is not None
        assert len(plan.raw_explain_output) < len(raw)
        assert RAW_OUTPUT_TRUNCATED in plan.raw_explain_output

    def test_none_raw_output_is_noop(self):
        plan = _make_plan(None)
        plan.apply_raw_output_policy("truncated", max_bytes=10)
        assert plan.raw_explain_output is None

    @pytest.mark.parametrize("policy", ["full", "truncated", "none", RAW_OUTPUT_NONE])
    def test_dag_and_fingerprint_unaffected_by_policy(self, policy):
        raw = "D" * 10_000
        plan = _make_plan(raw)
        fingerprint_before = plan.plan_fingerprint
        root_before = plan.logical_root

        plan.apply_raw_output_policy(policy, max_bytes=128)

        assert plan.plan_fingerprint == fingerprint_before
        assert plan.logical_root is root_before
        assert plan.verify_fingerprint() is True


