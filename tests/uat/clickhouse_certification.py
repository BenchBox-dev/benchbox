from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CLI_DESCRIPTION = (
    "Fail-closed result checks for ClickHouse server certification artifacts.\n"
    "\n"
    "The UAT runner records loaded rows per table in its result JSON and the data\n"
    "generator records expected rows per file in ``_datagen_manifest``. Certification\n"
    "compares those independently-produced records exactly; an aggregate total can\n"
    "hide a table-level omission or duplication.\n"
)


class CertificationArtifactError(ValueError):
    pass


@dataclass(frozen=True)
class ExactRowValidation:
    expected: dict[str, int]
    actual: dict[str, int]

    @property
    def mismatches(self) -> dict[str, tuple[int | None, int | None]]:
        mismatches: dict[str, tuple[int | None, int | None]] = {}
        for table in sorted(set(self.expected) | set(self.actual)):
            expected = self.expected.get(table)
            actual = self.actual.get(table)
            if expected != actual:
                mismatches[table] = (expected, actual)
        return mismatches

    @property
    def passed(self) -> bool:
        return not self.mismatches

    def require_pass(self) -> None:
        if self.passed:
            return
        details = ", ".join(
            f"{table}: expected={expected!r} actual={actual!r}" for table, (expected, actual) in self.mismatches.items()
        )
        raise CertificationArtifactError(f"exact manifest row gate failed: {details}")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CertificationArtifactError(f"could not read JSON artifact {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CertificationArtifactError(f"JSON artifact {path} must contain an object")
    return payload


def _non_negative_count(value: Any, *, path: Path, table: str, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CertificationArtifactError(f"{path}: {table}.{field} must be a non-negative integer")
    return value


def manifest_table_rows(path: Path, table_format: str) -> dict[str, int]:
    payload = _read_json(path)
    tables = payload.get("tables")
    if not isinstance(tables, dict) or not tables:
        raise CertificationArtifactError(f"{path}: manifest has no tables")
    expected: dict[str, int] = {}
    for raw_table, raw_formats in tables.items():
        table = str(raw_table).strip().lower()
        if not table or not isinstance(raw_formats, dict):
            raise CertificationArtifactError(f"{path}: invalid manifest table {raw_table!r}")
        format_block = raw_formats.get("formats", raw_formats)
        if not isinstance(format_block, dict):
            raise CertificationArtifactError(f"{path}: {table} has invalid format data")
        selected_entries = format_block.get(table_format)
        if not isinstance(selected_entries, list) or not selected_entries:
            raise CertificationArtifactError(f"{path}: {table} has no usable {table_format!r} manifest entries")
        total = 0
        for index, entry in enumerate(selected_entries):
            if not isinstance(entry, dict) or "row_count" not in entry:
                raise CertificationArtifactError(f"{path}: {table} entry {index} lacks row_count")
            total += _non_negative_count(entry["row_count"], path=path, table=table, field="row_count")
        expected[table] = total
    return expected


def result_table_rows(path: Path) -> dict[str, int]:
    payload = _read_json(path)
    tables = payload.get("tables")
    if not isinstance(tables, dict) or not tables:
        raise CertificationArtifactError(f"{path}: result has no per-table counts")
    actual: dict[str, int] = {}
    for raw_table, raw_stats in tables.items():
        table = str(raw_table).strip().lower()
        if not table or not isinstance(raw_stats, dict) or "rows" not in raw_stats:
            raise CertificationArtifactError(f"{path}: {table or raw_table!r} lacks a table row count")
        actual[table] = _non_negative_count(raw_stats["rows"], path=path, table=table, field="rows")
    return actual


def validate_exact_manifest_rows(manifest_path: Path, result_path: Path, table_format: str) -> ExactRowValidation:
    manifest = _read_json(Path(manifest_path))
    benchmark = manifest.get("benchmark")
    scale_factor = manifest.get("scale_factor")
    if not isinstance(benchmark, str) or not benchmark:
        raise CertificationArtifactError(f"{manifest_path}: manifest lacks benchmark identity")
    if isinstance(scale_factor, bool) or not isinstance(scale_factor, (int, float)) or not math.isfinite(scale_factor):
        raise CertificationArtifactError(f"{manifest_path}: manifest lacks a finite scale_factor")

    result = _read_json(Path(result_path))
    platform = result.get("platform")
    benchmark_payload = result.get("benchmark")
    platform_name = platform.get("name") if isinstance(platform, dict) else None
    benchmark_id = benchmark_payload.get("id") if isinstance(benchmark_payload, dict) else None
    result_scale = benchmark_payload.get("scale_factor") if isinstance(benchmark_payload, dict) else None
    normalized_platform = re.sub(r"[^a-z0-9]+", "-", str(platform_name or "").lower()).strip("-")
    if normalized_platform != "clickhouse-server":
        raise CertificationArtifactError(f"{result_path}: result platform must be ClickHouse Server")
    if benchmark_id != benchmark:
        raise CertificationArtifactError(
            f"{result_path}: result benchmark {benchmark_id!r} does not match manifest {benchmark!r}"
        )
    if isinstance(result_scale, bool) or not isinstance(result_scale, (int, float)) or result_scale != scale_factor:
        raise CertificationArtifactError(
            f"{result_path}: result scale_factor {result_scale!r} does not match manifest {scale_factor!r}"
        )
    validation = ExactRowValidation(
        expected=manifest_table_rows(Path(manifest_path), table_format),
        actual=result_table_rows(Path(result_path)),
    )
    validation.require_pass()
    return validation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument(
        "--table-format", required=True, help="Exact manifest format loaded by ClickHouse (for example tbl)"
    )
    args = parser.parse_args(argv)
    try:
        validation = validate_exact_manifest_rows(args.manifest, args.result, args.table_format)
    except CertificationArtifactError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 2
    print(f"PASS exact manifest rows for {len(validation.expected)} tables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
