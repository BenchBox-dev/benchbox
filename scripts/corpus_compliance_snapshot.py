#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
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
    ManifestEntry,
    canonical_benchmark_slug,
    canonical_phase,
    ranking_exclusion_reason,
)
from _project.scripts.explorer_pipeline.ranking import rank_platforms
from benchbox.core.results import comparison_identity, corpus_rule, corpus_score
from benchbox.validation.bundle import discover_bundles

CLI_DESCRIPTION = (
    "Record or check the outcome of every committed result bundle as the Explorer pipeline emits it and as the "
    "corpus rule computes it, so a code change that moves a stored result fails visibly."
)

RULE_LANE_PHASES = {
    corpus_rule.LANE_COMBINED: "combined",
    corpus_rule.LANE_POWER_ONLY: "power",
    corpus_rule.LANE_SUBSCALE: "power",
    corpus_rule.LANE_OWN: "power",
    corpus_rule.LANE_THROUGHPUT: "throughput",
}

UNLANED = "unlaned"

Outcome = dict[str, Any]
Snapshot = dict[str, Outcome]
CohortKey = tuple[str, float, str, int | None]


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), SCORE_DECIMALS)


def cohort_label(key: CohortKey) -> str:
    benchmark, scale_factor, phase, streams = key
    label = f"{benchmark}/sf{scale_factor:g}/{phase}"
    return label if streams is None else f"{label}/streams={streams}"


class _Loaded:
    def __init__(self, path: Path, name: str, data: dict[str, Any], entry: ManifestEntry, detail: Any) -> None:
        self.path = path
        self.name = name
        self.data = data
        self.entry = entry
        self.detail = detail
        self.slug = canonical_benchmark_slug(entry.benchmark)
        self.phase = canonical_phase(detail.test_type)


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


def _rule_lane_score(item: _Loaded, lane: str) -> float | None:
    if lane == corpus_rule.LANE_THROUGHPUT:
        return corpus_score.throughput_at_size(item.data)
    if lane == corpus_rule.LANE_COMBINED:
        return corpus_score.combined_score(item.data)
    if lane == corpus_rule.LANE_OWN:
        return item.entry.display_geomean_ms
    return corpus_score.power_at_size(item.data)


def _rule_lane_key(item: _Loaded, lane: str) -> CohortKey:
    phase = RULE_LANE_PHASES[lane]
    streams = corpus_rule.throughput_stream_count(item.data) if lane == corpus_rule.LANE_THROUGHPUT else None
    return (item.slug, item.entry.scale_factor, phase, streams)


def _rule_lane_reason(item: _Loaded, score: float | None) -> str | None:
    reason = ranking_exclusion_reason(item.entry, "display_geomean_ms")
    if reason not in (None, "missing_primary_metric"):
        return reason
    if score is None:
        return "missing_primary_metric"
    if not math.isfinite(score) or score <= 0:
        return "non_positive_primary_metric"
    return None


def _competition_rank(score: float, scored: list[float], lower_is_better: bool) -> int:
    return 1 + sum((other < score) if lower_is_better else (other > score) for other in scored)


def _majority_query_set(query_sets: list[frozenset[str]]) -> frozenset[str] | None:
    counts = Counter(query_sets)
    if not counts:
        return None
    query_set, count = counts.most_common(1)[0]
    return query_set if count > len(query_sets) - count else None


def _rule_ranks(
    loaded: list[_Loaded], outcomes: Mapping[str, corpus_rule.Outcome]
) -> tuple[dict[tuple[CohortKey, str], Outcome], dict[str, dict[str, CohortKey]]]:
    cohorts: dict[CohortKey, list[tuple[str, float | None, str | None, frozenset[str]]]] = defaultdict(list)
    lower_is_better: dict[CohortKey, bool] = {}
    keys: dict[str, dict[str, CohortKey]] = {}
    for item in loaded:
        keys[item.name] = {}
        for lane in outcomes[item.name].lanes:
            key = _rule_lane_key(item, lane)
            keys[item.name][lane] = key
            score = _rule_lane_score(item, lane)
            cohorts[key].append(
                (
                    item.entry.result_id,
                    score,
                    _rule_lane_reason(item, score),
                    frozenset(timing.query_id for timing in item.detail.display_timings),
                )
            )
            lower_is_better[key] = lane == corpus_rule.LANE_OWN
    ranks: dict[tuple[CohortKey, str], Outcome] = {}
    for key, members in cohorts.items():
        shared = _majority_query_set([query_set for _, _, reason, query_set in members if reason is None])
        members = [
            (result_id, score, reason or (None if query_set == shared else "mismatched_query_set"), query_set)
            for result_id, score, reason, query_set in members
        ]
        scored = [score for _, score, reason, _ in members if reason is None and score is not None]
        for result_id, score, reason, _ in members:
            ranked = reason is None and score is not None
            ranks[(key, result_id)] = {
                "cohort": cohort_label(key),
                "rank": _competition_rank(score, scored, lower_is_better[key])
                if ranked and score is not None
                else None,
                "total": len(scored),
                "score": _round(score),
                "exclusion_reason": reason,
            }
    return ranks, keys


