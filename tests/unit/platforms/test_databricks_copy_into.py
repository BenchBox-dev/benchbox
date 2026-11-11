from pathlib import Path
from unittest.mock import patch

import pytest

from benchbox.platforms.databricks import DatabricksAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.cloud_import,
]


try:
    from databricks import sdk as databricks_sdk

    DATABRICKS_SDK_AVAILABLE = True
except ImportError:
    DATABRICKS_SDK_AVAILABLE = False


@pytest.fixture(autouse=True)
def databricks_dependencies():

    with (
        patch("benchbox.platforms.databricks.check_platform_dependencies", return_value=(True, [])),
        patch("databricks.sdk.WorkspaceClient"),
    ):
        yield


class DummyCursor:
    def __init__(self):
        self.executed = []
        self._fetch_queue = []
        self._fetchall_queue = []

    def execute(self, sql):
        self.executed.append(sql)

        if "SELECT COUNT(*)" in sql.upper():
            self._fetch_queue.append([42])

        elif "SHOW TABLES" in sql.upper():
            self._fetchall_queue.append(
                [
                    ("raw", "lineitem", False),
                    ("raw", "orders", False),
                ]
            )

    def fetchone(self):
        if self._fetch_queue:
            return self._fetch_queue.pop(0)
        return [0]

    def fetchall(self):
        if self._fetchall_queue:
            return self._fetchall_queue.pop(0)
        return []

    def close(self):
        pass


class DummyConnection:
    def __init__(self):
        self.cursor_obj = DummyCursor()

    def cursor(self):
        return self.cursor_obj

    def close(self):
        pass


@pytest.fixture
def adapter(monkeypatch):

    a = DatabricksAdapter(
        server_hostname="test.cloud.databricks.com",
        http_path="/sql/1.0/warehouses/xyz",
        access_token="token",
        catalog="workspace",
        schema="raw",
        staging_root="dbfs:/Volumes/workspace/raw/source/tpch_sf01",
    )
    return a


@pytest.mark.skipif(not DATABRICKS_SDK_AVAILABLE, reason="databricks-sdk not installed (required for UC volumes)")
def test_copy_into_from_uc_volume(adapter):

    class B:
        pass

    b = B()
    b.tables = {
        "lineitem": Path("lineitem.tbl"),
        "orders": Path("orders.csv"),
    }

    conn = DummyConnection()

    stats, _, _ = adapter.load_data(b, conn, Path("/ignored/local"))

    sqls = "\n".join(conn.cursor_obj.executed)

    assert "COPY INTO LINEITEM FROM 'dbfs:/Volumes/workspace/raw/source/tpch_sf01/lineitem.tbl'" in sqls
    assert "COPY INTO ORDERS FROM 'dbfs:/Volumes/workspace/raw/source/tpch_sf01/orders.csv'" in sqls
    assert "CREATE OR REPLACE TEMPORARY VIEW" not in sqls
    assert "INSERT INTO" not in sqls
    assert stats["lineitem"] == 42
    assert stats["orders"] == 42


def test_copy_into_requires_staging(adapter):

    adapter.staging_root = None
    adapter.uc_catalog = None
    adapter.uc_schema = None
    adapter.uc_volume = None

    class B:
        pass

    b = B()
    b.tables = {"lineitem": Path("lineitem.tbl")}

    conn = DummyConnection()
    with pytest.raises(ValueError):
        adapter.load_data(b, conn, Path("/local"))


