# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import logging

import pytest

from benchbox.core.config_inheritance import resolve_dialect_for_query_translation
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.platforms.base.data_loading import escape_sql_string_literal
from benchbox.platforms.ducklake import (
    DuckLakeAdapter,
    _duckdb_version_supports_ducklake,
    _libpq_quote_value,
    _parse_duckdb_major_minor,
    _redact_secrets,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestDuckLakeVersionGuard:
    @pytest.mark.parametrize(
        "version,expected",
        [
            ("1.3.2", (1, 3)),
            ("1.4.0", (1, 4)),
            ("1.2.0", (1, 2)),
            ("v1.3.2", (1, 3)),
            ("1.3", (1, 3)),
            ("1.4.0-dev123", (1, 4)),
        ],
    )
    def test_parse_major_minor(self, version, expected):
        assert _parse_duckdb_major_minor(version) == expected

    @pytest.mark.parametrize("bad_version", [None, "", "not-a-version", "v"])
    def test_parse_major_minor_rejects_unparseable(self, bad_version):
        assert _parse_duckdb_major_minor(bad_version) is None

    @pytest.mark.parametrize(
        "version,supported",
        [
            ("1.2.0", False),
            ("1.2.9", False),
            ("1.3.0", True),
            ("1.3.2", True),
            ("1.4.0", True),
            ("v1.3.2", True),
            ("0.10.0", False),
            (None, False),
            ("garbage", False),
        ],
    )
    def test_meets_minimum_version(self, version, supported):
        assert _duckdb_version_supports_ducklake(version) is supported


class TestDuckLakeFromConfig:
    def test_resolves_default_paths_under_benchmark_runs(self, tmp_path):
        config = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "output_dir": str(tmp_path),
        }
        adapter = DuckLakeAdapter.from_config(config)

        assert adapter.metadata_path.suffix == ".ducklake"
        assert str(adapter.metadata_path).startswith(str(tmp_path))
        assert adapter.data_path.name != ""
        assert "ducklake_data" in adapter.data_path.parts
        assert not adapter.metadata_path.parent.exists()
        assert not adapter.data_path.exists()

    def test_honors_explicit_argparse_style_keys(self, tmp_path):
        metadata_path = tmp_path / "custom" / "catalog.ducklake"
        data_path = tmp_path / "custom" / "data"
        config = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "ducklake_metadata_path": str(metadata_path),
            "ducklake_data_path": str(data_path),
        }
        adapter = DuckLakeAdapter.from_config(config)

        assert adapter.metadata_path == metadata_path
        assert adapter.data_path == data_path

    def test_honors_explicit_normalized_keys(self, tmp_path):
        metadata_path = tmp_path / "normalized" / "catalog.ducklake"
        data_path = tmp_path / "normalized" / "data"
        config = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "metadata_path": str(metadata_path),
            "data_path": str(data_path),
        }
        adapter = DuckLakeAdapter.from_config(config)

        assert adapter.metadata_path == metadata_path
        assert adapter.data_path == data_path

    def test_argparse_style_key_takes_precedence_over_normalized(self, tmp_path):
        preferred = tmp_path / "preferred.ducklake"
        ignored = tmp_path / "ignored.ducklake"
        config = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "ducklake_metadata_path": str(preferred),
            "metadata_path": str(ignored),
            "ducklake_data_path": str(tmp_path / "data"),
        }
        adapter = DuckLakeAdapter.from_config(config)

        assert adapter.metadata_path == preferred

    def test_honors_metadata_and_data_path_from_nested_options(self, tmp_path):
        metadata_path = tmp_path / "nested" / "catalog.ducklake"
        data_path = tmp_path / "nested" / "data"
        config = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "options": {"metadata_path": str(metadata_path), "data_path": str(data_path)},
        }
        adapter = DuckLakeAdapter.from_config(config)

        assert adapter.metadata_path == metadata_path
        assert adapter.data_path == data_path


class TestDuckLakeAdapterBasics:
    def test_platform_name(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
        )
        assert adapter.platform_name == "DuckLake"

    def test_target_dialect_is_duckdb(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
        )
        assert adapter.get_target_dialect() == "duckdb"
        assert resolve_dialect_for_query_translation("ducklake") == "duckdb"

    def test_init_does_not_create_directories(self, tmp_path):
        metadata_path = tmp_path / "nested" / "catalog.ducklake"
        data_path = tmp_path / "nested" / "data"
        adapter = DuckLakeAdapter(metadata_path=str(metadata_path), data_path=str(data_path))

        assert adapter.metadata_path == metadata_path
        assert adapter.data_path == data_path
        assert not metadata_path.parent.exists()
        assert not data_path.exists()

    def test_constructs_with_fallback_paths_without_touching_disk(self, tmp_path, monkeypatch):
        import benchbox.utils.path_utils as path_utils

        monkeypatch.setattr(
            path_utils,
            "get_benchmark_runs_databases_path",
            lambda *args, **kwargs: tmp_path / "fallback",
        )

        adapter = DuckLakeAdapter()
        assert adapter.metadata_path.suffix == ".ducklake"
        assert str(adapter.metadata_path).startswith(str(tmp_path))
        assert not (tmp_path / "fallback").exists()

    def test_create_connection_rejects_old_duckdb_version(self, tmp_path, monkeypatch):
        metadata_path = tmp_path / "catalog.ducklake"
        data_path = tmp_path / "data"
        adapter = DuckLakeAdapter(metadata_path=str(metadata_path), data_path=str(data_path))

        import benchbox.platforms.ducklake as ducklake_module

        monkeypatch.setattr(ducklake_module, "_duckdb_version_supports_ducklake", lambda version: False)

        with pytest.raises(RuntimeError, match=r"DuckDB >= 1\.3"):
            adapter.create_connection()


