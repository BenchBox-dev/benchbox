import pytest

from benchbox.core.exceptions import ConfigurationError

from .common import (
    CloudSparkStubState,
    install_athena_spark_stub,
    install_dataproc_serverless_stub,
    install_dataproc_stub,
    install_emr_serverless_stub,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_athena_spark_requires_workgroup(monkeypatch):

    install_athena_spark_stub(monkeypatch)

    from benchbox.platforms.aws import AthenaSparkAdapter

    with pytest.raises(ConfigurationError) as excinfo:
        AthenaSparkAdapter(s3_staging_dir="s3://bucket/path")

    assert "workgroup" in str(excinfo.value).lower()


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_athena_spark_requires_s3_staging(monkeypatch):

    install_athena_spark_stub(monkeypatch)

    from benchbox.platforms.aws import AthenaSparkAdapter

    with pytest.raises(ConfigurationError) as excinfo:
        AthenaSparkAdapter(workgroup="spark-workgroup")

    assert "s3" in str(excinfo.value).lower()


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_athena_spark_validates_s3_path(monkeypatch):

    install_athena_spark_stub(monkeypatch)

    from benchbox.platforms.aws import AthenaSparkAdapter

    with pytest.raises(ConfigurationError) as excinfo:
        AthenaSparkAdapter(
            workgroup="spark-workgroup",
            s3_staging_dir="/invalid/path",
        )

    assert "s3://" in str(excinfo.value).lower()


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_athena_spark_platform_info(monkeypatch):

    state: CloudSparkStubState = install_athena_spark_stub(monkeypatch)

    from benchbox.platforms.aws import AthenaSparkAdapter

    adapter = AthenaSparkAdapter(
        workgroup="spark-workgroup",
        s3_staging_dir=f"s3://{state.bucket}/staging/",
        region=state.region,
    )

    info = adapter.get_platform_info()

    assert info["platform"] == "athena-spark"
    assert info["vendor"] == "AWS"
    assert info["region"] == state.region
    assert info["supports_sql"] is True
    assert info["supports_dataframe"] is True


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_athena_spark_configure_for_benchmark(monkeypatch):

    state: CloudSparkStubState = install_athena_spark_stub(monkeypatch)

    from benchbox.platforms.aws import AthenaSparkAdapter

    adapter = AthenaSparkAdapter(
        workgroup="spark-workgroup",
        s3_staging_dir=f"s3://{state.bucket}/staging/",
        region=state.region,
    )

    adapter.configure_for_benchmark(None, "tpch")
    assert adapter._benchmark_type == "tpch"
    assert adapter._spark_config is not None


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_emr_serverless_requires_s3_staging(monkeypatch):

    install_emr_serverless_stub(monkeypatch)

    from benchbox.platforms.aws import EMRServerlessAdapter

    with pytest.raises(ConfigurationError) as excinfo:
        EMRServerlessAdapter(
            application_id="app-12345",
            execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
        )

    assert "s3" in str(excinfo.value).lower()


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_emr_serverless_requires_execution_role(monkeypatch):

    install_emr_serverless_stub(monkeypatch)

    from benchbox.platforms.aws import EMRServerlessAdapter

    with pytest.raises(ConfigurationError) as excinfo:
        EMRServerlessAdapter(
            application_id="app-12345",
            s3_staging_dir="s3://bucket/path",
        )

    assert "execution_role" in str(excinfo.value).lower()


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_emr_serverless_requires_application_id_or_create(monkeypatch):

    install_emr_serverless_stub(monkeypatch)

    from benchbox.platforms.aws import EMRServerlessAdapter

    with pytest.raises(ConfigurationError) as excinfo:
        EMRServerlessAdapter(
            s3_staging_dir="s3://bucket/path",
            execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
        )

    assert "application" in str(excinfo.value).lower()


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_emr_serverless_platform_info(monkeypatch):

    state: CloudSparkStubState = install_emr_serverless_stub(monkeypatch)

    from benchbox.platforms.aws import EMRServerlessAdapter

    adapter = EMRServerlessAdapter(
        application_id=state.application_id,
        s3_staging_dir=f"s3://{state.bucket}/staging/",
        execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
        region=state.region,
    )

    info = adapter.get_platform_info()

    assert info["platform"] == "emr-serverless"
    assert info["vendor"] == "AWS"
    assert info["region"] == state.region
    assert info["application_id"] == state.application_id


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_emr_serverless_configure_for_benchmark(monkeypatch):

    state: CloudSparkStubState = install_emr_serverless_stub(monkeypatch)

    from benchbox.platforms.aws import EMRServerlessAdapter

    adapter = EMRServerlessAdapter(
        application_id=state.application_id,
        s3_staging_dir=f"s3://{state.bucket}/staging/",
        execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
        region=state.region,
    )

    adapter.configure_for_benchmark(None, "tpcds")
    assert adapter._benchmark_type == "tpcds"
    assert adapter._spark_config is not None


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_dataproc_requires_project_id(monkeypatch):

    install_dataproc_stub(monkeypatch)

    from benchbox.platforms.gcp import DataprocAdapter

    with pytest.raises(ConfigurationError) as excinfo:
        DataprocAdapter(gcs_staging_dir="gs://bucket/path")

    assert "project_id" in str(excinfo.value).lower()


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_dataproc_requires_gcs_staging(monkeypatch):

    install_dataproc_stub(monkeypatch)

    from benchbox.platforms.gcp import DataprocAdapter

    with pytest.raises(ConfigurationError) as excinfo:
        DataprocAdapter(project_id="smoke-project")

    assert "gcs" in str(excinfo.value).lower()


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_dataproc_validates_gcs_path(monkeypatch):

    install_dataproc_stub(monkeypatch)

    from benchbox.platforms.gcp import DataprocAdapter

    with pytest.raises(ConfigurationError) as excinfo:
        DataprocAdapter(
            project_id="smoke-project",
            gcs_staging_dir="/invalid/path",
        )

    assert "gs://" in str(excinfo.value).lower()


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_dataproc_platform_info(monkeypatch):

    state: CloudSparkStubState = install_dataproc_stub(monkeypatch)

    from benchbox.platforms.gcp import DataprocAdapter

    adapter = DataprocAdapter(
        project_id=state.project_id,
        region=state.region,
        gcs_staging_dir=f"gs://{state.bucket}/staging/",
    )

    info = adapter.get_platform_info()

    assert info["platform"] == "dataproc"
    assert info["vendor"] == "Google Cloud"
    assert info["project_id"] == state.project_id
    assert info["region"] == state.region


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_dataproc_configure_for_benchmark(monkeypatch):

    state: CloudSparkStubState = install_dataproc_stub(monkeypatch)

    from benchbox.platforms.gcp import DataprocAdapter

    adapter = DataprocAdapter(
        project_id=state.project_id,
        region=state.region,
        gcs_staging_dir=f"gs://{state.bucket}/staging/",
    )

    adapter.configure_for_benchmark(None, "ssb")
    assert adapter._benchmark_type == "ssb"
    assert adapter._spark_config is not None


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_dataproc_serverless_requires_project_id(monkeypatch):

    install_dataproc_serverless_stub(monkeypatch)

    from benchbox.platforms.gcp import DataprocServerlessAdapter

    with pytest.raises(ConfigurationError) as excinfo:
        DataprocServerlessAdapter(gcs_staging_dir="gs://bucket/path")

    assert "project_id" in str(excinfo.value).lower()


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_dataproc_serverless_requires_gcs_staging(monkeypatch):

    install_dataproc_serverless_stub(monkeypatch)

    from benchbox.platforms.gcp import DataprocServerlessAdapter

    with pytest.raises(ConfigurationError) as excinfo:
        DataprocServerlessAdapter(project_id="smoke-project")

    assert "gcs" in str(excinfo.value).lower()


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_dataproc_serverless_validates_gcs_path(monkeypatch):

    install_dataproc_serverless_stub(monkeypatch)

    from benchbox.platforms.gcp import DataprocServerlessAdapter

    with pytest.raises(ConfigurationError) as excinfo:
        DataprocServerlessAdapter(
            project_id="smoke-project",
            gcs_staging_dir="s3://wrong-cloud/path",
        )

    assert "gs://" in str(excinfo.value).lower()


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_dataproc_serverless_platform_info(monkeypatch):

    state: CloudSparkStubState = install_dataproc_serverless_stub(monkeypatch)

    from benchbox.platforms.gcp import DataprocServerlessAdapter

    adapter = DataprocServerlessAdapter(
        project_id=state.project_id,
        region=state.region,
        gcs_staging_dir=f"gs://{state.bucket}/staging/",
    )

    info = adapter.get_platform_info()

    assert info["platform"] == "dataproc-serverless"
    assert info["vendor"] == "Google Cloud"
    assert info["project_id"] == state.project_id
    assert info["region"] == state.region


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_dataproc_serverless_configure_for_benchmark(monkeypatch):

    state: CloudSparkStubState = install_dataproc_serverless_stub(monkeypatch)

    from benchbox.platforms.gcp import DataprocServerlessAdapter

    adapter = DataprocServerlessAdapter(
        project_id=state.project_id,
        region=state.region,
        gcs_staging_dir=f"gs://{state.bucket}/staging/",
    )

    adapter.configure_for_benchmark(None, "tpch")
    assert adapter._benchmark_type == "tpch"
    assert adapter._spark_config is not None


@pytest.mark.integration
@pytest.mark.platform_smoke
@pytest.mark.parametrize(
    "benchmark_type",
    [
        pytest.param("tpch", id="tpch"),
        pytest.param("tpcds", id="tpcds"),
        pytest.param("ssb", id="ssb"),
        pytest.param("unknown", id="unknown-defaults-to-tpch"),
    ],
)
def test_cloud_spark_config_mixin_benchmark_types(monkeypatch, benchmark_type):

    state: CloudSparkStubState = install_dataproc_serverless_stub(monkeypatch)

    from benchbox.platforms.gcp import DataprocServerlessAdapter

    adapter = DataprocServerlessAdapter(
        project_id=state.project_id,
        region=state.region,
        gcs_staging_dir=f"gs://{state.bucket}/staging/",
    )

    adapter.configure_for_benchmark(None, benchmark_type)
    assert adapter._benchmark_type == benchmark_type.lower()
    assert isinstance(adapter._spark_config, dict)
