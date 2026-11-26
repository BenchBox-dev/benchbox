from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FileEntry:
    path: str
    size_bytes: int
    row_count: int


@dataclass
class ConvertedFileEntry:
    path: str
    size_bytes: int
    row_count: int
    converted_from: str | None = None
    converted_at: str | None = None
    compression: str | None = None
    row_groups: int | None = None
    is_directory: bool = False
    conversion_options: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TableFormats:
    formats: dict[str, list[ConvertedFileEntry]]


@dataclass
class ManifestV1:
    benchmark: str
    scale_factor: float
    tables: dict[str, list[FileEntry]]
    compression: dict[str, Any] | None = None
    parallel: int | None = None
    created_at: str | None = None
    generator_version: str | None = None


PLAN_FINGERPRINT_SCHEME_LITERAL = "literal"
PLAN_FINGERPRINT_SCHEME_NORMALIZED = "normalized"


@dataclass
class PlanMetadata:
    plan_fingerprints: dict[str, str] = field(default_factory=dict)
    plan_versions: dict[str, int] = field(default_factory=dict)
    plan_capture_timestamp: dict[str, str] = field(default_factory=dict)
    platform: str | None = None
    platform_version: str | None = None
    normalization_scheme: str = PLAN_FINGERPRINT_SCHEME_LITERAL
    plan_fingerprint_versions: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.plan_fingerprints:
            result["plan_fingerprints"] = self.plan_fingerprints
        if self.plan_versions:
            result["plan_versions"] = self.plan_versions
        if self.plan_capture_timestamp:
            result["plan_capture_timestamp"] = self.plan_capture_timestamp
        if self.platform:
            result["platform"] = self.platform
        if self.platform_version:
            result["platform_version"] = self.platform_version
        if self.normalization_scheme != PLAN_FINGERPRINT_SCHEME_LITERAL:
            result["normalization_scheme"] = self.normalization_scheme
        if self.plan_fingerprint_versions:
            result["plan_fingerprint_versions"] = self.plan_fingerprint_versions
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanMetadata:
        return cls(
            plan_fingerprints=data.get("plan_fingerprints", {}),
            plan_versions=data.get("plan_versions", {}),
            plan_capture_timestamp=data.get("plan_capture_timestamp", {}),
            platform=data.get("platform"),
            platform_version=data.get("platform_version"),
            normalization_scheme=data.get("normalization_scheme", PLAN_FINGERPRINT_SCHEME_LITERAL),
            plan_fingerprint_versions=data.get("plan_fingerprint_versions", {}),
        )


@dataclass
class ManifestV2:
    version: int = 2
    benchmark: str | None = None
    scale_factor: float | None = None
    tables: dict[str, TableFormats] = field(default_factory=dict)
    format_preference: list[str] = field(default_factory=list)
    compression: dict[str, Any] | None = None
    parallel: int | None = None
    created_at: str | None = None
    generator_version: str | None = None
    plan_metadata: PlanMetadata | None = None
    extra_fields: dict[str, Any] = field(default_factory=dict)
