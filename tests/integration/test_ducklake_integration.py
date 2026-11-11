# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.


from __future__ import annotations

import json
import os
import uuid
import warnings
from functools import cache
from pathlib import Path

import duckdb
import pytest

from benchbox.platforms.ducklake import DuckLakeAdapter
from tests.integration._cli_e2e_utils import run_cli_command
from tests.integration.platforms.conftest import get_env_or_skip, skip_unless_docker_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]


_PROBE_FAILURES: dict[str, str] = {}


@cache
def _probe_extension(name: str) -> bool:
    try:
        conn = duckdb.connect(":memory:")
        try:
            conn.execute(f"INSTALL {name}")
            conn.execute(f"LOAD {name}")
            return True
        finally:
            conn.close()
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        _PROBE_FAILURES[name] = reason
        warnings.warn(
            f"DuckDB extension probe failed for {name!r} ({reason}). This result is cached for the "
            f"whole session, so every DuckLake test depending on {name!r} will now skip. If this was "
            "a transient extension-repository/network failure, re-run rather than trusting the skips.",
            stacklevel=2,
        )
        return False


def _skip_unless_extensions(*names: str) -> None:
    missing = [name for name in names if not _probe_extension(name)]
    if missing:
        detail = ", ".join(f"{name} [{_PROBE_FAILURES.get(name, 'reason not recorded')}]" for name in missing)
        pytest.skip(f"DuckDB extension(s) not available: {detail}")


class TestDuckLakeLiveConnection:
    def setup_method(self) -> None:
        _skip_unless_extensions("ducklake")

    def test_cursor_defaults_to_lake_catalog(self, tmp_path):
        metadata_path = tmp_path / "catalog.ducklake"
        data_path = tmp_path / "data"
        adapter = DuckLakeAdapter(metadata_path=str(metadata_path), data_path=str(data_path))

        connection = adapter.create_connection()
        try:
            connection.execute("CREATE TABLE nation (id INTEGER, name VARCHAR)")
            connection.execute("INSERT INTO nation VALUES (1, 'ALGERIA')")

            cur = connection.cursor()
            cur.execute("SELECT 1 FROM nation LIMIT 1")
            assert cur.fetchone() is not None

            assert connection.cursor().execute("SELECT current_catalog()").fetchone()[0] == "lake"

            assert connection.execute("SELECT count(*) FROM nation").fetchone()[0] == 1
        finally:
            connection.close()

    def test_create_connection_attaches_lake_catalog(self, tmp_path):
        metadata_path = tmp_path / "catalog.ducklake"
        data_path = tmp_path / "data"
        adapter = DuckLakeAdapter(metadata_path=str(metadata_path), data_path=str(data_path))

        conn = adapter.create_connection()
        try:
            assert metadata_path.parent.is_dir()
            assert data_path.is_dir()

            conn.execute("CREATE TABLE ducklake_smoke (id INTEGER, name VARCHAR)")
            conn.execute("INSERT INTO ducklake_smoke SELECT i, 'name-' || i::VARCHAR FROM range(12) AS rows(i)")
            rows = conn.execute("SELECT * FROM ducklake_smoke ORDER BY id").fetchall()
            assert rows == [(i, f"name-{i}") for i in range(12)]

            current_catalog = conn.execute("SELECT current_catalog()").fetchone()[0]
            assert current_catalog == "lake"
        finally:
            conn.close()

        assert metadata_path.exists()
        parquet_files = list(data_path.rglob("*.parquet"))
        assert len(parquet_files) >= 1

    def test_force_recreate_rebuilds_fresh_catalog(self, tmp_path):
        metadata_path = tmp_path / "catalog.ducklake"
        data_path = tmp_path / "data"

        adapter1 = DuckLakeAdapter(metadata_path=str(metadata_path), data_path=str(data_path))
        conn1 = adapter1.create_connection()
        try:
            conn1.execute("CREATE TABLE t (id INTEGER)")
            conn1.execute("INSERT INTO t SELECT i FROM range(12) AS rows(i)")
        finally:
            conn1.close()
        assert metadata_path.exists()
        assert list(data_path.rglob("*.parquet"))

        adapter2 = DuckLakeAdapter(
            metadata_path=str(metadata_path),
            data_path=str(data_path),
            force_recreate=True,
        )
        assert adapter2.force_recreate is True

        conn2 = adapter2.create_connection()
        try:
            conn2.execute("CREATE TABLE t (id INTEGER)")
            count = conn2.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            assert count == 0
        finally:
            conn2.close()
        assert adapter2.database_was_reused is False

    def test_handle_existing_database_force_removes_artifacts(self, tmp_path):
        metadata_path = tmp_path / "catalog.ducklake"
        data_path = tmp_path / "data"

        adapter = DuckLakeAdapter(metadata_path=str(metadata_path), data_path=str(data_path))
        conn = adapter.create_connection()
        try:
            conn.execute("CREATE TABLE t (id INTEGER)")
        finally:
            conn.close()
        assert metadata_path.exists()
        assert data_path.exists()

        adapter._reset_run_scoped_state()
        adapter.force_recreate = True
        adapter.handle_existing_database()

        assert not metadata_path.exists()
        assert not data_path.exists() or not any(data_path.iterdir())
        assert adapter.database_was_reused is False

    def test_get_platform_info_reports_ducklake_metadata(self, tmp_path):
        metadata_path = tmp_path / "catalog.ducklake"
        data_path = tmp_path / "data"
        adapter = DuckLakeAdapter(metadata_path=str(metadata_path), data_path=str(data_path))

        conn = adapter.create_connection()
        try:
            info = adapter.get_platform_info(conn)
        finally:
            conn.close()

        assert info["platform_type"] == "ducklake"
        assert info["platform_name"] == "DuckLake"
        assert info["catalog_backend"] == "duckdb"
        assert info["metadata_path"] == str(metadata_path)
        assert info["data_path"] == str(data_path)
        assert "ducklake_extension_version" in info

    def test_sqlite_catalog_end_to_end(self, tmp_path):
        metadata_path = tmp_path / "catalog.ducklake"
        data_path = tmp_path / "data"
        adapter = DuckLakeAdapter(
            metadata_path=str(metadata_path),
            data_path=str(data_path),
            catalog="sqlite",
        )
        sqlite_metadata_path = tmp_path / "catalog.sqlite"
        assert adapter.metadata_path == sqlite_metadata_path

        conn = adapter.create_connection()
        try:
            conn.execute("CREATE TABLE sqlite_smoke (id INTEGER, name VARCHAR)")
            conn.execute("INSERT INTO sqlite_smoke SELECT i, 'name-' || i::VARCHAR FROM range(12) AS rows(i)")
            rows = conn.execute("SELECT * FROM sqlite_smoke ORDER BY id").fetchall()
            assert rows == [(i, f"name-{i}") for i in range(12)]

            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM sqlite_smoke")
            assert cur.fetchone()[0] == 12
        finally:
            conn.close()

        assert sqlite_metadata_path.exists()
        assert not metadata_path.exists()
        parquet_files = list(data_path.rglob("*.parquet"))
        assert len(parquet_files) >= 1


