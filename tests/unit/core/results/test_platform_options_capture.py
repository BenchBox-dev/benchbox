from __future__ import annotations

from datetime import datetime

import pytest

from benchbox.core.results.builder import BenchmarkInfoInput, ResultBuilder, RunConfigInput
from benchbox.core.results.platform_info import PlatformInfoInput
from benchbox.core.results.platform_options import REDACTED_VALUE, build_platform_options_capture
from benchbox.core.results.query_normalizer import normalize_query_result
from benchbox.core.results.schema import build_result_payload
from benchbox.core.runner.runner import ValidationOptions, _build_run_config_from_options
from benchbox.core.schemas import BenchmarkConfig, DatabaseConfig
from benchbox.utils.verbosity import VerbositySettings

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_platform_options_capture_merges_values_and_records_sources() -> None:
    values, sources = build_platform_options_capture(
        requested_options={
            "warehouse": "CLI_WH",
            "cache_enabled": True,
            "password": "super-secret",
        },
        requested_sources={
            "warehouse": "cli_option",
            "cache_enabled": "registered_default",
            "password": "cli_option",
        },
        database_options={
            "warehouse": "SAVED_WH",
            "region": "us-east-1",
            "cache_enabled": True,
            "access_token": "saved-token",
            "verbose": True,
            "_explicit_platform_options": {"warehouse": "CLI_WH"},
        },
    )

    assert values == {
        "warehouse": "CLI_WH",
        "region": "us-east-1",
        "cache_enabled": True,
        "access_token": REDACTED_VALUE,
        "password": REDACTED_VALUE,
    }
    assert sources == {
        "warehouse": "cli_option",
        "region": "saved_config",
        "cache_enabled": "saved_config",
        "access_token": "saved_config",
        "password": "cli_option",
    }


def test_registered_default_does_not_overwrite_saved_config_provenance() -> None:
    values, sources = build_platform_options_capture(
        requested_options={"foo": "bar"},
        requested_sources={"foo": "registered_default"},
        database_options={"foo": "bar"},
    )

    assert values == {"foo": "bar"}
    assert sources == {"foo": "saved_config"}


def test_registered_default_recorded_when_no_saved_value() -> None:
    values, sources = build_platform_options_capture(
        requested_options={"foo": "bar"},
        requested_sources={"foo": "registered_default"},
        database_options=None,
    )

    assert values == {"foo": "bar"}
    assert sources == {"foo": "registered_default"}


def test_capture_retains_usernames_until_the_export_boundary() -> None:
    values, _sources = build_platform_options_capture(
        requested_options={"username": "alice-sentinel", "password": "secret-sentinel"}
    )

    assert values["username"] == "alice-sentinel"
    assert values["password"] == REDACTED_VALUE


@pytest.mark.parametrize(
    "key",
    [
        "sessionToken",
        "SessionToken",
        "session-token",
        "session.token",
        "AccessKeyId",
        "accessKeyId",
        "access-key-id",
        "ConnectionString",
        "connectionString",
        "connection-string",
        "PrivateKey",
        "privateKey",
        "private-key",
        "Credential",
        "AwsCredentials",
    ],
)
def test_camelcase_kebabcase_secret_keys_are_redacted(key: str) -> None:
    from benchbox.core.results.platform_options import is_secret_option_key

    assert is_secret_option_key(key), f"{key!r} should be classified as a secret key"


def test_sanitize_redacts_camelcase_secret_values() -> None:
    from benchbox.core.results.platform_options import sanitize_platform_options

    sanitized = sanitize_platform_options(
        {
            "sessionToken": "raw-session",
            "accessKeyId": "AKIA-RAW",
            "ConnectionString": "Server=...;Pwd=raw",
            "warehouse": "WH",
        }
    )

    assert sanitized["sessionToken"] == REDACTED_VALUE
    assert sanitized["accessKeyId"] == REDACTED_VALUE
    assert sanitized["ConnectionString"] == REDACTED_VALUE
    assert sanitized["warehouse"] == "WH"


@pytest.mark.parametrize(
    "key",
    [
        "s3_key_id",
        "kms_key_id",
        "KmsKeyId",
        "kmsKeyId",
        "key_id",
        "keyId",
    ],
)
def test_provider_prefixed_key_id_keys_are_redacted(key: str) -> None:
    from benchbox.core.results.platform_options import is_secret_option_key

    assert is_secret_option_key(key), f"{key!r} should be classified as a secret key"


