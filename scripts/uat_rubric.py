from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.uat import rubric

DEFAULT_STAGE_CONFIGS = (
    "tests/uat/configs/release-gate-01-native-dataframe.yaml",
    "tests/uat/configs/release-gate-02-docker-nonoltp.yaml",
    "tests/uat/configs/release-gate-03-docker-oltp.yaml",
)
PROPOSALS_FILENAME = "uat_rubric_proposals.json"
SIGNAL_RECORD_FILENAME = "uat_signal_record.json"


def gated_scales_from_configs(paths: Sequence[Path]) -> dict[tuple[str, str], set[float]]:
    from tests.uat.gate_summary import load_stage_plan

    gated: dict[tuple[str, str], set[float]] = {}
    for path in paths:
        for platform, benchmark, scale in load_stage_plan(path).cells:
            gated.setdefault((platform, benchmark), set()).add(scale)
    return gated


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report infrastructure-flaky UAT cells, envelope proposals and the per-cell signal record"
    )
    parser.add_argument(
        "--history-dir",
        type=Path,
        required=True,
        help="Directory holding trunk UAT run directories (each with cells.jsonl and uat_gate_summary.json)",
    )
    parser.add_argument("--output-dir", type=Path, default=Path.cwd(), help="Where the two JSON reports are written")
    parser.add_argument(
        "--stage-config",
        action="append",
        type=Path,
        default=None,
        help="Release-gate config defining the currently gated cells (repeatable; default: the three gate stages)",
    )
    parser.add_argument("--window", type=int, default=rubric.WINDOW_RUNS, help="Runs per cell the rate is taken over")
    parser.add_argument(
        "--threshold",
        type=float,
        default=rubric.INFRASTRUCTURE_RATE_THRESHOLD,
        help="Infrastructure-failure rate a cell must exceed to be flagged",
    )
    parser.add_argument(
        "--min-runs", type=int, default=rubric.WINDOW_RUNS, help="Runs of history a cell needs to enter the drop list"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.history_dir.is_dir():
        print(f"history directory not found: {args.history_dir}", file=sys.stderr)
        return 2
    stage_configs = args.stage_config or [REPO_ROOT / name for name in DEFAULT_STAGE_CONFIGS]
    runs = rubric.load_history(args.history_dir)
    gated = gated_scales_from_configs(stage_configs)
    proposals = rubric.build_envelope_report(runs, gated, window=args.window, threshold=args.threshold)
    signals = rubric.build_signal_report(runs, gated, min_runs=args.min_runs)
    proposals_path = rubric.write_report(args.output_dir / PROPOSALS_FILENAME, proposals)
    signals_path = rubric.write_report(args.output_dir / SIGNAL_RECORD_FILENAME, signals)
    drop_list = signals["drop_list"]
    print(f"history runs: {len(runs)}")
    print(f"envelope proposals: {len(proposals['proposals'])} -> {proposals_path}")
    print(f"tracker item suggestions (last gated scale): {len(proposals['tracker_item_suggestions'])}")
    print(f"drop-list candidates for owner review: {len(drop_list['candidates'])} -> {signals_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
