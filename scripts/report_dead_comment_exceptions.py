from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from check_comment_policy import (
    POLICY_PATH,
    allowed,
    exception_identity,
    expired_exception_identities,
    git,
    load_policy,
    scan_sources,
    source_paths,
)


def dead_exceptions(root: Path, policy: dict) -> list[tuple[dict, str]]:
    sources = {path: (root / path).read_bytes() for path in source_paths(root, False) if (root / path).is_file()}
    findings = scan_sources(root, sources, {**policy, "exceptions": []})
    by_path: dict[str, list] = {}
    for finding in findings:
        by_path.setdefault(finding.path, []).append(finding)
    texts = {path: sources[path].decode("utf-8", "replace") for path in by_path}
    expired = expired_exception_identities(policy)
    dead = []
    for entry in policy["exceptions"]:
        path = entry["path"]
        if not (root / path).exists():
            dead.append((entry, "path missing"))
            continue
        alone = {**policy, "exceptions": [entry]}
        matching = sum(
            1
            for finding in by_path.get(path, [])
            if finding.text == entry["text"] and allowed(finding, alone, texts[path], expired=set())
        )
        registered = entry.get("count", 1)
        is_expired = exception_identity(entry) in expired
        if matching and is_expired:
            dead.append((entry, "expired but live"))
        elif not matching:
            dead.append((entry, "no current finding at path and digest"))
        elif registered > matching:
            dead.append((entry, f"count {registered} exceeds {matching} matching line(s)"))
    return dead


def registration(root: Path, entry: dict) -> str:
    lines = [
        f'      "path": {json.dumps(entry["path"], ensure_ascii=False)},',
        f'      "symbol": {json.dumps(entry["symbol"], ensure_ascii=False)},',
        f'      "text": {json.dumps(entry["text"], ensure_ascii=False)},',
    ]
    if "payload" in entry:
        lines.append(f'      "payload": {json.dumps(entry["payload"], ensure_ascii=False)},')
    needle = "\n".join(lines)
    try:
        if (root / POLICY_PATH).read_text(encoding="utf-8").count(needle) != 1:
            return "registered: ambiguous"
        log = git(root, "log", "--reverse", "--format=%h %s", "-S", needle, "--", POLICY_PATH).decode().splitlines()
    except (OSError, subprocess.CalledProcessError):
        return "registered: unknown"
    if not log:
        return "registered: unknown"
    sha, _, subject = log[0].partition(" ")
    pr = re.search(r"\(#(\d+)\)\s*$", subject)
    return f"{sha} PR #{pr.group(1)}" if pr else sha


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List comment-policy exceptions that no current finding uses.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        policy = load_policy((root / POLICY_PATH).read_bytes())
        dead = dead_exceptions(root, policy)
        for entry, reason in dead:
            digest = entry.get("payload", "")
            print(
                f"{entry['path']}\t{entry['kind']}\t{digest}\t{reason}\t{registration(root, entry)}\t{entry['symbol']}"
            )
        print(
            f"dead-exceptions: {len(dead)} of {len(policy['exceptions'])} registered exceptions are dead or over-counted"
        )
    except (OSError, UnicodeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"dead-exceptions: configuration or parser failure: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
