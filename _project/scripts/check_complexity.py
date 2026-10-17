#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, cast

_C901_RE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+):\d+: C901 `(?P<func>[^`]+)` is too complex "
    r"\((?P<score>\d+) > \d+\)$"
)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_FOUND_RE = re.compile(r"^Found (?P<count>\d+) errors?\.$")
_SUCCESS_SUMMARY = "All checks passed!"
_EXCLUSION_FIELDS = {"target", "line", "score", "owner", "rationale", "expires"}


CLI_DESCRIPTION = (
    "Report cyclomatic complexity and govern temporary hard-ceiling exceptions.\n"
    "\n"
    "Ruff's configured C901 rule and this checker are intentionally separate gates:\n"
    "\n"
    "* configured Ruff fails when complexity is greater than 18 in Ruff's configured\n"
    "  file-discovery scope;\n"
    "* this checker scans ``benchbox`` with isolated Ruff, reports scores from 12\n"
    "  through 18, and fails on scores greater than 18 unless an exact, current\n"
    "  exception exists.\n"
    "\n"
    "Exception metadata is fail-closed. An entry must pin the target, line, measured\n"
    "score, owner, rationale, and a bounded future expiry date. Exceptions never\n"
    "hide the advisory band and ``--no-fail`` never suppresses metadata errors.\n"
)


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class Violation:
    file: str
    line: int
    func: str
    score: int

    @property
    def target(self) -> str:
        return f"{self.file}:{self.func}"


@dataclass(frozen=True)
class Exclusion:
    target: str
    line: int
    score: int
    owner: str
    rationale: str
    expires: date


@dataclass(frozen=True)
class ComplexityPolicy:
    max_complexity: int
    warn_complexity: int
    exclusions: tuple[Exclusion, ...]


@dataclass(frozen=True)
class RuffScan:
    violations: tuple[Violation, ...]
    error: str | None = None


@dataclass
class ModuleSummary:
    module: str
    violations: list[Violation] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.violations)

    @property
    def worst(self) -> int:
        return max((violation.score for violation in self.violations), default=0)

    @property
    def mean(self) -> float:
        if not self.violations:
            return 0.0
        return sum(violation.score for violation in self.violations) / len(self.violations)


def _module_key(filepath: str, source_root: str) -> str:
    parts = filepath.replace("\\", "/").split("/")
    try:
        root_index = parts.index(source_root)
    except ValueError:
        return source_root
    if len(parts) <= root_index + 2:
        return source_root
    return f"{parts[root_index]}/{parts[root_index + 1]}"


def _run_ruff(source_root: str) -> RuffScan:
    command = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--isolated",
        "--select",
        "C901",
        "--config",
        "lint.mccabe.max-complexity=1",
        "--output-format",
        "concise",
        source_root,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode not in {0, 1}:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        return RuffScan(violations=(), error=f"Ruff complexity scan failed: {detail}")

    if result.stderr.strip():
        return RuffScan(
            violations=(),
            error=f"Ruff complexity scan wrote unexpected stderr: {result.stderr.strip()}",
        )

    violations: list[Violation] = []
    found_counts: list[int] = []
    success_summaries = 0
    unexpected: list[str] = []
    for raw_line in result.stdout.splitlines():
        line = _ANSI_RE.sub("", raw_line)
        if not line:
            continue
        match = _C901_RE.match(line)
        if match:
            violations.append(
                Violation(
                    file=match.group("file").replace("\\", "/"),
                    line=int(match.group("line")),
                    func=match.group("func"),
                    score=int(match.group("score")),
                )
            )
            continue
        found_match = _FOUND_RE.match(line)
        if found_match:
            found_counts.append(int(found_match.group("count")))
            continue
        if line == _SUCCESS_SUMMARY:
            success_summaries += 1
            continue
        unexpected.append(line)

    if unexpected:
        sample = "; ".join(unexpected[:3])
        return RuffScan(violations=(), error=f"Ruff complexity scan produced unparseable stdout: {sample}")

    if result.returncode == 0:
        if violations or found_counts or success_summaries > 1:
            return RuffScan(violations=(), error="Ruff complexity scan return-0 output contract mismatch")
        return RuffScan(violations=())

    if success_summaries or len(found_counts) != 1:
        return RuffScan(violations=(), error="Ruff complexity scan return-1 output is missing one Found summary")
    if found_counts[0] != len(violations):
        return RuffScan(
            violations=(),
            error=(f"Ruff complexity scan summary/count mismatch: summary={found_counts[0]}, parsed={len(violations)}"),
        )
    return RuffScan(violations=tuple(violations))


def _load_config(pyproject_path: Path) -> dict[str, Any]:
    if not pyproject_path.exists():
        raise PolicyError(f"authoritative policy file is missing: {pyproject_path}")
    try:
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PolicyError(f"cannot read authoritative policy file {pyproject_path}: {exc}") from exc

    tool = data.get("tool")
    if not isinstance(tool, dict):
        raise PolicyError(f"{pyproject_path} is missing table [tool.benchbox.complexity]")
    benchbox = tool.get("benchbox")
    if not isinstance(benchbox, dict):
        raise PolicyError(f"{pyproject_path} is missing table [tool.benchbox.complexity]")
    raw = benchbox.get("complexity")
    if not isinstance(raw, dict):
        raise PolicyError(f"{pyproject_path} must define [tool.benchbox.complexity] as a table")
    return cast(dict[str, Any], raw)


def _positive_int(value: object, *, field_name: str, errors: list[str]) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        errors.append(f"{field_name} must be a positive integer")
        return None
    return value


def _parse_exclusions(
    config: dict[str, Any],
    *,
    today: date,
    max_exception_days: int,
) -> tuple[list[Exclusion], list[str]]:
    errors: list[str] = []
    legacy = config.get("exclude_functions")
    if legacy:
        errors.append("legacy exclude_functions is unsupported; use structured exclusions metadata")

    if "exclusions" not in config:
        return [], [*errors, "complexity.exclusions is required (use exclusions = [] when empty)"]
    raw_entries = config["exclusions"]
    if not isinstance(raw_entries, list):
        return [], [*errors, "complexity.exclusions must be an array of tables"]

    exclusions: list[Exclusion] = []
    for index, raw_entry in enumerate(cast(list[object], raw_entries)):
        label = f"complexity.exclusions[{index}]"
        if not isinstance(raw_entry, dict):
            errors.append(f"{label} must be a table")
            continue
        raw = cast(dict[str, object], raw_entry)

        unknown = sorted(set(raw) - _EXCLUSION_FIELDS)
        missing = sorted(_EXCLUSION_FIELDS - set(raw))
        if unknown:
            errors.append(f"{label} has unknown fields: {', '.join(unknown)}")
        if missing:
            errors.append(f"{label} is missing fields: {', '.join(missing)}")
            continue

        target = raw["target"]
        owner = raw["owner"]
        rationale = raw["rationale"]
        expiry_text = raw["expires"]
        line = _positive_int(raw["line"], field_name=f"{label}.line", errors=errors)
        score = _positive_int(raw["score"], field_name=f"{label}.score", errors=errors)
        valid = line is not None and score is not None

        target_text = target.strip().replace("\\", "/") if isinstance(target, str) else ""
        owner_text = owner.strip() if isinstance(owner, str) else ""
        rationale_text = rationale.strip() if isinstance(rationale, str) else ""
        if not target_text or ":" not in target_text:
            errors.append(f"{label}.target must be a non-empty 'path.py:function' string")
            valid = False
        if not owner_text:
            errors.append(f"{label}.owner must be non-empty")
            valid = False
        if not rationale_text:
            errors.append(f"{label}.rationale must be non-empty")
            valid = False

        expiry: date | None = None
        if not isinstance(expiry_text, str):
            errors.append(f"{label}.expires must use YYYY-MM-DD")
            valid = False
        else:
            try:
                expiry = date.fromisoformat(expiry_text)
            except ValueError:
                errors.append(f"{label}.expires must use YYYY-MM-DD")
                valid = False
            else:
                if expiry <= today:
                    errors.append(f"{label} expired on {expiry.isoformat()}")
                    valid = False
                elif (expiry - today).days > max_exception_days:
                    errors.append(
                        f"{label}.expires is {(expiry - today).days} days away; "
                        f"maximum exception lifetime is {max_exception_days} days"
                    )
                    valid = False

        if valid and expiry is not None and line is not None and score is not None:
            exclusions.append(
                Exclusion(
                    target=target_text,
                    line=line,
                    score=score,
                    owner=owner_text,
                    rationale=rationale_text,
                    expires=expiry,
                )
            )

    return exclusions, errors


def _validate_exclusions(
    exclusions: list[Exclusion],
    violations: tuple[Violation, ...],
    *,
    max_complexity: int,
) -> tuple[set[tuple[str, int]], list[str]]:
    errors: list[str] = []
    exempt: set[tuple[str, int]] = set()
    seen: set[tuple[str, int]] = set()

    for exclusion in exclusions:
        pin = (exclusion.target, exclusion.line)
        if pin in seen:
            errors.append(f"duplicate complexity exclusion pin: {exclusion.target}:{exclusion.line}")
            continue
        seen.add(pin)

        matches = [
            violation
            for violation in violations
            if violation.target == exclusion.target and violation.line == exclusion.line
        ]
        if len(matches) != 1:
            same_target = [violation for violation in violations if violation.target == exclusion.target]
            if same_target:
                current = ", ".join(f"line {item.line} score {item.score}" for item in same_target)
                errors.append(
                    f"stale complexity exclusion {exclusion.target}:{exclusion.line}; current target pins: {current}"
                )
            else:
                errors.append(f"stale complexity exclusion {exclusion.target}:{exclusion.line}; target is absent")
            continue

        violation = matches[0]
        if violation.score != exclusion.score:
            errors.append(
                f"drifted complexity exclusion {exclusion.target}:{exclusion.line}; "
                f"pinned score {exclusion.score}, measured {violation.score}"
            )
            continue
        if violation.score <= max_complexity:
            errors.append(
                f"stale complexity exclusion {exclusion.target}:{exclusion.line}; "
                f"score {violation.score} no longer exceeds hard ceiling {max_complexity}"
            )
            continue
        exempt.add(pin)

    return exempt, errors


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-root", default="benchbox", help="Source root to scan (default: benchbox)")
    parser.add_argument("--max-complexity", type=int, default=None, help="Override the configured hard ceiling")
    parser.add_argument("--warn-complexity", type=int, default=None, help="Override the advisory threshold")
    parser.add_argument("--pyproject", default="pyproject.toml", help="Configuration file (default: pyproject.toml)")
    parser.add_argument("--top", type=int, default=20, help="Show the top N scores (default: 20)")
    parser.add_argument("--no-fail", action="store_true", help="Report hard score failures without failing")
    return parser.parse_args(argv)


def _resolve_policy(args: argparse.Namespace, *, today: date) -> ComplexityPolicy:
    config = _load_config(Path(args.pyproject))
    errors: list[str] = []
    configured_max = _positive_int(config.get("max_complexity"), field_name="max_complexity", errors=errors)
    configured_warn = _positive_int(config.get("warn_complexity"), field_name="warn_complexity", errors=errors)
    max_exception_days = _positive_int(
        config.get("max_exception_days"),
        field_name="max_exception_days",
        errors=errors,
    )

    max_complexity = configured_max
    if args.max_complexity is not None:
        max_complexity = _positive_int(args.max_complexity, field_name="max_complexity", errors=errors)
    warn_complexity = configured_warn
    if args.warn_complexity is not None:
        warn_complexity = _positive_int(args.warn_complexity, field_name="warn_complexity", errors=errors)
    if max_complexity is not None and warn_complexity is not None and warn_complexity > max_complexity:
        errors.append("warn_complexity must not exceed max_complexity")
    if errors:
        raise PolicyError("\n  - ".join(errors))

    assert max_complexity is not None
    assert warn_complexity is not None
    assert max_exception_days is not None
    exclusions, errors = _parse_exclusions(
        config,
        today=today,
        max_exception_days=max_exception_days,
    )
    if errors:
        raise PolicyError("\n  - ".join(errors))
    return ComplexityPolicy(
        max_complexity=max_complexity,
        warn_complexity=warn_complexity,
        exclusions=tuple(exclusions),
    )


def _status_for(
    violation: Violation,
    *,
    exempt: set[tuple[str, int]],
    policy: ComplexityPolicy,
) -> str:
    if (violation.target, violation.line) in exempt:
        return "EXEMPT"
    if violation.score > policy.max_complexity:
        return "FAIL"
    if violation.score >= policy.warn_complexity:
        return "WARN"
    return ""


def _print_report(
    scan: RuffScan,
    *,
    source_root: str,
    policy: ComplexityPolicy,
    exempt: set[tuple[str, int]],
    top_count: int,
    no_fail: bool,
) -> int:
    modules: dict[str, ModuleSummary] = {}
    for violation in scan.violations:
        module = _module_key(violation.file, source_root)
        modules.setdefault(module, ModuleSummary(module=module)).violations.append(violation)

    warnings = [
        violation for violation in scan.violations if policy.warn_complexity <= violation.score <= policy.max_complexity
    ]
    failures = [
        violation
        for violation in scan.violations
        if violation.score > policy.max_complexity and (violation.target, violation.line) not in exempt
    ]

    print(f"Cyclomatic Complexity Report (advisory >= {policy.warn_complexity}, hard > {policy.max_complexity})")
    print("=" * 72)
    top = sorted(scan.violations, key=lambda violation: violation.score, reverse=True)[:top_count]
    print(f"\nTop {min(top_count, len(top))} most complex functions:")
    function_width = max(len(violation.func) for violation in top)
    location_width = max(len(f"{violation.file}:{violation.line}") for violation in top)
    for violation in top:
        location = f"{violation.file}:{violation.line}"
        status = _status_for(violation, exempt=exempt, policy=policy)
        print(f"  {violation.score:>4}  {violation.func:<{function_width}}  {location:<{location_width}}  {status}")

    print("\nPer-module summary:")
    module_width = max(len(summary.module) for summary in modules.values())
    print(f"  {'Module':<{module_width}}  {'Count':>5}  {'Worst':>5}  {'Mean':>6}")
    print(f"  {'-' * module_width}  {'-' * 5}  {'-' * 5}  {'-' * 6}")
    for summary in sorted(modules.values(), key=lambda item: item.worst, reverse=True):
        print(f"  {summary.module:<{module_width}}  {summary.count:>5}  {summary.worst:>5}  {summary.mean:>6.1f}")

    print(f"\nTotal functions measured (CC > 1): {len(scan.violations)}")
    print(f"  Advisory ({policy.warn_complexity} <= CC <= {policy.max_complexity}): {len(warnings)}")
    print(f"  Hard failures (CC > {policy.max_complexity}): {len(failures)}")
    print(f"  Current hard exceptions: {len(exempt)}")

    if not failures:
        print(f"\nPASSED: no unexcepted function exceeds hard ceiling {policy.max_complexity}")
        return 0
    print(f"\nFAILED: {len(failures)} function(s) exceed hard ceiling {policy.max_complexity}")
    for violation in sorted(failures, key=lambda item: item.score, reverse=True):
        print(f"  {violation.score:>4}  {violation.func}  ({violation.file}:{violation.line})")
    if no_fail:
        print("(--no-fail suppresses score failures only; metadata policy remains hard)")
        return 0
    return 1


def run(args: argparse.Namespace, *, today: date | None = None) -> int:
    try:
        policy = _resolve_policy(args, today=today or date.today())
    except PolicyError as exc:
        print(f"Complexity configuration FAILED:\n  - {exc}")
        return 1

    source_root = Path(args.source_root)
    if not source_root.is_dir():
        print(f"Complexity measurement FAILED: source root is not a directory: {source_root}")
        return 1
    scan = _run_ruff(args.source_root)
    if scan.error:
        print(scan.error)
        return 1
    if not scan.violations:
        print(f"Complexity measurement FAILED: no CC > 1 functions measured under {source_root}")
        return 1

    exempt, errors = _validate_exclusions(
        list(policy.exclusions),
        scan.violations,
        max_complexity=policy.max_complexity,
    )
    if errors:
        print("Complexity exclusion policy FAILED:")
        for error in errors:
            print(f"  - {error}")
        return 1
    return _print_report(
        scan,
        source_root=args.source_root,
        policy=policy,
        exempt=exempt,
        top_count=args.top,
        no_fail=args.no_fail,
    )


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv or sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
