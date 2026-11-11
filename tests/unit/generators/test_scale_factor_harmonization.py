from __future__ import annotations

import json

import pytest

from benchbox.core.clickbench.generator import ClickBenchDataGenerator
from benchbox.core.coffeeshop.generator import CoffeeShopDataGenerator
from benchbox.core.flightdata.downloader import MONTHS_PER_SCALE_FACTOR
from benchbox.core.h2odb.generator import H2ODataGenerator
from benchbox.core.joinorder_synthetic.generator import JoinOrderGenerator
from benchbox.core.nyctaxi.downloader import SCALE_FACTOR_SAMPLE_DIVISOR
from benchbox.core.ssb.generator import SSBDataGenerator
from benchbox.core.tsbs_devops.generator import DEFAULT_DURATION_DAYS, TSBSDevOpsDataGenerator

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _coffeeshop_sf1_order_lines() -> int:
    sequence = CoffeeShopDataGenerator._expand_pattern(CoffeeShopDataGenerator.ORDER_LINE_PATTERN)
    order_count = CoffeeShopDataGenerator.calculate_order_count(1.0)
    return sum(sequence[i % len(sequence)] for i in range(order_count))


def test_spec_locked_or_already_on_target_generators_stay_put(tmp_path):
    ssb = SSBDataGenerator(scale_factor=1.0, output_dir=tmp_path, compress_data=False)
    clickbench = ClickBenchDataGenerator(scale_factor=1.0, output_dir=tmp_path, compress_data=False)

    assert ssb.base_lineorders == 6_000_000
    assert clickbench.base_records == 1_000_000


def test_adjustable_baselines_project_near_one_gb(tmp_path):
    joinorder = JoinOrderGenerator(scale_factor=1.0, output_dir=tmp_path, compress_data=False)
    amplab_projection_gb = 0.45 * 2.5
    joinorder_old_total = sum(
        [
            7,
            4,
            113,
            12,
            4,
            18,
            2_500_000,
            4_000_000,
            300_000,
            120_000,
            3_000_000,
            35_000_000,
            2_600_000,
            15_000_000,
            1_400_000,
            5_000_000,
            30_000,
            3_000_000,
            150_000,
            900_000,
            400_000,
        ]
    )
    projected_sizes_gb = {
        "coffeeshop": 6.0 * (_coffeeshop_sf1_order_lines() / 78_000_000),
        "joinorder": 5.0 * (sum(joinorder.base_row_counts.values()) / joinorder_old_total),
        "amplab": amplab_projection_gb,
        "tsbs_devops": 0.466 * (DEFAULT_DURATION_DAYS / 1),
        "flightdata": 0.416 * (MONTHS_PER_SCALE_FACTOR / 17),
        "nyctaxi": 0.096 * (100 / SCALE_FACTOR_SAMPLE_DIVISOR),
    }

    for benchmark, projected_gb in projected_sizes_gb.items():
        assert 0.8 <= projected_gb <= 1.3, f"{benchmark} projected to {projected_gb:.3f} GB at SF=1"


def test_h2odb_matches_published_small_tier(tmp_path):
    generator = H2ODataGenerator(scale_factor=1.0, output_dir=tmp_path, compress_data=False)
    assert generator.base_trips == 10_000_000


def test_tsbs_devops_supports_compressed_generation(tmp_path):
    generator = TSBSDevOpsDataGenerator(
        scale_factor=0.01,
        output_dir=tmp_path,
        num_hosts=2,
        duration_days=1,
        interval_seconds=3600,
        compress_data=True,
        compression_type="zstd",
        force_regenerate=True,
    )

    table_files = generator.generate()
    assert set(table_files) == {"tags", "cpu", "mem", "disk", "net"}
    assert all(path.suffix == ".zst" for path in table_files.values())

    manifest = json.loads((tmp_path / "_datagen_manifest.json").read_text())
    assert manifest["compression"]["enabled"] is True
    assert manifest["compression"]["type"] == "zstd"


def test_tsbs_devops_scales_linearly_not_quadratically():
    g1 = TSBSDevOpsDataGenerator(scale_factor=1)
    g2 = TSBSDevOpsDataGenerator(scale_factor=2)
    stats1 = g1.get_generation_stats()
    stats2 = g2.get_generation_stats()

    assert stats1["duration_days"] == DEFAULT_DURATION_DAYS
    assert stats2["duration_days"] == DEFAULT_DURATION_DAYS

    ratio = stats2["total_rows"] / stats1["total_rows"]
    assert 1.5 <= ratio <= 2.5, f"Expected ~2× ratio, got {ratio:.2f}× (quadratic would be ~4×)"


def test_tsbs_devops_duration_fixed_at_large_sf():

    for sf in [1, 10, 100]:
        g = TSBSDevOpsDataGenerator(scale_factor=sf)
        assert g.duration_days == DEFAULT_DURATION_DAYS, (
            f"SF={sf}: duration_days={g.duration_days}, expected {DEFAULT_DURATION_DAYS}"
        )


def test_flightdata_warns_at_corpus_ceiling(caplog):

    import logging

    from benchbox.core.flightdata.downloader import FlightDataDownloader

    with caplog.at_level(logging.WARNING, logger="benchbox.core.flightdata.downloader"):
        FlightDataDownloader(scale_factor=100, output_dir="/tmp/test_fd_warn")

    assert any("corpus exhausted" in r.message for r in caplog.records), (
        f"Expected corpus exhaustion warning, got: {[r.message for r in caplog.records]}"
    )


def test_flightdata_no_warning_below_ceiling(caplog):

    import logging

    from benchbox.core.flightdata.downloader import FlightDataDownloader

    with caplog.at_level(logging.WARNING, logger="benchbox.core.flightdata.downloader"):
        FlightDataDownloader(scale_factor=1, output_dir="/tmp/test_fd_nowarn")

    assert not any("corpus exhausted" in r.message for r in caplog.records)


def test_nyctaxi_warns_at_sample_rate_saturation(caplog):
    import logging

    from benchbox.core.nyctaxi.downloader import NYCTaxiDataDownloader

    with caplog.at_level(logging.WARNING, logger="benchbox.core.nyctaxi.downloader"):
        NYCTaxiDataDownloader(scale_factor=10, output_dir="/tmp/test_nyc_warn_boundary")

    assert any("saturated" in r.message for r in caplog.records), (
        f"Expected saturation warning at SF=10, got: {[r.message for r in caplog.records]}"
    )

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="benchbox.core.nyctaxi.downloader"):
        NYCTaxiDataDownloader(scale_factor=100, output_dir="/tmp/test_nyc_warn")

    assert any("saturated" in r.message for r in caplog.records), (
        f"Expected saturation warning at SF=100, got: {[r.message for r in caplog.records]}"
    )


def test_nyctaxi_no_warning_below_saturation(caplog):

    import logging

    from benchbox.core.nyctaxi.downloader import NYCTaxiDataDownloader

    with caplog.at_level(logging.WARNING, logger="benchbox.core.nyctaxi.downloader"):
        NYCTaxiDataDownloader(scale_factor=1, output_dir="/tmp/test_nyc_nowarn")

    assert not any("saturated" in r.message for r in caplog.records)


