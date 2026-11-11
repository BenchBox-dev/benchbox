import pytest

from benchbox.validation.bundle import ValidationResult, _validate_required_tables

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _errors(data):
    vr = ValidationResult(path="bundle.json")
    _validate_required_tables(data, vr)
    return vr.errors


def test_obt_bundle_with_the_obt_table_passes():
    data = {"benchmark": {"id": "tpcds_obt"}, "tables": {"TPCDS_SALES_RETURNS_OBT": {"rows": 2880404}}}
    assert _errors(data) == []


def test_obt_bundle_that_loaded_tpcds_source_tables_fails():
    data = {"benchmark": {"id": "tpcds_obt"}, "tables": {"CALL_CENTER": {"rows": 6}, "STORE_SALES": {"rows": 10}}}
    errors = _errors(data)
    assert len(errors) == 1 and "tpcds_sales_returns_obt" in errors[0] and "not in 'tables'" in errors[0]


def test_obt_bundle_with_empty_obt_table_fails():
    data = {"benchmark": {"id": "tpcds_obt"}, "tables": {"tpcds_sales_returns_obt": {"rows": 0}}}
    assert "loaded with 0 rows" in _errors(data)[0]


def test_other_benchmarks_are_not_checked():
    assert _errors({"benchmark": {"id": "tpcds"}, "tables": {"CALL_CENTER": {"rows": 6}}}) == []
    assert _errors({"benchmark": {"id": "tpcds"}, "queries": [{"id": "1"}]}) == []


def test_obt_bundle_with_measured_queries_but_no_tables_block_fails():
    errors = _errors({"benchmark": {"id": "tpcds_obt"}, "queries": [{"id": "2", "status": "SUCCESS"}]})
    assert len(errors) == 1 and "no 'tables' block" in errors[0]


def test_obt_bundle_without_queries_or_tables_is_not_checked():
    assert _errors({"benchmark": {"id": "tpcds_obt"}}) == []


