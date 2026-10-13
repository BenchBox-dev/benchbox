from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from _project.scripts.explorer_pipeline.models import (
    BenchmarkSummary,
    ManifestEntry,
    PlatformRow,
    RankingConfig,
    ranking_exclusion_reason,
    variant_label,
)
from _project.scripts.explorer_pipeline.ranking import rank_platforms
from _project.scripts.explorer_pipeline.transformer import BundleTransformer

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_EXPLORER_FIXTURES = pytest.importorskip(
    "tests.unit.scripts.explorer_pipeline.conftest", reason="Explorer pipeline test fixtures are not in this checkout"
)
MINIMAL_BUNDLE = _EXPLORER_FIXTURES.MINIMAL_BUNDLE


def _entry(**overrides) -> ManifestEntry:
    base = {
        "result_id": "r1",
        "benchmark": "tpch",
        "scale_factor": 0.01,
        "platform": "polars-df",
        "platform_id": "polars-df",
        "driver_version": "1.0",
        "run_date": "2026-01-01",
        "power_score": 1.0,
        "total_duration_s": 1.0,
        "query_count": 22,
        "display_geomean_ms": 100.0,
        "trust_label": "maintainer-run",
        "visibility": "public-curated",
        "validation_status": "passed",
    }
    base.update(overrides)
    return ManifestEntry(**base)


def test_legacy_bundle_keeps_platform_identity() -> None:
    entry = _entry()
    assert entry.variant_id == "polars-df"
    assert entry.variant_label == "polars-df"
    assert entry.requested_execution_engine == "default"
    assert entry.gateway_routed is False
    assert ranking_exclusion_reason(entry) is None


def test_engine_request_defines_variant_identity() -> None:
    entry = _entry(
        variant_id="polars-df+streaming",
        requested_execution_engine="streaming",
        variant_label="",
    )
    assert entry.variant_id == "polars-df+streaming"
    assert entry.variant_label == "polars-df (streaming)"
    assert ranking_exclusion_reason(entry) is None


def test_gateway_routed_bundle_is_excluded_from_native_ranking() -> None:
    entry = _entry(
        platform="snowflake",
        platform_id="snowflake",
        variant_id="snowflake@greybeam",
        gateway_name="greybeam",
        gateway_routed=True,
    )
    assert entry.variant_label == "snowflake via Greybeam"
    assert ranking_exclusion_reason(entry) == "gateway_routed"


def test_variant_label_cases() -> None:
    assert variant_label("duckdb", variant_id="duckdb") == "duckdb"
    assert (
        variant_label("polars-df", variant_id="polars-df+streaming", requested_execution_engine="streaming")
        == "polars-df (streaming)"
    )
    assert (
        variant_label("snowflake", variant_id="snowflake@greybeam", gateway_name="greybeam", gateway_routed=True)
        == "snowflake via Greybeam"
    )


def _write_bundle(tmp_path: Path, name: str, platform_block: dict) -> Path:
    payload = copy.deepcopy(MINIMAL_BUNDLE)
    payload["platform"] = platform_block
    path = tmp_path / name
    path.write_text(json.dumps(payload))
    return path


def test_transformer_builds_variant_from_legacy_base_and_requested_engine(tmp_path: Path) -> None:
    transformer = BundleTransformer()
    bundle = _write_bundle(
        tmp_path,
        "variant.json",
        {
            "name": "Polars",
            "variant": "polars-df+streaming",
            "execution_engine": {"requested": "streaming", "resolution": "explicit"},
        },
    )
    entry = transformer.to_manifest_entry(bundle)
    assert entry.platform_id == "polars"
    assert entry.variant_id == "polars+streaming"
    assert entry.variant_label == "Polars (streaming)"
    assert entry.requested_execution_engine == "streaming"
    assert entry.gateway_routed is False
    detail = transformer.to_detail_result(bundle, "variant-result")
    assert detail.variant_id == "polars+streaming"
    assert detail.variant_label == "Polars (streaming)"


def test_transformer_default_run_joins_the_legacy_platform_row(tmp_path: Path) -> None:
    transformer = BundleTransformer()
    bundle = _write_bundle(
        tmp_path,
        "default.json",
        {"name": "Polars", "variant": "polars-df", "execution_engine": {"requested": "default"}},
    )
    entry = transformer.to_manifest_entry(bundle)
    assert entry.variant_id == entry.platform_id == "polars"
    assert entry.variant_label == "Polars"


def test_transformer_reads_legacy_engine_request_through_core_reader(tmp_path: Path) -> None:
    transformer = BundleTransformer()
    bundle = _write_bundle(tmp_path, "matrix.json", {"name": "Polars", "config": {"engine_requested": "streaming"}})
    entry = transformer.to_manifest_entry(bundle)
    assert entry.variant_id == "polars+streaming"
    assert entry.requested_execution_engine == "streaming"
    assert entry.variant_label == "Polars (streaming)"


