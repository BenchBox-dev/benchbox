import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


pytest.importorskip("pandas")

from benchbox.core.amplab.benchmark import AMPLabBenchmark
from benchbox.core.h2odb.benchmark import H2OBenchmark
from benchbox.core.ssb.benchmark import SSBBenchmark
from benchbox.core.tpcdi.benchmark import TPCDIBenchmark
from benchbox.core.tpcds.benchmark import TPCDSBenchmark
from benchbox.core.tpch.benchmark import TPCHBenchmark


@pytest.mark.parametrize(
    "bench_cls, kwargs, gen_attr",
    [
        (TPCHBenchmark, {}, "data_generator"),
        (TPCDSBenchmark, {}, "data_generator"),
        (SSBBenchmark, {"compress_data": False, "compression_type": "none"}, "data_generator"),
        (AMPLabBenchmark, {}, "data_generator"),
        (H2OBenchmark, {}, "data_generator"),
        (TPCDIBenchmark, {}, "data_generator"),
    ],
)
def test_verbose_and_quiet_propagation(bench_cls, kwargs, gen_attr):
    b = bench_cls(scale_factor=1.0, output_dir="/tmp/testdata", verbose=2, **kwargs)

    b.quiet = True

    assert getattr(b, "verbose_level", 0) >= 2
    assert getattr(b, "quiet", False) is True

    gen = getattr(b, gen_attr)
    assert hasattr(gen, "verbose_level")
    assert hasattr(gen, "quiet")


