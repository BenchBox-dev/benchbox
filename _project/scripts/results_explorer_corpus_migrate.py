#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

CHECKOUT_ROOT = Path(__file__).resolve().parents[2]
if str(CHECKOUT_ROOT) not in sys.path:
    sys.path.insert(0, str(CHECKOUT_ROOT))

from _project.scripts.explorer_pipeline.transformer import BundleTransformer
from benchbox.core.results.anonymization import (
    AnonymizationConfig,
    AnonymizationManager,
    MissingPublicPseudonymSaltError,
    public_pseudonym_salt_path,
    resolve_public_pseudonym_salt,
)
from benchbox.core.results.canonical_json import canonical_json_bytes
from benchbox.validation.bundle import discover_bundles

BUNDLES_DIR = Path("results-data/bundles")
DEFAULT_MANIFEST = BUNDLES_DIR / "path-privacy-migration.manifest.json"
COMPANION_SUFFIXES = (".plans.json", ".tuning.json", ".applied.json")
MANIFEST_SUFFIX = ".manifest.json"
STRUCTURAL_MANIFEST_KEYS = frozenset({"bundle_file", "bundle_hash", "companion_hashes"})


CLI_DESCRIPTION = (
    "Deterministically migrate curated Explorer bundles to the public path contract.\n"
    "\n"
    "The default mode is a dry run.  ``--write`` atomically rewrites primary bundles,\n"
    "JSON companions, and their manifests with the same public anonymizer used by the\n"
    "Explorer publication boundary.  A migration manifest records old/new hashes and\n"
    "result IDs without copying any private value into the audit artifact.\n"
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _queue_write(path: Path, payload: bytes, pending_writes: list[tuple[Path, bytes]], *, write: bool) -> None:
    if write:
        pending_writes.append((path, payload))


def _load_json(path: Path) -> tuple[Any, bytes]:
    raw = path.read_bytes()
    return json.loads(raw), raw


def _public_companion_payload(path: Path, payload: Any, manager: AnonymizationManager) -> Any:
    if path.name.endswith(".tuning.json") and isinstance(payload, dict):
        return manager.anonymize_tuning_payload(payload)
    return manager.anonymize_result_payload(payload)


def _manifest_path(bundle_path: Path) -> Path | None:
    per_bundle = bundle_path.with_name(f"{bundle_path.stem}{MANIFEST_SUFFIX}")
    if per_bundle.is_file():
        return per_bundle
    legacy = bundle_path.parent / "submission-manifest.json"
    return legacy if legacy.is_file() else None


def _sanitize_manifest(payload: dict[str, Any], manager: AnonymizationManager) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in payload.items():
        if key in STRUCTURAL_MANIFEST_KEYS:
            sanitized[key] = value
            continue
        walked = manager.anonymize_result_payload({key: value})
        if key in walked:
            sanitized[key] = walked[key]
    return sanitized


def _semantic_signature(data: dict[str, Any]) -> dict[str, Any]:
    benchmark = data.get("benchmark") if isinstance(data.get("benchmark"), dict) else {}
    platform = data.get("platform") if isinstance(data.get("platform"), dict) else {}
    run = data.get("run") if isinstance(data.get("run"), dict) else {}
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    raw_queries = data.get("queries")
    queries: list[dict[str, Any]] = (
        [query for query in raw_queries if isinstance(query, dict)] if isinstance(raw_queries, list) else []
    )
    return {
        "benchmark": {key: benchmark.get(key) for key in ("id", "scale_factor", "test_type", "name")},
        "platform": {key: platform.get(key) for key in ("name", "version")},
        "run": {key: run.get(key) for key in ("id", "timestamp", "total_duration_ms")},
        "summary": {
            "validation": summary.get("validation"),
            "queries": summary.get("queries"),
            "tpc_metrics": summary.get("tpc_metrics"),
        },
        "queries": [{key: query.get(key) for key in ("id", "ms", "status", "run_type")} for query in queries],
    }


def migrate(*, bundles_dir: Path, write: bool, manifest_path: Path) -> dict[str, Any]:
    manager = AnonymizationManager(AnonymizationConfig.from_public_environ(require_salt=True))
    if resolve_public_pseudonym_salt() is None:
        print(
            f"Using the pseudonym salt file {public_pseudonym_salt_path()}; set BENCHBOX_MACHINE_ID_SALT to override.",
            file=sys.stderr,
        )
    transformer = BundleTransformer()
    entries: list[dict[str, Any]] = []
    pending_writes: list[tuple[Path, bytes]] = []

    for bundle_path in discover_bundles(bundles_dir):
        data, raw = _load_json(bundle_path)
        if not isinstance(data, dict):
            raise ValueError(f"primary bundle must be an object: {bundle_path}")
        public_data = manager.anonymize_result_payload(data)
        if _semantic_signature(data) != _semantic_signature(public_data):
            raise ValueError(f"semantic fields changed during privacy migration: {bundle_path}")
        public_raw = canonical_json_bytes(public_data)
        old_result_id = transformer.result_id_from_bundle(bundle_path, data=data, raw=raw)
        new_result_id = transformer.result_id_from_bundle(bundle_path, data=public_data, raw=public_raw)

        companion_changes: list[dict[str, str]] = []
        companion_new_hashes: dict[str, str] = {}
        for suffix in COMPANION_SUFFIXES:
            companion = bundle_path.with_name(f"{bundle_path.stem}{suffix}")
            if not companion.is_file():
                continue
            companion_data, companion_raw = _load_json(companion)
            public_companion = _public_companion_payload(companion, companion_data, manager)
            public_companion_raw = canonical_json_bytes(public_companion)
            companion_new_hashes[companion.name] = _sha256(public_companion_raw)
            if companion_raw != public_companion_raw:
                companion_changes.append(
                    {
                        "file": companion.name,
                        "old_sha256": _sha256(companion_raw),
                        "new_sha256": _sha256(public_companion_raw),
                    }
                )
                _queue_write(companion, public_companion_raw, pending_writes, write=write)

        manifest = _manifest_path(bundle_path)
        manifest_change: dict[str, str] | None = None
        if manifest is not None:
            manifest_data, manifest_raw = _load_json(manifest)
            if not isinstance(manifest_data, dict):
                raise ValueError(f"submission manifest must be an object: {manifest}")
            public_manifest = _sanitize_manifest(manifest_data, manager)
            public_manifest["bundle_hash"] = _sha256(public_raw)
            hashes = public_manifest.get("companion_hashes")
            if isinstance(hashes, dict):
                public_manifest["companion_hashes"] = {
                    name: companion_new_hashes.get(name, value) for name, value in hashes.items()
                }
            public_manifest_raw = canonical_json_bytes(public_manifest)
            if manifest_raw != public_manifest_raw:
                manifest_change = {
                    "file": manifest.name,
                    "old_sha256": _sha256(manifest_raw),
                    "new_sha256": _sha256(public_manifest_raw),
                }
                _queue_write(manifest, public_manifest_raw, pending_writes, write=write)

        if raw != public_raw:
            _queue_write(bundle_path, public_raw, pending_writes, write=write)

        entries.append(
            {
                "file": bundle_path.relative_to(bundles_dir).as_posix(),
                "old_sha256": _sha256(raw),
                "new_sha256": _sha256(public_raw),
                "old_result_id": old_result_id,
                "new_result_id": new_result_id,
                "changed": raw != public_raw,
                "companion_changes": companion_changes,
                "manifest_change": manifest_change,
            }
        )

    entries.sort(key=lambda entry: entry["file"])
    changed = sum(1 for entry in entries if entry["changed"])
    manifest = {
        "schema_version": "1",
        "migration": "results-explorer-public-path-privacy-v1",
        "bundles": entries,
        "summary": {
            "total_bundles": len(entries),
            "changed_bundles": changed,
            "unchanged_bundles": len(entries) - changed,
            "result_id_changes": sum(entry["old_result_id"] != entry["new_result_id"] for entry in entries),
            "companion_changes": sum(len(entry["companion_changes"]) for entry in entries),
            "manifest_changes": sum(entry["manifest_change"] is not None for entry in entries),
        },
    }
    if write:
        if manifest_path.exists():
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict) or existing.get("migration") != manifest["migration"]:
                raise FileExistsError(f"refusing to overwrite an existing migration manifest: {manifest_path}")
            summary = manifest["summary"]
            changed_counts = {
                name: summary[name] for name in ("changed_bundles", "companion_changes", "manifest_changes")
            }
            if any(changed_counts.values()):
                detail = ", ".join(f"{name}={count}" for name, count in changed_counts.items() if count)
                raise FileExistsError(
                    f"existing migration manifest requires a new --manifest path for changed input "
                    f"({detail}): {manifest_path}"
                )
        else:
            _atomic_write(manifest_path, canonical_json_bytes(manifest))
        for path, payload in pending_writes:
            _atomic_write(path, payload)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--bundles-dir", type=Path, default=BUNDLES_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--write", action="store_true", help="rewrite bundles and companions atomically")
    args = parser.parse_args(argv)
    try:
        result = migrate(bundles_dir=args.bundles_dir, write=args.write, manifest_path=args.manifest)
    except MissingPublicPseudonymSaltError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({"mode": "write" if args.write else "dry-run", **result["summary"]}, sort_keys=True))
    if not args.write:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
