from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest

from benchbox.core.coffeeshop.generator import CoffeeShopDataGenerator, _ProductWindow

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def gen(tmp_path: Path) -> CoffeeShopDataGenerator:
    return CoffeeShopDataGenerator(scale_factor=0.000001, output_dir=tmp_path)


class TestCoffeeShopInit:
    def test_default_dates(self, gen: CoffeeShopDataGenerator):
        assert gen.start_date == date(2023, 1, 1)
        assert gen.end_date == date(2024, 12, 31)

    def test_scale_factor_stored(self, gen: CoffeeShopDataGenerator):
        assert gen.scale_factor == 0.000001

    def test_product_windows_populated(self, gen: CoffeeShopDataGenerator):
        assert len(gen._product_windows) > 0
        for w in gen._product_windows:
            assert isinstance(w, _ProductWindow)
            assert w.start <= w.end

    def test_location_seeds_loaded(self, gen: CoffeeShopDataGenerator):
        assert len(gen._location_seeds) > 0


class TestCalculateOrderCount:
    def test_sf_1_gives_8_5m(self):
        assert CoffeeShopDataGenerator.calculate_order_count(1.0) == 8_500_000

    def test_sf_0_gives_minimum_1(self):
        assert CoffeeShopDataGenerator.calculate_order_count(0.0) == 1

    def test_scales_linearly(self):
        count_1 = CoffeeShopDataGenerator.calculate_order_count(0.01)
        count_2 = CoffeeShopDataGenerator.calculate_order_count(0.02)
        assert count_2 == pytest.approx(count_1 * 2, abs=1)


class TestExpandPattern:
    def test_simple_expansion(self):
        result = CoffeeShopDataGenerator._expand_pattern([(1, 3), (2, 2)])
        assert result == [1, 1, 1, 2, 2]

    def test_empty_pattern(self):
        result = CoffeeShopDataGenerator._expand_pattern([])
        assert result == []

    def test_order_line_pattern_length(self):
        result = CoffeeShopDataGenerator._expand_pattern(CoffeeShopDataGenerator.ORDER_LINE_PATTERN)
        assert len(result) == sum(w for _, w in CoffeeShopDataGenerator.ORDER_LINE_PATTERN)


class TestDistributeWithWeights:
    def test_sums_to_total(self, gen: CoffeeShopDataGenerator):
        weights = {"a": 3.0, "b": 1.0, "c": 1.0}
        result = gen._distribute_with_weights(100, weights)
        assert sum(result.values()) == 100

    def test_higher_weight_gets_more(self, gen: CoffeeShopDataGenerator):
        weights = {"a": 10.0, "b": 1.0}
        result = gen._distribute_with_weights(100, weights)
        assert result["a"] > result["b"]

    def test_zero_total_gives_zeros(self, gen: CoffeeShopDataGenerator):
        weights = {"a": 1.0, "b": 1.0}
        result = gen._distribute_with_weights(0, weights)
        assert result == {"a": 0, "b": 0}

    def test_equal_weights_even_split(self, gen: CoffeeShopDataGenerator):
        weights = {"a": 1.0, "b": 1.0}
        result = gen._distribute_with_weights(10, weights)
        assert result["a"] == 5
        assert result["b"] == 5

    def test_all_zero_weights_equal_distribution(self, gen: CoffeeShopDataGenerator):
        weights = {"a": 0.0, "b": 0.0}
        result = gen._distribute_with_weights(10, weights)
        assert sum(result.values()) == 10


class TestTrendWeight:
    def test_start_weight(self, gen: CoffeeShopDataGenerator):
        weight = gen._trend_weight(0, 100)
        assert weight == pytest.approx(gen.TREND_START_WEIGHT)

    def test_end_weight(self, gen: CoffeeShopDataGenerator):
        weight = gen._trend_weight(99, 100)
        assert weight == pytest.approx(gen.TREND_END_WEIGHT)

    def test_single_day_uses_end_weight(self, gen: CoffeeShopDataGenerator):
        weight = gen._trend_weight(0, 1)
        assert weight == pytest.approx(gen.TREND_END_WEIGHT)

    def test_midpoint_between_start_and_end(self, gen: CoffeeShopDataGenerator):
        weight = gen._trend_weight(50, 101)
        expected = (gen.TREND_START_WEIGHT + gen.TREND_END_WEIGHT) / 2
        assert weight == pytest.approx(expected, abs=0.01)