class TestDuckLakeSqliteCatalogLive:
    def setup_method(self) -> None:
        _skip_unless_extensions("ducklake", "sqlite")

    def test_tpch_sf001_sqlite_catalog_end_to_end(self, tmp_path: Path) -> None:
        result = run_cli_command(
            [
                "run",
                "--platform",
                "ducklake",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--platform-option",
                "catalog=sqlite",
                "--queries",
                "Q1,Q6,Q17",
                "--non-interactive",
            ],
            cwd=tmp_path,
        )
        assert result.returncode == 0, (
            f"benchbox run failed (exit {result.returncode}).\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

        result_files = sorted((tmp_path / "benchmark_runs" / "results").glob("*.json"))
        assert result_files, f"No result JSON exported.\nSTDOUT:\n{result.stdout}"
        payload = json.loads(result_files[-1].read_text(encoding="utf-8"))

        summary = payload["summary"]
        assert summary["validation"] == "passed", summary
        assert summary["queries"]["failed"] == 0, summary["queries"]
        assert summary["queries"]["passed"] >= 1, summary["queries"]
        assert payload["platform"]["config"]["catalog_backend"] == "sqlite"

        databases_dir = tmp_path / "benchmark_runs" / "databases"
        sqlite_metadata_files = list(databases_dir.rglob("*.sqlite"))
        assert sqlite_metadata_files, f"Expected a .sqlite DuckLake catalog file under {databases_dir}"

        parquet_files = list(databases_dir.rglob("*.parquet"))
        assert parquet_files, f"Expected DuckLake Parquet data files under {databases_dir}"


class TestDuckLakeTpchRefreshLive:
    def setup_method(self) -> None:
        _skip_unless_extensions("ducklake")

    def test_rf1_rf2_values_path_with_expected_counts(self, tmp_path: Path) -> None:
        from benchbox.core.contracts.platform_capabilities import resolve_refresh_ingester
        from tests.integration._tpch_refresh_utils import _check_rf1_rf2_with_expected_counts, _load_sf001_base_read_csv

        adapter = DuckLakeAdapter(metadata_path=str(tmp_path / "catalog.ducklake"), data_path=str(tmp_path / "data"))
        assert DuckLakeAdapter.supports_refresh_functions is True
        assert resolve_refresh_ingester(adapter) is None

        connection = adapter.create_connection()
        try:
            _check_rf1_rf2_with_expected_counts(connection, tmp_path, _load_sf001_base_read_csv)
        finally:
            connection.close()


class TestDuckLakeWritePrimitivesLive:
    def setup_method(self) -> None:
        _skip_unless_extensions("ducklake")

    def test_write_primitives_success_and_engine_specific_skip(self, tmp_path: Path) -> None:
        result = run_cli_command(
            [
                "run",
                "--platform",
                "ducklake",
                "--benchmark",
                "write_primitives",
                "--scale",
                "0.01",
                "--queries",
                "insert_single_row,merge_scd_type2_basic,ddl_create_index_on_existing",
                "--non-interactive",
            ],
            cwd=tmp_path,
        )
        assert result.returncode == 0, (
            f"benchbox run failed (exit {result.returncode}).\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

        result_files = sorted((tmp_path / "benchmark_runs" / "results").glob("*.json"))
        assert len(result_files) == 1, result_files
        payload = json.loads(result_files[0].read_text(encoding="utf-8"))
        assert payload["summary"]["validation"] == "passed"
        assert payload["summary"]["queries"]["failed"] == 0
        statuses: dict[str, set[str]] = {}
        for query in payload["queries"]:
            statuses.setdefault(query["id"], set()).add(query["status"])
        assert statuses["insert_single_row"] == {"SUCCESS"}
        assert statuses["merge_scd_type2_basic"] == {"SUCCESS"}
        assert statuses["ddl_create_index_on_existing"] == {"SKIPPED"}
        assert payload["platform"]["config"]["catalog_backend"] == "duckdb"

        databases_dir = tmp_path / "benchmark_runs" / "databases"
        assert list(databases_dir.rglob("*.ducklake"))
        assert list(databases_dir.rglob("*.parquet"))


@pytest.mark.docker_integration
@pytest.mark.live_integration
@pytest.mark.live_postgresql
class TestDuckLakePostgresCatalogLive:
    def test_postgres_catalog_attach_and_query(self, tmp_path: Path) -> None:
        _skip_unless_extensions("ducklake", "postgres")
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "unused-for-postgres-catalog.ducklake"),
            data_path=str(tmp_path / "data"),
            catalog="postgres",
            pg_host=os.getenv("PG_DUCKLAKE_HOST", "localhost"),
            pg_port=int(os.getenv("PG_DUCKLAKE_PORT", "5432")),
            pg_user=os.getenv("PG_DUCKLAKE_USER", "benchbox"),
            pg_password=os.getenv("PG_DUCKLAKE_PASSWORD", "benchbox"),
            pg_database=os.getenv("PG_DUCKLAKE_DATABASE", "benchbox_test"),
        )
        assert adapter.catalog == "postgres"

        attach_target = adapter._build_catalog_attach_target()
        assert attach_target.startswith("postgres:")
        assert "dbname=benchbox_test" in attach_target
        assert f"host={adapter.pg_host}" in attach_target

        skip_unless_docker_service(adapter.pg_host, adapter.pg_port, platform="DuckLake PostgreSQL catalog")

        connection = adapter.create_connection()
        table = f"ducklake_pg_smoke_{uuid.uuid4().hex[:8]}"
        try:
            connection.execute(f"DROP TABLE IF EXISTS {table}")
            connection.execute(f"CREATE TABLE {table} (id INTEGER, name VARCHAR)")
            connection.execute(f"INSERT INTO {table} VALUES (1, 'alpha'), (2, 'beta')")
            count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count == 2
        finally:
            try:
                connection.execute(f"DROP TABLE IF EXISTS {table}")
            except Exception:
                pass
            connection.close()


