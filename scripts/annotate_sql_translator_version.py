#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from benchbox.validation.bundle import (
    SQL_TRANSLATOR_VERSION_KEY,
    TranslatorVersionError,
    attested_translator_version,
    reconcile_translator_version,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLES = REPO_ROOT / "results-data" / "bundles"
DEFAULT_NOTES = REPO_ROOT / "results-data" / "CORPUS_NOTES.md"
MANIFEST_SUFFIX = ".manifest.json"
COMPANION_SUFFIXES = (MANIFEST_SUFFIX, ".plans.json", ".tuning.json", ".applied.json", ".override.json")
WHEEL_TABLE_ROW = re.compile(
    r"^\| `(?P<id>[0-9a-f]{8})` \| (?P<cell>[^|]+) \| (?P<timestamp>[^|]+) \| `(?P<commit>[0-9a-f]+)` \| "
    r"(?P<wheel>\d+(?:\.\d+)+) \| (?P<evidence>[A-Z]) \|$"
)
WHEEL_PINS = {
    "30.24.0.17": {
        "fork_sha": "b181afce8e9e9cd78b12ba78151d1cf7dbc9adf7",
        "upstream_sha": "5d588c0f32d17a08191898638f67ff27bb0a13ff",
        "wheel_sha256": "f91d099155ca4be92397b8ab2e86ef5e07b4bee0aefcec31859afced9f4fc526",
    },
    "30.25.0.2": {
        "fork_sha": "9f51489a1721423dd45e2662b87a2870648b61bd",
        "upstream_sha": "9d2966ed3f8a7f0d370846dbae1579b40ddc2924",
        "wheel_sha256": "6207c3526bbaf1d90c6c69aec133496d0a4af2b3e2d1d06d2bb52c8c77ec8fa2",
    },
}


def reconciled_wheels(notes: Path) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for line in notes.read_text(encoding="utf-8").splitlines():
        match = WHEEL_TABLE_ROW.match(line)
        if match is None:
            continue
        row = match.groupdict()
        if row["id"] in rows:
            raise SystemExit(f"{notes} lists bundle {row['id']} more than once")
        if row["wheel"] not in WHEEL_PINS:
            raise SystemExit(
                f"{notes} attributes bundle {row['id']} to wheel {row['wheel']}, which has no recorded pin"
            )
        rows[row["id"]] = row
    if not rows:
        raise SystemExit(f"{notes} has no reconciled per-bundle wheel table")
    return rows


def attestation(row: dict[str, str]) -> dict[str, str]:
    return {
        "version": row["wheel"],
        **WHEEL_PINS[row["wheel"]],
        "evidence": (
            f"Wheel {row['wheel']} reconciled in results-data/CORPUS_NOTES.md (Wheel consistency, evidence "
            f"{row['evidence']}): the vendored pin benchbox/_vendor/sqlglot.json at checkout {row['commit']}. "
            "The run log does not print the wheel."
        ),
    }


def _bundle_for(bundles_dir: Path, bundle_id: str) -> Path:
    matches = [
        path for path in sorted(bundles_dir.rglob(f"*_{bundle_id}.json")) if not path.name.endswith(COMPANION_SUFFIXES)
    ]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one bundle for id {bundle_id}, found {len(matches)}")
    return matches[0]


def annotate(bundles_dir: Path, notes: Path, *, write: bool) -> list[Path]:
    changed: list[Path] = []
    for bundle_id, row in sorted(reconciled_wheels(notes).items()):
        path = _bundle_for(bundles_dir, bundle_id)
        sidecar = path.with_name(f"{path.stem}{MANIFEST_SUFFIX}")
        if not sidecar.is_file():
            raise SystemExit(f"no manifest sidecar for {path}; create it with the submission tooling first")
        manifest = json.loads(sidecar.read_text(encoding="utf-8"))
        fields = attestation(row)
        if SQL_TRANSLATOR_VERSION_KEY in manifest:
            if manifest[SQL_TRANSLATOR_VERSION_KEY] != fields:
                raise SystemExit(f"{sidecar} already carries a different {SQL_TRANSLATOR_VERSION_KEY}")
            continue
        try:
            attested = attested_translator_version({SQL_TRANSLATOR_VERSION_KEY: fields})
            reconcile_translator_version(json.loads(path.read_text(encoding="utf-8")), attested)
        except TranslatorVersionError as exc:
            raise SystemExit(f"{path}: {exc}") from exc
        manifest[SQL_TRANSLATOR_VERSION_KEY] = fields
        if write:
            sidecar.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        changed.append(sidecar)
    return changed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Attest the SQLGlot translator version in the manifest sidecars of the bundles that the "
        "reconciled per-bundle wheel table in results-data/CORPUS_NOTES.md covers. Bundle JSON is never "
        "modified. Lists the sidecars it would change unless --write is given."
    )
    parser.add_argument("--bundles-dir", type=Path, default=DEFAULT_BUNDLES)
    parser.add_argument("--notes", type=Path, default=DEFAULT_NOTES)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    changed = annotate(args.bundles_dir, args.notes, write=args.write)
    verb = "annotated" if args.write else "would annotate"
    for sidecar in changed:
        print(f"{verb} {sidecar}")
    print(f"{verb} {len(changed)} manifest sidecar(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
