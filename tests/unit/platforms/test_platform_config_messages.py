from __future__ import annotations

import os
import re
from unittest.mock import MagicMock, patch

import pytest

import benchbox.platforms
from benchbox.core.exceptions import ConfigurationError
from benchbox.core.hooks.platform_hooks import PlatformHookRegistry

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

OPTION_PATTERN = re.compile(r"--platform-option (\w+)=")
CLI_FLAG_PATTERN = re.compile(r"(?<![\w-])--[a-z][a-z-]+")


def named_options(message: str) -> list[str]:
    return OPTION_PATTERN.findall(message)


def named_variables(message: str, prefix: str) -> list[str]:
    return sorted(set(re.findall(rf"\b{prefix}[A-Z0-9_]*[A-Z0-9]\b", message)))


def printed_text(console: MagicMock) -> str:
    return " ".join(str(call.args[0]) for call in console.print.call_args_list if call.args)


@pytest.fixture
def clean_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith(
            ("CLICKHOUSE_CLOUD_", "SNOWFLAKE_", "REDSHIFT_", "BIGQUERY_", "ATHENA_", "MOTHERDUCK_", "GOOGLE_")
        ):
            monkeypatch.delenv(name, raising=False)
    return monkeypatch


class TestClickHouseCloudAuthenticationMessage:
    @pytest.fixture
    def message(self, clean_environment):
        from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter

        with pytest.raises(ValueError) as excinfo:
            ClickHouseCloudAdapter(host="abc123.us-east-2.aws.clickhouse.cloud")
        return str(excinfo.value)

    def test_names_no_unregistered_flag(self, message):
        assert "--clickhouse-cloud-password" not in message
        assert "--clickhouse-cloud-oauth-token" not in message

    def test_named_platform_options_are_accepted(self, message):
        from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter

        options = named_options(message)
        assert sorted(options) == ["oauth_token", "password"]
        for option in options:
            parsed = PlatformHookRegistry.parse_options("clickhouse-cloud", [(option, "value")])
            assert parsed[option] == "value"
            ClickHouseCloudAdapter(host="abc123.us-east-2.aws.clickhouse.cloud", **{option: "value"})

    def test_named_variables_authenticate_the_adapter(self, message, clean_environment):
        from benchbox.platforms.clickhouse_cloud import ClickHouseCloudAdapter

        variables = named_variables(message, "CLICKHOUSE_CLOUD_")
        assert variables == ["CLICKHOUSE_CLOUD_OAUTH_TOKEN", "CLICKHOUSE_CLOUD_PASSWORD"]
        for variable in variables:
            with patch.dict(os.environ, {variable: "value"}):
                ClickHouseCloudAdapter(host="abc123.us-east-2.aws.clickhouse.cloud")


class TestSnowflakeConfigurationMessage:
    @pytest.fixture(autouse=True)
    def snowflake_dependencies(self):
        with (
            patch("benchbox.platforms.snowflake.check_platform_dependencies", return_value=(True, [])),
            patch("benchbox.platforms.snowflake.snowflake"),
        ):
            yield

    @pytest.fixture
    def message(self, clean_environment):
        from benchbox.platforms.snowflake import SnowflakeAdapter

        with pytest.raises(ConfigurationError) as excinfo:
            SnowflakeAdapter()
        return str(excinfo.value)

    def test_variables_named_for_setup_are_detected_by_setup(self, message, clean_environment):
        from benchbox.platforms.credentials.snowflake import _auto_detect_snowflake

        variables = named_variables(message, "SNOWFLAKE_")
        assert "SNOWFLAKE_USER" not in variables
        for variable in variables:
            clean_environment.setenv(variable, f"value-of-{variable.lower()}")
        detected = _auto_detect_snowflake(MagicMock())
        assert detected is not None
        assert {detected[field] for field in ("username", "password", "account")} == {
            "value-of-snowflake_username",
            "value-of-snowflake_password",
            "value-of-snowflake_account",
        }

    def test_run_reads_no_snowflake_variables(self, message, clean_environment):
        from benchbox.platforms.snowflake import SnowflakeAdapter

        assert "does not read SNOWFLAKE_* environment variables" in message
        for variable in (
            "SNOWFLAKE_ACCOUNT",
            "SNOWFLAKE_USER",
            "SNOWFLAKE_USERNAME",
            "SNOWFLAKE_PASSWORD",
            "SNOWFLAKE_WAREHOUSE",
            "SNOWFLAKE_DATABASE",
        ):
            clean_environment.setenv(variable, "value")
        with pytest.raises(ConfigurationError):
            SnowflakeAdapter()

    def test_saved_credentials_resolve_the_error(self, message):
        from benchbox.platforms.snowflake import SnowflakeAdapter

        assert "benchbox setup --platform snowflake" in message
        SnowflakeAdapter(account="acct", username="user", password="secret", warehouse="WH", database="DB")

    def test_setup_prompt_lists_variable_names(self, clean_environment):
        from benchbox.platforms.credentials.snowflake import _auto_detect_snowflake

        console = MagicMock()
        assert _auto_detect_snowflake(console) is None
        listed = named_variables(printed_text(console), "SNOWFLAKE_")
        assert listed == [
            "SNOWFLAKE_ACCOUNT",
            "SNOWFLAKE_DATABASE",
            "SNOWFLAKE_PASSWORD",
            "SNOWFLAKE_USERNAME",
            "SNOWFLAKE_WAREHOUSE",
        ]
        for variable in listed:
            clean_environment.setenv(variable, "value")
        assert _auto_detect_snowflake(MagicMock()) is not None


