import pytest

from .common import StarRocksStubState, create_smoke_benchmark, install_starrocks_stub, run_smoke_benchmark

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.resource_heavy,
    pytest.mark.platform_smoke,
]


def test_starrocks_smoke_full_workflow(monkeypatch, tmp_path):

    state: StarRocksStubState = install_starrocks_stub(monkeypatch)

    from benchbox.platforms.starrocks import StarRocksAdapter

    adapter = StarRocksAdapter(
        host=state.host,
        port=state.port,
        http_port=state.http_port,
        database="benchbox_smoke",
    )

    benchmark = create_smoke_benchmark(tmp_path)
    table_stats, metadata, _ = run_smoke_benchmark(adapter, benchmark, tmp_path)

    assert metadata["platform_type"] == "starrocks"
    assert len(state.statements) > 0

    assert len(state.stream_loads) == 1
    stream_load = state.stream_loads[0]
    assert stream_load.url == f"http://{state.host}:{state.http_port}/api/benchbox_smoke/lineitem/_stream_load"
    assert stream_load.body == b"1,alpha\n2,beta\n"
    assert table_stats["lineitem"] == stream_load.rows == 2
