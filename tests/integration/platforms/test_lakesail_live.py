from __future__ import annotations

import os

import pytest

LAKESAIL_ENDPOINT = os.environ.get("LAKESAIL_ENDPOINT")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_integration,
    pytest.mark.live_lakesail,
    pytest.mark.skipif(
        not LAKESAIL_ENDPOINT,
        reason="Skipping LakeSail live test: LAKESAIL_ENDPOINT not set",
    ),
]


try:
    from benchbox.platforms.pyspark import PYSPARK_AVAILABLE
except ImportError:
    PYSPARK_AVAILABLE = False


@pytest.mark.skipif(not PYSPARK_AVAILABLE, reason="PySpark not installed")
class TestLakeSailSQLSmoke:
    @pytest.fixture
    def adapter(self):
        from benchbox.platforms.lakesail import LakeSailAdapter

        adapter = LakeSailAdapter(
            endpoint=LAKESAIL_ENDPOINT,
            database="benchbox_lakesail_smoke",
        )
        yield adapter
        try:
            if adapter._spark_session:
                adapter.close_connection(adapter._spark_session)
        except Exception:
            pass

    def test_connection(self, adapter):

        assert adapter.test_connection() is True

    def test_select_one(self, adapter):
        connection = adapter.create_connection()
        result = adapter.execute_query(connection, "SELECT 1 AS value", query_id="Q0", benchmark_type="tpch")
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 1
        adapter.close_connection(connection)

    def test_platform_info(self, adapter):

        connection = adapter.create_connection()
        info = adapter.get_platform_info(connection=connection)
        assert info["platform_type"] == "lakesail"
        assert info["platform_name"] == "LakeSail Sail"
        adapter.close_connection(connection)


@pytest.mark.skipif(not PYSPARK_AVAILABLE, reason="PySpark not installed")
class TestLakeSailDataFrameSmoke:
    @pytest.fixture
    def adapter(self):
        from benchbox.platforms.dataframe.lakesail_df import LakeSailDataFrameAdapter

        adapter = LakeSailDataFrameAdapter(
            endpoint=LAKESAIL_ENDPOINT,
        )
        yield adapter
        adapter.close()

    def test_platform_info(self, adapter):

        info = adapter.get_platform_info()
        assert info["platform"] == "LakeSail"
        assert info["family"] == "expression"

    def test_create_context(self, adapter):

        ctx = adapter.create_context()
        assert ctx is not None
        assert ctx.platform == "LakeSail"


