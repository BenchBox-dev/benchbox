from __future__ import annotations

import pytest

from benchbox.core.results.vendor_terms import evaluate_vendor_conditions, vendor_platform_key

pytestmark = [pytest.mark.unit, pytest.mark.fast]

NOTICE = {"sent_on": "2026-11-01", "reference": "ticket-123"}


class TestPlatformKey:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Snowflake", "snowflake"),
            ("ClickHouse Cloud", "clickhouse-cloud"),
            ("clickhouse_cloud", "clickhouse-cloud"),
        ],
    )
    def test_normalizes(self, raw, expected):
        assert vendor_platform_key(raw) == expected

    def test_local_clickhouse_is_not_cloud(self):
        assert vendor_platform_key("clickhouse-local") == "clickhouse-local"


class TestTierAndPreview:
    @pytest.mark.parametrize("tier", ["free", "trial"])
    def test_free_or_trial_refused_without_permission(self, tier):
        errors, _ = evaluate_vendor_conditions({"platform": "databricks", "plan_tier": tier})
        assert any("written permission" in e and tier in e for e in errors)

    def test_free_with_permission_accepted(self):
        errors, _ = evaluate_vendor_conditions(
            {"platform": "databricks", "plan_tier": "free", "vendor_permission": "email 2026-10-30"}
        )
        assert errors == []

    def test_preview_feature_refused_without_permission(self):
        errors, _ = evaluate_vendor_conditions(
            {"platform": "snowflake", "plan_tier": "paid", "preview_features": ["x"], "vendor_notice": NOTICE}
        )
        assert any("preview features (x)" in e for e in errors)

    def test_paid_without_preview_accepted(self):
        errors, warnings = evaluate_vendor_conditions(
            {"platform": "snowflake", "plan_tier": "paid", "vendor_notice": NOTICE}
        )
        assert errors == []
        assert warnings == []

    def test_unknown_tier_rejected(self):
        errors, _ = evaluate_vendor_conditions({"platform": "duckdb", "plan_tier": "gold"})
        assert any("plan_tier" in e for e in errors)

    def test_bad_preview_features_rejected(self):
        errors, _ = evaluate_vendor_conditions({"platform": "duckdb", "preview_features": "x"})
        assert any("preview_features" in e for e in errors)

    def test_undeclared_tier_warns_on_tiered_platform(self):
        _, warnings = evaluate_vendor_conditions({"platform": "motherduck", "vendor_notice": NOTICE})
        assert any("plan_tier" in w for w in warnings)

    def test_local_platform_needs_nothing(self):
        assert evaluate_vendor_conditions({"platform": "duckdb"}) == ([], [])


class TestVendorNotice:
    @pytest.mark.parametrize("platform", ["snowflake", "redshift", "athena", "clickhouse-cloud", "motherduck"])
    def test_missing_notice_warns(self, platform):
        _, warnings = evaluate_vendor_conditions({"platform": platform, "plan_tier": "paid"})
        assert any("vendor_notice" in w for w in warnings)

    @pytest.mark.parametrize(
        "notice",
        [
            {"sent_on": "yesterday", "reference": "r"},
            {"sent_on": "2026-11-01"},
            {"sent_on": None, "reference": "r"},
            "sent",
        ],
    )
    def test_malformed_notice_rejected(self, notice):
        errors, _ = evaluate_vendor_conditions({"platform": "snowflake", "plan_tier": "paid", "vendor_notice": notice})
        assert any("vendor_notice" in e for e in errors)

    def test_valid_notice_clears_warning(self):
        _, warnings = evaluate_vendor_conditions({"platform": "redshift", "plan_tier": "paid", "vendor_notice": NOTICE})
        assert warnings == []


class TestBigQuerySelfRun:
    def test_self_run_false_rejected(self):
        errors, _ = evaluate_vendor_conditions({"platform": "bigquery", "self_run": False})
        assert any("self_run" in e for e in errors)

    def test_self_run_true_accepted(self):
        assert evaluate_vendor_conditions({"platform": "bigquery", "self_run": True}) == ([], [])

    def test_missing_self_run_warns(self):
        _, warnings = evaluate_vendor_conditions({"platform": "bigquery"})
        assert any("self_run" in w for w in warnings)
