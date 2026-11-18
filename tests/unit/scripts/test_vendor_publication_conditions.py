from __future__ import annotations

import pytest

from benchbox.validation.bundle import (
    BUNDLE_RUN_TIMESTAMP_KEY,
    MANDATORY_VENDOR_PLATFORMS,
    VENDOR_CONDITIONS_MANDATORY_FROM,
    VENDOR_STATUS_EVIDENCE_BACKED,
    VENDOR_STATUS_INCOMPLETE,
    VENDOR_STATUS_NOT_APPLICABLE,
    VENDOR_STATUS_PRE_POLICY,
    evaluate_vendor_conditions,
    vendor_platform_key,
    vendor_publication_status,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

NOTICE = {"sent_on": "2026-11-01", "reference": "ticket-123"}
COMPLETE = {"plan_tier": "paid", "vendor_notice": NOTICE, "self_run": True}
POST_CUTOFF = f"{VENDOR_CONDITIONS_MANDATORY_FROM}T00:00:00+00:00"
PRE_CUTOFF = "2026-09-19T21:11:34.793439"


def complete(platform: str, **extra):
    return {"platform": platform, **COMPLETE, **extra}


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

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Databricks DataFrame", "databricks"),
            ("databricks-df", "databricks"),
            ("Athena Spark", "athena"),
            ("athena-spark", "athena"),
            ("Amazon Redshift", "redshift"),
            ("Google BigQuery", "bigquery"),
            ("Snowflake Snowpark", "snowflake"),
        ],
    )
    def test_matches_variant_names(self, raw, expected):
        assert vendor_platform_key(raw) == expected

    def test_local_clickhouse_is_not_cloud(self):
        assert vendor_platform_key("clickhouse-local") == "clickhouse-local"


class TestTierAndPreview:
    @pytest.mark.parametrize("tier", ["free", "trial"])
    def test_free_or_trial_refused_without_permission(self, tier):
        errors, _ = evaluate_vendor_conditions(complete("databricks", plan_tier=tier))
        assert any("written permission" in e and tier in e for e in errors)

    def test_free_with_permission_accepted(self):
        errors, _ = evaluate_vendor_conditions(
            complete("databricks", plan_tier="free", vendor_permission="email 2026-10-30")
        )
        assert errors == []

    def test_free_trial_funding_treated_as_trial_tier(self):
        errors, _ = evaluate_vendor_conditions({"platform": "duckdb", "funding": "free-trial", "plan_tier": "paid"})
        assert any("written permission" in e and "trial" in e for e in errors)

    def test_free_trial_funding_with_permission_accepted(self):
        errors, _ = evaluate_vendor_conditions(
            {"platform": "duckdb", "funding": "free-trial", "vendor_permission": "email 2026-10-30"}
        )
        assert errors == []

    def test_preview_feature_refused_without_permission(self):
        errors, _ = evaluate_vendor_conditions(complete("snowflake", preview_features=["x"]))
        assert any("preview features (x)" in e for e in errors)

    def test_databricks_preview_refused_without_permission(self):
        errors, _ = evaluate_vendor_conditions(complete("databricks", preview_features=["serverless gpu"]))
        assert len(errors) == 1
        assert "Beta" in errors[0] and "serverless gpu" in errors[0]

    def test_databricks_preview_refused_with_permission(self):
        errors, _ = evaluate_vendor_conditions(
            complete("databricks", preview_features=["serverless gpu"], vendor_permission="email 2026-10-30")
        )
        assert len(errors) == 1
        assert "does not lift the exclusion" in errors[0]

    def test_databricks_variant_preview_refused_with_permission(self):
        errors, _ = evaluate_vendor_conditions(
            complete("Databricks DataFrame", preview_features=["x"], vendor_permission="email 2026-10-30")
        )
        assert any("Beta" in e for e in errors)

    def test_other_vendor_preview_still_accepted_with_permission(self):
        errors, _ = evaluate_vendor_conditions(
            complete("snowflake", preview_features=["x"], vendor_permission="approval 2026-11-01")
        )
        assert errors == []

    def test_paid_without_preview_accepted(self):
        errors, warnings = evaluate_vendor_conditions(complete("snowflake"))
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
    @pytest.mark.parametrize("platform", ["redshift", "athena", "clickhouse-cloud", "motherduck"])
    def test_missing_notice_warns_on_non_mandatory_platform(self, platform):
        errors, warnings = evaluate_vendor_conditions({"platform": platform, "plan_tier": "paid"})
        assert errors == []
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
    @pytest.mark.parametrize("platform", ["snowflake", "bigquery", "databricks"])
    def test_malformed_notice_rejected(self, platform, notice):
        errors, _ = evaluate_vendor_conditions(complete(platform, vendor_notice=notice))
        assert any("vendor_notice" in e for e in errors)

    def test_valid_notice_clears_warning(self):
        _, warnings = evaluate_vendor_conditions({"platform": "redshift", "plan_tier": "paid", "vendor_notice": NOTICE})
        assert warnings == []


class TestBigQuerySelfRun:
    def test_self_run_false_rejected(self):
        errors, _ = evaluate_vendor_conditions(complete("bigquery", self_run=False))
        assert len(errors) == 1
        assert "self_run is false" in errors[0]

    def test_self_run_true_accepted(self):
        assert evaluate_vendor_conditions(complete("bigquery")) == ([], [])

    def test_self_run_must_be_boolean(self):
        errors, _ = evaluate_vendor_conditions(complete("duckdb", self_run="yes"))
        assert any("self_run must be a boolean" in e for e in errors)


