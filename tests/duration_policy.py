from __future__ import annotations

import json
import math
import os
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

T1_BUDGET_SECONDS = 0.5
_VALID_TIERS = {"t1", "t2", "t3"}
DURATION_FILE = Path(__file__).with_name("fixtures") / "test_durations.json"


def current_test_tier() -> str:
    tier = os.environ.get("BENCHBOX_TEST_TIER", "t1").strip().lower()
    if tier not in _VALID_TIERS:
        raise ValueError(f"BENCHBOX_TEST_TIER must be one of {sorted(_VALID_TIERS)}, got {tier!r}")
    return tier


def load_durations(path: Path = DURATION_FILE) -> dict[str, float]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("duration file root must be an object")
    if payload.get("schema_version") != 1:
        raise ValueError(f"unsupported duration file schema: {payload.get('schema_version')!r}")
    records = payload.get("tests")
    if not isinstance(records, dict):
        raise ValueError("duration file tests must be an object")

    durations: dict[str, float] = {}
    for nodeid, record in records.items():
        if not isinstance(nodeid, str) or not nodeid:
            raise ValueError("duration file node IDs must be non-empty strings")
        p95_value = record.get("p95_seconds") if isinstance(record, dict) else None
        if isinstance(p95_value, bool) or not isinstance(p95_value, (int, float)):
            raise ValueError(f"duration record for {nodeid!r} must contain numeric p95_seconds")
        p95_seconds = float(p95_value)
        if not math.isfinite(p95_seconds) or p95_seconds < 0:
            raise ValueError(f"duration record for {nodeid!r} must be finite and non-negative")
        durations[nodeid] = p95_seconds
    return durations


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _nodeid_from_file(file_attr: str, classname: str, name: str) -> str:
    path_parts = file_attr.split("/")
    if "tests" in path_parts:
        file_name = "/".join(path_parts[path_parts.index("tests") :])
    else:
        file_name = file_attr

    stem = Path(file_name).stem
    class_parts: list[str] = []
    if classname:
        c_parts = classname.split(".")
        if stem in c_parts:
            idx = c_parts.index(stem)
            class_parts = c_parts[idx + 1 :]
        elif c_parts and c_parts[-1] != stem:
            class_parts = [c_parts[-1]]
    if class_parts:
        return f"{file_name}::{'::'.join(class_parts)}::{name}"
    return f"{file_name}::{name}"


def _find_module_split_index(parts: list[str]) -> int:
    for i in range(len(parts), 0, -1):
        if Path("/".join(parts[:i]) + ".py").exists():
            return i
    for i, part in enumerate(parts):
        if part.startswith("test_") or part.endswith("_test"):
            return i + 1
    for i, part in enumerate(parts):
        if part and part[0].isupper():
            return i
    return len(parts)


def _nodeid_from_classname(classname: str, name: str) -> str:
    parts = classname.split(".")
    file_idx = _find_module_split_index(parts)
    file_parts = parts[:file_idx]
    if "tests" in file_parts:
        file_parts = file_parts[file_parts.index("tests") :]
    file_name = "/".join(file_parts)
    if not file_name.endswith(".py"):
        file_name += ".py"

    class_parts = parts[file_idx:]
    if class_parts:
        return f"{file_name}::{'::'.join(class_parts)}::{name}"
    return f"{file_name}::{name}"


def _junit_nodeid(testcase: ET.Element) -> str | None:
    name = testcase.get("name", "").strip()
    if not name:
        raise ValueError("JUnit testcase requires non-empty name attribute")
    if name.startswith("tests/") and "::" in name:
        return name

    file_attr = testcase.get("file", "").strip().replace("\\", "/").lstrip("./")
    classname = testcase.get("classname", "").strip()

    if file_attr:
        return _nodeid_from_file(file_attr, classname, name)
    if not classname:
        if name.startswith("tests.") and "::" not in name:
            return f"{name.replace('.', '/')}.py"
        return None
    return _nodeid_from_classname(classname, name)


def collect_junit_durations(paths: list[Path]) -> dict[str, float]:
    samples: dict[str, list[float]] = {}
    for path in paths:
        root = ET.parse(path).getroot()
        for testcase in root.iter("testcase"):
            raw_time = testcase.get("time")
            if raw_time is None:
                continue
            try:
                duration = float(raw_time)
            except ValueError as exc:
                raise ValueError(f"JUnit testcase has invalid time {raw_time!r}: {path}") from exc
            if not math.isfinite(duration) or duration < 0:
                raise ValueError(f"JUnit testcase has invalid finite non-negative time {raw_time!r}: {path}")
            nodeid = _junit_nodeid(testcase)
            if nodeid is None:
                continue
            samples.setdefault(nodeid, []).append(duration)

    return {nodeid: _percentile(values, 95) for nodeid, values in sorted(samples.items())}


