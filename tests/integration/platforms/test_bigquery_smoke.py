import pytest

from .common import (
    BigQueryStubState,
    create_smoke_benchmark,
    install_google_cloud_stubs,
    run_smoke_benchmark,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_bigquery_smoke_run(monkeypatch, tmp_path):
    state: BigQueryStubState = install_google_cloud_stubs(monkeypatch)
    from benchbox.platforms.bigquery import BigQueryAdapter

    benchmark = create_smoke_benchmark(tmp_path, suffix=".csv")

    adapter = BigQueryAdapter(
        project_id=state.project_id,
        dataset_id="benchbox_smoke",
        location=state.location,
        storage_bucket="benchbox-smoke",
    )

    stats, metadata, stub_state = run_smoke_benchmark(adapter, benchmark, tmp_path)

    assert stats["LINEITEM"] == stub_state.row_counts["LINEITEM"]
    assert stub_state.datasets.get("benchbox_smoke")
    assert stub_state.loads, "Expected load jobs to be recorded"
    assert stub_state.uploads, "Expected uploads to cloud storage"
    assert metadata["platform_name"] == "BigQuery"


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_bigquery_requires_project(monkeypatch):
    install_google_cloud_stubs(monkeypatch)
    from benchbox.core.exceptions import ConfigurationError
    from benchbox.platforms.bigquery import BigQueryAdapter

    with pytest.raises(ConfigurationError) as excinfo:
        BigQueryAdapter(project_id=None)

    assert "project_id" in str(excinfo.value)
