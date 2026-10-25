# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from benchbox.core.platform_manifest import PLATFORM_MANIFEST_BY_KEY
from benchbox.core.platform_registry import SUPPORT_STATUS_VALUES, PlatformRegistry
from benchbox.core.schemas import LibraryInfo

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

KNOWN_PAID_PLATFORM_COST_CLASSES = {
    "athena": "paid_credits",
    "athena-spark": "paid_compute",
    "bigquery": "paid_credits",
    "clickhouse-cloud": "paid_compute",
    "databend": "paid_compute",
    "databricks": "paid_credits",
    "databricks-df": "paid_credits",
    "dataproc": "paid_compute",
    "dataproc-serverless": "paid_compute",
    "emr-serverless": "paid_compute",
    "fabric-lakehouse": "paid_compute",
    "fabric-spark": "paid_compute",
    "fabric_dw": "paid_compute",
    "firebolt": "paid_compute",
    "glue": "paid_compute",
    "motherduck": "paid_credits",
    "pg-duckdb": "paid_credits",
    "quanton": "paid_compute",
    "redshift": "paid_compute",
    "singlestore": "paid_compute",
    "snowflake": "paid_credits",
    "snowpark-connect": "paid_credits",
    "starburst": "paid_compute",
    "synapse": "paid_compute",
    "synapse-spark": "paid_compute",
}


