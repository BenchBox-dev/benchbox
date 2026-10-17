from __future__ import annotations

import os
import re
import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchbox.core.results.platform_options import REDACTED_VALUE, is_secret_option_key, sanitize_platform_options

EFFECTIVE_SESSION_CONFIG_VERSION = 1

SNAPSHOT_SESSION_CREATED = "session_created"
SNAPSHOT_AFTER_CONFIGURATION = "after_configuration"
SNAPSHOT_RUN_START = "run_start"

STATUS_CAPTURED = "captured"
STATUS_NOT_READABLE = "not_readable"

REQUESTED_NOT_EFFECTIVE = "requested_not_effective"
EXTERNAL_OVERRIDE = "external_override"
EXTERNAL_SUPPLIED = "external_supplied"
CHANGED_BEFORE_RUN = "changed_before_run"

PATH_PLACEHOLDER = "<path>"

_CREDENTIAL_KEY_MARKERS = ("secret", "password", "token", "credential", "access.key", "accesskey")
_HOST_IDENTITY_KEYS = frozenset({"spark.driver.host", "spark.driver.bindaddress", "spark.app.name"})
_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_.\-])(?:[A-Za-z][A-Za-z0-9+.\-]*:)?(?://|/|[A-Za-z]:\\)[^\s'\",;]*")
_SUBMIT_CONF_RE = re.compile(r"^(spark\.[^=\s]+)=(.*)$")


def read_spark_session_conf(spark: Any) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    sources: list[str] = []
    runtime_values = _read_runtime_conf(spark)
    if runtime_values is not None:
        values.update(runtime_values)
        sources.append("runtime_config")
    spark_conf_values = _read_spark_context_conf(spark)
    if spark_conf_values is not None:
        for key, value in spark_conf_values.items():
            values.setdefault(key, value)
        sources.append("spark_context")
    return values, sources


def _read_runtime_conf(spark: Any) -> dict[str, str] | None:
    try:
        get_all = spark.conf.getAll
        raw = get_all() if callable(get_all) else get_all
    except Exception:
        return None
    return _conf_items(raw)


def _read_spark_context_conf(spark: Any) -> dict[str, str] | None:
    try:
        raw = spark.sparkContext.getConf().getAll()
    except Exception:
        return None
    return _conf_items(raw)


def _conf_items(raw: Any) -> dict[str, str] | None:
    if isinstance(raw, Mapping):
        items: Iterable[Any] = raw.items()
    elif isinstance(raw, list | tuple):
        items = raw
    else:
        return None
    values: dict[str, str] = {}
    for item in items:
        if not isinstance(item, list | tuple) or len(item) != 2:
            continue
        key, value = item
        if key is not None and value is not None:
            values[str(key)] = str(value)
    return values


def external_spark_conf(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    env = os.environ if environ is None else environ
    values: dict[str, str] = {}
    for conf_dir in _spark_conf_dirs(env):
        defaults_file = conf_dir / "spark-defaults.conf"
        if defaults_file.is_file():
            values.update(_parse_spark_defaults(defaults_file))
            break
    values.update(_parse_submit_args(env.get("PYSPARK_SUBMIT_ARGS", "")))
    return values


def _spark_conf_dirs(env: Mapping[str, str]) -> list[Path]:
    dirs: list[Path] = []
    if env.get("SPARK_CONF_DIR"):
        dirs.append(Path(env["SPARK_CONF_DIR"]))
    if env.get("SPARK_HOME"):
        dirs.append(Path(env["SPARK_HOME"]) / "conf")
    return dirs


def _parse_spark_defaults(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return values
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = re.split(r"\s+|=", stripped, maxsplit=1)
        if len(parts) == 2 and parts[0].startswith("spark."):
            values[parts[0]] = parts[1].strip()
    return values


def _parse_submit_args(raw: str) -> dict[str, str]:
    try:
        tokens = shlex.split(raw)
    except ValueError:
        return {}
    values: dict[str, str] = {}
    for index, token in enumerate(tokens):
        candidate = None
        if token in ("--conf", "-c") and index + 1 < len(tokens):
            candidate = tokens[index + 1]
        elif token.startswith("--conf="):
            candidate = token.removeprefix("--conf=")
        if candidate is None:
            continue
        match = _SUBMIT_CONF_RE.match(candidate)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def redact_spark_conf_value(key: str, value: str) -> str:
    lowered = key.lower()
    if is_secret_option_key(key) or any(marker in lowered for marker in _CREDENTIAL_KEY_MARKERS):
        return REDACTED_VALUE
    if lowered in _HOST_IDENTITY_KEYS:
        return REDACTED_VALUE
    scrubbed = sanitize_platform_options({key: value}, redact_usernames=False).get(key, value)
    return _ABSOLUTE_PATH_RE.sub(PATH_PLACEHOLDER, str(scrubbed))


def redact_spark_conf_values(values: Mapping[str, str]) -> dict[str, str]:
    return {key: redact_spark_conf_value(key, value) for key, value in sorted(values.items())}


def _normalized(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().lower()
    if text.startswith("file:"):
        text = text.removeprefix("file:")
        while text.startswith("//"):
            text = text[1:]
    return text.rstrip("/") or text


def _same_value(left: str | None, right: str | None) -> bool:
    return _normalized(left) == _normalized(right)


@dataclass
class _Snapshot:
    status: str
    sources: list[str]
    values: dict[str, str]


@dataclass
class EffectiveSparkConfigCapture:
    engine: str
    builder_conf: dict[str, str] = field(default_factory=dict)
    external_conf: dict[str, str] = field(default_factory=dict)
    requested_conf: dict[str, str] = field(default_factory=dict)
    snapshots: dict[str, _Snapshot] = field(default_factory=dict)

    def take_snapshot(self, name: str, spark: Any) -> None:
        values, sources = read_spark_session_conf(spark)
        status = STATUS_CAPTURED if sources else STATUS_NOT_READABLE
        self.snapshots[name] = _Snapshot(status=status, sources=sorted(sources), values=values)

    def discrepancies(self) -> list[dict[str, str]]:
        found: list[dict[str, str]] = []
        created = self._readable(SNAPSHOT_SESSION_CREATED)
        if created is not None:
            found.extend(self._external_discrepancies(created))
        configured = self._readable(SNAPSHOT_AFTER_CONFIGURATION)
        if configured is not None:
            for key, expected in sorted(self.requested_conf.items()):
                actual = configured.get(key)
                if not _same_value(actual, expected):
                    found.append(
                        self._entry(key, REQUESTED_NOT_EFFECTIVE, SNAPSHOT_AFTER_CONFIGURATION, expected, actual)
                    )
        run_start = self._readable(SNAPSHOT_RUN_START)
        if configured is not None and run_start is not None:
            for key in sorted(configured.keys() | run_start.keys()):
                before, after = configured.get(key), run_start.get(key)
                if before != after:
                    found.append(self._entry(key, CHANGED_BEFORE_RUN, SNAPSHOT_RUN_START, before, after))
        return found

    def _external_discrepancies(self, created: dict[str, str]) -> list[dict[str, str]]:
        found: list[dict[str, str]] = []
        for key, intended in sorted(self.builder_conf.items()):
            actual = created.get(key)
            if actual is not None and not _same_value(actual, intended):
                found.append(self._entry(key, EXTERNAL_OVERRIDE, SNAPSHOT_SESSION_CREATED, intended, actual))
        for key, supplied in sorted(self.external_conf.items()):
            if key in self.builder_conf:
                continue
            actual = created.get(key)
            if actual is not None and _same_value(actual, supplied):
                found.append(self._entry(key, EXTERNAL_SUPPLIED, SNAPSHOT_SESSION_CREATED, None, actual))
        return found

    def _readable(self, name: str) -> dict[str, str] | None:
        snapshot = self.snapshots.get(name)
        if snapshot is None or snapshot.status != STATUS_CAPTURED:
            return None
        return snapshot.values

    @staticmethod
    def _entry(key: str, kind: str, snapshot: str, expected: str | None, actual: str | None) -> dict[str, str]:
        entry = {"key": key, "kind": kind, "snapshot": snapshot}
        if expected is not None:
            entry["expected"] = redact_spark_conf_value(key, expected)
        entry["actual"] = redact_spark_conf_value(key, actual) if actual is not None else "<unset>"
        return entry

    def payload(self) -> dict[str, Any] | None:
        if not self.snapshots:
            return None
        return {
            "version": EFFECTIVE_SESSION_CONFIG_VERSION,
            "engine": self.engine,
            "snapshots": {
                name: {
                    "status": snapshot.status,
                    "sources": list(snapshot.sources),
                    "values": redact_spark_conf_values(snapshot.values),
                }
                for name, snapshot in self.snapshots.items()
            },
            "discrepancies": self.discrepancies(),
        }

    def console_lines(self) -> list[str]:
        if not self.snapshots:
            return []
        unreadable = sorted(name for name, snapshot in self.snapshots.items() if snapshot.status != STATUS_CAPTURED)
        lines: list[str] = []
        for name in unreadable:
            lines.append(
                f"⚠️  {self.engine} session config is not readable at {name}; effective settings are unverified"
            )
        for entry in self.discrepancies():
            detail = f"expected {entry['expected']}, " if "expected" in entry else ""
            lines.append(
                f"⚠️  {self.engine} session config {entry['key']}: {entry['kind']} ({detail}actual {entry['actual']})"
            )
        if not lines:
            run_start = self.snapshots.get(SNAPSHOT_RUN_START)
            count = len(run_start.values) if run_start is not None else 0
            lines.append(f"{self.engine} session config verified: {count} effective settings, no discrepancies")
        return lines