@pytest.mark.parametrize(
    "key",
    [
        "sort_key",
        "sortKey",
        "partition_key",
        "primary_key",
        "clustering_key",
        "distkey",
        "distribution_key",
        "warehouse",
        "memory_limit",
    ],
)
def test_non_credential_key_names_are_not_redacted(key: str) -> None:
    from benchbox.core.results.platform_options import is_secret_option_key

    assert not is_secret_option_key(key), f"{key!r} must keep exporting its real value"


def test_sanitize_redacts_key_id_end_to_end_without_touching_sort_keys() -> None:
    from benchbox.core.results.platform_options import sanitize_platform_options

    sanitized = sanitize_platform_options(
        {
            "s3_key_id": "AKIAIOSFODNN7EXAMPLE",
            "kms_key_id": "arn:aws:kms:us-east-1:111122223333:key/abcd",
            "s3_secret": "raw-secret",
            "sort_key": "l_shipdate",
            "partition_key": "l_orderkey",
        }
    )

    assert sanitized["s3_key_id"] == REDACTED_VALUE
    assert sanitized["kms_key_id"] == REDACTED_VALUE
    assert sanitized["s3_secret"] == REDACTED_VALUE
    assert sanitized["sort_key"] == "l_shipdate"
    assert sanitized["partition_key"] == "l_orderkey"


def test_credential_aliases_are_redacted_without_touching_paths_or_tuning_keys() -> None:
    from benchbox.core.results.platform_options import sanitize_platform_options

    sanitized = sanitize_platform_options(
        {
            "passwd": "PASSWD_SECRET",
            "pwd": "PWD_SECRET",
            "pat": "PAT_SECRET",
            "path": "/tmp/data",
            "sort_key": "o_orderkey",
            "partition_key": "id",
            "primary_key": "id",
        }
    )

    assert sanitized["passwd"] == REDACTED_VALUE
    assert sanitized["pwd"] == REDACTED_VALUE
    assert sanitized["pat"] == REDACTED_VALUE
    assert sanitized["path"] == "/tmp/data"
    assert sanitized["sort_key"] == "o_orderkey"
    assert sanitized["partition_key"] == "id"
    assert sanitized["primary_key"] == "id"


def test_uri_userinfo_credentials_are_redacted_without_touching_public_values() -> None:
    from benchbox.core.results.platform_options import sanitize_platform_options

    sanitized = sanitize_platform_options(
        {
            "database_path": "postgresql://user:URI_PASSWORD@db.example/db",
            "output_location": "https://user:URI_TOKEN@example.com/export",
            "url": "https://public.example/export",
            "path": "/tmp/data",
            "sort_key": "o_orderkey",
        }
    )

    assert sanitized["database_path"] == "postgresql://****@db.example/db"
    assert sanitized["output_location"] == "https://****@example.com/export"
    assert sanitized["url"] == "https://public.example/export"
    assert sanitized["path"] == "/tmp/data"
    assert sanitized["sort_key"] == "o_orderkey"


def test_uri_username_only_identity_is_preserved() -> None:
    from benchbox.core.results.platform_options import sanitize_platform_options

    sanitized = sanitize_platform_options(
        {
            "username_only": "https://public-user@example.invalid/export",
            "export_url": "https://public-user:URI_USERINFO_GATE@example.invalid/export",
        }
    )

    assert sanitized["username_only"] == "https://public-user@example.invalid/export"
    assert "public-user" in sanitized["username_only"]
    assert sanitized["export_url"] == "https://****@example.invalid/export"
    assert "URI_USERINFO_GATE" not in sanitized["export_url"]


