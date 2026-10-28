from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from . import candidate_chain, candidate_checks, omp_pin
from .candidate_chain import SCENARIOS, ClientRun, Scenario
from .candidate_checks import CONTRACT_ROWS, RECORDED_REASONS, Check

REPO_ROOT = Path(__file__).resolve().parents[3]
POLICY_PATH = REPO_ROOT / ".github" / "oracle-reviewers.yml"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "oracle_reviewers" / "omp_candidate"
USAGE_ERROR = 2
EVIDENCE_PREFIX = "Omp candidate check:"


class Chain(Protocol):
    def run(self, scenario: Scenario) -> ClientRun: ...


ChainFactory = Callable[[Path, Path], AbstractContextManager[Chain]]


@dataclass(frozen=True)
class PlatformBuild:
    target: str
    url: str
    expected: str
    actual: str
    size: int
    probed: bool = False

    @property
    def ok(self) -> bool:
        return self.expected == self.actual


@dataclass(frozen=True)
class Report:
    version: str
    builds: tuple[PlatformBuild, ...]
    transcript: str
    checks: tuple[Check, ...]
    unverified: tuple[str, ...] = ()
    unprobed_providers: tuple[str, ...] = ()
    host: str = ""
    pin_digest: str = ""
    head: str = ""

    @property
    def evidence_line(self) -> str | None:
        build = next((item for item in self.builds if item.target == self.host), None)
        if not self.releasable or build is None or not self.head:
            return None
        return f"{EVIDENCE_PREFIX} version={self.version} platform={self.host} sha256={build.actual} pin={self.pin_digest} head={self.head} result=releasable"

    @property
    def passed(self) -> bool:
        return all(build.ok for build in self.builds) and all(check.ok for check in self.checks)

    @property
    def ok(self) -> bool:
        return self.passed and not self.unverified and not self.unprobed_providers

    @property
    def releasable(self) -> bool:
        hashed = {build.target for build in self.builds}
        host_probed = any(build.target == self.host and build.probed for build in self.builds)
        return self.passed and host_probed and all(target in hashed for target in self.unverified)

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "ok": self.ok,
            "unverified_platforms": list(self.unverified),
            "unprobed_providers": list(self.unprobed_providers),
            "evidence_line": self.evidence_line,
            "version_transcript": self.transcript,
            "builds": [
                {
                    "platform": b.target,
                    "url": b.url,
                    "sha256": b.actual,
                    "pinned_sha256": b.expected,
                    "bytes": b.size,
                    "probed": b.probed,
                }
                for b in self.builds
            ],
            "checks": [
                {"name": c.name, "rows": list(c.rows), "ok": c.ok, "failures": list(c.failures)} for c in self.checks
            ],
            "recorded_reasons": {str(row): reason for row, reason in RECORDED_REASONS.items()},
        }

    def render(self) -> str:
        lines = [f"omp candidate {self.version}"]
        for build in self.builds:
            state = "ok" if build.ok else f"MISMATCH (pinned {build.expected})"
            state += ", probed" if build.probed else ", hash only"
            lines.append(f"  build {build.target}: sha256={build.actual} bytes={build.size} {state}")
        lines.append(f"  --version: {self.transcript}")
        for check in self.checks:
            rows = ",".join(str(row) for row in check.rows)
            lines.append(f"  {'PASS' if check.ok else 'FAIL'} {check.name} [rows {rows}]")
            lines.extend(f"       - {failure}" for failure in check.failures)
        lines.extend(
            f"  recorded: row {row} {CONTRACT_ROWS[row]}: {reason}" for row, reason in RECORDED_REASONS.items()
        )
        if self.unverified:
            lines.append(
                f"  partial: pinned platforms whose probes did not run (hash only or not downloaded): {', '.join(self.unverified)}"
            )
        if self.unprobed_providers:
            lines.append(
                "  partial: disabledProviders entries with no canary that observes them: "
                + ", ".join(self.unprobed_providers)
            )
        lines.append("result: " + ("green" if self.ok else "partial" if self.passed else "red"))
        if self.evidence_line is not None:
            lines.append(self.evidence_line)
        return "\n".join(lines)


def _fetch_build(target: str, build: omp_pin.Build, destination: Path, fetch: omp_pin.Fetch) -> PlatformBuild:
    fetch(build.url, destination)
    return PlatformBuild(target, build.url, build.sha256, omp_pin.sha256_of(destination), destination.stat().st_size)


def redact_capture(text: str, replacements: Sequence[tuple[str, str]]) -> str:
    for source, placeholder in replacements:
        text = text.replace(source, placeholder)
    return text


def _capture(directory: Path, workdir: Path, run: ClientRun) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    replacements = ((str(workdir / "workspace"), "/workspace"), (str(workdir), "/scratch"))
    (directory / f"{run.scenario}.jsonl").write_text(redact_capture(run.stdout, replacements), encoding="utf-8")


