#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

BUNDLES_DIR = Path("results-data/bundles")
SNAPSHOT_PATH = Path("results-data/compliance-classes.json")
SNAPSHOT_SCHEMA_VERSION = 2
REGENERATE_COMMAND = "uv run -- python scripts/corpus_compliance_snapshot.py --write"
SCORE_DECIMALS = 2

CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
if str(CHECKOUT_ROOT) not in sys.path:
    sys.path.insert(0, str(CHECKOUT_ROOT))

from _project.scripts.explorer_pipeline import pipeline as explorer_pipeline, transformer as explorer_transformer
from _project.scripts.explorer_pipeline.models import (
    KNOWN_DEFECT_RANKING_EXCLUSION,
    ManifestEntry,
    canonical_benchmark_slug,
    canonical_phase,
    get_ranking_config,
    ranking_exclusion_reason,
)
from _project.scripts.explorer_pipeline.ranking import rank_platforms
from benchbox.core.results import corpus_rule
from benchbox.core.results.subscale_policy import (
    POWER_QUERY_COUNTS,
    RANKING_EXCLUDED_COMPLIANCE_CLASSES,
    derive_power_at_size,
)
from benchbox.validation.bundle import discover_bundles

CLI_DESCRIPTION = (
    "Record or check the outcome of every committed result bundle as today's readers compute it and as the "
    "corpus rule computes it, so a code change that moves a stored result fails visibly."
)

LANE_PHASES = {
    corpus_rule.LANE_COMBINED: "combined",
    corpus_rule.LANE_POWER_ONLY: "power",
    corpus_rule.LANE_SUBSCALE: "power",
    corpus_rule.LANE_THROUGHPUT: "throughput",
}
POWER_LANES = frozenset({corpus_rule.LANE_COMBINED, corpus_rule.LANE_POWER_ONLY, corpus_rule.LANE_SUBSCALE})

Outcome = dict[str, Any]
Snapshot = dict[str, Outcome]
CohortKey = tuple[str, float, str, int | None]


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), SCORE_DECIMALS)


def cohort_label(key: CohortKey) -> str:
    benchmark, scale_factor, phase, streams = key
    label = f"{benchmark}/sf{scale_factor:g}/{phase}"
    return label if streams is None else f"{label}/streams={streams}"


def throughput_at_size_from_timings(data: Mapping[str, Any], scale_factor: float) -> float | None:
    block = (data.get("phases") or {}).get("throughput_test")
    duration_ms = block.get("duration_ms") if isinstance(block, Mapping) else None
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int | float) or duration_ms <= 0:
        return None
    executed = sum(
        1
        for query in data.get("queries") or []
        if isinstance(query, Mapping)
        and query.get("test_type") == "throughput"
        and query.get("run_type") == "measurement"
    )
    return executed * scale_factor * 3600 / (duration_ms / 1000) if executed else None


class _Loaded:
    def __init__(self, path: Path, name: str, data: dict[str, Any], entry: ManifestEntry, detail: Any) -> None:
        self.path = path
        self.name = name
        self.data = data
        self.entry = entry
        self.detail = detail
        self.slug = canonical_benchmark_slug(entry.benchmark)
        self.phase = canonical_phase(detail.test_type)
        self.today_key: CohortKey = (self.slug, entry.scale_factor, self.phase, detail.stream_count)


def _load(bundles_dir: Path, path: Path, seen: dict[str, str]) -> tuple[_Loaded | None, str | None]:
    name = path.relative_to(bundles_dir).as_posix()
    try:
        data, raw = explorer_transformer._load_bundle(path)
        trust = "maintainer-run"
        visibility = "public-curated"
        if explorer_pipeline._is_vendor_subtree(path, bundles_dir):
            trust, visibility = explorer_pipeline.VENDOR_TRUST_LABEL, explorer_pipeline.VENDOR_VISIBILITY
        else:
            trust = explorer_pipeline._manifest_trust_label(path, trust)
        transformer = explorer_transformer.BundleTransformer()
        result_id = transformer.result_id_from_bundle(path, data=data, raw=raw)
        entry = transformer.to_manifest_entry(
            path, trust_label=trust, visibility=visibility, result_id=result_id, data=data
        )
        detail = transformer.to_detail_result(
            path, result_id, trust_label=trust, visibility=visibility, bundle_download_url="", data=data
        )
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        return None, type(exc).__name__
    if result_id in seen:
        return None, f"duplicate_of:{seen[result_id]}"
    seen[result_id] = name
    return _Loaded(path, name, data, entry, detail), None


