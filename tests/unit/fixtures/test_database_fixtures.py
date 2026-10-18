# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


pytest_plugins = ["tests.fixtures.database_fixtures"]

from tests.fixtures.database_fixtures import (
    _create_duckdb_connection,
    create_test_database,
    get_database_config,
)


@pytest.mark.unit
@pytest.mark.duckdb
class TestBasicUsage:
    def test_with_memory_database(self, duckdb_memory_db):
        result = duckdb_memory_db.execute("SELECT 1 as test").fetchone()
        assert result[0] == 1

    def test_with_extensions(self, duckdb_with_extensions):
        try:
            duckdb_with_extensions.execute("SELECT * FROM parquet_metadata('nonexistent.parquet')")
        except Exception:
            pass

    def test_with_configuration(self, configured_duckdb):
        result = configured_duckdb.execute("SELECT 2 as test").fetchone()
        assert result[0] == 2


@pytest.mark.unit
@pytest.mark.duckdb
class TestParameterizedFixtures:
    def test_with_all_database_types(self, duckdb_database):
        result = duckdb_database.execute("SELECT 3 as test").fetchone()
        assert result[0] == 3

    @pytest.mark.parametrize("duckdb_database", ["memory", "performance"], indirect=True)
    def test_with_specific_configs(self, duckdb_database):
        result = duckdb_database.execute("SELECT 4 as test").fetchone()
        assert result[0] == 4


@pytest.mark.unit
@pytest.mark.duckdb
class TestCustomDatabases:
    def test_with_custom_configuration(self, duckdb_custom):
        db = duckdb_custom(memory_limit="4GB", threads=8, load_extensions=True)

        result = db.execute("SELECT 5 as test").fetchone()
        assert result[0] == 5

    def test_with_file_database(self, duckdb_custom):
        db = duckdb_custom(connection_type="file", memory_limit="2GB")

        db.execute("CREATE TABLE test_table (id INTEGER, value TEXT)")
        db.execute("INSERT INTO test_table VALUES (1, 'test')")

        result = db.execute("SELECT * FROM test_table").fetchone()
        assert result == (1, "test")

    def test_multiple_custom_databases(self, duckdb_custom):
        fast_db = duckdb_custom(memory_limit="256MB", threads=1, load_extensions=False)

        perf_db = duckdb_custom(memory_limit="4GB", threads=8, load_extensions=True)

        fast_db.execute("SELECT 1").fetchone()
        perf_db.execute("SELECT 2").fetchone()


@pytest.mark.unit
@pytest.mark.duckdb
class TestPerformanceFixtures:
    def test_with_performance_database(self, duckdb_performance):
        result = duckdb_performance.execute("SELECT 6 as test").fetchone()
        assert result[0] == 6

    def test_with_minimal_database(self, duckdb_minimal):
        result = duckdb_minimal.execute("SELECT 7 as test").fetchone()
        assert result[0] == 7


@pytest.mark.unit
@pytest.mark.duckdb
class TestUtilityFunctions:
    def test_create_database_outside_fixture(self):
        db = create_test_database("memory")
        try:
            result = db.execute("SELECT 8 as test").fetchone()
            assert result[0] == 8
        finally:
            db.close()

        db = create_test_database("memory", memory_limit="512MB", threads=2, load_extensions=True)
        try:
            result = db.execute("SELECT 9 as test").fetchone()
            assert result[0] == 9
        finally:
            db.close()

    def test_get_configuration(self):
        config = get_database_config("performance")
        assert config.memory_limit == "2GB"
        assert config.threads == 4
        assert config.load_extensions

    def test_custom_configuration_class(self, duckdb_config_factory):
        config = duckdb_config_factory(
            connection_type="memory",
            memory_limit="8GB",
            threads=16,
            load_extensions=True,
            settings={"enable_profiling": True, "enable_optimizer": True},
        )

        db = _create_duckdb_connection(config)
        try:
            result = db.execute("SELECT 10 as test").fetchone()
            assert result[0] == 10
        finally:
            db.close()


@pytest.mark.unit
@pytest.mark.duckdb
class TestBackwardCompatibility:
    def test_existing_memory_fixture(self, duckdb_memory_db):
        result = duckdb_memory_db.execute("SELECT 'backward' as test").fetchone()
        assert result[0] == "backward"

    def test_existing_file_fixture(self, duckdb_file_db):
        result = duckdb_file_db.execute("SELECT 'compatible' as test").fetchone()
        assert result[0] == "compatible"

    def test_existing_configured_fixture(self, configured_duckdb):
        result = configured_duckdb.execute("SELECT 'works' as test").fetchone()
        assert result[0] == "works"

    def test_existing_database_config(self, database_config):
        assert isinstance(database_config, dict)
        assert "memory_limit" in database_config
        assert "threads" in database_config


