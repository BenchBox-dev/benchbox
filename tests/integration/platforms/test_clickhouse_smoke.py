import sys

import pytest

from .common import ClickHouseStubState, install_clickhouse_stub

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.mark.integration
@pytest.mark.platform_smoke
@pytest.mark.skipif(sys.platform == "win32", reason="ClickHouse local mode (chDB) is not available on Windows")
def test_clickhouse_smoke_server_mode(monkeypatch, tmp_path):

    state: ClickHouseStubState = install_clickhouse_stub(monkeypatch)

    from benchbox.platforms.clickhouse import ClickHouseAdapter

    adapter = ClickHouseAdapter(
        deployment_mode="server",
        host=state.host,
        port=state.port,
        database="benchbox_smoke",
    )

    connection = adapter.create_connection()
    try:
        info = adapter.get_platform_info(connection)
        assert info["platform_type"] == "clickhouse"
        assert "ClickHouse" in info["platform_name"]
    finally:
        adapter.close_connection(connection)


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_clickhouse_requires_valid_mode(monkeypatch):

    install_clickhouse_stub(monkeypatch)

    from benchbox.platforms.clickhouse import ClickHouseAdapter

    with pytest.raises(ValueError) as excinfo:
        ClickHouseAdapter(deployment_mode="invalid_mode")

    assert "Invalid ClickHouse deployment mode" in str(excinfo.value)