def _live_checks(chain_factory: ChainFactory, binary: Path, workdir: Path, capture: Path | None) -> list[Check]:
    try:
        with chain_factory(binary, workdir / "chain") as chain:
            runs = {scenario.name: chain.run(scenario) for scenario in SCENARIOS}
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return [Check("live-chain", (1, 12), (f"the loopback chain could not run: {exc}",))]
    if capture is not None:
        for run in runs.values():
            _capture(capture, workdir / "chain", run)
    return candidate_checks.live_checks(runs)


def check_candidate(
    pin: omp_pin.Pin,
    *,
    targets: Sequence[str] | None = None,
    binary: Path | None = None,
    fetch: omp_pin.Fetch | None = None,
    chain_factory: ChainFactory = candidate_chain.loopback_chain,
    fixtures: Path = FIXTURE_DIR,
    capture: Path | None = None,
) -> Report:
    host = omp_pin.current_platform()
    selected = tuple(targets) if targets else tuple(pin.builds)
    if host not in selected:
        selected = (*selected, host)
    builds: list[PlatformBuild] = []
    with tempfile.TemporaryDirectory(prefix="omp-candidate-") as root_name:
        root = Path(root_name)
        candidate = root / "candidate" / "omp"
        candidate.parent.mkdir()
        for target in selected:
            build = omp_pin.select_build(pin, target)
            if target == host and binary is not None:
                shutil.copyfile(binary, candidate)
                builds.append(
                    PlatformBuild(
                        target, build.url, build.sha256, omp_pin.sha256_of(candidate), candidate.stat().st_size
                    )
                )
                continue
            destination = candidate if target == host else root / f"download-{target}"
            builds.append(_fetch_build(target, build, destination, fetch or omp_pin.download))
            if target != host:
                destination.unlink()
        home = root / "static-home"
        home.mkdir()
        host_build = next(item for item in builds if item.target == host)
        transcript = "not executed: the host build does not hash to the pin"
        if host_build.ok:
            candidate.chmod(0o755)
            checks = candidate_checks.static_checks(candidate, home, pin.version)
            code, text = candidate_checks.run_omp(candidate, home, "--version")
            transcript = " ".join(text.split()) if code == 0 else f"exit {code}"
        else:
            checks = [Check("static-probes", (1, 2, 3, 4, 11), (f"skipped: {transcript}",))]
        runnable = host_build.ok and all(check.ok for check in checks)
        checks.extend(candidate_checks.fixture_checks(fixtures))
        if runnable:
            checks.extend(_live_checks(chain_factory, candidate, root, capture))
        else:
            checks.append(Check("live-chain", (1, 12), ("skipped: the candidate hash or static checks failed",)))
        checks.extend(
            Check("row-coverage", (row,), (f"no probe covers contract row {row}",))
            for row in candidate_checks.uncovered_rows(checks)
        )
    probed = {host} if runnable else set()
    builds = [replace(build, probed=build.target in probed) for build in builds]
    unverified = tuple(target for target in pin.builds if target not in probed)
    return Report(
        pin.version,
        tuple(builds),
        transcript,
        tuple(checks),
        unverified,
        candidate_checks.unprobed_providers(),
        host,
        omp_pin.pin_digest(pin),
    )


def _load_pin(args: argparse.Namespace) -> omp_pin.Pin:
    source = Path(args.pin) if args.pin else POLICY_PATH
    pin = omp_pin.pin_from_text(source.read_text(encoding="utf-8"))
    if pin.version != args.version:
        raise omp_pin.PinError(
            f"{source} pins omp {pin.version}, not {args.version}; pass --pin with a file whose omp section names the candidate"
        )
    return pin


def clean_head() -> str:
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=60,
        )
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    value = head.stdout.strip()
    clean = status.returncode == 0 and not status.stdout.strip() and head.returncode == 0
    return value if clean and re.fullmatch(r"[0-9a-f]{40}", value) else ""


def command_check(args: argparse.Namespace) -> int:
    try:
        pin = _load_pin(args)
        report = check_candidate(
            pin,
            targets=args.platform or None,
            binary=Path(args.binary) if args.binary else None,
            capture=Path(args.capture_dir) if args.capture_dir else None,
        )
    except (OSError, omp_pin.PinError) as exc:
        print(f"omp-candidate-check: error: {exc}", file=sys.stderr)
        return USAGE_ERROR
    report = replace(report, head=clean_head())
    print(json.dumps(report.to_json(), indent=2) if args.json else report.render())
    return 0 if report.ok else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omp-candidate-check")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.add_argument("--version", required=True)
    check.add_argument("--pin")
    check.add_argument("--platform", action="append")
    check.add_argument("--binary")
    check.add_argument("--capture-dir")
    check.add_argument("--json", action="store_true")
    check.set_defaults(handler=command_check)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
