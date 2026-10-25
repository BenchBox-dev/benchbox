# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _provide_fake_duckdb(_hermetic_state, monkeypatch):
    from benchbox.platforms import duckdb as duckdb_module

    if getattr(duckdb_module, "duckdb", None) is not None:
        yield
        return

    class _FakeCursor:
        def __init__(self):
            self.statements: list[str] = []

        def execute(self, sql: str):
            self.statements.append(sql)
            return self

        def fetchall(self):
            return []

        def fetchone(self):
            return None

        def fetchmany(self, size=None):
            return []

        def close(self):
            return None

    class _FakeDuckDBModule:
        __version__ = "0.0-test"

        @staticmethod
        def connect(_database_path: str):
            return _FakeCursor()

    monkeypatch.setattr(duckdb_module, "duckdb", _FakeDuckDBModule(), raising=False)
    yield


@pytest.fixture
def duckdb_memory_db():
    import duckdb

    conn = duckdb.connect(":memory:")
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def mock_platform_dependency_checks(_hermetic_state):

    targets = [
        "benchbox.platforms.bigquery.check_platform_dependencies",
        "benchbox.platforms.databricks.adapter.check_platform_dependencies",
        "benchbox.platforms.clickhouse.check_platform_dependencies",
        "benchbox.platforms.snowflake.check_platform_dependencies",
        "benchbox.platforms.redshift.check_platform_dependencies",
    ]

    with ExitStack() as stack:
        for target in targets:
            try:
                stack.enter_context(patch(target, return_value=(True, [])))
            except AttributeError:
                pass

        try:
            from benchbox.platforms.snowflake import SnowflakeAdapter

            real_add_cli_arguments = SnowflakeAdapter.__dict__["add_cli_arguments"]

            def _stub_add_cli_arguments(parser) -> None:
                return None

            _stub_add_cli_arguments.real = getattr(
                real_add_cli_arguments.__func__, "real", real_add_cli_arguments.__func__
            )
            stack.enter_context(
                patch.object(SnowflakeAdapter, "add_cli_arguments", staticmethod(_stub_add_cli_arguments))
            )
        except ImportError:
            pass

        yield
