# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import shutil
import sys
import tempfile
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner
from rich.console import Console

from benchbox.cli.platform import (
    PLATFORM_ALIASES,
    LibraryInfo,
    PlatformInfo,
    PlatformManager,
    _format_support_status,
    check_platforms,
    disable_platform,
    enable_platform,
    get_platform_manager,
    install_platform,
    list_platforms,
    normalize_platform_name,
    platform_status,
    setup_platforms,
)
from benchbox.cli.platform_readiness import PlatformReadinessResult
from benchbox.core.platform_registry import PlatformRegistry

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def reset_platform_registry_test_state() -> None:
    PlatformRegistry._adapters = {}
    PlatformRegistry._auto_registered = False
    PlatformRegistry.clear_cache()


class TestLibraryInfo:
    def test_library_info_installed(self):

        lib = LibraryInfo(name="duckdb", version="0.9.0", installed=True)
        assert lib.name == "duckdb"
        assert lib.version == "0.9.0"
        assert lib.installed is True
        assert lib.import_error is None

    def test_library_info_missing(self):

        lib = LibraryInfo(
            name="nonexistent",
            version=None,
            installed=False,
            import_error="No module named 'nonexistent'",
        )
        assert lib.name == "nonexistent"
        assert lib.version is None
        assert lib.installed is False
        assert "No module named" in lib.import_error


class TestPlatformNameNormalization:
    def test_normalize_lowercase(self):

        assert normalize_platform_name("DuckDB") == "duckdb"
        assert normalize_platform_name("PostgreSQL") == "postgresql"
        assert normalize_platform_name("CLICKHOUSE") == "clickhouse"

    def test_normalize_postgres_aliases(self):

        assert normalize_platform_name("postgres") == "postgresql"
        assert normalize_platform_name("pg") == "postgresql"
        assert normalize_platform_name("pgsql") == "postgresql"
        assert normalize_platform_name("POSTGRES") == "postgresql"
        assert normalize_platform_name("PG") == "postgresql"

    def test_normalize_trino_presto_aliases(self):

        assert normalize_platform_name("trinodb") == "trino"
        assert normalize_platform_name("TrinoDB") == "trino"
        assert normalize_platform_name("prestodb") == "presto"
        assert normalize_platform_name("PrestoDB") == "presto"

    def test_normalize_clickhouse_alias(self):
        assert normalize_platform_name("ch") == "clickhouse-local"
        assert normalize_platform_name("CH") == "clickhouse-local"

    def test_normalize_bigquery_aliases(self):

        assert normalize_platform_name("bq") == "bigquery"
        assert normalize_platform_name("gbq") == "bigquery"
        assert normalize_platform_name("BQ") == "bigquery"

    def test_normalize_other_aliases(self):

        assert normalize_platform_name("duck") == "duckdb"
        assert normalize_platform_name("dbx") == "databricks"
        assert normalize_platform_name("snow") == "snowflake"
        assert normalize_platform_name("rs") == "redshift"
        assert normalize_platform_name("fusion") == "datafusion"
        assert normalize_platform_name("azure-synapse") == "synapse"
        assert normalize_platform_name("azuresynapse") == "synapse"

    def test_normalize_dataframe_platform_aliases(self):

        assert normalize_platform_name("polars-df") == "polars"
        assert normalize_platform_name("pandas-df") == "pandas"
        assert normalize_platform_name("pyspark-df") == "pyspark"
        assert normalize_platform_name("datafusion-df") == "datafusion"
        assert normalize_platform_name("dask-df") == "dask"
        assert normalize_platform_name("cudf-df") == "cudf"
        assert normalize_platform_name("lakesail-df") == "lakesail"

    def test_normalize_canonical_names_unchanged(self):
        assert normalize_platform_name("duckdb") == "duckdb"
        assert normalize_platform_name("postgresql") == "postgresql"
        assert normalize_platform_name("clickhouse") == "clickhouse"
        assert normalize_platform_name("bigquery") == "bigquery"
        assert normalize_platform_name("trino") == "trino"
        assert normalize_platform_name("presto") == "presto"

    def test_platform_aliases_dict_exists(self):

        assert "postgres" in PLATFORM_ALIASES
        assert "pg" in PLATFORM_ALIASES
        assert "ch" in PLATFORM_ALIASES
        assert "bq" in PLATFORM_ALIASES
        assert PLATFORM_ALIASES["postgres"] == "postgresql"


class TestPlatformInfo:
    def test_platform_info_complete(self):

        lib = LibraryInfo(name="duckdb", version="0.9.0", installed=True)
        platform = PlatformInfo(
            name="duckdb",
            display_name="DuckDB",
            description="High-performance analytical database",
            libraries=[lib],
            available=True,
            enabled=True,
            requirements=["duckdb>=0.8.0"],
            installation_command="uv add duckdb",
            adoption="mainstream",
            category="analytical",
        )

        assert platform.name == "duckdb"
        assert platform.display_name == "DuckDB"
        assert platform.available is True
        assert platform.enabled is True
        assert platform.adoption == "mainstream"
        assert platform.category == "analytical"
        assert len(platform.libraries) == 1
        assert platform.libraries[0].name == "duckdb"


