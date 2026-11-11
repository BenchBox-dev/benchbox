import pytest

from benchbox.core.validation.data import DataValidator, ValidationStatus

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class FakeCursor:
    def __init__(self, value=0):
        self.value = value
        self.queries = []

    def execute(self, sql):
        self.queries.append(sql)

    def fetchone(self):
        return (self.value,)


class FakeConn:
    def __init__(self, value=0):
        self._cursor = FakeCursor(value)

    def cursor(self):
        return self._cursor


def test_validate_row_counts_pass_with_warnings_only():

    class Adapter:
        platform_name = "duckdb"
        platform_config = {}

        def create_connection(self, **_):
            return FakeConn(0)

        def close_connection(self, _):
            return None

    dv = DataValidator(Adapter(), tolerance_percent=0.1, absolute_tolerance=100)

    def fake_table_count(conn, name):
        return {"ok": 1000, "warn": 980}[name]

    dv._get_table_row_count = fake_table_count

    res = dv.validate_row_counts({"ok": 1000, "warn": 900})
    assert res.is_valid is True
    assert res.passed_tables == 1
    assert res.warning_tables == 1

    status = {d.table_name: d.status for d in res.discrepancies}
    assert status["ok"] == ValidationStatus.PASSED
    assert status["warn"] == ValidationStatus.WARNING


def test_validate_row_counts_fail_with_exceeded_tolerance():
    class Adapter:
        platform_name = "duckdb"
        platform_config = {}

        def create_connection(self, **_):
            return FakeConn(0)

        def close_connection(self, _):
            return None

    dv = DataValidator(Adapter(), tolerance_percent=0.1, absolute_tolerance=100)

    def fake_table_count(conn, name):
        return {"bad": 1201}[name]

    dv._get_table_row_count = fake_table_count

    res = dv.validate_row_counts({"bad": 900})
    assert res.is_valid is False
    assert res.failed_tables == 1
    assert res.passed_tables == 0


def test_validate_row_counts_connection_error():
    class Adapter:
        platform_name = "duckdb"
        platform_config = {}

        def create_connection(self, **_):
            raise RuntimeError("boom")

        def close_connection(self, _):
            return None

    dv = DataValidator(Adapter())
    res = dv.validate_row_counts({"t": 1})
    assert res.is_valid is False
    assert any("Failed to retrieve actual row counts" in e for e in res.errors)


