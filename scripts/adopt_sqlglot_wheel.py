from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scripts.sync_sqlglot_runtime import NOTICE, adopt_wheel, check_runtime, inspect_wheel

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path("scripts/sqlglot_runtime_baseline.json")
CHECKS = (
    ("retirement and baseline", [sys.executable, "-m", "scripts.check_sqlglot_repro_retirement", "--check"]),
    ("runtime pin", [sys.executable, "-m", "scripts.sync_sqlglot_runtime", "--check"]),
)


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def expected_sha256(wheel: Path, explicit: str | None) -> str:
    if explicit is not None:
        digest = explicit.strip().lower()
    else:
        sidecar = wheel.with_name(wheel.name + ".sha256")
        if not sidecar.is_file():
            raise ValueError("Refusing an unverified wheel: pass --sha256 or provide a .sha256 file beside it")
        digest = sidecar.read_text(encoding="utf-8").split()[0].lower()
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Expected SHA-256 must be 64 hexadecimal characters")
    return digest


def plan(root: Path, wheel: Path, sha256: str | None) -> dict[str, Any]:
    if not wheel.is_file():
        raise ValueError(f"Wheel not found: {wheel}")
    expected = expected_sha256(wheel, sha256)
    manifest = inspect_wheel(wheel)
    if manifest["sha256"] != expected:
        raise ValueError("Wheel SHA-256 does not match the verified digest")
    current = json.loads((root / "benchbox/_vendor/sqlglot.json").read_text(encoding="utf-8"))
    if version_key(manifest["version"]) <= version_key(current["version"]):
        raise ValueError(f"Wheel {manifest['version']} is not newer than the pinned runtime {current['version']}")
    return {"current": current, "manifest": manifest}


def update_baseline(root: Path, manifest: dict[str, Any]) -> None:
    path = root / BASELINE
    baseline = json.loads(path.read_text(encoding="utf-8"))
    text = path.read_text(encoding="utf-8")
    old = json.dumps(baseline["identity"], indent=2).replace("\n", "\n  ")
    new = json.dumps(manifest, indent=2).replace("\n", "\n  ")
    if old not in text:
        raise ValueError("Baseline identity block has an unexpected layout")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def snapshot(root: Path) -> dict[Path, bytes]:
    runtime = root / "benchbox/_vendor"
    paths = [p for p in runtime.iterdir() if p.suffix in {".whl", ".zip"} or p.name in {"sqlglot.json", NOTICE}]
    paths.append(root / BASELINE)
    return {path: path.read_bytes() for path in paths}


def restore(root: Path, saved: dict[Path, bytes]) -> None:
    runtime = root / "benchbox/_vendor"
    for path in runtime.iterdir():
        if path.suffix in {".whl", ".zip"} and path not in saved:
            path.unlink()
    for path, data in saved.items():
        path.write_bytes(data)


def run_checks(root: Path) -> None:
    for label, command in CHECKS:
        print(f"Running {label} check")
        result = subprocess.run(command, cwd=root, check=False)
        if result.returncode != 0:
            raise ValueError(f"{label} check failed")


def adopt(
    wheel: Path,
    sha256: str | None = None,
    root: Path = ROOT,
    dry_run: bool = False,
    checks: Callable[[Path], None] | None = run_checks,
) -> dict[str, Any]:
    planned = plan(root, wheel, sha256)
    manifest = planned["manifest"]
    print(
        f"SQLGlot runtime {planned['current']['version']} -> {manifest['version']} (fork {manifest['fork_sha'][:12]})"
    )
    if dry_run:
        print("Dry run: wheel verified; no files changed")
        return manifest
    saved = snapshot(root)
    try:
        adopt_wheel(wheel, root / "benchbox/_vendor")
        update_baseline(root, manifest)
        check_runtime(root / "benchbox/_vendor")
        if checks is not None:
            checks(root)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        restore(root, saved)
        raise
    print(
        "Adopted. Remaining manual steps: add a CHANGELOG.md entry, review any case whose outcome changed in "
        f"{BASELINE}, then run make pr-preflight and open the PR. Nothing was published."
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Adopt a verified benchbox-sqlglot wheel: pin, runtime manifest, baseline identity and checks."
    )
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--sha256", help="Expected SHA-256 of the wheel (or provide <wheel>.sha256)")
    parser.add_argument("--dry-run", action="store_true", help="Verify the wheel and report; change nothing")
    arguments = parser.parse_args(argv)
    try:
        adopt(arguments.wheel, arguments.sha256, dry_run=arguments.dry_run)
    except (OSError, ValueError, KeyError, TypeError, StopIteration, zipfile.BadZipFile) as error:
        print(f"SQLGlot adoption: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