class TestSupportStatusIsDistinctFromDriverAvailability:
    def test_registry_populates_support_status_from_the_manifest(self):
        info = PlatformRegistry.get_platform_info("duckdb")

        assert info is not None
        assert info.support_status == "stable"

    def test_an_installed_driver_does_not_imply_a_support_tier(self):
        info = PlatformRegistry.get_platform_info("quanton")

        assert info is not None
        assert info.support_status == "experimental"

    def test_a_missing_driver_does_not_downgrade_the_support_tier(self):
        info = PlatformRegistry.get_platform_info("snowflake")

        assert info is not None
        assert info.support_status == "beta"

    def test_absent_status_renders_unknown_rather_than_inventing_a_tier(self):
        assert "unknown" in _format_support_status(None)
        assert "unknown" in _format_support_status("")
        assert "stable" not in _format_support_status(None)

    def test_unrecognised_status_is_shown_verbatim_not_remapped(self):
        assert "some-future-tier" in _format_support_status("some-future-tier")

    def test_known_statuses_render_their_own_label(self):
        for status in ("stable", "beta", "experimental", "deprecated"):
            assert status in _format_support_status(status)

    def test_table_shows_both_signals_under_separate_headings(self):
        console = Console(file=StringIO(), width=200)
        manager = PlatformManager()
        manager.console = console

        manager.display_platform_status(detail=True)
        output = console.file.getvalue()

        header = next(line for line in output.splitlines() if "Platform" in line and "Category" in line)
        assert "Driver" in header
        assert "Support" in header
        assert "Status" not in header

    def test_both_signals_survive_the_narrow_default_view(self):
        console = Console(file=StringIO(), width=80)
        manager = PlatformManager()
        manager.console = console

        manager.display_platform_status()
        output = console.file.getvalue()

        header = next(line for line in output.splitlines() if "Platform" in line and "Driver" in line)
        assert "Support" in header
        assert "Status" not in header