@pytest.mark.live_integration
class TestDuckLakeS3DataPathLive:
    def test_s3_data_path_credential_chain_attach_and_query(self, tmp_path: Path) -> None:
        _skip_unless_extensions("ducklake", "httpfs")
        bucket = os.getenv("BENCHBOX_S3_TEST_BUCKET")
        if not bucket:
            pytest.skip("Requires BENCHBOX_S3_TEST_BUCKET for DuckLake S3 DATA_PATH tests")
        get_env_or_skip("AWS_ACCESS_KEY_ID", "DuckLake S3")
        get_env_or_skip("AWS_SECRET_ACCESS_KEY", "DuckLake S3")

        run_id = uuid.uuid4().hex[:8]
        data_path = f"s3://{bucket}/benchbox_ducklake_test/{run_id}"

        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=data_path,
        )
        assert adapter._data_path_is_cloud is True

        connection = adapter.create_connection()
        table = f"ducklake_s3_smoke_{run_id}"
        try:
            connection.execute(f"CREATE TABLE {table} (id INTEGER, name VARCHAR)")
            connection.execute(f"INSERT INTO {table} VALUES (1, 'alpha'), (2, 'beta')")
            count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count == 2
        finally:
            try:
                connection.execute(f"DROP TABLE IF EXISTS {table}")
            except Exception:
                pass
            connection.close()