def _rank_cohorts(accum: dict[CohortKey, list[tuple[ManifestEntry, Any]]]) -> dict[tuple[CohortKey, str], Outcome]:
    ranks: dict[tuple[CohortKey, str], Outcome] = {}
    summaries = explorer_pipeline._build_benchmark_summaries(accum, {})
    for key, summary in summaries:
        ranked = rank_platforms(summary)
        for row in ranked.rows:
            ranks[(key, row.row.result_id)] = {
                "cohort": cohort_label(key),
                "rank": row.rank,
                "total": row.total_ranked,
                "score": _round(row.metric_value),
                "exclusion_reason": row.ranking_exclusion_reason,
            }
    return ranks


def _rule_entry(item: _Loaded, lane: str, outcome: corpus_rule.Outcome) -> tuple[CohortKey, ManifestEntry]:
    phase = LANE_PHASES.get(lane, item.phase)
    updates: dict[str, Any] = {"compliance_class": None}
    if lane in LANE_PHASES:
        updates["test_type"] = phase
        updates["power_score_source"] = outcome.score_source
    if lane in POWER_LANES:
        updates["power_score"] = derive_power_at_size(
            item.data.get("queries") or [], item.entry.scale_factor, POWER_QUERY_COUNTS.get(item.slug, 1)
        )
    if lane == corpus_rule.LANE_THROUGHPUT:
        updates["throughput_at_size"] = throughput_at_size_from_timings(item.data, item.entry.scale_factor)
    candidate = item.entry.model_copy(update=updates)
    metric = get_ranking_config(item.slug, phase).primary_metric
    reason = ranking_exclusion_reason(candidate, metric)
    bundle = explorer_transformer._parse_bundle(item.data)
    reason = explorer_transformer._throughput_stream_exclusion(bundle, item.data, phase, reason)
    reason = explorer_transformer._combined_exclusion(item.data, phase, reason)
    if explorer_transformer._sidecar_known_defects(item.path):
        reason = KNOWN_DEFECT_RANKING_EXCLUSION
    key = (item.slug, item.entry.scale_factor, phase, outcome_stream_count(item, lane))
    return key, candidate.model_copy(update={"ranking_exclusion_reason": reason})


def outcome_stream_count(item: _Loaded, lane: str) -> int | None:
    if lane == corpus_rule.LANE_THROUGHPUT:
        return corpus_rule.executed_stream_count(item.data)
    if lane in LANE_PHASES:
        return None
    return item.detail.stream_count


def _identity(item: _Loaded) -> Outcome:
    entry = item.entry
    return {
        "result_id": entry.result_id,
        "benchmark": item.slug,
        "scale_factor": entry.scale_factor,
        "platform_id": entry.platform_id,
        "variant_id": entry.variant_id,
        "platform_version": entry.platform_version,
        "execution_mode": entry.execution_mode,
        "tuning_mode": entry.tuning_mode,
    }


def _today(item: _Loaded, ranks: Mapping[tuple[CohortKey, str], Outcome]) -> Outcome:
    entry = item.entry
    rank = ranks[(item.today_key, entry.result_id)]
    return {
        "class": entry.compliance_class,
        "ranking_excluded": entry.compliance_class in RANKING_EXCLUDED_COMPLIANCE_CLASSES,
        "score_source": entry.power_score_source,
        **rank,
    }


