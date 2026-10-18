# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from pathlib import Path
from unittest import mock

import pytest

from benchbox.tpcds import TPCDS
from benchbox.tpch import TPCH

pytestmark = pytest.mark.medium


class TestErrorHandlingConsistency:
    def test_invalid_scale_factor_type_consistency(self):
        with pytest.raises(TypeError, match="scale_factor must be a number"):
            TPCH(scale_factor="invalid")

        with pytest.raises(TypeError, match="scale_factor must be a number"):
            TPCDS(scale_factor="invalid")

        with pytest.raises(TypeError, match="scale_factor must be a number"):
            TPCH(scale_factor=None)

        with pytest.raises(TypeError, match="scale_factor must be a number"):
            TPCDS(scale_factor=None)

    def test_invalid_scale_factor_value_consistency(self):
        with pytest.raises(ValueError, match="Scale factor must be positive"):
            TPCH(scale_factor=0)

        with pytest.raises(ValueError, match="Scale factor must be positive"):
            TPCDS(scale_factor=0)

        with pytest.raises(ValueError, match="Scale factor must be positive"):
            TPCH(scale_factor=-1.0)

        with pytest.raises(ValueError, match="Scale factor must be positive"):
            TPCDS(scale_factor=-1.0)

    def test_invalid_query_id_type_consistency(self):
        tpch = TPCH()
        tpcds = TPCDS()

        with pytest.raises(TypeError, match="query_id must be an integer"):
            tpch.get_query("invalid")

        with pytest.raises(TypeError, match="query_id must be an integer"):
            tpcds.get_query("invalid")

        with pytest.raises(TypeError, match="query_id must be an integer"):
            tpch.get_query(1.5)

        with pytest.raises(TypeError, match="query_id must be an integer"):
            tpcds.get_query(1.5)

        with pytest.raises(TypeError, match="query_id must be an integer"):
            tpch.get_query(None)

        with pytest.raises(TypeError, match="query_id must be an integer"):
            tpcds.get_query(None)

    def test_invalid_query_id_range_consistency(self):
        tpch = TPCH()
        tpcds = TPCDS()

        with pytest.raises(ValueError, match="Query ID must be 1-22"):
            tpch.get_query(0)

        with pytest.raises(ValueError, match="Query ID must be 1-99"):
            tpcds.get_query(0)

        with pytest.raises(ValueError, match="Query ID must be 1-22"):
            tpch.get_query(23)

        with pytest.raises(ValueError, match="Query ID must be 1-99"):
            tpcds.get_query(100)

        with pytest.raises(ValueError, match="Query ID must be 1-22"):
            tpch.get_query(9999)

        with pytest.raises(ValueError, match="Query ID must be 1-99"):
            tpcds.get_query(9999)

    def test_invalid_seed_type_consistency(self):
        tpch = TPCH()
        tpcds = TPCDS()

        with pytest.raises(TypeError, match="seed must be an integer"):
            tpch.get_query(1, seed="invalid")

        with pytest.raises(TypeError, match="seed must be an integer"):
            tpcds.get_query(1, seed="invalid")

        with pytest.raises(TypeError, match="seed must be an integer"):
            tpch.get_query(1, seed=1.5)

        with pytest.raises(TypeError, match="seed must be an integer"):
            tpcds.get_query(1, seed=1.5)

    def test_invalid_scale_factor_in_get_query_consistency(self):
        tpch = TPCH()
        tpcds = TPCDS()

        with pytest.raises(TypeError, match="scale_factor must be a number"):
            tpch.get_query(1, scale_factor="invalid")

        with pytest.raises(TypeError, match="scale_factor must be a number"):
            tpcds.get_query(1, scale_factor="invalid")

        with pytest.raises(ValueError, match="scale_factor must be positive"):
            tpch.get_query(1, scale_factor=0)

        with pytest.raises(ValueError, match="scale_factor must be positive"):
            tpcds.get_query(1, scale_factor=0)

        with pytest.raises(ValueError, match="scale_factor must be positive"):
            tpch.get_query(1, scale_factor=-1.0)

        with pytest.raises(ValueError, match="scale_factor must be positive"):
            tpcds.get_query(1, scale_factor=-1.0)

    @mock.patch("benchbox.core.tpch.generator.TPCHDataGenerator._find_or_build_dbgen")
    def test_valid_inputs_accepted_consistently(self, mock_dbgen_build):
        mock_dbgen_build.return_value = Path("/tmp/dbgen")

        tpch = TPCH(scale_factor=1.0)
        tpcds = TPCDS(scale_factor=1.0)

        assert tpch.scale_factor == 1.0
        assert tpcds.scale_factor == 1.0

        tpch_sf = TPCH(scale_factor=0.1)
        tpcds_sf = TPCDS(scale_factor=1.0)

        assert tpch_sf.scale_factor == 0.1
        assert tpcds_sf.scale_factor == 1.0


class TestErrorMessageFormats:
    def test_type_error_message_format(self):
        with pytest.raises(TypeError) as exc_info:
            TPCH(scale_factor="invalid")

        error_msg = str(exc_info.value)
        assert "scale_factor must be a number, got str" in error_msg

        with pytest.raises(TypeError) as exc_info:
            TPCDS(scale_factor="invalid")

        error_msg = str(exc_info.value)
        assert "scale_factor must be a number, got str" in error_msg

    def test_value_error_message_format(self):
        with pytest.raises(ValueError) as exc_info:
            TPCH(scale_factor=-1.0)

        error_msg = str(exc_info.value)
        assert "Scale factor must be positive" in error_msg

        with pytest.raises(ValueError) as exc_info:
            TPCDS(scale_factor=-1.0)

        error_msg = str(exc_info.value)
        assert "Scale factor must be positive" in error_msg

    def test_query_id_error_message_format(self):
        tpch = TPCH()
        tpcds = TPCDS()

        with pytest.raises(ValueError) as exc_info:
            tpch.get_query(100)

        error_msg = str(exc_info.value)
        assert "Query ID must be 1-22, got 100" in error_msg

        with pytest.raises(ValueError) as exc_info:
            tpcds.get_query(200)

        error_msg = str(exc_info.value)
        assert "Query ID must be 1-99, got 200" in error_msg