def test_uri_query_and_fragment_credential_params_are_scrubbed() -> None:
    import json

    from benchbox.core.results.platform_options import sanitize_platform_options

    values = {
        "query": "https://example.invalid/export?access_token=URI_QUERY_GATE&x=1",
        "ssl": "postgresql://host.example/db?sslpassword=URI_SSL_GATE&application_name=bb",
        "fragment": "https://example.invalid/export#access_token=URI_FRAG_GATE&section=results",
        "endpoint": "https://example.invalid/x?password=URI_PW_GATE&region=us-east-1",
        "combined": "postgresql://u:URI_USERINFO_GATE@example.invalid/db?sslpassword=URI_SSL2_GATE&x=1",
        "ordinary": "https://example.invalid/export?x=1&region=us-east-1",
    }
    sanitized = sanitize_platform_options(values)
    out = json.dumps(sanitized)

    for sentinel in (
        "URI_QUERY_GATE",
        "URI_SSL_GATE",
        "URI_FRAG_GATE",
        "URI_PW_GATE",
        "URI_USERINFO_GATE",
        "URI_SSL2_GATE",
    ):
        assert sentinel not in out, out

    assert sanitized["query"] == "https://example.invalid/export?access_token=****&x=1"
    assert sanitized["ssl"] == "postgresql://host.example/db?sslpassword=****&application_name=bb"
    assert sanitized["fragment"] == "https://example.invalid/export#access_token=****&section=results"
    assert sanitized["endpoint"] == "https://example.invalid/x?password=****&region=us-east-1"
    assert sanitized["combined"] == "postgresql://****@example.invalid/db?sslpassword=****&x=1"
    assert sanitized["ordinary"] == "https://example.invalid/export?x=1&region=us-east-1"
    assert "x=1" in out
    assert "application_name=bb" in out
    assert "region=us-east-1" in out


def test_uri_userinfo_redaction_consumes_an_unescaped_at_in_the_password() -> None:
    from benchbox.core.results.platform_options import sanitize_platform_options

    sanitized = sanitize_platform_options(
        {
            "database_path": "postgres://user:pa@ss@host/db",
            "output_location": "postgres://user:pw@host?opt=a@b",
        }
    )

    assert sanitized["database_path"] == "postgres://****@host/db"
    assert "ss@host" not in sanitized["database_path"]
    assert sanitized["output_location"] == "postgres://****@host?opt=a@b"


@pytest.mark.parametrize(
    "uri",
    [
        "abfss://container@account.dfs.core.windows.net/path",
        "abfs://container@account.dfs.core.windows.net/path",
        "wasbs://container@account.blob.core.windows.net/path",
        "wasb://container@account.blob.core.windows.net/path",
        "ABFSS://Container@account.dfs.core.windows.net/path",
    ],
)
def test_azure_storage_container_authority_is_preserved(uri: str) -> None:
    from benchbox.core.results.platform_options import sanitize_platform_options

    sanitized = sanitize_platform_options({"staging_root": uri})

    assert sanitized["staging_root"] == uri


def test_lifecycle_run_config_persists_sanitized_platform_options_with_provenance() -> None:
    benchmark_config = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        options={
            "platform_options": {
                "warehouse": "CLI_WH",
                "password": "super-secret",
            },
            "platform_option_sources": {
                "warehouse": "cli_option",
                "password": "cli_option",
            },
        },
    )
    database_config = DatabaseConfig(
        type="snowflake",
        name="Snowflake",
        options={
            "warehouse": "SAVED_WH",
            "region": "us-east-1",
            "access_token": "saved-token",
            "verbose": True,
        },
        warehouse_size="XSMALL",
    )

    run_config = _build_run_config_from_options(
        benchmark_config=benchmark_config,
        options=benchmark_config.options,
        platform_config={},
        database_config=database_config,
        validation_opts=ValidationOptions(),
        verbosity_settings=VerbositySettings.default(),
        test_type="power",
        table_format=None,
    )

    assert run_config.platform_options == {
        "warehouse": "CLI_WH",
        "region": "us-east-1",
        "warehouse_size": "XSMALL",
        "access_token": REDACTED_VALUE,
        "password": REDACTED_VALUE,
    }
    assert run_config.platform_option_sources == {
        "warehouse": "cli_option",
        "region": "saved_config",
        "warehouse_size": "saved_config",
        "access_token": "saved_config",
        "password": "cli_option",
    }


def test_lifecycle_run_config_preserves_requested_phases() -> None:
    benchmark_config = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        options={"requested_phases": ["power", "throughput"]},
    )

    run_config = _build_run_config_from_options(
        benchmark_config=benchmark_config,
        options=benchmark_config.options,
        platform_config={},
        database_config=None,
        validation_opts=ValidationOptions(),
        verbosity_settings=VerbositySettings.default(),
        test_type="combined",
        table_format=None,
    )

    assert run_config.options == {"requested_phases": ["power", "throughput"]}


