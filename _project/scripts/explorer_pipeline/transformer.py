from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import logging
import math
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

from _project.scripts.explorer_pipeline.models import (
    KNOWN_DEFECT_RANKING_EXCLUSION,
    THROUGHPUT_PHASE,
    BasisAvailability,
    BundleContainerBlock,
    BundleDocument,
    BundleLogicalProfile,
    BundlePlatformRuntime,
    BundleTuningBlock,
    DetailResult,
    ExplorerEnvironment,
    ManifestEntry,
    PercentileStats,
    QueryDisplayTiming,
    QueryTiming,
    _platform_id,
    canonical_benchmark_slug,
    canonical_phase,
    ranking_exclusion_reason,
    timing_eligibility,
)
from benchbox.core.benchmark_registry import get_benchmark_support_status
from benchbox.core.cost.models import CostScope, CostStatus, DeploymentMetadata, NormalizedCost
from benchbox.core.cost.pricing import PRICING_VERSION
from benchbox.core.results.anonymization import AnonymizationManager, find_public_path_leaks
from benchbox.core.results.canonical_json import canonical_json_bytes
from benchbox.core.results.schema_policy import EXPLORER_INPUT_SCHEMA_POLICY, result_schema_version_value
from benchbox.core.results.status import bundle_failed_query_count, bundle_non_clean_reason, normalize_validation_status
from benchbox.core.tuning.modes import is_canonical_mode
from benchbox.validation.bundle import (
    APPLIED_COMPANION_MAX_BYTES,
    APPLIED_RECEIPT_MAX_ENTRIES,
    COMPANION_SUFFIXES,
    OVERRIDE_SUFFIX,
    accepted_override_rules,
)

logger = logging.getLogger(__name__)

_UTC = _dt.timezone.utc
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_TIMESTAMP_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})?$")


CLOSED_CPU_FAMILIES: frozenset[str] = frozenset(
    {
        "apple_silicon",
        "graviton",
        "intel_xeon",
        "intel_core",
        "amd_epyc",
        "amd_ryzen",
        "ampere_altra",
        "arm_neoverse",
        "unknown",
    }
)

_CPU_FAMILY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bapple\s+(?:m\d|a\d)", re.IGNORECASE), "apple_silicon"),
    (re.compile(r"\bgraviton", re.IGNORECASE), "graviton"),
    (re.compile(r"\bxeon\b", re.IGNORECASE), "intel_xeon"),
    (re.compile(r"\b(?:intel.*core|core\(tm\))\b", re.IGNORECASE), "intel_core"),
    (re.compile(r"\bepyc\b", re.IGNORECASE), "amd_epyc"),
    (re.compile(r"\b(?:ryzen|threadripper)\b", re.IGNORECASE), "amd_ryzen"),
    (re.compile(r"\b(?:ampere|altra)\b", re.IGNORECASE), "ampere_altra"),
    (re.compile(r"\bneoverse\b", re.IGNORECASE), "arm_neoverse"),
)


def normalize_cpu_family(raw_model: str | None) -> str | None:
    if raw_model is None:
        return None
    cleaned = raw_model.strip()
    if not cleaned:
        return None
    for pattern, family in _CPU_FAMILY_PATTERNS:
        if pattern.search(cleaned):
            return family
    return "unknown"


class CompanionPrivacyError(Exception):
    pass


def _companion_path(bundle_path: Path, suffix: str) -> Path:
    return bundle_path.with_name(f"{bundle_path.stem}{suffix}")


def _redact_applied_dropped(payload: dict[str, Any], key: str = "dropped") -> None:
    dropped = payload.get(key)
    if dropped:
        count = len(dropped) if isinstance(dropped, list) else 1
        payload[key] = [{"redacted": True} for _ in range(count)]


def _sanitize_applied_drift_check(drift_check: Any) -> None:
    if not isinstance(drift_check, dict):
        return
    removed = False
    for key in ("errors", "warnings", "configuration_mismatches", "missing_tables", "extra_tables"):
        if key in drift_check:
            drift_check.pop(key, None)
            removed = True
    if removed:
        drift_check["drift_redacted"] = True


_REDACTED_APPLIED_SHAPE: dict[str, Any] = {"redacted": True, "reason": "unexpected_shape"}


def _sanitize_applied_entry(entry: dict[str, Any]) -> None:
    for key in ("statement", "diff", "detail", "evidence", "table", "name", "expected_columns", "observed_columns"):
        entry.pop(key, None)
    if "reason" in entry:
        entry.pop("reason", None)
        entry["reason_redacted"] = True
    entry["statement_redacted"] = True


def _sanitize_applied_observed(observed: dict[str, Any]) -> None:
    for key in ("table", "name", "columns", "evidence"):
        observed.pop(key, None)
    observed["redacted"] = True


def _sanitized_applied_container(value: Any, sanitize_item: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, list):
        return dict(_REDACTED_APPLIED_SHAPE)
    sanitized: list[Any] = []
    for item in value:
        if not isinstance(item, dict):
            sanitized.append(dict(_REDACTED_APPLIED_SHAPE))
            continue
        sanitize_item(item)
        sanitized.append(item)
    return sanitized


def _sanitize_applied_receipt(receipt: Any) -> Any:
    if receipt is None:
        return None
    if not isinstance(receipt, dict):
        return dict(_REDACTED_APPLIED_SHAPE)
    if "error" in receipt:
        receipt.pop("error", None)
        receipt["error_redacted"] = True
    if "entries" in receipt:
        receipt["entries"] = _sanitized_applied_container(receipt["entries"], _sanitize_applied_entry)
    if "observed" in receipt:
        receipt["observed"] = _sanitized_applied_container(receipt["observed"], _sanitize_applied_observed)
    _redact_applied_dropped(receipt)
    return receipt