def _rule(
    item: _Loaded,
    outcome: corpus_rule.Outcome,
    ranks: Mapping[tuple[CohortKey, str], Outcome],
    keys: Mapping[str, CohortKey],
) -> Outcome:
    return {
        "class": outcome.class_name,
        "public_admission": outcome.public_admission,
        "maintainer_admission": outcome.maintainer_admission,
        "lanes": list(outcome.lanes),
        "score_source": outcome.score_source,
        "labels": list(outcome.labels),
        "held_reason": outcome.held_reason,
        "missing_facts": list(outcome.missing_facts),
        "row": outcome.row,
        "ranks": {lane: ranks[(keys[lane], item.entry.result_id)] for lane in outcome.lanes},
    }


def compute_snapshot(bundles_dir: Path) -> Snapshot:
    seen: dict[str, str] = {}
    loaded: list[_Loaded] = []
    skipped: dict[str, str] = {}
    for path in discover_bundles(bundles_dir):
        item, problem = _load(bundles_dir, path, seen)
        if item is None:
            skipped[path.relative_to(bundles_dir).as_posix()] = problem or "unreadable"
        else:
            loaded.append(item)

    today_accum: dict[CohortKey, list[tuple[ManifestEntry, Any]]] = defaultdict(list)
    for item in loaded:
        today_accum[item.today_key].append((item.entry, item.detail))
    today_ranks = _rank_cohorts(today_accum)

    outcomes: dict[str, corpus_rule.Outcome] = {}
    lane_keys: dict[str, dict[str, CohortKey]] = {}
    rule_accum: dict[CohortKey, list[tuple[ManifestEntry, Any]]] = defaultdict(list)
    for item in loaded:
        facts = corpus_rule.facts_from_result(
            item.data,
            known_defects=corpus_rule.sidecar_known_defects(item.path),
            validation_status=item.entry.validation_status,
            failed_queries=item.entry.failed_query_count,
        )
        outcome = corpus_rule.derive_outcome(facts)
        outcomes[item.name] = outcome
        lane_keys[item.name] = {}
        for lane in outcome.lanes:
            key, entry = _rule_entry(item, lane, outcome)
            lane_keys[item.name][lane] = key
            rule_accum[key].append((entry, item.detail))
    rule_ranks = _rank_cohorts(rule_accum)

    snapshot: Snapshot = {}
    for item in loaded:
        snapshot[item.name] = {
            "identity": _identity(item),
            "today": _today(item, today_ranks),
            "rule": _rule(item, outcomes[item.name], rule_ranks, lane_keys[item.name]),
        }
    for name, problem in skipped.items():
        snapshot[name] = {"identity": None, "today": {"skipped": problem}, "rule": {"skipped": problem}}
    return dict(sorted(snapshot.items()))


def compare(recorded: Snapshot, computed: Snapshot) -> list[str]:
    problems: list[str] = []
    for section in ("identity", "today", "rule"):
        changed = sorted(
            name for name in recorded.keys() & computed.keys() if recorded[name][section] != computed[name][section]
        )
        if not changed:
            continue
        problems.append(
            f"{len(changed)} committed bundle(s) changed their {section} outcome. A code change moved stored "
            "results. If that is intended, regenerate the snapshot in the same pull request so the change is "
            f"reviewed: {REGENERATE_COMMAND}"
        )
        problems.extend(f"  {name}: {recorded[name][section]} -> {computed[name][section]}" for name in changed)
    missing = sorted(computed.keys() - recorded.keys())
    stale = sorted(recorded.keys() - computed.keys())
    if missing:
        problems.append(f"{len(missing)} bundle(s) are not in the snapshot: {missing}")
    if stale:
        problems.append(f"{len(stale)} snapshot entr(ies) have no bundle: {stale}")
    if missing or stale:
        problems.append(f"Bundles were added or removed. Regenerate the snapshot: {REGENERATE_COMMAND}")
    return problems


def load_snapshot(path: Path) -> Snapshot:
    return json.loads(path.read_text(encoding="utf-8"))["results"]


def render(snapshot: Snapshot) -> str:
    return json.dumps({"schema_version": SNAPSHOT_SCHEMA_VERSION, "results": snapshot}, indent=2) + "\n"


