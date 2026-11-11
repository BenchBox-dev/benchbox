# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox import TPCHSkew

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestTPCHSkewFacadeInit:
    def test_valid_scale_factor(self):
        b = TPCHSkew(scale_factor=0.5)
        assert b.scale_factor == 0.5

    def test_scale_factor_not_a_number_raises_type_error(self):
        with pytest.raises(TypeError):
            TPCHSkew(scale_factor="not_a_number")

    def test_negative_scale_factor_raises_value_error(self):
        with pytest.raises(ValueError):
            TPCHSkew(scale_factor=-1)

    def test_skew_preset_heavy(self):
        b = TPCHSkew(scale_factor=0.01, skew_preset="heavy")
        assert b.skew_preset == "heavy"

    def test_default_preset_is_moderate(self):
        b = TPCHSkew(scale_factor=0.01)
        assert b.skew_config.skew_factor == 0.5


class TestTPCHSkewFacadeGetQuery:
    def test_query_id_zero_raises_value_error(self):
        b = TPCHSkew(scale_factor=0.01)
        with pytest.raises(ValueError, match="1-22"):
            b.get_query(0)

    def test_query_id_23_raises_value_error(self):
        b = TPCHSkew(scale_factor=0.01)
        with pytest.raises(ValueError, match="1-22"):
            b.get_query(23)

    def test_query_id_string_raises_type_error(self):
        b = TPCHSkew(scale_factor=0.01)
        with pytest.raises(TypeError):
            b.get_query("6")

    def test_valid_query_returns_string(self):
        b = TPCHSkew(scale_factor=0.01)
        sql = b.get_query(1)
        assert isinstance(sql, str) and len(sql) > 0

    def test_invalid_scale_factor_in_get_query_raises_type_error(self):
        b = TPCHSkew(scale_factor=0.01)
        with pytest.raises(TypeError):
            b.get_query(1, scale_factor="bad")

    def test_zero_scale_factor_in_get_query_raises_value_error(self):
        b = TPCHSkew(scale_factor=0.01)
        with pytest.raises(ValueError):
            b.get_query(1, scale_factor=0)

    def test_non_int_seed_raises_type_error(self):
        b = TPCHSkew(scale_factor=0.01)
        with pytest.raises(TypeError):
            b.get_query(1, seed="x")


class TestTPCHSkewStaticMethods:
    def test_get_available_presets_contains_all(self):
        presets = TPCHSkew.get_available_presets()
        for p in ("none", "light", "moderate", "heavy"):
            assert p in presets

    def test_get_preset_description_heavy(self):
        desc = TPCHSkew.get_preset_description("heavy")
        assert isinstance(desc, str) and len(desc) > 0

    def test_get_preset_description_invalid_raises_value_error(self):
        with pytest.raises(ValueError):
            TPCHSkew.get_preset_description("invalid_preset")


class TestTPCHSkewPropertyDelegation:
    def test_skew_preset_property_returns_string(self):
        b = TPCHSkew(scale_factor=0.01)
        assert isinstance(b.skew_preset, str)

    def test_skew_config_returns_skew_configuration(self):
        from benchbox.core.tpch_skew.skew_config import SkewConfiguration

        b = TPCHSkew(scale_factor=0.01)
        assert isinstance(b.skew_config, SkewConfiguration)

    def test_tables_returns_dict(self):
        b = TPCHSkew(scale_factor=0.01)
        assert isinstance(b.tables, dict)

    def test_get_skew_info_has_required_keys(self):
        b = TPCHSkew(scale_factor=0.01)
        info = b.get_skew_info()
        assert "preset" in info
        assert "skew_factor" in info
        assert "distribution_type" in info


