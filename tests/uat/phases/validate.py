from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from benchbox.cli.commands.publish import _redacted_publish_source
from benchbox.validation.bundle import (
    CLI_REFUSED_COMPLIANCE_CLASSES,
    ValidationResult,
    discover_bundles,
    validate_bundles,
)
from tests.uat.phases import PhaseResult
from tests.uat.phases.report import atomic_write_text

TSV_HEADER = "platform\tbenchmark\tscale\tresult_path\tvalidator_status\terror_count\twarning_count\tfirst_error"


@dataclass(frozen=True)
class ValidateResult(PhaseResult):
    rollup_tsv_path: Path
    clean_count: int
    warning_count: int
    error_count: int
    refused_count: int
    total: int
    clean_rate: float
    floor: float
    floor_breached: bool

    def exit_code(self) -> int:
        if self.aborted:
            return 2
        if self.floor_breached:
            return 1
        return 0


@dataclass(frozen=True)
class _RollupRow:
    platform: str
    benchmark: str
    scale: str
    result_path: str
    validator_status: str
    error_count: int
    warning_count: int
    first_error: str

    def to_tsv(self) -> str:
        return "\t".join(
            [
                self.platform,
                self.benchmark,
                self.scale,
                self.result_path,
                self.validator_status,
                str(self.error_count),
                str(self.warning_count),
                self.first_error.replace("\t", " ").replace("\n", " "),
            ]
        )


def run_validate(
    results_dir: Path | Sequence[Path],
    *,
    output_tsv: Path,
    floor: float = 0.80,
) -> ValidateResult:
    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    bundle_paths = _bundle_paths(results_dir)
    if bundle_paths is None:
        return _aborted_validate_result(output_tsv, floor=floor, reason=f"results path not found: {results_dir}")

    rows = _build_rows(bundle_paths)
    atomic_write_text(output_tsv, _render_tsv(rows))
    return _result_from_rows(output_tsv, rows, floor=floor)


def _bundle_paths(results_dir: Path | Sequence[Path]) -> list[Path] | None:
    if isinstance(results_dir, Path):
        if not results_dir.exists():
            return None
        if results_dir.is_dir():
            return discover_bundles(results_dir)
        return [results_dir]
    return list(results_dir)


def _build_rows(bundle_paths: Sequence[Path]) -> list[_RollupRow]:
    rows: list[_RollupRow] = []
    for bundle_path in bundle_paths:
        platform, benchmark, scale, compliance_class = _read_bundle_metadata(bundle_path)

        if compliance_class in CLI_REFUSED_COMPLIANCE_CLASSES:
            rows.append(
                _RollupRow(
                    platform=platform,
                    benchmark=benchmark,
                    scale=scale,
                    result_path=str(bundle_path),
                    validator_status="refused-by-cli",
                    error_count=0,
                    warning_count=0,
                    first_error=f"compliance_class={compliance_class}",
                )
            )
            continue

        results = _validate_publishable_form(bundle_path)
        if not results:
            rows.append(
                _RollupRow(
                    platform=platform,
                    benchmark=benchmark,
                    scale=scale,
                    result_path=str(bundle_path),
                    validator_status="error",
                    error_count=1,
                    warning_count=0,
                    first_error="validate_bundles returned no result (manifest sidecar?)",
                )
            )
            continue

        result = results[0]
        rows.append(
            _RollupRow(
                platform=platform,
                benchmark=benchmark,
                scale=scale,
                result_path=str(bundle_path),
                validator_status=_classify(len(result.errors), len(result.warnings)),
                error_count=len(result.errors),
                warning_count=len(result.warnings),
                first_error=result.errors[0] if result.errors else "",
            )
        )
    return rows


_TUNING_PRIVACY_MARKER = "unanonymized tuning"


def _validate_publishable_form(bundle_path: Path) -> list[ValidationResult]:
    raw = validate_bundles([bundle_path])
    if not any(_TUNING_PRIVACY_MARKER in error for result in raw for error in result.errors):
        return raw
    redacted, scratch = _redacted_publish_source(bundle_path)
    if redacted is None:
        return raw
    try:
        publishable = validate_bundles([redacted])
    finally:
        if scratch is not None:
            scratch.cleanup()
    if publishable:
        for error in raw[0].errors:
            if _TUNING_PRIVACY_MARKER not in error and error not in publishable[0].errors:
                publishable[0].error(error)
    return publishable