def test_sanitize_excludes_internal_keys_when_requested() -> None:
    from benchbox.core.results.platform_options import sanitize_platform_options

    sanitized = sanitize_platform_options(
        {
            "warehouse": "WH",
            "tuning_config": object(),
            "unified_tuning_configuration": object(),
            "df_tuning_config": object(),
            "tuning_enabled": True,
            "_explicit_platform_options": {"warehouse": "WH"},
        },
        exclude_internal=True,
    )

    assert sanitized == {"warehouse": "WH"}
    assert "tuning_config" not in sanitized
    assert "unified_tuning_configuration" not in sanitized
    assert "df_tuning_config" not in sanitized
    assert "tuning_enabled" not in sanitized


def test_sanitize_without_exclude_internal_preserves_existing_behavior() -> None:
    from benchbox.core.results.platform_options import sanitize_platform_options

    sanitized = sanitize_platform_options({"warehouse": "WH", "tuning_enabled": True})

    assert sanitized["warehouse"] == "WH"
    assert sanitized["tuning_enabled"] is True


def test_sanitize_uses_to_dict_for_objects_that_support_it() -> None:
    from benchbox.core.results.platform_options import sanitize_platform_options

    class _Config:
        def to_dict(self) -> dict[str, object]:
            return {"driver": "postgres", "password": "super-secret"}

    sanitized = sanitize_platform_options({"tuning_config": _Config()})

    assert sanitized["tuning_config"] == {"driver": "postgres", "password": REDACTED_VALUE}


def test_sanitize_marks_unserializable_values_instead_of_using_repr() -> None:
    from benchbox.core.results.platform_options import sanitize_platform_options

    class _Opaque:
        def __repr__(self) -> str:
            return "<Opaque object at 0x1234 with lots of internal repr text>"

    sanitized = sanitize_platform_options({"weird": _Opaque()})

    assert sanitized["weird"] == "<unserializable:_Opaque>"
    assert "0x" not in sanitized["weird"]
    assert "repr" not in sanitized["weird"]


def test_sanitize_never_raises_when_to_dict_itself_fails() -> None:
    from benchbox.core.results.platform_options import sanitize_platform_options

    class _Broken:
        def to_dict(self) -> dict[str, object]:
            raise RuntimeError("boom")

    sanitized = sanitize_platform_options({"broken": _Broken()})

    assert sanitized["broken"] == "<unserializable:_Broken>"


def test_sanitize_never_raises_when_to_dict_lookup_itself_fails() -> None:
    from benchbox.core.results.platform_options import sanitize_platform_options

    class _BrokenDescriptor:
        @property
        def to_dict(self):
            raise RuntimeError("boom during attribute access")

    sanitized = sanitize_platform_options({"broken": _BrokenDescriptor()})

    assert sanitized["broken"] == "<unserializable:_BrokenDescriptor>"


def test_secret_redaction_unaffected_by_internal_key_filtering() -> None:
    from benchbox.core.results.platform_options import sanitize_platform_options

    sanitized = sanitize_platform_options(
        {"password": "super-secret", "tuning_config": "x"},
        exclude_internal=True,
    )

    assert sanitized == {"password": REDACTED_VALUE}


def test_result_payload_exports_platform_option_sources_from_run_config() -> None:
    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(name="TPC-H", scale_factor=0.01, benchmark_id="tpch"),
        platform=PlatformInfoInput(name="Snowflake", platform_version="8.0", client_library_version="3.0"),
        execution_id="platform-options-test",
    )
    builder.set_start_time(datetime(2026, 1, 1, 12, 0, 0))
    builder.set_run_config(
        RunConfigInput(
            platform_options={"warehouse": "CLI_WH", "password": REDACTED_VALUE},
            platform_option_sources={"warehouse": "cli_option", "password": "cli_option"},
        )
    )
    builder.add_query_result(
        normalize_query_result(
            {
                "query_id": "Q1",
                "execution_time_seconds": 0.1,
                "rows_returned": 1,
                "status": "SUCCESS",
                "run_type": "measurement",
            }
        )
    )

    payload = build_result_payload(builder.build())

    assert payload["config"]["platform_options"] == {"warehouse": "CLI_WH", "password": REDACTED_VALUE}
    assert payload["config"]["platform_option_sources"] == {
        "warehouse": "cli_option",
        "password": "cli_option",
    }


