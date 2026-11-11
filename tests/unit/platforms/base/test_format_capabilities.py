import pytest

from benchbox.platforms.base.format_capabilities import (
    CAPABILITIES_REGISTRY,
    DELTA_CAPABILITY,
    HUDI_CAPABILITY,
    ICEBERG_CAPABILITY,
    PARQUET_CAPABILITY,
    PLATFORM_FORMAT_PREFERENCES,
    SupportLevel,
    get_format_capability,
    get_preferred_format,
    get_supported_formats,
    has_feature,
    is_format_supported,
    normalize_platform_key,
)
from benchbox.utils.format_selection import FormatSelector

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestSupportLevel:
    def test_support_levels_exist(self):

        assert SupportLevel.NATIVE.value == "native"
        assert SupportLevel.EXTENSION.value == "extension"
        assert SupportLevel.EXPERIMENTAL.value == "experimental"
        assert SupportLevel.NOT_SUPPORTED.value == "not_supported"


class TestNormalizePlatformKey:
    def test_direct_match(self):

        assert normalize_platform_key("duckdb") == "duckdb"
        assert normalize_platform_key("datafusion") == "datafusion"
        assert normalize_platform_key("emr-serverless") == "emr-serverless"

    def test_case_insensitive(self):

        assert normalize_platform_key("DuckDB") == "duckdb"
        assert normalize_platform_key("Snowflake") == "snowflake"
        assert normalize_platform_key("BigQuery") == "bigquery"
        assert normalize_platform_key("Redshift") == "redshift"
        assert normalize_platform_key("Trino") == "trino"
        assert normalize_platform_key("Presto") == "presto"
        assert normalize_platform_key("Spark") == "spark"
        assert normalize_platform_key("LakeSail") == "lakesail"

    def test_clickhouse_display_names(self):

        assert normalize_platform_key("ClickHouse Cloud") == "clickhouse-cloud"
        assert normalize_platform_key("ClickHouse (Cloud)") == "clickhouse-cloud"
        assert normalize_platform_key("ClickHouse (Local)") == "clickhouse"

    def test_fabric_display_names(self):

        assert normalize_platform_key("Fabric Lakehouse") == "fabric-lakehouse"
        assert normalize_platform_key("Fabric Warehouse") == "fabric_dw"

    def test_class_name_normalization(self):

        assert normalize_platform_key("EMRServerlessAdapter") == "emr-serverless"
        assert normalize_platform_key("DataprocAdapter") == "dataproc"
        assert normalize_platform_key("DataprocServerlessAdapter") == "dataproc-serverless"
        assert normalize_platform_key("SynapseSparkAdapter") == "synapse-spark"
        assert normalize_platform_key("FabricSparkAdapter") == "fabric-spark"
        assert normalize_platform_key("FabricLakehouseAdapter") == "fabric-lakehouse"
        assert normalize_platform_key("AthenaSparkAdapter") == "athena-spark"

    def test_unknown_platform(self):

        assert normalize_platform_key("SomeNewPlatform") == "somenewplatform"

    def test_normalization_used_by_get_supported_formats(self):

        formats = get_supported_formats("ClickHouse Cloud")
        assert "parquet" in formats
        assert "iceberg" not in formats

    def test_pg_duckdb_spelling_variance(self):

        assert normalize_platform_key("pg-duckdb") == "pg_duckdb"
        assert normalize_platform_key("pg_duckdb") == "pg_duckdb"
        assert normalize_platform_key("fabric-dw") == "fabric_dw"
        assert get_supported_formats("pg-duckdb") == get_supported_formats("pg_duckdb")

    def test_no_folded_key_collisions(self):

        folded = [key.replace("-", "_") for key in PLATFORM_FORMAT_PREFERENCES]
        assert len(set(folded)) == len(folded)

    def test_pg_duckdb_rejects_unbacked_user_format(self):

        with pytest.raises(ValueError, match="pg_duckdb"):
            FormatSelector.select_format("pg_duckdb", ["tbl", "delta"], user_preference="delta")
        with pytest.raises(ValueError, match="pg-duckdb"):
            FormatSelector.select_format("pg-duckdb", ["tbl", "iceberg"], user_preference="iceberg")

    def test_normalization_used_by_is_format_supported(self):

        assert is_format_supported("ClickHouse Cloud", "parquet") is True
        assert (
            is_format_supported(
                "ClickHouse Cloud",
                "iceberg",
                table_mode="external",
                platform_config={"s3_staging_url": "s3://bucket/prefix/"},
            )
            is True
        )