class TestMandatoryFieldsAfterCutoff:
    @pytest.mark.parametrize("platform", sorted(MANDATORY_VENDOR_PLATFORMS))
    @pytest.mark.parametrize("missing", ["plan_tier", "vendor_notice", "self_run"])
    def test_missing_field_refused(self, platform, missing):
        manifest = complete(platform, submitted_at=POST_CUTOFF)
        del manifest[missing]
        errors, _ = evaluate_vendor_conditions(manifest)
        assert len(errors) == 1
        assert missing in errors[0]
        assert VENDOR_CONDITIONS_MANDATORY_FROM in errors[0]
        assert vendor_publication_status(manifest) == VENDOR_STATUS_INCOMPLETE

    @pytest.mark.parametrize("platform", sorted(MANDATORY_VENDOR_PLATFORMS))
    def test_all_fields_missing_refused_with_one_message(self, platform):
        errors, _ = evaluate_vendor_conditions({"platform": platform, "submitted_at": POST_CUTOFF})
        assert len(errors) == 1
        assert all(field in errors[0] for field in ("plan_tier", "vendor_notice", "self_run"))

    def test_unspecified_tier_counts_as_missing(self):
        errors, _ = evaluate_vendor_conditions(complete("snowflake", plan_tier="unspecified", submitted_at=POST_CUTOFF))
        assert any("plan_tier" in e for e in errors)

    def test_self_run_false_refused_on_snowflake(self):
        errors, _ = evaluate_vendor_conditions(complete("snowflake", self_run=False, submitted_at=POST_CUTOFF))
        assert any("self_run" in e for e in errors)

    @pytest.mark.parametrize("platform", sorted(MANDATORY_VENDOR_PLATFORMS))
    def test_complete_post_cutoff_accepted(self, platform):
        manifest = complete(platform, submitted_at=POST_CUTOFF)
        assert evaluate_vendor_conditions(manifest) == ([], [])
        assert vendor_publication_status(manifest) == VENDOR_STATUS_EVIDENCE_BACKED

    def test_undated_manifest_is_never_exempt(self):
        errors, _ = evaluate_vendor_conditions({"platform": "bigquery"})
        assert errors
        assert vendor_publication_status({"platform": "bigquery"}) == VENDOR_STATUS_INCOMPLETE

    def test_unparseable_date_is_never_exempt(self):
        manifest = {"platform": "bigquery", "submitted_at": "last tuesday"}
        assert vendor_publication_status(manifest) == VENDOR_STATUS_INCOMPLETE

    def test_post_cutoff_submission_date_overrides_old_bundle_timestamp(self):
        manifest = {"platform": "snowflake", "submitted_at": POST_CUTOFF, BUNDLE_RUN_TIMESTAMP_KEY: PRE_CUTOFF}
        assert vendor_publication_status(manifest) == VENDOR_STATUS_INCOMPLETE

    @pytest.mark.parametrize(
        "stamp",
        [{"submission_tool_version": "benchbox/0.0.0"}, {"result_source": "community"}],
    )
    def test_submit_written_manifest_without_submitted_at_is_never_exempt(self, stamp):
        manifest = {"platform": "snowflake", BUNDLE_RUN_TIMESTAMP_KEY: PRE_CUTOFF, **stamp}
        assert vendor_publication_status(manifest) == VENDOR_STATUS_INCOMPLETE
        errors, _ = evaluate_vendor_conditions(manifest)
        assert errors

    def test_submit_written_manifest_with_pre_cutoff_submitted_at_is_pre_policy(self):
        manifest = {
            "platform": "snowflake",
            "submission_tool_version": "benchbox/0.0.0",
            "submitted_at": "2026-11-17T10:00:00+00:00",
            BUNDLE_RUN_TIMESTAMP_KEY: PRE_CUTOFF,
        }
        assert vendor_publication_status(manifest) == VENDOR_STATUS_PRE_POLICY

    def test_other_platforms_not_affected(self):
        manifest = {"platform": "duckdb", "submitted_at": POST_CUTOFF}
        assert evaluate_vendor_conditions(manifest) == ([], [])
        assert vendor_publication_status(manifest) == VENDOR_STATUS_NOT_APPLICABLE


class TestPrePolicyLabel:
    @pytest.mark.parametrize("platform", sorted(MANDATORY_VENDOR_PLATFORMS))
    def test_pre_cutoff_manifest_labelled_not_refused(self, platform):
        manifest = {"platform": platform, BUNDLE_RUN_TIMESTAMP_KEY: PRE_CUTOFF}
        errors, warnings = evaluate_vendor_conditions(manifest)
        assert errors == []
        assert len(warnings) == 1
        assert VENDOR_STATUS_PRE_POLICY in warnings[0]
        assert "published before vendor-condition checks" in warnings[0]
        assert vendor_publication_status(manifest) == VENDOR_STATUS_PRE_POLICY

    def test_pre_cutoff_submission_date_labelled(self):
        manifest = {"platform": "databricks", "submitted_at": "2026-11-17T23:59:59+00:00"}
        assert vendor_publication_status(manifest) == VENDOR_STATUS_PRE_POLICY

    def test_pre_cutoff_manifest_with_fields_is_evidence_backed(self):
        manifest = complete("snowflake", **{BUNDLE_RUN_TIMESTAMP_KEY: PRE_CUTOFF})
        assert evaluate_vendor_conditions(manifest) == ([], [])
        assert vendor_publication_status(manifest) == VENDOR_STATUS_EVIDENCE_BACKED

    def test_pre_cutoff_does_not_excuse_free_tier_or_preview(self):
        manifest = {
            "platform": "databricks",
            "plan_tier": "free",
            BUNDLE_RUN_TIMESTAMP_KEY: PRE_CUTOFF,
            "preview_features": ["x"],
            "vendor_permission": "email",
        }
        errors, _ = evaluate_vendor_conditions(manifest)
        assert any("Beta" in e for e in errors)