class TestPlatformManager:
    def setup_method(self):
        reset_platform_registry_test_state()
        self.temp_dir = Path(tempfile.mkdtemp())
        self.config_path = self.temp_dir / "test_platforms.yaml"
        self.manager = PlatformManager(config_path=self.config_path)

    def teardown_method(self):
        shutil.rmtree(self.temp_dir)
        reset_platform_registry_test_state()

    def test_platform_registry_exists(self):

        registry = self.manager.platform_registry
        assert isinstance(registry, dict)
        assert "duckdb" in registry
        assert "sqlite" in registry

    def test_platform_registry_structure(self):

        duckdb_spec = self.manager.platform_registry["duckdb"]

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

        assert isinstance(duckdb_spec["libraries"], list)
        assert len(duckdb_spec["libraries"]) > 0
        assert duckdb_spec["libraries"][0]["name"] == "duckdb"
        assert duckdb_spec["libraries"][0]["required"] is True

    def test_config_loading_new_file(self):

        config = self.manager._config
        assert "enabled_platforms" in config
        assert isinstance(config["enabled_platforms"], list)
        assert len(config["enabled_platforms"]) == len(self.manager.platform_registry)

    def test_config_save_and_load(self):

        self.manager._config["enabled_platforms"] = ["duckdb", "sqlite"]
        self.manager._save_config()

        new_manager = PlatformManager(config_path=self.config_path)
        assert new_manager._config["enabled_platforms"] == ["duckdb", "sqlite"]

    @patch("importlib.import_module")
    def test_detect_library_success(self, mock_import):

        mock_module = Mock()
        mock_module.__version__ = "1.0.0"
        mock_import.return_value = mock_module

        lib_spec = {"name": "duckdb", "required": True}
        lib_info = self.manager._detect_library(lib_spec)

        assert lib_info.name == "duckdb"
        assert lib_info.version == "1.0.0"
        assert lib_info.installed is True
        assert lib_info.import_error is None

    @patch("importlib.import_module")
    def test_detect_library_failure(self, mock_import):

        mock_import.side_effect = ImportError("No module named 'missing_lib'")

        lib_spec = {"name": "missing_lib", "required": True}
        lib_info = self.manager._detect_library(lib_spec)

        assert lib_info.name == "missing_lib"
        assert lib_info.version is None
        assert lib_info.installed is False
        assert "No module named" in lib_info.import_error

    @patch.object(PlatformRegistry, "_detect_library")
    def test_detect_platforms_all_available(self, mock_detect_library):

        mock_detect_library.side_effect = lambda spec: LibraryInfo(name=spec["name"], version="1.0.0", installed=True)

        platforms = self.manager.detect_platforms()

        assert isinstance(platforms, dict)
        assert len(platforms) == len(self.manager.platform_registry)

        for _platform_name, platform_info in platforms.items():
            assert platform_info.available is True
            assert platform_info.enabled is True
            assert len(platform_info.libraries) > 0

    @patch.object(PlatformRegistry, "_detect_library")
    def test_detect_platforms_some_missing(self, mock_detect_library):

        def side_effect(spec):
            installed = spec["name"] in {"duckdb", "sqlite3"}
            return LibraryInfo(
                name=spec["name"],
                version="1.0.0" if installed else None,
                installed=installed,
                import_error=None if installed else f"No module named '{spec['name']}'",
            )

        mock_detect_library.side_effect = side_effect

        platforms = self.manager.detect_platforms()

        assert platforms["duckdb"].available is True
        assert platforms["sqlite"].available is True

    def test_get_available_platforms(self):

        with patch.object(self.manager, "detect_platforms") as mock_detect:
            mock_platforms = {
                "duckdb": PlatformInfo(
                    name="duckdb",
                    display_name="DuckDB",
                    description="",
                    libraries=[],
                    available=True,
                    enabled=True,
                    requirements=[],
                    installation_command="",
                    category="analytical",
                ),
                "missing": PlatformInfo(
                    name="missing",
                    display_name="Missing",
                    description="",
                    libraries=[],
                    available=False,
                    enabled=False,
                    requirements=[],
                    installation_command="",
                    category="database",
                ),
            }
            mock_detect.return_value = mock_platforms

            available = self.manager.get_available_platforms()
            assert available == ["duckdb"]

    def test_get_enabled_platforms(self):

        with patch.object(self.manager, "detect_platforms") as mock_detect:
            mock_platforms = {
                "duckdb": PlatformInfo(
                    name="duckdb",
                    display_name="DuckDB",
                    description="",
                    libraries=[],
                    available=True,
                    enabled=True,
                    requirements=[],
                    installation_command="",
                    category="analytical",
                ),
                "disabled": PlatformInfo(
                    name="disabled",
                    display_name="Disabled",
                    description="",
                    libraries=[],
                    available=True,
                    enabled=False,
                    requirements=[],
                    installation_command="",
                    category="database",
                ),
            }
            mock_detect.return_value = mock_platforms

            enabled = self.manager.get_enabled_platforms()
            assert enabled == ["duckdb"]

    def test_enable_platform_success(self):

        self.manager._config["enabled_platforms"] = ["duckdb"]

        with patch.object(self.manager, "detect_platforms") as mock_detect:
            mock_platforms = {
                "sqlite": PlatformInfo(
                    name="sqlite",
                    display_name="SQLite",
                    description="",
                    libraries=[],
                    available=True,
                    enabled=False,
                    requirements=[],
                    installation_command="",
                    category="embedded",
                )
            }
            mock_detect.return_value = mock_platforms

            result = self.manager.enable_platform("sqlite")
            assert result is True
            assert "sqlite" in self.manager._config["enabled_platforms"]

    def test_enable_platform_not_available(self):

        with patch.object(self.manager, "detect_platforms") as mock_detect:
            mock_platforms = {
                "missing": PlatformInfo(
                    name="missing",
                    display_name="Missing",
                    description="",
                    libraries=[],
                    available=False,
                    enabled=False,
                    requirements=[],
                    installation_command="",
                    category="database",
                )
            }
            mock_detect.return_value = mock_platforms

            result = self.manager.enable_platform("missing")
            assert result is False

    def test_enable_platform_unknown(self):

        result = self.manager.enable_platform("nonexistent")
        assert result is False

    def test_disable_platform_success(self):

        self.manager._config["enabled_platforms"] = ["duckdb", "sqlite"]

        result = self.manager.disable_platform("sqlite")
        assert result is True
        assert "sqlite" not in self.manager._config["enabled_platforms"]
        assert "duckdb" in self.manager._config["enabled_platforms"]

    def test_disable_platform_unknown(self):

        result = self.manager.disable_platform("nonexistent")
        assert result is False

    def test_is_platform_available(self):

        with patch.object(self.manager, "detect_platforms") as mock_detect:
            mock_platforms = {
                "duckdb": PlatformInfo(
                    name="duckdb",
                    display_name="DuckDB",
                    description="",
                    libraries=[],
                    available=True,
                    enabled=True,
                    requirements=[],
                    installation_command="",
                    category="analytical",
                ),
                "missing": PlatformInfo(
                    name="missing",
                    display_name="Missing",
                    description="",
                    libraries=[],
                    available=False,
                    enabled=False,
                    requirements=[],
                    installation_command="",
                    category="database",
                ),
            }
            mock_detect.return_value = mock_platforms

            assert self.manager.is_platform_available("duckdb") is True
            assert self.manager.is_platform_available("missing") is False
            assert self.manager.is_platform_available("nonexistent") is False

    def test_get_installation_guide(self):

        guide = self.manager.get_installation_guide("duckdb")

        assert guide is not None
        assert guide["platform"] == "DuckDB"
        assert guide["description"] == "Columnar OLAP engine • Single-node • In-memory"
        assert guide["installation_command"] == "uv add 'duckdb>=1.5,<2'"
        assert guide["category"] == "analytical"
        assert isinstance(guide["available"], bool)
        assert isinstance(guide["missing_libraries"], list)

    def test_get_installation_guide_unknown_platform(self):

        guide = self.manager.get_installation_guide("nonexistent")
        assert guide is None

    def test_display_platform_status_renders_rows_and_summary(self):
        stream = StringIO()
        self.manager.console = Console(file=stream, force_terminal=False, color_system=None, width=120)

        with patch.object(self.manager, "detect_platforms") as mock_detect:
            mock_detect.return_value = {
                "duckdb": PlatformInfo(
                    name="duckdb",
                    display_name="DuckDB",
                    description="Embedded OLAP",
                    libraries=[LibraryInfo(name="duckdb", version="1.0.0", installed=True)],
                    available=True,
                    enabled=True,
                    requirements=[],
                    installation_command="uv add duckdb",
                    category="analytical",
                ),
                "snowflake": PlatformInfo(
                    name="snowflake",
                    display_name="Snowflake",
                    description="Cloud warehouse",
                    libraries=[LibraryInfo(name="snowflake", version=None, installed=False)],
                    available=False,
                    enabled=False,
                    requirements=["snowflake-connector-python"],
                    installation_command="uv add snowflake-connector-python",
                    category="cloud",
                ),
                "sqlite": PlatformInfo(
                    name="sqlite",
                    display_name="SQLite",
                    description="Embedded row store",
                    libraries=[LibraryInfo(name="sqlite3", version="3.45.0", installed=True)],
                    available=True,
                    enabled=False,
                    requirements=[],
                    installation_command="builtin",
                    category="embedded",
                ),
            }

            self.manager.display_platform_status()

        output = stream.getvalue()
        assert "DuckDB" in output
        assert "Snowflake" in output
        assert "SQLite" in output
        assert "Summary:" in output
        assert "1 enabled, 2 available, 3 total" in output

    def test_display_platform_list_hides_unavailable_when_show_all_false(self):
        stream = StringIO()
        self.manager.console = Console(file=stream, force_terminal=False, color_system=None, width=120)

        with patch.object(self.manager, "detect_platforms") as mock_detect:
            mock_detect.return_value = {
                "duckdb": PlatformInfo(
                    name="duckdb",
                    display_name="DuckDB",
                    description="Embedded OLAP",
                    libraries=[],
                    available=True,
                    enabled=True,
                    requirements=[],
                    installation_command="uv add duckdb",
                    category="analytical",
                ),
                "snowflake": PlatformInfo(
                    name="snowflake",
                    display_name="Snowflake",
                    description="Cloud warehouse",
                    libraries=[],
                    available=False,
                    enabled=False,
                    requirements=["snowflake-connector-python"],
                    installation_command="uv add snowflake-connector-python",
                    category="cloud",
                ),
            }

            self.manager.display_platform_list(show_all=False)

        output = stream.getvalue()
        assert "DuckDB" in output
        assert "Snowflake" not in output

    @patch("benchbox.cli.platform.PlatformRegistry.get_platform_capabilities")
    def test_display_platform_deployments_renders_modes_and_requirements(self, mock_caps):
        stream = StringIO()
        self.manager.console = Console(file=stream, force_terminal=False, color_system=None, width=120)

        with patch.object(self.manager, "detect_platforms") as mock_detect:
            mock_detect.return_value = {
                "clickhouse": PlatformInfo(
                    name="clickhouse",
                    display_name="ClickHouse",
                    description="Columnar OLAP",
                    libraries=[],
                    available=True,
                    enabled=True,
                    requirements=[],
                    installation_command="uv add benchbox --extra clickhouse-cloud",
                    category="analytical",
                ),
                "duckdb": PlatformInfo(
                    name="duckdb",
                    display_name="DuckDB",
                    description="Embedded OLAP",
                    libraries=[],
                    available=True,
                    enabled=True,
                    requirements=[],
                    installation_command="uv add duckdb",
                    category="analytical",
                ),
            }
            mock_caps.side_effect = lambda name: (
                SimpleNamespace(
                    default_deployment="local",
                    deployment_modes={
                        "server": SimpleNamespace(
                            mode="self-hosted",
                            requires_credentials=True,
                            requires_cloud_storage=False,
                            requires_network=True,
                        ),
                        "local": SimpleNamespace(
                            mode="local",
                            requires_credentials=False,
                            requires_cloud_storage=False,
                            requires_network=False,
                        ),
                    },
                )
                if name == "clickhouse"
                else None
            )

            self.manager.display_platform_deployments()

        output = stream.getvalue()
        assert "clickhouse:server" in output
        assert "clickhouse:local" in output
        assert "credentials, network" in output
        assert "Usage: benchbox run --platform <platform>:<mode>" in output