def test_transformer_carries_gateway_routing(tmp_path: Path) -> None:
    transformer = BundleTransformer()
    bundle = _write_bundle(
        tmp_path,
        "gateway.json",
        {
            "name": "Snowflake",
            "variant": "snowflake@greybeam",
            "gateway": {"name": "greybeam", "routed": True},
        },
    )
    entry = transformer.to_manifest_entry(bundle)
    assert entry.variant_id == "snowflake@greybeam"
    assert entry.gateway_routed is True
    assert entry.gateway_name == "greybeam"
    assert entry.variant_label == "Snowflake via Greybeam"
    assert ranking_exclusion_reason(entry) == "gateway_routed"


def test_transformer_legacy_bundle_keeps_identity(tmp_path: Path) -> None:
    transformer = BundleTransformer()
    bundle = _write_bundle(tmp_path, "legacy.json", {"name": "duckdb", "version": "1.2.0"})
    entry = transformer.to_manifest_entry(bundle)
    assert entry.variant_id == entry.platform_id == "duckdb"
    assert entry.variant_label == "duckdb"


def test_transformer_blank_variant_falls_through_to_requested(tmp_path: Path) -> None:
    transformer = BundleTransformer()
    bundle = _write_bundle(
        tmp_path,
        "blank.json",
        {"name": "Polars", "variant": "  ", "execution_engine": {"requested": "streaming"}},
    )
    entry = transformer.to_manifest_entry(bundle)
    assert entry.variant_id == "polars+streaming"


def test_transformer_ignores_the_stored_variant_string(tmp_path: Path) -> None:
    transformer = BundleTransformer()
    bundle = _write_bundle(
        tmp_path,
        "same.json",
        {"name": "Polars", "variant": "Polars", "execution_engine": {"requested": "streaming"}},
    )
    entry = transformer.to_manifest_entry(bundle)
    assert entry.variant_id == "polars+streaming"


def _row(variant_id: str, result_id: str, geomean_ms: float, *, eligible: bool = True) -> PlatformRow:
    return PlatformRow(
        result_id=result_id,
        short_id=result_id,
        platform_id="polars-df",
        variant_id=variant_id,
        variant_label=f"polars-df ({variant_id})",
        platform="polars-df",
        platform_version=None,
        tuning_mode=None,
        tuning_hash=None,
        execution_mode=None,
        trust_label="maintainer-run",
        run_date="2026-05-05T00:00:00Z",
        is_ranking_eligible=eligible,
        ranking_exclusion_reason=None if eligible else "gateway_routed",
        power_score=None,
        display_geomean_ms=geomean_ms,
        sample_geomean_ms=None,
        cost_usd=None,
        compliance_class=None,
        percentile_stats=None,
        phase_durations=None,
        timings={},
    )


def test_engine_variants_rank_as_separate_rows() -> None:
    summary = BenchmarkSummary(
        benchmark="tpch",
        scale_factor=0.01,
        phase="power",
        query_ids=[],
        platforms=[
            _row("polars-df", "result-default", 100.0),
            _row("polars-df+streaming", "result-streaming", 200.0),
        ],
        ranking=RankingConfig(
            primary_metric="display_geomean_ms",
            secondary_metric="display_geomean_ms",
            primary_order="asc",
        ),
    )
    ranked = rank_platforms(summary)
    by_result = {row.row.result_id: row for row in ranked.rows}
    assert ranked.total_ranked == 2
    assert by_result["result-default"].rank == 1
    assert by_result["result-streaming"].rank == 2
    assert by_result["result-streaming"].row.variant_id == "polars-df+streaming"


def test_gateway_row_is_excluded_with_reason() -> None:
    summary = BenchmarkSummary(
        benchmark="tpch",
        scale_factor=0.01,
        phase="power",
        query_ids=[],
        platforms=[
            _row("snowflake", "result-native", 100.0),
            _row("snowflake@greybeam", "result-gateway", 50.0, eligible=False),
        ],
        ranking=RankingConfig(
            primary_metric="display_geomean_ms",
            secondary_metric="display_geomean_ms",
            primary_order="asc",
        ),
    )
    ranked = rank_platforms(summary)
    by_result = {row.row.result_id: row for row in ranked.rows}
    assert ranked.total_ranked == 1
    assert by_result["result-native"].rank == 1
    assert by_result["result-gateway"].rank is None
    assert by_result["result-gateway"].ranking_exclusion_reason == "gateway_routed"
