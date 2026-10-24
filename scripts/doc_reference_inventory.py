#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "quality/docs-reference-rules.json"
INVENTORY_PATH = ROOT / "quality/docs-reference-inventory.json"
LOCK_TESTS_PATH = ROOT / "quality/doc-lock-tests.json"
OWNERSHIP_PATH = ROOT / "quality/docs-ownership.json"
SELF_EXCLUDED = {
    "quality/docs-reference-rules.json",
    "quality/docs-reference-inventory.json",
    "quality/docs-ownership.json",
    "quality/doc-lock-tests.json",
}
DISPOSITIONS = (
    "contract-replaced",
    "governance-stays",
    "lock-test",
    "site-pipeline-cutover",
    "generator-deleted",
    "wiring-updated",
    "prose-link-pinned",
    "runtime-link-updated",
    "historical-record",
    "fixture-data",
    "built-site-url",
    "contract-tooling",
)
KINDS = ("file", "dir", "root", "pattern", "generated", "built-url", "extra", "unresolved")
DOCS_TOKEN = re.compile(r"(?<![A-Za-z0-9_-]/)(?<![A-Za-z0-9_-])docs/[A-Za-z0-9_./*{}\[\]-]*")
DOCS_SEGMENTS = re.compile(r"""["']docs["']((?:\s*[/,]\s*["'][^"'\n]+["'])*)""")
SEGMENT = re.compile(r"""["']([^"'\n]+)["']""")
TRAILING = ".,;:)'\"`>"


