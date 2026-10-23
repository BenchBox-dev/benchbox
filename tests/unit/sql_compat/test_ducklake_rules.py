from __future__ import annotations

import importlib
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture(scope="module")
def ducklake_decisions():
    importlib.import_module("benchbox.sql_compat.rules.ddl_optimize.ducklake_ddl_rewrites")

    from benchbox.sql_compat.context import CompatibilityContext, Phase
    from benchbox.sql_compat.registry import REGISTRY

    ctx = CompatibilityContext(
        platform="ducklake",
        platform_version=None,
        benchmark="",
        query_id=None,
        phase=Phase.DDL_OPTIMIZE,
        mode="sql",
        dialect=None,
    )
    return REGISTRY.resolve_all(ctx)


class TestDuckLakeRuleStructure:
    def test_exactly_one_rule_registered(self, ducklake_decisions):
        assert [d.rule_id for d in ducklake_decisions] == ["ddl_optimize.ducklake.all.strip_primary_keys"]

    def test_rule_payload_is_rewrite_ddl(self, ducklake_decisions):
        from benchbox.sql_compat.decision import RewriteDDLPayload

        (decision,) = ducklake_decisions
        assert isinstance(decision.payload, RewriteDDLPayload)

    def test_transformer_id_names_translate_ddl_option(self, ducklake_decisions):
        from dataclasses import fields

        from benchbox._vendor.sqlglot.benchbox.ddl import DdlOptions

        (decision,) = ducklake_decisions
        assert decision.payload.governance_only
        prefix, _, option = decision.payload.transformer_id.partition(":")
        assert prefix == "translate_ddl"
        assert option in {field.name for field in fields(DdlOptions)}

    def test_rewrite_strips_primary_keys_through_translate_ddl(self, tmp_path: Path):
        from benchbox.platforms.ducklake import DuckLakeAdapter

        adapter = DuckLakeAdapter(
            metadata_path=str(tmp_path / "catalog.ducklake"),
            data_path=str(tmp_path / "data"),
        )
        statement = "CREATE TABLE t (\n  id INTEGER PRIMARY KEY\n);"
        assert "PRIMARY KEY" not in adapter._rewrite_schema_statement(statement)
