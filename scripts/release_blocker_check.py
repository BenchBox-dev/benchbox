from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any

BLOCKING_LABEL = "release-blocking"
REPOSITORY = "BenchBox-dev/BenchBox"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def blocking_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blockers = []
    for issue in issues:
        _require(isinstance(issue, dict), "malformed issue record")
        labels = issue.get("labels")
        if not isinstance(labels, list):
            raise ValueError("issue labels are malformed")
        names = set()
        for label in labels:
            _require(isinstance(label, dict) and isinstance(label.get("name"), str), "issue label is malformed")
            names.add(label["name"])
        if issue.get("state") == "open" and BLOCKING_LABEL in names:
            blockers.append(issue)
    return blockers


def fetch_open_labelled_issues() -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for page in range(1, 101):
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{REPOSITORY}/issues?state=open&labels={BLOCKING_LABEL}&per_page=100&page={page}",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        batch = json.loads(result.stdout)
        _require(isinstance(batch, list), "issues response is not a list")
        issues.extend(batch)
        if len(batch) < 100:
            return issues
    raise ValueError("issues pagination limit exceeded")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refuse a release while a release-blocking issue is open.")
    parser.parse_args(argv)
    try:
        blockers = blocking_issues(fetch_open_labelled_issues())
        _require(not blockers, f"open release-blocking issues: {[issue.get('number') for issue in blockers]}")
    except (OSError, ValueError, TypeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        parser.exit(1, f"Release blocker check failed: {exc}\n")
    print(json.dumps({"blocking_label": BLOCKING_LABEL, "open_blockers": 0}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
