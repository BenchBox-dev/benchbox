#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

CLI_DESCRIPTION = "Summarise failed cross-engine comparison queries from a pytest JSON report."


def build_diff_artifact(report: dict[str, Any], *, comparison: str, scale_factor: float) -> dict[str, Any]:
    failed = [test for test in report.get("tests", []) if test.get("outcome") == "failed"]
    return {
        "benchmark": "tpch",
        "scale_factor": scale_factor,
        "reference_platform": "duckdb",
        "comparison_platform": comparison,
        "failed_queries": [
            {
                "query_id": test["nodeid"].rsplit("[", 1)[-1].rstrip("]"),
                "longrepr": (test.get("call") or {}).get("longrepr", ""),
            }
            for test in failed
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--comparison", required=True)
    parser.add_argument("--scale-factor", type=float, default=0.01)
    args = parser.parse_args(argv)

    if not args.report.is_file():
        print(f"No report file found at {args.report}")
        return 0
    report = json.loads(args.report.read_text(encoding="utf-8"))
    artifact = build_diff_artifact(report, comparison=args.comparison, scale_factor=args.scale_factor)
    args.output.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(f"Wrote {args.output} with {len(artifact['failed_queries'])} failed queries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
