from __future__ import annotations

RESULT_SOURCES: tuple[str, ...] = ("internal", "community", "vendor")

DEFAULT_RESULT_SOURCE = "internal"

SOURCE_TO_TRUST_LABEL: dict[str, str] = {
    "internal": "maintainer-run",
    "community": "community-submission",
    "vendor": "vendor-supplied",
}

TRUST_LABELS: tuple[str, ...] = (
    "maintainer-run",
    "community-submission",
    "vendor-supplied",
    "verified",
)

TRUST_LABEL_DISPLAY: dict[str, str] = {
    "maintainer-run": "Maintainer Run",
    "community-submission": "Community Submission",
    "vendor-supplied": "Vendor Supplied",
    "verified": "Verified",
}

TRUST_LABEL_TO_VISIBILITY: dict[str, str] = {
    "maintainer-run": "public-curated",
    "community-submission": "public-self-reported",
    "vendor-supplied": "public-vendor-reported",
    "verified": "public-verified",
}

RANKING_ELIGIBLE_VISIBILITIES: frozenset[str] = frozenset(
    {"public-curated", "public-verified", "public-vendor-reported"}
)


FUNDING_SOURCES: tuple[str, ...] = (
    "employer",
    "personal",
    "free-trial",
    "vendor-sponsored",
    "grant",
    "unspecified",
)

DEFAULT_FUNDING = "unspecified"

PLAN_TIERS: tuple[str, ...] = ("paid", "free", "trial", "unspecified")

VENDOR_PROVENANCE_FIELDS: tuple[str, ...] = (
    "plan_tier",
    "preview_features",
    "vendor_permission",
    "vendor_notice",
    "self_run",
)


def _normalize_token(value: object) -> str:
    return str(value).strip().lower() if value is not None else ""


def normalize_source(value: object) -> str:
    token = _normalize_token(value)
    return token if token in SOURCE_TO_TRUST_LABEL else DEFAULT_RESULT_SOURCE


def is_valid_source(value: object) -> bool:
    return _normalize_token(value) in SOURCE_TO_TRUST_LABEL


def normalize_funding(value: object) -> str:
    token = _normalize_token(value)
    return token if token in FUNDING_SOURCES else DEFAULT_FUNDING


def is_valid_funding(value: object) -> bool:
    return _normalize_token(value) in FUNDING_SOURCES


def trust_label_for_source(source: object) -> str:
    return SOURCE_TO_TRUST_LABEL[normalize_source(source)]


def visibility_for_label(trust_label: object) -> str:
    token = _normalize_token(trust_label)
    return TRUST_LABEL_TO_VISIBILITY.get(token, "public-self-reported")


def display_for_label(trust_label: object) -> str:
    token = _normalize_token(trust_label)
    return TRUST_LABEL_DISPLAY.get(token, token)


def is_ranking_eligible_visibility(visibility: object) -> bool:
    return _normalize_token(visibility) in RANKING_ELIGIBLE_VISIBILITIES


__all__ = [
    "RESULT_SOURCES",
    "DEFAULT_RESULT_SOURCE",
    "SOURCE_TO_TRUST_LABEL",
    "TRUST_LABELS",
    "TRUST_LABEL_DISPLAY",
    "TRUST_LABEL_TO_VISIBILITY",
    "RANKING_ELIGIBLE_VISIBILITIES",
    "FUNDING_SOURCES",
    "DEFAULT_FUNDING",
    "PLAN_TIERS",
    "VENDOR_PROVENANCE_FIELDS",
    "normalize_source",
    "is_valid_source",
    "normalize_funding",
    "is_valid_funding",
    "trust_label_for_source",
    "visibility_for_label",
    "display_for_label",
    "is_ranking_eligible_visibility",
]
