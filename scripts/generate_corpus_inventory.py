#!/usr/bin/env python3

from __future__ import annotations

import argparse
import functools
import hashlib
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

BUNDLES_DIR = Path("results-data/bundles")
INVENTORY_PATH = Path("results-data/corpus-inventory.json")

SUBMISSION_MANIFEST = "submission-manifest.json"
SUBMISSION_MANIFEST_SUFFIX = ".manifest.json"

CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
if str(CHECKOUT_ROOT) not in sys.path:
    sys.path.insert(0, str(CHECKOUT_ROOT))

from benchbox.validation.bundle import discover_bundles as discover_primary_bundles

CLI_DESCRIPTION = "Generate `results-data/corpus-inventory.json` from schema-v2 bundles."

try:
    from benchbox.core.results.provenance import DEFAULT_FUNDING, FUNDING_SOURCES, SOURCE_TO_TRUST_LABEL
except ImportError:  # pragma: no cover
    FUNDING_SOURCES = ("employer", "personal", "free-trial", "vendor-sponsored", "grant", "unspecified")
    DEFAULT_FUNDING = "unspecified"
    SOURCE_TO_TRUST_LABEL = {
        "internal": "maintainer-run",
        "community": "community-submission",
        "vendor": "vendor-supplied",
    }

DEFAULT_TRUST_LABEL = SOURCE_TO_TRUST_LABEL["internal"]
COMMUNITY_TRUST_LABEL = SOURCE_TO_TRUST_LABEL["community"]
VENDOR_TRUST_LABEL = SOURCE_TO_TRUST_LABEL["vendor"]

VENDOR_SUBTREE_COMPONENT = "vendor"


def _normalize_funding(value: object) -> str:
    token = str(value).strip().lower() if value is not None else ""
    return token if token in FUNDING_SOURCES else DEFAULT_FUNDING