class TestDailyDistribution:
    def test_sums_to_total_orders(self, gen: CoffeeShopDataGenerator):
        total = gen.calculate_order_count(gen.scale_factor)
        distribution = gen._distribute_daily_orders(total)
        assert sum(distribution.values()) == total

    def test_all_dates_in_range(self, gen: CoffeeShopDataGenerator):
        total = gen.calculate_order_count(gen.scale_factor)
        distribution = gen._distribute_daily_orders(total)
        for d in distribution:
            assert gen.start_date <= d <= gen.end_date


class TestBuildProductWindows:
    def test_windows_have_seeds(self, gen: CoffeeShopDataGenerator):
        for w in gen._product_windows:
            total = sum(len(seeds) for seeds in w.seeds_by_subcategory.values())
            assert total > 0

    def test_windows_sorted_by_date(self, gen: CoffeeShopDataGenerator):
        for i in range(len(gen._product_windows) - 1):
            assert gen._product_windows[i].start <= gen._product_windows[i + 1].start


class TestGroupLocationsByRegion:
    def test_all_seeds_grouped(self, gen: CoffeeShopDataGenerator):
        grouped = gen._group_locations_by_region()
        total = sum(len(locs) for locs in grouped.values())
        assert total == len(gen._location_seeds)

    def test_regions_are_strings(self, gen: CoffeeShopDataGenerator):
        grouped = gen._group_locations_by_region()
        for region in grouped:
            assert isinstance(region, str)
            assert len(region) > 0


class TestSelectProductForDate:
    def test_selects_product_for_valid_date(self, gen: CoffeeShopDataGenerator):
        seed = gen._select_product_for_date(date(2023, 6, 15), "Coffee")
        assert seed is not None
        assert hasattr(seed, "product_id")

    def test_raises_for_out_of_range_date(self, gen: CoffeeShopDataGenerator):
        with pytest.raises(ValueError, match="No product seeds available"):
            gen._select_product_for_date(date(1900, 1, 1), "Coffee")


class TestResetGenerationState:
    def test_clears_row_counts(self, gen: CoffeeShopDataGenerator):
        gen._table_row_counts["test"] = 100
        gen._reset_generation_state()
        assert gen._table_row_counts == {}


class TestGenerateData:
    def test_generates_all_tables(self, tmp_path: Path):
        gen = CoffeeShopDataGenerator(scale_factor=0.000001, output_dir=tmp_path)
        result = gen.generate_data()
        assert "dim_locations" in result
        assert "dim_products" in result
        assert "order_lines" in result

    def test_generated_files_exist(self, tmp_path: Path):
        gen = CoffeeShopDataGenerator(scale_factor=0.000001, output_dir=tmp_path)
        result = gen.generate_data()
        for name, path_str in result.items():
            assert Path(path_str).exists(), f"{name} file does not exist"

    def test_dim_locations_has_rows(self, tmp_path: Path):
        gen = CoffeeShopDataGenerator(scale_factor=0.000001, output_dir=tmp_path)
        gen.generate_data()
        assert gen._table_row_counts["dim_locations"] > 0

    def test_order_lines_has_rows(self, tmp_path: Path):
        gen = CoffeeShopDataGenerator(scale_factor=0.000001, output_dir=tmp_path)
        gen.generate_data()
        assert gen._table_row_counts["order_lines"] > 0

    def test_subset_table_generation(self, tmp_path: Path):
        gen = CoffeeShopDataGenerator(scale_factor=0.000001, output_dir=tmp_path)
        result = gen.generate_data(tables=["dim_locations"])
        assert "dim_locations" in result

    def test_order_lines_pulls_in_dimension_tables(self, tmp_path: Path):
        gen = CoffeeShopDataGenerator(scale_factor=0.000001, output_dir=tmp_path)
        result = gen.generate_data(tables=["order_lines"])
        assert "dim_locations" in result
        assert "dim_products" in result

    def test_empty_table_list(self, tmp_path: Path):
        gen = CoffeeShopDataGenerator(scale_factor=0.000001, output_dir=tmp_path)
        result = gen.generate_data(tables=[])
        assert result == {}