class TestFormatCapabilities:
    def test_parquet_capability(self):

        assert PARQUET_CAPABILITY.format_name == "parquet"
        assert PARQUET_CAPABILITY.display_name == "Apache Parquet"
        assert PARQUET_CAPABILITY.file_extension == ".parquet"
        assert "predicate_pushdown" in PARQUET_CAPABILITY.features
        assert "column_pruning" in PARQUET_CAPABILITY.features

    def test_parquet_platform_support(self):

        assert PARQUET_CAPABILITY.supported_platforms.get("duckdb") == SupportLevel.NATIVE
        assert PARQUET_CAPABILITY.supported_platforms.get("datafusion") == SupportLevel.NATIVE
        assert PARQUET_CAPABILITY.supported_platforms.get("athena") == SupportLevel.NATIVE
        assert PARQUET_CAPABILITY.supported_platforms.get("postgresql") == SupportLevel.EXTENSION
        assert PARQUET_CAPABILITY.supported_platforms.get("pg_duckdb") == SupportLevel.EXTENSION

    def test_parquet_spark_platform_support(self):

        for platform in [
            "spark",
            "emr-serverless",
            "dataproc",
            "dataproc-serverless",
            "synapse-spark",
            "athena-spark",
            "fabric-spark",
            "fabric-lakehouse",
        ]:
            assert PARQUET_CAPABILITY.supported_platforms[platform] == SupportLevel.NATIVE, (
                f"Expected NATIVE parquet support for {platform}"
            )
        assert PARQUET_CAPABILITY.supported_platforms["fabric_dw"] == SupportLevel.NATIVE

    def test_parquet_presto_trino_support(self):

        assert PARQUET_CAPABILITY.supported_platforms["trino"] == SupportLevel.NATIVE
        assert PARQUET_CAPABILITY.supported_platforms["presto"] == SupportLevel.NATIVE

    def test_delta_capability(self):

        assert DELTA_CAPABILITY.format_name == "delta"
        assert DELTA_CAPABILITY.display_name == "Delta Lake"
        assert "time_travel" in DELTA_CAPABILITY.features
        assert "z_order" in DELTA_CAPABILITY.features

    def test_delta_platform_support(self):

        expected = {
            "databricks": SupportLevel.NATIVE,
            "duckdb": SupportLevel.EXTENSION,
            "datafusion": SupportLevel.EXTENSION,
            "trino": SupportLevel.EXTENSION,
            "presto": SupportLevel.EXTENSION,
            "spark": SupportLevel.EXTENSION,
            "emr-serverless": SupportLevel.EXTENSION,
            "dataproc": SupportLevel.EXTENSION,
            "dataproc-serverless": SupportLevel.EXTENSION,
            "fabric-spark": SupportLevel.NATIVE,
            "quanton": SupportLevel.EXTENSION,
        }
        for platform, level in expected.items():
            assert DELTA_CAPABILITY.supported_platforms[platform] == level, (
                f"Expected {level} delta support for {platform}"
            )

        assert DELTA_CAPABILITY.supported_platforms["synapse-spark"] == SupportLevel.NATIVE

        for platform in ["snowflake", "clickhouse", "redshift", "bigquery", "fabric-lakehouse"]:
            assert platform not in DELTA_CAPABILITY.supported_platforms, (
                f"{platform} should not be registered in native capabilities"
            )

        assert "lakesail" not in DELTA_CAPABILITY.supported_platforms

    def test_iceberg_capability(self):

        assert ICEBERG_CAPABILITY.format_name == "iceberg"
        assert ICEBERG_CAPABILITY.display_name == "Apache Iceberg"
        assert "partition_evolution" in ICEBERG_CAPABILITY.features
        assert "hidden_partitioning" in ICEBERG_CAPABILITY.features

    def test_iceberg_platform_support(self):

        expected = {
            "duckdb": SupportLevel.EXPERIMENTAL,
            "datafusion": SupportLevel.EXTENSION,
            "trino": SupportLevel.EXTENSION,
            "presto": SupportLevel.EXTENSION,
            "spark": SupportLevel.EXTENSION,
            "emr-serverless": SupportLevel.EXTENSION,
            "dataproc": SupportLevel.EXTENSION,
            "dataproc-serverless": SupportLevel.EXTENSION,
            "quanton": SupportLevel.NATIVE,
        }
        for platform, level in expected.items():
            assert ICEBERG_CAPABILITY.supported_platforms[platform] == level, (
                f"Expected {level} iceberg support for {platform}"
            )

        assert "lakesail" not in ICEBERG_CAPABILITY.supported_platforms

    def test_hudi_capability(self):

        assert HUDI_CAPABILITY.format_name == "hudi"
        assert HUDI_CAPABILITY.display_name == "Apache Hudi"
        assert "time_travel" in HUDI_CAPABILITY.features
        assert "compaction" in HUDI_CAPABILITY.features
        assert "copy_on_write" in HUDI_CAPABILITY.features
        assert "merge_on_read" in HUDI_CAPABILITY.features

    def test_hudi_platform_support(self):

        expected = {
            "spark": SupportLevel.EXTENSION,
            "quanton": SupportLevel.NATIVE,
            "emr-serverless": SupportLevel.EXTENSION,
            "dataproc": SupportLevel.EXTENSION,
            "dataproc-serverless": SupportLevel.EXTENSION,
        }
        for platform, level in expected.items():
            assert HUDI_CAPABILITY.supported_platforms[platform] == level, (
                f"Expected {level} hudi support for {platform}"
            )

        assert "databricks" not in HUDI_CAPABILITY.supported_platforms

    def test_hudi_in_registry(self):

        assert "hudi" in CAPABILITIES_REGISTRY
        assert CAPABILITIES_REGISTRY["hudi"] is HUDI_CAPABILITY

    def test_all_formats_in_registry(self):

        expected_formats = {"parquet", "delta", "iceberg", "hudi", "vortex", "ducklake"}
        assert set(CAPABILITIES_REGISTRY.keys()) == expected_formats


