#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    forbidden_readme_count_claims = [
        "Twenty-Two Benchmarks",
        "SQL Platforms (",
        "DataFrame Platforms (",
    ]
    offenders = [phrase for phrase in forbidden_readme_count_claims if phrase in readme]
    if offenders:
        print(
            "README.md has exact hand-maintained count claims; use registry-derived checks "
            f"or mark them non-authoritative: {offenders}",
            file=sys.stderr,
        )
        return 1

    print("OK: README.md carries no hand-maintained registry count claims.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
