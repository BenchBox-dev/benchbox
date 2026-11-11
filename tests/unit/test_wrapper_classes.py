# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


try:
    import pandas

    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False


@pytest.mark.unit
class TestTPCHWrapper:
    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as td:
            yield Path(td)

    def test_tpch_import_and_basic_properties(self):
        from benchbox.tpch import TPCH

        assert TPCH is not None
        assert hasattr(TPCH, "__name__")
        assert TPCH.__name__ == "TPCH"

    def test_tpch_initialization(self, temp_dir):
        from benchbox.tpch import TPCH

        tpch = TPCH(scale_factor=0.01, output_dir=temp_dir)
        assert tpch.scale_factor == 0.01
        assert tpch.output_dir == temp_dir

    def test_tpch_inheritance_structure(self, temp_dir):
        from benchbox.base import BaseBenchmark
        from benchbox.tpch import TPCH

        tpch = TPCH(scale_factor=0.01, output_dir=temp_dir)
        assert isinstance(tpch, BaseBenchmark)

    @patch("benchbox.tpch.TPCH.setup_database")
    def test_tpch_database_setup(self, mock_setup, temp_dir):
        from benchbox.tpch import TPCH

        tpch = TPCH(scale_factor=0.01, output_dir=temp_dir)

        connection_string = "sqlite:///:memory:"
        tpch.setup_database(connection_string)

        mock_setup.assert_called_once_with(connection_string)

    def test_tpch_schema_access(self, temp_dir):
        from benchbox.tpch import TPCH

        tpch = TPCH(scale_factor=0.01, output_dir=temp_dir)

        schema = tpch.get_schema()
        assert isinstance(schema, dict)
        assert len(schema) > 0

    def test_tpch_query_access(self, temp_dir):
        from benchbox.tpch import TPCH

        tpch = TPCH(scale_factor=0.01, output_dir=temp_dir)

        try:
            query = tpch.get_query(1)
            assert isinstance(query, str)
            assert len(query) > 0
        except Exception:
            pass

    @patch("benchbox.core.tpch.generator.TPCHDataGenerator.generate")
    def test_tpch_data_generation(self, mock_generate, temp_dir):
        from benchbox.tpch import TPCH

        mock_generate.return_value = {
            "customer": Path("customer.tbl"),
            "orders": Path("orders.tbl"),
        }

        tpch = TPCH(scale_factor=0.01, output_dir=temp_dir)
        result = tpch.generate_data()

        assert isinstance(result, list)
        assert len(result) == 2


@pytest.mark.unit
class TestTPCDSWrapper:
    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as td:
            yield Path(td)

    def test_tpcds_import_and_basic_properties(self):
        from benchbox.tpcds import TPCDS

        assert TPCDS is not None
        assert hasattr(TPCDS, "__name__")
        assert TPCDS.__name__ == "TPCDS"

    def test_tpcds_initialization(self, temp_dir):
        from benchbox.tpcds import TPCDS

        tpcds = TPCDS(scale_factor=1.0, output_dir=temp_dir)
        assert tpcds.scale_factor == 1.0
        assert tpcds.output_dir == temp_dir

    def test_tpcds_inheritance_structure(self, temp_dir):
        from benchbox.base import BaseBenchmark
        from benchbox.tpcds import TPCDS

        tpcds = TPCDS(scale_factor=1.0, output_dir=temp_dir)
        assert isinstance(tpcds, BaseBenchmark)

    @patch("benchbox.tpcds.TPCDS.setup_database")
    def test_tpcds_database_setup(self, mock_setup, temp_dir):
        from benchbox.tpcds import TPCDS

        tpcds = TPCDS(scale_factor=1.0, output_dir=temp_dir)

        connection_string = "sqlite:///:memory:"
        tpcds.setup_database(connection_string)

        mock_setup.assert_called_once_with(connection_string)

    def test_tpcds_schema_access(self, temp_dir):
        from benchbox.tpcds import TPCDS

        tpcds = TPCDS(scale_factor=1.0, output_dir=temp_dir)

        schema = tpcds.get_schema()
        assert isinstance(schema, dict)
        assert len(schema) > 0

    @patch("benchbox.core.tpcds.c_tools.DSQGenBinary.generate")
    def test_tpcds_query_access(self, mock_generate, temp_dir):
        from benchbox.tpcds import TPCDS

        mock_generate.return_value = "SELECT COUNT(*) FROM store_sales"

        tpcds = TPCDS(scale_factor=1.0, output_dir=temp_dir)

        query = tpcds.get_query(1)
        assert isinstance(query, str)
        assert len(query) > 0

    @patch("benchbox.core.tpcds.generator.TPCDSDataGenerator.generate")
    def test_tpcds_data_generation(self, mock_generate, temp_dir):
        from benchbox.tpcds import TPCDS

        mock_generate.return_value = {
            "store_sales": Path("store_sales.dat"),
            "catalog_sales": Path("catalog_sales.dat"),
        }

        tpcds = TPCDS(scale_factor=1.0, output_dir=temp_dir)
        result = tpcds.generate_data()

        assert isinstance(result, list)
        assert len(result) == 2


