import pytest

from .common import PrestoStubState, install_presto_stub

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_presto_smoke_run(monkeypatch, tmp_path):

    state: PrestoStubState = install_presto_stub(monkeypatch)

    from benchbox.platforms.presto import PrestoAdapter

    adapter = PrestoAdapter(
        host=state.host,
        port=state.port,
        catalog=state.catalog,
        schema=state.schema,
        username="presto",
    )

    connection = adapter.create_connection()
    try:
        info = adapter.get_platform_info(connection)
        assert info["platform_type"] == "presto"
        assert info["platform_name"] == "PrestoDB"
    finally:
        adapter.close_connection(connection)


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_presto_requires_catalog(monkeypatch):

    install_presto_stub(monkeypatch)

    from benchbox.platforms.presto import PrestoAdapter

    adapter = PrestoAdapter(
        host="localhost",
        port=8080,
        username="presto",
    )

    assert adapter.platform_name == "Presto"
