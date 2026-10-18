import pytest

from benchbox.platforms.snowflake import SnowflakeAdapter

from .common import create_smoke_benchmark, install_snowflake_stub, run_smoke_benchmark

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_snowflake_smoke_run(monkeypatch, tmp_path):
    install_snowflake_stub(monkeypatch)
    benchmark = create_smoke_benchmark(tmp_path)

    adapter = SnowflakeAdapter(
        account="benchbox",
        username="benchbox_user",
        password="secret",
        warehouse="COMPUTE_WH",
        database="BENCHBOX",
        schema="PUBLIC",
    )

    stats, metadata, stub_state = run_smoke_benchmark(adapter, benchmark, tmp_path)

    assert stats["LINEITEM"] == stub_state.row_counts["LINEITEM"]
    assert stub_state.put_commands, "Expected PUT uploads"
    assert stub_state.copy_commands, "Expected COPY INTO commands"
    assert metadata["platform_name"] == "Snowflake"


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_snowflake_requires_core_settings(monkeypatch):
    install_snowflake_stub(monkeypatch)

    from benchbox.core.exceptions import ConfigurationError

    with pytest.raises(ConfigurationError) as excinfo:
        SnowflakeAdapter(account="benchbox")

    assert "Snowflake configuration" in str(excinfo.value)