class TestPlatformRegistry:
    def setup_method(self):
        PlatformRegistry.clear_cache()

    @pytest.mark.parametrize("error_type", [None, ImportError, OSError])
    def test_detect_library_restores_import_cwd(self, tmp_path, error_type):
        original_cwd = Path.cwd()

        def import_with_native_side_effect(module_name):
            assert module_name == "chdb"
            os.chdir(tmp_path)
            if error_type is not None:
                raise error_type("native library unavailable")
            return SimpleNamespace(__version__="1.2.3")

        try:
            with patch(
                "benchbox.core.platform_registry.importlib.import_module", side_effect=import_with_native_side_effect
            ):
                info = PlatformRegistry.detect_library({"name": "chdb"})
            assert Path.cwd() == original_cwd
            assert info.installed is (error_type is None)
            assert info.version == ("1.2.3" if error_type is None else None)
            assert info.import_error == (None if error_type is None else "native library unavailable")
        finally:
            os.chdir(original_cwd)

    def test_get_all_platform_metadata(self):

        metadata = PlatformRegistry.get_all_platform_metadata()

        assert isinstance(metadata, dict)

        base_platforms = {"duckdb", "sqlite"}
        assert base_platforms.issubset(set(metadata.keys()))

        duckdb_spec = metadata["duckdb"]
        required_keys = [
            "display_name",
            "description",
            "category",
            "libraries",
            "requirements",
            "installation_command",
            "adoption",
            "supports",
        ]
        for key in required_keys:
            assert key in duckdb_spec, f"Missing required key: {key}"

        assert duckdb_spec["support_status"] == "stable"
        assert duckdb_spec["display_name"] == "DuckDB"
        assert duckdb_spec["category"] == "analytical"
        assert duckdb_spec["adoption"] == "mainstream"
        assert isinstance(duckdb_spec["libraries"], list)
        assert len(duckdb_spec["libraries"]) > 0

    def test_get_platform_names_matches_metadata_keys_and_returns_a_fresh_list(self):
        metadata_names = list(PlatformRegistry.get_all_platform_metadata())

        with patch("benchbox.core.platform_registry.deepcopy", side_effect=AssertionError("unexpected deepcopy")):
            names = PlatformRegistry.get_platform_names()
            names.pop()

            assert PlatformRegistry.get_platform_names() == metadata_names

    def test_get_all_platform_metadata_returns_deeply_isolated_copy(self):
        metadata1 = PlatformRegistry.get_all_platform_metadata()
        metadata2 = PlatformRegistry.get_all_platform_metadata()

        assert metadata1 == metadata2
        assert metadata1 is not metadata2

        metadata1["test"] = "modified"
        metadata1["duckdb"]["capabilities"]["supports_sql"] = False
        metadata1["duckdb"]["capabilities"]["default_mode"] = "dataframe"
        metadata1["duckdb"]["requirements"].append("cache-poison-requirement")
        metadata1["duckdb"]["libraries"][0]["name"] = "cache-poison-library"
        metadata1["duckdb"]["supports"].append("cache-poison-feature")

        assert "test" not in metadata2
        fresh = PlatformRegistry.get_all_platform_metadata()["duckdb"]
        assert fresh["capabilities"]["supports_sql"] is True
        assert fresh["capabilities"]["default_mode"] == "sql"
        assert "cache-poison-requirement" not in fresh["requirements"]
        assert fresh["libraries"][0]["name"] == "duckdb"
        assert "cache-poison-feature" not in fresh["supports"]
        assert PlatformRegistry.supports_mode("duckdb", "sql") is True
        assert PlatformRegistry.get_default_mode("duckdb") == "sql"

        manifest_entry = PLATFORM_MANIFEST_BY_KEY["duckdb"]
        assert manifest_entry.capabilities["supports_sql"] is True
        assert manifest_entry.capabilities["default_mode"] == "sql"
        assert "cache-poison-requirement" not in manifest_entry.metadata["requirements"]
        assert manifest_entry.metadata["libraries"][0]["name"] == "duckdb"

    def test_sibling_metadata_getters_return_isolated_nested_lists(self):
        info = PlatformRegistry.get_platform_info("duckdb")
        capabilities = PlatformRegistry.get_platform_capabilities("duckdb")
        assert info is not None
        assert capabilities is not None

        info.requirements.append("info-poison-requirement")
        info.supports.append("info-poison-feature")
        capabilities.deployment_modes["local"].dependencies.append("capability-poison-dependency")

        fresh_info = PlatformRegistry.get_platform_info("duckdb")
        fresh_capabilities = PlatformRegistry.get_platform_capabilities("duckdb")
        assert fresh_info is not None
        assert fresh_capabilities is not None
        assert "info-poison-requirement" not in fresh_info.requirements
        assert "info-poison-feature" not in fresh_info.supports
        assert "capability-poison-dependency" not in fresh_capabilities.deployment_modes["local"].dependencies

        fresh_metadata = PlatformRegistry.get_all_platform_metadata()["duckdb"]
        assert "info-poison-requirement" not in fresh_metadata["requirements"]
        assert "info-poison-feature" not in fresh_metadata["supports"]
        assert (
            "capability-poison-dependency"
            not in fresh_metadata["capabilities"]["deployment_modes"]["local"]["dependencies"]
        )

    @patch("benchbox.core.platform_registry.importlib.import_module")
    def test_detect_library_success(self, mock_import):

        mock_module = Mock()
        mock_module.__version__ = "1.2.3"
        mock_import.return_value = mock_module

        lib_spec = {"name": "test_lib", "required": True}
        lib_info = PlatformRegistry.detect_library(lib_spec)

        assert isinstance(lib_info, LibraryInfo)
        assert lib_info.name == "test_lib"
        assert lib_info.version == "1.2.3"
        assert lib_info.installed is True
        assert lib_info.import_error is None

    @patch("benchbox.core.platform_registry.importlib.import_module")
    def test_detect_library_failure(self, mock_import):

        mock_import.side_effect = ImportError("No module named 'missing_lib'")

        lib_spec = {"name": "missing_lib", "required": True}
        lib_info = PlatformRegistry.detect_library(lib_spec)

        assert isinstance(lib_info, LibraryInfo)
        assert lib_info.name == "missing_lib"
        assert lib_info.version is None
        assert lib_info.installed is False
        assert "No module named" in lib_info.import_error

    @patch("benchbox.core.platform_registry.importlib.import_module")
    def test_detect_library_with_import_name(self, mock_import):

        mock_module = Mock()
        mock_module.__version__ = "2.0.0"
        mock_import.return_value = mock_module

        lib_spec = {"name": "psycopg", "import_name": "psycopg", "required": True}
        lib_info = PlatformRegistry.detect_library(lib_spec)

        mock_import.assert_called_once_with("psycopg")
        assert lib_info.name == "psycopg"
        assert lib_info.version == "2.0.0"
        assert lib_info.installed is True

    @patch("benchbox.core.platform_registry.importlib.import_module")
    def test_detect_library_no_version(self, mock_import):

        mock_module = Mock()
        del mock_module.__version__
        mock_import.return_value = mock_module

        lib_spec = {"name": "sqlite3", "required": True}
        lib_info = PlatformRegistry.detect_library(lib_spec)

        assert lib_info.name == "sqlite3"
        assert lib_info.version is None
        assert lib_info.installed is True
        assert lib_info.import_error is None

    def test_platform_boundary_separation(self):

        metadata = PlatformRegistry.get_all_platform_metadata()

        for _platform_name, platform_spec in metadata.items():
            assert "display_name" in platform_spec
            assert "description" in platform_spec
            assert "category" in platform_spec
            assert "libraries" in platform_spec
            assert "requirements" in platform_spec
            assert "installation_command" in platform_spec
            assert "adoption" in platform_spec
            assert "supports" in platform_spec
            assert "support_status" in platform_spec

            for lib_spec in platform_spec["libraries"]:
                assert "name" in lib_spec
                assert "required" in lib_spec

    def test_metadata_consistency_with_get_platform_info(self):

        metadata = PlatformRegistry.get_all_platform_metadata()

        for platform_name in metadata:
            platform_info = PlatformRegistry.get_platform_info(platform_name)

            if platform_info:
                assert platform_info.name == platform_name
                assert platform_info.display_name == metadata[platform_name]["display_name"]
                assert platform_info.description == metadata[platform_name]["description"]
                assert platform_info.category == metadata[platform_name]["category"]

    def test_platform_categories(self):

        metadata = PlatformRegistry.get_all_platform_metadata()

        base_categories = {
            "duckdb": "analytical",
            "sqlite": "embedded",
        }

        for platform_name, expected_category in base_categories.items():
            assert metadata[platform_name]["category"] == expected_category

    def test_adoption_tiers(self):

        metadata = PlatformRegistry.get_all_platform_metadata()

        expected_tiers = {
            "mainstream": {"duckdb", "snowflake", "bigquery", "databricks", "spark"},
            "established": {
                "clickhouse",
                "redshift",
                "trino",
                "synapse",
                "athena",
                "polars",
                "postgresql",
                "pyspark",
            },
            "emerging": {"datafusion", "motherduck", "firebolt", "starburst", "pandas"},
            "niche": {
                "sqlite",
                "presto",
                "timescaledb",
                "influxdb",
                "cudf",
                "dask",
                "fabric_dw",
                "glue",
                "emr-serverless",
                "athena-spark",
                "dataproc",
                "dataproc-serverless",
                "fabric-spark",
                "synapse-spark",
                "snowpark-connect",
                "databricks-df",
            },
        }

        valid_tiers = {"mainstream", "established", "emerging", "niche"}

        for platform_name, platform_spec in metadata.items():
            tier = platform_spec.get("adoption", "niche")
            assert tier in valid_tiers, f"{platform_name} has invalid adoption tier: {tier}"

        for tier, platforms in expected_tiers.items():
            for platform_name in platforms:
                if platform_name in metadata:
                    assert metadata[platform_name]["adoption"] == tier, (
                        f"{platform_name} should be '{tier}', got '{metadata[platform_name]['adoption']}'"
                    )

    def test_library_requirements_format(self):

        metadata = PlatformRegistry.get_all_platform_metadata()

        for platform_name, platform_spec in metadata.items():
            libraries = platform_spec["libraries"]
            assert isinstance(libraries, list)
            assert len(libraries) > 0, f"{platform_name} should have at least one library"

            for lib_spec in libraries:
                assert isinstance(lib_spec, dict)
                assert "name" in lib_spec
                assert "required" in lib_spec
                assert isinstance(lib_spec["required"], bool)

                if "import_name" in lib_spec:
                    assert isinstance(lib_spec["import_name"], str)

    def test_installation_commands_exist(self):

        metadata = PlatformRegistry.get_all_platform_metadata()

        for platform_name, platform_spec in metadata.items():
            install_cmd = platform_spec["installation_command"]
            assert isinstance(install_cmd, str)
            assert len(install_cmd) > 0, f"{platform_name} should have installation command"

            assert (
                install_cmd.startswith("uv add")
                or install_cmd.startswith("pip install")
                or install_cmd == "Built-in Python library"
            ), f"Unexpected install command for {platform_name}: {install_cmd}"

    def test_pyspark_dual_mode_metadata(self):
        metadata = PlatformRegistry.get_all_platform_metadata()
        pyspark_spec = metadata["pyspark"]

        assert pyspark_spec["capabilities"]["default_mode"] == "dataframe"
        assert pyspark_spec["capabilities"]["supports_sql"] is True
        assert pyspark_spec["capabilities"]["supports_dataframe"] is True

        caps = PlatformRegistry.get_platform_capabilities("pyspark")
        assert caps.supports_sql
        assert caps.supports_dataframe
        assert caps.default_mode == "dataframe"

    def test_platform_taxonomy_helpers_derive_from_capabilities(self):
        sql_platforms = PlatformRegistry.get_sql_platforms()
        dataframe_platforms = PlatformRegistry.get_dataframe_platforms()
        self_hosted_platforms = PlatformRegistry.get_self_hosted_platforms()

        assert "duckdb" in sql_platforms
        assert "polars" not in sql_platforms
        assert "clickhouse" not in sql_platforms
        assert "clickhouse" in PlatformRegistry.get_sql_platforms(include_deprecated=True)

        assert {"polars", "pandas", "dask", "datafusion", "pyspark"}.issubset(dataframe_platforms)
        assert "duckdb" not in dataframe_platforms

        assert {
            "clickhouse-server",
            "postgresql",
            "presto",
            "trino",
            "influxdb",
            "velox",
        }.issubset(self_hosted_platforms)
        assert "clickhouse" not in self_hosted_platforms

    def test_known_paid_platforms_expose_cost_class(self):
        for platform_name, expected_cost_class in KNOWN_PAID_PLATFORM_COST_CLASSES.items():
            caps = PlatformRegistry.get_platform_capabilities(platform_name)
            assert caps is not None
            assert caps.cost_class == expected_cost_class

        assert PlatformRegistry.get_platform_capabilities("duckdb").cost_class == "free"
        assert PlatformRegistry.get_platform_capabilities("datafusion").cost_class == "free"
        assert PlatformRegistry.get_platform_capabilities("postgresql").cost_class == "free"
        assert PlatformRegistry.get_platform_capabilities("sqlite").cost_class == "free"

    def test_all_platforms_have_exactly_one_valid_support_status(self):
        metadata = PlatformRegistry.get_all_platform_metadata()
        valid = set(SUPPORT_STATUS_VALUES)

        assert len(metadata) == 52
        for platform_name, platform_spec in metadata.items():
            assert set(platform_spec.keys()).intersection({"support_status"}) == {"support_status"}
            assert platform_spec["support_status"] in valid, f"{platform_name} has invalid support_status"

        summary = PlatformRegistry.get_platform_count_summary()
        assert sum(summary["support_status"].values()) == len(metadata)
        assert summary["support_status"] == {
            "stable": 5,
            "beta": 28,
            "experimental": 18,
            "repo_only": 0,
            "deprecated": 1,
            "document_only": 0,
        }

    def test_support_status_is_distinct_from_dependency_availability(self):
        metadata = PlatformRegistry.get_all_platform_metadata()

        assert metadata["snowflake"]["support_status"] == "beta"
        availability = PlatformRegistry.get_platform_availability()
        if "snowflake" in availability:
            assert isinstance(availability["snowflake"], bool)

    def test_platform_support_status_filtering(self):
        assert PlatformRegistry.get_platform_support_status("fabric-dw") == "beta"
        assert "clickhouse" in PlatformRegistry.get_platforms_by_support_status("deprecated")

        with pytest.raises(ValueError, match="Unknown support_status"):
            PlatformRegistry.get_platforms_by_support_status("unknown")

    @patch("benchbox.core.platform_registry.importlib.import_module")
    def test_optional_adapter_diagnostics_available(self, mock_import):
        adapter_cls = object()
        mock_import.return_value = SimpleNamespace(SnowflakeAdapter=adapter_cls)

        diagnostics = PlatformRegistry.diagnose_optional_adapter_imports(["snowflake"])

        assert diagnostics["snowflake"]["status"] == "available"
        assert diagnostics["snowflake"]["available"] is True
        assert diagnostics["snowflake"]["support_status"] == "beta"
        mock_import.assert_called_once_with("benchbox.platforms.snowflake")

    @patch("benchbox.core.platform_registry.importlib.import_module")
    def test_optional_adapter_diagnostics_missing_dependency(self, mock_import):
        mock_import.side_effect = ModuleNotFoundError(
            "No module named 'snowflake.connector'",
            name="snowflake.connector",
        )

        diagnostics = PlatformRegistry.diagnose_optional_adapter_imports(["snowflake"])

        assert diagnostics["snowflake"]["status"] == "missing_optional_dependency"
        assert diagnostics["snowflake"]["available"] is False
        assert diagnostics["snowflake"]["error_type"] == "ModuleNotFoundError"

    @patch("benchbox.core.platform_registry.importlib.import_module")
    def test_optional_adapter_diagnostics_missing_adapter_module_is_broken(self, mock_import):
        mock_import.side_effect = ModuleNotFoundError(
            "No module named 'benchbox.platforms.snowflake'",
            name="benchbox.platforms.snowflake",
        )

        diagnostics = PlatformRegistry.diagnose_optional_adapter_imports(["snowflake"])

        assert diagnostics["snowflake"]["status"] == "broken_adapter_import"
        assert diagnostics["snowflake"]["error_type"] == "ModuleNotFoundError"

    @patch("benchbox.core.platform_registry.importlib.import_module")
    def test_optional_adapter_diagnostics_native_load_failure(self, mock_import):
        mock_import.side_effect = OSError("dlopen(libarrow.dylib): image not found")

        diagnostics = PlatformRegistry.diagnose_optional_adapter_imports(["datafusion"])

        assert diagnostics["datafusion"]["status"] == "native_library_load_failure"
        assert diagnostics["datafusion"]["error_type"] == "OSError"

    @patch("benchbox.core.platform_registry.importlib.import_module")
    def test_optional_adapter_diagnostics_broken_adapter_import(self, mock_import):
        mock_import.return_value = SimpleNamespace()

        diagnostics = PlatformRegistry.diagnose_optional_adapter_imports(["snowflake"])

        assert diagnostics["snowflake"]["status"] == "broken_adapter_import"
        assert "SnowflakeAdapter" in diagnostics["snowflake"]["error_message"]

    @patch("benchbox.core.platform_registry.importlib.import_module")
    def test_optional_adapter_diagnostics_deprecated_platform(self, mock_import):
        diagnostics = PlatformRegistry.diagnose_optional_adapter_imports(["clickhouse"])

        assert diagnostics["clickhouse"]["status"] == "deprecated_platform"
        assert diagnostics["clickhouse"]["support_status"] == "deprecated"
        mock_import.assert_not_called()

    def test_public_docs_platform_and_benchmark_count_markers_match_registries(self):
        from benchbox.core.benchmark_registry import get_all_benchmarks, list_public_benchmark_ids

        summary = PlatformRegistry.get_platform_count_summary()
        platform_marker = (
            f"Platform registry: **{summary['total']}** metadata entries; "
            f"**{summary['sql_capable']}** SQL-capable; "
            f"**{summary['dataframe_capable']}** DataFrame-capable; "
            f"**{summary['dual_mode']}** dual-mode; "
            f"support status counts: stable={summary['support_status']['stable']}, "
            f"beta={summary['support_status']['beta']}, "
            f"experimental={summary['support_status']['experimental']}, "
            f"deprecated={summary['support_status']['deprecated']}."
        )
        benchmark_marker = (
            f"Benchmark registry: **{len(get_all_benchmarks())}** metadata entries; "
            f"**{len(list_public_benchmark_ids())}** public discovery entries."
        )

        readme = Path("README.md").read_text(encoding="utf-8")
        comparison_matrix = Path("docs/platforms/comparison-matrix.md").read_text(encoding="utf-8")

        assert platform_marker in readme
        assert benchmark_marker in readme
        assert platform_marker in comparison_matrix

    def test_requires_cloud_storage_for_cloud_platforms(self):

        cloud_platforms = ["databricks", "bigquery", "snowflake", "redshift"]

        for platform_name in cloud_platforms:
            assert PlatformRegistry.requires_cloud_storage(platform_name), (
                f"{platform_name} should require cloud storage"
            )

    def test_requires_cloud_storage_for_local_platforms(self):

        local_platforms = ["duckdb", "sqlite", "clickhouse"]

        for platform_name in local_platforms:
            assert not PlatformRegistry.requires_cloud_storage(platform_name), (
                f"{platform_name} should not require cloud storage"
            )

    def test_requires_cloud_storage_case_insensitive(self):

        assert PlatformRegistry.requires_cloud_storage("databricks")
        assert PlatformRegistry.requires_cloud_storage("Databricks")
        assert PlatformRegistry.requires_cloud_storage("DATABRICKS")

    def test_requires_cloud_storage_unknown_platform(self):
        assert not PlatformRegistry.requires_cloud_storage("unknown_platform")
        assert not PlatformRegistry.requires_cloud_storage("nonexistent")

    def test_get_cloud_path_examples_databricks(self):

        examples = PlatformRegistry.get_cloud_path_examples("databricks")

        assert isinstance(examples, list)
        assert len(examples) > 0

        path_prefixes = [example.split("://")[0] + "://" if "://" in example else "" for example in examples]
        assert any("dbfs:" in example for example in examples), "Should include dbfs: examples"
        assert any("s3://" in prefix for prefix in path_prefixes), "Should include S3 examples"

    def test_get_cloud_path_examples_bigquery(self):

        examples = PlatformRegistry.get_cloud_path_examples("bigquery")

        assert isinstance(examples, list)
        assert len(examples) > 0

        assert all("gs://" in example for example in examples), "BigQuery examples should all use gs://"

    def test_get_cloud_path_examples_snowflake(self):

        examples = PlatformRegistry.get_cloud_path_examples("snowflake")

        assert isinstance(examples, list)
        assert len(examples) > 0

        path_strings = " ".join(examples)
        assert "s3://" in path_strings, "Should include S3 examples"
        assert "azure://" in path_strings or "gcs://" in path_strings, "Should include other cloud providers"

    def test_get_cloud_path_examples_redshift(self):

        examples = PlatformRegistry.get_cloud_path_examples("redshift")

        assert isinstance(examples, list)
        assert len(examples) > 0

        assert all("s3://" in example for example in examples), "Redshift examples should all use s3://"

    def test_get_cloud_path_examples_case_insensitive(self):

        examples_lower = PlatformRegistry.get_cloud_path_examples("databricks")
        examples_upper = PlatformRegistry.get_cloud_path_examples("DATABRICKS")
        examples_mixed = PlatformRegistry.get_cloud_path_examples("Databricks")

        assert examples_lower == examples_upper
        assert examples_lower == examples_mixed

    def test_get_cloud_path_examples_local_platform(self):

        assert PlatformRegistry.get_cloud_path_examples("duckdb") == []
        assert PlatformRegistry.get_cloud_path_examples("sqlite") == []
        assert PlatformRegistry.get_cloud_path_examples("clickhouse") == []

    def test_get_cloud_path_examples_unknown_platform(self):

        assert PlatformRegistry.get_cloud_path_examples("unknown_platform") == []
        assert PlatformRegistry.get_cloud_path_examples("nonexistent") == []

    def test_all_expected_platforms_register_successfully(self):
        expected_base_platforms = {"duckdb", "sqlite"}
        expected_cloud_platforms = {"databricks", "bigquery", "snowflake", "redshift", "clickhouse"}

        registered = set(PlatformRegistry.get_available_platforms())

        assert expected_base_platforms.issubset(registered), (
            f"Base platforms missing: {expected_base_platforms - registered}"
        )

        for platform in expected_cloud_platforms:
            try:
                is_available = PlatformRegistry.is_platform_available(platform)
                if is_available:
                    assert platform in registered, f"{platform} reports available but not in registered list"
            except ImportError as e:
                assert "circular" not in str(e).lower(), (
                    f"{platform} has circular import issue: {e}. "
                    "This likely means a top-level import was added that creates circular dependency. "
                    "Use lazy imports (import inside functions) to break the cycle."
                )