class TestPlatformFormatPreferences:
    def test_trino_preferences(self):

        prefs = PLATFORM_FORMAT_PREFERENCES["trino"]
        assert prefs.index("iceberg") < prefs.index("delta")
        assert prefs.index("delta") < prefs.index("parquet")

    def test_presto_preferences(self):

        assert PLATFORM_FORMAT_PREFERENCES["presto"] == PLATFORM_FORMAT_PREFERENCES["trino"]

    def test_spark_preferences(self):

        prefs = PLATFORM_FORMAT_PREFERENCES["spark"]
        assert prefs[0] == "delta"
        assert "hudi" in prefs
        assert "iceberg" in prefs

    def test_emr_serverless_preferences(self):

        prefs = PLATFORM_FORMAT_PREFERENCES["emr-serverless"]
        assert "delta" in prefs
        assert "iceberg" in prefs
        assert "hudi" in prefs

    def test_synapse_spark_preferences(self):

        prefs = PLATFORM_FORMAT_PREFERENCES["synapse-spark"]
        assert prefs[0] == "delta"
        assert "iceberg" in prefs
        assert "parquet" in prefs

    def test_athena_spark_preferences(self):

        prefs = PLATFORM_FORMAT_PREFERENCES["athena-spark"]
        assert prefs[0] == "delta"
        assert "iceberg" in prefs
        assert "parquet" in prefs

    def test_fabric_lakehouse_preferences(self):

        prefs = PLATFORM_FORMAT_PREFERENCES["fabric-lakehouse"]
        assert prefs[0] == "parquet"
        assert "delta" not in prefs

    def test_fabric_warehouse_preferences(self):

        prefs = PLATFORM_FORMAT_PREFERENCES["fabric_dw"]
        assert prefs[0] == "parquet"
        assert "delta" not in prefs
        assert "iceberg" not in prefs

    def test_quanton_preferences(self):

        prefs = PLATFORM_FORMAT_PREFERENCES["quanton"]
        assert "iceberg" in prefs
        assert "hudi" in prefs
        assert "delta" in prefs

    def test_lakesail_preferences(self):

        prefs = PLATFORM_FORMAT_PREFERENCES["lakesail"]
        assert prefs[0] == "parquet"
        assert "delta" not in prefs
        assert "iceberg" not in prefs
        assert "hudi" not in prefs

    def test_snowflake_native_preferences_exclude_delta_or_iceberg(self):

        prefs = PLATFORM_FORMAT_PREFERENCES["snowflake"]
        assert "parquet" in prefs
        assert "delta" not in prefs
        assert "iceberg" not in prefs

    def test_bigquery_native_preferences_exclude_delta(self):

        prefs = PLATFORM_FORMAT_PREFERENCES["bigquery"]
        assert prefs[0] == "parquet"
        assert prefs.index("tbl") < prefs.index("csv")
        assert "parquet" in prefs
        assert "delta" not in prefs

    def test_redshift_native_preferences_exclude_delta(self):

        prefs = PLATFORM_FORMAT_PREFERENCES["redshift"]
        assert prefs.index("tbl") < prefs.index("parquet")
        assert "parquet" in prefs
        assert "delta" not in prefs

    def test_clickhouse_native_preferences_exclude_delta_or_iceberg(self):

        prefs = PLATFORM_FORMAT_PREFERENCES["clickhouse"]
        assert "parquet" in prefs
        assert "delta" not in prefs
        assert "iceberg" not in prefs