class TestDuckLakeSchemaRewrite:
    def test_rewrite_strips_inline_and_table_level_primary_keys(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
        )
        statement = (
            "CREATE TABLE write_ops_log (\n"
            "  log_id INTEGER PRIMARY KEY,\n"
            "  operation_id VARCHAR(100) NOT NULL,\n"
            "  PRIMARY KEY (operation_id)\n"
            ");"
        )
        rewritten = adapter._rewrite_schema_statement(statement)
        assert "PRIMARY KEY" not in rewritten.upper()
        assert '"log_id" INT' in rewritten
        assert '"operation_id" TEXT(100) NOT NULL' in rewritten

    def test_rewrite_matches_write_primitives_staging_ddl(self, tmp_path):
        from benchbox.core.write_primitives.schema import get_all_staging_tables_sql

        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
        )
        for chunk in get_all_staging_tables_sql("duckdb").split("\n\n"):
            if chunk.strip():
                assert "PRIMARY KEY" not in adapter._rewrite_schema_statement(chunk).upper()

    def test_duckdb_default_rewrite_is_identity(self):
        from benchbox.platforms.duckdb import DuckDBAdapter

        statement = "CREATE TABLE t (\n  id INTEGER PRIMARY KEY\n);"
        assert DuckDBAdapter()._rewrite_schema_statement(statement) == statement

    def test_operation_platform_key_resolves_ducklake(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
        )
        assert adapter.operation_platform_key == "ducklake"

    def test_operation_platform_fallback_key_resolves_duckdb(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
        )
        assert adapter.operation_platform_fallback_key == "duckdb"

    def test_ducklake_pk_capability_bypasses_lock_and_ddl(self):
        from benchbox.core.write_primitives.benchmark import _pk_lock_bypass_required
        from benchbox.core.write_primitives.schema import _supports_primary_keys

        assert _pk_lock_bypass_required("ducklake") is True
        assert _supports_primary_keys("ducklake") is False


class TestDuckLakeCatalogValidation:
    def test_default_catalog_is_duckdb(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
        )
        assert adapter.catalog == "duckdb"

    @pytest.mark.parametrize("catalog", ["duckdb", "sqlite", "postgres"])
    def test_valid_catalogs_accepted(self, tmp_path, catalog):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
            catalog=catalog,
        )
        assert adapter.catalog == catalog

    @pytest.mark.parametrize("catalog", ["mysql", "not-a-catalog", "DUCKDB!"])
    def test_invalid_catalog_raises(self, tmp_path, catalog):
        with pytest.raises(ValueError, match="Unsupported DuckLake catalog backend"):
            DuckLakeAdapter(
                metadata_path=str(tmp_path / "catalog.ducklake"),
                data_path=str(tmp_path / "data"),
                catalog=catalog,
            )

    def test_empty_string_catalog_defaults_to_duckdb(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
            catalog="",
        )
        assert adapter.catalog == "duckdb"

    def test_catalog_is_case_insensitive(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
            catalog="SQLite",
        )
        assert adapter.catalog == "sqlite"


class TestDuckLakeSqliteCatalog:
    def test_default_derived_ducklake_suffix_swapped_to_sqlite(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
            catalog="sqlite",
        )
        assert adapter.metadata_path == tmp_path / "catalog.sqlite"

    def test_explicit_sqlite_suffix_left_alone(self, tmp_path):
        explicit = tmp_path / "custom_name.sqlite"
        adapter = DuckLakeAdapter(
            metadata_path=str(explicit),
            data_path=str(tmp_path / "data"),
            catalog="sqlite",
        )
        assert adapter.metadata_path == explicit

    def test_duckdb_catalog_keeps_ducklake_suffix(self, tmp_path):
        explicit = tmp_path / "catalog.ducklake"
        adapter = DuckLakeAdapter(
            metadata_path=str(explicit),
            data_path=str(tmp_path / "data"),
        )
        assert adapter.metadata_path == explicit


class TestDuckLakeAttachTargetBuilder:
    def test_duckdb_attach_target_is_bare_metadata_path(self, tmp_path):
        metadata_path = tmp_path / "catalog.ducklake"
        adapter = DuckLakeAdapter(metadata_path=str(metadata_path), data_path=str(tmp_path / "data"))
        assert adapter._build_catalog_attach_target() == str(metadata_path)

    def test_sqlite_attach_target_has_sqlite_prefix(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
            catalog="sqlite",
        )
        target = adapter._build_catalog_attach_target()
        assert target == f"sqlite:{tmp_path / 'catalog.sqlite'}"

    def test_postgres_attach_target_uses_pg_params(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
            catalog="postgres",
            pg_host="pg.example.com",
            pg_port=5433,
            pg_database="mydb",
            pg_user="alice",
            pg_password="s3cr3t",
        )
        target = adapter._build_catalog_attach_target()
        assert target == "postgres:dbname=mydb host=pg.example.com user=alice password=s3cr3t port=5433"

    def test_postgres_attach_target_applies_defaults(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
            catalog="postgres",
        )
        assert adapter.pg_host == "localhost"
        assert adapter.pg_port == 5432
        assert adapter.pg_user == "postgres"
        assert adapter.pg_password is None
        assert adapter.pg_database == "ducklake_catalog"
        target = adapter._build_catalog_attach_target()
        assert target == "postgres:dbname=ducklake_catalog host=localhost user=postgres port=5432"

    def test_postgres_connstring_quotes_values_with_spaces(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
            catalog="postgres",
            pg_database="my db",
            pg_password="pa'ss",
        )
        connstring = adapter._build_postgres_connstring()
        assert "dbname='my db'" in connstring
        assert r"password='pa\'ss'" in connstring


class TestLibpqQuoteValue:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("localhost", "localhost"),
            ("5432", "5432"),
            ("my_db-1", "my_db-1"),
            ("", "''"),
            ("my db", "'my db'"),
            ("a\tb", "'a\tb'"),
            ("pa'ss", r"'pa\'ss'"),
            ("a\\b", r"'a\\b'"),
            ("a\\'b", r"'a\\\'b'"),
        ],
    )
    def test_quotes_components_per_libpq_rules(self, value, expected):
        assert _libpq_quote_value(value) == expected


