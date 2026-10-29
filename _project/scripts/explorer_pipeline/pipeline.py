from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _project.scripts.explorer_pipeline.contract import EXPLORER_READ_MODEL_VERSION
from _project.scripts.explorer_pipeline.duckdb_builder import DuckDBSnapshotBuilder
from _project.scripts.explorer_pipeline.models import (
    BenchmarkSummary,
    DetailResult,
    ManifestEntry,
    PlatformRow,
    canonical_benchmark_slug,
    canonical_phase,
    get_ranking_config,
    is_ranking_eligible,
    primary_metric_value,
    ranking_exclusion_reason,
)
from _project.scripts.explorer_pipeline.ranking import RankedCohort, rank_platforms
from _project.scripts.explorer_pipeline.transformer import (
    BundleTransformer,
    CompanionPrivacyError,
    _applied_receipt,
    _override_display,
    _parse_bundle,
    _platform_percentile_stats,
    _public_companion_bytes,
    _sanitize_applied_receipt,
)
from _project.scripts.results_explorer_snapshot_invariants import check_snapshot
from benchbox.core.results.anonymization import AnonymizationManager, find_public_path_leaks
from benchbox.core.results.canonical_json import canonical_json_bytes
from benchbox.core.results.provenance import SOURCE_TO_TRUST_LABEL
from benchbox.validation.bundle import COMPANION_SUFFIXES, discover_bundles

logger = logging.getLogger(__name__)

PUBLISHED_COMPANION_SUFFIXES = (".plans.json",)


class DuplicateResultIdError(Exception):
    pass


class PrivacyRejectionError(Exception):
    pass


SUBMISSION_MANIFEST_FILENAME = "submission-manifest.json"
SUBMISSION_MANIFEST_SUFFIX = ".manifest.json"
COMMUNITY_TRUST_LABEL = "community-submission"
VENDOR_TRUST_LABEL = "vendor-supplied"
VENDOR_VISIBILITY = "public-vendor-reported"
VENDOR_SUBTREE_COMPONENT = "vendor"


def _remove_published_wal(output_dir: Path) -> None:
    wal_path = output_dir / "results.duckdb.wal"
    try:
        wal_path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise OSError(f"could not remove stale published WAL {wal_path}: {exc}") from exc


