from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

PLAN_TIERS: tuple[str, ...] = ("paid", "free", "trial", "unspecified")

VENDOR_NOTICE_PLATFORMS: Mapping[str, str] = {
    "snowflake": "Snowflake Acceptable Use Policy section IV: share the results with Snowflake reasonably in advance of publishing",
    "redshift": "AWS Service Terms section 1.8: send the replication information to AWS",
    "athena": "AWS Service Terms section 1.8: send the replication information to AWS",
    "clickhouse-cloud": "ClickHouse Acceptable Use Policy section 2: send the replication information to ClickHouse",
    "motherduck": "MotherDuck MSA section 1.5: send the replication information to MotherDuck",
}

TIERED_PLATFORMS: Mapping[str, str] = {
    "snowflake": "Beta or Preview features need Snowflake's approval",
    "redshift": "Beta or Preview features need AWS's approval",
    "athena": "Beta or Preview features need AWS's approval",
    "clickhouse-cloud": "Beta or Preview features need ClickHouse's approval",
    "databricks": "Databricks Free Edition needs express permission; Beta or Preview features need Databricks' approval",
    "motherduck": "Free or trial use needs MotherDuck's permission",
}

SELF_RUN_PLATFORMS: Mapping[str, str] = {
    "bigquery": "BigQuery terms: publish only tests you ran yourself, not on behalf of a hyperscale cloud provider",
}

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def vendor_platform_key(platform: object) -> str:
    token = re.sub(r"[\s_]+", "-", str(platform or "").strip().lower())
    if token.startswith("clickhouse") and "cloud" in token:
        return "clickhouse-cloud"
    return token


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def evaluate_vendor_conditions(manifest: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    platform = vendor_platform_key(manifest.get("platform"))

    tier = manifest.get("plan_tier")
    if tier is not None and tier not in PLAN_TIERS:
        errors.append(f"Invalid manifest plan_tier {tier!r}: must be one of {sorted(PLAN_TIERS)}")
        tier = None

    features = manifest.get("preview_features")
    feature_list: list[str] = []
    if features is not None:
        if not isinstance(features, list) or any(not _nonempty_string(item) for item in features):
            errors.append("Manifest preview_features must be a list of non-empty feature name strings")
        else:
            feature_list = [str(item).strip() for item in features]

    permission = manifest.get("vendor_permission")
    if permission is not None and not _nonempty_string(permission):
        errors.append("Manifest vendor_permission must be a non-empty string naming the vendor's written permission")
        permission = None

    restricted = tier in {"free", "trial"} or bool(feature_list)
    if restricted and not permission:
        parts = []
        if tier in {"free", "trial"}:
            parts.append(f"{tier} plan tier")
        if feature_list:
            parts.append(f"preview features ({', '.join(feature_list)})")
        hint = TIERED_PLATFORMS.get(platform, "vendor terms restrict publishing results from these")
        errors.append(
            f"Results from {' and '.join(parts)} cannot be published without the vendor's written permission "
            f"(record it in vendor_permission): {hint}"
        )

    if platform in TIERED_PLATFORMS and tier in (None, "unspecified"):
        warnings.append(
            f"Manifest does not declare plan_tier for {platform}; free, trial and preview results cannot be "
            f"screened ({TIERED_PLATFORMS[platform]})"
        )

    if platform in VENDOR_NOTICE_PLATFORMS:
        notice = manifest.get("vendor_notice")
        if notice is None:
            warnings.append(f"Manifest has no vendor_notice for {platform}: {VENDOR_NOTICE_PLATFORMS[platform]}")
        elif (
            not isinstance(notice, Mapping)
            or not _nonempty_string(notice.get("reference"))
            or not isinstance(notice.get("sent_on"), str)
            or not _DATE_PATTERN.match(notice["sent_on"])
        ):
            errors.append(
                "Manifest vendor_notice must be an object with sent_on (YYYY-MM-DD) and a non-empty reference"
            )
    elif manifest.get("vendor_notice") is not None:
        notice = manifest["vendor_notice"]
        if not isinstance(notice, Mapping):
            errors.append("Manifest vendor_notice must be an object with sent_on (YYYY-MM-DD) and a reference")

    if platform in SELF_RUN_PLATFORMS:
        self_run = manifest.get("self_run")
        if self_run is False:
            errors.append(f"Manifest self_run is false: {SELF_RUN_PLATFORMS[platform]}")
        elif self_run is not True:
            warnings.append(f"Manifest does not attest self_run for {platform}: {SELF_RUN_PLATFORMS[platform]}")
    elif manifest.get("self_run") is not None and not isinstance(manifest.get("self_run"), bool):
        errors.append("Manifest self_run must be a boolean")

    return errors, warnings