class TestDuckLakePostgresAttachInjection:
    def _final_attach_literal(self, adapter):
        target = adapter._build_catalog_attach_target()
        return "'ducklake:" + escape_sql_string_literal(target) + "'"

    @pytest.mark.parametrize(
        "malicious_password",
        [
            "pass'word",
            "x' OR sslmode=disable",
            "'; DROP TABLE lineitem; --",
            "a' host='evil.example.com",
        ],
    )
    def test_malicious_password_stays_inside_the_literal(self, tmp_path, malicious_password):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
            catalog="postgres",
            pg_database="benchdb",
            pg_user="alice",
            pg_password=malicious_password,
        )
        literal = self._final_attach_literal(adapter)

        assert literal.startswith("'") and literal.endswith("'")
        interior = literal[1:-1]
        assert "'" not in interior.replace("''", "")

        connstring = adapter._build_postgres_connstring()
        assert connstring.count("password=") == 1
        assert connstring.startswith("dbname=benchdb host=") or "dbname=benchdb" in connstring
        assert "password='" in connstring

    def test_backslash_in_password_survives_both_escaping_layers(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
            catalog="postgres",
            pg_database="benchdb",
            pg_password="a\\b'c",
        )
        connstring = adapter._build_postgres_connstring()
        assert r"password='a\\b\'c'" in connstring

        literal = self._final_attach_literal(adapter)
        assert literal.startswith("'") and literal.endswith("'")
        assert "'" not in literal[1:-1].replace("''", "")


class TestDuckLakeCredentialRedaction:
    SENTINEL = "s3nt1nel-pg-passw0rd"

    def _failing_adapter(self, tmp_path, monkeypatch, error_message, **kwargs):
        from benchbox.platforms.duckdb import DuckDBAdapter

        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=kwargs.pop("data_path", str(tmp_path / "data")),
            **kwargs,
        )

        class _StubSetupConn:
            def execute(self, sql):
                if sql.startswith("ATTACH"):
                    raise RuntimeError(error_message(sql))
                return self

            def close(self):
                pass

        monkeypatch.setattr(DuckDBAdapter, "create_connection", lambda self, **_: _StubSetupConn())
        return adapter

    @pytest.mark.parametrize(
        "password",
        [
            "s3nt1nel-pg-passw0rd",
            "pass word with spaces",
            "pass'word",
            "a\\b'c",
        ],
    )
    def test_postgres_attach_failure_never_leaks_the_password(self, tmp_path, monkeypatch, password):
        adapter = self._failing_adapter(
            tmp_path,
            monkeypatch,
            lambda sql: f"Connection Error: could not connect: {sql} port=5432: FATAL: password authentication failed",
            catalog="postgres",
            pg_database="benchdb",
            pg_user="alice",
            pg_password=password,
        )

        with pytest.raises(RuntimeError) as excinfo:
            adapter.create_connection()

        message = str(excinfo.value)
        assert password not in message
        libpq_quoted = _libpq_quote_value(password)
        assert libpq_quoted not in message
        assert escape_sql_string_literal(libpq_quoted) not in message
        assert excinfo.value.__cause__ is None
        assert "Failed to initialize the DuckLake catalog" in message
        assert "catalog=postgres" in message

    def test_redaction_handles_keyword_shaped_text_inside_quoted_password(self):
        message = r"""Connection Error: password="abc key=value tail\'xyz" port=5432"""
        assert _redact_secrets(message) == "Connection Error: password=**** port=5432"

    def test_postgres_attach_failure_keeps_non_secret_context(self, tmp_path, monkeypatch):
        adapter = self._failing_adapter(
            tmp_path,
            monkeypatch,
            lambda sql: "Connection Error: connection to server at 'db.example.com' failed: refused",
            catalog="postgres",
            pg_database="benchdb",
            pg_user="alice",
            pg_password=self.SENTINEL,
        )

        with pytest.raises(RuntimeError) as excinfo:
            adapter.create_connection()

        message = str(excinfo.value)
        assert self.SENTINEL not in message
        assert "connection to server at 'db.example.com' failed: refused" in message

    def test_non_postgres_attach_failure_keeps_paths_and_cause(self, tmp_path, monkeypatch):
        adapter = self._failing_adapter(
            tmp_path,
            monkeypatch,
            lambda sql: "IO Error: catalog file is locked",
        )

        with pytest.raises(RuntimeError) as excinfo:
            adapter.create_connection()

        message = str(excinfo.value)
        assert "catalog.ducklake" in message
        assert str(tmp_path / "data") in message
        assert "IO Error: catalog file is locked" in message
        assert isinstance(excinfo.value.__cause__, RuntimeError)

    def test_s3_secret_never_leaks_through_attach_failure(self, tmp_path, monkeypatch):
        adapter = self._failing_adapter(
            tmp_path,
            monkeypatch,
            lambda sql: "IO Error: HTTP 403 using SECRET 'top-s3cret-key' for bucket",
            data_path="s3://my-bucket/bench/",
            s3_key_id="AKIAEXAMPLE",
            s3_secret="top-s3cret-key",
        )

        with pytest.raises(RuntimeError) as excinfo:
            adapter.create_connection()

        message = str(excinfo.value)
        assert "top-s3cret-key" not in message
        assert "AKIAEXAMPLE" not in message
        assert excinfo.value.__cause__ is None


