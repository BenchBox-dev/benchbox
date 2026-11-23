from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.execution_engine import (
    MIXED,
    NOT_CAPTURED,
    ExecutionEngineReceipt,
    ObservationNotRecognizedError,
    QueryObservationTarget,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

PLANS = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures" / "observed_engine" / "gluten_explain_plans.json").read_text(
        encoding="utf-8"
    )
)["plans"]


@pytest.fixture
def velox_adapter():
    with patch.dict(
        "sys.modules",
        {
            "pyspark": MagicMock(__version__="3.5.0"),
            "pyspark.sql": MagicMock(SparkSession=MagicMock()),
            "pyspark.sql.types": MagicMock(),
        },
    ):
        from benchbox.platforms.velox import VeloxAdapter

        yield VeloxAdapter(gluten_jar_path="/opt/gluten.jar")


def _spark_returning(plan_by_query: dict[str, str]) -> MagicMock:
    spark = MagicMock()
    spark.version = "3.5.0"

    def sql(statement: str):
        frame = MagicMock()
        query = statement.removeprefix("EXPLAIN EXTENDED ")
        frame.collect.return_value = [(plan_by_query.get(query, "== Physical Plan ==\nScan"),)]
        return frame

    spark.sql.side_effect = sql
    return spark


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("offloaded", "velox"),
        ("vanilla", "standard"),
        ("fallback", MIXED),
        ("vanilla_transition_under_native", MIXED),
        ("adaptive_with_offload", "velox"),
        ("adaptive_not_yet_planned", None),
        ("no_physical_section", None),
        ("empty_physical_section", None),
    ],
)
def test_plan_classification(name, expected):
    from benchbox.platforms.velox import classify_physical_plan

    assert classify_physical_plan(PLANS[name]) == expected


def test_each_timed_query_is_explained_not_the_synthetic_probe(velox_adapter):
    spark = _spark_returning({"select 1": PLANS["offloaded"], "select 2": PLANS["fallback"]})
    assert velox_adapter.observe_execution_engine(QueryObservationTarget(spark, "select 1", "q1")) == "velox"
    assert velox_adapter.observe_execution_engine(QueryObservationTarget(spark, "select 2", "q2")) == MIXED
    explained = [call.args[0] for call in spark.sql.call_args_list]
    assert explained == ["EXPLAIN EXTENDED select 1", "EXPLAIN EXTENDED select 2"]


def test_unrecognized_plan_is_reported_not_guessed(velox_adapter):
    spark = _spark_returning({"select 1": PLANS["no_physical_section"]})
    with pytest.raises(ObservationNotRecognizedError):
        velox_adapter.observe_execution_engine(QueryObservationTarget(spark, "select 1", "q1"))


def _platform_receipt(adapter, connection):
    return adapter.get_platform_info(connection)["configuration"]["execution_engine"]


def test_run_record_follows_the_timed_queries_even_when_the_probe_says_otherwise(velox_adapter):
    spark = _spark_returning({"select 1": PLANS["vanilla"], "select 2": PLANS["vanilla"]})
    probe_plan = "== Physical Plan ==\nVeloxColumnarToRowExec\n+- ^ ProjectExecTransformer"
    probing = MagicMock(wraps=spark)
    probing.version = "3.5.0"
    probing.sql.side_effect = lambda s: (
        spark.sql(s)
        if "range(10)" not in s
        else MagicMock(collect=MagicMock(return_value=[(probe_plan + " VeloxColumnar",)]))
    )
    velox_adapter.record_query_engine_observation(probing, "select 1", "q1")
    velox_adapter.record_query_engine_observation(probing, "select 2", "q2")

    info = velox_adapter.get_platform_info(probing)

    assert info["velox_active"] is True
    receipt = info["configuration"]["execution_engine"]
    assert (receipt["observed"], receipt["observed_source"]) == ("standard", "explain")


def test_run_record_is_velox_when_every_timed_query_is_offloaded(velox_adapter):
    spark = _spark_returning({"select 1": PLANS["offloaded"]})
    velox_adapter.record_query_engine_observation(spark, "select 1", "q1")
    receipt = _platform_receipt(velox_adapter, None)
    assert (receipt["observed"], receipt["observed_source"], receipt["observed_reason"]) == ("velox", "explain", None)


def test_run_record_is_mixed_when_queries_differ(velox_adapter):
    spark = _spark_returning({"select 1": PLANS["offloaded"], "select 2": PLANS["vanilla"]})
    velox_adapter.record_query_engine_observation(spark, "select 1", "q1")
    velox_adapter.record_query_engine_observation(spark, "select 2", "q2")
    assert _platform_receipt(velox_adapter, None)["observed"] == MIXED


def test_run_record_is_not_captured_when_a_timed_query_cannot_be_explained(velox_adapter):
    spark = MagicMock()
    spark.sql.side_effect = RuntimeError("explain refused")
    velox_adapter.record_query_engine_observation(spark, "select 1", "q1")
    receipt = _platform_receipt(velox_adapter, None)
    assert (receipt["observed"], receipt["observed_source"]) == (NOT_CAPTURED, "none")
    assert receipt["observed_reason"] == "the library reported no engine for 1 of 1 queries"


def test_run_record_is_not_captured_without_any_timed_query_and_never_from_the_probe(velox_adapter):
    spark = _spark_returning({})
    spark.sql.side_effect = lambda s: MagicMock(collect=MagicMock(return_value=[("VeloxColumnarToRow",)]))
    receipt = _platform_receipt(velox_adapter, spark)
    assert (receipt["observed"], receipt["observed_reason"]) == (NOT_CAPTURED, "no query completed")


def test_the_receipt_round_trips(velox_adapter):
    receipt = _platform_receipt(velox_adapter, None)
    assert asdict(ExecutionEngineReceipt(**receipt)) == receipt


def test_timed_query_execution_records_its_own_plan(velox_adapter):
    spark = _spark_returning({"select 1": PLANS["offloaded"]})
    spark.catalog = MagicMock()
    result_df = MagicMock()
    result_df.collect.return_value = [(1,)]
    result_df.schema = MagicMock()
    execute = spark.sql.side_effect
    spark.sql.side_effect = lambda statement: execute(statement) if statement.startswith("EXPLAIN") else result_df
    velox_adapter.disable_cache = False

    result = velox_adapter.execute_query(spark, "select 1", "q1")

    assert result["status"] == "SUCCESS"
    assert _platform_receipt(velox_adapter, None)["observed"] == "velox"
