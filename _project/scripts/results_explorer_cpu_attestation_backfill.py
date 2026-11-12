#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

CHECKOUT_ROOT = Path(__file__).resolve().parents[2]
if str(CHECKOUT_ROOT) not in sys.path:
    sys.path.insert(0, str(CHECKOUT_ROOT))

from _project.scripts.explorer_pipeline.transformer import BundleTransformer
from _project.scripts.results_explorer_corpus_migrate import (
    COMPANION_SUFFIXES,
    _atomic_write,
    _load_json,
    _manifest_path,
    _queue_write,
    _semantic_signature,
    _sha256,
)
from benchbox.core.results.anonymization import AnonymizationManager
from benchbox.core.results.canonical_json import canonical_json_bytes
from benchbox.validation.bundle import discover_bundles

BUNDLES_DIR = Path("results-data/bundles")
DEFAULT_MANIFEST = BUNDLES_DIR / "cpu-identity-provenance-v2.manifest.json"

ATTESTED_CPU_MODEL = "Apple M4"
ATTESTED_CPU_VENDOR = "Apple"

ATTESTATION = (
    "Operator attestation, not a measurement. Every run in this corpus executed "
    "on a single machine -- natively, or driving Apple container Linux images "
    "whose engines share that host CPU. No other machine has been used in the "
    "project's development. Corroborating recorded evidence: every bundle that "
    "records a client host records Darwin/arm64, and the raw local archive "
    "shows a single machine_id. The CPU itself was never captured, because the "
    "capture path was defective (see fix/cpu-identity-capture-source)."
)


CLI_DESCRIPTION = (
    "Backfill operator-attested CPU identity into curated Explorer bundles.\n"
    "\n"
    "No historical result recorded a CPU. The capture path took ``cpu_model`` from\n"
    "``platform.processor()`` (the bare architecture on Darwin) and dropped\n"
    "``cpu_vendor`` entirely, and the DataFrame adapters recorded no client host at\n"
    "all -- see ``fix/cpu-identity-capture-source`` and the\n"
    "``dataframe-client-host-capture-gap`` tracker item. So there is no measured\n"
    "value in the archive to recover: 4 of 3845 raw local results carry a CPU, and\n"
    "all four post-date the fix.\n"
    "\n"
    "The values written here are therefore an OPERATOR ATTESTATION, not a\n"
    "measurement. The project maintainer attests that every run in this corpus\n"
    "executed on one machine -- natively, or driving Apple container Linux images\n"
    "whose engines share that host's CPU. That is consistent with the recorded\n"
    "evidence: every bundle carrying a client host records ``Darwin``/``arm64`` and\n"
    "the raw archive shows a single ``machine_id``.\n"
    "\n"
    "The attestation is recorded in the emitted manifest and in\n"
    "``results-data/CORPUS_NOTES.md`` and as typed in-band CPU identity provenance.\n"
    "\n"
    "Default mode is a dry run. ``--write`` rewrites bundles, companions and\n"
    "manifests atomically and emits the migration manifest.\n"
    "\n"
    "IMPORTANT: ``result_id`` embeds a SHA-256 prefix of the raw bundle bytes, so\n"
    "editing a bundle renumbers it. That is expected and has precedent\n"
    "(``path-privacy-migration``, ``unread-identifier-field-drop`` each renumbered\n"
    "all 207 entries); the emitted manifest records every old -> new mapping.\n"
)


def _inject_cpu(data: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(data))
    env = out.get("environment")
    if not isinstance(env, dict):
        env = {}
        out["environment"] = env

    client_host = env.get("client_host")
    if not isinstance(client_host, dict):
        client_host = {}
    client_host["cpu_model"] = ATTESTED_CPU_MODEL
    client_host["cpu_vendor"] = ATTESTED_CPU_VENDOR
    client_host["cpu_identity_provenance"] = "user_attested"
    env["client_host"] = dict(sorted(client_host.items()))

    env["cpu_model"] = ATTESTED_CPU_MODEL
    env["cpu_vendor"] = ATTESTED_CPU_VENDOR
    env["cpu_identity_provenance"] = "user_attested"
    out["environment"] = dict(sorted(env.items()))
    return out