class TestGetSupportedFormats:
    def test_duckdb_supported_formats(self):

        formats = get_supported_formats("duckdb")
        assert "parquet" in formats
        assert "delta" in formats
        assert "tbl" in formats

        assert formats.index("tbl") < formats.index("parquet")

    def test_pg_duckdb_supported_formats(self):

        formats = get_supported_formats("pg_duckdb")
        assert formats == ["tbl", "parquet", "csv"]
        assert "delta" not in formats
        assert "iceberg" not in formats

    def test_datafusion_supported_formats(self):

        formats = get_supported_formats("datafusion")
        assert "parquet" in formats
        assert "delta" in formats
        assert "iceberg" in formats
        assert "tbl" in formats

    def test_databricks_supported_formats(self):

        formats = get_supported_formats("databricks")
        assert "delta" in formats
        assert "parquet" in formats

        assert formats.index("delta") < formats.index("parquet")

    def test_trino_supported_formats(self):

        formats = get_supported_formats("trino")
        assert "iceberg" in formats
        assert "delta" in formats
        assert "parquet" in formats

    def test_spark_supported_formats(self):

        formats = get_supported_formats("spark")
        assert "delta" in formats
        assert "iceberg" in formats
        assert "hudi" in formats
        assert "parquet" in formats

    def test_quanton_supported_formats(self):

        formats = get_supported_formats("quanton")
        assert "iceberg" in formats
        assert "hudi" in formats
        assert "delta" in formats

    def test_snowflake_supported_formats(self):

        formats = get_supported_formats("snowflake")
        assert "parquet" in formats
        assert "delta" not in formats
        assert "iceberg" not in formats

    def test_snowflake_external_supported_formats(self):

        formats = get_supported_formats(
            "snowflake",
            table_mode="external",
            platform_config={
                "staging_root": "s3://bucket/prefix",
                "iceberg_external_volume": "BENCHBOX_VOL",
            },
        )
        assert formats[:3] == ["iceberg", "delta", "parquet"]

    def test_bigquery_external_supported_formats(self):

        formats = get_supported_formats(
            "bigquery",
            table_mode="external",
            platform_config={
                "staging_root": "gs://bucket/prefix",
                "biglake_connection": "project.us.conn",
            },
        )
        assert formats[:3] == ["delta", "iceberg", "parquet"]

    def test_bigquery_external_iceberg_requires_biglake_connection(self):

        formats = get_supported_formats(
            "bigquery",
            table_mode="external",
            platform_config={"staging_root": "gs://bucket/prefix"},
        )
        assert "iceberg" not in formats
        assert "delta" not in formats

    def test_redshift_external_supported_formats(self):

        formats = get_supported_formats(
            "redshift",
            table_mode="external",
            platform_config={
                "staging_root": "s3://bucket/prefix",
                "iam_role": "arn:aws:iam::123456789012:role/benchbox",
            },
        )
        assert formats[:3] == ["delta", "iceberg", "parquet"]

    def test_redshift_external_iceberg_requires_iam_role(self):

        formats = get_supported_formats(
            "redshift",
            table_mode="external",
            platform_config={"staging_root": "s3://bucket/prefix"},
        )
        assert "iceberg" not in formats
        assert "delta" not in formats

    def test_clickhouse_cloud_external_supported_formats(self):

        formats = get_supported_formats(
            "ClickHouse Cloud",
            table_mode="external",
            platform_config={"s3_staging_url": "s3://bucket/prefix/"},
        )
        assert formats[:2] == ["iceberg", "parquet"]
        assert "iceberg" not in get_supported_formats("ClickHouse (Local)", table_mode="external")

    def test_bigquery_external_without_biglake_connection_falls_back_to_parquet(self):

        formats = get_supported_formats(
            "bigquery",
            table_mode="external",
            platform_config={"staging_root": "gs://bucket/prefix"},
        )
        assert formats == ["parquet", "tbl", "csv"]

    def test_snowflake_external_without_iceberg_volume_falls_back_to_delta_then_parquet(self):

        formats = get_supported_formats(
            "snowflake",
            table_mode="external",
            platform_config={"staging_root": "s3://bucket/prefix"},
        )
        assert formats[:2] == ["delta", "parquet"]

    def test_athena_native_supported_formats_stay_text_only(self):

        assert get_supported_formats("Athena") == ["tbl", "csv"]

    def test_athena_external_supported_formats_require_parquet(self):

        assert get_supported_formats("Athena", table_mode="external") == ["parquet"]

    def test_unknown_platform(self):

        formats = get_supported_formats("unknown_platform")
        assert formats == []