def _read_bundle_metadata(bundle_path: Path) -> tuple[str, str, str, str | None]:
    try:
        data = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "-", "-", "-", None
    if not isinstance(data, dict):
        return "-", "-", "-", None

    platform = "-"
    raw_platform = data.get("platform")
    if isinstance(raw_platform, dict):
        name = raw_platform.get("name")
        if isinstance(name, str) and name:
            platform = name

    benchmark = "-"
    scale = "-"
    compliance_class: str | None = None
    raw_benchmark = data.get("benchmark")
    if isinstance(raw_benchmark, dict):
        benchmark_id = raw_benchmark.get("id")
        if isinstance(benchmark_id, str) and benchmark_id:
            benchmark = benchmark_id
        scale_factor = raw_benchmark.get("scale_factor")
        if scale_factor is not None:
            scale = str(scale_factor)
        raw_compliance = raw_benchmark.get("compliance_class")
        if isinstance(raw_compliance, str):
            compliance_class = raw_compliance

    return platform, benchmark, scale, compliance_class


def _classify(error_count: int, warning_count: int) -> str:
    if error_count > 0:
        return "error"
    if warning_count > 0:
        return "warning_only"
    return "clean"


def _result_from_rows(rollup_tsv: Path, rows: Sequence[_RollupRow], *, floor: float) -> ValidateResult:
    clean = sum(1 for row in rows if row.validator_status == "clean")
    warning = sum(1 for row in rows if row.validator_status == "warning_only")
    error = sum(1 for row in rows if row.validator_status == "error")
    refused = sum(1 for row in rows if row.validator_status == "refused-by-cli")
    total = len(rows)
    denominator = total - refused
    clean_rate = clean / denominator if denominator > 0 else 0.0
    return ValidateResult(
        phase="validate",
        rollup_tsv_path=rollup_tsv,
        clean_count=clean,
        warning_count=warning,
        error_count=error,
        refused_count=refused,
        total=total,
        clean_rate=clean_rate,
        floor=floor,
        floor_breached=clean_rate < floor,
    )


def _render_tsv(rows: Sequence[_RollupRow]) -> str:
    body = [TSV_HEADER]
    body.extend(row.to_tsv() for row in rows)
    body.extend(_format_footer(rows))
    return "\n".join(body) + "\n"


def _format_footer(rows: Sequence[_RollupRow]) -> list[str]:
    total = len(rows)
    clean = sum(1 for row in rows if row.validator_status == "clean")
    error = sum(1 for row in rows if row.validator_status == "error")
    warning_only = sum(1 for row in rows if row.validator_status == "warning_only")
    refused = sum(1 for row in rows if row.validator_status == "refused-by-cli")
    submittable = total - refused
    lines = [
        f"# overall: total={total} submittable={submittable} clean={clean} "
        f"warning_only={warning_only} error={error} refused-by-cli={refused} "
        f"validator_clean_rate={_rate(clean, submittable)}"
    ]

    by_platform: dict[str, list[_RollupRow]] = {}
    by_benchmark: dict[str, list[_RollupRow]] = {}
    for row in rows:
        by_platform.setdefault(row.platform, []).append(row)
        by_benchmark.setdefault(row.benchmark, []).append(row)

    for platform in sorted(by_platform):
        platform_rows = by_platform[platform]
        submittable_rows = [row for row in platform_rows if row.validator_status != "refused-by-cli"]
        platform_clean = sum(1 for row in submittable_rows if row.validator_status == "clean")
        lines.append(
            f"# platform={platform}: total={len(platform_rows)} submittable={len(submittable_rows)} "
            f"clean={platform_clean} validator_clean_rate={_rate(platform_clean, len(submittable_rows))}"
        )

    for benchmark in sorted(by_benchmark):
        benchmark_rows = by_benchmark[benchmark]
        submittable_rows = [row for row in benchmark_rows if row.validator_status != "refused-by-cli"]
        benchmark_clean = sum(1 for row in submittable_rows if row.validator_status == "clean")
        lines.append(
            f"# benchmark={benchmark}: total={len(benchmark_rows)} submittable={len(submittable_rows)} "
            f"clean={benchmark_clean} validator_clean_rate={_rate(benchmark_clean, len(submittable_rows))}"
        )

    return lines


def _rate(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "n/a"
    return f"{(100 * numerator / denominator):.1f}%"


def _aborted_validate_result(
    rollup_tsv: Path,
    *,
    floor: float,
    reason: str,
) -> ValidateResult:
    return ValidateResult(
        phase="validate",
        aborted=True,
        abort_reason=reason,
        rollup_tsv_path=rollup_tsv,
        clean_count=0,
        warning_count=0,
        error_count=0,
        refused_count=0,
        total=0,
        clean_rate=0.0,
        floor=floor,
        floor_breached=False,
    )


def parse_validator_status_by_path(rollup_tsv: Path) -> dict[Path, str]:
    out: dict[Path, str] = {}
    with rollup_tsv.open("r", encoding="utf-8") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        try:
            status_idx = header.index("validator_status")
            path_idx = header.index("result_path")
        except ValueError:
            return out
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) <= max(status_idx, path_idx):
                continue
            result_path = fields[path_idx].strip()
            if not result_path:
                continue
            out[Path(result_path).resolve()] = fields[status_idx]
    return out
