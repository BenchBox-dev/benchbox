from __future__ import annotations

import pytest

from benchbox.core.tpcds import parameter_extractor as pe

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_extract_common_params_primary_keys() -> None:
    sql = """
    where d_year = 2001
      and d_moy = 11
      and s_state = 'CA'
      and cd_gender = 'F'
      and cd_marital_status = 'M'
      and cd_education_status = 'College'
      and i_manufact_id = 427
    """
    expected = {"year", "month", "state", "gender", "marital_status", "education", "manufact_id"}

    out = pe._extract_common_params(sql, expected)

    assert out == {
        "year": 2001,
        "month": 11,
        "state": "CA",
        "gender": "F",
        "marital_status": "M",
        "education": "College",
        "manufact_id": 427,
    }


def test_extract_common_params_cd_alias_keys_and_missing_values() -> None:
    sql = "where ca_state = 'OR' and cd_gender = 'M' and cd_marital_status = 'S' and cd_education_status = 'Primary'"
    expected = {"state", "cd_gender", "cd_marital_status", "cd_education_status", "month"}

    out = pe._extract_common_params(sql, expected)

    assert out == {
        "state": "OR",
        "cd_gender": "M",
        "cd_marital_status": "S",
        "cd_education_status": "Primary",
    }


def test_extract_common_params_extended_key_families() -> None:
    sql = """
    where d_year = 2002
      and d_moy in (3, 4, 5)
      and d_qoy = 2
      and d_qoy in (1, 2, 3)
      and d_dom = 11
      and t_hour between 8 and 9
      and t_hour between 19 and 20
      and s_state in ('TX', 'OR', 'AZ')
      and ca_city in ('Edgewood', 'Midway')
      and s_county = 'Williamson County'
      and i_category in ('Books', 'Music')
      and i_color in ('slate', 'blanched')
      and i_manager_id = 28
      and i_class_id = 1
      and i_category_id = 5
      and d_month_seq = 1212
      and s_gmt_offset = -5.0
      and ib_lower_bound >= 38128
    """
    expected = {
        "year",
        "months",
        "quarter",
        "quarters",
        "day",
        "hours",
        "states",
        "cities",
        "county",
        "categories",
        "colors",
        "manager_id",
        "class_id",
        "category_id",
        "d_month_seq",
        "gmt_offset",
        "income_band",
    }
    out = pe._extract_common_params(sql, expected)
    assert out == {
        "year": 2002,
        "months": [3, 4, 5],
        "quarter": 2,
        "quarters": [1, 2, 3],
        "day": 11,
        "hours": [(8, 9), (19, 20)],
        "states": ["TX", "OR", "AZ"],
        "cities": ["Edgewood", "Midway"],
        "county": "Williamson County",
        "categories": ["Books", "Music"],
        "colors": ["slate", "blanched"],
        "manager_id": 28,
        "class_id": 1,
        "category_id": 5,
        "d_month_seq": 1212,
        "gmt_offset": -5.0,
        "income_band": 38128,
    }


def test_extract_common_params_filters_to_expected_keys() -> None:
    sql = """
    where d_year = 2002 and d_moy = 11 and s_state = 'CA' and i_manager_id = 42 and i_color = 'red'
    """
    out = pe._extract_common_params(sql, {"month", "manager_id"})
    assert out == {"month": 11, "manager_id": 42}


def test_extract_common_params_malformed_sql_falls_back_to_empty() -> None:
    out = pe._extract_common_params("select from ???", {"year", "hours", "states", "categories"})
    assert out == {}


def test_extract_tpcds_parameters_filters_and_collects(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDSQGen:
        def generate(self, query_id: int, seed: int, scale_factor: float, stream_id: int | None) -> str:
            assert seed == 7
            assert scale_factor == 0.01
            assert stream_id == 3
            if query_id == 1:
                return "where d_year = 2000 and d_moy = 2"
            if query_id == 2:
                return "select 1"
            raise RuntimeError("boom")

    monkeypatch.setattr("benchbox.core.tpcds.c_tools.DSQGenBinary", FakeDSQGen)
    monkeypatch.setattr(
        "benchbox.core.tpcds.dataframe_queries.parameters.TPCDS_DEFAULT_PARAMS",
        {
            1: {"year": 1998, "month": 1},
            2: {"state": "TX"},
            3: {"year": 1997},
            4: {},
        },
        raising=False,
    )

    out = pe.extract_tpcds_parameters(seed=7, scale_factor=0.01, stream_id=3, query_ids=[1, 2, 3, 4, 5])
    assert out == {1: {"year": 2000, "month": 2}}


def test_extract_tpcds_parameters_without_query_ids_uses_default_range(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDSQGen:
        def __init__(self):
            self.calls = 0

        def generate(self, query_id: int, seed: int, scale_factor: float, stream_id: int | None) -> str:
            self.calls += 1
            return "where d_year = 1999"

    fake = FakeDSQGen()
    monkeypatch.setattr("benchbox.core.tpcds.c_tools.DSQGenBinary", lambda: fake)
    monkeypatch.setattr(
        "benchbox.core.tpcds.dataframe_queries.parameters.TPCDS_DEFAULT_PARAMS",
        {1: {"year": 1998}},
        raising=False,
    )

    out = pe.extract_tpcds_parameters(seed=1, scale_factor=1.0, stream_id=None, query_ids=None)
    assert out == {1: {"year": 1999}}
    assert fake.calls == 1


def test_get_tpcds_extracted_parameters_cache_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_extract(seed: int, scale_factor: float, stream_id: int | None):
        calls.append((seed, scale_factor, stream_id))
        return {1: {"seed": seed, "stream": stream_id}}

    monkeypatch.setattr(pe, "extract_tpcds_parameters", fake_extract)
    pe.clear_cache()

    first = pe.get_tpcds_extracted_parameters(11, 0.1, 2, use_cache=True)
    second = pe.get_tpcds_extracted_parameters(11, 0.1, 2, use_cache=True)
    third = pe.get_tpcds_extracted_parameters(11, 0.1, 2, use_cache=False)

    assert first == second == third == {1: {"seed": 11, "stream": 2}}
    assert calls == [(11, 0.1, 2), (11, 0.1, 2)]


def test_clear_cache_empties_store() -> None:
    pe._cache[(1, 1.0, None)] = {1: {"year": 1998}}
    pe.clear_cache()
    assert pe._cache == {}


def test_supported_key_set_grows_and_is_tracked() -> None:
    keys = pe.get_supported_parameter_keys()
    assert {"year", "state", "hours", "categories", "city", "income_band"} <= keys


def test_unsupported_key_set_exposes_fallback_surface() -> None:
    keys = pe.get_unsupported_parameter_keys()
    assert "zip_codes" in keys
    assert "channel_type" in keys


def test_parameter_key_coverage_inventory_is_ranked() -> None:
    coverage = pe.get_parameter_key_coverage()
    assert coverage
    assert {"key", "queries", "supported", "risk"} <= set(coverage[0])
    assert coverage == sorted(coverage, key=lambda item: (-item["queries"], item["key"]))