class TestGetPreferredFormat:
    def test_duckdb_preferred_format(self):

        preferred = get_preferred_format("duckdb")
        assert preferred == "tbl"

    def test_duckdb_with_available_formats(self):

        preferred = get_preferred_format("duckdb", ["tbl", "csv"])
        assert preferred == "tbl"

        preferred = get_preferred_format("duckdb", ["tbl", "parquet"])
        assert preferred == "tbl"

    def test_pg_duckdb_preferred_format(self):

        assert get_preferred_format("pg_duckdb") == "tbl"
        assert get_preferred_format("pg_duckdb", ["parquet", "tbl"]) == "tbl"

    def test_databricks_preferred_format(self):

        preferred = get_preferred_format("databricks")
        assert preferred == "delta"

    def test_athena_native_preferred_format_avoids_parquet(self):

        preferred = get_preferred_format("Athena", ["parquet", "tbl"])
        assert preferred == "tbl"

    def test_athena_external_preferred_format_requires_parquet(self):

        preferred = get_preferred_format("Athena", ["tbl", "parquet"], table_mode="external")
        assert preferred == "parquet"

    def test_snowflake_external_preferred_format(self):

        preferred = get_preferred_format(
            "snowflake",
            table_mode="external",
            platform_config={
                "staging_root": "s3://bucket/prefix",
                "iceberg_external_volume": "BENCHBOX_VOL",
            },
        )
        assert preferred == "iceberg"

    def test_trino_preferred_format(self):

        preferred = get_preferred_format("trino")
        assert preferred == "iceberg"

    def test_fallback_to_tbl(self):

        preferred = get_preferred_format("duckdb", [])
        assert preferred == "tbl"

    def test_fallback_to_first_available(self):

        preferred = get_preferred_format("unknown_platform", ["custom_format"])
        assert preferred == "custom_format"


