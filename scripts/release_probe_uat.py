from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
CONFIGS = REPOSITORY / "tests" / "uat" / "configs"
STAGE_CONFIGS = (
    "release-gate-01-native-dataframe",
    "release-gate-02-docker-nonoltp",
    "release-gate-03-docker-oltp",
)
DOCKER_STAGES = (2, 3)
STAGE_SPACING = dt.timedelta(minutes=3)
PROBE_HOST = "release-probe"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cell_row(key: tuple[str, str, float]) -> str:
    platform, benchmark, scale = key
    return json.dumps({"platform": platform, "benchmark": benchmark, "scale": scale, "status": "passed"}) + "\n"


def _lifecycle(stage: int, platforms: list[str], started: dt.datetime) -> str:
    if stage not in DOCKER_STAGES:
        return ""
    lines = [f"{started.isoformat()} [host] id={PROBE_HOST}\n"]
    for offset, platform in enumerate(platforms, start=1):
        moment = started + dt.timedelta(seconds=offset)
        lines.append(f"{moment.isoformat()} [docker] platform={platform} action=up status=ok\n")
    return "".join(lines)


def write_stage(
    destination: Path,
    stage: int,
    *,
    head_sha: str,
    dist_digest_file: Path,
    begun: dt.datetime,
    configs: Path = CONFIGS,
) -> Path:
    from tests.uat import gate_summary
    from tests.uat.config import load_config

    config_path = configs / f"{STAGE_CONFIGS[stage - 1]}.yaml"
    config = load_config(config_path)
    plan = gate_summary.load_stage_plan(config_path)
    platforms = sorted({key[0] for key in plan.cells})
    cells = sorted(plan.cells)
    pruned = sorted(plan.pruned)
    stage_start = begun + STAGE_SPACING * (stage - 1)

    run = destination / "run"
    run.mkdir(parents=True)
    (run / gate_summary.CELLS_FILENAME).write_text("".join(_cell_row(key) for key in cells), encoding="utf-8")
    (run / gate_summary.COMPATIBILITY_PRUNED_FILENAME).write_text(
        "".join(_cell_row(key) for key in pruned), encoding="utf-8"
    )
    (run / "cells.jsonl.accounting.json").write_text("{}\n", encoding="utf-8")
    (run / "uat_lifecycle.log").write_text(_lifecycle(stage, platforms, stage_start), encoding="utf-8")
    shutil.copyfile(dist_digest_file, destination / "dist-digest.txt")

    floor = config.report.cross_scale_coverage_min_pairs or 0
    summary = gate_summary.GateSummary(
        config_name=config.name,
        source_commit_sha=head_sha,
        source_dirty=False,
        container_engine="docker",
        completed_at=(stage_start + STAGE_SPACING / 2).isoformat(),
        dry_run=False,
        aborted=False,
        abort_phase=None,
        abort_reason=None,
        phase_exit_codes={"execute": 0, "validate": 0, "report": 0},
        accounting=gate_summary.PhaseAccounting(attempted=len(cells), passed=len(cells), total_defined=len(cells)),
        unreachable_is_estimated=False,
        validator_clean_rate=1.0,
        validator_clean_rate_floor=config.validate.validator_clean_rate_floor,
        validator_floor_breached=False,
        cross_scale_clean_pairs=floor,
        cross_scale_floor=floor,
        cross_scale_floor_breached=None,
        verdict=gate_summary.VERDICT_GREEN,
        artifact_digests={
            "cells_jsonl": _sha256(run / "cells.jsonl"),
            "accounting_sidecar": _sha256(run / "cells.jsonl.accounting.json"),
            "lifecycle_log": _sha256(run / "uat_lifecycle.log"),
        },
        distribution_digest=_sha256(dist_digest_file),
        shard_platforms=platforms,
    )
    gate_summary.write_gate_summary(run, summary)
    return destination


def emit(output: Path, name: str, head_sha: str, dist_digest_file: Path, configs: Path = CONFIGS) -> list[Path]:
    begun = dt.datetime.now(dt.UTC).replace(tzinfo=None, microsecond=0)
    written = []
    for stage in (1, 2, 3):
        destination = output / f"release-uat-{name}-stage{stage}-probe"
        written.append(
            write_stage(
                destination, stage, head_sha=head_sha, dist_digest_file=dist_digest_file, begun=begun, configs=configs
            )
        )
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write stub release UAT shard evidence for a probe run.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--dist-digest-file", type=Path, required=True)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(REPOSITORY))
    for destination in emit(args.output, args.name, args.head_sha, args.dist_digest_file):
        print(f"wrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