class TestRedshiftConfigurationMessage:
    @pytest.fixture(autouse=True)
    def skip_cluster_state_check(self):
        from benchbox.platforms.redshift import RedshiftAdapter

        with patch.object(RedshiftAdapter, "_resolve_connect_timeout", return_value=10):
            yield

    @pytest.fixture
    def message(self, clean_environment):
        from benchbox.platforms.redshift import RedshiftAdapter

        try:
            RedshiftAdapter()
        except ImportError:
            pytest.skip("Redshift drivers not installed")
        except ConfigurationError as exc:
            return str(exc)
        raise AssertionError("RedshiftAdapter() accepted an empty configuration")

    def test_variables_named_for_setup_are_detected_by_setup(self, message, clean_environment):
        from benchbox.platforms.credentials.redshift import _auto_detect_redshift

        variables = named_variables(message, "REDSHIFT_")
        assert "REDSHIFT_USER" not in variables
        assert "REDSHIFT_USERNAME" in variables
        for variable in variables:
            clean_environment.setenv(variable, "value")
        detected = _auto_detect_redshift(MagicMock())
        assert detected is not None
        assert detected["username"] == "value"

    def test_run_reads_no_redshift_variables(self, message, clean_environment):
        from benchbox.platforms.redshift import RedshiftAdapter

        assert "does not read REDSHIFT_* environment variables" in message
        for variable in ("REDSHIFT_HOST", "REDSHIFT_USER", "REDSHIFT_USERNAME", "REDSHIFT_PASSWORD"):
            clean_environment.setenv(variable, "value")
        with pytest.raises(ConfigurationError):
            RedshiftAdapter()


class TestBigQueryConfigurationMessage:
    @pytest.fixture(autouse=True)
    def bigquery_dependencies(self):
        with (
            patch("benchbox.platforms.bigquery.check_platform_dependencies", return_value=(True, [])),
            patch("benchbox.platforms.bigquery.bigquery"),
        ):
            yield

    @pytest.fixture
    def message(self, clean_environment):
        from benchbox.platforms.bigquery import BigQueryAdapter

        with pytest.raises(ConfigurationError) as excinfo:
            BigQueryAdapter()
        return str(excinfo.value)

    def test_variables_are_read_by_setup_not_by_run(self, message, clean_environment, tmp_path):
        from benchbox.platforms.bigquery import BigQueryAdapter
        from benchbox.platforms.credentials.bigquery import _auto_detect_bigquery

        key_file = tmp_path / "key.json"
        key_file.write_text("{}")
        clean_environment.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(key_file))
        clean_environment.setenv("BIGQUERY_PROJECT", "project-from-env")

        assert "BIGQUERY_PROJECT" in message
        assert "benchbox setup --platform bigquery" in message
        detected = _auto_detect_bigquery(MagicMock())
        assert detected is not None
        assert detected["project_id"] == "project-from-env"
        with pytest.raises(ConfigurationError):
            BigQueryAdapter()

    def test_names_no_unregistered_flag(self, message):
        assert "--project-id" not in message
        assert not named_options(message)


class TestMotherDuckTokenMessage:
    def test_names_only_the_environment_variable(self, clean_environment):
        from benchbox.platforms.motherduck import MotherDuckAdapter

        with pytest.raises(ValueError) as excinfo:
            MotherDuckAdapter()
        message = str(excinfo.value)

        assert "--motherduck-token" not in message
        assert named_variables(message, "MOTHERDUCK_") == ["MOTHERDUCK_TOKEN"]
        clean_environment.setenv("MOTHERDUCK_TOKEN", "token")
        MotherDuckAdapter()


