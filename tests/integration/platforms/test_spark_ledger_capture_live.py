from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from benchbox.core.tuning.applied_ledger import (
    EXECUTED,
    NOOP,
    PHASE_DDL,
    AppliedTuningLedger,
    is_schema_tuning_statement,
    recording_connection,
)

from .delta_live_helpers import delta_live_skip_reason, make_delta_spark_session

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_integration,
    pytest.mark.live_delta,
    pytest.mark.skipif(
        delta_live_skip_reason() is not None,
        reason=delta_live_skip_reason() or "PySpark + Delta Lake runtime unavailable",
    ),
]


@pytest.fixture(scope="module")
def spark(tmp_path_factory, pyspark_test_environment):
    session = make_delta_spark_session(tmp_path_factory.mktemp("ledger_capture_warehouse"), app_name="ledger-capture")
    session.sql("CREATE DATABASE IF NOT EXISTS ledger_capture")
    session.sql("USE ledger_capture")
    yield session
    session.stop()


def test_partitioned_and_clustered_ddl_is_recorded_and_status_is_not_noop(spark) -> None:
    from benchbox.platforms.spark import SparkAdapter

    ledger = AppliedTuningLedger()
    assert ledger.overall_status(tuning_enabled=True, has_config=True) == NOOP
    connection = recording_connection(
        spark,
        ledger,
        PHASE_DDL,
        statement_filter=is_schema_tuning_statement,
        execute_verbs=SparkAdapter.ledger_execute_verbs,
    )
    adapter = SimpleNamespace(
        _create_schema_with_tuning=lambda *args, **kwargs: (
            "CREATE TABLE orders (o_id INT, o_date DATE)\nPARTITIONED BY (o_date);\n"
            "CREATE TABLE lineitem (l_id INT, l_key INT)\nCLUSTER BY (l_key);\n"
            "CREATE TABLE plain (p_id INT);"
        ),
        table_format="delta",
        logger=logging.getLogger(__name__),
        _remove_orphaned_table_location=lambda *args, **kwargs: None,
    )

    SparkAdapter.create_schema(adapter, SimpleNamespace(), connection)

    recorded = [(s.phase, s.status, s.statement) for s in ledger.statements]
    assert [(phase, status) for phase, status, _ in recorded] == [(PHASE_DDL, EXECUTED)] * 2
    assert "PARTITIONED BY (o_date)" in recorded[0][2]
    assert "CLUSTER BY (l_key)" in recorded[1][2]
    assert ledger.overall_status(tuning_enabled=True, has_config=True) != NOOP
    assert spark.sql("SELECT COUNT(*) FROM plain").collect()[0][0] == 0
    assert {table.name for table in spark.catalog.listTables()} >= {"orders", "lineitem", "plain"}
