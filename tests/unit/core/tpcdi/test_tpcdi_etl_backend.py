from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest

from benchbox.core.tpcdi.etl.backend import TPCDIETLBackend
from benchbox.core.tpcdi.etl.dataframe_backend import DataFrameETLBackend
from benchbox.core.tpcdi.etl.sql_backend import SQLETLBackend

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_sql_backend_loads_dataframes() -> None:
    connection = MagicMock()
    connection.executemany = MagicMock()
    connection.commit = MagicMock()

    backend = SQLETLBackend(
        connection=connection,
        create_tables_sql="CREATE TABLE DimCustomer(SK_CustomerID INT);",
        execute_validation_query=lambda _qid, _conn: [],
        validation_query_ids=[],
    )

    staged_data = {"DimCustomer": pd.DataFrame({"SK_CustomerID": [1, 2]})}
    result = backend.load_dataframes(staged_data, batch_type="historical")

    assert result["records_loaded"] == 2
    assert result["tables_updated"] == ["DimCustomer"]
    connection.executemany.assert_called_once()
    connection.commit.assert_called()


def test_sql_backend_validate_results_returns_expected_shape() -> None:
    connection = MagicMock()
    connection.execute.side_effect = RuntimeError("no tables in unit test")
    backend = SQLETLBackend(
        connection=connection,
        create_tables_sql="",
        execute_validation_query=lambda qid, _conn: [{"query": qid}],
        validation_query_ids=["V1", "V2"],
    )

    result = backend.validate_results()

    assert set(result) >= {
        "validation_queries",
        "data_quality_issues",
        "data_quality_score",
        "completeness_checks",
        "consistency_checks",
        "accuracy_checks",
    }
    assert result["validation_queries"]["V1"]["success"] is True
    assert result["validation_queries"]["V2"]["success"] is True


def test_dataframe_backend_delegates_insert_update() -> None:
    maintenance_ops = SimpleNamespace(
        insert_rows=lambda *_args, **_kwargs: SimpleNamespace(success=True, rows_affected=3, error_message=None),
        update_rows=lambda *_args, **_kwargs: SimpleNamespace(success=True, rows_affected=2, error_message=None),
    )
    backend = DataFrameETLBackend(maintenance_ops=maintenance_ops, platform_name="polars-df")

    staged_data = {"DimCustomer": pd.DataFrame({"SK_CustomerID": [1, 2, 3]})}
    load_result = backend.load_dataframes(staged_data, batch_type="historical")
    expire_result = backend.execute_scd2_expire("DimCustomer", "IsCurrent = TRUE", {"IsCurrent": False})
    insert_result = backend.execute_scd2_insert("DimCustomer", staged_data["DimCustomer"])

    assert load_result["records_loaded"] == 3
    assert load_result["tables_updated"] == ["DimCustomer"]
    assert expire_result["rows_affected"] == 2
    assert insert_result["rows_affected"] == 3


def test_backend_result_compatibility_keys() -> None:
    connection = MagicMock()
    connection.execute.side_effect = RuntimeError("mock")
    sql_backend = SQLETLBackend(
        connection=connection,
        create_tables_sql="",
        execute_validation_query=lambda _qid, _conn: [],
        validation_query_ids=[],
    )
    df_backend = DataFrameETLBackend(
        maintenance_ops=SimpleNamespace(
            insert_rows=lambda *_args, **_kwargs: SimpleNamespace(success=True, rows_affected=0, error_message=None),
            update_rows=lambda *_args, **_kwargs: SimpleNamespace(success=True, rows_affected=0, error_message=None),
        ),
        platform_name="polars-df",
    )

    sql_validation = sql_backend.validate_results()
    df_validation = df_backend.validate_results()
    expected_keys = {
        "validation_queries",
        "data_quality_issues",
        "data_quality_score",
        "completeness_checks",
        "consistency_checks",
        "accuracy_checks",
    }
    assert expected_keys.issubset(sql_validation.keys())
    assert expected_keys.issubset(df_validation.keys())


def test_sql_backend_rejects_unknown_table_name() -> None:
    connection = MagicMock()
    backend = SQLETLBackend(
        connection=connection,
        create_tables_sql="",
        execute_validation_query=lambda _qid, _conn: [],
        validation_query_ids=[],
    )

    with pytest.raises(ValueError, match="Unsupported table"):
        backend.load_dataframes({"NotATable": pd.DataFrame({"x": [1]})}, batch_type="historical")


def test_sql_backend_rejects_unsafe_condition_tokens() -> None:
    connection = MagicMock()
    cursor = MagicMock()
    cursor.rowcount = 1
    connection.cursor.return_value = cursor
    backend = SQLETLBackend(
        connection=connection,
        create_tables_sql="",
        execute_validation_query=lambda _qid, _conn: [],
        validation_query_ids=[],
    )

    with pytest.raises(ValueError, match="Unsafe SQL tokens"):
        backend.execute_scd2_expire(
            "DimCustomer",
            "IsCurrent = TRUE; DROP TABLE DimCustomer",
            {"IsCurrent": False},
        )


def test_sql_backend_parameterizes_dict_where_condition() -> None:
    connection = MagicMock()
    cursor = MagicMock()
    cursor.rowcount = 2
    connection.cursor.return_value = cursor
    backend = SQLETLBackend(
        connection=connection,
        create_tables_sql="",
        execute_validation_query=lambda _qid, _conn: [],
        validation_query_ids=[],
    )

    result = backend.execute_scd2_expire(
        "DimCustomer",
        {"SK_CustomerID": 42, "IsCurrent": True},
        {"IsCurrent": False},
    )

    assert result["rows_affected"] == 2
    assert cursor.execute.call_count == 1
    sql_text, values = cursor.execute.call_args[0]
    assert "UPDATE" in sql_text
    assert values == (False, 42, True)


def test_dataframe_backend_validation_explicitly_reports_not_executed() -> None:
    maintenance_ops = SimpleNamespace(
        insert_rows=lambda *_args, **_kwargs: SimpleNamespace(success=True, rows_affected=0, error_message=None),
        update_rows=lambda *_args, **_kwargs: SimpleNamespace(success=True, rows_affected=0, error_message=None),
    )
    backend = DataFrameETLBackend(maintenance_ops=maintenance_ops, platform_name="polars-df")

    validation = backend.validate_results()
    assert validation["validation_level"] == "not_executed"
    assert validation["data_quality_score"] == 0.0


def test_backends_satisfy_runtime_checkable_protocol() -> None:
    connection = MagicMock()
    sql_backend = SQLETLBackend(
        connection=connection,
        create_tables_sql="",
        execute_validation_query=lambda _qid, _conn: [],
        validation_query_ids=[],
    )
    df_backend = DataFrameETLBackend(
        maintenance_ops=SimpleNamespace(
            insert_rows=lambda *_args, **_kwargs: SimpleNamespace(success=True, rows_affected=0, error_message=None),
            update_rows=lambda *_args, **_kwargs: SimpleNamespace(success=True, rows_affected=0, error_message=None),
        ),
        platform_name="polars-df",
    )

    assert isinstance(sql_backend, TPCDIETLBackend)
    assert isinstance(df_backend, TPCDIETLBackend)