@functools.cache
def _load_corpus_validator():
    path = CHECKOUT_ROOT / "results-data" / "validate_corpus.py"
    spec = importlib.util.spec_from_file_location("validate_corpus", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cohort_phase_suffix(bundle_path: Path) -> str:
    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    return _load_corpus_validator().cohort_phase_suffix(payload)


def discover_bundles(bundles_dir: Path) -> list[Path]:
    return discover_primary_bundles(bundles_dir)


def _bundle_hash(bundle_path: Path) -> str:
    return hashlib.sha256(bundle_path.read_bytes()).hexdigest()


def _is_vendor_subtree(bundle_path: Path, bundles_dir: Path) -> bool:
    try:
        rel = bundle_path.relative_to(bundles_dir)
    except ValueError:
        return False
    return len(rel.parts) >= 2 and rel.parts[0] == VENDOR_SUBTREE_COMPONENT


def _bundle_trust_label(bundle_path: Path, bundles_dir: Path) -> str:
    if _is_vendor_subtree(bundle_path, bundles_dir):
        return VENDOR_TRUST_LABEL

    manifest = _read_submission_manifest(bundle_path)
    if manifest is None:
        return DEFAULT_TRUST_LABEL

    source = manifest.get("result_source")
    if source in SOURCE_TO_TRUST_LABEL:
        return SOURCE_TO_TRUST_LABEL[source]
    return COMMUNITY_TRUST_LABEL


def _read_submission_manifest(bundle_path: Path) -> dict | None:
    for candidate in (
        bundle_path.parent / f"{bundle_path.stem}{SUBMISSION_MANIFEST_SUFFIX}",
        bundle_path.parent / SUBMISSION_MANIFEST,
    ):
        if candidate.is_file():
            try:
                loaded = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
            return loaded if isinstance(loaded, dict) else {}
    return None


def _bundle_funding(bundle_path: Path, data: dict) -> str:
    for name in (f"{bundle_path.stem}{SUBMISSION_MANIFEST_SUFFIX}", SUBMISSION_MANIFEST):
        sidecar = bundle_path.parent / name
        if sidecar.is_file():
            try:
                manifest = json.loads(sidecar.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                break
            if isinstance(manifest, dict) and manifest.get("funding"):
                return _normalize_funding(manifest.get("funding"))
            break

    provenance = data.get("provenance")
    if isinstance(provenance, dict) and provenance.get("funding"):
        return _normalize_funding(provenance.get("funding"))

    return DEFAULT_FUNDING


def _query_count(bundle_data: dict) -> int:
    summary = bundle_data.get("summary", {})
    queries = summary.get("queries", {})
    if isinstance(queries, dict) and "total" in queries:
        return int(queries["total"])
    return len(bundle_data.get("queries", []))


def _platform_version(data: dict) -> str:
    platform = data.get("platform", {})
    execution = data.get("execution", {})
    if isinstance(platform, dict) and str(platform.get("name", "")).lower() == "duckdb":
        if isinstance(execution, dict):
            for key in (
                "driver_version_resolved",
                "driver_version_requested",
                "driver_resolved_version",
                "driver_requested_version",
            ):
                value = execution.get(key)
                if value and value != "unknown":
                    return str(value)
        value = platform.get("client_version") if isinstance(platform, dict) else None
        if value and value != "unknown":
            return str(value)
    value = platform.get("version") if isinstance(platform, dict) else None
    return str(value) if value and value != "unknown" else "unknown"


def extract_metadata(bundle_path: Path, bundles_dir: Path) -> dict:
    try:
        data = json.loads(bundle_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed bundle JSON: {bundle_path}") from exc

    benchmark = data.get("benchmark", {})
    platform = data.get("platform", {})
    run = data.get("run", {})
    rel_path = bundle_path.relative_to(bundles_dir).as_posix()

    return {
        "file": rel_path,
        "benchmark": benchmark.get("id", "unknown"),
        "benchmark_name": benchmark.get("name", benchmark.get("id", "unknown")),
        "platform": platform.get("name", "unknown"),
        "platform_version": _platform_version(data),
        "scale_factor": benchmark.get("scale_factor", 0),
        "phase": _load_corpus_validator().bundle_phase(data),
        "timestamp": run.get("timestamp"),
        "query_count": _query_count(data),
        "trust_label": _bundle_trust_label(bundle_path, bundles_dir),
        "funding": _bundle_funding(bundle_path, data),
        "bundle_sha256": _bundle_hash(bundle_path),
    }


def _is_public_benchmark(benchmark_id: str) -> bool:
    try:
        from benchbox.core.benchmark_registry import get_benchmark_surface
    except ImportError:
        return True
    return get_benchmark_surface(benchmark_id) == "public"


def generate_inventory(bundles_dir: Path) -> dict:
    bundle_paths = discover_bundles(bundles_dir)
    entries = []
    for path in bundle_paths:
        entry = extract_metadata(path, bundles_dir)
        if _is_public_benchmark(entry["benchmark"]):
            entries.append(entry)

    entries.sort(
        key=lambda e: (
            e["benchmark"],
            e["platform"],
            str(e["scale_factor"]),
            e.get("timestamp") or "",
            e["file"],
        )
    )

    cohort_members: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    for entry in entries:
        key = (entry["benchmark"], str(entry["scale_factor"]) + _cohort_phase_suffix(bundles_dir / entry["file"]))
        identity = entry["platform"]
        if entry["platform_version"] != "unknown":
            identity = f"{identity} v{entry['platform_version']}"
        cohort_members[key].add(identity)

    cohorts: dict[str, list[str]] = {}
    for (benchmark, scale_factor), platforms in sorted(cohort_members.items()):
        cohorts[f"{benchmark}@sf{scale_factor}"] = sorted(platforms)

    by_benchmark = Counter(entry["benchmark"] for entry in entries)
    by_platform = Counter(entry["platform"] for entry in entries)
    by_trust_label = Counter(entry["trust_label"] for entry in entries)
    by_funding = Counter(entry["funding"] for entry in entries)
    by_phase = Counter(entry["phase"] for entry in entries)

    return {
        "schema_version": "2.3",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bundles": entries,
        "cohorts": cohorts,
        "summary": {
            "total_bundles": len(entries),
            "by_benchmark": dict(sorted(by_benchmark.items())),
            "by_platform": dict(sorted(by_platform.items())),
            "by_trust_label": dict(sorted(by_trust_label.items())),
            "by_funding": dict(sorted(by_funding.items())),
            "by_phase": dict(sorted(by_phase.items())),
        },
    }


def _normalized_inventory(inventory: dict) -> dict:
    normalized = dict(inventory)
    normalized.pop("generated_at", None)
    return normalized


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Fail if the on-disk inventory is stale.")
    mode.add_argument("--write", action="store_true", help="Write the regenerated inventory to disk.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    check_mode = args.check

    if not BUNDLES_DIR.is_dir():
        print(f"Error: {BUNDLES_DIR} not found", file=sys.stderr)
        return 1

    inventory = generate_inventory(BUNDLES_DIR)

    if check_mode:
        if not INVENTORY_PATH.exists():
            print("FAIL: corpus-inventory.json does not exist", file=sys.stderr)
            return 1

        existing = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
        if _normalized_inventory(existing) == _normalized_inventory(inventory):
            print(f"OK: corpus-inventory.json is up-to-date ({len(inventory['bundles'])} bundles)")
            return 0

        existing_files = {e["file"] for e in existing.get("bundles", [])}
        new_files = {e["file"] for e in inventory["bundles"]}
        added = sorted(new_files - existing_files)
        removed = sorted(existing_files - new_files)
        if added:
            print(f"Missing from inventory: {added}", file=sys.stderr)
        if removed:
            print(f"Stale in inventory: {removed}", file=sys.stderr)
        print(
            "FAIL: corpus-inventory.json is out of date. Run: uv run -- python scripts/generate_corpus_inventory.py --write",
            file=sys.stderr,
        )
        return 1

    INVENTORY_PATH.write_text(
        json.dumps(inventory, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Generated {INVENTORY_PATH} with {len(inventory['bundles'])} bundles")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