class TestPlatformRegistryBoundaries:
    def test_cli_uses_public_methods_only(self):
        from benchbox.cli.platform import PlatformManager

        manager = PlatformManager()

        metadata = manager.platform_registry
        assert isinstance(metadata, dict)

        lib_spec = {"name": "test", "required": True}
        with patch("benchbox.core.platform_registry.importlib.import_module") as mock_import:
            mock_import.side_effect = ImportError("test")
            lib_info = manager._detect_library(lib_spec)
            assert isinstance(lib_info, LibraryInfo)
            assert not lib_info.installed

    def test_platform_metadata_completeness(self):

        from benchbox.cli.platform import PlatformManager

        manager = PlatformManager()
        metadata = manager.platform_registry

        for platform_name, platform_spec in metadata.items():
            cli_required_fields = [
                "display_name",
                "description",
                "category",
                "libraries",
                "requirements",
                "installation_command",
                "adoption",
                "supports",
                "support_status",
            ]

            for field in cli_required_fields:
                assert field in platform_spec, f"Platform {platform_name} missing {field}"

            for lib_spec in platform_spec["libraries"]:
                assert "name" in lib_spec
                assert "required" in lib_spec


class TestPlatformDisplayNames:
    def test_all_display_names_are_non_empty(self):
        metadata = PlatformRegistry.get_all_platform_metadata()
        empty = [k for k, v in metadata.items() if not v.get("display_name")]
        assert not empty, f"Platforms with empty display_name: {empty}"

    def test_fabric_dw_display_name_includes_microsoft(self):
        registry = PlatformRegistry()
        info = registry.get_platform_info("fabric_dw")
        assert info.display_name == "Microsoft Fabric Warehouse"

    def test_athena_display_name_uses_amazon_not_aws(self):
        registry = PlatformRegistry()
        info = registry.get_platform_info("athena")
        assert info.display_name == "Amazon Athena"
        assert not info.display_name.startswith("AWS")

    def test_dataproc_display_names_use_google_cloud(self):
        registry = PlatformRegistry()
        for key in ("dataproc", "dataproc-serverless"):
            info = registry.get_platform_info(key)
            assert "Google Cloud" in info.display_name, f"{key}: expected 'Google Cloud' in '{info.display_name}'"
            assert "GCP" not in info.display_name, f"{key}: unexpected 'GCP' in '{info.display_name}'"

    def test_synapse_display_names_include_analytics(self):
        registry = PlatformRegistry()
        for key in ("synapse", "synapse-spark"):
            info = registry.get_platform_info(key)
            assert "Analytics" in info.display_name, f"{key}: expected 'Analytics' in '{info.display_name}'"

    def test_databricks_sql_display_name_disambiguates_from_dataframe(self):
        registry = PlatformRegistry()
        sql_name = registry.get_platform_info("databricks").display_name
        df_name = registry.get_platform_info("databricks-df").display_name
        assert sql_name != df_name, "databricks and databricks-df should have different display names"
        assert "SQL" in sql_name, f"databricks display_name should contain 'SQL', got '{sql_name}'"


class TestPlatformAliases:
    def test_fabric_dw_hyphen_alias_resolves_via_cli_normalizer(self):
        from benchbox.cli.platform import normalize_platform_name

        assert normalize_platform_name("fabric-dw") == "fabric_dw"

    def test_fabric_dw_underscore_still_resolves(self):
        from benchbox.cli.platform import normalize_platform_name

        assert normalize_platform_name("fabric_dw") == "fabric_dw"

    def test_fabric_dw_hyphen_resolves_via_registry(self):
        registry = PlatformRegistry()
        info = registry.get_platform_info("fabric-dw")
        assert info is not None
        assert info.display_name == "Microsoft Fabric Warehouse"

    def test_fabric_dw_and_fabric_hyphen_dw_return_same_platform(self):
        registry = PlatformRegistry()
        info_underscore = registry.get_platform_info("fabric_dw")
        info_hyphen = registry.get_platform_info("fabric-dw")
        assert info_underscore.display_name == info_hyphen.display_name