class TestCLICommands:
    def setup_method(self):
        self.runner = CliRunner()
        self.temp_dir = Path(tempfile.mkdtemp())
        self.config_path = self.temp_dir / "test_platforms.yaml"

    def teardown_method(self):
        shutil.rmtree(self.temp_dir)

    @patch("benchbox.cli.platform.get_platform_manager")
    def test_list_platforms_table_format(self, mock_get_manager):

        mock_manager = Mock()
        mock_get_manager.return_value = mock_manager

        result = self.runner.invoke(list_platforms, ["--format", "table"])

        assert result.exit_code == 0
        mock_manager.display_platform_status.assert_called_once()

    @patch("benchbox.cli.platform.get_platform_manager")
    def test_list_platforms_simple_format(self, mock_get_manager):

        mock_manager = Mock()
        mock_get_manager.return_value = mock_manager

        result = self.runner.invoke(list_platforms, ["--format", "simple", "--all"])

        assert result.exit_code == 0
        mock_manager.display_platform_list.assert_called_once_with(show_all=True)

    @patch("benchbox.cli.platform.get_platform_manager")
    def test_list_platforms_json_flag(self, mock_get_manager):
        mock_manager = Mock()
        mock_get_manager.return_value = mock_manager

        result = self.runner.invoke(list_platforms, ["--json"])

        assert result.exit_code == 0
        mock_manager.emit_platform_json.assert_called_once()
        mock_manager.display_platform_status.assert_not_called()

    @patch("benchbox.cli.platform.get_platform_manager")
    def test_list_platforms_show_deployments(self, mock_get_manager):

        mock_manager = Mock()
        mock_get_manager.return_value = mock_manager

        result = self.runner.invoke(list_platforms, ["--show-deployments"])

        assert result.exit_code == 0
        mock_manager.display_platform_deployments.assert_called_once()
        mock_manager.display_platform_status.assert_not_called()

    @patch("benchbox.cli.platform.get_platform_manager")
    def test_platform_status_all(self, mock_get_manager):

        mock_manager = Mock()
        mock_get_manager.return_value = mock_manager

        result = self.runner.invoke(platform_status)

        assert result.exit_code == 0
        mock_manager.display_platform_status.assert_called_once()

    @patch("benchbox.cli.platform.get_platform_manager")
    def test_platform_status_specific(self, mock_get_manager):

        mock_manager = Mock()
        mock_lib = LibraryInfo(name="duckdb", version="1.0.0", installed=True)
        mock_platforms = {
            "duckdb": PlatformInfo(
                name="duckdb",
                display_name="DuckDB",
                description="High-performance database",
                libraries=[mock_lib],
                available=True,
                enabled=True,
                requirements=["duckdb>=0.8.0"],
                installation_command="uv add duckdb",
                category="analytical",
            )
        }
        mock_manager.detect_platforms.return_value = mock_platforms
        mock_get_manager.return_value = mock_manager

        result = self.runner.invoke(platform_status, ["duckdb"])

        assert result.exit_code == 0
        assert "DuckDB" in result.output
        assert "High-performance database" in result.output

    @patch("benchbox.cli.platform.check_platform_readiness")
    @patch("benchbox.cli.platform.get_platform_manager")
    def test_platform_status_specific_includes_readiness_details(self, mock_get_manager, mock_check_readiness):
        mock_manager = Mock()
        mock_lib = LibraryInfo(name="pyspark", version="4.1.1", installed=True)
        mock_manager.detect_platforms.return_value = {
            "lakesail": PlatformInfo(
                name="lakesail",
                display_name="LakeSail Sail",
                description="Spark Connect",
                libraries=[mock_lib],
                available=True,
                enabled=True,
                requirements=["pyspark>=3.4.0"],
                installation_command="uv add pyspark",
                category="analytical",
            )
        }
        mock_get_manager.return_value = mock_manager
        mock_check_readiness.return_value = (
            PlatformReadinessResult(
                platform="lakesail-df",
                check="spark_connect_endpoint",
                status="environment_skip",
                summary="LakeSail endpoint is not reachable.",
                remediation="Start a Sail server.",
            ),
        )

        result = self.runner.invoke(platform_status, ["lakesail-df"])

        assert result.exit_code == 0
        assert "LakeSail Sail" in result.output
        assert "Environment skip" in result.output
        assert "LakeSail endpoint is not reachable." in result.output
        assert "Start a Sail server." in result.output
        mock_check_readiness.assert_called_once_with("lakesail-df")

    @patch("benchbox.cli.platform.get_platform_manager")
    def test_platform_status_missing_dependencies_shows_installation_details(self, mock_get_manager):
        mock_manager = Mock()
        mock_lib = LibraryInfo(
            name="snowflake.connector",
            version=None,
            installed=False,
            import_error="No module named 'snowflake.connector'",
        )
        mock_manager.detect_platforms.return_value = {
            "snowflake": PlatformInfo(
                name="snowflake",
                display_name="Snowflake",
                description="Cloud data warehouse",
                libraries=[mock_lib],
                available=False,
                enabled=False,
                requirements=["snowflake-connector-python>=3.0.0"],
                installation_command="uv add snowflake-connector-python",
                category="cloud",
            )
        }
        mock_get_manager.return_value = mock_manager

        result = self.runner.invoke(platform_status, ["snowflake"])

        assert result.exit_code == 0
        assert "Missing Dependencies" in result.output
        assert "snowflake.connector" in result.output
        assert "No module named 'snowflake.connector'" in result.output
        assert "uv add snowflake-connector-python" in result.output
        assert "snowflake-connector-python>=3.0.0" in result.output

    @patch("benchbox.cli.platform.get_platform_manager")
    def test_platform_status_unknown(self, mock_get_manager):

        mock_manager = Mock()
        mock_manager.detect_platforms.return_value = {}
        mock_get_manager.return_value = mock_manager

        result = self.runner.invoke(platform_status, ["nonexistent"])

        assert result.exit_code == 1
        assert "Unknown platform" in result.output

    @patch("benchbox.cli.platform.get_platform_manager")
    def test_enable_platform_success(self, mock_get_manager):

        mock_manager = Mock()
        mock_platforms = {
            "duckdb": PlatformInfo(
                name="duckdb",
                display_name="DuckDB",
                description="",
                libraries=[],
                available=True,
                enabled=False,
                requirements=[],
                installation_command="",
                category="analytical",
            )
        }
        mock_manager.detect_platforms.return_value = mock_platforms
        mock_manager.enable_platform.return_value = True
        mock_get_manager.return_value = mock_manager

        result = self.runner.invoke(enable_platform, ["duckdb"])

        assert result.exit_code == 0
        assert "Enabled platform: DuckDB" in result.output
        mock_manager.enable_platform.assert_called_once_with("duckdb")

    @patch("benchbox.cli.platform.get_platform_manager")
    def test_enable_platform_already_enabled(self, mock_get_manager):

        mock_manager = Mock()
        mock_platforms = {
            "duckdb": PlatformInfo(
                name="duckdb",
                display_name="DuckDB",
                description="",
                libraries=[],
                available=True,
                enabled=True,
                requirements=[],
                installation_command="",
                category="analytical",
            )
        }
        mock_manager.detect_platforms.return_value = mock_platforms
        mock_get_manager.return_value = mock_manager

        result = self.runner.invoke(enable_platform, ["duckdb"])

        assert result.exit_code == 0
        assert "already enabled" in result.output

    @patch("benchbox.cli.platform.get_platform_manager")
    def test_enable_platform_not_available(self, mock_get_manager):

        mock_manager = Mock()
        mock_platforms = {
            "missing": PlatformInfo(
                name="missing",
                display_name="Missing Platform",
                description="",
                libraries=[],
                available=False,
                enabled=False,
                requirements=[],
                installation_command="uv add missing",
                category="database",
            )
        }
        mock_manager.detect_platforms.return_value = mock_platforms
        mock_get_manager.return_value = mock_manager

        result = self.runner.invoke(enable_platform, ["missing"])

        assert result.exit_code == 1
        assert "missing required dependencies" in result.output
        assert "uv add missing" in result.output

    @patch("benchbox.cli.platform.get_platform_manager")
    def test_enable_platform_force_allows_missing_dependencies(self, mock_get_manager):
        mock_manager = Mock()
        mock_platforms = {
            "datafusion": PlatformInfo(
                name="datafusion",
                display_name="DataFusion",
                description="",
                libraries=[],
                available=False,
                enabled=False,
                requirements=["datafusion>=0.0.0"],
                installation_command="uv add datafusion",
                category="analytical",
            )
        }
        mock_manager.detect_platforms.return_value = mock_platforms
        mock_manager.enable_platform.return_value = True
        mock_get_manager.return_value = mock_manager

        result = self.runner.invoke(enable_platform, ["fusion", "--force"])

        assert result.exit_code == 0
        assert "Enabled platform: DataFusion" in result.output
        assert "dependencies missing" in result.output
        mock_manager.enable_platform.assert_called_once_with("datafusion")

    @patch("benchbox.cli.platform.get_platform_manager")
    def test_disable_platform_success(self, mock_get_manager):

        mock_manager = Mock()
        mock_platforms = {
            "duckdb": PlatformInfo(
                name="duckdb",
                display_name="DuckDB",
                description="",
                libraries=[],
                available=True,
                enabled=True,
                requirements=[],
                installation_command="",
                category="analytical",
            )
        }
        mock_manager.detect_platforms.return_value = mock_platforms
        mock_manager.disable_platform.return_value = True
        mock_get_manager.return_value = mock_manager

        result = self.runner.invoke(disable_platform, ["duckdb"], input="y\n")

        assert result.exit_code == 0
        assert "Disabled platform: DuckDB" in result.output
        mock_manager.disable_platform.assert_called_once_with("duckdb")

    @patch("benchbox.cli.platform.get_platform_manager")
    def test_disable_platform_cancelled_by_user(self, mock_get_manager):
        mock_manager = Mock()
        mock_platforms = {
            "duckdb": PlatformInfo(
                name="duckdb",
                display_name="DuckDB",
                description="",
                libraries=[],
                available=True,
                enabled=True,
                requirements=[],
                installation_command="",
                category="analytical",
            )
        }
        mock_manager.detect_platforms.return_value = mock_platforms
        mock_get_manager.return_value = mock_manager

        result = self.runner.invoke(disable_platform, ["duck"], input="n\n")

        assert result.exit_code == 0
        assert "Cancelled" in result.output
        mock_manager.disable_platform.assert_not_called()

    @patch("benchbox.cli.platform.get_platform_manager")
    def test_install_platform_available(self, mock_get_manager):

        mock_manager = Mock()
        mock_guide = {
            "platform": "DuckDB",
            "description": "High-performance database",
            "category": "analytical",
            "available": True,
            "installation_command": "uv add duckdb",
            "requirements": ["duckdb>=0.8.0"],
            "missing_libraries": [],
        }
        mock_manager.get_installation_guide.return_value = mock_guide
        mock_get_manager.return_value = mock_manager

        result = self.runner.invoke(install_platform, ["duckdb"])

        assert result.exit_code == 0
        assert "Already installed and available" in result.output

    @patch("benchbox.cli.platform.get_platform_manager")
    def test_install_platform_missing(self, mock_get_manager):

        mock_manager = Mock()
        mock_guide = {
            "platform": "ClickHouse",
            "description": "Columnar database",
            "category": "analytical",
            "available": False,
            "installation_command": "uv add clickhouse-driver",
            "requirements": ["clickhouse-driver>=0.2.0"],
            "missing_libraries": ["clickhouse_driver"],
        }
        mock_manager.get_installation_guide.return_value = mock_guide
        mock_get_manager.return_value = mock_manager

        result = self.runner.invoke(install_platform, ["clickhouse"], input="n\n")

        assert result.exit_code == 0
        assert "Missing dependencies" in result.output
        assert "uv add clickhouse-driver" in result.output
        assert "clickhouse_driver" in result.output

    @patch("benchbox.cli.platform.get_platform_manager")
    def test_install_platform_dry_run_reports_no_install(self, mock_get_manager):
        mock_manager = Mock()
        mock_manager.get_installation_guide.return_value = {
            "platform": "ClickHouse",
            "description": "Columnar database",
            "category": "analytical",
            "available": False,
            "installation_command": "uv add clickhouse-driver",
            "requirements": ["clickhouse-driver>=0.2.0"],
            "missing_libraries": ["clickhouse_driver"],
        }
        mock_get_manager.return_value = mock_manager

        result = self.runner.invoke(install_platform, ["clickhouse", "--dry-run"])

        assert result.exit_code == 0
        assert "Dry run mode: No installation performed" in result.output
        assert "After installation, run" not in result.output

    @patch("benchbox.cli.platform.get_platform_manager")
    def test_check_platforms_all_ready(self, mock_get_manager):

        mock_manager = Mock()
        mock_platforms = {
            "duckdb": PlatformInfo(
                name="duckdb",
                display_name="DuckDB",
                description="",
                libraries=[],
                available=True,
                enabled=True,
                requirements=[],
                installation_command="",
                category="analytical",
            )
        }
        mock_manager.detect_platforms.return_value = mock_platforms
        mock_manager.is_platform_available.return_value = True
        mock_get_manager.return_value = mock_manager

        result = self.runner.invoke(check_platforms, ["duckdb"])

        assert result.exit_code == 0
        assert "DuckDB: Ready" in result.output
        assert "All checked platforms are ready!" in result.output

    @patch("benchbox.cli.platform.get_platform_manager")
    def test_check_platforms_some_missing(self, mock_get_manager):

        mock_manager = Mock()
        mock_platforms = {
            "duckdb": PlatformInfo(
                name="duckdb",
                display_name="DuckDB",
                description="",
                libraries=[],
                available=True,
                enabled=True,
                requirements=[],
                installation_command="",
                category="analytical",
            ),
            "missing": PlatformInfo(
                name="missing",
                display_name="Missing Platform",
                description="",
                libraries=[],
                available=False,
                enabled=False,
                requirements=[],
                installation_command="uv add missing",
                category="database",
            ),
        }
        mock_manager.detect_platforms.return_value = mock_platforms
        mock_manager.is_platform_available.side_effect = lambda x: x == "duckdb"
        mock_get_manager.return_value = mock_manager

        result = self.runner.invoke(check_platforms, ["duckdb", "missing"])

        assert result.exit_code == 1
        assert "DuckDB: Ready" in result.output
        assert "Missing Platform: Missing dependencies" in result.output
        assert "Some platforms need attention" in result.output

    @patch("benchbox.cli.platform.check_platform_readiness")
    @patch("benchbox.cli.platform.get_platform_manager")
    def test_check_platforms_environment_skip_from_readiness(self, mock_get_manager, mock_check_readiness):
        mock_manager = Mock()
        mock_manager.detect_platforms.return_value = {
            "lakesail": PlatformInfo(
                name="lakesail",
                display_name="LakeSail Sail",
                description="Spark Connect",
                libraries=[],
                available=True,
                enabled=True,
                requirements=[],
                installation_command="uv add pyspark",
                category="analytical",
            )
        }
        mock_get_manager.return_value = mock_manager
        mock_check_readiness.return_value = (
            PlatformReadinessResult(
                platform="lakesail-df",
                check="spark_connect_endpoint",
                status="environment_skip",
                summary="LakeSail endpoint is not reachable.",
                detail="Benchmark should be skipped until provisioning is complete.",
                remediation="Start a Sail server.",
            ),
        )

        result = self.runner.invoke(check_platforms, ["lakesail-df"])

        assert result.exit_code == 1
        assert "LakeSail Sail: Environment not ready" in result.output
        assert "environment skip" in result.output
        assert "Benchmark should be skipped" in result.output
        assert "Some platforms need attention" in result.output
        mock_check_readiness.assert_called_once_with("lakesail-df")

    @patch("benchbox.cli.platform.check_platform_readiness")
    @patch("benchbox.cli.platform.get_platform_manager")
    def test_check_platforms_disabled_with_readiness_failure_does_not_fail_command(
        self, mock_get_manager, mock_check_readiness
    ):
        mock_manager = Mock()
        mock_manager.detect_platforms.return_value = {
            "lakesail": PlatformInfo(
                name="lakesail",
                display_name="LakeSail Sail",
                description="Spark Connect",
                libraries=[],
                available=True,
                enabled=False,
                requirements=[],
                installation_command="uv add pyspark",
                category="analytical",
            )
        }
        mock_get_manager.return_value = mock_manager
        mock_check_readiness.return_value = (
            PlatformReadinessResult(
                platform="lakesail",
                check="spark_connect_endpoint",
                status="environment_skip",
                summary="LakeSail endpoint is not reachable.",
                detail="Benchmark should be skipped until provisioning is complete.",
                remediation="Start a Sail server.",
            ),
        )

        result = self.runner.invoke(check_platforms, ["lakesail"])

        assert result.exit_code == 0, result.output
        assert "Available but disabled" in result.output
        assert "Environment not ready" not in result.output

    @patch("benchbox.cli.platform.get_platform_manager")
    def test_check_platforms_enabled_only_with_none_enabled(self, mock_get_manager):
        mock_manager = Mock()
        mock_manager.detect_platforms.return_value = {}
        mock_manager.get_enabled_platforms.return_value = []
        mock_get_manager.return_value = mock_manager

        result = self.runner.invoke(check_platforms, ["--enabled-only"])

        assert result.exit_code == 0
        assert "No platforms to check" in result.output


