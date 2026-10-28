#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.site_inputs import repo_file_entries

CLI_DESCRIPTION = "Fail when a core path that the published site documentation links to is missing from this tree."
INVENTORY_URL = "https://raw.githubusercontent.com/BenchBox-dev/benchbox-site/main/inventory/core-linked-paths.json"
FETCH_TIMEOUT_SECONDS = 20
EXIT_MISSING = 1
RENAME_ORDER = (
    "To delete or rename a path the site documentation links to: first drop the link in benchbox-site or pin it to a "
    "core commit SHA and merge that, then merge the core change, then relink the new path in benchbox-site."
)


class InventoryUnavailableError(RuntimeError):
    pass


def parse_inventory(text: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise InventoryUnavailableError(f"inventory is not valid JSON: {error}") from error
    paths = payload.get("paths") if isinstance(payload, dict) else None
    if not isinstance(paths, list):
        raise InventoryUnavailableError("inventory has no 'paths' list")
    for entry in paths:
        valid = (
            isinstance(entry, dict)
            and isinstance(entry.get("path"), str)
            and isinstance(entry.get("sources"), list)
            and all(isinstance(source, str) for source in entry["sources"])
        )
        if not valid:
            raise InventoryUnavailableError(f"inventory entry is malformed: {entry!r}")
    return paths


def fetch_inventory(url: str) -> list[dict[str, Any]]:
    request = urllib.request.Request(url, headers={"User-Agent": "BenchBox-Site-Linked-Paths"})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, UnicodeDecodeError, OSError) as error:
        raise InventoryUnavailableError(f"cannot fetch {url}: {error}") from error
    return parse_inventory(body)


def read_inventory(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.inventory_file is None:
        return fetch_inventory(args.inventory_url)
    try:
        return parse_inventory(args.inventory_file.read_text(encoding="utf-8"))
    except OSError as error:
        raise InventoryUnavailableError(f"cannot read {args.inventory_file}: {error}") from error


def missing_entries(inventory: Sequence[dict[str, Any]], present: set[str]) -> list[dict[str, Any]]:
    return [entry for entry in inventory if entry["path"] not in present]


def report(missing: Sequence[dict[str, Any]]) -> str:
    lines = [f"{len(missing)} core path(s) linked from the published site documentation are missing from this tree:"]
    for entry in missing:
        lines.append(f"  {entry['path']}")
        lines.extend(f"    linked from {source}" for source in entry["sources"])
    lines.append(RENAME_ORDER)
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--inventory-url", default=INVENTORY_URL)
    parser.add_argument("--inventory-file", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        inventory = read_inventory(args)
    except InventoryUnavailableError as error:
        print(f"warning: site-linked path check skipped: {error}", file=sys.stderr)
        return 0
    present = {entry["path"] for entry in repo_file_entries()}
    missing = missing_entries(inventory, present)
    if missing:
        print(report(missing), file=sys.stderr)
        return EXIT_MISSING
    print(f"site-linked paths OK: all {len(inventory)} listed core paths exist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