class TestRedactSecretsHelper:
    def test_redacts_every_encoding_of_a_supplied_secret(self):
        secret = "pass word'x"
        quoted = _libpq_quote_value(secret)
        message = f"raw={secret} quoted={quoted} sql={escape_sql_string_literal(quoted)}"
        redacted = _redact_secrets(message, secret)
        assert secret not in redacted
        assert quoted not in redacted

    def test_redacts_password_component_without_knowing_the_value(self):
        message = "dbname=benchdb host=db user=alice password=hunter2 port=5432: FATAL"
        redacted = _redact_secrets(message)
        assert "hunter2" not in redacted
        assert "port=5432" in redacted
        assert "dbname=benchdb" in redacted

    def test_redacts_quoted_password_containing_spaces(self):
        message = "dbname=benchdb password='hunter 2 with spaces' port=5432"
        redacted = _redact_secrets(message)
        assert "hunter" not in redacted
        assert "port=5432" in redacted

    def test_redacts_secret_clause(self):
        message = "CREATE OR REPLACE SECRET benchbox_ducklake_s3 (TYPE s3, KEY_ID 'AKIA', SECRET 'topsecret')"
        redacted = _redact_secrets(message)
        assert "topsecret" not in redacted
        assert "benchbox_ducklake_s3" in redacted

    def test_leaves_credential_free_messages_untouched(self):
        message = "IO Error: catalog file is locked"
        assert _redact_secrets(message, None) == message


class TestDuckLakeDeploymentModeIsApplied:
    def _from_config(self, tmp_path, **extra):
        config = {"benchmark": "tpch", "scale_factor": 0.01, "output_dir": str(tmp_path)}
        config.update(extra)
        return DuckLakeAdapter.from_config(config)

    @pytest.mark.parametrize(
        "mode,expected_catalog",
        [
            ("local", "duckdb"),
            ("local_catalog_s3", "duckdb"),
            ("postgres_catalog", "postgres"),
            ("postgres_catalog_s3", "postgres"),
        ],
    )
    def test_mode_supplies_the_catalog_axis(self, tmp_path, mode, expected_catalog):
        assert self._from_config(tmp_path, deployment_mode=mode).catalog == expected_catalog

    def test_no_deployment_mode_leaves_behaviour_unchanged(self, tmp_path):
        assert self._from_config(tmp_path).catalog == "duckdb"

    def test_explicit_catalog_option_beats_the_mode(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="benchbox.platforms.ducklake"):
            adapter = self._from_config(tmp_path, deployment_mode="postgres_catalog", options={"catalog": "sqlite"})

        assert adapter.catalog == "sqlite"
        assert "implies catalog=postgres" in caplog.text
        assert "using the explicit catalog=sqlite" in caplog.text

    def test_s3_mode_with_a_local_data_path_warns_and_uses_the_given_path(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="benchbox.platforms.ducklake"):
            adapter = self._from_config(tmp_path, deployment_mode="postgres_catalog_s3")

        assert adapter._data_path_is_cloud is False
        assert "implies a cloud data_path" in caplog.text

    def test_local_mode_with_an_s3_data_path_warns(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="benchbox.platforms.ducklake"):
            adapter = self._from_config(
                tmp_path, deployment_mode="postgres_catalog", options={"data_path": "s3://bucket/x"}
            )

        assert adapter._data_path_is_cloud is True
        assert "implies a local data_path" in caplog.text

    def test_matching_mode_and_options_produce_no_warning(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="benchbox.platforms.ducklake"):
            adapter = self._from_config(
                tmp_path,
                deployment_mode="postgres_catalog_s3",
                options={"catalog": "postgres", "data_path": "s3://bucket/x"},
            )

        assert adapter.catalog == "postgres"
        assert adapter._data_path_is_cloud is True
        assert "implies" not in caplog.text

    def test_unknown_mode_fails_closed(self, tmp_path):
        with pytest.raises(ValueError, match="does not support deployment mode"):
            self._from_config(tmp_path, deployment_mode="not-a-mode")

    def test_every_registered_mode_is_mapped(self):
        from benchbox.core.platform_registry import PlatformRegistry
        from benchbox.platforms.ducklake import _DEPLOYMENT_MODE_AXES

        registered = set(PlatformRegistry.get_available_deployment_modes("ducklake"))
        assert registered == set(_DEPLOYMENT_MODE_AXES), (
            "ducklake deployment modes in the registry and _DEPLOYMENT_MODE_AXES have drifted"
        )


class TestDuckLakeFromConfigCatalogOptions:
    def test_resolves_catalog_from_flat_config(self, tmp_path):
        config = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "output_dir": str(tmp_path),
            "catalog": "sqlite",
        }
        adapter = DuckLakeAdapter.from_config(config)
        assert adapter.catalog == "sqlite"

    def test_resolves_catalog_from_nested_options(self, tmp_path):
        config = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "output_dir": str(tmp_path),
            "options": {"catalog": "postgres", "pg_host": "dbhost", "pg_port": 5433},
        }
        adapter = DuckLakeAdapter.from_config(config)
        assert adapter.catalog == "postgres"
        assert adapter.pg_host == "dbhost"
        assert adapter.pg_port == 5433

    def test_no_catalog_option_defaults_to_duckdb(self, tmp_path):
        config = {"benchmark": "tpch", "scale_factor": 0.01, "output_dir": str(tmp_path)}
        adapter = DuckLakeAdapter.from_config(config)
        assert adapter.catalog == "duckdb"

    def test_resolves_s3_creds_from_nested_options(self, tmp_path):
        config = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "output_dir": str(tmp_path),
            "options": {"s3_key_id": "AKIA...", "s3_secret": "shh", "s3_region": "us-east-1"},
        }
        adapter = DuckLakeAdapter.from_config(config)
        assert adapter.s3_key_id == "AKIA..."
        assert adapter.s3_secret == "shh"
        assert adapter.s3_region == "us-east-1"

    def test_resolves_force_recreate_from_nested_options(self, tmp_path):
        config = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "output_dir": str(tmp_path),
            "options": {"force_recreate": True},
        }
        adapter = DuckLakeAdapter.from_config(config)
        assert adapter.force_recreate is True

    def test_resolves_force_recreate_from_legacy_flat_force_key(self, tmp_path):
        config = {
            "benchmark": "tpch",
            "scale_factor": 0.01,
            "output_dir": str(tmp_path),
            "force": True,
        }
        adapter = DuckLakeAdapter.from_config(config)
        assert adapter.force_recreate is True

    def test_force_recreate_defaults_to_false(self, tmp_path):
        config = {"benchmark": "tpch", "scale_factor": 0.01, "output_dir": str(tmp_path)}
        adapter = DuckLakeAdapter.from_config(config)
        assert adapter.force_recreate is False