class TestGlobalPlatformManager:
    def test_get_platform_manager_singleton(self):

        manager1 = get_platform_manager()
        manager2 = get_platform_manager()
        assert manager1 is manager2

    def test_get_platform_manager_returns_platform_manager(self):

        manager = get_platform_manager()
        assert isinstance(manager, PlatformManager)


class TestSetupPlatformsCommand:
    def setup_method(self):
        self.runner = CliRunner()

    @patch("benchbox.cli.platform.get_platform_manager")
    def test_setup_platforms_non_interactive_enables_available_disabled_platforms(self, mock_get_manager):
        mock_manager = Mock()
        mock_manager.detect_platforms.return_value = {
            "duckdb": PlatformInfo(
                name="duckdb",
                display_name="DuckDB",
                description="",
                libraries=[],
                available=True,
                enabled=False,
                requirements=[],
                installation_command="uv add duckdb",
                category="analytical",
            ),
            "sqlite": PlatformInfo(
                name="sqlite",
                display_name="SQLite",
                description="",
                libraries=[],
                available=True,
                enabled=True,
                requirements=[],
                installation_command="builtin",
                category="embedded",
            ),
            "snowflake": PlatformInfo(
                name="snowflake",
                display_name="Snowflake",
                description="",
                libraries=[],
                available=False,
                enabled=False,
                requirements=[],
                installation_command="uv add snowflake-connector-python",
                category="cloud",
            ),
        }
        mock_manager.enable_platform.return_value = True
        mock_get_manager.return_value = mock_manager

        result = self.runner.invoke(setup_platforms, ["--non-interactive"])

        assert result.exit_code == 0
        mock_manager.enable_platform.assert_called_once_with("duckdb")
        assert "Enabled: DuckDB" in result.output
        assert "Enabled 1 platforms" in result.output

    @patch("benchbox.cli.platform.get_platform_manager")
    @patch("benchbox.cli.platform.NumberedSelectPrompt.ask")
    def test_setup_platforms_status_branch_displays_current_status(self, mock_ask, mock_get_manager):
        mock_manager = Mock()
        mock_manager.detect_platforms.return_value = {}
        mock_get_manager.return_value = mock_manager
        mock_ask.side_effect = ["status", "done"]

        result = self.runner.invoke(setup_platforms, ["--interactive"])

        assert result.exit_code == 0
        mock_manager.display_platform_status.assert_called_once()

    @patch("benchbox.cli.platform.numbered_platform_select", return_value=None)
    @patch("benchbox.cli.platform.get_platform_manager")
    @patch("benchbox.cli.platform.NumberedSelectPrompt.ask")
    def test_setup_platforms_install_branch_reports_no_missing_platforms(self, mock_ask, mock_get_manager, mock_select):
        mock_manager = Mock()
        mock_manager.detect_platforms.return_value = {}
        mock_get_manager.return_value = mock_manager
        mock_ask.side_effect = ["install", "done"]

        result = self.runner.invoke(setup_platforms, ["--interactive"])

        assert result.exit_code == 0
        mock_select.assert_called_once()
        assert "No platforms with missing dependencies." in result.output

    @patch("benchbox.cli.platform.numbered_platform_select", return_value=None)
    @patch("benchbox.cli.platform.get_platform_manager")
    @patch("benchbox.cli.platform.NumberedSelectPrompt.ask")
    def test_setup_platforms_enable_branch_reports_no_candidates(self, mock_ask, mock_get_manager, mock_select):
        mock_manager = Mock()
        mock_manager.detect_platforms.return_value = {}
        mock_get_manager.return_value = mock_manager
        mock_ask.side_effect = ["enable", "done"]

        result = self.runner.invoke(setup_platforms, ["--interactive"])

        assert result.exit_code == 0
        mock_select.assert_called_once()
        assert "No available platforms to enable." in result.output

    @patch("benchbox.cli.platform.numbered_platform_select", return_value=None)
    @patch("benchbox.cli.platform.get_platform_manager")
    @patch("benchbox.cli.platform.NumberedSelectPrompt.ask")
    def test_setup_platforms_disable_branch_reports_no_enabled_platforms(self, mock_ask, mock_get_manager, mock_select):
        mock_manager = Mock()
        mock_manager.detect_platforms.return_value = {}
        mock_get_manager.return_value = mock_manager
        mock_ask.side_effect = ["disable", "done"]

        result = self.runner.invoke(setup_platforms, ["--interactive"])

        assert result.exit_code == 0
        mock_select.assert_called_once()
        assert "No enabled platforms to disable." in result.output