def _sanitize_applied_companion(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = copy.deepcopy(payload)
    statements = sanitized.get("statements")
    if isinstance(statements, list):
        for entry in statements:
            if not isinstance(entry, dict):
                continue
            entry.pop("statement", None)
            entry.pop("error", None)
            entry.pop("table", None)
            entry["statement_redacted"] = True
    _redact_applied_dropped(sanitized)
    _sanitize_applied_drift_check(sanitized.get("drift_check"))
    if "receipt" in sanitized:
        sanitized["receipt"] = _sanitize_applied_receipt(sanitized["receipt"])
    return sanitized


def _public_companion_bytes(
    bundle_path: Path,
    suffix: str,
    anonymizer: AnonymizationManager,
) -> bytes | None:
    if suffix not in COMPANION_SUFFIXES:
        raise ValueError(f"unsupported explorer companion suffix: {suffix}")

    source = _companion_path(bundle_path, suffix)
    if not source.exists():
        return None
    if source.is_symlink() or not source.is_file():
        logger.warning("Skipping non-regular companion %s", source)
        return None
    try:
        if suffix == ".applied.json" and source.stat().st_size > APPLIED_COMPANION_MAX_BYTES:
            logger.warning("Skipping oversized applied companion %s", source)
            return None
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("Skipping malformed companion %s - %s: %s", source, type(exc).__name__, exc)
        return None

    if not isinstance(payload, dict):
        logger.warning("Skipping non-object companion %s", source)
        return None

    if suffix == ".tuning.json":
        public_payload = anonymizer.anonymize_tuning_payload(payload)
    elif suffix == ".applied.json":
        public_payload = _sanitize_applied_companion(anonymizer.anonymize_result_payload(payload))
    else:
        public_payload = anonymizer.anonymize_result_payload(payload)

    leaks = find_public_path_leaks(public_payload)
    if leaks:
        raise CompanionPrivacyError(
            f"public {suffix.removesuffix('.json').lstrip('.')} privacy check failed for fields: "
            + ", ".join(sorted(set(leaks)))
        )
    return canonical_json_bytes(public_payload)


_PASS_STATUSES = {"SUCCESS", "PASS", "pass", "success"}
_ALLOWED_EXECUTION_RUN_TYPES: frozenset[str] = frozenset({"measurement", "warmup"})
_COST_MODEL_SOURCE = "benchbox.core.cost.pricing"
_COST_SCOPES: frozenset[str] = frozenset({"compute_only", "compute_plus_storage"})
_COST_STATUSES: frozenset[str] = frozenset({"normalized", "not_applicable_local", "unavailable"})
_KNOWN_LOGICAL_QUERY_COUNTS: dict[str, int] = {
    "tpch": 22,
    "tpch_skew": 22,
    "tpchavoc": 22,
    "tpcds": 99,
    "ssb": 13,
    "star_schema": 13,
    "clickbench": 43,
}


def _load_bundle(bundle_path: Path) -> tuple[dict[str, Any], bytes]:
    raw = bundle_path.read_bytes()
    data = json.loads(raw)
    _ensure_explorer_input_schema(data)
    return data, raw


def _parse_bundle(data: dict[str, Any]) -> BundleDocument:
    return BundleDocument.model_validate(data)


def _ensure_explorer_input_schema(data: dict[str, Any]) -> None:
    decision = EXPLORER_INPUT_SCHEMA_POLICY.evaluate(result_schema_version_value(data))
    if not decision.accepted:
        raise ValueError(decision.error_message())


def _sha256_prefix(raw: bytes, length: int = 8) -> str:
    return hashlib.sha256(raw).hexdigest()[:length]


def _utc_run_date_from_timestamp(timestamp: object) -> str:
    if not isinstance(timestamp, str):
        raise ValueError(f"run.timestamp must be a string, got {timestamp!r}")
    if _DATE_RE.fullmatch(timestamp):
        try:
            return _dt.date.fromisoformat(timestamp).isoformat()
        except ValueError as exc:
            raise ValueError(f"invalid run.timestamp: {timestamp!r}") from exc
    if not _TIMESTAMP_RE.fullmatch(timestamp):
        raise ValueError(f"invalid run.timestamp: {timestamp!r}")
    try:
        parsed = _dt.datetime.fromisoformat(f"{timestamp[:-1]}+00:00" if timestamp.endswith("Z") else timestamp)
    except ValueError as exc:
        raise ValueError(f"invalid run.timestamp: {timestamp!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_UTC)
    return parsed.astimezone(_UTC).date().isoformat()


def _run_date_from_timestamp(timestamp: object) -> str:
    _utc_run_date_from_timestamp(timestamp)
    assert isinstance(timestamp, str)
    return timestamp[:10].replace("-", "")


def _driver_version(bundle: BundleDocument) -> str | None:
    execution = bundle.execution
    is_duckdb = str(bundle.platform.name).lower() == "duckdb"
    if is_duckdb:
        for key in (
            "driver_version_resolved",
            "driver_version_requested",
            "driver_resolved_version",
            "driver_requested_version",
        ):
            val = getattr(execution, key)
            if val and isinstance(val, str):
                return val
        client_version = bundle.platform.client_version
        if client_version and isinstance(client_version, str) and client_version != "unknown":
            return client_version
    for key in (
        "driver_version_actual",
        "driver_version_resolved",
        "driver_version_requested",
        "driver_actual_version",
        "driver_resolved_version",
        "driver_requested_version",
    ):
        val = getattr(execution, key)
        if val and isinstance(val, str):
            return val
    return None


def _tpc_metric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _power_score(bundle: BundleDocument) -> float | None:
    return _tpc_metric(bundle.summary.tpc_metrics.power_at_size)


def _throughput_phase_clean(bundle: BundleDocument) -> bool:
    block = bundle.phases.get("throughput_test")
    if block is None or str(block.status or "").upper() != "COMPLETED":
        return False
    streams = block.stream_results
    if not isinstance(streams, list) or not streams:
        return False
    if not all(isinstance(stream, dict) and stream.get("success") is True for stream in streams):
        return False
    return not block.errors and not block.outstanding_work


def _throughput_at_size(bundle: BundleDocument) -> float | None:
    if not _throughput_phase_clean(bundle):
        return None
    return _tpc_metric(bundle.summary.tpc_metrics.throughput_at_size)


def _stream_count(bundle: BundleDocument, test_type: str | None) -> int | None:
    if canonical_phase(test_type) != THROUGHPUT_PHASE:
        return None
    block = bundle.phases.get("throughput_test")
    streams = block.stream_results if block is not None else None
    return len(streams) if isinstance(streams, list) and streams else None


def _geomean_ms(bundle: BundleDocument) -> float | None:
    values: list[float] = []
    for q in bundle.queries:
        if q.run_type is not None and q.run_type != "measurement":
            continue
        duration_ms_raw = q.ms if q.ms is not None else q.execution_time_ms
        if duration_ms_raw is None:
            continue
        try:
            ms = float(duration_ms_raw)
        except (TypeError, ValueError):
            continue
        if ms > 0:
            values.append(ms)
    if not values:
        return None
    return math.exp(sum(math.log(v) for v in values) / len(values))


_FUNDING_SOURCES = ("employer", "personal", "free-trial", "vendor-sponsored", "grant", "unspecified")


def _funding(bundle: BundleDocument) -> str:
    token = str(bundle.provenance.funding or "").strip().lower()
    return token if token in _FUNDING_SOURCES else "unspecified"


def _platform_version(bundle: BundleDocument) -> str | None:
    val = bundle.platform.version
    return str(val) if val and val != "unknown" else None


_EXECUTION_MODES = frozenset({"sql", "dataframe"})


def _execution_mode(bundle: BundleDocument) -> str | None:
    candidates: list[Any] = [
        bundle.config.execution_mode,
        bundle.platform.config.execution_mode,
        bundle.config.mode,
        bundle.execution.execution_mode,
    ]
    for node in candidates:
        if node and str(node).lower() in _EXECUTION_MODES:
            return str(node).lower()
    return None


def _tuning_mode(bundle: BundleDocument) -> str | None:
    config = bundle.config
    if config.tuning_mode and is_canonical_mode(str(config.tuning_mode)):
        return str(config.tuning_mode)
    execution = bundle.execution
    if execution.tuning_mode and is_canonical_mode(str(execution.tuning_mode)):
        return str(execution.tuning_mode)
    return None


def _tuning_hash(bundle: BundleDocument) -> str | None:
    mode = _tuning_mode(bundle)
    config = bundle.config
    tuning_detail: dict[str, Any] | None = None
    raw_detail = config.tuning_config or config.tuning
    if isinstance(raw_detail, dict):
        tuning_detail = raw_detail
    if mode is None and tuning_detail is None:
        return None
    payload = json.dumps({"mode": mode, "detail": tuning_detail}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:8]


def _tuning_summary(bundle: BundleDocument) -> BundleTuningBlock:
    return bundle.platform.tuning


def _requested_config_hash(bundle: BundleDocument) -> str | None:
    summary = _tuning_summary(bundle)
    return summary.requested_config_hash


def _applied_ledger_hash(bundle: BundleDocument) -> str | None:
    summary = _tuning_summary(bundle)
    return summary.applied_ledger_hash


def _tuning_validation_status(bundle: BundleDocument) -> str | None:
    summary = _tuning_summary(bundle)
    return summary.validation_status


def _has_requested_tuning(bundle: BundleDocument | None) -> bool:
    if bundle is None:
        return False
    return bool(bundle.platform.tuning.requested)


def _inline_applied_receipt(bundle: BundleDocument | None) -> Any:
    if bundle is None:
        return None
    return bundle.platform.tuning.applied.receipt


def _companion_applied_receipt(bundle_path: Path) -> tuple[Any, bool]:
    companion = bundle_path.with_name(f"{bundle_path.stem}.applied.json")
    try:
        companion_size = companion.stat().st_size
    except (OSError, ValueError):
        return None, False
    if companion_size > APPLIED_COMPANION_MAX_BYTES:
        marker = json.dumps(
            {
                "entries": [],
                "original_byte_count": companion_size,
                "truncated": True,
                "truncation_reason": "byte_limit",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return marker, True
    try:
        payload = json.loads(companion.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, False
    if not isinstance(payload, dict):
        return None, False
    return payload.get("receipt"), False


def _override_display(bundle_path: Path) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "override_rules": [],
        "override_evidence": None,
        "override_approver": None,
        "override_expires": None,
    }
    accepted, _errors = accepted_override_rules(bundle_path)
    if not accepted:
        return empty
    try:
        payload = json.loads(bundle_path.with_name(f"{bundle_path.stem}{OVERRIDE_SUFFIX}").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(payload, dict):
        return empty
    return {
        "override_rules": sorted(accepted),
        "override_evidence": payload.get("evidence") if isinstance(payload.get("evidence"), str) else None,
        "override_approver": payload.get("approver") if isinstance(payload.get("approver"), str) else None,
        "override_expires": payload.get("expires") if isinstance(payload.get("expires"), str) else None,
    }


def _sidecar_known_defects(bundle_path: Path) -> list[str]:
    manifest_path = bundle_path.parent / f"{bundle_path.stem}.manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    recorded = payload.get("known_defects")
    if not isinstance(recorded, list):
        return []
    return [item for item in recorded if isinstance(item, str) and item]


def _applied_receipt(bundle_path: Path, bundle: BundleDocument | None = None) -> str | None:
    receipt = _inline_applied_receipt(bundle)
    if receipt is None:
        receipt, already_serialized = _companion_applied_receipt(bundle_path)
        if already_serialized:
            return receipt
    if receipt is None:
        return None
    if isinstance(receipt, dict) and isinstance(receipt.get("entries"), list):
        entries = receipt["entries"]
        if len(entries) > APPLIED_RECEIPT_MAX_ENTRIES:
            receipt = {
                **receipt,
                "entries": entries[:APPLIED_RECEIPT_MAX_ENTRIES],
                "original_entry_count": len(entries),
                "truncated": True,
                "truncation_reason": "entry_limit",
            }
    try:
        return json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return None


def _tuning_policy_generation(bundle: BundleDocument) -> str | None:
    summary = _tuning_summary(bundle)
    return summary.tuning_policy_generation


def _template_evidence(bundle: BundleDocument) -> str | None:
    summary = _tuning_summary(bundle)
    return summary.template_evidence


def _logical_profile(bundle: BundleDocument) -> BundleLogicalProfile | None:
    return bundle.platform.tuning.logical_profile


def _physical_mechanisms(bundle: BundleDocument) -> list[str] | None:
    profile = _logical_profile(bundle)
    if profile is None:
        return None
    mechanisms = profile.physical_mechanisms
    if mechanisms is None:
        return []
    if not isinstance(mechanisms, list):
        return []
    return [str(item) for item in mechanisms]


def _physical_rendering_id(bundle: BundleDocument) -> str | None:
    profile = _logical_profile(bundle)
    if profile is None:
        return None
    val = profile.physical_rendering_id
    return str(val) if val else None


def _phase_executed(phase: Any) -> bool:
    if phase is None:
        return False
    evidence = phase.model_dump(exclude_unset=True)
    return bool(evidence) and str(evidence.get("status") or "").upper() != "NOT_RUN"


def _test_type(bundle: BundleDocument) -> str | None:
    if bundle.benchmark.test_type:
        return bundle.benchmark.test_type
    if _phase_executed(bundle.phases.get("power_test")):
        return "power"
    if _phase_executed(bundle.phases.get("throughput_test")):
        return "throughput"
    return None


def _validation_status(bundle: BundleDocument, raw: dict[str, Any]) -> str | None:
    val = bundle.summary.validation
    failed_queries = bundle_failed_query_count(raw)
    raw_summary = raw.get("summary")
    if raw_summary is not None and not isinstance(raw_summary, dict):
        return None
    if isinstance(val, str):
        normalized = normalize_validation_status(val)
        if failed_queries and normalized in {None, "passed"}:
            return "partial"
        if _translation_uncertain(raw, normalized):
            return "uncertain"
        return normalized
    if isinstance(val, dict):
        s = val.get("status")
        normalized = normalize_validation_status(s)
        if failed_queries and normalized in {None, "passed"}:
            return "partial"
        if _translation_uncertain(raw, normalized):
            return "uncertain"
        return normalized
    if failed_queries:
        return "partial"
    return None


def _translation_uncertain(data: dict[str, Any], validation_status: str | None) -> bool:
    if validation_status not in {None, "passed"}:
        return False
    reason = bundle_non_clean_reason(data)
    return bool(reason and reason.startswith("translation_status="))


def _unavailable_normalized_cost() -> NormalizedCost:
    return NormalizedCost(
        normalized_cost_usd=None,
        cost_model_version=PRICING_VERSION,
        cost_model_source=_COST_MODEL_SOURCE,
        cost_scope="compute_only",
        cost_status="unavailable",
        billing_unit="unknown",
        pricing_region="unknown",
    )


def _raw_normalized_cost_block(bundle: BundleDocument) -> dict[str, Any] | None:
    if bundle.normalized_cost is not None:
        return bundle.normalized_cost

    cost = bundle.cost
    if cost is None:
        return None

    for key in ("normalized_cost", "normalized"):
        raw = cost.get(key)
        if isinstance(raw, dict):
            return raw

    normalized_keys = {
        "normalized_cost_usd",
        "cost_model_version",
        "cost_model_source",
        "cost_scope",
        "cost_status",
    }
    if normalized_keys.intersection(cost):
        return cost

    return None


def _decimal_or_none(val: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(val)) if val is not None else None
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid normalized_cost_usd value: {val!r}") from exc
    if parsed is not None and not parsed.is_finite():
        raise ValueError(f"Invalid normalized_cost_usd value: {val!r}")
    return parsed


def _deployment_metadata(raw: Any) -> DeploymentMetadata:
    if not isinstance(raw, dict):
        return DeploymentMetadata()
    node_count = raw.get("node_count")
    return DeploymentMetadata(
        cloud_provider=raw.get("cloud_provider"),
        cloud_region=raw.get("cloud_region"),
        instance_type=raw.get("instance_type"),
        warehouse_size=raw.get("warehouse_size"),
        node_count=int(node_count) if node_count is not None else None,
        cluster_size=raw.get("cluster_size"),
        storage_format=raw.get("storage_format"),
        storage_tier=raw.get("storage_tier"),
    )


def _require_cost_choice(raw: dict[str, Any], key: str, choices: frozenset[str]) -> str:
    value = raw.get(key)
    if value not in choices:
        raise ValueError(f"Normalized cost field {key!r} must be one of {sorted(choices)}; got {value!r}")
    return str(value)


def _require_cost_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Normalized cost field {key!r} must be a non-empty string")
    return value


def _normalized_cost_from_block(raw: dict[str, Any]) -> NormalizedCost:
    return NormalizedCost(
        normalized_cost_usd=_decimal_or_none(raw.get("normalized_cost_usd")),
        cost_model_version=_require_cost_string(raw, "cost_model_version"),
        cost_model_source=_require_cost_string(raw, "cost_model_source"),
        cost_scope=cast(CostScope, _require_cost_choice(raw, "cost_scope", _COST_SCOPES)),
        cost_status=cast(CostStatus, _require_cost_choice(raw, "cost_status", _COST_STATUSES)),
        billing_unit=_require_cost_string(raw, "billing_unit"),
        pricing_region=_require_cost_string(raw, "pricing_region"),
        deployment=_deployment_metadata(raw.get("deployment")),
    )


def _normalized_cost(bundle: BundleDocument) -> NormalizedCost:
    raw = _raw_normalized_cost_block(bundle)
    if raw is None:
        return _unavailable_normalized_cost()
    return _normalized_cost_from_block(raw)


def _cost_usd_alias(cost: NormalizedCost) -> float | None:
    if cost.cost_usd is None:
        return None
    return float(cost.cost_usd)


def _string_or_none(raw: Any) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def _first_string(*values: Any) -> str | None:
    for value in values:
        parsed = _string_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _deployment_class_from_contract(bundle: BundleDocument) -> str | None:
    environment = bundle.environment
    runtime = environment.platform_runtime
    deployment = bundle.platform.deployment

    runtime_type = _string_or_none(runtime.runtime_type)
    deployment_type = _string_or_none(deployment.deployment_type)
    endpoint_class = _string_or_none(deployment.endpoint_class)

    runtime_key = runtime_type.lower() if runtime_type else None
    deployment_key = deployment_type.lower() if deployment_type else None
    endpoint_key = endpoint_class.lower() if endpoint_class else None

    if runtime_key in {"managed_cloud", "serverless"}:
        return "cloud"
    if deployment_key in {"managed_cloud", "serverless"} or endpoint_key == "cloud_endpoint":
        return "cloud"
    if runtime_key in {"local_process", "dataframe_process", "docker_container"}:
        return "local"
    if deployment_key == "embedded" or endpoint_key in {"embedded_process", "localhost_port"}:
        return "local"
    if endpoint_key == "remote_host":
        return "remote"
    if runtime_key == "unknown" or deployment_key == "unknown" or endpoint_key == "unknown":
        return "unavailable"
    if runtime_key or deployment_key or endpoint_key:
        return "unavailable"
    return None


def _has_normalized_environment_contract(bundle: BundleDocument) -> bool:
    environment = bundle.environment
    platform = bundle.platform
    runtime_present = environment.platform_runtime != BundlePlatformRuntime()
    container_present = environment.container != BundleContainerBlock()
    platform_present = any(
        getattr(platform, key) != type(getattr(platform, key))()
        for key in ("deployment", "cloud", "compute", "storage")
    )
    return runtime_present or container_present or platform_present


def _legacy_environment_facets_from_cost(normalized_cost: NormalizedCost) -> dict[str, str | None]:
    deployment = normalized_cost.deployment
    cloud_provider = _string_or_none(deployment.cloud_provider)
    cloud_region = _string_or_none(deployment.cloud_region)
    instance_or_warehouse = _first_string(
        deployment.instance_type,
        deployment.warehouse_size,
        deployment.cluster_size,
        deployment.node_count,
    )
    storage_format = _string_or_none(deployment.storage_format)

    deployment_class: str | None = None
    if normalized_cost.cost_status == "not_applicable_local":
        deployment_class = "local"
    elif cloud_provider or cloud_region:
        deployment_class = "cloud"
    elif normalized_cost.cost_status == "unavailable":
        deployment_class = "unavailable"

    return {
        "deployment_class": deployment_class,
        "cloud_provider": cloud_provider,
        "cloud_region": cloud_region,
        "instance_or_warehouse": instance_or_warehouse,
        "storage_format": storage_format,
    }


def _environment_facets(
    bundle: BundleDocument,
    *,
    normalized_cost: NormalizedCost | None = None,
) -> dict[str, str | None]:
    platform = bundle.platform
    cloud = platform.cloud
    compute = platform.compute
    storage = platform.storage

    facets = {
        "deployment_class": _deployment_class_from_contract(bundle),
        "cloud_provider": _string_or_none(cloud.provider),
        "cloud_region": _first_string(cloud.region, cloud.location),
        "instance_or_warehouse": _first_string(
            compute.node_type,
            compute.warehouse_size,
            compute.warehouse,
            compute.cluster_id,
            compute.cluster_name,
            compute.rpu,
            compute.serverless_slots,
            compute.worker_shape,
            compute.driver_shape,
        ),
        "storage_format": _string_or_none(storage.table_format),
    }
    if _has_normalized_environment_contract(bundle) or normalized_cost is None:
        return facets
    return _legacy_environment_facets_from_cost(normalized_cost)


def _compliance_class(bundle: BundleDocument) -> str | None:
    return bundle.benchmark.compliance_class


def _benchmark_support_status(benchmark_id: str) -> str | None:
    try:
        status = get_benchmark_support_status(benchmark_id)
    except (KeyError, ValueError):
        return None
    return status


def _phase_durations(bundle: BundleDocument) -> dict[str, float] | None:
    result: dict[str, float] = {}
    for phase_name, phase_data in bundle.phases.items():
        duration_ms = phase_data.duration_ms
        if duration_ms is not None:
            try:
                result[str(phase_name)] = float(duration_ms) / 1000.0
            except (TypeError, ValueError):
                pass
    return result if result else None


def _compute_percentile(values: list[float], p: float) -> float:
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    k = (p / 100.0) * (n - 1)
    f = int(math.floor(k))
    c = int(math.ceil(k))
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def _platform_percentile_stats(display_timings: list[QueryDisplayTiming]) -> PercentileStats | None:
    values = [dt.display_ms for dt in display_timings if dt.display_ms is not None and dt.display_ms > 0]
    if not values:
        return None
    return PercentileStats(
        p50=_compute_percentile(values, 50),
        p90=_compute_percentile(values, 90),
        p95=_compute_percentile(values, 95),
        p99=_compute_percentile(values, 99),
    )


def _query_timings(bundle: BundleDocument) -> list[QueryTiming]:
    timings: list[QueryTiming] = []
    for q in bundle.queries:
        if q.run_type is not None and q.run_type not in _ALLOWED_EXECUTION_RUN_TYPES:
            continue
        duration_ms_raw = q.ms if q.ms is not None else (q.execution_time_ms or 0.0)
        try:
            duration_ms = float(duration_ms_raw)
        except (TypeError, ValueError):
            duration_ms = 0.0
        status = "pass" if q.status in _PASS_STATUSES else "fail"
        timings.append(
            QueryTiming(
                query_id=q.query_id,
                duration_ms=duration_ms,
                status=status,
                run_type=q.run_type,
                iter=int(q.iter) if q.iter is not None else None,
                stream=int(q.stream) if q.stream is not None else None,
            )
        )
    return timings


def _query_display_ms(query_timings: list[QueryTiming]) -> tuple[float | None, int]:
    passing = [t for t in query_timings if t.status == "pass"]
    if not passing:
        return None, 0
    measurement = [t for t in passing if t.run_type == "measurement"]
    legacy_unlabelled = [t for t in passing if t.run_type is None]
    candidates = measurement if measurement else legacy_unlabelled
    if not candidates:
        return None, 0
    durations = sorted(t.duration_ms for t in candidates)
    mid = len(durations) // 2
    if len(durations) % 2 == 1:
        return durations[mid], len(candidates)
    return (durations[mid - 1] + durations[mid]) / 2.0, len(candidates)


def _build_display_timings(timings: list[QueryTiming]) -> list[QueryDisplayTiming]:
    seen: dict[str, list[QueryTiming]] = {}
    for t in timings:
        seen.setdefault(t.query_id, []).append(t)
    result: list[QueryDisplayTiming] = []
    for qid, qid_timings in seen.items():
        display_ms, sample_count = _query_display_ms(qid_timings)
        result.append(QueryDisplayTiming(query_id=qid, display_ms=display_ms, sample_count=sample_count))
    return result


def _compute_basis_availability(queries: list[QueryTiming]) -> BasisAvailability:
    warmup_passing = [q for q in queries if q.run_type == "warmup" and q.status == "pass"]
    has_warmup = len(warmup_passing) > 0
    warmup_status = "available" if has_warmup else "no_warmup_recorded"

    measurement_queries = [q for q in queries if q.run_type == "measurement" or q.run_type is None]
    query_pass_counts: dict[str, int] = {q.query_id: 0 for q in measurement_queries}
    for q in measurement_queries:
        if q.status == "pass":
            query_pass_counts[q.query_id] += 1

    if query_pass_counts:
        counts = list(query_pass_counts.values())
        count_freq = Counter(counts)
        nominal_count = sorted(count_freq.items(), key=lambda x: (x[1], x[0]), reverse=True)[0][0]
        varying_pass_queries = {qid: cnt for qid, cnt in sorted(query_pass_counts.items()) if cnt != nominal_count}
    else:
        nominal_count = 0
        varying_pass_queries = {}

    available_bases: list[str] = ["default"]
    unavailable_bases: dict[str, str] = {}

    if has_warmup:
        available_bases.append("warmup")
    else:
        unavailable_bases["warmup"] = "no_warmup_recorded"

    if nominal_count > 0:
        available_bases.append("all_warm")
        for p in range(1, nominal_count + 1):
            available_bases.append(f"warm_pass_{p}")
    else:
        unavailable_bases["all_warm"] = "no_measurement_executions"

    return BasisAvailability(
        has_warmup=has_warmup,
        measurement_pass_count=nominal_count,
        warmup_status=warmup_status,
        available_bases=available_bases,
        unavailable_bases=unavailable_bases,
        query_pass_counts=query_pass_counts,
        varying_pass_queries=varying_pass_queries,
    )


def _display_geomean_ms(display_timings: list[QueryDisplayTiming]) -> float | None:
    values = [dt.display_ms for dt in display_timings if dt.display_ms is not None and dt.display_ms > 0]
    if not values:
        return None
    return math.exp(sum(math.log(v) for v in values) / len(values))


def _summary_query_count(bundle: BundleDocument) -> int:
    total = bundle.summary.queries.total
    try:
        return int(total or 0)
    except (TypeError, ValueError):
        return 0


def _logical_query_count(bundle: BundleDocument, display_timings: list[QueryDisplayTiming]) -> int:
    dataframe_skip_total = _dataframe_skip_logical_query_count(bundle)
    if dataframe_skip_total is not None:
        return dataframe_skip_total

    raw_query_count = _summary_query_count(bundle)
    observed_query_count = len({timing.query_id for timing in display_timings if timing.query_id})
    if observed_query_count <= 0:
        return raw_query_count
    if raw_query_count <= observed_query_count:
        return raw_query_count or observed_query_count

    benchmark = str(bundle.benchmark.id)
    known_count = _KNOWN_LOGICAL_QUERY_COUNTS.get(benchmark)
    if known_count and observed_query_count <= known_count and raw_query_count % known_count == 0:
        return known_count

    if raw_query_count % observed_query_count == 0 and any(timing.sample_count > 1 for timing in display_timings):
        return observed_query_count

    return raw_query_count


def _dataframe_skip_logical_query_count(bundle: BundleDocument) -> int | None:
    best_total: int | None = None
    for query in bundle.queries:
        summary = query.dataframe_skip_summary
        if not isinstance(summary, dict):
            continue
        executed = _int_or_none(summary.get("executed_total"))
        skipped = _int_or_none(summary.get("skipped_total"))
        if executed is None or skipped is None:
            continue
        total = executed + skipped
        if total > 0 and (best_total is None or total > best_total):
            best_total = total
    return best_total


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _to_finite_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (ValueError, TypeError):
        return None


def _detail_environment(bundle: BundleDocument) -> ExplorerEnvironment:
    env = bundle.environment
    provided = dict(env.model_extra or {})
    for key in ("os", "arch", "cpu_count", "memory_gb", "python", "cpu_model", "cpu_identity_provenance"):
        if key in env.model_fields_set:
            provided[key] = getattr(env, key)
    for key in ("platform_runtime", "container", "client_link"):
        if key in env.model_fields_set:
            provided[key] = getattr(env, key).model_dump(exclude_unset=True)
    if "environment" in bundle.model_fields_set:
        raw_cpu = env.cpu_model
        cleaned_cpu = raw_cpu.strip() if isinstance(raw_cpu, str) else None
        if cleaned_cpu:
            provided["cpu_model"] = cleaned_cpu
            provided["cpu_family"] = normalize_cpu_family(cleaned_cpu)
        else:
            provided["cpu_model"] = None
            provided["cpu_family"] = None

    link = env.client_link
    overhead = link.statement_overhead_ms
    provided["client_region"] = _string_or_none(link.client_region)
    provided["client_cloud"] = _string_or_none(link.client_cloud)
    provided["link_status"] = _string_or_none(link.collection_status)
    provided["statement_overhead_min_ms"] = _to_finite_float_or_none(overhead.min)
    provided["statement_overhead_median_ms"] = _to_finite_float_or_none(overhead.median)
    return ExplorerEnvironment(**provided)


class BundleTransformer:
    def load_bundle(self, bundle_path: Path) -> dict[str, Any]:
        data, _ = _load_bundle(bundle_path)
        return data

    def load_bundle_full(self, bundle_path: Path) -> tuple[dict[str, Any], bytes]:
        return _load_bundle(bundle_path)

    def result_id_from_bundle(
        self,
        bundle_path: Path,
        data: dict[str, Any] | None = None,
        *,
        raw: bytes | None = None,
    ) -> str:
        if data is not None and raw is not None:
            _ensure_explorer_input_schema(data)
            bundle_data, file_raw = data, raw
        elif data is not None:
            _ensure_explorer_input_schema(data)
            bundle_data, file_raw = data, bundle_path.read_bytes()
        else:
            bundle_data, file_raw = _load_bundle(bundle_path)
        bundle = _parse_bundle(bundle_data)

        raw_benchmark = bundle_data.get("benchmark", {})
        raw_benchmark = raw_benchmark if isinstance(raw_benchmark, dict) else {}
        benchmark = bundle.benchmark.id
        platform = str(bundle.platform.name).lower().replace(" ", "-")
        scale_factor = raw_benchmark.get("scale_factor", 0.0)
        run_date = _run_date_from_timestamp(bundle.run.timestamp)
        sha_prefix = _sha256_prefix(file_raw)
        return f"{benchmark}-{platform}-sf{scale_factor}-{run_date}-{sha_prefix}"

    def to_manifest_entry(
        self,
        bundle_path: Path,
        *,
        trust_label: str = "maintainer-run",
        visibility: str = "public-curated",
        result_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> ManifestEntry:
        bundle_data = data if data is not None else self.load_bundle(bundle_path)
        _ensure_explorer_input_schema(bundle_data)
        bundle = _parse_bundle(bundle_data)
        rid = result_id or self.result_id_from_bundle(bundle_path, data=bundle_data)

        benchmark = bundle.benchmark.id
        scale_factor = float(bundle.benchmark.scale_factor)
        platform = str(bundle.platform.name)
        run_date = _utc_run_date_from_timestamp(bundle.run.timestamp)
        total_duration_s = float(bundle.run.total_duration_ms) / 1000.0

        query_count = _summary_query_count(bundle)
        failed_query_count = bundle_failed_query_count(bundle_data)

        timings = _query_timings(bundle)
        display_timings = _build_display_timings(timings)
        logical_query_count = _logical_query_count(bundle, display_timings)
        timing_contract = timing_eligibility(display_timings, logical_query_count)
        normalized_cost = _normalized_cost(bundle)
        environment_facets = _environment_facets(
            bundle,
            normalized_cost=normalized_cost if _raw_normalized_cost_block(bundle) is not None else None,
        )
        override = _override_display(bundle_path)
        test_type = _test_type(bundle)
        entry = ManifestEntry(
            result_id=rid,
            benchmark=benchmark,
            scale_factor=scale_factor,
            platform=platform,
            platform_id=_platform_id(platform),
            driver_version=_driver_version(bundle),
            run_date=run_date,
            power_score=_power_score(bundle),
            throughput_at_size=_throughput_at_size(bundle),
            stream_count=_stream_count(bundle, test_type),
            total_duration_s=total_duration_s,
            geomean_ms=_geomean_ms(bundle),
            display_geomean_ms=_display_geomean_ms(display_timings),
            query_count=int(query_count),
            logical_query_count=logical_query_count,
            has_display_timing=timing_contract.has_display_timing,
            valid_query_count=timing_contract.valid_query_count,
            missing_query_count=timing_contract.missing_query_count,
            zero_timing_count=timing_contract.zero_timing_count,
            display_exclusion_reason=timing_contract.display_exclusion_reason,
            comparison_exclusion_reason=timing_contract.comparison_exclusion_reason,
            trust_label=trust_label,
            visibility=visibility,
            funding=_funding(bundle),
            platform_version=_platform_version(bundle),
            execution_mode=_execution_mode(bundle),
            tuning_mode=_tuning_mode(bundle),
            tuning_hash=_tuning_hash(bundle),
            requested_config_hash=_requested_config_hash(bundle),
            applied_ledger_hash=_applied_ledger_hash(bundle),
            tuning_validation_status=_tuning_validation_status(bundle),
            applied_receipt=_applied_receipt(bundle_path, bundle),
            override_rules=override["override_rules"],
            override_evidence=override["override_evidence"],
            override_approver=override["override_approver"],
            override_expires=override["override_expires"],
            tuning_policy_generation=_tuning_policy_generation(bundle),
            template_evidence=_template_evidence(bundle),
            test_type=test_type,
            validation_status=_validation_status(bundle, bundle_data),
            failed_query_count=failed_query_count,
            benchmark_support_status=_benchmark_support_status(canonical_benchmark_slug(str(benchmark))),
            cost_usd=_cost_usd_alias(normalized_cost),
            normalized_cost=normalized_cost,
            deployment_class=environment_facets["deployment_class"],
            cloud_provider=environment_facets["cloud_provider"],
            cloud_region=environment_facets["cloud_region"],
            instance_or_warehouse=environment_facets["instance_or_warehouse"],
            storage_format=environment_facets["storage_format"],
            compliance_class=_compliance_class(bundle),
            basis_availability=_compute_basis_availability(timings),
        )
        reason = ranking_exclusion_reason(entry)
        if _sidecar_known_defects(bundle_path):
            reason = KNOWN_DEFECT_RANKING_EXCLUSION
        return entry.model_copy(update={"ranking_exclusion_reason": reason})

    def to_detail_result(
        self,
        bundle_path: Path,
        result_id: str,
        *,
        trust_label: str = "maintainer-run",
        visibility: str = "public-curated",
        bundle_download_url: str = "",
        data: dict[str, Any] | None = None,
    ) -> DetailResult:
        bundle_data = data if data is not None else self.load_bundle(bundle_path)
        _ensure_explorer_input_schema(bundle_data)
        bundle = _parse_bundle(bundle_data)

        benchmark = bundle.benchmark.id
        scale_factor = float(bundle.benchmark.scale_factor)
        platform = str(bundle.platform.name)
        run_date = _utc_run_date_from_timestamp(bundle.run.timestamp)
        total_duration_s = float(bundle.run.total_duration_ms) / 1000.0

        environment = _detail_environment(bundle)

        stem = bundle_path.stem
        has_plans = bundle_path.with_name(f"{stem}.plans.json").exists()
        has_tuning = _has_requested_tuning(bundle) or bundle_path.with_name(f"{stem}.tuning.json").exists()
        override = _override_display(bundle_path)

        timings = _query_timings(bundle)
        display_timings = _build_display_timings(timings)
        query_count = _summary_query_count(bundle)
        logical_query_count = _logical_query_count(bundle, display_timings)
        timing_contract = timing_eligibility(display_timings, logical_query_count)
        normalized_cost = _normalized_cost(bundle)
        test_type = _test_type(bundle)
        detail = DetailResult(
            result_id=result_id,
            benchmark=benchmark,
            scale_factor=scale_factor,
            platform=platform,
            platform_id=_platform_id(platform),
            driver_version=_driver_version(bundle),
            run_date=run_date,
            total_duration_s=total_duration_s,
            geomean_ms=_geomean_ms(bundle),
            display_geomean_ms=_display_geomean_ms(display_timings),
            power_score=_power_score(bundle),
            throughput_at_size=_throughput_at_size(bundle),
            stream_count=_stream_count(bundle, test_type),
            has_display_timing=timing_contract.has_display_timing,
            logical_query_count=logical_query_count,
            valid_query_count=timing_contract.valid_query_count,
            missing_query_count=timing_contract.missing_query_count,
            zero_timing_count=timing_contract.zero_timing_count,
            display_exclusion_reason=timing_contract.display_exclusion_reason,
            comparison_exclusion_reason=timing_contract.comparison_exclusion_reason,
            environment=environment,
            queries=timings,
            display_timings=display_timings,
            has_plans=has_plans,
            has_tuning=has_tuning,
            bundle_download_url=bundle_download_url,
            trust_label=trust_label,
            visibility=visibility,
            platform_version=_platform_version(bundle),
            execution_mode=_execution_mode(bundle),
            tuning_mode=_tuning_mode(bundle),
            tuning_hash=_tuning_hash(bundle),
            requested_config_hash=_requested_config_hash(bundle),
            applied_ledger_hash=_applied_ledger_hash(bundle),
            tuning_validation_status=_tuning_validation_status(bundle),
            applied_receipt=_applied_receipt(bundle_path, bundle),
            override_rules=override["override_rules"],
            override_evidence=override["override_evidence"],
            override_approver=override["override_approver"],
            override_expires=override["override_expires"],
            tuning_policy_generation=_tuning_policy_generation(bundle),
            template_evidence=_template_evidence(bundle),
            test_type=test_type,
            validation_status=_validation_status(bundle, bundle_data),
            failed_query_count=bundle_failed_query_count(bundle_data),
            cost_usd=_cost_usd_alias(normalized_cost),
            normalized_cost=normalized_cost,
            compliance_class=_compliance_class(bundle),
            phase_durations=_phase_durations(bundle),
            physical_mechanisms=_physical_mechanisms(bundle),
            physical_rendering_id=_physical_rendering_id(bundle),
            basis_availability=_compute_basis_availability(timings),
        )
        manifest_peer = ManifestEntry(
            result_id=result_id,
            benchmark=benchmark,
            scale_factor=scale_factor,
            platform=platform,
            platform_id=_platform_id(platform),
            driver_version=_driver_version(bundle),
            run_date=run_date,
            power_score=detail.power_score,
            throughput_at_size=detail.throughput_at_size,
            stream_count=detail.stream_count,
            test_type=detail.test_type,
            total_duration_s=total_duration_s,
            geomean_ms=detail.geomean_ms,
            display_geomean_ms=detail.display_geomean_ms,
            query_count=int(query_count),
            logical_query_count=logical_query_count,
            trust_label=trust_label,
            visibility=visibility,
            validation_status=detail.validation_status,
            failed_query_count=detail.failed_query_count,
        )
        reason = ranking_exclusion_reason(manifest_peer)
        if _sidecar_known_defects(bundle_path):
            reason = KNOWN_DEFECT_RANKING_EXCLUSION
        return detail.model_copy(update={"ranking_exclusion_reason": reason})


__all__ = ["BundleTransformer"]
