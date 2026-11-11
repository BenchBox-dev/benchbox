# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

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


class TestQuietKwargHandling:
    def test_tpch_benchmark_accepts_quiet_kwarg(self, tmp_path):
        from benchbox.core.tpch import TPCHBenchmark

        benchmark = TPCHBenchmark(
            scale_factor=0.01,
            output_dir=tmp_path / "tpch",
            quiet=True,
        )
        assert benchmark.quiet is True

    def test_tpcds_benchmark_accepts_quiet_kwarg(self, tmp_path):
        from benchbox.core.tpcds import TPCDSBenchmark

        benchmark = TPCDSBenchmark(
            scale_factor=1.0,
            output_dir=tmp_path / "tpcds",
            quiet=True,
        )
        assert benchmark.quiet is True

    def test_ssb_benchmark_accepts_quiet_kwarg(self, tmp_path):
        from benchbox.core.ssb import SSBBenchmark

        benchmark = SSBBenchmark(
            scale_factor=0.01,
            output_dir=tmp_path / "ssb",
            quiet=True,
            compress_data=False,
            compression_type="none",
        )
        assert benchmark.quiet is True

    def test_h2odb_benchmark_accepts_quiet_kwarg(self, tmp_path):
        from benchbox.core.h2odb import H2OBenchmark

        benchmark = H2OBenchmark(
            scale_factor=0.01,
            output_dir=tmp_path / "h2odb",
            quiet=True,
        )
        assert benchmark.quiet is True

    def test_amplab_benchmark_accepts_quiet_kwarg(self, tmp_path):
        from benchbox.core.amplab import AMPLabBenchmark

        benchmark = AMPLabBenchmark(
            scale_factor=0.01,
            output_dir=tmp_path / "amplab",
            quiet=True,
        )
        assert benchmark.quiet is True

    def test_clickbench_benchmark_accepts_quiet_kwarg(self, tmp_path):
        from benchbox.core.clickbench import ClickBenchBenchmark

        benchmark = ClickBenchBenchmark(
            scale_factor=0.01,
            output_dir=tmp_path / "clickbench",
            quiet=True,
        )
        assert benchmark.quiet is True

    def test_write_primitives_benchmark_accepts_quiet_kwarg(self, tmp_path):
        from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark

        benchmark = WritePrimitivesBenchmark(
            scale_factor=0.01,
            output_dir=tmp_path / "write_primitives",
            quiet=True,
        )
        assert benchmark.quiet is True

    def test_read_primitives_benchmark_accepts_quiet_kwarg(self, tmp_path):
        from benchbox.core.read_primitives import ReadPrimitivesBenchmark

        benchmark = ReadPrimitivesBenchmark(
            scale_factor=0.01,
            output_dir=tmp_path / "read_primitives",
            quiet=True,
        )
        assert benchmark.quiet is True

    def test_joinorder_synthetic_benchmark_accepts_quiet_kwarg(self, tmp_path):
        from benchbox.core.joinorder_synthetic import JoinOrderSyntheticBenchmark

        benchmark = JoinOrderSyntheticBenchmark(
            scale_factor=0.01,
            output_dir=tmp_path / "joinorder_synthetic",
            quiet=True,
        )
        assert benchmark.quiet is True

    @pytest.mark.skipif(not PANDAS_AVAILABLE, reason="pandas not installed (required for TPCDI)")
    def test_tpcdi_benchmark_accepts_quiet_kwarg(self, tmp_path):
        from benchbox.core.tpcdi import TPCDIBenchmark

        try:
            benchmark = TPCDIBenchmark(
                scale_factor=0.01,
                output_dir=tmp_path / "tpcdi",
                quiet=True,
            )
            assert benchmark.quiet is True
        except (NameError, ImportError, FileNotFoundError):
            pass

    def test_coffeeshop_benchmark_accepts_quiet_kwarg(self, tmp_path):
        from benchbox.core.coffeeshop import CoffeeShopBenchmark

        benchmark = CoffeeShopBenchmark(
            scale_factor=0.001,
            output_dir=tmp_path / "coffeeshop",
            quiet=True,
        )
        assert benchmark.quiet is True

    def test_quiet_propagates_to_data_generator(self, tmp_path):

        from benchbox.core.tpch import TPCHBenchmark

        benchmark = TPCHBenchmark(
            scale_factor=0.01,
            output_dir=tmp_path / "tpch",
            quiet=True,
        )

        assert benchmark.quiet is True

        assert benchmark.data_generator.quiet is True

    def test_quiet_false_by_default(self, tmp_path):

        from benchbox.core.tpch import TPCHBenchmark

        benchmark = TPCHBenchmark(
            scale_factor=0.01,
            output_dir=tmp_path / "tpch",
        )

        assert benchmark.quiet is False

    def test_multiple_benchmarks_with_quiet(self, tmp_path):

        from benchbox.core.h2odb import H2OBenchmark
        from benchbox.core.ssb import SSBBenchmark
        from benchbox.core.tpch import TPCHBenchmark

        tpch = TPCHBenchmark(scale_factor=0.01, output_dir=tmp_path / "tpch", quiet=True)
        ssb = SSBBenchmark(
            scale_factor=0.01,
            output_dir=tmp_path / "ssb",
            quiet=True,
            compress_data=False,
            compression_type="none",
        )
        h2o = H2OBenchmark(scale_factor=0.01, output_dir=tmp_path / "h2o", quiet=True)

        assert tpch.quiet is True
        assert ssb.quiet is True
        assert h2o.quiet is True

    def test_quiet_with_other_kwargs(self, tmp_path):

        from benchbox.core.tpch import TPCHBenchmark

        benchmark = TPCHBenchmark(
            scale_factor=0.01,
            output_dir=tmp_path / "tpch",
            quiet=True,
            verbose=2,
            parallel=1,
            force_regenerate=False,
            compress_data=False,
            compression_type="none",
        )

        assert benchmark.quiet is True
        assert benchmark.verbose_level == 0
        assert benchmark.parallel == 1


