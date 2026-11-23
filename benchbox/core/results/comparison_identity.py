from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).with_name("comparison_identity.json")
NOT_RECORDED = "version not recorded"
MAX_VERSION_LENGTH = 120

_TRUST_SUFFIX = re.compile(r"[-_]trust[-_](?:ci|community|local|unknown)", re.IGNORECASE)
_MODE_SUFFIXES = (" (dataframe)", " (sql)")


@dataclass(frozen=True)
class ComparisonVersion:
    version: str | None
    is_release: bool
    source: str | None

    @property
    def recorded(self) -> bool:
        return self.version is not None

    @property
    def text(self) -> str:
        return self.version if self.version is not None else NOT_RECORDED


@dataclass(frozen=True)
class ComparisonIdentity:
    variant_id: str
    comparison_version: ComparisonVersion

    @property
    def cohort_key(self) -> str:
        return self.variant_id

    @property
    def label(self) -> str:
        version = self.comparison_version
        return f"{self.variant_id} v{version.version}" if version.recorded else f"{self.variant_id} ({NOT_RECORDED})"


@cache
def load_table() -> dict[str, Any]:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def platform_key(bundle: Mapping[str, Any]) -> str:
    name = _mapping(bundle.get("platform")).get("name")
    token = _TRUST_SUFFIX.sub("", str(name or "")).strip().lower()
    for suffix in _MODE_SUFFIXES:
        token = token.removesuffix(suffix)
    return token.replace(" ", "-")


def _read_field(bundle: Mapping[str, Any], path: str) -> str | None:
    value: Any = bundle
    for part in path.split("."):
        value = _mapping(value).get(part)
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    text = str(value).strip()
    if not text or text.lower() in load_table()["unrecorded_values"]:
        return None
    return text[:MAX_VERSION_LENGTH]


def resolve_comparison_version(bundle: Mapping[str, Any]) -> ComparisonVersion:
    table = load_table()
    entry = {**table["default"], **table["platforms"].get(platform_key(bundle), {})}
    for path in entry["fields"]:
        version = _read_field(bundle, path)
        if version is not None:
            return ComparisonVersion(version, re.search(entry["pre_release"], version) is None, path)
    return ComparisonVersion(None, False, None)


def comparison_identity(bundle: Mapping[str, Any], variant_id: str) -> ComparisonIdentity:
    return ComparisonIdentity(variant_id, resolve_comparison_version(bundle))
