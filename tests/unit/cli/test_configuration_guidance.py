from pathlib import Path

import pytest
from click.testing import CliRunner

from benchbox.cli.main import cli
from benchbox.core.hooks.platform_hooks import PlatformHookRegistry

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("relative_path", "forbidden_advice"),
    [
        (
            "benchbox/platforms/bigquery.py",
            ("benchbox platforms setup --platform", "--platform-option project_id="),
        ),
        (
            "benchbox/platforms/pg_duckdb.py",
            ("--platform-option motherduck_token=",),
        ),
        (
            "benchbox/platforms/databricks/adapter.py",
            (
                "benchbox platforms setup --platform",
                "--platform-option server_hostname=",
                "--platform-option http_path=",
            ),
        ),
        ("benchbox/platforms/snowflake.py", ("benchbox platforms setup --platform", "--platform-option account=")),
        ("benchbox/platforms/redshift.py", ("benchbox platforms setup --platform", "--platform-option host=")),
        (
            "benchbox/platforms/starburst.py",
            ("--platform-option host=", "--platform-option username=", "--platform-option password="),
        ),
        (
            "benchbox/platforms/presto_trino_adapter_base.py",
            ("--platform-option host=<host>", "--platform-option port=<port>"),
        ),
    ],
)
def test_connection_guidance_does_not_advertise_unregistered_platform_options(
    relative_path: str, forbidden_advice: tuple[str, ...]
) -> None:
    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    for advice in forbidden_advice:
        assert advice not in source


@pytest.mark.parametrize(
    ("platform", "option", "value"),
    [
        ("athena", "aws_profile", "benchbox-dev"),
        ("athena", "s3_bucket", "benchbox-athena-staging"),
        ("athena", "s3_staging_dir", "s3://benchbox-athena-staging/results"),
        ("athena", "staging_root", "s3://benchbox-athena-staging/data"),
        ("bigquery", "biglake_connection", "my-project.us.my-connection"),
        ("motherduck", "database", "benchbox"),
        ("pg-duckdb", "duckdb_db_path", "/tmp/pg_duckdb.db"),
        ("redshift", "iam_role", "arn:aws:iam::123456789012:role/benchbox"),
        ("redshift", "s3_bucket", "benchbox-redshift-staging"),
        ("redshift", "staging_root", "s3://benchbox-redshift-staging/data"),
        ("snowflake", "iceberg_external_volume", "benchbox_vol"),
        ("snowflake", "staging_root", "s3://benchbox-snowflake-staging/data"),
        ("spark", "driver_memory", "8g"),
        ("spark", "java_home", "/usr/lib/jvm/java-17"),
        ("synapse", "staging_root", "abfss://c@a.dfs.core.windows.net/data"),
        ("velox", "jar", "/opt/gluten/gluten-velox-bundle.jar"),
        ("fabric_dw", "warehouse", "example-warehouse"),
        ("clickhouse-cloud", "host", "example.us-east-2.aws.clickhouse.cloud"),
        ("clickhouse-server", "host", "my-clickhouse.example.com"),
        ("clickhouse-server", "port", "9000"),
        ("clickhouse-server", "username", "default"),
        ("clickhouse-server", "password", "secret"),
    ],
)
def test_advertised_connection_options_are_accepted_by_the_cli(
    tmp_path: Path, platform: str, option: str, value: str
) -> None:
    accepted = set(PlatformHookRegistry.list_option_specs(platform))
    for spec in PlatformHookRegistry._option_specs.get(platform, {}).values():
        accepted.update(getattr(spec, "aliases", ()) or ())
    assert option in accepted

    result = CliRunner().invoke(
        cli,
        [
            "run",
            "--platform",
            platform,
            "--benchmark",
            "tpch",
            "--scale",
            "0.01",
            "--dry-run",
            str(tmp_path / platform),
            "--non-interactive",
            "--platform-option",
            f"{option}={value}",
        ],
    )

    assert result.exit_code == 0, result.output


def test_platforms_setup_platform_delegates_to_the_credential_wizard() -> None:
    result = CliRunner().invoke(cli, ["platforms", "setup", "--platform", "snowflake"])

    assert "No such option" not in result.output
    assert "snowflake" in result.output.lower()


def test_platforms_setup_platform_rejects_a_non_credential_platform() -> None:
    result = CliRunner().invoke(cli, ["platforms", "setup", "--platform", "duckdb"])

    assert result.exit_code != 0
    assert "is not one of" in result.output
    assert "Credentials Setup" not in result.output


def test_the_two_setup_commands_point_at_each_other() -> None:
    credentials = CliRunner().invoke(cli, ["setup", "--help"]).output
    adapters = CliRunner().invoke(cli, ["platforms", "setup", "--help"]).output

    assert "platforms setup" in credentials
    assert "benchbox setup" in adapters