@pytest.mark.unit
@pytest.mark.duckdb
class TestMigrationPatterns:
    def test_old_pattern_with_extensions(self, duckdb_memory_db):
        from tests.fixtures.database_fixtures import setup_duckdb_extensions

        setup_duckdb_extensions(duckdb_memory_db)

        result = duckdb_memory_db.execute("SELECT 'old_pattern' as test").fetchone()
        assert result[0] == "old_pattern"

    def test_new_pattern_with_extensions(self, duckdb_with_extensions):
        result = duckdb_with_extensions.execute("SELECT 'new_pattern' as test").fetchone()
        assert result[0] == "new_pattern"

    def test_old_pattern_configuration(self, duckdb_memory_db, database_config):
        for key, value in database_config.items():
            try:
                if isinstance(value, str):
                    duckdb_memory_db.execute(f"SET {key} = '{value}';")
                else:
                    duckdb_memory_db.execute(f"SET {key} = {value};")
            except Exception:
                pass

        result = duckdb_memory_db.execute("SELECT 'old_config' as test").fetchone()
        assert result[0] == "old_config"

    def test_new_pattern_configuration(self, configured_duckdb):
        result = configured_duckdb.execute("SELECT 'new_config' as test").fetchone()
        assert result[0] == "new_config"

    def test_best_pattern_custom_needs(self, duckdb_custom):
        db = duckdb_custom(
            memory_limit="1GB",
            threads=4,
            load_extensions=True,
            settings={"enable_profiling": True, "enable_optimizer": True},
        )

        result = db.execute("SELECT 'best_pattern' as test").fetchone()
        assert result[0] == "best_pattern"


@pytest.mark.unit
@pytest.mark.duckdb
class TestFixtureErrorHandling:
    def test_invalid_configuration_name(self):
        with pytest.raises(KeyError, match="Unknown database configuration"):
            get_database_config("nonexistent_config")

    def test_custom_database_with_invalid_type(self, duckdb_custom):
        with pytest.raises(ValueError, match="Unsupported connection type"):
            duckdb_custom(connection_type="invalid_type")

    def test_file_database_without_path(self, duckdb_custom, tmp_path):
        db = duckdb_custom(connection_type="file")

        db.execute("CREATE TABLE test_file_db (id INTEGER)")
        db.execute("INSERT INTO test_file_db VALUES (42)")

        result = db.execute("SELECT * FROM test_file_db").fetchone()
        assert result[0] == 42


@pytest.mark.unit
@pytest.mark.duckdb
class TestFixtureResourceManagement:
    def test_connection_cleanup(self, duckdb_custom):
        connections = []
        for i in range(3):
            db = duckdb_custom(memory_limit="256MB")
            db.execute(f"SELECT {i}")
            connections.append(db)

    def test_temporary_file_cleanup(self, duckdb_custom):
        db = duckdb_custom(connection_type="file")

        db.execute("CREATE TABLE cleanup_test (id INTEGER)")
        db.execute("INSERT INTO cleanup_test VALUES (1)")


@pytest.mark.unit
@pytest.mark.duckdb
class TestFixtureConfiguration:
    def test_memory_limit_settings(self, duckdb_custom):
        memory_limits = ["256MB", "512MB", "1GB", "2GB"]

        for limit in memory_limits:
            db = duckdb_custom(memory_limit=limit)
            result = db.execute("SELECT 1").fetchone()
            assert result[0] == 1

    def test_thread_count_settings(self, duckdb_custom):
        thread_counts = [1, 2, 4, 8]

        for threads in thread_counts:
            db = duckdb_custom(threads=threads)
            result = db.execute("SELECT 1").fetchone()
            assert result[0] == 1

    def test_extension_loading(self, duckdb_custom):
        db_no_ext = duckdb_custom(load_extensions=False)
        result = db_no_ext.execute("SELECT 1").fetchone()
        assert result[0] == 1

        db_with_ext = duckdb_custom(load_extensions=True)
        result = db_with_ext.execute("SELECT 1").fetchone()
        assert result[0] == 1

    def test_custom_settings(self, duckdb_custom):
        custom_settings = {
            "enable_optimizer": True,
            "enable_profiling": False,
            "preserve_insertion_order": False,
        }

        db = duckdb_custom(settings=custom_settings)
        result = db.execute("SELECT 1").fetchone()
        assert result[0] == 1


@pytest.mark.unit
@pytest.mark.duckdb
class TestDatabaseFixtureConsistency:
    def test_all_fixtures_basic_functionality(
        self,
        duckdb_memory_db,
        duckdb_with_extensions,
        configured_duckdb,
        duckdb_performance,
        duckdb_minimal,
    ):
        fixtures = [
            ("memory", duckdb_memory_db),
            ("with_extensions", duckdb_with_extensions),
            ("configured", configured_duckdb),
            ("performance", duckdb_performance),
            ("minimal", duckdb_minimal),
        ]

        for name, db in fixtures:
            result = db.execute("SELECT 1").fetchone()
            assert result[0] == 1, f"Basic SELECT failed for {name} fixture"

            db.execute(f"CREATE TABLE test_{name} (id INTEGER, value TEXT)")
            db.execute(f"INSERT INTO test_{name} VALUES (1, 'test')")

            result = db.execute(f"SELECT * FROM test_{name}").fetchone()
            assert result == (1, "test"), f"Table operations failed for {name} fixture"

            db.execute(f"DROP TABLE test_{name}")

    def test_parameterized_fixture_consistency(self, duckdb_database):
        result = duckdb_database.execute("SELECT 42").fetchone()
        assert result[0] == 42

        duckdb_database.execute("CREATE TABLE param_test (value INTEGER)")
        duckdb_database.execute("INSERT INTO param_test VALUES (123)")

        result = duckdb_database.execute("SELECT * FROM param_test").fetchone()
        assert result[0] == 123
