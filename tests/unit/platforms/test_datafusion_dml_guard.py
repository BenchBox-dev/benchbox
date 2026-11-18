from __future__ import annotations

import pytest

datafusion = pytest.importorskip("datafusion")
pa = pytest.importorskip("pyarrow")

from benchbox.platforms.datafusion import DataFusionConnectionCompat

LEADING_COMMENT = "-" * 2 + " cleanup"

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture()
def connection() -> DataFusionConnectionCompat:
    context = datafusion.SessionContext()
    context.register_record_batches(
        "facts", [[pa.record_batch([pa.array(range(10)), pa.array([0] * 10)], names=["k", "v"])]]
    )
    context.register_record_batches("doomed", [[pa.record_batch([pa.array([1, 2, 3])], names=["x"])]])
    return DataFusionConnectionCompat(context)


def _count(connection: DataFusionConnectionCompat, where: str = "") -> int:
    return connection.execute(f"SELECT COUNT(*) FROM facts {where}").fetchone()[0]


def test_subquery_in_delete_is_refused_and_the_table_is_unchanged(connection) -> None:
    with pytest.raises(RuntimeError, match=r"apache/datafusion#24657"):
        connection.execute("DELETE FROM facts WHERE k IN (SELECT x FROM doomed)")
    assert _count(connection) == 10


def test_correlated_exists_delete_is_refused_and_the_table_is_unchanged(connection) -> None:
    with pytest.raises(RuntimeError, match=r"apache/datafusion#24657"):
        connection.execute("DELETE FROM facts WHERE EXISTS (SELECT 1 FROM doomed WHERE doomed.x = facts.k)")
    assert _count(connection) == 10


def test_scalar_subquery_delete_is_refused_and_the_table_is_unchanged(connection) -> None:
    with pytest.raises(RuntimeError, match=r"apache/datafusion#24657"):
        connection.execute("DELETE FROM facts WHERE k > (SELECT MAX(x) FROM doomed)")
    assert _count(connection) == 10


def test_subquery_update_is_refused_and_the_table_is_unchanged(connection) -> None:
    with pytest.raises(RuntimeError, match=r"apache/datafusion#24657"):
        connection.execute("UPDATE facts SET v = 1 WHERE k IN (SELECT x FROM doomed)")
    assert _count(connection, "WHERE v = 0") == 10


def test_sql_passthrough_also_refuses_a_subquery_delete(connection) -> None:
    with pytest.raises(RuntimeError, match=r"apache/datafusion#24657"):
        connection.sql("DELETE FROM facts WHERE k IN (SELECT x FROM doomed)")
    assert _count(connection) == 10


def test_literal_key_delete_removes_exactly_the_matching_rows(connection) -> None:
    connection.execute("DELETE FROM facts WHERE k IN (1, 2, 3)")
    assert _count(connection) == 7
    assert _count(connection, "WHERE k IN (1, 2, 3)") == 0


def test_simple_predicate_update_still_runs(connection) -> None:
    connection.execute("UPDATE facts SET v = 5 WHERE k = 4")
    assert _count(connection, "WHERE v = 5") == 1


def test_leading_comment_does_not_bypass_the_guard(connection) -> None:
    with pytest.raises(RuntimeError, match=r"apache/datafusion#24657"):
        connection.execute(LEADING_COMMENT + "\nDELETE FROM facts WHERE k IN (SELECT x FROM doomed)")
    assert _count(connection) == 10


def test_select_with_a_subquery_is_not_blocked(connection) -> None:
    assert connection.execute("SELECT COUNT(*) FROM facts WHERE k IN (SELECT x FROM doomed)").fetchone()[0] == 3
