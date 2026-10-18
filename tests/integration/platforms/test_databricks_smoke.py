import pytest

from benchbox.platforms.databricks import DatabricksAdapter

from .common import create_smoke_benchmark, install_databricks_stub, run_smoke_benchmark

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_databricks_smoke_run(monkeypatch, tmp_path):
    state = install_databricks_stub(monkeypatch)
    benchmark = create_smoke_benchmark(tmp_path)

    adapter = DatabricksAdapter(
        server_hostname="fake.databricks.local",
        http_path="/sql/test",
        access_token="token",
        catalog=state.catalog,
        schema=state.schema,
        staging_root="dbfs:/Volumes/main/benchbox/smoke",
        enable_delta_optimization=False,
    )

    stats, metadata, stub_state = run_smoke_benchmark(adapter, benchmark, tmp_path)

    assert stats["lineitem"] == stub_state.row_counts["LINEITEM"]
    assert any(stmt.startswith("COPY INTO LINEITEM") for stmt in stub_state.copy_statements)
    assert metadata["platform_name"] == "Databricks"


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_databricks_requires_credentials(monkeypatch):
    install_databricks_stub(monkeypatch)

    from benchbox.core.exceptions import ConfigurationError

    with pytest.raises(ConfigurationError) as excinfo:
        DatabricksAdapter(server_hostname="example", http_path="/warehouse")

    assert "Databricks configuration" in str(excinfo.value)