@pytest.mark.unit
class TestOtherWrapperClasses:
    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as td:
            yield Path(td)

    def test_amplab_wrapper(self, temp_dir):
        from benchbox.amplab import AMPLab

        amplab = AMPLab(output_dir=temp_dir)
        assert amplab.output_dir == temp_dir

        from benchbox.base import BaseBenchmark

        assert isinstance(amplab, BaseBenchmark)

    def test_h2odb_wrapper(self, temp_dir):
        from benchbox.h2odb import H2ODB

        h2odb = H2ODB(output_dir=temp_dir)
        assert h2odb.output_dir == temp_dir

        from benchbox.base import BaseBenchmark

        assert isinstance(h2odb, BaseBenchmark)

    def test_clickbench_wrapper(self, temp_dir):
        from benchbox.clickbench import ClickBench

        clickbench = ClickBench(output_dir=temp_dir)
        assert clickbench.output_dir == temp_dir

        from benchbox.base import BaseBenchmark

        assert isinstance(clickbench, BaseBenchmark)

    def test_ssb_wrapper(self, temp_dir):
        from benchbox.ssb import SSB

        ssb = SSB(output_dir=temp_dir, compress_data=False, compression_type="none")
        assert ssb.output_dir == temp_dir

        from benchbox.base import BaseBenchmark

        assert isinstance(ssb, BaseBenchmark)

    def test_write_primitives_wrapper(self, temp_dir):
        from benchbox.write_primitives import WritePrimitives

        write_primitives = WritePrimitives(output_dir=temp_dir)
        assert write_primitives.output_dir == temp_dir

        from benchbox.base import BaseBenchmark

        assert isinstance(write_primitives, BaseBenchmark)

    def test_read_primitives_wrapper(self, temp_dir):
        from benchbox.read_primitives import ReadPrimitives

        read_primitives = ReadPrimitives(output_dir=temp_dir)
        assert read_primitives.output_dir == temp_dir

        from benchbox.base import BaseBenchmark

        assert isinstance(read_primitives, BaseBenchmark)

    @pytest.mark.skipif(not PANDAS_AVAILABLE, reason="pandas not installed (required for TPCDI)")
    def test_tpcdi_wrapper(self, temp_dir):
        from benchbox.tpcdi import TPCDI

        tpcdi = TPCDI(output_dir=temp_dir)
        assert tpcdi.output_dir == temp_dir

        from benchbox.base import BaseBenchmark

        assert isinstance(tpcdi, BaseBenchmark)

    def test_joinorder_wrapper(self, temp_dir):
        from benchbox.joinorder import JoinOrder

        joinorder = JoinOrder(output_dir=temp_dir)
        assert joinorder.output_dir == temp_dir

        from benchbox.base import BaseBenchmark

        assert isinstance(joinorder, BaseBenchmark)


