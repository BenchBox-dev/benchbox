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
    _atomic_write,
    _load_json,
    _manifest_path,
    _queue_write,
    _semantic_signature,
    _sha256,
)
from benchbox.core.results.canonical_json import canonical_json_bytes
from benchbox.validation.bundle import discover_bundles

BUNDLES_DIR = Path("results-data/bundles")
DEFAULT_RECORD = BUNDLES_DIR / "snowflake-warehouse-size-correction.manifest.json"
SIZE_FIELDS = ("config", "raw_config")

REASON = (
    "Snowflake bundles recorded the adapter default warehouse size in platform.config and "
    "platform.raw_config although the warehouse was never resized (modify_warehouse_settings "
    "defaults to false). The size SHOW WAREHOUSES observed during the run, recorded in "
    "platform.compute.warehouse_size, is the size used; the configured fields now carry it."
)

CLI_DESCRIPTION = (
    "Correct Snowflake bundles whose recorded configured warehouse size differs from the\n"
    "size observed during the run.\n"
    "\n"
    "Default mode is a dry run. --write rewrites the bundles, refreshes the bundle_hash in each\n"
    "bundle's submission manifest and emits a correction record with old and new hashes and\n"
    "result IDs. result_id embeds a SHA-256 prefix of the bundle bytes, so every corrected\n"
    "bundle is renumbered. Regenerate results-data/corpus-inventory.json afterwards.\n"
)


def _observed_size(data: dict[str, Any]) -> str | None:
    platform = data.get("platform")
    if not isinstance(platform, dict) or str(platform.get("name", "")).lower() != "snowflake":
        return None
    compute = platform.get("compute")
    if not isinstance(compute, dict) or compute.get("source") != "observed":
        return None
    size = compute.get("warehouse_size")
    return size if isinstance(size, str) and size else None


def _correct(data: dict[str, Any], observed: str) -> tuple[dict[str, Any], dict[str, str]]:
    out = json.loads(json.dumps(data))
    previous: dict[str, str] = {}
    for field in SIZE_FIELDS:
        block = out["platform"].get(field)
        if isinstance(block, dict) and block.get("warehouse_size") not in (None, observed):
            previous[field] = block["warehouse_size"]
            block["warehouse_size"] = observed
    return out, previous


def correct(*, bundles_dir: Path, write: bool, record_path: Path) -> dict[str, Any]:
    transformer = BundleTransformer()
    entries: list[dict[str, Any]] = []
    pending_writes: list[tuple[Path, bytes]] = []

    for bundle_path in discover_bundles(bundles_dir):
        data, raw = _load_json(bundle_path)
        if not isinstance(data, dict):
            raise ValueError(f"primary bundle must be an object: {bundle_path}")
        observed = _observed_size(data)
        if observed is None:
            continue
        corrected, previous = _correct(data, observed)
        if not previous:
            continue
        if _semantic_signature(data) != _semantic_signature(corrected):
            raise ValueError(f"semantic fields changed during warehouse size correction: {bundle_path}")

        corrected_raw = canonical_json_bytes(corrected)
        old_hash, new_hash = _sha256(raw), _sha256(corrected_raw)

        manifest = _manifest_path(bundle_path)
        manifest_changed = False
        if manifest is not None:
            manifest_raw = manifest.read_bytes()
            manifest_data = json.loads(manifest_raw)
            if manifest_data.get("bundle_hash") != old_hash:
                raise ValueError(f"manifest bundle_hash does not match the bundle before correction: {manifest}")
            updated_manifest_raw = manifest_raw.replace(old_hash.encode(), new_hash.encode(), 1)
            _queue_write(manifest, updated_manifest_raw, pending_writes, write=write)
            manifest_changed = True

        _queue_write(bundle_path, corrected_raw, pending_writes, write=write)
        entries.append(
            {
                "file": bundle_path.relative_to(bundles_dir).as_posix(),
                "observed_warehouse_size": observed,
                "previous_warehouse_size": previous,
                "old_sha256": old_hash,
                "new_sha256": new_hash,
                "old_result_id": transformer.result_id_from_bundle(bundle_path, data=data, raw=raw),
                "new_result_id": transformer.result_id_from_bundle(bundle_path, data=corrected, raw=corrected_raw),
                "manifest_updated": manifest_changed,
            }
        )

    entries.sort(key=lambda entry: entry["file"])
    summary = {
        "reason": REASON,
        "bundles": entries,
        "totals": {
            "corrected": len(entries),
            "manifests_updated": sum(1 for entry in entries if entry["manifest_updated"]),
        },
    }

    if write:
        if record_path.exists():
            if pending_writes:
                raise FileExistsError(
                    f"refusing to correct bundles while preserving an existing correction record: {record_path}"
                )
            return summary
        for path, payload in pending_writes:
            _atomic_write(path, payload)
        _atomic_write(record_path, canonical_json_bytes(summary))

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bundles-dir", type=Path, default=BUNDLES_DIR)
    parser.add_argument("--record", type=Path, default=DEFAULT_RECORD)
    parser.add_argument("--write", action="store_true", help="rewrite bundles and manifests atomically")
    args = parser.parse_args(argv)

    summary = correct(bundles_dir=args.bundles_dir, write=args.write, record_path=args.record)
    mode = "WROTE" if args.write else "DRY RUN"
    print(f"{mode}: {summary['totals']['corrected']} bundles corrected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