STANDARD_TERMINAL_WIDTH = 80
DEFAULT_HEADERS = ("Platform", "Driver", "Support", "Libraries")


def _render_platform_status(width: int, *, detail: bool = False) -> str:
    stream = StringIO()
    manager = get_platform_manager()
    manager.console = Console(file=stream, force_terminal=False, color_system=None, width=width)
    manager.display_platform_status(detail=detail)
    return stream.getvalue()


def _header_line(output: str) -> str:
    for line in output.splitlines():
        if "Platform" in line and "Driver" in line:
            return line
    raise AssertionError(f"no header row found in:\n{output[:2000]}")


def test_platform_status_headers_are_not_truncated_at_80_columns() -> None:
    header = _header_line(_render_platform_status(STANDARD_TERMINAL_WIDTH))

    assert "\u2026" not in header, f"a default column header is truncated at 80 columns: {header}"
    for column in DEFAULT_HEADERS:
        assert column in header, f"{column} missing from the default header: {header}"


def test_platform_status_does_not_overflow_80_columns() -> None:
    output = _render_platform_status(STANDARD_TERMINAL_WIDTH)

    overflowing = [line for line in output.splitlines() if len(line.rstrip()) > STANDARD_TERMINAL_WIDTH]

    assert not overflowing, "output wider than the terminal:\n" + "\n".join(overflowing[:5])


