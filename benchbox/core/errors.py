from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlanCaptureError(Exception):
    reason: str
    platform: str
    query_id: str
    details: str | None = None

    def __str__(self) -> str:
        base = f"{self.platform} plan capture failed for {self.query_id}: {self.reason}"
        if self.details:
            base = f"{base} ({self.details})"
        return base


class SerializationError(Exception):
    pass


@dataclass
class PlanValidationError(Exception):
    query_id: str
    validation_errors: list[str] = field(default_factory=list)
    plan_structure: str = ""

    def __str__(self) -> str:
        errors = "\n  - ".join(self.validation_errors)
        msg = f"Plan validation failed for {self.query_id}:\n  - {errors}"
        if self.plan_structure:
            msg = f"{msg}\nStructure: {self.plan_structure}"
        return msg


@dataclass
class PlanParseError(Exception):
    query_id: str
    platform: str
    error_message: str
    line_number: int | None = None
    explain_sample: str | None = None
    platform_version: str | None = None
    detected_format: str | None = None
    recovery_hint: str | None = None

    def __str__(self) -> str:
        msg = [f"Failed to parse query plan for {self.query_id} ({self.platform}): {self.error_message}"]

        if self.line_number:
            msg.append(f"  At line: {self.line_number}")

        if self.detected_format:
            msg.append(f"  Detected format: {self.detected_format}")

        if self.explain_sample:
            sample_lines = self.explain_sample.split("\n")[:5]
            indented = "\n    ".join(sample_lines)
            msg.append(f"  EXPLAIN output sample:\n    {indented}")

        if self.platform_version:
            msg.append(f"  Platform version: {self.platform_version}")

        if self.recovery_hint:
            msg.append(f"  Hint: {self.recovery_hint}")

        return "\n".join(msg)

    @property
    def diagnostic_info(self) -> dict:
        return {
            "query_id": self.query_id,
            "platform": self.platform,
            "error_message": self.error_message,
            "line_number": self.line_number,
            "detected_format": self.detected_format,
            "platform_version": self.platform_version,
            "has_explain_sample": self.explain_sample is not None,
            "recovery_hint": self.recovery_hint,
        }


class FingerprintIntegrityError(Exception):
    def __init__(self, query_id: str, message: str = "Fingerprint mismatch"):
        self.query_id = query_id
        super().__init__(f"{message} for {query_id}. Plan may have been corrupted or modified.")


class ScaleFactorNotSupportedError(ValueError):
    def __init__(self, benchmark_id: str, scale_factor: float, scale_options: list[float]):
        self.benchmark_id = benchmark_id
        self.scale_factor = scale_factor
        self.scale_options = list(scale_options)
        super().__init__(f"{benchmark_id} accepts scale_factor in {self.scale_options}; got {scale_factor}")


class ScaleFactorOutOfRangeError(ValueError):
    def __init__(
        self,
        benchmark_id: str,
        scale_factor: float,
        minimum: float,
        maximum: float,
        scale_options: list[float],
    ):
        self.benchmark_id = benchmark_id
        self.scale_factor = scale_factor
        self.minimum = minimum
        self.maximum = maximum
        self.scale_options = list(scale_options)
        super().__init__(
            f"{benchmark_id} accepts a finite scale_factor from {minimum:g} to {maximum:g} "
            f"(whole numbers from 1); got {scale_factor}. Standard values: {self.scale_options}"
        )


__all__ = [
    "PlanCaptureError",
    "SerializationError",
    "PlanValidationError",
    "PlanParseError",
    "FingerprintIntegrityError",
    "ScaleFactorNotSupportedError",
    "ScaleFactorOutOfRangeError",
]
