# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
from types import SimpleNamespace

import pytest

from benchbox.cli.database import DatabaseManager

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _CapturingHandler(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def captured_debug_records():
    logger = logging.getLogger("benchbox.cli.database")
    handler = _CapturingHandler()
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


class TestCreateConfigDebugLogging:
    def test_platform_options_extra_is_sanitized(self, captured_debug_records):
        DatabaseManager().create_config("duckdb", platform_options={"motherduck_token": "TOK-SENTINEL"})
        building = [r for r in captured_debug_records if r.getMessage() == "Building database config"]
        assert building, "the Building database config record must still be emitted"
        options = building[0].platform_options
        assert "motherduck_token" in options, "option KEYS must stay visible for debugging"
        assert options["motherduck_token"] != "TOK-SENTINEL"

    def test_built_config_extra_is_sanitized(self, captured_debug_records):
        DatabaseManager().create_config("duckdb", platform_options={"motherduck_token": "TOK-SENTINEL"})
        built = [r for r in captured_debug_records if r.getMessage() == "Database configuration built"]
        assert built, "the Database configuration built record must still be emitted"
        record = built[0]
        assert "TOK-SENTINEL" not in str(record.config)
        assert "TOK-SENTINEL" not in str(record.options)
        assert "motherduck_token" in record.options

    def test_non_secret_options_keep_real_values(self, captured_debug_records):
        DatabaseManager().create_config("duckdb", platform_options={"memory_limit": "4GB"})
        building = [r for r in captured_debug_records if r.getMessage() == "Building database config"]
        assert building[0].platform_options["memory_limit"] == "4GB"

    def test_credential_bearing_dsn_is_sanitized(self, captured_debug_records, monkeypatch):
        dsn = "databend+http://user:DSN-SENTINEL@host/database"
        monkeypatch.setattr(
            "benchbox.cli.database.ensure_driver_version",
            lambda **_: SimpleNamespace(
                requested=None,
                resolved="test",
                actual="test",
                runtime_strategy="current-process",
                runtime_path=None,
                runtime_python_executable=None,
                auto_install_used=False,
            ),
        )

        DatabaseManager().create_config("databend", platform_options={"dsn": dsn})

        matching = [
            r
            for r in captured_debug_records
            if r.getMessage() in {"Building database config", "Database configuration built"}
        ]
        assert matching
        assert all("DSN-SENTINEL" not in str(record.__dict__) for record in matching)


def test_available_databases_is_detected_on_first_access_and_cached(monkeypatch):
    from benchbox.core.platform_registry import PlatformRegistry

    manager = DatabaseManager()
    assert "available_databases" not in vars(manager)

    monkeypatch.setattr(PlatformRegistry, "get_available_platforms", lambda: ["duckdb", "clickhouse"])
    detected = manager.available_databases

    assert manager.available_databases is detected
    duckdb_info = detected["duckdb"]
    assert set(duckdb_info) >= {"name", "version", "description", "adoption", "supports_olap"}
    assert duckdb_info["name"]
    if "clickhouse" in detected:
        assert set(detected["clickhouse"]) >= {"supports_server", "supports_embedded"}