def test_result_payload_serializes_dataframe_tuning_config() -> None:
    import json

    from benchbox.core.dataframe.tuning.interface import DataFrameTuningConfiguration

    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(name="TPC-H", scale_factor=1.0, benchmark_id="tpch"),
        platform=PlatformInfoInput(name="Polars", platform_version="1.3", client_library_version="1.3"),
        execution_id="dataframe-tuning-config-test",
    )
    dataframe_tuning = DataFrameTuningConfiguration()
    dataframe_tuning.memory.chunk_size = 1024
    builder.set_run_config(
        RunConfigInput(
            tuning_mode="tuned",
            tuning_config=dataframe_tuning,
        )
    )
    builder.add_query_result(
        normalize_query_result(
            {
                "query_id": "Q1",
                "execution_time_seconds": 0.1,
                "rows_returned": 1,
                "status": "SUCCESS",
                "run_type": "measurement",
            }
        )
    )

    payload = build_result_payload(builder.build())

    json.dumps(payload)
    assert payload["config"]["tuning_config"] == {
        "memory": {
            "memory_limit": None,
            "chunk_size": 1024,
            "spill_to_disk": False,
            "spill_directory": None,
            "rechunk_after_filter": True,
        }
    }


class TestUsernameRedactionInternalPath:
    def test_username_keys_are_redacted(self):
        from benchbox.core.results.platform_options import sanitize_platform_options

        result = sanitize_platform_options(
            {"username": "alice-sentinel", "user": "bob-sentinel", "pg_user": "carol-sentinel"}
        )
        assert result["username"] == REDACTED_VALUE
        assert result["user"] == REDACTED_VALUE
        assert result["pg_user"] == REDACTED_VALUE

    def test_user_lookalike_keys_are_not_over_redacted(self):
        from benchbox.core.results.platform_options import sanitize_platform_options

        result = sanitize_platform_options({"user_agent": "ua/1.0", "num_users": 7, "users_table": "users"})
        assert result["user_agent"] == "ua/1.0"
        assert result["num_users"] == 7
        assert result["users_table"] == "users"

    def test_secret_keys_keep_current_behavior(self):
        from benchbox.core.results.platform_options import sanitize_platform_options

        result = sanitize_platform_options({"password": "pw-sentinel", "s3_key_id": "AKIA-sentinel"})
        assert result["password"] == REDACTED_VALUE
        assert result["s3_key_id"] == REDACTED_VALUE

    def test_nested_username_is_redacted(self):
        from benchbox.core.results.platform_options import sanitize_platform_options

        result = sanitize_platform_options({"connection": {"username": "alice-sentinel", "host": "db.internal"}})
        assert result["connection"]["username"] == REDACTED_VALUE
        assert result["connection"]["host"] == "db.internal"


class TestServiceAccountRedaction:
    def test_service_account_is_redacted_internally(self):
        from benchbox.core.results.platform_options import sanitize_platform_options

        out = sanitize_platform_options({"service_account": "svc-bench@proj.iam.gserviceaccount.com"})
        assert out["service_account"] != "svc-bench@proj.iam.gserviceaccount.com"

    def test_non_email_service_account_is_pseudonymized_publicly(self):
        import json

        from benchbox.core.results.anonymization import AnonymizationManager

        out = AnonymizationManager().anonymize_result_payload(
            {"platform_metadata": {"platform_raw_config": {"service_account": "SA-SENTINEL-NOT-AN-EMAIL"}}}
        )["platform_metadata"]["platform_raw_config"]
        assert "SA-SENTINEL-NOT-AN-EMAIL" not in json.dumps(out, default=str)

    def test_auth_method_lookalikes_keep_real_values(self):
        from benchbox.core.results.platform_options import sanitize_platform_options

        out = sanitize_platform_options({"auth_method": "service_principal", "runtime_version": "1.2"})
        assert out["auth_method"] == "service_principal"
        assert out["runtime_version"] == "1.2"


