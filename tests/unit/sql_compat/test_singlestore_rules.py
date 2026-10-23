from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture(autouse=True)
def _mock_singlestoredb(monkeypatch):
    fake_module = MagicMock(name="singlestoredb")
    monkeypatch.setitem(sys.modules, "singlestoredb", fake_module)
    monkeypatch.setattr("benchbox.platforms.singlestore._s2", fake_module, raising=False)
    yield


_EXPECTED_RULES = [
    {
        "rule_id": "ddl_optimize.singlestore.all.strip_foreign_keys",
        "transformer_id": "translate_ddl:strip_foreign_keys",
    },
    {
        "rule_id": "ddl_optimize.singlestore.all.reference_table_for_dimensions",
        "transformer_id": "translate_ddl:singlestore.reference_tables",
    },
    {
        "rule_id": "ddl_optimize.singlestore.all.inject_shard_key",
        "transformer_id": "translate_ddl:singlestore.shard_keys",
    },
    {
        "rule_id": "ddl_optimize.singlestore.all.inject_sort_key",
        "transformer_id": "translate_ddl:singlestore.sort_keys",
    },
]


@pytest.fixture(scope="module")
def singlestore_decisions():
    importlib.import_module("benchbox.sql_compat.rules.ddl_optimize.singlestore_ddl_rewrites")

    from benchbox.sql_compat.context import CompatibilityContext, Phase
    from benchbox.sql_compat.registry import REGISTRY

    ctx = CompatibilityContext(
        platform="singlestore",
        platform_version=None,
        benchmark="",
        query_id=None,
        phase=Phase.DDL_OPTIMIZE,
        mode="sql",
        dialect=None,
    )
    return REGISTRY.resolve_all(ctx)


class TestSingleStoreRuleStructure:
    def test_exactly_four_rules_registered(self, singlestore_decisions):
        assert len(singlestore_decisions) == 4

    def test_rules_registered_in_correct_order(self, singlestore_decisions):
        actual_ids = [d.rule_id for d in singlestore_decisions]
        expected_ids = [r["rule_id"] for r in _EXPECTED_RULES]
        assert actual_ids == expected_ids

    @pytest.mark.parametrize("expected", _EXPECTED_RULES, ids=[r["rule_id"] for r in _EXPECTED_RULES])
    def test_rule_payload_is_rewrite_ddl(self, expected, singlestore_decisions):
        from benchbox.sql_compat.decision import RewriteDDLPayload

        decision = next(d for d in singlestore_decisions if d.rule_id == expected["rule_id"])
        assert isinstance(decision.payload, RewriteDDLPayload)

    @pytest.mark.parametrize("expected", _EXPECTED_RULES, ids=[r["rule_id"] for r in _EXPECTED_RULES])
    def test_transformer_id_matches_expected(self, expected, singlestore_decisions):
        decision = next(d for d in singlestore_decisions if d.rule_id == expected["rule_id"])
        assert decision.payload.transformer_id == expected["transformer_id"]

    @pytest.mark.parametrize("expected", _EXPECTED_RULES, ids=[r["rule_id"] for r in _EXPECTED_RULES])
    def test_rule_has_non_empty_description(self, expected, singlestore_decisions):
        decision = next(d for d in singlestore_decisions if d.rule_id == expected["rule_id"])
        assert decision.payload.description

    @pytest.mark.parametrize("expected", _EXPECTED_RULES, ids=[r["rule_id"] for r in _EXPECTED_RULES])
    def test_rule_has_non_empty_reason(self, expected, singlestore_decisions):
        decision = next(d for d in singlestore_decisions if d.rule_id == expected["rule_id"])
        assert decision.reason

    def test_rule_id_format_matches_convention(self, singlestore_decisions):
        for decision in singlestore_decisions:
            parts = decision.rule_id.split(".")
            assert len(parts) == 4, f"rule_id {decision.rule_id!r} should have 4 dot-separated parts"
            assert parts[0] == "ddl_optimize"
            assert parts[1] == "singlestore"


class TestSingleStoreResolveConflictGuard:
    def test_resolve_raises_conflict_for_singlestore(self, singlestore_decisions):
        from benchbox.sql_compat.context import CompatibilityContext, Phase
        from benchbox.sql_compat.registry import REGISTRY, CompatibilityRegistryConflict

        ctx = CompatibilityContext(
            platform="singlestore",
            platform_version=None,
            benchmark="",
            query_id=None,
            phase=Phase.DDL_OPTIMIZE,
            mode="sql",
            dialect=None,
        )
        with pytest.raises(CompatibilityRegistryConflict):
            REGISTRY.resolve(ctx)
