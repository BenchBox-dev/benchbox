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
APPLE_GUEST_EVIDENCE = (
    "Bundle records a Linux kernel on an Apple CPU vendor: a Linux guest on Apple silicon, not the native macOS host."
)
DOCKER_SERVER_EVIDENCE = (
    "Bundle records a self-hosted localhost server engine driven from a macOS client whose Docker runtime discovery "
    "found no container: the engine ran in a Docker container on the Mac, not on the macOS host."
)


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _host(bundle: dict) -> dict:
    environment = _mapping(bundle.get("environment"))
    return {**environment, **_mapping(environment.get("client_host"))}


def _unrecorded(host: dict) -> bool:
    return "execution_class" not in host or (
        host["execution_class"] == "native" and host.get("execution_class_source") == "inferred"
    )


def _is_linux_guest_on_apple_silicon(host: dict) -> bool:
    return str(host.get("os", "")).startswith("Linux") and str(host.get("cpu_vendor", "")).lower() == "apple"


def _is_docker_server_on_mac(bundle: dict, host: dict) -> bool:
    deployment = _mapping(_mapping(bundle.get("platform")).get("deployment"))
    environment = _mapping(bundle.get("environment"))
    return (
        str(host.get("os", "")).startswith("Darwin")
        and deployment.get("deployment_type") == "self_hosted"
        and deployment.get("endpoint_class") == "localhost_port"
        and _mapping(environment.get("platform_runtime")).get("runtime_type") == "unknown"
        and bool(environment.get("container"))
    )


def attestation(bundle: dict) -> dict[str, str] | None:
    host = _host(bundle)
    if not _unrecorded(host):
        return None
    if _is_linux_guest_on_apple_silicon(host):
        return {"execution_class_evidence": APPLE_GUEST_EVIDENCE}
    if _is_docker_server_on_mac(bundle, host):
        return {"execution_class_evidence": DOCKER_SERVER_EVIDENCE, "container_runtime": "docker"}
    return None


def annotate(bundles_dir: Path, *, write: bool) -> list[Path]:
    changed: list[Path] = []
    for path in sorted(bundles_dir.rglob("*.json")):
        if path.name.endswith(COMPANION_SUFFIXES):
            continue
        bundle = json.loads(path.read_text(encoding="utf-8"))
        fields = attestation(bundle) if isinstance(bundle, dict) else None
        if fields is None:
            continue
        sidecar = path.with_name(f"{path.stem}{MANIFEST_SUFFIX}")
        if not sidecar.is_file():
            raise SystemExit(f"no manifest sidecar for {path}; create it with the submission tooling first")
        manifest = json.loads(sidecar.read_text(encoding="utf-8"))
        if manifest.get("execution_class") is not None:
            continue
        manifest.update({"execution_class": "container", "container_host_os": "macOS", **fields})
        if write:
            sidecar.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        changed.append(sidecar)
    return changed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Attest the container execution class in manifest sidecars of bundles that record a Linux "
        "kernel on an Apple CPU vendor, or a self-hosted localhost server engine driven from a macOS client "
        "with no Docker container found. Bundle JSON is never modified. Lists the sidecars it would change "
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