def test_platform_status_default_omits_the_columns_that_forced_the_truncation() -> None:
    header = _header_line(_render_platform_status(STANDARD_TERMINAL_WIDTH))

    assert "Category" not in header
    assert "Description" not in header


def test_platform_status_detail_restores_the_full_table() -> None:
    header = _header_line(_render_platform_status(200, detail=True))

    for column in (*DEFAULT_HEADERS, "Category", "Description"):
        assert column in header, f"{column} missing from --detail header: {header}"


def test_the_width_gate_still_detects_a_truncated_header() -> None:
    header = _header_line(_render_platform_status(STANDARD_TERMINAL_WIDTH, detail=True))

    assert "\u2026" in header, f"expected --detail to truncate at 80 columns, got: {header}"


def test_every_default_row_still_carries_its_support_tier() -> None:
    output = _render_platform_status(STANDARD_TERMINAL_WIDTH)
    tiers = ("stable", "beta", "experimental", "deprecated")

    counted = sum(output.count(tier) for tier in tiers)
    listed = len(get_platform_manager().detect_platforms())

    assert counted >= listed, f"{counted} tier labels for {listed} platforms"


def test_platform_status_orders_stable_platforms_before_experimental_ones() -> None:
    output = _render_platform_status(STANDARD_TERMINAL_WIDTH)
    lines = [line for line in output.splitlines() if line.startswith("\u2502")]

    stable_rows = [index for index, line in enumerate(lines) if "stable" in line]
    experimental_rows = [index for index, line in enumerate(lines) if "experimental" in line]

    assert stable_rows and experimental_rows, "fixture assumption changed"
    assert max(stable_rows) < min(experimental_rows)


