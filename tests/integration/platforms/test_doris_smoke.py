import pytest

from .common import create_smoke_benchmark, install_doris_stub, run_smoke_benchmark

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_doris_smoke_basic(monkeypatch, tmp_path):

    state = install_doris_stub(monkeypatch)

    from benchbox.platforms.doris import DorisAdapter

    adapter = DorisAdapter(
        host=state.host,
        port=state.port,
        database="benchbox_smoke",
    )

    connection = adapter.create_connection()
    try:
        info = adapter.get_platform_info(connection)
        assert info["platform_type"] == "doris"
        assert info["platform_name"] == "Apache Doris"
    finally:
        adapter.close_connection(connection)


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_doris_smoke_schema_creation(monkeypatch, tmp_path):

    state = install_doris_stub(monkeypatch)

    from benchbox.platforms.doris import DorisAdapter

    adapter = DorisAdapter(
        host=state.host,
        port=state.port,
        database="benchbox_smoke",
    )

    benchmark = create_smoke_benchmark(tmp_path)
    connection = adapter.create_connection()
    try:
        schema_time = adapter.create_schema(benchmark, connection)
        assert isinstance(schema_time, float)
        assert schema_time >= 0

        ddl_statements = [s for s in state.statements if "CREATE" in s.upper()]
        assert len(ddl_statements) > 0, "Expected CREATE statements to be executed"
    finally:
        adapter.close_connection(connection)


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_doris_smoke_data_loading(monkeypatch, tmp_path):

    state = install_doris_stub(monkeypatch)

    from benchbox.platforms.doris import DorisAdapter

    adapter = DorisAdapter(
        host=state.host,
        port=state.port,
        database="benchbox_smoke",
    )

    benchmark = create_smoke_benchmark(tmp_path)
    connection = adapter.create_connection()
    try:
        adapter.create_schema(benchmark, connection)
        table_stats, load_time, _ = adapter.load_data(benchmark, connection, tmp_path)

        assert isinstance(load_time, float)
        assert load_time >= 0
        assert "lineitem" in table_stats
        assert table_stats["lineitem"] == 2

        assert len(state.inserts) > 0, "Expected INSERT statements to be executed"
    finally:
        adapter.close_connection(connection)


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_doris_smoke_query_execution(monkeypatch, tmp_path):

    state = install_doris_stub(monkeypatch)

    from benchbox.platforms.doris import DorisAdapter

    adapter = DorisAdapter(
        host=state.host,
        port=state.port,
        database="benchbox_smoke",
    )

    connection = adapter.create_connection()
    try:
        result = adapter.execute_query(
            connection,
            "SELECT COUNT(*) FROM lineitem",
            "q1",
        )
        assert result["status"] == "SUCCESS"
        assert isinstance(result["execution_time_seconds"], float)
        assert result["execution_time_seconds"] >= 0
    finally:
        adapter.close_connection(connection)


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_doris_smoke_full_workflow(monkeypatch, tmp_path):

    state = install_doris_stub(monkeypatch)

    from benchbox.platforms.doris import DorisAdapter

    adapter = DorisAdapter(
        host=state.host,
        port=state.port,
        database="benchbox_smoke",
    )

    benchmark = create_smoke_benchmark(tmp_path)
    table_stats, metadata, _ = run_smoke_benchmark(adapter, benchmark, tmp_path)

    assert metadata["platform_type"] == "doris"
    assert metadata["platform_name"] == "Apache Doris"

    assert len(state.statements) > 0


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_doris_smoke_cleanup(monkeypatch, tmp_path):

    state = install_doris_stub(monkeypatch)

    from benchbox.platforms.doris import DorisAdapter

    adapter = DorisAdapter(
        host=state.host,
        port=state.port,
        database="benchbox_smoke",
    )

    connection = adapter.create_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("DROP TABLE IF EXISTS `lineitem`")
        cursor.close()
        drop_statements = [s for s in state.statements if "DROP" in s.upper()]
        assert len(drop_statements) > 0, "Expected DROP statement to be executed"
    finally:
        adapter.close_connection(connection)


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_doris_smoke_config_validation(monkeypatch):

    install_doris_stub(monkeypatch)

    from benchbox.platforms.doris import DorisAdapter

    adapter = DorisAdapter(
        host="localhost",
        port=9030,
        database="benchbox_smoke",
    )
    assert adapter.database == "benchbox_smoke"

    with pytest.raises(ValueError, match="Invalid database identifier"):
        DorisAdapter(
            host="localhost",
            port=9030,
            database="'; DROP TABLE --",
        )