class TestDuckLakeRunIdentity:
    class _StubConn:
        def __init__(self):
            self.executed: list[str] = []
            self.inserted: list[tuple[str, str]] = []

        def execute(self, sql):
            self.executed.append(sql)
            return self

        def executemany(self, sql, rows):
            self.executed.append(sql)
            self.inserted.extend(rows)

    def test_marker_table_omits_unsupported_primary_key_constraint(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
            benchmark="tpch",
            scale_factor=0.01,
        )
        conn = self._StubConn()

        adapter._write_run_identity(conn, object())

        create_sql = conn.executed[0]
        assert create_sql.startswith('CREATE TABLE IF NOT EXISTS lake.main."__benchbox_run_identity"')
        assert "PRIMARY KEY" not in create_sql
        assert "UNIQUE" not in create_sql
        assert {key for key, _value in conn.inserted} == {"benchmark", "scale_factor", "tuning_sha256"}


class TestDuckLakePostgresCatalogReuse:
    class _StubConn:
        def __init__(self, tables: list[str] | list[tuple[str, str]]):
            self._tables = tables
            self.executed: list[str] = []

        def execute(self, sql):
            self.executed.append(sql)
            return self

        def fetchall(self):
            return [table if isinstance(table, tuple) else ("main", table) for table in self._tables]

    def _adapter(self, tmp_path, **kwargs):
        return DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
            catalog="postgres",
            pg_database="benchdb",
            **kwargs,
        )

    def test_populated_catalog_without_force_is_reused(self, tmp_path):
        adapter = self._adapter(tmp_path)
        conn = self._StubConn(["lineitem", "orders"])

        adapter._resolve_postgres_catalog_reuse(conn)

        assert adapter.database_was_reused is True
        assert not any(sql.startswith("DROP TABLE") for sql in conn.executed)

    def test_populated_catalog_with_force_is_dropped_server_side(self, tmp_path):
        adapter = self._adapter(tmp_path, force_recreate=True)
        conn = self._StubConn(["lineitem", "orders"])

        adapter._resolve_postgres_catalog_reuse(conn)

        assert adapter.database_was_reused is False
        dropped = [sql for sql in conn.executed if sql.startswith("DROP TABLE")]
        assert dropped == [
            'DROP TABLE IF EXISTS lake."main"."lineitem"',
            'DROP TABLE IF EXISTS lake."main"."orders"',
        ]

    def test_force_also_clears_the_local_data_path(self, tmp_path):
        data_path = tmp_path / "data"
        data_path.mkdir(parents=True)
        (data_path / "part-0.parquet").write_text("superseded")
        adapter = self._adapter(tmp_path, force_recreate=True)
        adapter.data_path = data_path

        adapter._resolve_postgres_catalog_reuse(self._StubConn(["lineitem"]))

        assert list(data_path.rglob("*.parquet")) == []
        assert data_path.is_dir()

    def test_force_on_cloud_data_path_does_not_delete_but_warns(self, tmp_path, caplog):
        adapter = self._adapter(tmp_path, force_recreate=True)
        adapter.data_path = "s3://my-bucket/bench/"
        adapter._data_path_is_cloud = True

        with caplog.at_level(logging.WARNING, logger="benchbox.platforms.ducklake"):
            adapter._resolve_postgres_catalog_reuse(self._StubConn(["lineitem"]))

        assert "did NOT clear the cloud DATA_PATH" in caplog.text

    def test_empty_catalog_leaves_the_fresh_run_assumption(self, tmp_path):
        adapter = self._adapter(tmp_path)
        conn = self._StubConn([])

        adapter._resolve_postgres_catalog_reuse(conn)

        assert adapter.database_was_reused is False
        assert not any(sql.startswith("DROP TABLE") for sql in conn.executed)

    def test_stale_local_metadata_file_does_not_mark_a_postgres_run_reused(self, tmp_path):
        adapter = self._adapter(tmp_path)
        adapter.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        adapter.metadata_path.write_text("stale catalog from a duckdb-backed run")

        adapter.handle_existing_database()

        assert adapter.database_was_reused is False
        assert adapter.metadata_path.exists()

    def test_local_catalog_still_decides_reuse_from_metadata_path(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
        )
        adapter.metadata_path.write_text("existing catalog")

        adapter.handle_existing_database()

        assert adapter.database_was_reused is True

    def test_dry_run_never_drops_anything(self, tmp_path):
        adapter = self._adapter(tmp_path, force_recreate=True)
        adapter.dry_run = True
        conn = self._StubConn(["lineitem"])

        adapter._resolve_postgres_catalog_reuse(conn)

        assert conn.executed == []

    def test_table_listing_is_scoped_to_the_lake_catalog(self, tmp_path):
        adapter = self._adapter(tmp_path)
        conn = self._StubConn(["lineitem"])

        adapter._existing_lake_tables(conn)

        assert "duckdb_tables()" in conn.executed[0]
        assert "database_name = 'lake'" in conn.executed[0]

    def _connect_with_stub(self, adapter, monkeypatch, tables):
        from benchbox.platforms.duckdb import DuckDBAdapter

        outer = self

        class _StubSetupConn(outer._StubConn):
            def cursor(self):
                return self

            def close(self):
                pass

        stub = _StubSetupConn(tables)
        monkeypatch.setattr(DuckDBAdapter, "create_connection", lambda self, **_: stub)
        adapter.create_connection()
        return stub

    def test_create_connection_wires_the_resolver_for_postgres(self, tmp_path, monkeypatch):
        adapter = self._adapter(tmp_path)
        self._connect_with_stub(adapter, monkeypatch, ["lineitem"])

        assert adapter.database_was_reused is True

    def test_create_connection_does_not_resolve_for_local_catalogs(self, tmp_path, monkeypatch):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
        )
        stub = self._connect_with_stub(adapter, monkeypatch, ["lineitem"])

        assert adapter.database_was_reused is False
        assert not any("duckdb_tables()" in sql for sql in stub.executed)


