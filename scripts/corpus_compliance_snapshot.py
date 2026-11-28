#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

BUNDLES_DIR = Path("results-data/bundles")
SNAPSHOT_PATH = Path("results-data/compliance-classes.json")
SNAPSHOT_SCHEMA_VERSION = 3
REGENERATE_COMMAND = "uv run -- python scripts/corpus_compliance_snapshot.py --write"
SCORE_DECIMALS = 2

CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
if str(CHECKOUT_ROOT) not in sys.path:
    sys.path.insert(0, str(CHECKOUT_ROOT))

from _project.scripts.explorer_pipeline import pipeline as explorer_pipeline, transformer as explorer_transformer
from _project.scripts.explorer_pipeline.medians import build_identity_medians
from _project.scripts.explorer_pipeline.models import (
    ManifestEntry,
    canonical_benchmark_slug,
    canonical_phase,
    ranking_exclusion_reason,
)
from _project.scripts.explorer_pipeline.ranking import MINIMUM_RANKED_COHORT, rank_platforms
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

MEDIAN_KEY_FIELDS = ("cohort_key", "lane", "platform_id", "variant_id", "comparison_version")

Outcome = dict[str, Any]
Snapshot = dict[str, Outcome]
IdentityMedians = dict[str, list[dict[str, Any]]]
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


def _rank_cohorts(summaries: list[tuple[CohortKey, Any]]) -> dict[tuple[CohortKey, str], Outcome]:
    ranks: dict[tuple[CohortKey, str], Outcome] = {}
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
    return _compute(bundles_dir)[0]


def compute_identity_medians(bundles_dir: Path) -> IdentityMedians:
    return _compute(bundles_dir)[1]


def _compute(bundles_dir: Path) -> tuple[Snapshot, IdentityMedians]:
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
    summaries = explorer_pipeline._build_benchmark_summaries(accum, {})
    ranks = _rank_cohorts(summaries)

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
    medians = {
        "today": _today_medians(summaries, loaded),
        "rule": _rule_medians(loaded, rule_ranks, rule_keys),
    }
    return dict(sorted(snapshot.items())), medians


def _median_row(**fields: Any) -> dict[str, Any]:
    fields["median_score"] = _round(fields["median_score"])
    return fields


def _sorted_medians(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: tuple(row[field] for field in MEDIAN_KEY_FIELDS))


def _today_medians(summaries: list[tuple[CohortKey, Any]], loaded: list[_Loaded]) -> list[dict[str, Any]]:
    data_by_result = {item.entry.result_id: item.data for item in loaded}
    details_by_result = {item.entry.result_id: item.detail for item in loaded}
    return _sorted_medians(
        [
            _median_row(
                cohort_key=median.cohort_key,
                lane=median.lane,
                platform_id=median.platform_id,
                variant_id=median.variant_id,
                comparison_version=median.comparison_version,
                version_is_release=median.version_is_release,
                run_count=median.run_count,
                median_score=median.median_score,
                is_leaderboard_version=median.is_leaderboard_version,
                rank=median.rank,
                total_in_cohort=median.total_in_cohort,
                ranking_exclusion_reason=median.ranking_exclusion_reason,
            )
            for median in build_identity_medians(summaries, data_by_result, details_by_result)
        ]
    )


TIMING_FIELDS = ("ms", "execution_time_ms", "duration_ms")


