# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import contextlib
from collections.abc import Generator
from pathlib import Path
from typing import Any, Optional, Union
from unittest.mock import Mock, patch

import pytest

from benchbox import TPCDS, TPCH

SCALE_FACTORS = {
    "small": 1.0,
    "medium": 1.0,
    "large": 1.0,
}

BENCHMARK_CLASSES = {
    "tpch": ("benchbox", "TPCH"),
    "tpcds": ("benchbox", "TPCDS"),
    "tpcdi": ("benchbox", "TPCDI"),
    "ssb": ("benchbox", "SSB"),
    "amplab": ("benchbox", "AMPLab"),
    "h2odb": ("benchbox", "H2ODB"),
    "clickbench": ("benchbox", "ClickBench"),
    "merge": ("benchbox", "Merge"),
    "read_primitives": ("benchbox", "Primitives"),
    "joinorder": ("benchbox", "JoinOrder"),
}


def _import_benchmark_class(benchmark_name: str) -> Optional[type]:
    if benchmark_name not in BENCHMARK_CLASSES:
        return None

    module_name, class_name = BENCHMARK_CLASSES[benchmark_name]
    try:
        module = __import__(module_name, fromlist=[class_name])
        return getattr(module, class_name)
    except (ImportError, AttributeError):
        return None


def _create_mock_benchmark(benchmark_name: str, scale_factor: float, output_dir: Path) -> Mock:
    mock_benchmark = Mock()
    mock_benchmark.scale_factor = scale_factor
    mock_benchmark.output_dir = output_dir
    mock_benchmark.benchmark_name = benchmark_name
    mock_benchmark.get_queries = Mock(return_value={})
    mock_benchmark.get_query_categories = Mock(return_value=[])
    mock_benchmark.get_schema = Mock(return_value={})
    mock_benchmark.generate_data = Mock(return_value={})
    mock_benchmark.get_queries_by_category = Mock(return_value={})
    mock_benchmark.get_query = Mock(return_value="SELECT 1")
    return mock_benchmark


@pytest.fixture(params=["small", "medium"])
def scale_factor(request) -> float:
    return SCALE_FACTORS[request.param]


@pytest.fixture
def benchmark_factory(tmp_path: Path):

    def _create_benchmark(
        benchmark_name: str, scale_factor: float = 0.01, verbose: bool = False, **kwargs
    ) -> Union[object, Mock]:
        benchmark_class = _import_benchmark_class(benchmark_name)
        output_dir = tmp_path / f"{benchmark_name}_data_{scale_factor}"

        if benchmark_class is None:
            return _create_mock_benchmark(benchmark_name, scale_factor, output_dir)

        try:
            return benchmark_class(
                scale_factor=scale_factor,
                output_dir=output_dir,
                verbose=verbose,
                **kwargs,
            )
        except Exception:
            return _create_mock_benchmark(benchmark_name, scale_factor, output_dir)

    return _create_benchmark


@pytest.fixture
def tpch_benchmark(benchmark_factory) -> Generator[Union[TPCH, Mock], None, None]:
    yield benchmark_factory("tpch", scale_factor=1.0)


@pytest.fixture
def tpch_benchmark_medium(
    benchmark_factory,
) -> Generator[Union[TPCH, Mock], None, None]:
    yield benchmark_factory("tpch", scale_factor=1.0)


@pytest.fixture
def tpcds_benchmark(benchmark_factory) -> Generator[Union[TPCDS, Mock], None, None]:
    yield benchmark_factory("tpcds", scale_factor=1.0)


@pytest.fixture
def primitives_benchmark(
    benchmark_factory,
) -> Generator[Union[object, Mock], None, None]:
    yield benchmark_factory("read_primitives", scale_factor=1.0)


@pytest.fixture
def mock_benchmark_data_generation():
    tpch_mock_files = {
        "region": "/tmp/region.csv",
        "nation": "/tmp/nation.csv",
        "customer": "/tmp/customer.csv",
        "supplier": "/tmp/supplier.csv",
        "part": "/tmp/part.csv",
        "partsupp": "/tmp/partsupp.csv",
        "orders": "/tmp/orders.csv",
        "lineitem": "/tmp/lineitem.csv",
    }

    generic_mock_files = {
        "table1": "/tmp/table1.csv",
        "table2": "/tmp/table2.csv",
        "table3": "/tmp/table3.csv",
    }

    patches = {}
    mock_results = {"mock_files": {}}

    for benchmark_name, (_module_name, _class_name) in BENCHMARK_CLASSES.items():
        benchmark_class = _import_benchmark_class(benchmark_name)
        if benchmark_class is not None:
            try:
                mock_data = tpch_mock_files if benchmark_name in ["tpch", "tpcds"] else generic_mock_files

                patcher = patch.object(benchmark_class, "generate_data", return_value=mock_data)
                mock_gen = patcher.start()
                patches[f"{benchmark_name}_gen"] = mock_gen
                mock_results["mock_files"][benchmark_name] = mock_data
            except AttributeError:
                pass

    try:
        yield {**patches, **mock_results}
    finally:
        for benchmark_name in BENCHMARK_CLASSES:
            with contextlib.suppress(Exception):
                patch.stopall()


