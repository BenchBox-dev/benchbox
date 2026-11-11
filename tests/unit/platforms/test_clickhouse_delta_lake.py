# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.platforms.clickhouse.delta_lake import (
    DELTA_BASE_FUNCTION,
    DELTA_ENGINE_NAME,
    DELTA_TABLE_FUNCTION_NAMES,
    classify_delta_location,
    delta_engine_probe_sql,
    delta_function_probe_sql,
    delta_lake_azure_table_function,
    delta_lake_count_sql,
    delta_lake_engine_ddl,
    delta_lake_local_table_function,
    delta_lake_select_sql,
    delta_lake_table_function,
    has_local_delta_registration,
    has_native_delta_registration,
    quote_identifier,
    quote_literal,
    resolve_delta_reader,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestQuoteLiteral:
    def test_wraps_simple_value(self) -> None:
        assert quote_literal("s3://bucket/table") == "'s3://bucket/table'"

    def test_escapes_single_quote_and_backslash(self) -> None:
        assert quote_literal("o'clock\\path") == "'o\\'clock\\\\path'"

    def test_rejects_empty_value(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            quote_literal("")

    def test_rejects_nul_value(self) -> None:
        with pytest.raises(ValueError, match="NUL"):
            quote_literal("ab\x00cd")


class TestQuoteIdentifier:
    def test_wraps_table_name(self) -> None:
        assert quote_identifier("orders") == "`orders`"

    def test_escapes_backtick(self) -> None:
        assert quote_identifier("odd`name") == "`odd\\`name`"

    def test_rejects_empty_name(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            quote_identifier("")

    def test_rejects_nul_name(self) -> None:
        with pytest.raises(ValueError, match="NUL"):
            quote_identifier("ab\x00cd")


class TestTableFunction:
    def test_bare_url(self) -> None:
        assert delta_lake_table_function("s3://bucket/orders") == "deltaLake('s3://bucket/orders')"

    def test_s3_alias(self) -> None:
        assert (
            delta_lake_table_function("s3://bucket/orders", function="deltaLakeS3")
            == "deltaLakeS3('s3://bucket/orders')"
        )

    def test_with_credentials(self) -> None:
        assert (
            delta_lake_table_function("s3://bucket/orders", access_key_id="AK", secret_access_key="SK")
            == "deltaLake('s3://bucket/orders', 'AK', 'SK')"
        )

    def test_credential_values_are_quoted(self) -> None:
        expression = delta_lake_table_function("s3://bucket/orders", access_key_id="a'k", secret_access_key="s\\k")
        assert expression == "deltaLake('s3://bucket/orders', 'a\\'k', 's\\\\k')"

    def test_rejects_empty_url(self) -> None:
        with pytest.raises(ValueError, match="non-empty URL"):
            delta_lake_table_function("")

    def test_rejects_unknown_function(self) -> None:
        with pytest.raises(ValueError, match="Unknown Delta Lake table function"):
            delta_lake_table_function("s3://bucket/orders", function="deltaLakeLocal")

    def test_rejects_lone_credential(self) -> None:
        with pytest.raises(ValueError, match="together or not at all"):
            delta_lake_table_function("s3://bucket/orders", access_key_id="AK")

    def test_rejects_blank_url(self) -> None:
        with pytest.raises(ValueError, match="non-empty URL"):
            delta_lake_table_function("   ")

    def test_rejects_blank_credential(self) -> None:
        with pytest.raises(ValueError, match="access_key_id.*non-blank"):
            delta_lake_table_function("s3://bucket/orders", access_key_id="  ", secret_access_key="SK")


class TestLocalTableFunction:
    def test_builds_expression(self) -> None:
        assert delta_lake_local_table_function("/data/orders") == "deltaLakeLocal('/data/orders')"

    def test_rejects_empty_path(self) -> None:
        with pytest.raises(ValueError, match="non-empty path"):
            delta_lake_local_table_function("  ")


class TestEngineDdl:
    def test_bare_attach(self) -> None:
        assert delta_lake_engine_ddl("orders", "s3://bucket/orders") == (
            "CREATE TABLE `orders` ENGINE = DeltaLake('s3://bucket/orders')"
        )

    def test_database_qualified_with_credentials(self) -> None:
        assert delta_lake_engine_ddl(
            "orders",
            "s3://bucket/orders",
            database="lake",
            access_key_id="AK",
            secret_access_key="SK",
        ) == ("CREATE TABLE `lake`.`orders` ENGINE = DeltaLake('s3://bucket/orders', 'AK', 'SK')")

    def test_rejects_empty_table(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            delta_lake_engine_ddl("", "s3://bucket/orders")

    def test_rejects_blank_table_and_database(self) -> None:
        with pytest.raises(ValueError, match="non-empty table name"):
            delta_lake_engine_ddl("   ", "s3://bucket/orders")
        with pytest.raises(ValueError, match="non-empty database name"):
            delta_lake_engine_ddl("orders", "s3://bucket/orders", database="  ")

    def test_rejects_lone_credential(self) -> None:
        with pytest.raises(ValueError, match="together or not at all"):
            delta_lake_engine_ddl("orders", "s3://bucket/orders", secret_access_key="SK")


class TestAzureTableFunction:
    def test_bare_location(self) -> None:
        assert delta_lake_azure_table_function("https://acct.blob.core.windows.net", "lake", "orders") == (
            "deltaLakeAzure('https://acct.blob.core.windows.net', 'lake', 'orders')"
        )

    def test_with_account_credentials(self) -> None:
        assert delta_lake_azure_table_function(
            "https://acct.blob.core.windows.net", "lake", "orders", account_name="acct", account_key="KEY"
        ) == ("deltaLakeAzure('https://acct.blob.core.windows.net', 'lake', 'orders', 'acct', 'KEY')")

    def test_rejects_blank_location_part(self) -> None:
        with pytest.raises(ValueError, match="non-empty container"):
            delta_lake_azure_table_function("https://acct.blob.core.windows.net", "  ", "orders")

    def test_rejects_lone_account_credential(self) -> None:
        with pytest.raises(ValueError, match="together or not at all"):
            delta_lake_azure_table_function("https://acct.blob.core.windows.net", "lake", "orders", account_key="KEY")

    def test_credential_values_are_quoted(self) -> None:
        expression = delta_lake_azure_table_function(
            "https://acct.blob.core.windows.net", "lake", "orders", account_name="a'c", account_key="k\\y"
        )
        assert expression == (
            "deltaLakeAzure('https://acct.blob.core.windows.net', 'lake', 'orders', 'a\\'c', 'k\\\\y')"
        )

    def test_rejects_blank_account_key(self) -> None:
        with pytest.raises(ValueError, match="account_key.*non-blank"):
            delta_lake_azure_table_function(
                "https://acct.blob.core.windows.net", "lake", "orders", account_name="acct", account_key=" "
            )


class TestSelectAndCount:
    def test_select_star_with_limit(self) -> None:
        source = delta_lake_table_function("s3://bucket/orders")
        assert delta_lake_select_sql(source, limit=10) == ("SELECT * FROM deltaLake('s3://bucket/orders') LIMIT 10")

    def test_select_column_list_is_quoted(self) -> None:
        source = delta_lake_local_table_function("/data/orders")
        assert delta_lake_select_sql(source, columns=["id", "v"]) == (
            "SELECT `id`, `v` FROM deltaLakeLocal('/data/orders')"
        )

    def test_select_rejects_empty_columns(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            delta_lake_select_sql("deltaLake('s3://b/t')", columns=[])

    def test_select_rejects_blank_column_in_list(self) -> None:
        with pytest.raises(ValueError, match="non-blank column"):
            delta_lake_select_sql("deltaLake('s3://b/t')", columns=["id", "  "])

    def test_select_rejects_negative_limit(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            delta_lake_select_sql("deltaLake('s3://b/t')", limit=-1)

    def test_select_rejects_bool_limit(self) -> None:
        with pytest.raises(ValueError, match="non-negative int"):
            delta_lake_select_sql("deltaLake('s3://b/t')", limit=True)

    def test_select_rejects_float_and_string_limits(self) -> None:
        with pytest.raises(ValueError, match="non-negative int"):
            delta_lake_select_sql("deltaLake('s3://b/t')", limit=10.5)
        with pytest.raises(ValueError, match="non-negative int"):
            delta_lake_select_sql("deltaLake('s3://b/t')", limit="10")

    def test_select_accepts_zero_limit_and_tuple_columns(self) -> None:
        assert delta_lake_select_sql("deltaLake('s3://b/t')", columns=("id", "v"), limit=0) == (
            "SELECT `id`, `v` FROM deltaLake('s3://b/t') LIMIT 0"
        )

    def test_select_rejects_empty_string_column(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            delta_lake_select_sql("deltaLake('s3://b/t')", columns="  ")

    def test_count(self) -> None:
        assert delta_lake_count_sql("deltaLake('s3://bucket/orders')") == (
            "SELECT count() FROM deltaLake('s3://bucket/orders')"
        )

    def test_count_rejects_empty_source(self) -> None:
        with pytest.raises(ValueError, match="non-empty source"):
            delta_lake_count_sql("  ")


class TestSupportProbe:
    def test_function_probe_lists_all_emitted_functions(self) -> None:
        assert delta_function_probe_sql() == (
            "SELECT name FROM system.table_functions WHERE name IN "
            "('deltaLake', 'deltaLakeS3', 'deltaLakeLocal', 'deltaLakeAzure')"
        )
        assert set(DELTA_TABLE_FUNCTION_NAMES) == {"deltaLake", "deltaLakeS3", "deltaLakeLocal", "deltaLakeAzure"}
        assert DELTA_TABLE_FUNCTION_NAMES[0] == DELTA_BASE_FUNCTION == "deltaLake"

    def test_engine_probe(self) -> None:
        assert delta_engine_probe_sql() == "SELECT name FROM system.table_engines WHERE name = 'DeltaLake'"
        assert DELTA_ENGINE_NAME == "DeltaLake"

    def test_registration_requires_base_function_and_engine(self) -> None:
        full = ["deltaLake", "deltaLakeS3", "deltaLakeLocal", "deltaLakeAzure"]
        assert has_native_delta_registration(full, ["DeltaLake"]) is True
        assert (
            has_native_delta_registration(["deltaLakeS3", "deltaLakeLocal", "deltaLakeAzure"], ["DeltaLake"]) is False
        )
        assert has_native_delta_registration(["deltaLake"], ["MergeTree"]) is False
        assert has_native_delta_registration([], []) is False

    def test_local_alias_is_probed_individually(self) -> None:
        assert has_local_delta_registration(["deltaLake", "deltaLakeLocal"]) is True

        assert has_local_delta_registration(["deltaLake", "deltaLakeS3", "deltaLakeAzure"]) is False
        assert has_local_delta_registration([]) is False


class TestClassifyDeltaLocation:
    def test_s3(self) -> None:
        assert classify_delta_location("s3://bucket/table") == "s3"
        assert classify_delta_location("S3://bucket/table") == "s3"

    def test_https_s3_forms(self) -> None:

        assert classify_delta_location("https://clickhouse-public-datasets.s3.amazonaws.com/delta_lake/hits/") == "s3"
        assert classify_delta_location("https://bucket.s3.us-east-1.amazonaws.com/table") == "s3"

        assert classify_delta_location("https://s3.us-east-1.amazonaws.com/bucket/table") == "s3"
        assert classify_delta_location("https://s3.amazonaws.com/bucket/table") == "s3"
        assert classify_delta_location("http://bucket.s3.amazonaws.com/table") == "s3"

    def test_azure_forms(self) -> None:
        assert classify_delta_location("abfss://container@acct.dfs.core.windows.net/table") == "azure"
        assert classify_delta_location("wasb://container@acct.blob.core.windows.net/table") == "azure"
        assert classify_delta_location("https://acct.blob.core.windows.net/container/table") == "azure"
        assert classify_delta_location("DefaultEndpointsProtocol=https;AccountName=acct;") == "azure"

    def test_gcs_forms(self) -> None:
        assert classify_delta_location("gs://bucket/table") == "gcs"
        assert classify_delta_location("https://storage.googleapis.com/bucket/table") == "gcs"

    def test_local_forms(self) -> None:
        assert classify_delta_location("/data/orders") == "local"
        assert classify_delta_location("./relative/orders") == "local"
        assert classify_delta_location("file:///data/orders") == "local"
        assert classify_delta_location("C:\\data\\orders") == "local"

    def test_unknown_and_empty(self) -> None:
        assert classify_delta_location("hdfs://namenode/table") == "unknown"
        assert classify_delta_location("a:b") == "unknown"
        with pytest.raises(ValueError, match="empty"):
            classify_delta_location("   ")


class TestResolveDeltaReader:
    def test_native_s3_when_available(self) -> None:
        s3 = resolve_delta_reader("s3://bucket/orders", native_available=True)
        assert (s3.kind, s3.location_kind) == ("native", "s3")
        assert s3.source_sql == "deltaLake('s3://bucket/orders')"

        https = resolve_delta_reader(
            "https://clickhouse-public-datasets.s3.amazonaws.com/delta_lake/hits/", native_available=True
        )
        assert (https.kind, https.location_kind) == ("native", "s3")

    def test_local_native_requires_local_alias(self) -> None:
        local = resolve_delta_reader("/data/orders", native_available=True, local_native_available=True)
        assert (local.kind, local.location_kind) == ("native", "local")
        assert local.source_sql == "deltaLakeLocal('/data/orders')"

    def test_local_without_local_alias_falls_back_to_snapshot(self) -> None:

        local = resolve_delta_reader("/data/orders", native_available=True)
        assert local.kind == "parquet-snapshot"
        assert local.source_sql == ""
        assert "deltaLakeLocal" in local.reason

        local = resolve_delta_reader("/data/orders", native_available=False, local_native_available=True)
        assert local.kind == "parquet-snapshot"

    def test_remote_without_native_reads_raises(self) -> None:

        for location in (
            "s3://bucket/orders",
            "https://bucket.s3.amazonaws.com/table",
            "abfss://c@a.dfs.core.windows.net/t",
            "gs://bucket/table",
        ):
            with pytest.raises(ValueError, match="No executable Delta read path"):
                resolve_delta_reader(location, native_available=False)

    def test_azure_and_gcs_raise_even_when_native_available(self) -> None:

        for location in ("abfss://c@a.dfs.core.windows.net/t", "gs://bucket/table"):
            with pytest.raises(ValueError, match="No executable Delta read path"):
                resolve_delta_reader(location, native_available=True)

    def test_unknown_location_raises(self) -> None:
        with pytest.raises(ValueError, match="Unrecognized Delta location"):
            resolve_delta_reader("hdfs://namenode/table", native_available=True)