def _primary_rule_lane(entry: Outcome) -> str | None:
    today_phase = entry["today"].get("cohort", "").split("/")[2:3]
    lanes = entry["rule"].get("ranks") or {}
    for lane, rank in lanes.items():
        if rank["cohort"].split("/")[2:3] == today_phase:
            return lane
    return next(iter(lanes), None)


def differences(snapshot: Snapshot) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, entry in snapshot.items():
        today, rule = entry["today"], entry["rule"]
        if "skipped" in today:
            continue
        lane = _primary_rule_lane(entry)
        rule_rank = rule["ranks"][lane] if lane else None
        today_rank = today["rank"]
        new_rank = rule_rank["rank"] if rule_rank else None
        reasons: list[str] = []
        if rule["class"] == corpus_rule.CLASS_UNDETERMINED:
            reasons.append("undetermined: missing " + ", ".join(rule["missing_facts"]))
        if today_rank is None and new_rank is not None:
            reasons.append("becomes ranked")
        elif today_rank is not None and new_rank is None:
            reasons.append("loses rank")
        elif today_rank != new_rank:
            reasons.append("rank changes")
        if rule_rank and today["cohort"] != rule_rank["cohort"]:
            reasons.append("cohort changes")
        if not reasons:
            continue
        rows.append(
            {
                "path": name,
                "today_class": today["class"],
                "today_lane": today["cohort"],
                "today_rank": today_rank,
                "today_exclusion": today["exclusion_reason"],
                "rule_class": rule["class"],
                "rule_lane": rule_rank["cohort"] if rule_rank else None,
                "rule_rank": new_rank,
                "rule_exclusion": rule_rank["exclusion_reason"] if rule_rank else rule["held_reason"],
                "reason": "; ".join(reasons),
            }
        )
    return rows


def render_report(snapshot: Snapshot) -> str:
    rows = differences(snapshot)
    counts = {
        "total": len(snapshot),
        "differ": len(rows),
        "become_ranked": sum("becomes ranked" in row["reason"] for row in rows),
        "lose_rank": sum("loses rank" in row["reason"] for row in rows),
        "undetermined": sum(1 for entry in snapshot.values() if entry["rule"].get("class") == "undetermined"),
    }
    lines = [
        "# Corpus rule difference report",
        "",
        f"- stored results: {counts['total']}",
        f"- rule outcome differs from today's: {counts['differ']}",
        f"- become ranked: {counts['become_ranked']}",
        f"- lose rank: {counts['lose_rank']}",
        f"- undetermined: {counts['undetermined']}",
        "",
        "| path | today class | today lane | today rank | today exclusion | rule class | rule lane | rule rank | rule exclusion | reason |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                str(row[key]) if row[key] is not None else "-"
                for key in (
                    "path",
                    "today_class",
                    "today_lane",
                    "today_rank",
                    "today_exclusion",
                    "rule_class",
                    "rule_lane",
                    "rule_rank",
                    "rule_exclusion",
                    "reason",
                )
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Fail if any committed bundle's outcome differs.")
    mode.add_argument("--write", action="store_true", help="Write the regenerated snapshot to disk.")
    mode.add_argument("--report", metavar="PATH", help="Write the today-versus-rule difference report to PATH.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not BUNDLES_DIR.is_dir():
        print(f"Error: {BUNDLES_DIR} not found", file=sys.stderr)
        return 1
    computed = compute_snapshot(BUNDLES_DIR)

    if args.write:
        SNAPSHOT_PATH.write_text(render(computed), encoding="utf-8")
        print(f"Wrote {SNAPSHOT_PATH} with {len(computed)} bundles")
        return 0

    if args.report:
        Path(args.report).write_text(render_report(computed), encoding="utf-8")
        print(f"Wrote {args.report}")
        return 0

    if not SNAPSHOT_PATH.exists():
        print(f"FAIL: {SNAPSHOT_PATH} does not exist. Run: {REGENERATE_COMMAND}", file=sys.stderr)
        return 1
    problems = compare(load_snapshot(SNAPSHOT_PATH), computed)
    if problems:
        print("FAIL: " + "\n".join(problems), file=sys.stderr)
        return 1
    print(f"OK: outcomes match the snapshot ({len(computed)} bundles)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
