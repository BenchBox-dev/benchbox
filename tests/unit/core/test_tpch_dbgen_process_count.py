from types import SimpleNamespace

import pytest

from benchbox.core.benchmark_loader import get_benchmark_instance, tpch_dbgen_process_count
from benchbox.core.schemas import BenchmarkConfig

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.parametrize(
    ("scale_factor", "cpu_cores", "expected"),
    [
        (0.01, 10, 4),
        (0.1, 10, 4),
        (0.999, 10, 4),
        (0.999, 4, 4),
        (0.999, 3, 3),
        (0.01, 2, 2),
        (0.01, 1, 1),
        (1, 10, 10),
        (1.0, 64, 64),
        (10, 10, 10),
        (10, 2, 2),
    ],
)
def test_dbgen_process_count_caps_below_scale_factor_one(scale_factor, cpu_cores, expected):
    assert tpch_dbgen_process_count(scale_factor, cpu_cores) == expected


@pytest.mark.parametrize(
    ("scale_factor", "cpu_cores", "expected"),
    [(0.01, 10, 4), (0.01, 2, 2), (1.0, 10, 10)],
)
def test_loader_passes_the_capped_count_to_the_tpch_generator(tmp_path, scale_factor, cpu_cores, expected):
    config = BenchmarkConfig(name="tpch", display_name="TPC-H", scale_factor=scale_factor)
    profile = SimpleNamespace(cpu_cores_logical=cpu_cores)

    benchmark = get_benchmark_instance(config, profile, output_dir=tmp_path, quiet=True)

    assert benchmark.data_generator.parallel == expected


def test_loader_leaves_other_benchmarks_at_the_core_count(tmp_path):
    config = BenchmarkConfig(name="tpcds", display_name="TPC-DS", scale_factor=0.01)
    profile = SimpleNamespace(cpu_cores_logical=10)

    benchmark = get_benchmark_instance(config, profile, output_dir=tmp_path, quiet=True)

    assert benchmark.parallel == 10
