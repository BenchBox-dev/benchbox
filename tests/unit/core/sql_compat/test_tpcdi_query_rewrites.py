from __future__ import annotations

import random
from decimal import ROUND_HALF_UP, Decimal

import duckdb
import pytest

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import CompatibilityContext, Phase
from benchbox.sql_compat.decision import RewriteQueryPayload
from benchbox.sql_compat.registry import REGISTRY
from benchbox.sql_compat.rules.query_adapter.tpcdi_query_rewrites import (
    EXACT_DECIMAL_QUOTIENT_PLATFORMS,
    EXACT_DECIMAL_QUOTIENTS,
)
from benchbox.utils.dialect_utils import apply_rewrite

pytestmark = [pytest.mark.unit, pytest.mark.fast]

CENT = Decimal("0.01")
QUOTIENT_SQL = "SELECT CAST(x / y AS DECIMAL(10, 2)) FROM t"


def _context(platform: str) -> CompatibilityContext:
    return CompatibilityContext(
        platform=platform,
        platform_version=None,
        benchmark="tpcdi",
        query_id="1",
        phase=Phase.QUERY_ADAPTER,
        mode="sql",
        dialect=platform,
    )


def _quotients(sql: str, pairs: list[tuple[Decimal | None, Decimal | None]]) -> list[Decimal | None]:
    with duckdb.connect(":memory:") as connection:
        connection.execute("CREATE TABLE t (i INTEGER, x DECIMAL(10, 2), y DECIMAL(10, 2))")
        connection.executemany("INSERT INTO t VALUES (?, ?, ?)", [(i, x, y) for i, (x, y) in enumerate(pairs)])
        ordered = sql.replace("SELECT ", "SELECT i, ", 1) + " ORDER BY i"
        return [row[1] for row in connection.execute(ordered).fetchall()]


def _expected(x: Decimal | None, y: Decimal | None) -> Decimal | None:
    if x is None or y is None or y == 0:
        return None
    quotient = x / y
    return abs(quotient).quantize(CENT, rounding=ROUND_HALF_UP).copy_sign(quotient)


def test_exact_decimal_quotients_rule_is_registered_for_duckdb():
    assert EXACT_DECIMAL_QUOTIENT_PLATFORMS == ("duckdb",)
    decision = REGISTRY.resolve(_context("duckdb"))
    assert decision is not None
    assert decision.rule_id == "query_adapter.duckdb.tpcdi.exact_decimal_quotients"
    assert decision.action is CompatAction.REWRITE_QUERY
    assert isinstance(decision.payload, RewriteQueryPayload)
    assert decision.payload.transformer_id == EXACT_DECIMAL_QUOTIENTS


def test_exact_decimal_quotients_rule_is_not_registered_elsewhere():
    assert REGISTRY.resolve(_context("sqlite")) is None


def test_unrewritten_cast_rounds_the_tie_the_wrong_way():
    result = _quotients(QUOTIENT_SQL, [(Decimal("1.71"), Decimal("6.00"))])
    assert result == [Decimal("0.28")]


def test_exact_decimal_quotients_rounds_ties_half_away_from_zero():
    pairs = [
        (Decimal("1.71"), Decimal("6.00")),
        (Decimal("0.57"), Decimal("2.00")),
        (Decimal("-1.71"), Decimal("6.00")),
        (Decimal("1.71"), Decimal("-6.00")),
        (Decimal("5.00"), Decimal("0.00")),
        (Decimal("0.00"), Decimal("0.00")),
        (None, Decimal("2.00")),
    ]
    rewritten = apply_rewrite(EXACT_DECIMAL_QUOTIENTS, QUOTIENT_SQL, "duckdb")
    assert _quotients(rewritten, pairs) == [
        Decimal("0.29"),
        Decimal("0.29"),
        Decimal("-0.29"),
        Decimal("-0.29"),
        None,
        None,
        None,
    ]


def test_exact_decimal_quotients_matches_decimal_half_up_on_random_pairs():
    generator = random.Random(20260326)
    pairs = []
    for _ in range(10_000):
        x = Decimal(generator.randint(-99_999_999, 99_999_999)).scaleb(-2)
        y = Decimal(generator.randint(-9_999_999_999, 9_999_999_999)).scaleb(-2)
        if generator.random() < 0.3:
            x = Decimal(generator.randint(-99_999, 99_999)).scaleb(-2)
            y = Decimal(generator.randint(-999, 999)).scaleb(-2)
        pairs.append((x.quantize(CENT), y.quantize(CENT)))
    rewritten = apply_rewrite(EXACT_DECIMAL_QUOTIENTS, QUOTIENT_SQL, "duckdb")
    actual = _quotients(rewritten, pairs)
    mismatches = [(pair, got) for pair, got in zip(pairs, actual) if got != _expected(*pair)]
    assert mismatches == []