class TestDuckLakeOncePerRunExistingDatabaseDecision:
    def _local_adapter(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
            force_recreate=True,
        )
        adapter.metadata_path.write_text("catalog")
        return adapter

    def test_force_recreate_removes_the_catalog_only_on_the_first_decision(self, tmp_path):
        adapter = self._local_adapter(tmp_path)

        adapter.handle_existing_database()
        assert not adapter.metadata_path.exists()

        adapter.metadata_path.write_text("catalog rebuilt by this run")
        adapter.handle_existing_database()

        assert adapter.metadata_path.read_text() == "catalog rebuilt by this run"

    def test_a_new_run_decides_again(self, tmp_path):
        adapter = self._local_adapter(tmp_path)
        adapter.handle_existing_database()
        adapter.metadata_path.write_text("catalog from the previous run")

        adapter._reset_run_scoped_state()
        adapter.handle_existing_database()

        assert not adapter.metadata_path.exists()

    def test_a_validation_connection_never_removes_the_catalog(self, tmp_path):
        adapter = self._local_adapter(tmp_path)

        with adapter.non_destructive_connection_context():
            adapter.handle_existing_database()

        assert adapter.metadata_path.exists()

    def test_postgres_catalog_drops_tables_only_on_the_first_decision(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
            catalog="postgres",
            pg_database="benchdb",
            force_recreate=True,
        )
        connection = TestDuckLakePostgresCatalogReuse._StubConn(["lineitem"])

        adapter._resolve_postgres_catalog_reuse(connection)
        adapter._resolve_postgres_catalog_reuse(connection)

        assert [sql for sql in connection.executed if sql.startswith("DROP TABLE")] == [
            'DROP TABLE IF EXISTS lake."main"."lineitem"'
        ]


class TestDuckLakeForceWithCloudDataPath:
    def test_force_warns_that_the_cloud_prefix_was_not_cleared(self, tmp_path, caplog):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path="s3://my-bucket/bench/",
            force_recreate=True,
        )
        adapter.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        adapter.metadata_path.write_text("")

        with caplog.at_level(logging.WARNING, logger="benchbox.platforms.ducklake"):
            adapter._reset_ducklake_catalog()

        assert not adapter.metadata_path.exists()
        assert "did NOT clear the cloud DATA_PATH" in caplog.text
        assert "s3://my-bucket/bench/" in caplog.text

    def test_local_data_path_is_cleared_and_does_not_warn(self, tmp_path, caplog):
        data_path = tmp_path / "data"
        data_path.mkdir(parents=True)
        (data_path / "part-0.parquet").write_text("stale")
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(data_path),
            force_recreate=True,
        )
        adapter.metadata_path.write_text("")

        with caplog.at_level(logging.WARNING, logger="benchbox.platforms.ducklake"):
            adapter._reset_ducklake_catalog()

        assert list(data_path.rglob("*.parquet")) == []
        assert data_path.is_dir()
        assert "did NOT clear" not in caplog.text


class TestDuckLakeS3Routing:
    def test_cloud_data_path_detected(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path="s3://my-bucket/bench/",
        )
        assert adapter._data_path_is_cloud is True
        assert str(adapter.data_path) == "s3://my-bucket/bench/"

    def test_local_data_path_not_flagged_cloud(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
        )
        assert adapter._data_path_is_cloud is False

    def test_cloud_data_path_skips_local_mkdir_on_connect(self, tmp_path, monkeypatch):
        from benchbox.platforms.duckdb import DuckDBAdapter

        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path="s3://my-bucket/bench/",
        )

        class _StubSetupConn:
            def __init__(self):
                self.executed: list[str] = []

            def execute(self, sql):
                self.executed.append(sql)
                if sql.startswith("ATTACH"):
                    raise RuntimeError("stop-after-attach (test stub)")
                return self

            def close(self):
                pass

        stub_conn = _StubSetupConn()
        monkeypatch.setattr(DuckDBAdapter, "create_connection", lambda self, **_: stub_conn)

        with pytest.raises(RuntimeError, match="Failed to initialize the DuckLake catalog"):
            adapter.create_connection()

        assert any("INSTALL httpfs" in sql for sql in stub_conn.executed)
        assert any("CREATE OR REPLACE SECRET" in sql and "credential_chain" in sql for sql in stub_conn.executed)
        assert any(sql.startswith("ATTACH") for sql in stub_conn.executed)

    def test_s3_secret_sql_defaults_to_credential_chain(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path="s3://my-bucket/bench/",
        )
        sql = adapter._build_s3_secret_sql()
        assert "PROVIDER credential_chain" in sql
        assert "KEY_ID" not in sql

    def test_s3_secret_sql_uses_explicit_keys_when_provided(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path="s3://my-bucket/bench/",
            s3_key_id="AKIAEXAMPLE",
            s3_secret="topsecret",
            s3_region="us-west-2",
        )
        sql = adapter._build_s3_secret_sql()
        assert "KEY_ID 'AKIAEXAMPLE'" in sql
        assert "SECRET 'topsecret'" in sql
        assert "REGION 'us-west-2'" in sql
        assert "credential_chain" not in sql

    def test_s3_secret_sql_omits_key_id_form_without_both_creds(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path="s3://my-bucket/bench/",
            s3_key_id="AKIAEXAMPLE",
        )
        sql = adapter._build_s3_secret_sql()
        assert "PROVIDER credential_chain" in sql

    def test_s3_secret_sql_is_idempotent_create_or_replace(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path="s3://my-bucket/bench/",
        )
        assert adapter._build_s3_secret_sql().startswith("CREATE OR REPLACE SECRET ")


