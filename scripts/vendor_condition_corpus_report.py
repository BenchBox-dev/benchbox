#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
if str(CHECKOUT_ROOT) not in sys.path:
    sys.path.insert(0, str(CHECKOUT_ROOT))

from benchbox.validation.bundle import (
    BUNDLE_RUN_TIMESTAMP_KEY,
    MANDATORY_VENDOR_FIELDS,
    MANDATORY_VENDOR_PLATFORMS,
    VENDOR_CONDITIONS_MANDATORY_FROM,
    VENDOR_STATUS_EVIDENCE_BACKED,
    VENDOR_STATUS_PRE_POLICY,
    _manifest_with_bundle_identity,
    bundle_vendor_publication_status,
    evaluate_vendor_conditions,
    vendor_platform_key,
)

STATUS_WITHDRAWN = "withdrawn"
STATUS_NONE = "none"
STATUSES = (VENDOR_STATUS_EVIDENCE_BACKED, VENDOR_STATUS_PRE_POLICY, STATUS_WITHDRAWN, STATUS_NONE)
EVIDENCE_KEYS = frozenset({"plan_tier", "account_tier", "vendor_notice", "self_run", "vendor_permission"})


def _bundle_evidence_keys(node: object) -> set[str]:
    if isinstance(node, dict):
        found = {key for key in node if key in EVIDENCE_KEYS}
        for value in node.values():
            found |= _bundle_evidence_keys(value)
        return found
    if isinstance(node, list):
        found: set[str] = set()
        for item in node:
            found |= _bundle_evidence_keys(item)
        return found
    return set()


def _load_withdrawn(path: Path | None) -> frozenset[str]:
    if path is None:
        return frozenset()
    return frozenset(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _load_bundle(bundle_path: Path) -> dict | None:
    try:
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return bundle if isinstance(bundle, dict) else None


def classify_corpus(bundles_dir: Path, withdrawn: frozenset[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for manifest_path in sorted(bundles_dir.glob("*.manifest.json")):
        bundle_path = manifest_path.with_name(manifest_path.name.removesuffix(".manifest.json") + ".json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        bundle = _load_bundle(bundle_path)
        if bundle is None:
            if "bundle_file" in manifest:
                rows.append(_row(bundle_path.name, "unknown", "", [], [], STATUS_NONE))
            continue
        merged = _manifest_with_bundle_identity(manifest, bundle_path)
        platform_name = str(merged.get("platform") or "")
        if not platform_name:
            rows.append(_row(bundle_path.name, "unknown", "", [], [], STATUS_NONE))
            continue
        if vendor_platform_key(platform_name) not in MANDATORY_VENDOR_PLATFORMS:
            continue
        present = [field for field in MANDATORY_VENDOR_FIELDS if manifest.get(field) is not None]
        evidence = sorted(_bundle_evidence_keys(bundle))
        status = bundle_vendor_publication_status(manifest, bundle_path)
        errors, _ = evaluate_vendor_conditions(merged)
        if bundle_path.name in withdrawn:
            status = STATUS_WITHDRAWN
        elif errors or status not in (VENDOR_STATUS_EVIDENCE_BACKED, VENDOR_STATUS_PRE_POLICY):
            status = STATUS_NONE
        rows.append(
            _row(
                bundle_path.name,
                platform_name,
                str(merged.get(BUNDLE_RUN_TIMESTAMP_KEY) or "")[:10],
                present,
                evidence,
                status,
            )
        )
    return rows


def _row(
    bundle: str, platform: str, run_date: str, present: list[str], evidence: list[str], status: str
) -> dict[str, str]:
    return {
        "bundle": bundle,
        "platform": platform,
        "run_date": run_date,
        "fields_present": ",".join(present) or "-",
        "run_record_evidence": ",".join(evidence) or "none",
        "status": status,
    }


def render_report(rows: list[dict[str, str]]) -> str:
    counts = Counter(row["status"] for row in rows)
    lines = [
        f"cutoff: {VENDOR_CONDITIONS_MANDATORY_FROM}",
        f"manifests: {len(rows)}",
        *(f"{status}: {counts.get(status, 0)}" for status in STATUSES),
        "",
        "| Platform | Run date | Fields present | Run-record evidence | Status | Bundle |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    lines.extend(
        f"| {row['platform']} | {row['run_date']} | {row['fields_present']} | {row['run_record_evidence']} "
        f"| {row['status']} | {row['bundle']} |"
        for row in sorted(rows, key=lambda r: (r["platform"], r["run_date"], r["bundle"]))
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Classify BigQuery, Snowflake and Databricks corpus manifests.")
    parser.add_argument("--bundles-dir", type=Path, default=CHECKOUT_ROOT / "results-data" / "bundles")
    parser.add_argument("--withdrawn", type=Path, default=None, help="File listing withdrawn bundle file names")
    args = parser.parse_args(argv)
    rows = classify_corpus(args.bundles_dir, _load_withdrawn(args.withdrawn))
    print(render_report(rows))
    return 1 if any(row["status"] == STATUS_NONE for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
