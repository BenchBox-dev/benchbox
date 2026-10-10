from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

CURRENT_SCHEMA_VERSION = "2.2"
KNOWN_SCHEMA_V2_VERSIONS = ("2.0", "2.1", "2.2")
ROW_COUNT_VALIDATION_SCHEMA_VERSION = "2.2"
LEGACY_NORMALIZED_SCHEMA_VERSION = "1.x"

_NUMERIC_SCHEMA_VERSION_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?$")


@dataclass(frozen=True)
class SchemaVersionDecision:
    policy_name: str
    raw_version: Any
    normalized_version: str | None
    accepted: bool
    reason: str
    accepted_versions: str
    remediation: str

    def error_message(self) -> str:
        if self.raw_version is None or self.raw_version == "":
            rendered_version = "<missing>"
        else:
            rendered_version = repr(self.raw_version)
        return (
            f"Unsupported schema version for {self.policy_name}: {rendered_version}. "
            f"{self.policy_name} accepts {self.accepted_versions}. {self.remediation}"
        )


@dataclass(frozen=True)
class SchemaVersionPolicy:
    policy_name: str
    accepted_versions: tuple[str, ...]
    accepted_versions_description: str
    remediation: str
    accept_numeric_v2_family: bool = False
    legacy_fallback_version: str | None = None

    def evaluate(self, raw_version: Any) -> SchemaVersionDecision:
        version, problem = normalize_schema_version_value(raw_version)

        if problem is not None:
            return self._fallback_or_reject(raw_version, version, problem)

        assert version is not None
        if version in self.accepted_versions:
            return self._decision(raw_version, version, True, "accepted")

        if self.accept_numeric_v2_family and schema_version_major(version) == "2":
            return self._decision(raw_version, version, True, "accepted-forward-compatible-v2")

        if schema_version_major(version) == "2":
            reason = "unknown-v2"
        elif schema_version_major(version) == "1":
            reason = "legacy-v1"
        else:
            reason = "unsupported-major"
        return self._fallback_or_reject(raw_version, version, reason)

    def _fallback_or_reject(
        self,
        raw_version: Any,
        normalized_version: str | None,
        reason: str,
    ) -> SchemaVersionDecision:
        if self.legacy_fallback_version is not None:
            return self._decision(
                raw_version,
                self.legacy_fallback_version,
                True,
                f"legacy-fallback-{reason}",
            )
        return self._decision(raw_version, normalized_version, False, reason)

    def _decision(
        self,
        raw_version: Any,
        normalized_version: str | None,
        accepted: bool,
        reason: str,
    ) -> SchemaVersionDecision:
        return SchemaVersionDecision(
            policy_name=self.policy_name,
            raw_version=raw_version,
            normalized_version=normalized_version,
            accepted=accepted,
            reason=reason,
            accepted_versions=self.accepted_versions_description,
            remediation=self.remediation,
        )


def schema_version_major(version: str) -> str:
    return version.split(".", 1)[0]


def normalize_schema_version_value(raw_version: Any) -> tuple[str | None, str | None]:
    if raw_version is None:
        return None, "missing"
    if not isinstance(raw_version, str):
        return None, "malformed"
    version = raw_version.strip()
    if not version:
        return None, "missing"
    if not _NUMERIC_SCHEMA_VERSION_RE.fullmatch(version):
        return version, "malformed"
    return version, None


def result_schema_version_value(data: dict[str, Any]) -> Any:
    if not isinstance(data, dict):
        return None
    if "result_schema_version" in data and "version" in data:
        result_version, result_problem = normalize_schema_version_value(data.get("result_schema_version"))
        legacy_version, legacy_problem = normalize_schema_version_value(data.get("version"))
        if result_problem is None and legacy_problem is None and result_version != legacy_version:
            raise ValueError("result_schema_version and version must match when both are present")
    if "result_schema_version" in data:
        return data.get("result_schema_version")
    if "version" in data:
        return data.get("version")
    return data.get("schema_version")


PRODUCER_SCHEMA_POLICY = SchemaVersionPolicy(
    policy_name="producer result schema policy",
    accepted_versions=(CURRENT_SCHEMA_VERSION,),
    accepted_versions_description=f"current producer schema version {CURRENT_SCHEMA_VERSION}",
    remediation="Use the current BenchBox exporter to write new result bundles.",
)

RUNTIME_SCHEMA_POLICY = SchemaVersionPolicy(
    policy_name="runtime result schema policy",
    accepted_versions=KNOWN_SCHEMA_V2_VERSIONS,
    accepted_versions_description="schema versions 2.0, 2.1, and 2.2",
    remediation="Re-export the result using a BenchBox version that writes a supported schema.",
)

LOADER_SCHEMA_POLICY = SchemaVersionPolicy(
    policy_name="runtime loader schema policy",
    accepted_versions=KNOWN_SCHEMA_V2_VERSIONS,
    accepted_versions_description="schema versions 2.0, 2.1, and 2.2",
    remediation="Re-export the result using a BenchBox version that writes a supported schema.",
)

NORMALIZER_SCHEMA_POLICY = SchemaVersionPolicy(
    policy_name="normalizer schema policy",
    accepted_versions=KNOWN_SCHEMA_V2_VERSIONS,
    accepted_versions_description="known v2 schema versions 2.0, 2.1, and 2.2; all other shapes use legacy fallback",
    remediation="Use a known v2 result bundle for exact field mapping, or rely on legacy best-effort extraction.",
    legacy_fallback_version=LEGACY_NORMALIZED_SCHEMA_VERSION,
)

PUBLIC_SUBMISSION_SCHEMA_POLICY = SchemaVersionPolicy(
    policy_name="public submission schema policy",
    accepted_versions=KNOWN_SCHEMA_V2_VERSIONS,
    accepted_versions_description=(
        f"numeric schema version family 2.x; the current producer schema version is {CURRENT_SCHEMA_VERSION}"
    ),
    remediation="Re-export the bundle with BenchBox if the top-level version is missing or malformed.",
    accept_numeric_v2_family=True,
)

EXPLORER_INPUT_SCHEMA_POLICY = SchemaVersionPolicy(
    policy_name="explorer input schema policy",
    accepted_versions=KNOWN_SCHEMA_V2_VERSIONS,
    accepted_versions_description="schema versions 2.0, 2.1, and 2.2",
    remediation="Re-export or normalize the bundle before building explorer data.",
)


def detect_normalizer_schema_version(data: dict[str, Any]) -> str:
    decision = NORMALIZER_SCHEMA_POLICY.evaluate(result_schema_version_value(data))
    assert decision.accepted
    assert decision.normalized_version is not None
    return decision.normalized_version


def is_loader_supported_result_schema(data: dict[str, Any]) -> bool:
    return LOADER_SCHEMA_POLICY.evaluate(result_schema_version_value(data)).accepted