class TestDuckLakeGcsAzureBackends:
    def test_gcs_data_path_detected_as_cloud(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path="gs://my-bucket/bench/",
        )
        assert adapter._data_path_is_cloud is True
        assert str(adapter.data_path) == "gs://my-bucket/bench/"

    def test_azure_data_path_detected_as_cloud(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path="az://my-container/bench/",
        )
        assert adapter._data_path_is_cloud is True
        assert str(adapter.data_path) == "az://my-container/bench/"

    def test_gcs_secret_sql_uses_hmac_keys(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path="gs://my-bucket/bench/",
            gcs_key_id="GOOGEXAMPLE",
            gcs_secret="topsecret",
        )
        sql = adapter._build_gcs_secret_sql()
        assert "TYPE gcs" in sql
        assert "KEY_ID 'GOOGEXAMPLE'" in sql
        assert "SECRET 'topsecret'" in sql
        assert sql.startswith("CREATE OR REPLACE SECRET ")

    def test_gcs_secret_sql_requires_both_keys(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path="gs://my-bucket/bench/",
            gcs_key_id="GOOGEXAMPLE",
        )
        with pytest.raises(ValueError, match="HMAC credentials"):
            adapter._build_gcs_secret_sql()

    def test_azure_secret_sql_prefers_connection_string(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path="az://my-container/bench/",
            azure_connection_string="DefaultEndpointsProtocol=https;AccountName=acct;AccountKey=key",
            azure_account_name="acct",
        )
        sql = adapter._build_azure_secret_sql()
        assert "TYPE azure" in sql
        assert "CONNECTION_STRING 'DefaultEndpointsProtocol=https;AccountName=acct;AccountKey=key'" in sql
        assert sql.startswith("CREATE OR REPLACE SECRET ")

    def test_azure_secret_sql_uses_chain_with_account_name(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path="az://my-container/bench/",
            azure_account_name="myaccount",
        )
        sql = adapter._build_azure_secret_sql()
        assert "PROVIDER credential_chain" in sql
        assert "ACCOUNT_NAME 'myaccount'" in sql

    def test_azure_secret_sql_requires_some_credential(self, tmp_path):
        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path="az://my-container/bench/",
        )
        with pytest.raises(ValueError, match="requires credentials"):
            adapter._build_azure_secret_sql()

    def test_cloud_secret_dispatch_selects_extension_per_family(self, tmp_path):
        s3 = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path="s3://my-bucket/bench/",
        )
        assert s3._build_cloud_secret_sql()[0] == "httpfs"
        assert "TYPE s3" in s3._build_cloud_secret_sql()[1]

        gcs = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path="gcs://my-bucket/bench/",
            gcs_key_id="GOOGEXAMPLE",
            gcs_secret="topsecret",
        )
        extension, sql = gcs._build_cloud_secret_sql()
        assert extension == "httpfs"
        assert "TYPE gcs" in sql

        azure = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path="abfss://my-container/bench/",
            azure_account_name="myaccount",
        )
        extension, sql = azure._build_cloud_secret_sql()
        assert extension == "azure"
        assert "TYPE azure" in sql

    def test_gcs_connect_installs_httpfs_and_gcs_secret(self, tmp_path, monkeypatch):
        from benchbox.platforms.duckdb import DuckDBAdapter

        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path="gs://my-bucket/bench/",
            gcs_key_id="GOOGEXAMPLE",
            gcs_secret="topsecret",
        )

        class _StubSetupConn:
            def __init__(self):
                self.executed: list[str] = []

            def execute(self, sql):
                self.executed.append(sql)
                if sql.startswith("ATTACH"):
                    raise RuntimeError("stop-after-attach (test stub)")
                return self

            def close(self):
                pass

        stub_conn = _StubSetupConn()
        monkeypatch.setattr(DuckDBAdapter, "create_connection", lambda self, **_: stub_conn)

        with pytest.raises(RuntimeError, match="Failed to initialize the DuckLake catalog"):
            adapter.create_connection()

        assert any("INSTALL httpfs" in sql for sql in stub_conn.executed)
        assert any("TYPE gcs" in sql for sql in stub_conn.executed)
        assert any(sql.startswith("ATTACH") for sql in stub_conn.executed)

    def test_azure_connect_installs_azure_extension(self, tmp_path, monkeypatch):
        from benchbox.platforms.duckdb import DuckDBAdapter

        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path="az://my-container/bench/",
            azure_account_name="myaccount",
        )

        class _StubSetupConn:
            def __init__(self):
                self.executed: list[str] = []

            def execute(self, sql):
                self.executed.append(sql)
                if sql.startswith("ATTACH"):
                    raise RuntimeError("stop-after-attach (test stub)")
                return self

            def close(self):
                pass

        stub_conn = _StubSetupConn()
        monkeypatch.setattr(DuckDBAdapter, "create_connection", lambda self, **_: stub_conn)

        with pytest.raises(RuntimeError, match="Failed to initialize the DuckLake catalog"):
            adapter.create_connection()

        assert any("INSTALL azure" in sql for sql in stub_conn.executed)
        assert any("LOAD azure" in sql for sql in stub_conn.executed)
        assert any("TYPE azure" in sql for sql in stub_conn.executed)

    def test_new_credential_forms_are_redacted(self):
        message = (
            "CREATE OR REPLACE SECRET benchbox_ducklake_gcs (TYPE gcs, KEY_ID 'GOOGEXAMPLE', SECRET 'topsecret') "
            "CREATE OR REPLACE SECRET benchbox_ducklake_azure (TYPE azure, "
            "CONNECTION_STRING 'DefaultEndpointsProtocol=https;AccountKey=key') "
            "CREATE OR REPLACE SECRET benchbox_ducklake_azure (TYPE azure, "
            "PROVIDER credential_chain, ACCOUNT_NAME 'myaccount')"
        )
        redacted = _redact_secrets(message)
        assert "GOOGEXAMPLE" not in redacted
        assert "topsecret" not in redacted
        assert "AccountKey=key" not in redacted
        assert "myaccount" not in redacted

    def test_azure_secret_failure_never_leaks_account_name(self, tmp_path, monkeypatch):
        from benchbox.platforms.duckdb import DuckDBAdapter

        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path="az://my-container/bench/",
            azure_account_name="myaccount",
        )

        class _StubSetupConn:
            def execute(self, sql):
                if sql.startswith("CREATE OR REPLACE SECRET"):
                    raise RuntimeError(f"Catalog Error: {sql}")
                return self

            def close(self):
                pass

        monkeypatch.setattr(DuckDBAdapter, "create_connection", lambda self, **_: _StubSetupConn())

        with pytest.raises(RuntimeError) as excinfo:
            adapter.create_connection()

        message = str(excinfo.value)
        assert "myaccount" not in message
        assert "explicit key/secret" in message
        assert excinfo.value.__cause__ is None

    def test_azure_attach_failure_never_leaks_account_name(self, tmp_path, monkeypatch):
        adapter = TestDuckLakeCredentialRedaction()._failing_adapter(
            tmp_path,
            monkeypatch,
            lambda sql: f"HTTP Error: could not list container for ACCOUNT_NAME 'myaccount': {sql}",
            data_path="az://my-container/bench/",
            azure_account_name="myaccount",
        )

        with pytest.raises(RuntimeError) as excinfo:
            adapter.create_connection()

        message = str(excinfo.value)
        assert "myaccount" not in message
        assert excinfo.value.__cause__ is None

    def test_new_credentials_scrubbed_from_result_metadata(self, tmp_path):
        metadata = TestDuckLakeResultMetadataRecordsBacking()._metadata(
            tmp_path,
            data_path="gs://bucket/x",
            gcs_key_id="GOOG-sentinel",
            gcs_secret="shh-sentinel",
            azure_connection_string="conn-sentinel",
            azure_account_name="acct-sentinel",
        )
        blob = json.dumps(metadata, default=str)
        for sentinel in ("GOOG-sentinel", "shh-sentinel", "conn-sentinel", "acct-sentinel"):
            assert sentinel not in blob, f"{sentinel} leaked into exported result metadata"


