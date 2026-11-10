from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STAGES = (1, 2, 3)
GATE_SUMMARY_FILENAME = "uat_gate_summary.json"
RUN_DIRECTORY = "run"


def _attempted_cells(summary_path: Path) -> tuple[int | None, str | None]:
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"gate summary {summary_path} is unreadable: {exc}"
    accounting = payload.get("accounting") if isinstance(payload, dict) else None
    attempted = accounting.get("attempted") if isinstance(accounting, dict) else None
    if isinstance(attempted, bool) or not isinstance(attempted, int):
        return None, f"gate summary {summary_path} records no integer 'accounting.attempted'"
    return attempted, None


def check_evidence(root: Path, tag: str) -> tuple[list[str], int, int]:
    violations: list[str] = []
    shards = 0
    executed = 0
    for stage in STAGES:
        artifacts = sorted(path for path in root.glob(f"release-uat-{tag}-stage{stage}-*") if path.is_dir())
        if not artifacts:
            violations.append(f"stage {stage}: no shard evidence directory was downloaded under {root}")
            continue
        for artifact in artifacts:
            shards += 1
            summary_path = artifact / RUN_DIRECTORY / GATE_SUMMARY_FILENAME
            if not summary_path.is_file():
                violations.append(
                    f"{artifact.name}: no {RUN_DIRECTORY}/{GATE_SUMMARY_FILENAME}; the shard staged no run"
                )
                continue
            attempted, problem = _attempted_cells(summary_path)
            if problem is not None:
                violations.append(f"{artifact.name}: {problem}")
            elif attempted <= 0:
                violations.append(f"{artifact.name}: the shard executed zero cells")
            else:
                executed += attempted
    if shards and executed == 0:
        violations.append("no shard executed any cell")
    return violations, shards, executed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when the release UAT evidence holds no shards or no executed cells"
    )
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args(argv)
    violations, shards, executed = check_evidence(args.root, args.tag)
    for violation in violations:
        print(f"::error::{violation}", file=sys.stderr)
    if violations:
        return 1
    print(f"release UAT evidence for {args.tag}: {shards} shard run(s), {executed} executed cell(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