class TestAthenaStagingMessage:
    @pytest.fixture
    def message(self, clean_environment, tmp_path):
        from benchbox.platforms.athena import AthenaAdapter

        clean_environment.delenv("AWS_ACCESS_KEY_ID", raising=False)
        clean_environment.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
        clean_environment.setenv("HOME", str(tmp_path))
        modules = {"boto3": MagicMock(), "pyathena": MagicMock(), "pyathena.cursor": MagicMock()}
        with (
            patch.dict("sys.modules", modules),
            patch.object(AthenaAdapter, "_check_instance_metadata_available", return_value=False),
            pytest.raises(ConfigurationError) as excinfo,
        ):
            AthenaAdapter()
        return str(excinfo.value)

    def test_named_options_are_accepted(self, message):
        options = named_options(message)
        assert sorted(options) == ["aws_profile", "s3_bucket", "s3_staging_dir"]
        for option in options:
            assert PlatformHookRegistry.parse_options("athena", [(option, "value")])[option] == "value"

    def test_setup_variable_is_detected_by_setup(self, message, clean_environment):
        from benchbox.platforms.credentials.athena import _auto_detect_athena

        assert "ATHENA_S3_STAGING_DIR" in message
        assert "benchbox setup --platform athena" in message
        clean_environment.setenv("ATHENA_S3_STAGING_DIR", "s3://bucket/path/")
        clean_environment.setenv("AWS_PROFILE", "profile")
        detected = _auto_detect_athena(MagicMock())
        assert detected is not None
        assert detected["s3_staging_dir"] == "s3://bucket/path/"


class TestDatabricksGatewayMessage:
    def test_points_at_setup_not_an_unread_variable(self, clean_environment):
        from benchbox.core.gateway import supported_gateways
        from benchbox.platforms.databricks.adapter import _validate_gateway

        gateways = sorted(supported_gateways("databricks"))
        if not gateways:
            pytest.skip("Databricks has no alternative gateway")
        clean_environment.setenv("DATABRICKS_CLIENT_ID", "id")
        with pytest.raises(ConfigurationError) as excinfo:
            _validate_gateway(gateways[0])
        message = str(excinfo.value)

        assert "DATABRICKS_TOKEN" not in message
        assert "benchbox setup --platform databricks" in message


class TestQuantonApiKeyMessage:
    def test_named_option_is_accepted(self, clean_environment):
        from benchbox.platforms.onehouse.quanton_adapter import QuantonAdapter

        clean_environment.delenv("ONEHOUSE_API_KEY", raising=False)
        with pytest.raises(ConfigurationError) as excinfo:
            QuantonAdapter(s3_staging_dir="s3://bucket/path")
        message = str(excinfo.value)

        options = named_options(message)
        assert options == ["api_key"]
        assert PlatformHookRegistry.parse_options("quanton", [("api_key", "value")])["api_key"] == "value"
        assert named_variables(message, "ONEHOUSE_") == ["ONEHOUSE_API_KEY"]


class TestSparkExternalStagingMessage:
    def test_athena_spark_instruction_option_is_registered(self):
        from benchbox.platforms.aws.athena_spark_adapter import AthenaSparkAdapter

        assert "s3_staging_dir platform option" in AthenaSparkAdapter.external_staging_instruction
        parsed = PlatformHookRegistry.parse_options("athena-spark", [("s3_staging_dir", "s3://bucket/path")])
        assert parsed["s3_staging_dir"] == "s3://bucket/path"

    def test_default_instruction_names_no_unregistered_option(self):
        from benchbox.platforms.base.cloud_spark.external_tables import SparkExternalTableMixin

        assert not named_options(SparkExternalTableMixin.external_staging_instruction)
        assert not CLI_FLAG_PATTERN.search(SparkExternalTableMixin.external_staging_instruction)

    def test_missing_staging_raises_with_adapter_instruction(self):
        from benchbox.platforms.base.cloud_spark.external_tables import SparkExternalTableMixin

        class Adapter(SparkExternalTableMixin):
            platform_name = "Example"
            external_staging_instruction = "Pass --output s3://bucket/path."

        with pytest.raises(ValueError, match=re.escape("Pass --output s3://bucket/path.")):
            Adapter().validate_external_table_requirements()
