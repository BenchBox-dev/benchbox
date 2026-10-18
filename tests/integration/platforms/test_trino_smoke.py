import pytest

from .common import TrinoStubState, install_trino_stub

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_trino_smoke_run(monkeypatch, tmp_path):

    state: TrinoStubState = install_trino_stub(monkeypatch)

    from benchbox.platforms.trino import TrinoAdapter

    adapter = TrinoAdapter(
        host=state.host,
        port=state.port,
        catalog=state.catalog,
        schema=state.schema,
        username="trino",
    )

    connection = adapter.create_connection()
    try:
        info = adapter.get_platform_info(connection)
        assert info["platform_type"] == "trino"
        assert info["platform_name"] == "Trino"
    finally:
        adapter.close_connection(connection)


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_trino_requires_catalog(monkeypatch):

    install_trino_stub(monkeypatch)

    from benchbox.platforms.trino import TrinoAdapter

    adapter = TrinoAdapter(
        host="localhost",
        port=8080,
        username="trino",
    )

    assert adapter.platform_name == "Trino"
