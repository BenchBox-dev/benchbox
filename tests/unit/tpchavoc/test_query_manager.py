from __future__ import annotations

import pytest

from benchbox.core.tpchavoc.queries import TPCHavocQueryManager

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


@pytest.fixture(scope="module")
def manager() -> TPCHavocQueryManager:
    return TPCHavocQueryManager()


def test_manager_reports_all_queries(manager: TPCHavocQueryManager):
    implemented = manager.get_implemented_queries()
    assert set(implemented) == set(range(1, 23))


def test_get_query_variant_returns_sql(manager: TPCHavocQueryManager):
    sql = manager.get_query_variant(1, 2)
    assert "select" in sql.lower()
    assert "lineitem" in sql.lower()


def test_get_all_variants_info(manager: TPCHavocQueryManager):
    info = manager.get_all_variants_info(1)
    assert len(info) == 10
    assert all(entry["description"] for entry in info.values())


def test_invalid_variant_errors(manager: TPCHavocQueryManager):
    with pytest.raises(ValueError):
        manager.get_query_variant(1, 99)

    with pytest.raises(ValueError):
        manager.get_query_variant(99, 1)


def test_q11_fraction_scales_with_scale_factor(manager: TPCHavocQueryManager):
    for scale_factor, literal in [(1.0, "0.0001000000"), (0.1, "0.0010000000"), (10.0, "0.0000100000")]:
        for variant_id in range(1, 11):
            sql = manager.get_query_variant(11, variant_id, scale_factor=scale_factor)
            assert literal in sql, f"11_v{variant_id} at SF={scale_factor} missing threshold {literal}"


def test_explicit_params_do_not_suppress_scale_substitution(manager: TPCHavocQueryManager):
    sql = manager.get_query_variant(11, 1, params={"unrelated": "value"}, scale_factor=0.1)
    assert "{q11_fraction}" not in sql
    assert "0.0010000000" in sql


def test_no_unsubstituted_tokens_in_any_variant(manager: TPCHavocQueryManager):
    for query_id in manager.get_implemented_queries():
        for variant_id in range(1, 11):
            for sql in (
                manager.get_query_variant(query_id, variant_id, scale_factor=0.1),
                manager.get_parameterized_query_variant(query_id, variant_id, scale_factor=0.1),
            ):
                assert "{" not in sql and "}" not in sql, f"unsubstituted token in {query_id}_v{variant_id}"


def test_parameterized_variant_renders_scale_threshold(manager: TPCHavocQueryManager):
    sql = manager.get_parameterized_query_variant(11, 1, scale_factor=0.1)
    assert "{q11_fraction}" not in sql
    assert "0.0010000000" in sql