def _identity(item: _Loaded) -> Outcome:
    entry = item.entry
    version = comparison_identity.resolve_comparison_version(item.data)
    return {
        "result_id": entry.result_id,
        "benchmark": item.slug,
        "scale_factor": entry.scale_factor,
        "platform_id": entry.platform_id,
        "variant_id": entry.variant_id,
        "platform_version": entry.platform_version,
        "comparison_version": version.text,
        "version_is_release": version.is_release,
        "execution_mode": entry.execution_mode,
        "tuning_mode": entry.tuning_mode,
    }


def _today(
    item: _Loaded, ranks: Mapping[tuple[CohortKey, str], Outcome], keys: Mapping[str | None, CohortKey]
) -> Outcome:
    entry = item.entry
    return {
        "class": entry.corpus_class,
        "score_source": entry.power_score_source,
        "ranks": {lane or UNLANED: ranks[(key, entry.result_id)] for lane, key in keys.items()},
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

    transformer = explorer_transformer.BundleTransformer()
    keys: dict[str, dict[str | None, CohortKey]] = {}
    accum: dict[CohortKey, list[tuple[ManifestEntry, Any]]] = defaultdict(list)
    for item in loaded:
        keys[item.name] = {}
        for lane_entry in transformer.lane_entries(item.entry, item.data):
            key = explorer_pipeline.summary_key(lane_entry, item.detail)
            keys[item.name][lane_entry.ranking_lane] = key
            accum[key].append((lane_entry, item.detail))
    ranks = _rank_cohorts(accum)

    outcomes = {
        item.name: corpus_rule.derive_outcome(
            corpus_rule.facts_from_result(
                item.data,
                known_defects=corpus_rule.sidecar_known_defects(item.path),
                validation_status=item.entry.validation_status,
                failed_queries=item.entry.failed_query_count,
            )
        )
        for item in loaded
    }
    rule_ranks, rule_keys = _rule_ranks(loaded, outcomes)

    snapshot: Snapshot = {}
    for item in loaded:
        snapshot[item.name] = {
            "identity": _identity(item),
            "today": _today(item, ranks, keys[item.name]),
            "rule": _rule(item, outcomes[item.name], rule_ranks, rule_keys[item.name]),
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
    entries = ",\n".join(
        f"    {json.dumps(name)}: {{\n"
        + ",\n".join(f"      {json.dumps(section)}: {json.dumps(value)}" for section, value in entry.items())
        + "\n    }"
        for name, entry in snapshot.items()
    )
    results = "{\n" + entries + "\n  }" if entries else "{}"
    return f'{{\n  "schema_version": {SNAPSHOT_SCHEMA_VERSION},\n  "results": {results}\n}}\n'


def _lane_reasons(today: Outcome | None, rule: Outcome | None) -> list[str]:
    if today is None:
        return ["pipeline lacks the lane"]
    if rule is None:
        return ["loses rank"] if today["rank"] is not None else []
    reasons: list[str] = []
    if today["rank"] is None and rule["rank"] is not None:
        reasons.append("becomes ranked")
    elif today["rank"] is not None and rule["rank"] is None:
        reasons.append("loses rank")
    elif today["rank"] != rule["rank"]:
        reasons.append("rank changes")
    if today["cohort"] != rule["cohort"]:
        reasons.append("cohort changes")
    if today["total"] != rule["total"]:
        reasons.append("total changes")
    if today["score"] != rule["score"]:
        reasons.append("score changes")
    return reasons


def differences(snapshot: Snapshot) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, entry in snapshot.items():
        today, rule = entry["today"], entry["rule"]
        if "skipped" in today:
            continue
        today_ranks, rule_ranks = today["ranks"], rule["ranks"]
        reasons: list[str] = []
        if rule["class"] == corpus_rule.CLASS_UNDETERMINED:
            reasons.append("undetermined: missing " + ", ".join(rule["missing_facts"]))
        first: tuple[Outcome | None, Outcome | None] | None = None
        lanes = dict.fromkeys([*rule_ranks, *today_ranks])
        for lane in lanes:
            lane_today = today_ranks.get(lane)
            lane_rule = rule_ranks.get(lane)
            if lane_today is None and lane_rule is None:
                continue
            if lane == UNLANED and not rule_ranks:
                lane_rule = None
            elif lane == UNLANED:
                continue
            lane_reasons = _lane_reasons(lane_today, lane_rule)
            if lane_reasons and first is None:
                first = (lane_today, lane_rule)
            reasons.extend(f"{lane}: {reason}" for reason in lane_reasons)
        if not reasons:
            continue
        shown_today, shown_rule = first or (
            next(iter(today_ranks.values()), None),
            next(iter(rule_ranks.values()), None),
        )
        rows.append(
            {
                "path": name,
                "today_class": today["class"],
                "today_lane": shown_today["cohort"] if shown_today else None,
                "today_rank": shown_today["rank"] if shown_today else None,
                "today_exclusion": shown_today["exclusion_reason"] if shown_today else None,
                "rule_class": rule["class"],
                "rule_lane": shown_rule["cohort"] if shown_rule else None,
                "rule_rank": shown_rule["rank"] if shown_rule else None,
                "rule_exclusion": shown_rule["exclusion_reason"] if shown_rule else rule["held_reason"],
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
