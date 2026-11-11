import pytest

from .common import (
    AthenaStubState,
    create_smoke_benchmark,
    install_athena_stubs,
    run_smoke_benchmark,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_athena_smoke_run(monkeypatch, tmp_path):

    state: AthenaStubState = install_athena_stubs(monkeypatch)

    from benchbox.platforms.athena import AthenaAdapter

    benchmark = create_smoke_benchmark(tmp_path)

    adapter = AthenaAdapter(
        region=state.region,
        workgroup=state.workgroup,
        database="benchbox_smoke",
        s3_bucket=state.s3_bucket,
        s3_staging_dir=f"s3://{state.s3_bucket}/staging/",
        s3_output_location=f"s3://{state.s3_bucket}/results/",
    )

    stats, metadata, stub_state = run_smoke_benchmark(adapter, benchmark, tmp_path)

    assert stats["lineitem"] == stub_state.row_counts["LINEITEM"]
    assert stub_state.uploads, "Expected S3 uploads"
    assert stub_state.tables_created, "Expected tables to be created"
    assert metadata["platform_name"] == "AWS Athena"


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_athena_requires_s3_bucket(monkeypatch):

    install_athena_stubs(monkeypatch)

    from benchbox.core.exceptions import ConfigurationError
    from benchbox.platforms.athena import AthenaAdapter

    with pytest.raises(ConfigurationError) as excinfo:
        AthenaAdapter(region="us-east-1")

    assert "S3 location" in str(excinfo.value) or "s3" in str(excinfo.value).lower()


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_athena_platform_info(monkeypatch, tmp_path):

    state: AthenaStubState = install_athena_stubs(monkeypatch)

    from benchbox.platforms.athena import AthenaAdapter

    adapter = AthenaAdapter(
        region=state.region,
        workgroup=state.workgroup,
        database="benchbox_test",
        s3_bucket=state.s3_bucket,
        s3_staging_dir=f"s3://{state.s3_bucket}/staging/",
        s3_output_location=f"s3://{state.s3_bucket}/results/",
    )

    connection = adapter.create_connection()
    try:
        info = adapter.get_platform_info(connection)
        assert info["platform_type"] == "athena"
        assert info["platform_name"] == "AWS Athena"
        assert info["configuration"]["region"] == state.region
        assert info["configuration"]["workgroup"] == state.workgroup
    finally:
        adapter.close_connection(connection)


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_athena_cost_tracking(monkeypatch, tmp_path):

    state: AthenaStubState = install_athena_stubs(monkeypatch)

    from benchbox.platforms.athena import AthenaAdapter

    adapter = AthenaAdapter(
        region=state.region,
        workgroup=state.workgroup,
        database="benchbox_cost_test",
        s3_bucket=state.s3_bucket,
        s3_staging_dir=f"s3://{state.s3_bucket}/staging/",
        s3_output_location=f"s3://{state.s3_bucket}/results/",
    )

    connection = adapter.create_connection()
    try:
        result = adapter.execute_query(
            connection,
            "SELECT 1",
            query_id="Q1",
            benchmark_type=None,
            validate_row_count=False,
        )

        assert result["status"] == "SUCCESS"
        assert "data_scanned_bytes" in result
        assert "cost" in result

        cost_summary = adapter.get_cost_summary()
        assert cost_summary["query_count"] >= 1
        assert "total_cost_usd" in cost_summary
    finally:
        adapter.close_connection(connection)