class TestDuckLakeResultMetadataRecordsBacking:
    def _metadata(self, tmp_path, **options):
        adapter = DuckLakeAdapter.from_config(
            {"benchmark": "tpch", "scale_factor": 0.01, "output_dir": str(tmp_path), "options": options}
        )
        return adapter.get_normalized_result_metadata()

    @pytest.mark.parametrize(
        "options,catalog,is_cloud,location",
        [
            ({}, "duckdb", False, "local_filesystem"),
            ({"catalog": "sqlite"}, "sqlite", False, "local_filesystem"),
            ({"catalog": "postgres"}, "postgres", False, "local_filesystem"),
            ({"data_path": "s3://bucket/x"}, "duckdb", True, "cloud_object_store"),
            ({"catalog": "postgres", "data_path": "s3://bucket/x"}, "postgres", True, "cloud_object_store"),
        ],
    )
    def test_both_axes_are_recorded(self, tmp_path, options, catalog, is_cloud, location):
        storage = self._metadata(tmp_path, **options)["platform_storage"]

        assert storage["catalog_backend"] == catalog
        assert storage["data_path_is_cloud"] is is_cloud
        assert storage["storage_location"] == location

    def test_no_credential_material_reaches_result_metadata(self, tmp_path):
        metadata = self._metadata(
            tmp_path,
            catalog="postgres",
            data_path="s3://bucket/x",
            pg_user="alice-sentinel",
            pg_password="hunter2-sentinel",
            s3_key_id="AKIA-sentinel",
            s3_secret="shh-sentinel",
        )
        blob = json.dumps(metadata, default=str)

        for sentinel in ("alice-sentinel", "hunter2-sentinel", "AKIA-sentinel", "shh-sentinel"):
            assert sentinel not in blob, f"{sentinel} leaked into exported result metadata"

    def test_non_credential_config_still_exported(self, tmp_path):
        metadata = self._metadata(
            tmp_path, catalog="postgres", pg_host="db.example.com", pg_port=5433, pg_database="benchdb"
        )
        blob = json.dumps(metadata, default=str)

        assert "db.example.com" in blob
        assert "benchdb" in blob


class TestDuckLakeRegistration:
    def test_platform_registered_in_registry(self):
        caps = PlatformRegistry.get_platform_capabilities("ducklake")
        assert caps is not None
        assert caps.supports_sql is True
        assert caps.supports_dataframe is False

    def test_inherits_from_duckdb(self):
        caps = PlatformRegistry.get_platform_capabilities("ducklake")
        assert caps.inherits_from == "duckdb"
        assert caps.platform_family == "duckdb"

    def test_platform_appears_in_available_platforms(self):
        available = PlatformRegistry.get_available_platforms()
        assert "ducklake" in available

    def test_support_status_is_beta(self):
        assert PlatformRegistry.get_platform_support_status("ducklake") == "beta"

    def test_adapter_class_resolves(self):
        adapter_class = PlatformRegistry.get_adapter_class("ducklake")
        assert adapter_class is DuckLakeAdapter

    def test_appears_in_list_available_platforms(self):
        from benchbox.platforms import list_available_platforms

        platforms = list_available_platforms()
        assert "ducklake" in platforms
        assert platforms["ducklake"] is True

    def test_lazy_export_resolves_adapter(self):
        import benchbox.platforms as platforms_module

        assert platforms_module.DuckLakeAdapter is DuckLakeAdapter
