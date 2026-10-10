from __future__ import annotations

import datetime
import hashlib
import importlib.util
import json
import math
import re
import statistics
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


def _load_schema_policy_helpers():
    try:
        from benchbox.core.results.schema_policy import (
            PUBLIC_SUBMISSION_SCHEMA_POLICY,
            result_schema_version_value,
        )

        return PUBLIC_SUBMISSION_SCHEMA_POLICY, result_schema_version_value
    except ImportError:
        pass
    helper_path = Path(__file__).resolve().parents[1] / "core" / "results" / "schema_policy.py"
    spec = importlib.util.spec_from_file_location("_benchbox_schema_policy", helper_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load schema version policy from {helper_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.PUBLIC_SUBMISSION_SCHEMA_POLICY, module.result_schema_version_value


PUBLIC_SUBMISSION_SCHEMA_POLICY, result_schema_version_value = _load_schema_policy_helpers()


try:
    from benchbox.core.results.provenance import FUNDING_SOURCES, RESULT_SOURCES
except ImportError:  # pragma: no cover
    FUNDING_SOURCES = ("employer", "personal", "free-trial", "vendor-sponsored", "grant", "unspecified")
    RESULT_SOURCES = ("internal", "community", "vendor")


def _load_bundle_failed_query_count():
    helper_path = Path(__file__).resolve().parents[1] / "core" / "results" / "query_status.py"
    spec = importlib.util.spec_from_file_location("_benchbox_query_status", helper_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load failed-query policy from {helper_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.bundle_failed_query_count


bundle_failed_query_count = _load_bundle_failed_query_count()

SUBMISSION_NOTES_MAX_LEN = 500

KNOWN_DEFECT_IDS = frozenset(
    {
        "defective-macdbgen-addresses",
        "defective-windows-dsdgen-scale",
    }
)

APPLIED_RECEIPT_MAX_ENTRIES = 10_000
APPLIED_COMPANION_MAX_BYTES = 8 * 1024 * 1024


REQUIRED_TOP_KEYS = ("result_schema_version", "run", "benchmark", "platform", "summary", "queries")
REQUIRED_RUN_KEYS = {"id", "timestamp", "total_duration_ms"}
REQUIRED_BENCHMARK_KEYS = {"id", "scale_factor"}
REQUIRED_PLATFORM_KEYS = {"name"}
REQUIRED_QUERY_KEYS = {"id", "ms"}

ACCEPTED_VERSION_PREFIX = "2."
NUMERIC_SCHEMA_VERSION_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?$")
ROW_COUNT_VALIDATION_SCHEMA_VERSION = (2, 2)
ROW_COUNT_VALIDATION_MESSAGE_MAX_CHARS = 500
ROW_COUNT_VALIDATION_STATUSES = frozenset({"PASSED", "FAILED", "SKIPPED", "ERROR"})
ROW_COUNT_VALIDATION_FIELDS = frozenset({"status", "expected", "actual", "error", "warning"})
ROW_COUNT_VALIDATION_REQUIRED_FIELDS = frozenset({"status", "expected", "actual"})

OVERRIDE_SUFFIX = ".override.json"
COMPANION_SUFFIXES = (".plans.json", ".tuning.json", ".applied.json", OVERRIDE_SUFFIX)
SUBMISSION_MANIFEST_FILENAME = "submission-manifest.json"
SUBMISSION_MANIFEST_SUFFIX = ".manifest.json"
PUBLIC_CLEAN_VALIDATION_STATUS = "passed"
PUBLIC_MIRROR_ALLOWED_VALIDATION_STATUSES = frozenset({"passed", "partial", "not_run"})
PUBLIC_NON_CLEAN_TRANSLATION_STATUSES = {"fallback", "failed"}
CLI_REFUSED_COMPLIANCE_CLASSES = frozenset({"unofficial_nonstandard", "unofficial_subscale"})

CANONICAL_LOGICAL_QUERY_COUNTS: dict[str, int] = {
    "tpch": 22,
    "tpch_skew": 22,
    "tpchavoc": 22,
    "tpcds": 99,
    "ssb": 13,
    "star_schema": 13,
    "clickbench": 43,
}

_TPCH_CANONICAL_IDS = frozenset(str(i) for i in range(1, 23))
_TPCDS_CANONICAL_IDS = frozenset(str(i) for i in range(1, 100))
_SSB_CANONICAL_IDS = frozenset(
    {
        "1.1",
        "1.2",
        "1.3",
        "2.1",
        "2.2",
        "2.3",
        "3.1",
        "3.2",
        "3.3",
        "3.4",
        "4.1",
        "4.2",
        "4.3",
    }
)
_CLICKBENCH_CANONICAL_IDS = frozenset(str(i) for i in range(1, 44))

CANONICAL_LOGICAL_QUERY_IDS: dict[str, frozenset[str]] = {
    "tpch": _TPCH_CANONICAL_IDS,
    "tpch_skew": _TPCH_CANONICAL_IDS,
    "tpchavoc": _TPCH_CANONICAL_IDS,
    "tpcds": _TPCDS_CANONICAL_IDS,
    "ssb": _SSB_CANONICAL_IDS,
    "star_schema": _SSB_CANONICAL_IDS,
    "clickbench": _CLICKBENCH_CANONICAL_IDS,
}

TPCHAVOC_CANONICAL_VARIANTS = frozenset(f"{query}_v{variant}" for query in range(1, 23) for variant in range(1, 11))
TPCHAVOC_DOCUMENTED_SKIPS: dict[str, frozenset[str]] = {
    "datafusion": frozenset(
        [
            "12_v1",
            "14_v8",
            "16_v10",
            "16_v7",
            "17_v10",
            "17_v7",
            "1_v7",
            "4_v10",
            "4_v7",
            "7_v1",
            "8_v1",
            "9_v1",
        ]
    ),
    "lakesail": frozenset(
        [
            "10_v1",
            "11_v4",
            "11_v9",
            "12_v1",
            "14_v2",
            "14_v8",
            "16_v1",
            "16_v10",
            "16_v7",
            "17_v10",
            "17_v2",
            "17_v4",
            "17_v7",
            "1_v7",
            "1_v8",
            "2_v5",
            "2_v7",
            "3_v1",
            "4_v10",
            "4_v7",
            "5_v4",
            "6_v2",
            "7_v1",
            "8_v1",
            "9_v1",
        ]
    ),
    "clickhouse": frozenset(
        [
            "10_v1",
            "11_v4",
            "13_v8",
            "14_v8",
            "16_v1",
            "16_v4",
            "17_v10",
            "17_v7",
            "1_v10",
            "1_v7",
            "3_v1",
            "3_v10",
            "3_v9",
            "4_v10",
            "4_v7",
            "5_v1",
            "5_v10",
            "5_v4",
            "7_v1",
            "8_v1",
            "9_v1",
        ]
    ),
    "postgres": frozenset(
        [
            "10_v9",
            "11_v9",
            "13_v9",
            "1_v7",
            "5_v9",
            "7_v9",
            "9_v9",
        ]
    ),
    "snowflake": frozenset(
        [
            "1_v7",
            "2_v2",
        ]
    ),
    "databricks": frozenset(
        [
            "1_v7",
        ]
    ),
    "bigquery": frozenset(
        [
            "1_v7",
            "2_v2",
        ]
    ),
}


def _tpchavoc_documented_skips(platform_name: Any) -> frozenset[str]:
    if not isinstance(platform_name, str):
        return frozenset()
    platform = re.sub(r"[^a-z0-9]+", "-", platform_name.lower()).strip("-")
    if platform in {"clickhouse-local", "clickhouse-server", "clickhouse-cloud"}:
        return TPCHAVOC_DOCUMENTED_SKIPS["clickhouse"]
    if platform in {"pg-duckdb", "pg-mooncake", "timescaledb"}:
        return TPCHAVOC_DOCUMENTED_SKIPS["postgres"]
    return TPCHAVOC_DOCUMENTED_SKIPS.get(platform, frozenset())


def _tpchavoc_variant_has_usable_timing(query: dict) -> bool:
    try:
        ms = float(query.get("ms"))
    except (TypeError, ValueError):
        return False
    return math.isfinite(ms) and ms > 0


def _normalize_coverage_query_id(raw_id: Any) -> str | None:
    if not isinstance(raw_id, str):
        return None
    text = raw_id.strip()
    if not text:
        return None
    upper = text.upper()
    if upper.startswith("QUERY_"):
        text = text[6:]
    elif upper.startswith("QUERY"):
        text = text[5:]
    elif upper.startswith("Q") and len(text) > 1 and (text[1].isdigit() or text[1].islower()):
        text = text[1:]
    text = text.strip()
    if not text:
        return None
    if "." in text:
        base, _, ext = text.rpartition(".")
        if ext.isalpha():
            text = base.strip()
            if not text:
                return None
    match = re.fullmatch(r"(\d+)([A-Za-z]+)?", text)
    if match:
        digits, suffix = match.groups()
        return f"{digits}{suffix.lower() if suffix else ''}"
    return text


KNOWN_BENCHMARKS = {
    "tpch",
    "tpcds",
    "ssb",
    "star_schema",
    "clickbench",
    "nyctaxi",
    "flightdata",
    "tsbs-devops",
    "tsbs_devops",
    "h2odb",
    "amplab",
    "coffeeshop",
    "joinorder",
    "tpch_skew",
    "tpchavoc",
    "datavault",
    "tpcdi",
    "tpcds_obt",
    "read_primitives",
    "write_primitives",
    "metadata_primitives",
    "transaction_primitives",
    "ai_primitives",
    "vector_search",
}
KNOWN_PLATFORMS = {
    "duckdb",
    "datafusion",
    "ducklake",
    "polars",
    "sqlite",
    "motherduck",
    "clickhouse",
    "clickhouse-local",
    "clickhouse-server",
    "clickhouse-cloud",
    "snowflake",
    "databricks",
    "bigquery",
    "redshift",
    "athena",
    "firebolt",
    "postgresql",
    "cedardb",
    "timescaledb",
    "pg-duckdb",
    "pg_duckdb",
    "pg-mooncake",
    "pg_mooncake",
    "trino",
    "starburst",
    "presto",
    "influxdb",
    "questdb",
    "starrocks",
    "databend",
    "doris",
    "spark",
    "pyspark",
    "lakesail",
    "pandas",
    "cudf",
    "dask",
    "synapse",
    "fabric_dw",
    "fabric-lakehouse",
    "fabric-spark",
}

MAX_QUERY_DURATION_MS = 7_200_000
MAX_TOTAL_DURATION_MS = 86_400_000

NORMALIZED_COST_MODEL_SOURCE = "benchbox.core.cost.pricing"
NORMALIZED_COST_REQUIRED_KEYS = {
    "normalized_cost_usd",
    "cost_model_version",
    "cost_model_source",
    "cost_scope",
    "cost_status",
    "billing_unit",
    "pricing_region",
}
NORMALIZED_COST_SCOPES = {"compute_only", "compute_plus_storage"}
NORMALIZED_COST_STATUSES = {"normalized", "not_applicable_local", "unavailable"}

NORMALIZED_COST_BILLING_UNITS = frozenset(
    {
        "tib_scanned",
        "tb_scanned",
        "credit",
        "node_hour",
        "dbu",
        "dwu_hour",
        "cu_hour",
        "fbu",
        "instance_hour",
    }
)
DIRECT_COST_TOTAL_KEYS = ("total_usd", "total_cost")
TOP_LEVEL_DIRECT_COST_KEYS = ("cost_usd",)


class ValidationResult:
    def __init__(self, path: str) -> None:
        self.path = path
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.override_required: list[str] = []
        self.benchmark_id: str = "-"
        self.platform_name: str = "-"
        self.scale_factor: str = "-"

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def require_override(self, rule_id: str, msg: str) -> None:
        if rule_id not in {known_id for known_id, _, _ in RULES}:
            raise ValueError(
                f"unknown rubric rule {rule_id!r}: a typo here would create an "
                "override finding no committed artifact could ever satisfy"
            )
        self.warnings.append(msg)
        if rule_id not in self.override_required:
            self.override_required.append(rule_id)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


RULES_VERSION = "1"
RULES: tuple[tuple[str, str, str], ...] = (
    ("timing-plateau", "1", "warn-require-override"),
    ("scale-invariant", "1", "warn-require-override"),
    ("floor-outlier", "1", "info"),
    ("small-scale-floor", "1", "warn-require-override"),
)


OVERRIDE_REQUIRED_FIELDS = ("rules", "reason", "evidence", "expires", "approver")
OVERRIDE_OPTIONAL_FIELDS = ("bundles",)
OVERRIDE_RULE_ENTRY_FIELDS = ("rule", "rule_version")


def _override_artifact_path(bundle_path: str | Path) -> Path | None:
    try:
        candidate = Path(bundle_path)
    except (TypeError, ValueError):
        return None
    if not candidate.suffix == ".json":
        return None
    name = candidate.name
    if name.lower().endswith(OVERRIDE_SUFFIX):
        return None
    return candidate.with_name(f"{candidate.stem}{OVERRIDE_SUFFIX}")


def validate_override_document(payload: Any, *, bundle_stem: str) -> list[str]:
    if not isinstance(payload, dict):
        return ["override artifact must be a JSON object"]
    unknown = sorted(k for k in payload if k not in OVERRIDE_REQUIRED_FIELDS + OVERRIDE_OPTIONAL_FIELDS)
    errors = [f"override artifact has unknown fields: {unknown}"] if unknown else []
    missing = [k for k in OVERRIDE_REQUIRED_FIELDS if k not in payload]
    if missing:
        errors.append(f"override artifact missing fields: {missing}")
        return errors
    registry = {rule_id: (version, severity) for rule_id, version, severity in RULES}
    entries = payload["rules"]
    if not isinstance(entries, list) or not entries:
        errors.append("override artifact rules must be a non-empty list")
        return errors
    for index, entry in enumerate(entries):
        errors.extend(_validate_override_rule_entry(entry, index, registry))
    for key in ("reason", "evidence", "approver"):
        if not isinstance(payload[key], str) or not payload[key].strip():
            errors.append(f"override artifact {key} must be a non-empty string")
    if "bundles" in payload:
        bundles = payload["bundles"]
        if (
            not isinstance(bundles, list)
            or not bundles
            or any(not isinstance(b, str) or not b.strip() for b in bundles)
        ):
            errors.append("override artifact bundles must be a non-empty list of non-empty strings")
        elif bundle_stem not in bundles:
            errors.append(f"override artifact bundles does not contain its own stem {bundle_stem!r}")
    expires = payload["expires"]
    if expires == "single-batch":
        return errors
    if not isinstance(expires, str):
        errors.append("override artifact expires must be a YYYY-MM-DD date or 'single-batch'")
        return errors
    try:
        expiry = datetime.date.fromisoformat(expires)
    except ValueError:
        errors.append(f"override artifact expires {expires!r} is not a YYYY-MM-DD date or 'single-batch'")
        return errors
    if expiry < datetime.datetime.now(datetime.timezone.utc).date():
        errors.append(f"override artifact expired on {expires}")
    return errors


def _validate_override_rule_entry(entry: Any, index: int, registry: dict[str, tuple[str, str]]) -> list[str]:
    prefix = f"override artifact rules[{index}]"
    if not isinstance(entry, dict):
        return [f"{prefix} must be an object"]
    errors = []
    unknown_entry = sorted(k for k in entry if k not in OVERRIDE_RULE_ENTRY_FIELDS)
    if unknown_entry:
        errors.append(f"{prefix} has unknown fields: {unknown_entry}")
    if sorted(entry) != ["rule", "rule_version"]:
        return errors + [f"{prefix} must hold exactly rule and rule_version"]
    rule, version = entry["rule"], entry["rule_version"]
    if rule not in registry:
        errors.append(f"{prefix} rule {rule!r} is not a known rubric rule")
    elif registry[rule][1] != "warn-require-override":
        errors.append(
            f"{prefix} rule {rule!r} has severity {registry[rule][1]!r}, "
            "only warn-require-override rules are overridable"
        )
    elif version != registry[rule][0]:
        errors.append(
            f"{prefix} rule_version {version!r} does not match registry version "
            f"{registry[rule][0]!r}; re-review against the current rule"
        )
    return errors


def accepted_override_rules(bundle_path: str | Path) -> tuple[set[str], list[str]]:
    artifact = _override_artifact_path(bundle_path)
    if artifact is None or not artifact.is_file():
        return set(), []
    try:
        payload = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return set(), [f"override artifact {artifact.name} is unreadable: {exc}"]
    errors = validate_override_document(payload, bundle_stem=artifact.name[: -len(OVERRIDE_SUFFIX)])
    if errors:
        return set(), [f"override artifact {artifact.name}: {message}" for message in errors]
    return {entry["rule"] for entry in payload["rules"]}, []


def unsatisfied_override_rules(results: list[ValidationResult]) -> dict[str, list[str]]:
    pending: dict[str, list[str]] = {}
    for vr in results:
        if not vr.override_required:
            continue
        accepted, _artifact_errors = accepted_override_rules(vr.path)
        remaining = [rule for rule in vr.override_required if rule not in accepted]
        if remaining:
            pending[vr.path] = remaining
    return pending


def override_artifact_errors(bundle_path: str | Path) -> list[str]:
    _accepted, errors = accepted_override_rules(bundle_path)
    return errors


def validation_failed(results: list[ValidationResult], *, strict_overrides: bool = True) -> bool:
    if any(not vr.ok for vr in results):
        return True
    return bool(strict_overrides and unsatisfied_override_rules(results))


def _capture_metadata(data: dict, vr: ValidationResult) -> None:
    bm = data.get("benchmark")
    if isinstance(bm, dict):
        vr.benchmark_id = bm.get("id", "-")
        sf = bm.get("scale_factor")
        if sf is not None:
            vr.scale_factor = str(sf)
    pl = data.get("platform")
    if isinstance(pl, dict):
        vr.platform_name = pl.get("name", "-")


def _validate_version(version: Any, vr: ValidationResult) -> None:
    if PUBLIC_SUBMISSION_SCHEMA_POLICY is not None:
        decision = PUBLIC_SUBMISSION_SCHEMA_POLICY.evaluate(version)
        if not decision.accepted:
            vr.error(decision.error_message())
        return

    if (
        not isinstance(version, str)
        or not NUMERIC_SCHEMA_VERSION_RE.fullmatch(version.strip())
        or not version.strip().startswith(ACCEPTED_VERSION_PREFIX)
    ):
        vr.error(
            f"Unsupported schema version for public submission schema policy: {version!r}. "
            "public submission schema policy accepts numeric schema version family 2.x."
        )


def _validate_run_section(run: Any, vr: ValidationResult) -> None:
    if not isinstance(run, dict):
        vr.error("'run' must be a dict")
        return
    missing_run = REQUIRED_RUN_KEYS - set(run.keys())
    if missing_run:
        vr.error(f"Missing keys in 'run': {sorted(missing_run)}")

    total_ms = run.get("total_duration_ms")
    if total_ms is None:
        return
    try:
        total_ms_f = float(total_ms)
    except (TypeError, ValueError):
        vr.error(f"Invalid total_duration_ms: {total_ms!r}")
        return
    if total_ms_f < 0:
        vr.error(f"Negative total_duration_ms: {total_ms_f}")
    elif total_ms_f > MAX_TOTAL_DURATION_MS:
        vr.warn(f"Unusually long total_duration_ms: {total_ms_f:.0f} (>{MAX_TOTAL_DURATION_MS})")


def _validate_benchmark_section(benchmark: Any, vr: ValidationResult) -> None:
    if not isinstance(benchmark, dict):
        vr.error("'benchmark' must be a dict")
        return
    missing_bm = REQUIRED_BENCHMARK_KEYS - set(benchmark.keys())
    if missing_bm:
        vr.error(f"Missing keys in 'benchmark': {sorted(missing_bm)}")

    bm_id = benchmark.get("id", "")
    if isinstance(bm_id, str) and bm_id and bm_id not in KNOWN_BENCHMARKS:
        vr.warn(f"Unknown benchmark id: {bm_id!r}")

    sf = benchmark.get("scale_factor")
    if sf is None:
        return
    try:
        sf_f = float(sf)
    except (TypeError, ValueError):
        vr.error(f"Invalid scale_factor: {sf!r}")
        return
    if sf_f <= 0:
        vr.error(f"scale_factor must be positive: {sf_f}")


TIMING_PLAUSIBILITY_HETEROGENEOUS_BENCHMARKS = frozenset(
    {
        "tpch",
        "tpch_skew",
        "tpchavoc",
        "tpcds",
        "ssb",
        "star_schema",
        "clickbench",
    }
)

PLATEAU_MAX_MIN_RATIO = 2.0
PLATEAU_MAX_CV = 0.15
PLATEAU_MIN_DISTINCT_QUERIES = 3
PLATEAU_MIN_PEAK_MS = 1000.0

SCALE_INVARIANCE_MIN_SPAN = 10.0
SCALE_INVARIANCE_MIN_GEOMEAN_RATIO = 1.5
SCALE_INVARIANCE_MIN_PER_QUERY_MEDIAN_RATIO = 1.3

FLOOR_OUTLIER_PEER_MULTIPLE = 3.0
FLOOR_OUTLIER_MIN_PEERS = 2

SMALL_SCALE_MAX_FACTOR = 0.1
SMALL_SCALE_FLOOR_MIN_MS = 2000.0
SMALL_SCALE_MAX_ROWS_LOADED = 1_000_000

_PASS_TIMING_STATUSES = frozenset({"SUCCESS", "PASS"})


def _measurement_ms_by_query(data: dict[str, Any]) -> dict[str, list[float]]:
    queries = data.get("queries")
    if not isinstance(queries, list):
        return {}
    grouped: dict[str, list[float]] = {}
    for q in queries:
        if not isinstance(q, dict):
            continue
        run_type = str(q.get("run_type") or "measurement").strip().lower()
        if run_type != "measurement":
            continue
        status = q.get("status")
        if not isinstance(status, str) or status.upper() not in _PASS_TIMING_STATUSES:
            continue
        qid = q.get("id")
        if not isinstance(qid, str) or not qid:
            continue
        try:
            ms = float(q.get("ms"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(ms) or ms < 1.0:
            continue
        grouped.setdefault(qid, []).append(ms)
    return grouped


def _bundle_benchmark_id(data: dict[str, Any]) -> str | None:
    benchmark = data.get("benchmark")
    if not isinstance(benchmark, dict):
        return None
    bm_id = benchmark.get("id")
    if not isinstance(bm_id, str) or not bm_id.strip():
        return None
    return bm_id.strip().casefold()


def _bundle_platform_key(data: dict[str, Any]) -> str | None:
    platform = data.get("platform")
    if not isinstance(platform, dict):
        return None
    name = platform.get("name")
    return str(name).strip().lower() if name is not None and str(name).strip() else None


def _bundle_scale_factor(data: dict[str, Any]) -> float | None:
    benchmark = data.get("benchmark")
    if not isinstance(benchmark, dict):
        return None
    try:
        sf = float(benchmark.get("scale_factor"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(sf) or sf <= 0:
        return None
    return sf


def _bundle_passed_validation(data: dict[str, Any]) -> bool:
    summary = data.get("summary")
    if not isinstance(summary, dict):
        return False
    return _normalize_status(summary.get("validation")) == PUBLIC_CLEAN_VALIDATION_STATUS


def _bundle_geomean_ms(data: dict[str, Any]) -> float | None:
    summary = data.get("summary")
    if not isinstance(summary, dict):
        return None
    timing = summary.get("timing")
    if not isinstance(timing, dict):
        return None
    try:
        value = float(timing.get("geometric_mean_ms"))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _bundle_rows_loaded(data: dict[str, Any]) -> int | None:
    summary = data.get("summary")
    if not isinstance(summary, dict):
        return None
    payload = summary.get("data")
    if not isinstance(payload, dict):
        return None
    rows = payload.get("rows_loaded")
    if isinstance(rows, bool):
        return None
    if isinstance(rows, float) and not math.isfinite(rows):
        return None
    try:
        value = int(rows)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if value >= 0 else None


def _warn_timing_plateau(data: dict[str, Any], vr: ValidationResult) -> None:
    bm_id = _bundle_benchmark_id(data)
    if bm_id not in TIMING_PLAUSIBILITY_HETEROGENEOUS_BENCHMARKS:
        return
    grouped = _measurement_ms_by_query(data)
    if len(grouped) < PLATEAU_MIN_DISTINCT_QUERIES:
        return
    means = [statistics.fmean(samples) for samples in grouped.values()]
    peak = max(means)
    floor = min(means)
    if peak < PLATEAU_MIN_PEAK_MS:
        return
    ratio = peak / floor
    cv = statistics.pstdev(means) / statistics.fmean(means)
    if ratio < PLATEAU_MAX_MIN_RATIO and cv < PLATEAU_MAX_CV:
        vr.require_override(
            "timing-plateau",
            f"timing-plateau: benchmark {bm_id!r} per-query means span "
            f"{floor:.0f}-{peak:.0f}ms (max/min {ratio:.2f}, CV {cv:.2f}); "
            "heterogeneous queries should vary more — check for fixed-overhead-dominated "
            "measurement (evidence: queries[].ms grouped by queries[].id)",
        )


def _warn_small_scale_floor(data: dict[str, Any], vr: ValidationResult) -> None:
    sf = _bundle_scale_factor(data)
    if sf is None or sf > SMALL_SCALE_MAX_FACTOR:
        return
    grouped = _measurement_ms_by_query(data)
    if not grouped:
        return
    floor = min(ms for samples in grouped.values() for ms in samples)
    if floor <= SMALL_SCALE_FLOOR_MIN_MS:
        return
    rows = _bundle_rows_loaded(data)
    if rows is not None and rows >= SMALL_SCALE_MAX_ROWS_LOADED:
        return
    rows_note = f"{rows} rows loaded" if rows is not None else "rows_loaded unreported"
    vr.require_override(
        "small-scale-floor",
        f"small-scale-floor: scale factor {sf:g} ({rows_note}) but fastest measurement is "
        f"{floor:.0f}ms — fixed overhead dominates; expected sub-second answers on this "
        "data volume (evidence: queries[].ms, summary.data.rows_loaded)",
    )


def _passed_cohorts(
    entries: list[tuple[dict[str, Any], ValidationResult]],
) -> tuple[
    dict[tuple[str, str], list[tuple[dict[str, Any], ValidationResult]]],
    dict[tuple[str, float], list[tuple[dict[str, Any], ValidationResult]]],
]:
    cohorts: dict[tuple[str, str], list[tuple[dict[str, Any], ValidationResult]]] = {}
    peers: dict[tuple[str, float], list[tuple[dict[str, Any], ValidationResult]]] = {}
    for data, vr in entries:
        if not _bundle_passed_validation(data):
            continue
        bm_id = _bundle_benchmark_id(data)
        if bm_id not in TIMING_PLAUSIBILITY_HETEROGENEOUS_BENCHMARKS:
            continue
        platform = _bundle_platform_key(data)
        sf = _bundle_scale_factor(data)
        if bm_id is None:
            continue
        if platform is not None:
            cohorts.setdefault((bm_id, platform), []).append((data, vr))
        if sf is not None:
            peers.setdefault((bm_id, sf), []).append((data, vr))
    return cohorts, peers


def _warn_scale_invariance(
    cohorts: dict[tuple[str, str], list[tuple[dict[str, Any], ValidationResult]]],
) -> None:
    for (bm_id, _platform), members in cohorts.items():
        scales = sorted({sf for data, _ in members if (sf := _bundle_scale_factor(data)) is not None})
        if len(scales) < 2 or scales[-1] / scales[0] < SCALE_INVARIANCE_MIN_SPAN:
            continue
        lo, hi = scales[0], scales[-1]
        lo_geo = [
            g for data, _ in members if _bundle_scale_factor(data) == lo and (g := _bundle_geomean_ms(data)) is not None
        ]
        hi_geo = [
            g for data, _ in members if _bundle_scale_factor(data) == hi and (g := _bundle_geomean_ms(data)) is not None
        ]
        lo_queries = _merged_query_samples(members, lo)
        hi_queries = _merged_query_samples(members, hi)
        shared = [qid for qid in lo_queries if qid in hi_queries]
        geo_ratio = statistics.fmean(hi_geo) / statistics.fmean(lo_geo) if lo_geo and hi_geo else None
        per_query_ratio = (
            statistics.median(statistics.fmean(hi_queries[qid]) / statistics.fmean(lo_queries[qid]) for qid in shared)
            if shared
            else None
        )
        tripped = (geo_ratio is not None and geo_ratio < SCALE_INVARIANCE_MIN_GEOMEAN_RATIO) or (
            per_query_ratio is not None and per_query_ratio < SCALE_INVARIANCE_MIN_PER_QUERY_MEDIAN_RATIO
        )
        if (geo_ratio is None and per_query_ratio is None) or not tripped:
            continue
        detail = (f"geomean x{geo_ratio:.2f}" if geo_ratio is not None else "geomean unevaluable") + (
            f", per-query median x{per_query_ratio:.2f}" if per_query_ratio is not None else ", per-query unevaluable"
        )
        span = hi / lo
        for data, vr in members:
            if _bundle_scale_factor(data) in (lo, hi):
                vr.require_override(
                    "scale-invariant",
                    f"scale-invariant: benchmark {bm_id!r} grows {span:g}x in scale "
                    f"but timings barely move ({detail}); check for result caching or "
                    "fixed-overhead-dominated measurement",
                )


def _merged_query_samples(
    members: list[tuple[dict[str, Any], ValidationResult]],
    scale: float,
) -> dict[str, list[float]]:
    merged: dict[str, list[float]] = {}
    for data, _vr in members:
        if _bundle_scale_factor(data) != scale:
            continue
        for qid, samples in _measurement_ms_by_query(data).items():
            merged.setdefault(qid, []).extend(samples)
    return merged


def _warn_floor_outlier(
    peers: dict[tuple[str, float], list[tuple[dict[str, Any], ValidationResult]]],
) -> None:
    for (bm_id, sf), members in peers.items():
        if len(members) < FLOOR_OUTLIER_MIN_PEERS + 1:
            continue
        floors = _peer_floor_timings(members)
        platforms = [_bundle_platform_key(data) for data, _ in members]
        for index, (_data, vr) in enumerate(members):
            own = floors.get(index)
            if own is None:
                continue
            own_platform = platforms[index]
            by_platform: dict[str, list[float]] = {}
            for other, value in floors.items():
                if other == index:
                    continue
                key = platforms[other]
                if key is None or key == own_platform:
                    continue
                by_platform.setdefault(key, []).append(value)
            if len(by_platform) < FLOOR_OUTLIER_MIN_PEERS:
                continue
            peer_median = statistics.median(statistics.median(values) for values in by_platform.values())
            if own > FLOOR_OUTLIER_PEER_MULTIPLE * peer_median:
                vr.warn(
                    f"floor-outlier (informational): fastest measurement {own:.0f}ms is "
                    f"more than {FLOOR_OUTLIER_PEER_MULTIPLE:g}x the peer median "
                    f"at benchmark {bm_id!r} scale {sf:g} — never refused on this signal alone"
                )


def _peer_floor_timings(
    members: list[tuple[dict[str, Any], ValidationResult]],
) -> dict[int, float]:
    floors: dict[int, float] = {}
    for index, (data, _vr) in enumerate(members):
        grouped = _measurement_ms_by_query(data)
        if grouped:
            floors[index] = min(ms for samples in grouped.values() for ms in samples)
    return floors


def _warn_cross_bundle_timing(entries: list[tuple[dict[str, Any], ValidationResult]]) -> None:
    cohorts, peers = _passed_cohorts(entries)
    _warn_scale_invariance(cohorts)
    _warn_floor_outlier(peers)


_CACHE_RECEIPT_PLATFORMS = frozenset({"snowflake", "redshift", "databricks"})


def _platform_records_cache_receipt(platform: dict) -> bool:
    name = platform.get("name")
    return isinstance(name, str) and name.lower().replace(" ", "-") in _CACHE_RECEIPT_PLATFORMS


def _validate_cache_control_section(
    platform: Any,
    vr: ValidationResult,
    *,
    allow_partial_validation: bool = False,
) -> None:
    if allow_partial_validation:
        return
    if not isinstance(platform, dict):
        return
    compute = platform.get("compute")
    if not isinstance(compute, dict):
        return
    receipt = compute.get("cache_control")
    if receipt is None:
        if compute.get("result_cache_enabled") and _platform_records_cache_receipt(platform):
            vr.error(
                "platform.compute declares an enabled result cache without a "
                "cache_control receipt; cached timings are not comparable evidence "
                "(rerun with the result cache disabled so session validation records a receipt)"
            )
        return
    if not isinstance(receipt, dict):
        vr.error("platform.compute.cache_control must be an object")
        return
    if receipt.get("validated") is not True:
        vr.error(
            "platform.compute.cache_control is unconfirmed (validated is not true); "
            "rerun with session cache validation before submitting as clean"
        )
        return
    if receipt.get("cache_disabled") is not True:
        vr.error(
            "platform.compute.cache_control confirms the result cache is enabled; "
            "cached timings are not comparable evidence"
        )


def _warn_empty_result_rows(data: dict[str, Any], vr: ValidationResult) -> None:
    if not isinstance(data, dict):
        return
    queries = data.get("queries")
    if not isinstance(queries, list):
        return
    success_rows: list[Any] = []
    for q in queries:
        if not isinstance(q, dict):
            continue
        run_type = str(q.get("run_type") or "measurement").strip().lower()
        if run_type != "measurement":
            continue
        status = q.get("status")
        if not isinstance(status, str) or status.upper() not in ("SUCCESS", "PASS"):
            continue
        success_rows.append(q.get("rows"))
    if not success_rows:
        return
    if not all((value or 0) == 0 for value in success_rows):
        return
    loaded = _bundle_rows_loaded(data)
    if loaded is not None and loaded <= 0:
        return
    if loaded is None:
        vr.warn(
            "result-rows-empty: every measurement SUCCESS reports zero or missing rows "
            "with rows_loaded unreported — confirm the run executed against loaded data "
            "(evidence: queries[].rows, summary.data.rows_loaded)"
        )
    else:
        vr.warn(
            f"result-rows-empty: every measurement SUCCESS reports zero rows against "
            f"{loaded} loaded rows — check for silently empty execution "
            "(evidence: queries[].rows, summary.data.rows_loaded)"
        )


def _validate_compliance_section(
    benchmark: Any,
    vr: ValidationResult,
    *,
    allow_partial_validation: bool = False,
) -> None:
    if not isinstance(benchmark, dict):
        return
    compliance = benchmark.get("compliance_class")
    if compliance is None:
        return
    if compliance != "official":
        if allow_partial_validation:
            return
        vr.error(
            f"benchmark.compliance_class={compliance!r} is not accepted for public submissions; "
            "only compliance_class=official may be submitted"
        )


def _validate_query_coverage(
    data: dict[str, Any],
    vr: ValidationResult,
    *,
    allow_partial_validation: bool = False,
) -> None:
    if allow_partial_validation:
        return
    benchmark = data.get("benchmark")
    if not isinstance(benchmark, dict):
        return
    bm_id = benchmark.get("id")
    normalized_id = bm_id.strip().casefold() if isinstance(bm_id, str) else None
    canonical = CANONICAL_LOGICAL_QUERY_IDS.get(normalized_id) if normalized_id else None
    if not canonical:
        return
    queries = data.get("queries")
    if not isinstance(queries, list):
        return
    observed: set[str] = set()
    uncounted = 0
    for q in queries:
        if not isinstance(q, dict):
            continue
        normalized_qid = _normalize_coverage_query_id(q.get("id"))
        if normalized_qid is None:
            uncounted += 1
        else:
            observed.add(normalized_qid)
    if normalized_id == "tpcds":
        for base_id in ("14", "23", "24", "39"):
            observed.discard(base_id)
            if {f"{base_id}a", f"{base_id}b"} <= observed:
                observed.add(base_id)
    variant_hint = ""
    if normalized_id == "tpchavoc":
        platform = data.get("platform")
        platform_name = platform.get("name") if isinstance(platform, dict) else None
        documented_skips = _tpchavoc_documented_skips(platform_name)
        successful = {
            _normalize_coverage_query_id(q.get("id"))
            for q in queries
            if isinstance(q, dict)
            and isinstance(q.get("status"), str)
            and q["status"].upper() in {"SUCCESS", "PASS"}
            and str(q.get("run_type") or "measurement").strip().lower() == "measurement"
            and _tpchavoc_variant_has_usable_timing(q)
        }
        missing_variants = TPCHAVOC_CANONICAL_VARIANTS - documented_skips - successful
        observed = canonical - {variant.split("_v")[0] for variant in missing_variants}
        if missing_variants:
            shown = ", ".join(sorted(missing_variants, key=lambda v: tuple(map(int, v.split("_v"))))[:12])
            if len(missing_variants) > 12:
                shown += f", … (+{len(missing_variants) - 12} more)"
            variant_hint = f" (missing successful or documented-skip variants: {shown})"
    missing = sorted(
        canonical - observed,
        key=lambda s: [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", s)],
    )
    if missing:
        covered = len(observed & canonical)
        shown = ", ".join(missing[:12])
        if len(missing) > 12:
            shown += f", … (+{len(missing) - 12} more)"
        hint = f" ({uncounted} queries carry a non-string or blank id)" if uncounted else ""
        vr.error(
            f"benchmark {bm_id!r} covers {covered} of {len(canonical)} canonical queries{hint} "
            f"(missing: {shown}){variant_hint}; partial runs remain local artifacts "
            "unless validated through the trusted mirror path"
        )


def _validate_platform_section(platform: Any, vr: ValidationResult) -> None:
    if not isinstance(platform, dict):
        vr.error("'platform' must be a dict")
        return
    missing_pl = REQUIRED_PLATFORM_KEYS - set(platform.keys())
    if missing_pl:
        vr.error(f"Missing keys in 'platform': {sorted(missing_pl)}")

    pl_name = platform.get("name", "")
    if isinstance(pl_name, str) and pl_name:
        normalized = pl_name.lower().replace(" ", "-")
        if normalized not in KNOWN_PLATFORMS:
            vr.warn(f"Unknown platform name: {pl_name!r}")

    _validate_inline_applied_ledger(platform, vr)


def _validate_inline_applied_ledger(platform: dict, vr: ValidationResult) -> None:
    tuning = platform.get("tuning")
    if not isinstance(tuning, dict):
        return
    applied = tuning.get("applied")
    if not isinstance(applied, dict):
        return

    for label, count in _oversized_applied_ledger_arrays(applied):
        vr.error(
            f"platform.tuning.applied.{label} exceeds the {APPLIED_RECEIPT_MAX_ENTRIES}-entry limit ({count} entries)"
        )

    try:
        size = len(json.dumps(applied, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        return
    if size > APPLIED_COMPANION_MAX_BYTES:
        vr.error(
            f"platform.tuning.applied exceeds the {APPLIED_COMPANION_MAX_BYTES}-byte limit ({size} bytes serialized)"
        )


def _validate_summary_section(
    summary: Any,
    vr: ValidationResult,
    *,
    allow_partial_validation: bool = False,
) -> None:
    if not isinstance(summary, dict):
        vr.error("'summary' must be a dict")
        return
    queries_summary = summary.get("queries", {})
    if isinstance(queries_summary, dict):
        total_q = queries_summary.get("total", 0)
        if isinstance(total_q, (int, float)) and total_q == 0:
            vr.error("summary.queries.total must be greater than 0 for a public result")

    validation_status = _normalize_status(summary.get("validation"))
    allowed = (
        PUBLIC_MIRROR_ALLOWED_VALIDATION_STATUSES
        if allow_partial_validation
        else frozenset({PUBLIC_CLEAN_VALIDATION_STATUS})
    )
    if validation_status not in allowed:
        if validation_status is None:
            vr.error("summary.validation is required for public submissions and must be 'passed'")
        elif allow_partial_validation:
            vr.error(
                f"summary.validation must be one of {sorted(allowed)} for trusted mirror "
                f"validation, got {validation_status!r}"
            )
        else:
            vr.error(f"summary.validation must be 'passed' for public submissions, got {validation_status!r}")


def _normalize_status(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("status")
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def _validate_translation_section(data: dict, vr: ValidationResult) -> None:
    execution = data.get("execution")
    if not isinstance(execution, dict):
        return

    translation = execution.get("translation")
    if translation is None:
        return

    translation_status = _normalize_status(translation)
    if translation_status is None:
        vr.error("execution.translation.status is required when execution.translation is present")
    elif translation_status in PUBLIC_NON_CLEAN_TRANSLATION_STATUSES:
        vr.error(f"execution.translation.status={translation_status!r} is not accepted for public submissions")


def _validate_environment_client_link(data: dict, vr: ValidationResult) -> None:
    environment = data.get("environment")
    if environment is None:
        return
    if not isinstance(environment, dict):
        vr.error("'environment' must be a dict")
        return

    client_link = environment.get("client_link")
    if client_link is None:
        return
    if not isinstance(client_link, dict):
        vr.error("'environment.client_link' must be a dict")
        return

    collection_status = client_link.get("collection_status")
    if collection_status is None:
        vr.error("'environment.client_link.collection_status' is required when 'environment.client_link' is present")
    elif not isinstance(collection_status, str):
        vr.error(f"'environment.client_link.collection_status' must be a string, got {collection_status!r}")
    elif collection_status not in {"available", "partial", "unavailable", "not_requested"}:
        vr.warn(f"Unknown environment.client_link.collection_status: {collection_status!r}")

    source = client_link.get("source")
    if source is not None and not isinstance(source, str):
        vr.error(f"'environment.client_link.source' must be a string, got {source!r}")

    for key in ("client_region", "client_cloud", "collection_error_class", "collection_error_message"):
        value = client_link.get(key)
        if value is not None and not isinstance(value, str):
            vr.error(f"'environment.client_link.{key}' must be a string, got {value!r}")

    overhead = client_link.get("statement_overhead_ms")
    if overhead is not None:
        _validate_overhead_shape(overhead, vr)


def _validate_overhead_shape(overhead: Any, vr: ValidationResult) -> None:
    if not isinstance(overhead, dict):
        vr.error("'environment.client_link.statement_overhead_ms' must be a dict")
        return
    samples = overhead.get("samples")
    if samples is not None:
        if isinstance(samples, bool) or not isinstance(samples, int):
            vr.error(f"'environment.client_link.statement_overhead_ms.samples' must be an int, got {samples!r}")
        elif samples < 0:
            vr.error(f"'environment.client_link.statement_overhead_ms.samples' must be non-negative, got {samples!r}")
    for key in ("min", "median"):
        value = overhead.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            vr.error(f"'environment.client_link.statement_overhead_ms.{key}' must be a number, got {value!r}")
        elif not math.isfinite(value):
            vr.error(f"'environment.client_link.statement_overhead_ms.{key}' must be finite, got {value!r}")
        elif value < 0:
            vr.error(f"'environment.client_link.statement_overhead_ms.{key}' must be non-negative, got {value!r}")


def _validate_tables_block(data: dict, vr: ValidationResult) -> None:
    tables = data.get("tables")
    if tables is None:
        return
    if not isinstance(tables, dict):
        vr.error("'tables' must be a dict")
        return

    for name, entry in tables.items():
        if not isinstance(entry, dict):
            vr.error(f"'tables.{name}' must be a dict")
            continue
        rows = entry.get("rows")
        if rows is not None:
            if isinstance(rows, bool) or not isinstance(rows, (int, float)):
                vr.error(f"'tables.{name}.rows' must be a number, got {rows!r}")
            elif not math.isfinite(rows):
                vr.error(f"'tables.{name}.rows' must be finite, got {rows!r}")
            elif rows < 0:
                vr.error(f"'tables.{name}.rows' must be non-negative, got {rows!r}")
        load_ms = entry.get("load_ms")
        if load_ms is None:
            continue
        if isinstance(load_ms, bool) or not isinstance(load_ms, (int, float)):
            vr.error(f"'tables.{name}.load_ms' must be a number, got {load_ms!r}")
        elif not math.isfinite(load_ms):
            vr.error(f"'tables.{name}.load_ms' must be finite, got {load_ms!r}")
        elif load_ms < 0:
            vr.error(f"'tables.{name}.load_ms' must be non-negative, got {load_ms!r}")


_REQUIRED_LOADED_TABLES: dict[str, tuple[str, ...]] = {
    "tpcds_obt": ("tpcds_sales_returns_obt",),
}


def _validate_required_tables(data: dict, vr: ValidationResult) -> None:
    benchmark = data.get("benchmark")
    benchmark_id = str(benchmark.get("id") or "").lower() if isinstance(benchmark, dict) else ""
    required = _REQUIRED_LOADED_TABLES.get(benchmark_id)
    if not required:
        return
    tables = data.get("tables")
    if not isinstance(tables, dict):
        if data.get("queries"):
            vr.error(
                f"benchmark '{benchmark_id}' bundle has measured queries but no 'tables' block, "
                f"so it cannot show that {', '.join(required)} was loaded"
            )
        return
    loaded = {str(name).lower(): entry for name, entry in tables.items()}
    for table in required:
        entry = loaded.get(table)
        rows = entry.get("rows") if isinstance(entry, dict) else None
        if entry is None:
            vr.error(
                f"benchmark '{benchmark_id}' queries table '{table}', which is not in 'tables' "
                f"(loaded: {sorted(loaded)}); the run measured a different dataset"
            )
        elif isinstance(rows, (int, float)) and not isinstance(rows, bool) and rows <= 0:
            vr.error(f"benchmark '{benchmark_id}' queries table '{table}', which was loaded with {rows} rows")


def _validate_platform_config_clustering(data: dict, vr: ValidationResult) -> None:
    platform = data.get("platform")
    if not isinstance(platform, dict):
        return

    config = platform.get("config")
    if config is None:
        return
    if not isinstance(config, dict):
        vr.error("'platform.config' must be a dict")
        return

    strategy = config.get("databricks_clustering_strategy")
    if strategy is None:
        return
    if not isinstance(strategy, str):
        vr.error(f"'platform.config.databricks_clustering_strategy' must be a string, got {strategy!r}")
    elif strategy not in {"z_order", "liquid_clustering", "liquid_clustering_auto", "none"}:
        vr.warn(f"Unknown platform.config.databricks_clustering_strategy: {strategy!r}")


_TUNING_EVIDENCE_KEYS = ("requested", "tuning_source", "requested_config_hash", "hash", "source")


def _warn_pre_cutoff_clustering_claim(data: dict, vr: ValidationResult) -> None:
    platform = data.get("platform")
    if not isinstance(platform, dict):
        return
    if platform.get("name") != "databricks":
        return
    config = platform.get("config")
    if not isinstance(config, dict):
        return
    if config.get("databricks_clustering_strategy") != "z_order":
        return
    tuning = platform.get("tuning")
    if isinstance(tuning, dict) and any(tuning.get(key) for key in _TUNING_EVIDENCE_KEYS):
        return
    export = data.get("export")
    if isinstance(export, dict) and export.get("benchbox_version"):
        return
    vr.warn(
        "platform.config.databricks_clustering_strategy='z_order' on a Databricks "
        "bundle without tuning context or export.benchbox_version predates the "
        "clustering provenance cutoff and may mislabel plain OPTIMIZE compaction; "
        "treat as unknown."
    )


def _raw_normalized_cost_block(data: dict[str, Any]) -> dict[str, Any] | None:
    raw = data.get("normalized_cost")
    if isinstance(raw, dict):
        return raw

    cost = data.get("cost")
    if not isinstance(cost, dict):
        return None

    for key in ("normalized_cost", "normalized"):
        raw = cost.get(key)
        if isinstance(raw, dict):
            return raw

    if NORMALIZED_COST_REQUIRED_KEYS.intersection(cost):
        return cost

    return None


def _parse_decimal(value: Any, field_path: str, vr: ValidationResult) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        vr.error(f"{field_path} must be a finite decimal value; got {value!r}")
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        vr.error(f"{field_path} must be a finite decimal value; got {value!r}")
        return None
    if not parsed.is_finite():
        vr.error(f"{field_path} must be a finite decimal value; got {value!r}")
        return None
    return parsed


def _validate_required_cost_string(raw: dict[str, Any], key: str, vr: ValidationResult) -> str | None:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        vr.error(f"normalized_cost.{key} must be a non-empty string")
        return None
    return value


def _validate_normalized_cost_block(
    raw: dict[str, Any], vr: ValidationResult
) -> tuple[bool, Decimal | None, str | None, str | None]:
    error_count = len(vr.errors)
    missing = NORMALIZED_COST_REQUIRED_KEYS - set(raw.keys())
    if missing:
        vr.error(f"normalized_cost missing required provenance fields: {sorted(missing)}")

    cost_value = _parse_decimal(raw.get("normalized_cost_usd"), "normalized_cost.normalized_cost_usd", vr)
    _validate_required_cost_string(raw, "cost_model_version", vr)
    model_source = _validate_required_cost_string(raw, "cost_model_source", vr)
    cost_scope = _validate_required_cost_string(raw, "cost_scope", vr)
    cost_status = _validate_required_cost_string(raw, "cost_status", vr)
    billing_unit = _validate_required_cost_string(raw, "billing_unit", vr)
    pricing_region = _validate_required_cost_string(raw, "pricing_region", vr)

    if model_source is not None and model_source != NORMALIZED_COST_MODEL_SOURCE:
        vr.error(f"normalized_cost.cost_model_source must be {NORMALIZED_COST_MODEL_SOURCE!r}; got {model_source!r}")
    if cost_scope is not None and cost_scope not in NORMALIZED_COST_SCOPES:
        vr.error(f"normalized_cost.cost_scope must be one of {sorted(NORMALIZED_COST_SCOPES)}; got {cost_scope!r}")
    if cost_status is not None and cost_status not in NORMALIZED_COST_STATUSES:
        vr.error(f"normalized_cost.cost_status must be one of {sorted(NORMALIZED_COST_STATUSES)}; got {cost_status!r}")

    if cost_value is not None and cost_value < 0:
        vr.error(f"normalized_cost.normalized_cost_usd cannot be negative: {cost_value}")
    if cost_status == "normalized" and cost_value is None:
        vr.error("normalized_cost.cost_status 'normalized' requires normalized_cost_usd")
    if cost_status == "not_applicable_local" and cost_value != Decimal("0"):
        vr.error("normalized_cost.cost_status 'not_applicable_local' requires normalized_cost_usd of 0")
    if cost_status == "unavailable" and cost_value is not None:
        vr.error("normalized_cost.cost_status 'unavailable' must not include normalized_cost_usd")
    if cost_status == "normalized" and billing_unit not in NORMALIZED_COST_BILLING_UNITS:
        vr.error(
            "normalized_cost.cost_status 'normalized' requires a concrete billing_unit "
            f"in {sorted(NORMALIZED_COST_BILLING_UNITS)}; got {billing_unit!r}"
        )
    if cost_status == "normalized" and pricing_region in {"unknown", "not_applicable"}:
        vr.error("normalized_cost.cost_status 'normalized' requires a concrete pricing_region")

    deployment = raw.get("deployment")
    if cost_status == "normalized" and not isinstance(deployment, dict):
        vr.error("normalized_cost.cost_status 'normalized' requires deployment metadata")
    elif deployment is not None and not isinstance(deployment, dict):
        vr.error("normalized_cost.deployment must be an object when present")
    elif isinstance(deployment, dict):
        if cost_status == "normalized":
            for key in ("cloud_provider", "cloud_region"):
                value = deployment.get(key)
                if not isinstance(value, str) or not value:
                    vr.error(f"normalized_cost.deployment.{key} must be a non-empty string for normalized cost")
        node_count = deployment.get("node_count")
        if node_count is not None and (isinstance(node_count, bool) or not isinstance(node_count, int)):
            vr.error(f"normalized_cost.deployment.node_count must be an integer when present; got {node_count!r}")

    return len(vr.errors) == error_count, cost_value, cost_status, cost_scope


def _direct_cost_total_fields(data: dict[str, Any]) -> list[tuple[str, Any]]:
    direct_fields: list[tuple[str, Any]] = []
    cost = data.get("cost")
    if isinstance(cost, dict):
        for key in DIRECT_COST_TOTAL_KEYS:
            if key in cost:
                direct_fields.append((f"cost.{key}", cost[key]))

    for key in TOP_LEVEL_DIRECT_COST_KEYS:
        if key in data:
            direct_fields.append((key, data[key]))

    return direct_fields


def _validate_public_cost_section(data: dict[str, Any], vr: ValidationResult) -> None:
    direct_fields = _direct_cost_total_fields(data)
    raw_normalized = _raw_normalized_cost_block(data)

    normalized_valid = True
    normalized_value: Decimal | None = None
    cost_status: str | None = None
    cost_scope: str | None = None
    if raw_normalized is not None:
        normalized_valid, normalized_value, cost_status, cost_scope = _validate_normalized_cost_block(
            raw_normalized, vr
        )

    if not direct_fields:
        return

    field_names = ", ".join(path for path, _value in direct_fields)
    if raw_normalized is None or not normalized_valid:
        vr.error(
            "User-supplied public leaderboard cost totals are not accepted: "
            f"{field_names} require BenchBox normalized_cost provenance"
        )
        return

    if cost_status == "unavailable" or normalized_value is None:
        vr.error(
            "Public leaderboard cost totals require normalized cost availability: "
            f"{field_names} cannot accompany cost_status {cost_status!r}"
        )
        return

    for field_path, value in direct_fields:
        direct_value = _parse_decimal(value, field_path, vr)
        if direct_value is None:
            continue
        if direct_value < 0:
            vr.error(f"{field_path} cannot be negative: {direct_value}")
            continue
        if field_path == "cost_usd" and (cost_status != "normalized" or cost_scope != "compute_only"):
            vr.error("cost_usd is only accepted as a compute_only alias for normalized BenchBox cost")
            continue
        if direct_value != normalized_value:
            vr.error(
                f"{field_path} ({direct_value}) must match normalized_cost.normalized_cost_usd ({normalized_value})"
            )


def _schema_version_tuple(version: Any) -> tuple[int, ...] | None:
    if not isinstance(version, str) or not NUMERIC_SCHEMA_VERSION_RE.fullmatch(version.strip()):
        return None
    return tuple(int(part) for part in version.strip().split("."))


def _validate_row_count_validation(index: int, q: dict[str, Any], version: Any, vr: ValidationResult) -> None:
    if "row_count_validation" not in q:
        return

    version_tuple = _schema_version_tuple(version)
    if version_tuple is None or version_tuple < ROW_COUNT_VALIDATION_SCHEMA_VERSION:
        vr.error(f"queries[{index}].row_count_validation requires schema version 2.2 or later")

    evidence = q.get("row_count_validation")
    if not isinstance(evidence, dict):
        vr.error(f"queries[{index}].row_count_validation must be an object")
        return

    unknown = set(evidence) - ROW_COUNT_VALIDATION_FIELDS
    if unknown:
        vr.error(f"queries[{index}].row_count_validation has unknown fields: {sorted(unknown)}")
    missing = ROW_COUNT_VALIDATION_REQUIRED_FIELDS - set(evidence)
    if missing:
        vr.error(f"queries[{index}].row_count_validation missing fields: {sorted(missing)}")

    status = evidence.get("status")
    if not isinstance(status, str) or status not in ROW_COUNT_VALIDATION_STATUSES:
        vr.error(f"queries[{index}].row_count_validation.status is invalid: {status!r}")

    normalized_counts: dict[str, int | None] = {}
    for field in ("expected", "actual"):
        value = evidence.get(field)
        if value is None:
            normalized_counts[field] = None
        elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
            vr.error(f"queries[{index}].row_count_validation.{field} must be a non-negative integer or null")
            normalized_counts[field] = None
        else:
            normalized_counts[field] = value

    rows = q.get("rows")
    if rows is not None and (isinstance(rows, bool) or not isinstance(rows, int) or rows < 0):
        vr.error(f"queries[{index}].rows must be a non-negative integer or null when row-count evidence is present")
        rows = None
    if normalized_counts["actual"] != rows:
        vr.error(f"queries[{index}].row_count_validation.actual must match queries[{index}].rows")
    if status == "PASSED" and (
        normalized_counts["expected"] is None or normalized_counts["actual"] != normalized_counts["expected"]
    ):
        vr.error(f"queries[{index}].row_count_validation PASSED requires equal integer expected and actual counts")

    for field in ("error", "warning"):
        if field not in evidence:
            continue
        message = evidence[field]
        if not isinstance(message, str):
            vr.error(f"queries[{index}].row_count_validation.{field} must be a string")
        elif len(message) > ROW_COUNT_VALIDATION_MESSAGE_MAX_CHARS:
            vr.error(
                f"queries[{index}].row_count_validation.{field} exceeds "
                f"{ROW_COUNT_VALIDATION_MESSAGE_MAX_CHARS} characters"
            )


def _validate_single_query(index: int, q: Any, version: Any, vr: ValidationResult) -> bool:
    if not isinstance(q, dict):
        vr.error(f"queries[{index}] is not a dict")
        return False

    missing_qk = REQUIRED_QUERY_KEYS - set(q.keys())
    if missing_qk:
        vr.error(f"queries[{index}] missing keys: {sorted(missing_qk)}")
        return False

    _validate_row_count_validation(index, q, version, vr)

    ms = q.get("ms")
    if ms is None:
        return False
    try:
        ms_f = float(ms)
    except (TypeError, ValueError):
        vr.error(f"queries[{index}] invalid ms value: {ms!r}")
        return False
    if ms_f < 0:
        vr.error(f"queries[{index}] negative duration: {ms_f}")
    elif ms_f > MAX_QUERY_DURATION_MS:
        vr.warn(f"queries[{index}] unusually long: {ms_f:.0f}ms")
    return ms_f > 0


def _validate_queries_section(queries: Any, version: Any, vr: ValidationResult) -> None:
    if not isinstance(queries, list):
        vr.error("'queries' must be a list")
        return
    if len(queries) == 0:
        vr.error("queries array must not be empty for a public result")
        return

    any_nonzero = False
    any_nonzero_measurement = False
    for i, q in enumerate(queries):
        if _validate_single_query(i, q, version, vr):
            any_nonzero = True
            run_type = str(q.get("run_type") or "measurement").strip().lower() if isinstance(q, dict) else ""
            if run_type == "measurement":
                any_nonzero_measurement = True

    if not any_nonzero:
        vr.error("All query timings are 0ms - likely invalid data")
    elif not any_nonzero_measurement:
        vr.error("queries must contain at least one positive measurement timing")


def _validate_execution_consistency(data: dict[str, Any], vr: ValidationResult) -> None:
    summary = data.get("summary")
    if not isinstance(summary, dict) or _normalize_status(summary.get("validation")) != PUBLIC_CLEAN_VALIDATION_STATUS:
        return

    failed = bundle_failed_query_count(data)
    if failed > 0:
        noun = "query" if failed == 1 else "queries"
        vr.error(f"summary.validation='passed' contradicts {failed} failed measurement {noun}")

    queries = data.get("queries")
    if isinstance(queries, list):
        for i, q in enumerate(queries):
            if not isinstance(q, dict):
                continue
            rcv = q.get("row_count_validation")
            if isinstance(rcv, dict):
                rcv_status = rcv.get("status")
                if rcv_status is not None and rcv_status != "PASSED":
                    vr.error(
                        f"summary.validation='passed' contradicts queries[{i}].row_count_validation.status={rcv_status!r}"
                    )


def _validate_validation_phase_consistency(data: dict[str, Any], vr: ValidationResult) -> None:
    summary = data.get("summary")
    if not isinstance(summary, dict):
        return
    summary_status = _normalize_status(summary.get("validation"))
    if summary_status not in {"passed", "partial"}:
        return

    if "phases" not in data:
        return
    phases = data["phases"]
    if not isinstance(phases, dict) or "validation" not in phases:
        phase_status = "unknown"
    else:
        validation_phase = phases["validation"]
        raw_phase_status = validation_phase.get("status") if isinstance(validation_phase, dict) else None
        phase_status = (
            (_normalize_status(raw_phase_status) or "unknown") if isinstance(raw_phase_status, str) else "unknown"
        )

    compatible_phase_statuses = {"passed"} if summary_status == "passed" else {"passed", "partial"}
    if phase_status not in compatible_phase_statuses:
        vr.error(f"summary.validation={summary_status!r} contradicts phases.validation.status={phase_status!r}")


def _validate_bundle(
    data: dict,
    vr: ValidationResult,
    *,
    allow_partial_validation: bool = False,
) -> None:
    _capture_metadata(data, vr)

    try:
        version = result_schema_version_value(data)
    except ValueError as exc:
        vr.error(str(exc))
        return
    missing_top = set(REQUIRED_TOP_KEYS) - set(data.keys())
    if version is not None:
        missing_top.discard("result_schema_version")
    else:
        missing_top.add("result_schema_version")

    if missing_top:
        vr.error(f"Missing required top-level keys: {sorted(missing_top)}")
        return

    _validate_version(version, vr)
    _validate_run_section(data.get("run", {}), vr)
    _validate_benchmark_section(data.get("benchmark", {}), vr)
    _validate_compliance_section(
        data.get("benchmark", {}),
        vr,
        allow_partial_validation=allow_partial_validation,
    )
    _validate_platform_section(data.get("platform", {}), vr)
    _validate_cache_control_section(
        data.get("platform", {}),
        vr,
        allow_partial_validation=allow_partial_validation,
    )
    _validate_summary_section(
        data.get("summary", {}),
        vr,
        allow_partial_validation=allow_partial_validation,
    )
    _validate_translation_section(data, vr)
    _validate_environment_client_link(data, vr)
    _validate_tables_block(data, vr)
    _validate_required_tables(data, vr)
    _validate_platform_config_clustering(data, vr)
    _warn_pre_cutoff_clustering_claim(data, vr)
    _validate_public_cost_section(data, vr)
    _validate_queries_section(data.get("queries", []), version, vr)
    _warn_empty_result_rows(data, vr)
    _warn_timing_plateau(data, vr)
    _warn_small_scale_floor(data, vr)
    _validate_query_coverage(data, vr, allow_partial_validation=allow_partial_validation)
    _validate_execution_consistency(data, vr)
    for message in override_artifact_errors(vr.path):
        vr.error(message)
    _validate_validation_phase_consistency(data, vr)


def _hash_file(file_path: Path) -> str:
    return _hash_bytes(file_path.read_bytes())


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_safe_bundle_filename(name: str) -> bool:
    if not name or name in (".", ".."):
        return False
    if "/" in name or "\\" in name or "\x00" in name:
        return False
    parts = Path(name).parts
    if Path(name).is_absolute() or ".." in parts or len(parts) != 1:
        return False
    return True


def _validate_manifest_hash(manifest_path: Path, bundle_dir: Path, vr: ValidationResult) -> None:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        vr.error(f"Cannot parse submission manifest JSON: {exc}")
        return
    except OSError as exc:
        vr.error(f"Cannot read submission manifest file: {exc}")
        return

    bundle_file = manifest.get("bundle_file")
    expected_hash = manifest.get("bundle_hash")
    missing_fields: list[str] = []
    if not isinstance(bundle_file, str) or not bundle_file:
        missing_fields.append("bundle_file")
    if not isinstance(expected_hash, str) or not expected_hash:
        missing_fields.append("bundle_hash")
    if missing_fields:
        for field in missing_fields:
            vr.error(f"Submission manifest has no {field} field")
        return

    if not _is_safe_bundle_filename(bundle_file):
        vr.error(f"Unsafe bundle_file in manifest: {bundle_file!r} (must be a plain filename)")
        return

    primary_path = bundle_dir / bundle_file
    if primary_path.is_symlink():
        vr.error(f"Bundle file is a symlink, not a regular file: {bundle_file} (symlinks not allowed)")
        return
    if not primary_path.is_file():
        vr.error(f"Bundle file declared in manifest not found in PR: {bundle_file}")
        return

    _validate_manifest_provenance(manifest, primary_path, vr)

    actual_hash = _hash_file(primary_path)
    if actual_hash != expected_hash:
        vr.error(
            f"Bundle hash mismatch for {bundle_file}: manifest says "
            f"{expected_hash[:16]}..., computed {actual_hash[:16]}..."
        )

    companion_hashes = manifest.get("companion_hashes") or {}
    if not isinstance(companion_hashes, dict):
        vr.error("companion_hashes field must be an object (filename -> hash)")
        return

    for comp_name, comp_expected in companion_hashes.items():
        _validate_manifest_companion(comp_name, comp_expected, bundle_dir, vr)


def _validate_manifest_companion(
    comp_name: object,
    comp_expected: object,
    bundle_dir: Path,
    vr: ValidationResult,
) -> None:
    if not isinstance(comp_name, str) or not _is_safe_bundle_filename(comp_name):
        vr.error(f"Unsafe companion filename in manifest: {comp_name!r} (must be a plain filename)")
        return
    comp_path = bundle_dir / comp_name
    if comp_path.is_symlink():
        vr.error(f"Companion file is a symlink, not a regular file: {comp_name} (symlinks not allowed)")
        return
    if not comp_path.is_file():
        vr.error(f"Companion file declared in manifest not found in PR: {comp_name}")
        return
    if not isinstance(comp_expected, str) or not comp_expected:
        vr.error(f"Empty companion hash for {comp_name}")
        return
    if not _validate_applied_companion_limits(comp_path, vr):
        return
    comp_actual = _hash_file(comp_path)
    if comp_actual != comp_expected:
        vr.error(
            f"Companion hash mismatch for {comp_name}: manifest says "
            f"{comp_expected[:16]}..., computed {comp_actual[:16]}..."
        )


def _validate_applied_companion_limits(companion: Path, vr: ValidationResult) -> bool:
    if not companion.name.lower().endswith(".applied.json"):
        return True
    try:
        size = companion.stat().st_size
    except OSError as exc:
        vr.error(f"Cannot inspect applied companion {companion.name}: {exc}")
        return False
    if size > APPLIED_COMPANION_MAX_BYTES:
        vr.error(
            f"Applied companion {companion.name} exceeds the {APPLIED_COMPANION_MAX_BYTES}-byte limit ({size} bytes)"
        )
        return False
    try:
        payload = json.loads(companion.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    if not isinstance(payload, dict):
        return True
    oversized = _oversized_applied_ledger_arrays(payload)
    for label, count in oversized:
        vr.error(
            f"Applied receipt {companion.name} exceeds the {APPLIED_RECEIPT_MAX_ENTRIES}-entry limit "
            f"at {label} ({count} entries)"
        )
    return not oversized


def _oversized_applied_ledger_arrays(applied: dict) -> list[tuple[str, int]]:
    receipt = applied.get("receipt") if isinstance(applied.get("receipt"), dict) else {}
    drift = applied.get("drift_check") if isinstance(applied.get("drift_check"), dict) else {}
    candidates = (
        ("statements", applied.get("statements")),
        ("dropped", applied.get("dropped")),
        ("satisfied", applied.get("satisfied")),
        ("receipt.entries", receipt.get("entries")),
        ("receipt.observed", receipt.get("observed")),
        ("receipt.dropped", receipt.get("dropped")),
        ("drift_check.errors", drift.get("errors")),
        ("drift_check.warnings", drift.get("warnings")),
        ("drift_check.configuration_mismatches", drift.get("configuration_mismatches")),
        ("drift_check.missing_tables", drift.get("missing_tables")),
        ("drift_check.extra_tables", drift.get("extra_tables")),
    )
    return [
        (label, len(value))
        for label, value in candidates
        if isinstance(value, list) and len(value) > APPLIED_RECEIPT_MAX_ENTRIES
    ]


def _validate_manifest_provenance(manifest: dict[str, Any], primary_path: Path, vr: ValidationResult) -> None:
    funding = manifest.get("funding")
    if funding is not None and funding not in FUNDING_SOURCES:
        vr.error(f"Invalid manifest funding {funding!r}: must be one of {sorted(FUNDING_SOURCES)}")

    notes = manifest.get("submission_notes")
    if notes is not None:
        if not isinstance(notes, str):
            vr.error("Manifest submission_notes must be a string")
        elif len(notes) > SUBMISSION_NOTES_MAX_LEN:
            vr.error(f"Manifest submission_notes exceeds {SUBMISSION_NOTES_MAX_LEN} characters ({len(notes)})")

    result_source = manifest.get("result_source")
    if result_source is not None:
        if result_source not in RESULT_SOURCES:
            vr.error(f"Invalid manifest result_source {result_source!r}: must be one of {sorted(RESULT_SOURCES)}")
        elif result_source == "vendor" and primary_path.parent.name != "vendor":
            vr.error(
                "Manifest result_source 'vendor' is only valid for bundles directly "
                "under a maintainer-controlled results-data/bundles/vendor/ path; a "
                "community submission cannot self-assert the vendor-supplied label."
            )

    known_defects = manifest.get("known_defects")
    if known_defects is not None:
        if (
            not isinstance(known_defects, list)
            or not known_defects
            or any(not isinstance(item, str) for item in known_defects)
        ):
            vr.error("Manifest known_defects must be a non-empty list of defect id strings")
        else:
            for defect in known_defects:
                if defect not in KNOWN_DEFECT_IDS:
                    vr.error(f"Unknown manifest known_defects id {defect!r}: must be one of {sorted(KNOWN_DEFECT_IDS)}")


def discover_bundles(path: Path) -> list[Path]:
    return [candidate for candidate in sorted(path.rglob("*")) if is_primary_bundle_file(candidate)]


def is_primary_bundle_file(path: Path) -> bool:
    if not path.is_file():
        return False
    name = path.name.lower()
    if not name.endswith(".json"):
        return False
    if any(name.endswith(suffix) for suffix in COMPANION_SUFFIXES):
        return False
    if name == "corpus-inventory.json":
        return False
    return not _is_submission_manifest_path(path)


def _is_submission_manifest_path(path: Path) -> bool:
    name = path.name.lower()
    return name == SUBMISSION_MANIFEST_FILENAME or name.endswith(SUBMISSION_MANIFEST_SUFFIX)


def validate_bundles(
    paths: list[Path],
    require_manifest: bool = False,
    *,
    allow_partial_validation: bool = False,
) -> list[ValidationResult]:
    results = []
    parsed: list[tuple[dict[str, Any], ValidationResult]] = []
    for bundle_path in paths:
        if _is_submission_manifest_path(bundle_path):
            continue

        vr = ValidationResult(str(bundle_path))

        if not bundle_path.exists():
            vr.error(f"File not found: {bundle_path}")
            results.append(vr)
            continue
        if not bundle_path.is_file():
            vr.error(f"Bundle path is not a regular file: {bundle_path}")
            results.append(vr)
            continue

        try:
            data = json.loads(bundle_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            vr.error(f"Invalid JSON: {exc}")
            results.append(vr)
            continue

        if not isinstance(data, dict):
            vr.error("Top-level value must be a JSON object")
            results.append(vr)
            continue

        _validate_bundle(data, vr, allow_partial_validation=allow_partial_validation)
        parsed.append((data, vr))

        applied_name = f"{bundle_path.stem}.applied.json".lower()
        for companion in bundle_path.parent.iterdir():
            if companion.name.lower() != applied_name:
                continue
            if companion.is_symlink():
                vr.error(f"Companion file is a symlink, not a regular file: {companion.name} (symlinks not allowed)")
            elif companion.is_file():
                _validate_applied_companion_limits(companion, vr)

        per_bundle_manifest = bundle_path.parent / f"{bundle_path.stem}{SUBMISSION_MANIFEST_SUFFIX}"
        legacy_manifest = bundle_path.parent / SUBMISSION_MANIFEST_FILENAME
        manifest_path = (
            per_bundle_manifest
            if per_bundle_manifest.exists()
            else (legacy_manifest if legacy_manifest.exists() else None)
        )
        if manifest_path is not None:
            _validate_manifest_hash(manifest_path, bundle_path.parent, vr)
        elif require_manifest:
            vr.error(
                f"Submission manifest not found for {bundle_path.name}: expected "
                f"{bundle_path.stem}{SUBMISSION_MANIFEST_SUFFIX} alongside the bundle. "
                "Community submissions must include the manifest produced by "
                "`benchbox submit` (it carries the bundle-hash contract and the "
                "community-submission trust label)."
            )

        results.append(vr)

    _warn_cross_bundle_timing(parsed)
    return results


def format_summary(results: list[ValidationResult]) -> str:
    lines: list[str] = []
    total_errors = 0
    total_warnings = 0
    total_overrides = 0

    for vr in results:
        total_errors += len(vr.errors)
        total_warnings += len(vr.warnings)
        total_overrides += len(vr.override_required)

        status = "PASS" if vr.ok else "FAIL"
        lines.append(f"  {status}  {vr.path}")
        for e in vr.errors:
            lines.append(f"        ERROR: {e}")
        for w in vr.warnings:
            lines.append(f"        WARN:  {w}")
        for rule_id in vr.override_required:
            lines.append(f"        OVERRIDE-REQUIRED: {rule_id}")

    header = (
        f"Validated {len(results)} bundle(s): {total_errors} error(s), "
        f"{total_warnings} warning(s), {total_overrides} override(s) required"
    )
    return header + "\n" + "\n".join(lines)


def format_pr_comment(results: list[ValidationResult], *, strict_overrides: bool = True) -> str:
    pending = unsatisfied_override_rules(results)
    all_pass = all(vr.ok for vr in results) and not (strict_overrides and pending)

    if all_pass:
        lines = ["## Submission Validation: PASSED"]
    else:
        lines = ["## Submission Validation: FAILED"]

    lines.append("")
    lines.append(f"Validated **{len(results)}** bundle(s).")
    lines.append("")

    lines.append("| Bundle | Status | Benchmark | Platform | Scale |")
    lines.append("|--------|--------|-----------|----------|-------|")

    for vr in results:
        status = "PASS" if vr.ok else "FAIL"
        name = Path(vr.path).name.replace("|", "\\|")
        bm_id = vr.benchmark_id.replace("|", "\\|")
        pl_name = vr.platform_name.replace("|", "\\|")
        sf = vr.scale_factor.replace("|", "\\|")
        lines.append(f"| `{name}` | {status} | {bm_id} | {pl_name} | SF {sf} |")

    has_issues = any(vr.errors or vr.warnings for vr in results)
    if has_issues:
        lines.append("")
        lines.append("### Details")
        lines.append("")
        for vr in results:
            if not vr.errors and not vr.warnings:
                continue
            lines.append(f"**`{Path(vr.path).name}`**")
            for e in vr.errors:
                lines.append(f"- ERROR: {e}")
            for w in vr.warnings:
                lines.append(f"- WARN: {w}")
            lines.append("")

    if pending:
        lines.append("")
        lines.append(f"### Overrides required (rules v{RULES_VERSION})")
        lines.append("")
        lines.append("| Bundle | Rule |")
        lines.append("|--------|------|")
        for path, rule_ids in sorted(pending.items()):
            name = Path(path).name.replace("|", "\\|")
            for rule_id in sorted(rule_ids):
                lines.append(f"| `{name}` | `{rule_id}` |")
        lines.append("")
        if strict_overrides:
            lines.append("Add a committed override artifact for each rule to proceed.")
        else:
            lines.append("Mirror lane: advisory only, no override required.")

    return "\n".join(lines)
