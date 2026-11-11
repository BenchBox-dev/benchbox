from __future__ import annotations

import importlib

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _make_ctx(dialect: str, benchmark: str):
    from benchbox.sql_compat.context import CompatibilityContext, Phase

    return CompatibilityContext(
        platform=dialect.lower(),
        platform_version=None,
        benchmark=benchmark,
        query_id=None,
        phase=Phase.SCHEMA_EMIT,
        mode="sql",
        dialect=dialect,
    )


@pytest.fixture(autouse=True)
def _load_pk_rules():
    importlib.import_module("benchbox.sql_compat.rules.schema_emit.pk_capability")
    importlib.import_module("benchbox.sql_compat.rules.schema_emit.pk_capability_txn")


_INFORMATIONAL_DIALECTS = [
    "snowflake",
    "redshift",
    "bigquery",
    "databricks",
    "tsql",
    "spark",
    "trino",
    "presto",
    "starrocks",
    "doris",
]
_SKIPPED_DDL_FRAGMENT_DIALECTS = [
    "datafusion",
]
_REWRITTEN_DIALECTS = [
    "clickhouse",
]

_COVERED_DIALECTS = _INFORMATIONAL_DIALECTS + _SKIPPED_DDL_FRAGMENT_DIALECTS + _REWRITTEN_DIALECTS


@pytest.mark.parametrize("dialect", _COVERED_DIALECTS)
def test_write_primitives_pk_rule_registered(dialect: str):
    from benchbox.sql_compat.registry import REGISTRY

    ctx = _make_ctx(dialect, "write_primitives")
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, (
        f"No PK rule registered for dialect '{dialect}' in write_primitives. "
        "Add a rule to benchbox/sql_compat/rules/schema_emit/pk_capability.py."
    )


@pytest.mark.parametrize("dialect", _COVERED_DIALECTS)
def test_transaction_primitives_pk_rule_registered(dialect: str):
    from benchbox.sql_compat.registry import REGISTRY

    ctx = _make_ctx(dialect, "transaction_primitives")
    decision = REGISTRY.resolve(ctx)
    assert decision is not None, (
        f"No PK rule registered for dialect '{dialect}' in transaction_primitives. "
        "Add a rule to benchbox/sql_compat/rules/schema_emit/pk_capability_txn.py."
    )


_MIXIN_USER_DIALECT: dict[str, str | None] = {
    "DataFusionAdapter": "datafusion",
    "PolarsAdapter": None,
}


def _import_mixin_user_modules() -> None:
    importlib.import_module("benchbox.platforms.datafusion")
    importlib.import_module("benchbox.platforms.polars_platform")


def test_no_constraint_mixin_parity():
    from benchbox.platforms.base.no_constraint_mixin import NoConstraintEnforcementMixin
    from benchbox.sql_compat.actions import CompatAction
    from benchbox.sql_compat.registry import REGISTRY

    _import_mixin_user_modules()

    discovered = {cls.__name__ for cls in NoConstraintEnforcementMixin.__subclasses__()}
    unknown = discovered - _MIXIN_USER_DIALECT.keys()
    assert not unknown, (
        f"New NoConstraintEnforcementMixin user(s) without a parity entry: {sorted(unknown)}. "
        "Add them to _MIXIN_USER_DIALECT in this test (dialect string for SQL-mode "
        "adapters, or None for DataFrame-only adapters)."
    )

    for cls_name, dialect in _MIXIN_USER_DIALECT.items():
        if dialect is None:
            continue
        for benchmark in ("write_primitives", "transaction_primitives"):
            ctx = _make_ctx(dialect, benchmark)
            decision = REGISTRY.resolve(ctx)
            assert decision is not None, (
                f"{cls_name} (dialect={dialect!r}) has no registered PK rule for {benchmark}. This is a parity bug."
            )
            assert decision.action != CompatAction.NATIVE, (
                f"{cls_name} (dialect={dialect!r}) resolves to NATIVE for {benchmark}, but the "
                "mixin no-ops constraint enforcement at runtime. The rule must be "
                "INFORMATIONAL or SKIPPED_DDL_FRAGMENT."
            )


@pytest.mark.parametrize("dialect", _INFORMATIONAL_DIALECTS)
def test_informational_dialects_bypass_pk_lock(dialect: str):
    from benchbox.sql_compat.actions import CompatAction
    from benchbox.sql_compat.registry import REGISTRY

    ctx = _make_ctx(dialect, "write_primitives")
    decision = REGISTRY.resolve(ctx)
    assert decision is not None
    assert decision.action != CompatAction.NATIVE, (
        f"Dialect '{dialect}' resolves to NATIVE but is classified INFORMATIONAL in the audit. "
        "The lock bypass will not fire, leaving a potential silent-corruption window."
    )