def tracked_files() -> list[str]:
    result = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, text=True, check=True)
    return sorted(path for path in result.stdout.split("\0") if path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_rules() -> dict[str, Any]:
    rules = read_json(RULES_PATH)
    for rule in rules["rules"]:
        if rule["disposition"] not in DISPOSITIONS:
            raise ValueError(f"rule {rule['id']!r} has unknown disposition {rule['disposition']!r}")
        if rule["disposition"] == "contract-replaced" and not rule.get("contract_fields"):
            raise ValueError(f"rule {rule['id']!r} is contract-replaced without contract_fields")
    return rules


class Targets:
    def __init__(self, extras: list[str]) -> None:
        manifest = read_json(OWNERSHIP_PATH)
        owners: dict[str, str] = manifest["files"]
        self.site_files = {path for path, owner in owners.items() if owner != "core-only"}
        self.core_files = {path for path, owner in owners.items() if owner == "core-only"}
        self.generated = [record["path"] for record in manifest["generated"]]
        transition_sources = {source for record in manifest["transitions"].values() for source in record["sources"]}
        self.site_files |= transition_sources
        self.core_files -= transition_sources
        self.extras = set(extras)

    def classify(self, raw: str) -> tuple[str, str] | None:
        target = raw.rstrip(TRAILING)
        if target in self.extras:
            return target, "extra"
        if not target.startswith("docs"):
            return None
        if target in {"docs", "docs/"}:
            return "docs/", "root"
        if any(char in target for char in "*{}[]"):
            return target, "pattern"
        if target.endswith(".html"):
            return target, "built-url"
        if target in self.site_files:
            return target, "file"
        if any(target == output.rstrip("/") or target.startswith(output) for output in self.generated):
            return target, "generated"
        prefix = target if target.endswith("/") else target + "/"
        if any(path.startswith(prefix) for path in self.site_files):
            return prefix, "dir"
        if target in self.core_files or any(path.startswith(prefix) for path in self.core_files):
            return None
        return target, "unresolved"


def extract(text: str, targets: Targets) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for match in DOCS_TOKEN.finditer(text):
        resolved = targets.classify(match.group())
        if resolved is not None:
            found.append(resolved)
    for extra in targets.extras:
        for _ in re.finditer(re.escape(extra), text):
            found.append((extra, "extra"))
    for match in DOCS_SEGMENTS.finditer(text):
        segments = SEGMENT.findall(match.group(1))
        joined = "/".join(["docs", *segments])
        resolved = targets.classify(joined if segments else "docs")
        if resolved is not None:
            found.append(resolved)
    return found


def scan(files: list[str], targets: Targets) -> dict[str, Counter[tuple[str, str]]]:
    rows: dict[str, Counter[tuple[str, str]]] = {}
    for path in files:
        if path.startswith("docs/") or path in SELF_EXCLUDED:
            continue
        try:
            text = (ROOT / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        found = extract(text, targets)
        if found:
            rows[path] = Counter(found)
    return rows


def glob_matches(path: str, pattern: str) -> bool:
    regex = ""
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            regex += "(?:.*/)?"
            index += 3
        elif pattern.startswith("**", index):
            regex += ".*"
            index += 2
        elif pattern[index] == "*":
            regex += "[^/]*"
            index += 1
        elif pattern[index] == "?":
            regex += "[^/]"
            index += 1
        else:
            regex += re.escape(pattern[index])
            index += 1
    return re.fullmatch(regex, path) is not None


def match_rule(path: str, rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    for rule in rules:
        if any(glob_matches(path, pattern) for pattern in rule["match"]):
            return rule
    return None


def build_inventory() -> dict[str, Any]:
    rules_doc = load_rules()
    rules = rules_doc["rules"]
    targets = Targets(rules_doc["extra_targets"])
    scanned = scan(tracked_files(), targets)
    entries: list[dict[str, Any]] = []
    unclassified: list[str] = []
    used: Counter[str] = Counter()
    for path, counter in sorted(scanned.items()):
        rule = match_rule(path, rules)
        if rule is None:
            unclassified.append(path)
            continue
        used[rule["id"]] += 1
        unique = sorted(counter)
        entry: dict[str, Any] = {
            "path": path,
            "rule": rule["id"],
            "disposition": rule["disposition"],
            "occurrences": sum(counter.values()),
            "target_count": len(unique),
            "kinds": dict(sorted(Counter(kind for _target, kind in unique).items())),
        }
        if rule.get("aggregate"):
            digest = hashlib.sha256("\n".join(f"{kind} {target}" for target, kind in unique).encode()).hexdigest()
            entry["targets_sha256"] = digest
        else:
            entry["targets"] = [{"target": target, "kind": kind} for target, kind in unique]
        entries.append(entry)
    if unclassified:
        raise ValueError(f"unclassified references to site-owned paths: {unclassified}")
    stale = sorted(rule["id"] for rule in rules if used[rule["id"]] == 0)
    if stale:
        raise ValueError(f"rules matching no referencing file: {stale}")
    return {
        "schema": 1,
        "extra_targets": sorted(rules_doc["extra_targets"]),
        "summary": {
            "files": len(entries),
            "references": sum(entry["occurrences"] for entry in entries),
            "dispositions": dict(sorted(Counter(entry["disposition"] for entry in entries).items())),
            "kinds": dict(
                sorted(Counter(kind for counter in scanned.values() for _target, kind in counter.elements()).items())
            ),
            "unclassified": 0,
        },
        "files": entries,
    }


def render(inventory: dict[str, Any]) -> str:
    return json.dumps(inventory, indent=1, sort_keys=True) + "\n"


def pointer_exists(document: Any, pointer: str) -> bool:
    node = document
    for part in pointer.lstrip("/").split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return False
    return True


LOCK_CLASSIFICATIONS = (
    "contract-field",
    "governance-stays",
    "wiring-deleted",
    "incidental-deleted",
    "runtime-stays",
    "generated-page-cutover",
)


def check_lock_tests(contracts: dict[str, Any], inventory: dict[str, Any]) -> list[str]:
    table = read_json(LOCK_TESTS_PATH)
    errors: list[str] = []
    seen: set[str] = set()
    for row in table["lock_tests"]:
        ident = row["id"]
        if ident in seen:
            errors.append(f"duplicate lock-test row {ident}")
        seen.add(ident)
        path = ROOT / row["file"]
        if not path.is_file():
            errors.append(f"{ident}: missing file {row['file']}")
            continue
        text = path.read_text(encoding="utf-8")
        for name in row.get("tests", []):
            if not re.search(rf"\b(?:def|class) {re.escape(name)}\b", text):
                errors.append(f"{ident}: {row['file']} defines no {name}")
        if row["classification"] not in LOCK_CLASSIFICATIONS:
            errors.append(f"{ident}: unknown classification {row['classification']!r}")
        fields = row.get("contract_fields", [])
        if row["classification"] == "contract-field" and not fields:
            errors.append(f"{ident}: contract-field row names no contract field")
        for pointer in fields:
            if not pointer_exists(contracts, pointer):
                errors.append(f"{ident}: contract field {pointer} does not exist")
        if not row.get("reason"):
            errors.append(f"{ident}: missing reason")
    covered = {row["file"] for row in table["lock_tests"]}
    for entry in inventory["files"]:
        if entry["disposition"] == "lock-test" and entry["path"] not in covered:
            errors.append(f"{entry['path']} references site-owned paths but has no lock-test row")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enumerate every reference to site-owned documentation paths.")
    parser.add_argument("--check", action="store_true", help="Fail when the committed inventory is stale.")
    args = parser.parse_args(argv)
    try:
        text = render(build_inventory())
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    if args.check:
        current = INVENTORY_PATH.read_text(encoding="utf-8") if INVENTORY_PATH.exists() else ""
        if current != text:
            print(
                f"{INVENTORY_PATH.relative_to(ROOT)} is stale; run make docs-reference-inventory",
                file=sys.stderr,
            )
            return 1
        return 0
    INVENTORY_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {INVENTORY_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
