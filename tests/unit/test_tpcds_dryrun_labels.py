from unittest.mock import Mock

import pytest

from benchbox.cli.dryrun import DryRunExecutor

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _mk_benchmark_mock():
    b = Mock()
    b._name = "TPC-DS"
    return b


def test_power_test_returns_labeled_keys():
    executor = DryRunExecutor()
    benchmark = _mk_benchmark_mock()

    benchmark.get_queries.return_value = {
        "Position_1_Query_1": "SELECT 1",
        "Position_2_Query_2": "SELECT 2",
    }
    cfg = Mock()
    cfg.name = "tpcds"
    cfg.scale_factor = 0.01

    out = executor._extract_queries_via_real_test_execution(benchmark, cfg, "power")

    assert set(out.keys()) == {"Position_1_Query_1", "Position_2_Query_2"}
    assert "SELECT 1" in out["Position_1_Query_1"]


def test_throughput_test_returns_labeled_keys():
    executor = DryRunExecutor()
    benchmark = _mk_benchmark_mock()

    benchmark.get_queries.return_value = {
        "Stream_0_Position_1_Query_7": "SELECT 7",
        "Stream_1_Position_3_Query_21": "SELECT 21",
    }
    cfg = Mock()
    cfg.name = "tpcds"
    cfg.scale_factor = 0.01

    out = executor._extract_queries_via_real_test_execution(benchmark, cfg, "throughput")

    assert any(k.startswith("Stream_") for k in out)
    assert "Stream_0_Position_1_Query_7" in out


def test_maintenance_test_returns_labeled_keys():
    executor = DryRunExecutor()
    benchmark = _mk_benchmark_mock()

    benchmark.get_queries.return_value = {
        "Op_1_CUSTOMER_INSERT": "INSERT INTO customer SELECT * FROM customer WHERE 1=0",
        "Op_2_WEB_SALES_UPDATE": "UPDATE web_sales SET web_sales_ts = web_sales_ts",
        "Op_3_STORE_RETURNS_DELETE": "DELETE FROM store_returns WHERE 1=0",
    }
    cfg = Mock()
    cfg.name = "tpcds"
    cfg.scale_factor = 0.01

    out = executor._extract_queries_via_real_test_execution(benchmark, cfg, "maintenance")

    assert set(out.keys()) == {
        "Op_1_CUSTOMER_INSERT",
        "Op_2_WEB_SALES_UPDATE",
        "Op_3_STORE_RETURNS_DELETE",
    }
    assert out["Op_1_CUSTOMER_INSERT"].upper().startswith("INSERT INTO CUSTOMER")


