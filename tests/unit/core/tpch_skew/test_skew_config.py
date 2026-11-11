# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.tpch_skew.skew_config import (
    AttributeSkewConfig,
    JoinSkewConfig,
    SkewConfiguration,
    SkewPreset,
    SkewType,
    TemporalSkewConfig,
    get_preset_config,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestSkewType:
    def test_all_types_defined(self):

        assert SkewType.ATTRIBUTE.value == "attribute"
        assert SkewType.JOIN.value == "join"
        assert SkewType.TEMPORAL.value == "temporal"
        assert SkewType.COMBINED.value == "combined"


class TestSkewPreset:
    def test_all_presets_defined(self):

        presets = [
            SkewPreset.NONE,
            SkewPreset.LIGHT,
            SkewPreset.MODERATE,
            SkewPreset.HEAVY,
            SkewPreset.EXTREME,
            SkewPreset.REALISTIC,
        ]
        assert len(presets) == 6

    def test_preset_values(self):

        assert SkewPreset.NONE.value == "none"
        assert SkewPreset.LIGHT.value == "light"
        assert SkewPreset.MODERATE.value == "moderate"
        assert SkewPreset.HEAVY.value == "heavy"
        assert SkewPreset.EXTREME.value == "extreme"
        assert SkewPreset.REALISTIC.value == "realistic"


class TestAttributeSkewConfig:
    def test_default_values(self):

        config = AttributeSkewConfig()
        assert config.customer_nation_skew == 0.0
        assert config.customer_segment_skew == 0.0
        assert config.part_brand_skew == 0.0
        assert config.shipmode_skew == 0.0

    def test_custom_values(self):

        config = AttributeSkewConfig(
            customer_nation_skew=0.5,
            part_brand_skew=0.8,
        )
        assert config.customer_nation_skew == 0.5
        assert config.part_brand_skew == 0.8

    def test_get_active_skews(self):

        config = AttributeSkewConfig(
            customer_nation_skew=0.5,
            part_brand_skew=0.0,
            shipmode_skew=0.3,
        )
        active = config.get_active_skews()
        assert "customer_nation_skew" in active
        assert "shipmode_skew" in active
        assert "part_brand_skew" not in active


class TestJoinSkewConfig:
    def test_default_values(self):

        config = JoinSkewConfig()
        assert config.customer_order_skew == 0.0
        assert config.part_popularity_skew == 0.0
        assert config.supplier_volume_skew == 0.0

    def test_custom_values(self):

        config = JoinSkewConfig(
            customer_order_skew=0.7,
            part_popularity_skew=0.9,
        )
        assert config.customer_order_skew == 0.7
        assert config.part_popularity_skew == 0.9

    def test_get_active_skews(self):

        config = JoinSkewConfig(
            customer_order_skew=0.7,
            part_popularity_skew=0.0,
        )
        active = config.get_active_skews()
        assert "customer_order_skew" in active
        assert "part_popularity_skew" not in active


class TestTemporalSkewConfig:
    def test_default_values(self):

        config = TemporalSkewConfig()
        assert config.order_date_skew == 0.0
        assert config.enable_hot_periods is False
        assert config.hot_period_intensity == 0.5

    def test_hot_periods_in_active_skews(self):

        config = TemporalSkewConfig(enable_hot_periods=True)
        active = config.get_active_skews()
        assert "enable_hot_periods" in active


class TestSkewConfiguration:
    def test_default_configuration(self):

        config = SkewConfiguration()
        assert config.skew_factor == 0.5
        assert config.distribution_type == "zipfian"
        assert config.enable_attribute_skew is True
        assert config.enable_join_skew is True
        assert config.enable_temporal_skew is False

    def test_custom_configuration(self):

        config = SkewConfiguration(
            skew_factor=0.8,
            distribution_type="normal",
            enable_temporal_skew=True,
        )
        assert config.skew_factor == 0.8
        assert config.distribution_type == "normal"
        assert config.enable_temporal_skew is True

    def test_get_skew_summary(self):

        config = SkewConfiguration(skew_factor=0.6)
        summary = config.get_skew_summary()

        assert "skew_factor" in summary
        assert "distribution" in summary
        assert "attribute_skew" in summary
        assert "join_skew" in summary
        assert "temporal_skew" in summary
        assert summary["skew_factor"] == 0.6

    def test_validate_valid_config(self):

        config = SkewConfiguration(skew_factor=0.5)
        warnings = config.validate()
        assert len(warnings) == 0

    def test_validate_invalid_skew_factor(self):

        config = SkewConfiguration(skew_factor=1.5)
        warnings = config.validate()
        assert any("skew_factor" in w for w in warnings)

    def test_validate_unknown_distribution(self):

        config = SkewConfiguration(distribution_type="gamma")
        warnings = config.validate()
        assert any("distribution_type" in w for w in warnings)

    def test_validate_high_skew_warning(self):

        config = SkewConfiguration(attribute_skew=AttributeSkewConfig(customer_nation_skew=0.95))
        warnings = config.validate()
        assert any("high skew" in w.lower() for w in warnings)


class TestGetPresetConfig:
    def test_none_preset(self):

        config = get_preset_config(SkewPreset.NONE)
        assert config.skew_factor == 0.0
        assert config.distribution_type == "uniform"
        assert config.enable_attribute_skew is False
        assert config.enable_join_skew is False

    def test_light_preset(self):

        config = get_preset_config(SkewPreset.LIGHT)
        assert config.skew_factor == 0.2
        assert config.distribution_type == "zipfian"
        assert config.attribute_skew.customer_nation_skew == 0.2
        assert config.join_skew.customer_order_skew == 0.2

    def test_moderate_preset(self):

        config = get_preset_config(SkewPreset.MODERATE)
        assert config.skew_factor == 0.5
        assert config.attribute_skew.customer_nation_skew == 0.5
        assert config.join_skew.customer_order_skew == 0.5

    def test_heavy_preset(self):

        config = get_preset_config(SkewPreset.HEAVY)
        assert config.skew_factor == 0.8
        assert config.enable_temporal_skew is True
        assert config.attribute_skew.customer_nation_skew == 0.8

    def test_extreme_preset(self):

        config = get_preset_config(SkewPreset.EXTREME)
        assert config.skew_factor == 1.0
        assert config.attribute_skew.part_brand_skew == 1.0
        assert config.join_skew.part_popularity_skew == 1.0
        assert config.temporal_skew.enable_hot_periods is True

    def test_realistic_preset(self):

        config = get_preset_config(SkewPreset.REALISTIC)
        assert config.skew_factor == 0.6
        assert config.enable_temporal_skew is True
        assert config.attribute_skew.part_brand_skew == 0.8

    def test_seed_propagation(self):

        config = get_preset_config(SkewPreset.MODERATE, seed=42)
        assert config.seed == 42

    def test_presets_valid(self):

        for preset in SkewPreset:
            config = get_preset_config(preset)
            warnings = config.validate()
            assert not any("must be" in w for w in warnings)