@pytest.fixture
def benchmark_mock_factory():

    def _create_mock(
        benchmark_name: str,
        scale_factor: float = 0.01,
        custom_queries: Optional[dict[str, str]] = None,
        custom_schema: Optional[dict[str, Any]] = None,
        **kwargs,
    ) -> Mock:
        mock_benchmark = Mock()

        mock_benchmark.benchmark_name = benchmark_name
        mock_benchmark.scale_factor = scale_factor
        mock_benchmark.verbose = False
        mock_benchmark.output_dir = Path(f"/tmp/{benchmark_name}_data")

        mock_benchmark.get_queries = Mock(
            return_value=custom_queries or {f"q{i}": f"SELECT {i} as test_query;" for i in range(1, 6)}
        )

        mock_benchmark.get_query_categories = Mock(return_value=["basic", "aggregation", "join", "window", "complex"])

        mock_benchmark.get_schema = Mock(
            return_value=custom_schema
            or {
                "test_table": {
                    "columns": ["id", "name", "value"],
                    "types": ["INTEGER", "VARCHAR(100)", "DECIMAL(10,2)"],
                }
            }
        )

        mock_benchmark.generate_data = Mock(return_value={"test_table": f"/tmp/{benchmark_name}_test_table.csv"})

        mock_benchmark.get_queries_by_category = Mock(
            return_value={
                "basic": ["q1", "q2"],
                "aggregation": ["q3"],
                "join": ["q4"],
                "complex": ["q5"],
            }
        )

        mock_benchmark.get_query = Mock(return_value="SELECT 1 as test;")

        for key, value in kwargs.items():
            setattr(mock_benchmark, key, value)

        return mock_benchmark

    return _create_mock


@pytest.fixture(params=list(BENCHMARK_CLASSES.keys()))
def any_benchmark(request, benchmark_factory):
    benchmark_name = request.param
    return benchmark_factory(benchmark_name, scale_factor=1.0)


@pytest.fixture(params=[("tpch", 1.0), ("tpcds", 1.0), ("tpch", 1.0)])
def benchmark_with_scale(request, benchmark_factory):
    benchmark_name, scale_factor = request.param
    benchmark = benchmark_factory(benchmark_name, scale_factor=scale_factor)
    return benchmark, benchmark_name, scale_factor


@pytest.fixture
def benchmark_performance_config() -> dict[str, Any]:
    return {
        "timeout_seconds": 30,
        "memory_limit_mb": 512,
        "max_concurrent_queries": 2,
        "enable_query_cache": False,
        "enable_result_cache": False,
        "collect_metrics": True,
        "profile_queries": False,
    }


@pytest.fixture
def benchmark_test_queries() -> dict[str, str]:
    return {
        "simple_select": "SELECT * FROM region LIMIT 10;",
        "aggregation": "SELECT COUNT(*) FROM lineitem;",
        "join": """
            SELECT r.r_name, n.n_name
            FROM region r
            JOIN nation n ON r.r_regionkey = n.n_regionkey
            LIMIT 10;
        """,
        "window_function": """
            SELECT
                l_orderkey,
                l_linenumber,
                ROW_NUMBER() OVER (PARTITION BY l_orderkey ORDER BY l_linenumber) as rn
            FROM lineitem
            LIMIT 10;
        """,
        "complex_aggregation": """
            SELECT
                l_returnflag,
                l_linestatus,
                COUNT(*) as count_order,
                SUM(l_quantity) as sum_qty,
                AVG(l_extendedprice) as avg_price
            FROM lineitem
            GROUP BY l_returnflag, l_linestatus
            ORDER BY l_returnflag, l_linestatus;
        """,
    }


@pytest.fixture
def benchmark_error_scenarios() -> dict[str, str]:
    return {
        "invalid_table": "SELECT * FROM nonexistent_table;",
        "invalid_column": "SELECT nonexistent_column FROM region;",
        "syntax_error": "SELECT * FORM region;",
        "division_by_zero": "SELECT 1/0;",
        "timeout_query": """
            WITH RECURSIVE t(n) AS (
                SELECT 1
                UNION ALL
                SELECT n+1 FROM t WHERE n < 1000000
            )
            SELECT COUNT(*) FROM t;
        """,
    }


@pytest.fixture
def benchmark_comparison_data() -> dict[str, Any]:
    return {
        "expected_row_counts": {
            "region": 5,
            "nation": 25,
            "customer": 150,
            "supplier": 10,
            "part": 200,
            "partsupp": 800,
            "orders": 1500,
            "lineitem": 6000,
        },
        "performance_thresholds": {
            "simple_select": 0.1,
            "aggregation": 1.0,
            "join": 2.0,
            "window_function": 3.0,
            "complex_aggregation": 5.0,
        },
        "memory_thresholds": {
            "simple_select": 10,
            "aggregation": 50,
            "join": 100,
            "window_function": 150,
            "complex_aggregation": 200,
        },
    }
