import pytest

from benchbox.platforms.redshift import RedshiftAdapter

from .common import create_smoke_benchmark, install_redshift_stubs, run_smoke_benchmark

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_redshift_smoke_run(monkeypatch, tmp_path):
    state = install_redshift_stubs(monkeypatch)
    benchmark = create_smoke_benchmark(tmp_path)

    adapter = RedshiftAdapter(
        host=state.host,
        port=state.port,
        database="dev",
        username="benchbox",
        password="secret",
        s3_bucket="benchbox-smoke",
        s3_prefix="smoke",
        iam_role="arn:aws:iam::123456789012:role/BenchBox",
        aws_access_key_id="AKIAFAKE",
        aws_secret_access_key="secret",
    )

    stats, metadata, stub_state = run_smoke_benchmark(adapter, benchmark, tmp_path)

    assert stats["lineitem"] == stub_state.row_counts["LINEITEM"]
    assert stub_state.copies, "Expected COPY commands"
    assert stub_state.uploads, "Expected S3 uploads"

    assert stub_state.commits == 0, "Expected no explicit commits with autocommit enabled"
    assert metadata["platform_name"] == "Redshift"


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_redshift_requires_credentials(monkeypatch):
    install_redshift_stubs(monkeypatch)

    from benchbox.core.exceptions import ConfigurationError

    with pytest.raises(ConfigurationError) as excinfo:
        RedshiftAdapter(host="example")

    assert "Redshift configuration" in str(excinfo.value)