def is_bootstrap_artifact(path: Path = DURATION_FILE) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    return payload.get("bootstrap") is True and payload.get("tests") == {}


def write_duration_file(
    path: Path,
    durations: dict[str, float],
    *,
    generated_at: str | None = None,
    source: str = "T3 nightly JUnit reports",
    bootstrap: bool = False,
    default_seconds: float | None = None,
) -> None:
    if bootstrap and durations:
        raise ValueError("bootstrap duration artifacts must contain an empty tests map")
    for nodeid, value in durations.items():
        if not isinstance(nodeid, str) or not nodeid or not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"duration for {nodeid!r} must contain a numeric value")
        if not math.isfinite(float(value)) or value < 0:
            raise ValueError(f"duration for {nodeid!r} must be finite and non-negative")

    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": generated_at or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": source,
    }
    if default_seconds is not None:
        if not math.isfinite(default_seconds) or default_seconds < 0:
            raise ValueError("default_seconds must be finite and non-negative")
        payload["default_seconds"] = round(default_seconds, 6)
    if bootstrap:
        payload["bootstrap"] = True
    payload["tests"] = {nodeid: {"p95_seconds": round(value, 6)} for nodeid, value in sorted(durations.items())}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _marker_kwargs(item: Any, name: str) -> dict[str, Any] | None:
    marker = item.get_closest_marker(name)
    return dict(marker.kwargs) if marker is not None else None


def _validate_expiry(value: Any, *, marker_name: str, nodeid: str, today: date) -> str | None:
    if not isinstance(value, str) or not value:
        return f"{nodeid}: @{marker_name} requires expiry=YYYY-MM-DD"
    try:
        expiry = date.fromisoformat(value)
    except ValueError:
        return f"{nodeid}: @{marker_name} expiry must be YYYY-MM-DD, got {value!r}"
    if expiry < today:
        return f"{nodeid}: @{marker_name} expired on {value}"
    return None


def validate_markers(item: Any, *, today: date | None = None) -> list[str]:
    today = today or date.today()
    nodeid = item.nodeid
    errors: list[str] = []

    quarantine = _marker_kwargs(item, "quarantine")
    if quarantine is not None:
        for key in ("owner", "issue"):
            if not isinstance(quarantine.get(key), str) or not quarantine[key].strip():
                errors.append(f"{nodeid}: @pytest.mark.quarantine requires non-empty {key}=")
        if expiry_error := _validate_expiry(
            quarantine.get("expiry"), marker_name="quarantine", nodeid=nodeid, today=today
        ):
            errors.append(expiry_error)

    exemption = _marker_kwargs(item, "duration_exempt")
    if exemption is not None:
        if not isinstance(exemption.get("reason"), str) or not exemption["reason"].strip():
            errors.append(f"{nodeid}: @pytest.mark.duration_exempt requires non-empty reason=")
        if expiry_error := _validate_expiry(
            exemption.get("expiry"), marker_name="duration_exempt", nodeid=nodeid, today=today
        ):
            errors.append(expiry_error)

    return errors


def t1_budget_violations(
    item: Any,
    durations: dict[str, float],
    *,
    allow_missing: bool | None = None,
) -> list[str]:
    if item.get_closest_marker("fast") is None:
        return []
    if item.get_closest_marker("duration_exempt") is not None:
        return []
    duration = durations.get(item.nodeid)
    if duration is None:
        if allow_missing is None:
            allow_missing = (
                os.environ.get("BENCHBOX_TEST_DURATION_BOOTSTRAP", "").strip().lower() in {"1", "true", "yes"}
                or is_bootstrap_artifact()
            )
        if not allow_missing:
            return [
                f"{item.nodeid}: missing timing record in {DURATION_FILE.name}; "
                "add a valid @pytest.mark.duration_exempt or refresh duration artifact"
            ]
        return []
    if duration > T1_BUDGET_SECONDS:
        return [
            f"{item.nodeid}: p95 duration {duration:.3f}s exceeds the T1 budget of "
            f"{T1_BUDGET_SECONDS:.3f}s; add a valid @pytest.mark.duration_exempt or move it to T2"
        ]
    return []
