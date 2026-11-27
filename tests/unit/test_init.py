# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import importlib

import pytest

import benchbox
from benchbox import BaseBenchmark

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
class TestPackageInit:
    def test_version_exists(self):
        assert hasattr(benchbox, "__version__")
        assert isinstance(benchbox.__version__, str)
        assert len(benchbox.__version__) > 0

    def test_base_benchmark_import(self):
        assert hasattr(benchbox, "BaseBenchmark")
        assert benchbox.BaseBenchmark is BaseBenchmark

    def test_tpch_import(self):
        assert hasattr(benchbox, "TPCH")
        from benchbox.tpch import TPCH

        assert benchbox.TPCH is TPCH

    def test_tpcds_import(self):
        assert hasattr(benchbox, "TPCDS")
        from benchbox.tpcds import TPCDS

        assert benchbox.TPCDS is TPCDS


@pytest.mark.unit
class TestLazyImports:
    def test_lazy_import_write_primitives(self):
        write_primitives_class = benchbox.WritePrimitives
        assert write_primitives_class is not None
        assert hasattr(write_primitives_class, "__name__")
        assert write_primitives_class.__name__ == "WritePrimitives"

    def test_lazy_import_read_primitives(self):
        read_primitives_class = benchbox.ReadPrimitives
        assert read_primitives_class is not None
        assert hasattr(read_primitives_class, "__name__")
        assert read_primitives_class.__name__ == "ReadPrimitives"

    @pytest.mark.skipif(not PANDAS_AVAILABLE, reason="pandas not installed (required for TPCDI)")
    def test_lazy_import_tpcdi(self):
        tpcdi_class = benchbox.TPCDI
        assert tpcdi_class is not None
        assert hasattr(tpcdi_class, "__name__")

    def test_lazy_import_ssb(self):
        ssb_class = benchbox.SSB
        assert ssb_class is not None
        assert hasattr(ssb_class, "__name__")

    def test_lazy_import_amplab(self):
        amplab_class = benchbox.AMPLab
        assert amplab_class is not None
        assert hasattr(amplab_class, "__name__")

    def test_lazy_import_h2odb(self):
        h2odb_class = benchbox.H2ODB
        assert h2odb_class is not None
        assert hasattr(h2odb_class, "__name__")

    def test_lazy_import_clickbench(self):
        clickbench_class = benchbox.ClickBench
        assert clickbench_class is not None
        assert hasattr(clickbench_class, "__name__")

    def test_lazy_import_joinorder(self):
        joinorder_class = benchbox.JoinOrder
        assert joinorder_class is not None
        assert hasattr(joinorder_class, "__name__")

    def test_invalid_attribute(self):
        with pytest.raises(AttributeError, match="module 'benchbox' has no attribute"):
            _ = benchbox.InvalidBenchmark

    def test_all_exports(self):
        assert hasattr(benchbox, "__all__")
        expected = [
            "BaseBenchmark",
            "TPCH",
            "TPCDS",
            "TPCDI",
            "SSB",
            "AMPLab",
            "H2ODB",
            "ClickBench",
            "WritePrimitives",
            "ReadPrimitives",
            "JoinOrder",
        ]
        for item in expected:
            assert item in benchbox.__all__, f"Expected {item} in __all__"

    def test_primitives_are_lazy_loadable(self):
        write_primitives_class = benchbox.WritePrimitives
        read_primitives_class = benchbox.ReadPrimitives

        lazy_cache = benchbox.__getattr__.__globals__["_lazy_cache"]
        assert "WritePrimitives" in lazy_cache
        assert "ReadPrimitives" in lazy_cache
        assert write_primitives_class.__name__ == "WritePrimitives"
        assert read_primitives_class.__name__ == "ReadPrimitives"

    @pytest.mark.skipif(not PANDAS_AVAILABLE, reason="pandas not installed (required for TPCDI)")
    def test_lazy_import_failure_provides_actionable_message(self, monkeypatch):
        importlib.import_module("benchbox.__init__")

        lazy_cache = benchbox.__getattr__.__globals__["_lazy_cache"]
        lazy_cache.clear()
        import benchbox as benchbox_package

        module_dict = benchbox_package.__dict__
        had_existing_attr = "TPCDI" in module_dict
        existing_attr = module_dict.get("TPCDI") if had_existing_attr else None
        if had_existing_attr:
            del module_dict["TPCDI"]

        simulated_error = ModuleNotFoundError("No module named 'pandas'", name="pandas")
        lazy_cache["TPCDI"] = simulated_error
        assert lazy_cache["TPCDI"] is simulated_error

        try:
            with pytest.raises(ImportError) as excinfo:
                _ = benchbox.TPCDI

            message = str(excinfo.value)
            assert "'pandas'" in message
            assert "tpcdi" in message
            assert "benchbox[tpcdi]" in message or "--extra tpcdi" in message
            assert "Version Information" in message
            assert "Original error" in message
        finally:
            lazy_cache.clear()
            if had_existing_attr and existing_attr is not None:
                benchbox_package.TPCDI = existing_attr
            else:
                from benchbox.tpcdi import TPCDI as _TPCDI

                benchbox_package.TPCDI = _TPCDI
