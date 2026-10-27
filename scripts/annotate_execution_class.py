#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLES = REPO_ROOT / "results-data" / "bundles"
MANIFEST_SUFFIX = ".manifest.json"
COMPANION_SUFFIXES = (MANIFEST_SUFFIX, ".plans.json", ".tuning.json", ".applied.json", ".override.json")
EVIDENCE = (
    "Bundle records a Linux kernel on an Apple CPU vendor: a Linux guest on Apple silicon, not the native macOS host."
)


def _host(bundle: dict) -> dict:
    environment = bundle.get("environment")
    if not isinstance(environment, dict):
        return {}
    client_host = environment.get("client_host")
    return {**environment, **(client_host if isinstance(client_host, dict) else {})}


def is_linux_guest_on_apple_silicon(bundle: dict) -> bool:
    host = _host(bundle)
    return (
        "execution_class" not in host
        and str(host.get("os", "")).startswith("Linux")
        and str(host.get("cpu_vendor", "")).lower() == "apple"
    )


def annotate(bundles_dir: Path, *, write: bool) -> list[Path]:
    changed: list[Path] = []
    for path in sorted(bundles_dir.rglob("*.json")):
        if path.name.endswith(COMPANION_SUFFIXES):
            continue
        bundle = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(bundle, dict) or not is_linux_guest_on_apple_silicon(bundle):
            continue
        sidecar = path.with_name(f"{path.stem}{MANIFEST_SUFFIX}")
        if not sidecar.is_file():
            raise SystemExit(f"no manifest sidecar for {path}; create it with the submission tooling first")
        manifest = json.loads(sidecar.read_text(encoding="utf-8"))
        if manifest.get("execution_class") is not None:
            continue
        manifest.update(
            {"execution_class": "container", "container_host_os": "macOS", "execution_class_evidence": EVIDENCE}
        )
        if write:
            sidecar.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        changed.append(sidecar)
    return changed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Attest the container execution class in manifest sidecars of bundles that record a Linux "
        "kernel on an Apple CPU vendor. Bundle JSON is never modified. Lists the sidecars it would change "
        "unless --write is given."
    )
    parser.add_argument("--bundles-dir", type=Path, default=DEFAULT_BUNDLES)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    changed = annotate(args.bundles_dir, write=args.write)
    verb = "annotated" if args.write else "would annotate"
    for sidecar in changed:
        print(f"{verb} {sidecar}")
    print(f"{verb} {len(changed)} manifest sidecar(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