def _promote_staged_output(staged_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    target_bundles = output_dir / "bundles"
    if target_bundles.is_symlink():
        raise ValueError(f"published bundles path must be a real directory, not a symlink: {target_bundles}")
    target_bundles.mkdir(parents=True, exist_ok=True)
    staged_bundles = staged_dir / "bundles"
    candidate_names = {path.name for path in staged_bundles.iterdir() if path.is_file()}

    for source in sorted(staged_bundles.iterdir()):
        if not source.is_file():
            continue
        target = target_bundles / source.name
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    candidate_db = staged_dir / "results.duckdb"
    os.replace(candidate_db, output_dir / "results.duckdb")

    _remove_published_wal(output_dir)
    for child in list(target_bundles.iterdir()):
        if child.is_file() and child.name.endswith(".json") and child.name not in candidate_names:
            try:
                child.unlink()
            except OSError:
                logger.warning("Could not remove stale published bundle %s", child)
    for legacy_name in ("benchmarks", "details", "compare"):
        shutil.rmtree(output_dir / legacy_name, ignore_errors=True)
    for legacy_file in ("manifest.json", "meta_leaderboard.json", "short_ids.json", "results_schema.json"):
        try:
            (output_dir / legacy_file).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("Could not remove legacy artifact %s", output_dir / legacy_file)


_DIGEST_CHUNK_BYTES = 1024 * 1024


def _publication_digest(bundle_path: Path, public_raw: bytes) -> str:
    hasher = hashlib.sha256()
    hasher.update(public_raw)
    for suffix in COMPANION_SUFFIXES:
        companion = bundle_path.with_name(f"{bundle_path.stem}{suffix}")
        hasher.update(suffix.encode())
        try:
            with companion.open("rb") as handle:
                for chunk in iter(lambda handle=handle: handle.read(_DIGEST_CHUNK_BYTES), b""):
                    hasher.update(chunk)
        except OSError:
            hasher.update(b"-")
    return hasher.hexdigest()


def _is_vendor_subtree(bundle_path: Path, bundles_dir: Path) -> bool:
    try:
        rel = bundle_path.relative_to(bundles_dir)
    except ValueError:
        return False
    return len(rel.parts) >= 2 and rel.parts[0] == VENDOR_SUBTREE_COMPONENT


def _find_submission_manifest(bundle_path: Path) -> Path | None:
    per_bundle = bundle_path.parent / f"{bundle_path.stem}{SUBMISSION_MANIFEST_SUFFIX}"
    if per_bundle.is_file():
        return per_bundle
    legacy = bundle_path.parent / SUBMISSION_MANIFEST_FILENAME
    if legacy.is_file():
        return legacy
    return None


def _manifest_trust_label(bundle_path: Path, default: str) -> str:
    manifest_path = _find_submission_manifest(bundle_path)
    if manifest_path is None:
        return default
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return COMMUNITY_TRUST_LABEL
    if not isinstance(manifest, dict):
        return COMMUNITY_TRUST_LABEL
    source = manifest.get("result_source")
    if isinstance(source, str) and source in SOURCE_TO_TRUST_LABEL:
        return SOURCE_TO_TRUST_LABEL[source]
    return COMMUNITY_TRUST_LABEL


def _public_applied_receipt(
    bundle_path: Path,
    bundle_data: dict[str, Any] | None,
    anonymizer: AnonymizationManager,
) -> str | None:
    receipt_json = _applied_receipt(bundle_path, _parse_bundle(bundle_data) if bundle_data is not None else None)
    if receipt_json is None:
        return None
    try:
        public_receipt = _sanitize_applied_receipt(anonymizer.anonymize_result_payload(json.loads(receipt_json)))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not sanitize applied receipt {bundle_path.name}: {exc}") from exc
    leaks = find_public_path_leaks(public_receipt)
    if leaks:
        raise PrivacyRejectionError(
            f"{bundle_path}: public applied receipt privacy check failed for fields: " + ", ".join(sorted(set(leaks)))
        )
    return canonical_json_bytes(public_receipt).decode("utf-8")


def _public_override_display(
    bundle_path: Path,
) -> dict[str, Any]:
    display = _override_display(bundle_path)
    leaks = find_public_path_leaks(display)
    if leaks:
        raise PrivacyRejectionError(
            f"{bundle_path}: public override display privacy check failed for fields: " + ", ".join(sorted(set(leaks)))
        )
    return display


def _public_bundle_data(
    bundle_path: Path,
    bundle_data: dict[str, Any],
    anonymizer: AnonymizationManager,
) -> tuple[dict[str, Any], str | None]:
    public_bundle = anonymizer.anonymize_result_payload(bundle_data)
    public_platform = public_bundle.get("platform")
    if isinstance(public_platform, dict):
        public_tuning = public_platform.get("tuning")
        if isinstance(public_tuning, dict):
            requested = public_tuning.get("requested")
            if requested is None:
                legacy_bytes = _public_companion_bytes(bundle_path, ".tuning.json", anonymizer)
                if legacy_bytes is not None:
                    legacy = json.loads(legacy_bytes)
                    if isinstance(legacy, dict) and isinstance(legacy.get("requested"), dict):
                        requested = legacy["requested"]
            if requested is not None:
                sanitized_tuning = anonymizer.anonymize_tuning_payload({"requested": requested})
                public_tuning["requested"] = sanitized_tuning.get("requested", {})
        else:
            legacy_bytes = _public_companion_bytes(bundle_path, ".tuning.json", anonymizer)
            if legacy_bytes is not None:
                legacy = json.loads(legacy_bytes)
                if isinstance(legacy, dict) and isinstance(legacy.get("requested"), dict):
                    public_platform["tuning"] = anonymizer.anonymize_tuning_payload({"requested": legacy["requested"]})
    public_leaks = find_public_path_leaks(public_bundle)
    if public_leaks:
        raise PrivacyRejectionError(
            f"{bundle_path}: public bundle privacy check failed for fields: " + ", ".join(sorted(set(public_leaks)))
        )
    return public_bundle, _public_applied_receipt(bundle_path, bundle_data, anonymizer)


_SummaryKey = tuple[str, float, str, int | None]
_SummaryAccum = dict[_SummaryKey, list[tuple[ManifestEntry, DetailResult]]]


def _sf_str(scale_factor: float) -> str:
    return f"{scale_factor:g}"


def _natural_sort_key(s: str) -> list[int | str]:
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", s) if c]


def _build_short_ids(result_ids: list[str]) -> dict[str, str]:
    if not result_ids:
        return {}
    for length in range(8, 65, 2):
        candidate: dict[str, str] = {hashlib.sha256(rid.encode()).hexdigest()[:length]: rid for rid in result_ids}
        if len(candidate) == len(result_ids):
            return candidate
    raise RuntimeError(f"Could not build collision-free short IDs from {len(result_ids)} result_id(s) (duplicates?)")


def _platform_row_sort_key(benchmark: str, phase: str) -> Callable[[PlatformRow], tuple[bool, float]]:
    cfg = get_ranking_config(benchmark, phase)
    desc = cfg.primary_order == "desc"

    def _key(row: PlatformRow) -> tuple[bool, float]:
        ineligible = not row.is_ranking_eligible
        primary_val = primary_metric_value(row, cfg.primary_metric)
        if primary_val is None:
            return (ineligible, float("inf"))
        metric = -primary_val if desc else primary_val
        return (ineligible, metric)

    return _key


def _build_benchmark_summaries(
    accum: _SummaryAccum,
    full_to_short: dict[str, str],
) -> list[tuple[_SummaryKey, BenchmarkSummary]]:
    summaries = []
    for key, pairs in sorted(accum.items(), key=lambda item: (*item[0][:3], item[0][3] or 0)):
        benchmark, scale_factor, phase, stream_count = key

        all_query_ids = sorted(
            {dt.query_id for _, detail in pairs for dt in detail.display_timings},
            key=_natural_sort_key,
        )
        query_set_counts: dict[frozenset[str], int] = {}
        for entry, detail in pairs:
            if entry.ranking_exclusion_reason is None and ranking_exclusion_reason(entry) is None:
                query_set = frozenset(dt.query_id for dt in detail.display_timings)
                query_set_counts[query_set] = query_set_counts.get(query_set, 0) + 1
        canonical_query_set: frozenset[str] | None = None
        if query_set_counts:
            candidate, candidate_count = max(query_set_counts.items(), key=lambda item: item[1])
            eligible_count = sum(query_set_counts.values())
            if candidate_count > eligible_count - candidate_count:
                canonical_query_set = candidate

        platform_rows: list[PlatformRow] = []
        for entry, detail in pairs:
            timings: dict[str, float | None] = dict.fromkeys(all_query_ids, None)
            for dt in detail.display_timings:
                timings[dt.query_id] = dt.display_ms

            row_ranking_reason = entry.ranking_exclusion_reason
            row_query_set = frozenset(dt.query_id for dt in detail.display_timings)
            if row_ranking_reason is None and (canonical_query_set is None or row_query_set != canonical_query_set):
                row_ranking_reason = "mismatched_query_set"
            row = PlatformRow(
                result_id=entry.result_id,
                short_id=full_to_short.get(entry.result_id, ""),
                platform_id=entry.platform_id,
                variant_id=entry.variant_id,
                variant_label=entry.variant_label,
                platform=entry.platform,
                platform_version=entry.platform_version,
                tuning_mode=entry.tuning_mode,
                tuning_hash=entry.tuning_hash,
                execution_mode=entry.execution_mode,
                trust_label=entry.trust_label,
                run_date=entry.run_date,
                is_ranking_eligible=is_ranking_eligible(entry) and row_ranking_reason is None,
                ranking_exclusion_reason=row_ranking_reason,
                power_score=entry.power_score,
                throughput_at_size=entry.throughput_at_size,
                display_geomean_ms=entry.display_geomean_ms,
                sample_geomean_ms=entry.geomean_ms,
                cost_usd=entry.cost_usd,
                compliance_class=entry.compliance_class,
                power_score_source=entry.power_score_source,
                percentile_stats=_platform_percentile_stats(detail.display_timings),
                phase_durations=detail.phase_durations,
                timings=timings,
            )
            platform_rows.append(row)

        platform_rows.sort(key=_platform_row_sort_key(benchmark, phase))

        summary = BenchmarkSummary(
            benchmark=benchmark,
            scale_factor=scale_factor,
            phase=phase,
            stream_count=stream_count,
            query_ids=all_query_ids,
            platforms=platform_rows,
            ranking=get_ranking_config(benchmark, phase),
        )
        summaries.append((key, summary))
    return summaries


_BENCHMARK_LABELS: dict[str, str] = {
    "ai_primitives": "AI Primitives",
    "amplab": "AMPLab",
    "clickbench": "ClickBench",
    "coffeeshop": "CoffeeShop",
    "datavault": "TPC-H Data Vault",
    "flightdata": "Flight Data",
    "h2odb": "H2ODB",
    "joinorder": "JoinOrder",
    "metadata_primitives": "Metadata",
    "nyctaxi": "NYC Taxi",
    "read_primitives": "Read Primitives",
    "star_schema": "SSB",
    "ssb": "SSB",
    "tsbs-devops": "TSBS DevOps",
    "tsbs_devops": "TSBS DevOps",
    "tpcdi": "TPC-DI",
    "tpcds": "TPC-DS",
    "tpcds_obt": "TPC-DS-OBT",
    "tpch": "TPC-H",
    "tpch_skew": "TPC-H Skew",
    "tpchavoc": "TPC-Havoc",
    "transaction_primitives": "Transactions",
    "vector_search": "Vector Search",
    "write_primitives": "Write Primitives",
}


def _humanize_benchmark(benchmark: str) -> str:
    return _BENCHMARK_LABELS.get(benchmark, benchmark.upper())


def _cohort_label(benchmark: str, sf: str, phase: str, stream_count: int | None) -> str:
    label = f"{_humanize_benchmark(benchmark)} SF{sf}"
    if phase == "power":
        return label
    label = f"{label} {phase.capitalize()}"
    if stream_count is None:
        return label
    noun = "stream" if stream_count == 1 else "streams"
    return f"{label} ({stream_count} {noun})"


def _cohort_href(benchmark: str, scale_factor: float, phase: str, stream_count: int | None) -> str:
    href = f"/results/{benchmark}/?sf={scale_factor}&phase={phase}"
    return href if stream_count is None else f"{href}&streams={stream_count}"


def _rank_platforms_in_cohort(
    summary: BenchmarkSummary,
    cohort_key: str,
    full_to_short: dict[str, str],
    ranked: RankedCohort | None = None,
) -> tuple[list[dict[str, Any]], str, bool]:
    if ranked is None:
        ranked = rank_platforms(summary)

    entries: list[dict[str, Any]] = []
    for ranked_row in ranked.rows:
        row = ranked_row.row
        entries.append(
            {
                "platform_id": row.platform_id,
                "variant_id": row.variant_id,
                "variant_label": row.variant_label,
                "platform": row.platform,
                "result_id": row.result_id,
                "short_id": full_to_short.get(row.result_id, ""),
                "tuning_mode": row.tuning_mode,
                "trust_label": row.trust_label,
                "rank": ranked_row.rank,
                "total": ranked_row.total_ranked,
                "metric_value": ranked_row.metric_value,
                "speedup_vs_best": ranked_row.speedup_vs_best,
            }
        )
    return entries, ranked.primary_metric, ranked.higher_is_better


def _compute_average_ranks(platform_agg: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for pdata in platform_agg.values():
        ranks = pdata["ranks"]
        avg_rank = round(sum(r["rank"] for r in ranks.values()) / len(ranks), 1) if ranks else None
        records.append(
            {
                "platform_id": pdata["platform_id"],
                "variant_id": pdata.get("variant_id") or pdata["platform_id"],
                "variant_label": pdata.get("variant_label") or pdata["platform"],
                "platform": pdata["platform"],
                "ranks": ranks,
                "avg_rank": avg_rank,
                "n_cohorts": len(ranks),
            }
        )
    records.sort(key=lambda p: (p["avg_rank"] is None, p["avg_rank"] or float("inf"), p["variant_id"]))
    return records


def _build_meta_leaderboard(
    summaries: list[tuple[_SummaryKey, BenchmarkSummary]],
    generated_at: str,
    full_to_short: dict[str, str] | None = None,
) -> dict[str, Any]:
    if full_to_short is None:
        full_to_short = {}
    ranked_summaries = [(key, s, rank_platforms(s)) for key, s in summaries]
    eligible = [(key, s, ranked) for key, s, ranked in ranked_summaries if ranked.total_ranked >= 2]
    if not eligible:
        return {"generated_at": generated_at, "cohorts": [], "platforms": []}

    cohort_records: list[dict[str, Any]] = []
    platform_agg: dict[str, dict[str, Any]] = {}

    for (benchmark, scale_factor, phase, stream_count), summary, ranked in eligible:
        sf = _sf_str(scale_factor)
        streams_suffix = "" if stream_count is None else f"-{stream_count}streams"
        cohort_key = f"{benchmark}-sf{sf}-{phase}{streams_suffix}"
        label = _cohort_label(benchmark, sf, phase, stream_count)

        platform_entries, primary_metric, higher_is_better = _rank_platforms_in_cohort(
            summary, cohort_key, full_to_short, ranked
        )

        for entry in platform_entries:
            rank = entry["rank"]
            if rank is None:
                continue
            pid = entry["variant_id"] or entry["platform_id"]
            if pid not in platform_agg:
                platform_agg[pid] = {
                    "platform_id": entry["platform_id"],
                    "variant_id": entry["variant_id"],
                    "variant_label": entry["variant_label"],
                    "platform": entry["platform"],
                    "ranks": {},
                }
            existing = platform_agg[pid]["ranks"].get(cohort_key)
            if existing is None or rank < existing["rank"]:
                platform_agg[pid]["ranks"][cohort_key] = {
                    "rank": rank,
                    "total": entry["total"],
                    "metric_value": entry["metric_value"],
                    "speedup_vs_best": entry["speedup_vs_best"],
                }

        cohort_platforms = [{k: v for k, v in e.items() if k != "total"} for e in platform_entries]
        cohort_records.append(
            {
                "key": cohort_key,
                "benchmark": benchmark,
                "scale_factor": scale_factor,
                "phase": phase,
                "stream_count": stream_count,
                "label": label,
                "href": _cohort_href(benchmark, scale_factor, phase, stream_count),
                "platform_count": ranked.total_ranked,
                "primary_metric": primary_metric,
                "primary_order": "desc" if higher_is_better else "asc",
                "platforms": cohort_platforms,
            }
        )

    return {
        "generated_at": generated_at,
        "cohorts": cohort_records,
        "platforms": _compute_average_ranks(platform_agg),
    }


@dataclass(frozen=True)
class BuildStats:
    processed: int
    skipped: int
    cohorts: int
    output_dir: Path


class ExplorerPipeline:
    def __init__(
        self,
        transformer: BundleTransformer | None = None,
        duckdb_builder: DuckDBSnapshotBuilder | None = None,
    ) -> None:
        self._transformer = transformer or BundleTransformer()
        self._duckdb_builder = duckdb_builder or DuckDBSnapshotBuilder()

    def run(
        self,
        data_dir: Path,
        output_dir: Path,
        trust_label: str = "maintainer-run",
        visibility: str = "public-curated",
        bundle_url_prefix: str = "/results/data/bundles",
        read_model_version: int = EXPLORER_READ_MODEL_VERSION,
    ) -> BuildStats:
        bundles_dir = data_dir / "bundles"
        if not bundles_dir.exists():
            logger.warning("Bundles directory does not exist: %s", bundles_dir)
            bundle_files: list[Path] = []
        else:
            bundle_files = discover_bundles(bundles_dir)
            logger.info("Found %d bundle(s) in %s", len(bundle_files), bundles_dir)

        output_dir = output_dir.resolve()
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        staging_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
        try:
            public_anonymizer = AnonymizationManager()

            out_bundles_dir = staging_dir / "bundles"
            out_bundles_dir.mkdir(parents=True)

            manifest_entries: list[ManifestEntry] = []
            summary_accum: _SummaryAccum = defaultdict(list)
            details_map: dict[str, DetailResult] = {}
            skipped_bundles = 0
            seen_result_ids: dict[str, tuple[Path, str]] = {}

            for bundle_path in bundle_files:
                try:
                    bundle_data, bundle_raw = self._transformer.load_bundle_full(bundle_path)

                    effective_trust = trust_label
                    effective_visibility = visibility
                    if _is_vendor_subtree(bundle_path, bundles_dir):
                        effective_trust = VENDOR_TRUST_LABEL
                        effective_visibility = VENDOR_VISIBILITY
                        logger.debug(
                            "Vendor subtree bundle %s - using trust_label=%r", bundle_path.name, effective_trust
                        )
                    else:
                        effective_trust = _manifest_trust_label(bundle_path, trust_label)
                        if effective_trust != trust_label:
                            logger.debug(
                                "Recorded provenance for %s - using trust_label=%r",
                                bundle_path.name,
                                effective_trust,
                            )

                    public_bundle, public_receipt = _public_bundle_data(bundle_path, bundle_data, public_anonymizer)
                    public_raw = canonical_json_bytes(public_bundle)
                    result_id = self._transformer.result_id_from_bundle(bundle_path, data=public_bundle, raw=public_raw)
                    prefix = bundle_url_prefix.rstrip("/")
                    bundle_download_url = f"{prefix}/{result_id}.json"

                    entry = self._transformer.to_manifest_entry(
                        bundle_path,
                        trust_label=effective_trust,
                        visibility=effective_visibility,
                        result_id=result_id,
                        data=public_bundle,
                    )
                    entry = entry.model_copy(update={"applied_receipt": public_receipt})
                    public_override = _public_override_display(bundle_path)
                    entry = entry.model_copy(
                        update={
                            "override_rules": public_override["override_rules"],
                            "override_evidence": public_override["override_evidence"],
                            "override_approver": public_override["override_approver"],
                            "override_expires": public_override["override_expires"],
                        }
                    )

                    detail = self._transformer.to_detail_result(
                        bundle_path,
                        result_id,
                        trust_label=effective_trust,
                        visibility=effective_visibility,
                        bundle_download_url=bundle_download_url,
                        data=public_bundle,
                    )
                    detail = detail.model_copy(update={"applied_receipt": public_receipt})
                    detail = detail.model_copy(
                        update={
                            "override_rules": public_override["override_rules"],
                            "override_evidence": public_override["override_evidence"],
                            "override_approver": public_override["override_approver"],
                            "override_expires": public_override["override_expires"],
                        }
                    )

                    dest_bundle = (out_bundles_dir / f"{result_id}.json").resolve()
                    if not dest_bundle.is_relative_to(out_bundles_dir.resolve()):
                        skipped_bundles += 1
                        logger.warning(
                            "Skipping bundle copy for %s - result_id %r escapes bundles directory",
                            bundle_path,
                            result_id,
                        )
                        continue

                    public_digest = _publication_digest(bundle_path, public_raw)
                    previous = seen_result_ids.get(result_id)
                    if previous is not None:
                        previous_path, previous_digest = previous
                        if previous_digest != public_digest:
                            raise DuplicateResultIdError(
                                f"duplicate result_id {result_id!r} with differing published content: "
                                f"{previous_path} and {bundle_path}"
                            )
                        skipped_bundles += 1
                        logger.warning(
                            "Skipping bundle %s - result_id %r already published from %s with identical content",
                            bundle_path,
                            result_id,
                            previous_path,
                        )
                        continue
                    seen_result_ids[result_id] = (bundle_path, public_digest)

                    dest_bundle.write_bytes(public_raw)

                    detail.plans_published = False
                    for suffix in PUBLISHED_COMPANION_SUFFIXES:
                        try:
                            public_companion = _public_companion_bytes(bundle_path, suffix, public_anonymizer)
                        except CompanionPrivacyError as exc:
                            raise PrivacyRejectionError(f"{bundle_path} ({suffix} companion): {exc}") from exc
                        if public_companion is None:
                            continue

                        companion_dest = (out_bundles_dir / f"{result_id}{suffix}").resolve()
                        if not companion_dest.is_relative_to(out_bundles_dir.resolve()):
                            raise PrivacyRejectionError(
                                f"published companion path escapes bundles directory: {companion_dest.name}"
                            )
                        companion_dest.write_bytes(public_companion)
                        if suffix == ".plans.json":
                            detail.plans_published = True

                    manifest_entries.append(entry)

                    phase = canonical_phase(detail.test_type)
                    summary_key: _SummaryKey = (
                        canonical_benchmark_slug(entry.benchmark),
                        entry.scale_factor,
                        phase,
                        detail.stream_count,
                    )
                    summary_accum[summary_key].append((entry, detail))
                    details_map[entry.result_id] = detail

                    logger.debug("Processed bundle %s → %s", bundle_path.name, result_id)

                except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
                    skipped_bundles += 1
                    logger.warning("Skipping bundle %s - %s: %s", bundle_path, type(exc).__name__, exc)

            generated_at = datetime.now(tz=timezone.utc).isoformat()
            logger.info("Processed %d bundle(s)", len(manifest_entries))
            if skipped_bundles:
                logger.warning("Skipped %d bundle(s) due to processing errors", skipped_bundles)

            all_result_ids = [e.result_id for e in manifest_entries]
            short_id_map = _build_short_ids(all_result_ids)
            full_to_short = {v: k for k, v in short_id_map.items()}

            summaries = _build_benchmark_summaries(summary_accum, full_to_short)
            logger.info(
                "Built %d benchmark summary(ies) for DuckDB population",
                len(summaries),
            )

            meta = _build_meta_leaderboard(summaries, generated_at, full_to_short)
            logger.info(
                "Built meta-leaderboard (%d cohorts, %d platforms) for DuckDB population",
                len(meta["cohorts"]),
                len(meta["platforms"]),
            )

            duckdb_path = staging_dir / "results.duckdb"
            self._duckdb_builder.build_full(
                entries=manifest_entries,
                details_map=details_map,
                summaries=summaries,
                short_id_map=short_id_map,
                full_to_short=full_to_short,
                meta=meta,
                bundle_url_prefix=bundle_url_prefix,
                output_path=duckdb_path,
                read_model_version=read_model_version,
            )
            invariant_errors = check_snapshot(duckdb_path)
            if invariant_errors:
                raise ValueError("snapshot invariants failed: " + "; ".join(invariant_errors))
            _promote_staged_output(staging_dir, output_dir)
            logger.info("Promoted validated DuckDB browser store to %s", output_dir / "results.duckdb")
            return BuildStats(
                processed=len(manifest_entries),
                skipped=skipped_bundles,
                cohorts=len(summaries),
                output_dir=output_dir,
            )
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)


__all__ = ["BuildStats", "ExplorerPipeline"]