def backfill(*, bundles_dir: Path, write: bool, manifest_path: Path) -> dict[str, Any]:
    manager = AnonymizationManager.for_public_only_bundles()
    transformer = BundleTransformer()
    entries: list[dict[str, Any]] = []
    pending_writes: list[tuple[Path, bytes]] = []

    for bundle_path in discover_bundles(bundles_dir):
        data, raw = _load_json(bundle_path)
        if not isinstance(data, dict):
            raise ValueError(f"primary bundle must be an object: {bundle_path}")

        attested = manager.anonymize_result_payload(_inject_cpu(data))
        if _semantic_signature(data) != _semantic_signature(attested):
            raise ValueError(f"semantic fields changed during CPU backfill: {bundle_path}")

        attested_raw = canonical_json_bytes(attested)
        old_result_id = transformer.result_id_from_bundle(bundle_path, data=data, raw=raw)
        new_result_id = transformer.result_id_from_bundle(bundle_path, data=attested, raw=attested_raw)

        companion_new_hashes: dict[str, str] = {}
        for suffix in COMPANION_SUFFIXES:
            companion = bundle_path.with_name(f"{bundle_path.stem}{suffix}")
            if companion.is_file():
                _, companion_raw = _load_json(companion)
                companion_new_hashes[companion.name] = _sha256(companion_raw)

        manifest = _manifest_path(bundle_path)
        manifest_change: dict[str, str] | None = None
        if manifest is not None:
            manifest_data, manifest_raw = _load_json(manifest)
            if not isinstance(manifest_data, dict):
                raise ValueError(f"submission manifest must be an object: {manifest}")
            updated_manifest = dict(manifest_data)
            updated_manifest["bundle_hash"] = _sha256(attested_raw)
            updated_manifest_raw = canonical_json_bytes(updated_manifest)
            if manifest_raw != updated_manifest_raw:
                manifest_change = {
                    "file": manifest.name,
                    "old_sha256": _sha256(manifest_raw),
                    "new_sha256": _sha256(updated_manifest_raw),
                }
                _queue_write(manifest, updated_manifest_raw, pending_writes, write=write)

        if raw != attested_raw:
            _queue_write(bundle_path, attested_raw, pending_writes, write=write)

        entries.append(
            {
                "file": bundle_path.relative_to(bundles_dir).as_posix(),
                "old_sha256": _sha256(raw),
                "new_sha256": _sha256(attested_raw),
                "old_result_id": old_result_id,
                "new_result_id": new_result_id,
                "changed": raw != attested_raw,
                "had_client_host": isinstance((data.get("environment") or {}).get("client_host"), dict),
                "manifest_change": manifest_change,
            }
        )

    entries.sort(key=lambda entry: entry["file"])
    summary = {
        "attestation": ATTESTATION,
        "cpu_model": ATTESTED_CPU_MODEL,
        "cpu_vendor": ATTESTED_CPU_VENDOR,
        "provenance": "operator-attested",
        "bundles": entries,
        "totals": {
            "bundles": len(entries),
            "changed": sum(1 for e in entries if e["changed"]),
            "manifest_changes": sum(1 for e in entries if e["manifest_change"] is not None),
            "result_ids_changed": sum(1 for e in entries if e["old_result_id"] != e["new_result_id"]),
            "had_client_host": sum(1 for e in entries if e["had_client_host"]),
            "lacked_client_host": sum(1 for e in entries if not e["had_client_host"]),
        },
    }

    if write:
        if manifest_path.exists():
            if pending_writes:
                raise FileExistsError(
                    "refusing to mutate managed corpus artifacts while preserving an existing "
                    f"attestation manifest: bundles={summary['totals']['changed']}, "
                    f"manifests={summary['totals']['manifest_changes']}: {manifest_path}. "
                    "Move the existing manifest aside deliberately if a re-attestation is intended."
                )
            return summary

        for path, payload in pending_writes:
            _atomic_write(path, payload)
        _atomic_write(manifest_path, canonical_json_bytes(summary))

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--bundles-dir", type=Path, default=BUNDLES_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--write", action="store_true", help="rewrite bundles and manifests atomically")
    args = parser.parse_args(argv)

    summary = backfill(bundles_dir=args.bundles_dir, write=args.write, manifest_path=args.manifest)
    totals = summary["totals"]
    mode = "WROTE" if args.write else "DRY RUN"
    print(f"{mode}: {totals['bundles']} bundles, {totals['changed']} changed, ")
    print(f"  result_ids renumbered: {totals['result_ids_changed']}")
    print(f"  already had a client_host: {totals['had_client_host']}")
    print(f"  had no client_host (DataFrame runs): {totals['lacked_client_host']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