class TestIsFormatSupported:
    def test_parquet_support(self):

        assert is_format_supported("duckdb", "parquet") is True
        assert is_format_supported("datafusion", "parquet") is True
        assert is_format_supported("pg_duckdb", "parquet") is True
        assert is_format_supported("unknown_platform", "parquet") is False

    def test_delta_support(self):

        assert is_format_supported("databricks", "delta") is True
        assert is_format_supported("duckdb", "delta") is True
        assert is_format_supported("pg_duckdb", "delta") is False
        assert is_format_supported("datafusion", "delta") is True
        assert is_format_supported("trino", "delta") is True
        assert is_format_supported("snowflake", "delta") is False
        assert is_format_supported("bigquery", "delta") is False
        assert is_format_supported("redshift", "delta") is False
        assert (
            is_format_supported(
                "snowflake",
                "delta",
                table_mode="external",
                platform_config={"staging_root": "s3://bucket/prefix"},
            )
            is True
        )
        assert (
            is_format_supported(
                "bigquery",
                "delta",
                table_mode="external",
                platform_config={
                    "staging_root": "gs://bucket/prefix",
                    "biglake_connection": "project.us.conn",
                },
            )
            is True
        )
        assert (
            is_format_supported(
                "redshift",
                "delta",
                table_mode="external",
                platform_config={
                    "staging_root": "s3://bucket/prefix",
                    "iam_role": "arn:aws:iam::123456789012:role/benchbox",
                },
            )
            is True
        )
        assert is_format_supported("bigquery", "delta", table_mode="external") is False
        assert is_format_supported("sqlite", "delta") is False

    def test_iceberg_support(self):

        assert is_format_supported("duckdb", "iceberg") is True
        assert is_format_supported("pg_duckdb", "iceberg") is False
        assert is_format_supported("trino", "iceberg") is True
        assert is_format_supported("snowflake", "iceberg") is False
        assert is_format_supported("clickhouse", "iceberg") is False
        assert (
            is_format_supported(
                "snowflake",
                "iceberg",
                table_mode="external",
                platform_config={
                    "staging_root": "s3://bucket/prefix",
                    "iceberg_external_volume": "BENCHBOX_VOL",
                },
            )
            is True
        )
        assert is_format_supported("snowflake", "iceberg", table_mode="external") is False
        assert (
            is_format_supported(
                "ClickHouse Cloud",
                "iceberg",
                table_mode="external",
                platform_config={"s3_staging_url": "s3://bucket/prefix/"},
            )
            is True
        )
        assert is_format_supported("sqlite", "iceberg") is False
        assert is_format_supported("postgresql", "iceberg") is False

    def test_hudi_support(self):

        assert is_format_supported("spark", "hudi") is True
        assert is_format_supported("quanton", "hudi") is True
        assert is_format_supported("emr-serverless", "hudi") is True
        assert is_format_supported("lakesail", "hudi") is False
        assert is_format_supported("databricks", "hudi") is False
        assert is_format_supported("duckdb", "hudi") is False

    def test_legacy_formats_always_supported(self):

        assert is_format_supported("any_platform", "tbl") is True
        assert is_format_supported("any_platform", "csv") is True
        assert is_format_supported("any_platform", "dat") is True

    def test_unknown_format(self):

        assert is_format_supported("duckdb", "unknown_format") is False


class TestGetFormatCapability:
    def test_get_parquet_capability(self):

        cap = get_format_capability("parquet")
        assert cap is not None
        assert cap.format_name == "parquet"

    def test_get_delta_capability(self):

        cap = get_format_capability("delta")
        assert cap is not None
        assert cap.format_name == "delta"

    def test_get_hudi_capability(self):

        cap = get_format_capability("hudi")
        assert cap is not None
        assert cap.format_name == "hudi"
        assert cap.display_name == "Apache Hudi"

    def test_get_unknown_capability(self):

        cap = get_format_capability("unknown")
        assert cap is None


class TestHasFeature:
    def test_parquet_features(self):

        assert has_feature("parquet", "predicate_pushdown") is True
        assert has_feature("parquet", "column_pruning") is True
        assert has_feature("parquet", "time_travel") is False

    def test_delta_features(self):

        assert has_feature("delta", "time_travel") is True
        assert has_feature("delta", "z_order") is True
        assert has_feature("delta", "predicate_pushdown") is False

    def test_iceberg_features(self):

        assert has_feature("iceberg", "partition_evolution") is True
        assert has_feature("iceberg", "time_travel") is True
        assert has_feature("iceberg", "z_order") is False

    def test_hudi_features(self):

        assert has_feature("hudi", "time_travel") is True
        assert has_feature("hudi", "compaction") is True
        assert has_feature("hudi", "copy_on_write") is True
        assert has_feature("hudi", "merge_on_read") is True
        assert has_feature("hudi", "z_order") is False

    def test_unknown_format_features(self):

        assert has_feature("unknown", "any_feature") is False