class TestApiKeyAndAccountKeyRedaction:
    def test_api_key_variants_are_redacted(self):
        from benchbox.core.results.platform_options import sanitize_platform_options

        result = sanitize_platform_options(
            {"api_key": "APIKEY-SENTINEL", "onehouse_api_key": "OH-SENTINEL", "apiKey": "CAMEL-SENTINEL"}
        )
        assert result["api_key"] == REDACTED_VALUE
        assert result["onehouse_api_key"] == REDACTED_VALUE
        assert result["apiKey"] == REDACTED_VALUE

    def test_storage_account_key_is_redacted(self):
        from benchbox.core.results.platform_options import sanitize_platform_options

        assert sanitize_platform_options({"storage_account_key": "AZKEY-SENTINEL"})["storage_account_key"] == (
            REDACTED_VALUE
        )

    def test_data_modelling_keys_still_export(self):
        from benchbox.core.results.platform_options import sanitize_platform_options

        result = sanitize_platform_options(
            {"sort_key": "l_shipdate", "partition_key": "l_orderkey", "primary_key": "id", "account": "acct-1"}
        )
        assert result["sort_key"] == "l_shipdate"
        assert result["partition_key"] == "l_orderkey"
        assert result["primary_key"] == "id"
        assert result["account"] == "acct-1"


@pytest.mark.parametrize(
    "key",
    [
        "dsn",
        "database_dsn",
        "spark.hadoop.fs.azure.sas.container.account.blob.core.windows.net",
    ],
)
def test_dsn_and_sas_credential_keys_are_redacted(key: str) -> None:
    from benchbox.core.results.platform_options import sanitize_platform_options

    assert sanitize_platform_options({key: "CREDENTIAL-SENTINEL"})[key] == REDACTED_VALUE


def test_lifecycle_run_config_carries_show_query_plans_from_database_options() -> None:
    benchmark_config = BenchmarkConfig(name="tpch", display_name="TPC-H")
    database_config = DatabaseConfig(
        type="duckdb",
        name="DuckDB",
        options={"show_query_plans": True},
    )

    run_config = _build_run_config_from_options(
        benchmark_config=benchmark_config,
        options=benchmark_config.options,
        platform_config={},
        database_config=database_config,
        validation_opts=ValidationOptions(),
        verbosity_settings=VerbositySettings.default(),
        test_type="standard",
        table_format=None,
    )

    assert run_config.show_query_plans is True


def test_lifecycle_run_config_show_query_plans_defaults_none() -> None:
    benchmark_config = BenchmarkConfig(name="tpch", display_name="TPC-H")

    run_config = _build_run_config_from_options(
        benchmark_config=benchmark_config,
        options=benchmark_config.options,
        platform_config={},
        database_config=None,
        validation_opts=ValidationOptions(),
        verbosity_settings=VerbositySettings.default(),
        test_type="standard",
        table_format=None,
    )

    assert run_config.show_query_plans is None


def test_lifecycle_run_config_carries_show_query_plans_from_database_extra() -> None:
    benchmark_config = BenchmarkConfig(name="tpch", display_name="TPC-H")
    database_config = DatabaseConfig(type="duckdb", name="DuckDB", options={})
    database_config.show_query_plans = True

    run_config = _build_run_config_from_options(
        benchmark_config=benchmark_config,
        options=benchmark_config.options,
        platform_config={},
        database_config=database_config,
        validation_opts=ValidationOptions(),
        verbosity_settings=VerbositySettings.default(),
        test_type="standard",
        table_format=None,
    )

    assert run_config.show_query_plans is True


def test_lifecycle_run_config_explicit_false_show_query_plans_overrides_adapter() -> None:
    benchmark_config = BenchmarkConfig(name="tpch", display_name="TPC-H")
    database_config = DatabaseConfig(
        type="duckdb",
        name="DuckDB",
        options={"show_query_plans": False},
    )

    run_config = _build_run_config_from_options(
        benchmark_config=benchmark_config,
        options=benchmark_config.options,
        platform_config={},
        database_config=database_config,
        validation_opts=ValidationOptions(),
        verbosity_settings=VerbositySettings.default(),
        test_type="standard",
        table_format=None,
    )

    assert run_config.show_query_plans is False
