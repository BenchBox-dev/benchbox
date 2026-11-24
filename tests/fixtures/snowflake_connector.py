from __future__ import annotations

import dataclasses

import pytest


def installed_connector_major() -> int:
    connector = pytest.importorskip("snowflake.connector")
    return int(connector.__version__.split(".", 1)[0])


def installed_connector_parameters() -> set[str]:
    connection = pytest.importorskip("snowflake.connector.connection")
    if connection.DEFAULT_CONFIGURATION:
        return set(connection.DEFAULT_CONFIGURATION)
    config = pytest.importorskip("snowflake.connector.connection_config")
    return {field.name for field in dataclasses.fields(config.ConnectionConfig)}
