from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from benchbox.core.write_primitives.benchmark import (
    WritePrimitivesBenchmark,
    _resolve_validation_sql,
)
from benchbox.core.write_primitives.catalog.loader import (
    ValidationQuery,
    WritePrimitivesCatalogError,
    _parse_validation_platform_overrides,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture()
def wp(tmp_path: Path) -> WritePrimitivesBenchmark:
    return WritePrimitivesBenchmark(output_dir=tmp_path)


class TestValidationQueryDefault:
    def test_platform_overrides_defaults_to_empty_dict(self):
        vq = ValidationQuery(id="v1", sql="SELECT 1")
        assert vq.platform_overrides == {}

    def test_platform_overrides_can_be_set(self):
        vq = ValidationQuery(
            id="v1",
            sql="SELECT 1",
            platform_overrides={"clickhouse": "SELECT 2", "redshift": None},
        )
        assert vq.platform_overrides == {"clickhouse": "SELECT 2", "redshift": None}


class TestParseValidationPlatformOverrides:
    def test_missing_returns_empty(self):
        assert _parse_validation_platform_overrides("op", "v1", {}) == {}

    def test_explicit_none_returns_empty(self):
        assert _parse_validation_platform_overrides("op", "v1", {"platform_overrides": None}) == {}

    def test_string_override_parsed(self):
        result = _parse_validation_platform_overrides("op", "v1", {"platform_overrides": {"clickhouse": "SELECT 1"}})
        assert result == {"clickhouse": "SELECT 1"}

    def test_null_override_parsed_as_skip(self):
        result = _parse_validation_platform_overrides("op", "v1", {"platform_overrides": {"redshift": None}})
        assert result == {"redshift": None}

    def test_mixed_overrides(self):
        result = _parse_validation_platform_overrides(
            "op",
            "v1",
            {"platform_overrides": {"clickhouse": "SELECT a", "redshift": None}},
        )
        assert result == {"clickhouse": "SELECT a", "redshift": None}

    def test_keys_are_stripped(self):
        result = _parse_validation_platform_overrides(
            "op", "v1", {"platform_overrides": {"  clickhouse  ": "SELECT 1"}}
        )
        assert result == {"clickhouse": "SELECT 1"}

    def test_non_mapping_rejected(self):
        with pytest.raises(WritePrimitivesCatalogError, match="must be a mapping"):
            _parse_validation_platform_overrides("op", "v1", {"platform_overrides": ["bad"]})

    def test_empty_string_key_rejected(self):
        with pytest.raises(WritePrimitivesCatalogError, match="non-empty platform names"):
            _parse_validation_platform_overrides("op", "v1", {"platform_overrides": {"": "SELECT 1"}})

    def test_non_string_key_rejected(self):
        with pytest.raises(WritePrimitivesCatalogError, match="non-empty platform names"):
            _parse_validation_platform_overrides("op", "v1", {"platform_overrides": {123: "SELECT 1"}})

    def test_empty_string_value_rejected(self):
        with pytest.raises(WritePrimitivesCatalogError, match="non-empty string or null"):
            _parse_validation_platform_overrides("op", "v1", {"platform_overrides": {"clickhouse": ""}})

    def test_non_string_non_null_value_rejected(self):
        with pytest.raises(WritePrimitivesCatalogError, match="non-empty string or null"):
            _parse_validation_platform_overrides("op", "v1", {"platform_overrides": {"clickhouse": 123}})


class TestResolveValidationSql:
    def _make_vq(self, overrides=None):
        return SimpleNamespace(id="v1", sql="DEFAULT SQL", platform_overrides=overrides or {})

    def test_default_when_platform_key_none(self):
        vq = self._make_vq({"clickhouse": "OVERRIDE"})
        sql, skip = _resolve_validation_sql(vq, None)
        assert sql == "DEFAULT SQL"
        assert skip is None

    def test_default_when_platform_key_not_in_overrides(self):
        vq = self._make_vq({"clickhouse": "OVERRIDE"})
        sql, skip = _resolve_validation_sql(vq, "duckdb")
        assert sql == "DEFAULT SQL"
        assert skip is None

    def test_returns_override_when_match(self):
        vq = self._make_vq({"clickhouse": "OVERRIDE_SQL"})
        sql, skip = _resolve_validation_sql(vq, "clickhouse")
        assert sql == "OVERRIDE_SQL"
        assert skip is None

    def test_returns_skip_when_explicit_null(self):
        vq = self._make_vq({"redshift": None})
        sql, skip = _resolve_validation_sql(vq, "redshift")
        assert sql is None
        assert skip is not None
        assert "redshift" in skip
        assert "v1" in skip


class TestRunOperationValidationOverrides:
    def _make_val_query(self, sql="DEFAULT", overrides=None):
        return SimpleNamespace(
            id="v1",
            sql=sql,
            expected_rows=1,
            expected_rows_min=None,
            expected_rows_max=None,
            expected_values=None,
            check_expression=None,
            platform_overrides=overrides or {},
        )

    def test_uses_default_when_no_platform_key(self, wp: WritePrimitivesBenchmark):
        val_query = self._make_val_query("SELECT DEFAULT", overrides={"clickhouse": "SELECT OVERRIDE"})
        operation = SimpleNamespace(validation_queries=[val_query])
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(1,)]
        passed, results, _ = wp._run_operation_validation(operation, conn, "OP_1")
        assert passed is True

        conn.execute.assert_called_once_with("SELECT DEFAULT")
        assert results[0]["sql"] == "SELECT DEFAULT"
        assert results[0].get("skipped") is None

    def test_uses_override_when_platform_matches(self, wp: WritePrimitivesBenchmark):
        val_query = self._make_val_query("SELECT DEFAULT", overrides={"clickhouse": "SELECT OVERRIDE"})
        operation = SimpleNamespace(validation_queries=[val_query])
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(1,)]
        passed, results, _ = wp._run_operation_validation(operation, conn, "OP_1", platform_key="clickhouse")
        assert passed is True
        conn.execute.assert_called_once_with("SELECT OVERRIDE")
        assert results[0]["sql"] == "SELECT OVERRIDE"

    def test_skips_with_null_override_passing(self, wp: WritePrimitivesBenchmark):
        val_query = self._make_val_query("SELECT DEFAULT", overrides={"redshift": None})
        operation = SimpleNamespace(validation_queries=[val_query])
        conn = MagicMock()
        passed, results, _ = wp._run_operation_validation(operation, conn, "OP_1", platform_key="redshift")
        assert passed is True
        conn.execute.assert_not_called()
        assert results[0]["skipped"] is True
        assert "redshift" in results[0]["skip_reason"]
        assert results[0]["passed"] is True

    def test_validation_fallback_key(self, wp: WritePrimitivesBenchmark):
        skipped = self._make_val_query("SELECT SKIPPED", overrides={"duckdb": None})
        skipped.id = "v_skipped"
        operation = SimpleNamespace(validation_queries=[skipped])
        conn = MagicMock()
        passed, results, _ = wp._run_operation_validation(
            operation, conn, "OP_1", platform_key="ducklake", platform_fallback_key="duckdb"
        )
        assert passed is True
        assert results[0]["skipped"] is True
        conn.execute.assert_not_called()

    def test_mixed_validations_one_skipped_one_run(self, wp: WritePrimitivesBenchmark):
        skipped = self._make_val_query("SELECT SKIPPED", overrides={"redshift": None})
        skipped.id = "v_skipped"
        active = self._make_val_query("SELECT ACTIVE")
        active.id = "v_active"
        operation = SimpleNamespace(validation_queries=[skipped, active])
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(1,)]
        passed, results, _ = wp._run_operation_validation(operation, conn, "OP_1", platform_key="redshift")
        assert passed is True
        assert len(results) == 2
        assert results[0]["query_id"] == "v_skipped"
        assert results[0]["skipped"] is True
        assert results[1]["query_id"] == "v_active"
        assert results[1].get("skipped") is None

        conn.execute.assert_called_once_with("SELECT ACTIVE")


class TestValidationFallbackResolution:
    def _vq(self, overrides):
        return SimpleNamespace(id="v1", sql="SELECT DEFAULT", platform_overrides=overrides)

    def test_fallback_string_override_used(self):
        sql, skip = _resolve_validation_sql(self._vq({"duckdb": "SELECT DIALECT"}), "ducklake", "duckdb")
        assert (sql, skip) == ("SELECT DIALECT", None)

    def test_fallback_null_override_skips(self):
        sql, skip = _resolve_validation_sql(self._vq({"duckdb": None}), "ducklake", "duckdb")
        assert sql is None
        assert skip is not None and "ducklake" in skip

    def test_engine_entry_wins_over_fallback(self):
        sql, skip = _resolve_validation_sql(
            self._vq({"ducklake": "SELECT ENGINE", "duckdb": None}), "ducklake", "duckdb"
        )
        assert (sql, skip) == ("SELECT ENGINE", None)

    def test_no_fallback_keeps_default(self):
        sql, skip = _resolve_validation_sql(self._vq({"duckdb": "SELECT DIALECT"}), "ducklake")
        assert (sql, skip) == ("SELECT DEFAULT", None)


