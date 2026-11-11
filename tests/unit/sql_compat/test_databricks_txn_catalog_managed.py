from __future__ import annotations

import importlib

import pytest

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import CompatibilityContext, Phase
from benchbox.sql_compat.decision import RewriteDDLPayload
from benchbox.sql_compat.registry import REGISTRY

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_RULE_ID = "ddl_optimize.databricks.transaction_primitives.txn_staging_catalog_managed"


@pytest.fixture(scope="module", autouse=True)
def _load_databricks_rules():
    importlib.import_module("benchbox.sql_compat.rules.ddl_optimize.databricks_ddl_rewrites")


def _ctx(benchmark: str) -> CompatibilityContext:
    return CompatibilityContext(
        platform="databricks",
        platform_version=None,
        benchmark=benchmark,
        query_id=None,
        phase=Phase.DDL_OPTIMIZE,
        mode="sql",
        dialect="databricks",
    )


def test_txn_ctx_resolves_to_catalog_managed_rule():
    decision = REGISTRY.resolve(_ctx("transaction_primitives"))

    assert decision is not None
    assert decision.rule_id == _RULE_ID
    assert decision.action == CompatAction.REWRITE_DDL
    assert isinstance(decision.payload, RewriteDDLPayload)
    assert decision.payload.governance_only is True


def test_platform_wide_tier_still_has_single_winner():
    decision = REGISTRY.resolve(_ctx("write_primitives"))

    assert decision is not None
    assert decision.rule_id == "ddl_optimize.databricks.all.convert_to_delta_table"


def test_rule_carries_non_empty_description_and_reason():
    decisions = REGISTRY.resolve_all(_ctx("transaction_primitives"))
    decision = next(d for d in decisions if d.rule_id == _RULE_ID)

    assert decision.payload.description
    assert decision.reason


