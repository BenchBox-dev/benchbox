from __future__ import annotations

from datetime import date

import pytest

from benchbox.core.tpch import parameter_extractor as pe

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.parametrize(
    ("query_id", "sql", "expected"),
    [
        (1, "... interval '68' day ...", {"delta": 68, "cutoff_date": date(1998, 9, 24)}),
        (2, "where p_size = 38 and p_type like '%STEEL' and r_name = 'ASIA'", {"size": 38}),
        (
            6,
            "l_shipdate >= date '1993-01-01' and l_discount between 0.07 - 0.01 and 0.07 + 0.01 and l_quantity < 25",
            {"discount": 0.07, "quantity_limit": 25, "start_date": date(1993, 1, 1), "end_date": date(1994, 1, 1)},
        ),
        (
            16,
            "p_brand <> 'Brand#41' and p_type not like 'MEDIUM BURNISHED%' and p_size in (4, 22, 31)",
            {"brand": "Brand#41", "type_prefix": "MEDIUM BURNISHED", "sizes": [4, 22, 31]},
        ),
        (22, "substring(c_phone from 1 for 2) in ('24', '33', '31')", {"country_codes": ["24", "33", "31"]}),
    ],
)
def test_extractors_parse_expected_values(query_id: int, sql: str, expected: dict[str, object]) -> None:
    params = pe._extract_query_params(query_id, sql)

    assert params is not None
    for key, value in expected.items():
        assert params[key] == value


def test_extract_query_params_unknown_query_raises() -> None:
    with pytest.raises(pe.ParameterExtractionError):
        pe._extract_query_params(999, "select 1")


@pytest.mark.parametrize(("query_id", "sql"), [(7, "no nation filters"), (18, "no threshold"), (20, "no match")])
def test_extractors_raise_when_patterns_missing(query_id: int, sql: str) -> None:
    with pytest.raises(pe.ParameterExtractionError, match=f"Q{query_id}"):
        pe._extract_query_params(query_id, sql)


def test_extractor_rejects_dialect_translated_sql() -> None:
    translated = "WHERE c_mktsegment = 'HOUSEHOLD' AND o_orderdate < CAST('1995-03-03' AS DATE)"
    with pytest.raises(pe.ParameterExtractionError):
        pe._extract_query_params(3, translated)


def test_q6_discount_bounds_are_exact_decimals() -> None:
    params = pe._extract_query_params(
        6, "l_shipdate >= date '1994-01-01' and l_discount between .06 - 0.01 and .06 + 0.01 and l_quantity < 24"
    )
    assert params["discount_low"] == 0.05
    assert params["discount_high"] == 0.07


def test_date_helpers() -> None:
    assert pe._parse_date("1995-03-17") == date(1995, 3, 17)
    assert pe._add_months(date(1995, 11, 1), 3) == date(1996, 2, 1)
    assert pe._add_years(date(1995, 3, 17), 1) == date(1996, 3, 17)


def test_extract_tpch_parameters_raises_on_any_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeQGen:
        def generate(self, query_id: int, seed: int, scale_factor: float) -> str:
            assert seed == 11
            assert scale_factor == 0.01
            if query_id == 1:
                return "interval '7' day"
            return "unparseable"

    import benchbox.core.tpch.queries as queries

    monkeypatch.setattr(queries, "QGenBinary", FakeQGen)

    with pytest.raises(pe.ParameterExtractionError, match="Q2"):
        pe.extract_tpch_parameters(seed=11, scale_factor=0.01)


def test_get_tpch_extracted_parameters_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, float]] = []

    def fake_extract(seed: int, scale_factor: float) -> dict[int, dict[str, object]]:
        calls.append((seed, scale_factor))
        return {1: {"seed": seed, "sf": scale_factor}}

    monkeypatch.setattr(pe, "extract_tpch_parameters", fake_extract)
    pe.clear_cache()

    first = pe.get_tpch_extracted_parameters(123, 0.1, use_cache=True)
    second = pe.get_tpch_extracted_parameters(123, 0.1, use_cache=True)
    third = pe.get_tpch_extracted_parameters(123, 0.1, use_cache=False)

    assert first == second == third
    assert calls == [(123, 0.1), (123, 0.1)]