def _final_iteration_queries(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    legacy = corpus_rule.legacy_query_timing_defaults(data)
    default_iteration = 1 if legacy else 0

    def iteration(query: Mapping[str, Any]) -> int:
        value = query.get("iter", query.get("iteration", default_iteration))
        try:
            return 0 if isinstance(value, bool) else int(value)
        except (TypeError, ValueError):
            return 0

    by_type: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for query in data.get("queries") or []:
        if isinstance(query, Mapping) and query.get("run_type", "measurement" if legacy else None) == "measurement":
            if iteration(query) > 0:
                by_type[str(query.get("test_type") or "power").lower()].append(query)
    return [
        query
        for queries in by_type.values()
        for query in queries
        if iteration(query) == max(iteration(other) for other in queries)
    ]


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _median_nodes(nodes: list[Any]) -> Any:
    first = nodes[0]
    if isinstance(first, Mapping):
        return {
            key: statistics.median(peers)
            if key in TIMING_FIELDS and all(_is_number(peer) for peer in peers)
            else _median_nodes(peers)
            for key, value in first.items()
            for peers in [[node[key] for node in nodes if isinstance(node, Mapping) and key in node]]
        }
    if isinstance(first, list) and all(isinstance(node, list) and len(node) == len(first) for node in nodes):
        return [_median_nodes([node[index] for node in nodes]) for index in range(len(first))]
    return first


def _median_data(runs: list[Mapping[str, Any]]) -> dict[str, Any]:
    merged = _median_nodes([{key: value for key, value in run.items() if key != "queries"} for run in runs])
    peers: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for run in runs:
        for query in _final_iteration_queries(run):
            test_type = str(query.get("test_type") or "power").lower()
            peers[(test_type, str(query.get("stream")), str(query.get("id", query.get("query_id"))))].append(query)
    queries = []
    for members in peers.values():
        query = dict(members[0])
        for field in TIMING_FIELDS:
            values = [member[field] for member in members if _is_number(member.get(field))]
            if values:
                query[field] = statistics.median(values)
        queries.append(query)
    merged["queries"] = queries
    return merged


def _own_lane_median(items: list[_Loaded]) -> float | None:
    by_query: dict[str, list[float]] = defaultdict(list)
    for item in items:
        for timing in item.detail.display_timings:
            if timing.display_ms is not None and timing.display_ms > 0:
                by_query[timing.query_id].append(timing.display_ms)
    if not by_query:
        return None
    return math.exp(statistics.fmean(math.log(statistics.median(values)) for values in by_query.values()))


def _rule_median_score(lane: str, items: list[_Loaded]) -> float | None:
    if lane == corpus_rule.LANE_OWN:
        return _own_lane_median(items)
    merged = _median_data([item.data for item in items])
    return _rule_lane_score(_Loaded(items[0].path, items[0].name, merged, items[0].entry, items[0].detail), lane)


def _standing_versions(candidates: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    by_identity: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        if row["median_score"] is not None:
            by_identity[(row["platform_id"], row["variant_id"])].append(row)
    standing: dict[tuple[str, str], dict[str, Any]] = {}
    for identity, rows in by_identity.items():
        released = [row for row in rows if row["version_is_release"]]
        unrecorded = [row for row in rows if row["comparison_version"] == comparison_identity.NOT_RECORDED]
        if released:
            standing[identity] = max(released, key=lambda row: _version_sort_key(row["comparison_version"]))
        elif unrecorded:
            standing[identity] = unrecorded[0]
    return standing


def _version_sort_key(version: str) -> tuple[tuple[int, int, str], ...]:
    return tuple((0, int(part), "") if part.isdigit() else (1, 0, part) for part in re.split(r"(\d+)", version) if part)


def _rule_medians(
    loaded: list[_Loaded],
    rule_ranks: Mapping[tuple[CohortKey, str], Outcome],
    rule_keys: Mapping[str, Mapping[str, CohortKey]],
) -> list[dict[str, Any]]:
    groups: dict[tuple[CohortKey, str, str, str, str, bool], list[tuple[_Loaded, bool]]] = defaultdict(list)
    for item in loaded:
        version = comparison_identity.resolve_comparison_version(item.data)
        for lane, key in rule_keys[item.name].items():
            ranked = rule_ranks[(key, item.entry.result_id)]["rank"] is not None
            group = (key, lane, item.entry.platform_id, item.entry.variant_id, version.text, version.is_release)
            groups[group].append((item, ranked))
    rows: list[dict[str, Any]] = []
    for (key, lane, platform_id, variant_id, version, is_release), members in groups.items():
        eligible = [item for item, ranked in members if ranked]
        rows.append(
            {
                "cohort_key": cohort_label(key),
                "lane": lane,
                "platform_id": platform_id,
                "variant_id": variant_id,
                "comparison_version": version,
                "version_is_release": is_release,
                "run_count": len(eligible),
                "median_score": _rule_median_score(lane, eligible) if eligible else None,
            }
        )
    lower_is_better = {lane: lane == corpus_rule.LANE_OWN for lane in {row["lane"] for row in rows}}
    cohorts: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        cohorts[(row["cohort_key"], row["lane"])].append(row)
    result: list[dict[str, Any]] = []
    for (_, lane), cohort in cohorts.items():
        standing = _standing_versions(cohort)
        standing_ids = {id(row) for row in standing.values()}
        scores = [row["median_score"] for row in standing.values()]
        ranked = len(scores) >= MINIMUM_RANKED_COHORT
        for row in cohort:
            stands = id(row) in standing_ids
            identity = (row["platform_id"], row["variant_id"])
            superseded = (
                not stands
                and row["median_score"] is not None
                and row["comparison_version"] == comparison_identity.NOT_RECORDED
                and identity in standing
                and standing[identity]["version_is_release"]
            )
            if stands and ranked:
                rank, reason = _competition_rank(row["median_score"], scores, lower_is_better[lane]), None
            elif row["median_score"] is None:
                rank, reason = None, "no_median_score"
            elif not stands:
                rank = None
                reason = "unrecorded_version_superseded_by_release" if superseded else "not_leaderboard_version"
            else:
                rank, reason = None, "below_cohort_minimum"
            result.append(
                _median_row(
                    **row,
                    is_leaderboard_version=stands,
                    rank=rank,
                    total_in_cohort=len(scores),
                    ranking_exclusion_reason=reason,
                )
            )
    return _sorted_medians(result)


def compare_identity_medians(recorded: IdentityMedians, computed: IdentityMedians) -> list[str]:
    problems: list[str] = []
    for side in ("today", "rule"):
        before = {tuple(row[field] for field in MEDIAN_KEY_FIELDS): row for row in recorded[side]}
        after = {tuple(row[field] for field in MEDIAN_KEY_FIELDS): row for row in computed[side]}
        changed = sorted(key for key in before.keys() & after.keys() if before[key] != after[key])
        added = sorted(after.keys() - before.keys())
        removed = sorted(before.keys() - after.keys())
        if not (changed or added or removed):
            continue
        problems.append(
            f"{len(changed) + len(added) + len(removed)} identity median row(s) changed their {side} outcome. A code "
            "change moved the published leaderboard. If that is intended, regenerate the snapshot in the same pull "
            f"request so the change is reviewed: {REGENERATE_COMMAND}"
        )
        problems.extend(f"  {'/'.join(map(str, key))}: {before[key]} -> {after[key]}" for key in changed)
        problems.extend(f"  {'/'.join(map(str, key))}: added {after[key]}" for key in added)
        problems.extend(f"  {'/'.join(map(str, key))}: removed {before[key]}" for key in removed)
    return problems


def identity_median_differences(medians: IdentityMedians) -> list[str]:
    today = {tuple(row[field] for field in MEDIAN_KEY_FIELDS): row for row in medians["today"]}
    rule = {tuple(row[field] for field in MEDIAN_KEY_FIELDS): row for row in medians["rule"]}
    return ["/".join(map(str, key)) for key in sorted(today.keys() | rule.keys()) if today.get(key) != rule.get(key)]


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


def load_identity_medians(path: Path) -> IdentityMedians:
    return json.loads(path.read_text(encoding="utf-8"))["identity_medians"]


def _render_medians(medians: IdentityMedians) -> str:
    sides = ",\n".join(
        f"    {json.dumps(side)}: [\n" + ",\n".join(f"      {json.dumps(row)}" for row in rows) + "\n    ]"
        for side, rows in medians.items()
    )
    return "{\n" + sides + "\n  }"


def render(snapshot: Snapshot, medians: IdentityMedians) -> str:
    entries = ",\n".join(
        f"    {json.dumps(name)}: {{\n"
        + ",\n".join(f"      {json.dumps(section)}: {json.dumps(value)}" for section, value in entry.items())
        + "\n    }"
        for name, entry in snapshot.items()
    )
    results = "{\n" + entries + "\n  }" if entries else "{}"
    return (
        f'{{\n  "schema_version": {SNAPSHOT_SCHEMA_VERSION},\n  "results": {results},\n'
        f'  "identity_medians": {_render_medians(medians)}\n}}\n'
    )


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
    computed, computed_medians = _compute(BUNDLES_DIR)

    if args.write:
        SNAPSHOT_PATH.write_text(render(computed, computed_medians), encoding="utf-8")
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
    problems.extend(compare_identity_medians(load_identity_medians(SNAPSHOT_PATH), computed_medians))
    if problems:
        print("FAIL: " + "\n".join(problems), file=sys.stderr)
        return 1
    print(f"OK: outcomes match the snapshot ({len(computed)} bundles)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
