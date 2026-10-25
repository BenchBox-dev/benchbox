from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from benchbox.core.tpcds.dataframe_queries.parameter_adapters import (
    ADAPTERS,
    adapter_query_ids,
    bind_parameters,
)

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]

UNADAPTED = 1


class _StubDSQGen:
    def __init__(self, substitutions, binary_path):
        self.substitutions = substitutions
        self.dsqgen_path = binary_path
        self.calls = []

    def generate_parameter_log(self, query_id, **kwargs):
        self.calls.append((query_id, kwargs))
        return SimpleNamespace(substitutions=self.substitutions)


class TestAdapters:
    def test_q39_reproduces_the_templates_month_arithmetic(self):

        assert ADAPTERS[39]({"YEAR.01": "1998", "MONTH.01": "4"}) == {"year": 1998, "months": [4, 5]}

    def test_q44_takes_the_store_and_the_null_column(self):
        assert ADAPTERS[44]({"STORE.01": "1", "NULLCOLSS.01": "ss_hdemo_sk"}) == {
            "store_sk": 1,
            "null_col": "ss_hdemo_sk",
        }

    def test_q49_supplies_the_month_the_defaults_file_lacks(self):
        assert ADAPTERS[49]({"YEAR.01": "2000", "MONTH.01": "12"}) == {"year": 2000, "month": 12}

    def test_q93_takes_the_reason_text(self):
        assert ADAPTERS[93]({"REASON.01": "Did not like the warranty"}) == {"reason": "Did not like the warranty"}

    def test_q37_reproduces_the_templates_price_range(self):
        logged = {
            "PRICE.01": "22",
            "INVDATE.01": "2001-06-02",
            **{f"MANUFACT_ID.0{n}": str(n * 100) for n in (1, 2, 3, 4)},
        }
        assert ADAPTERS[37](logged) == {
            "current_price_min": 22,
            "current_price_max": 52,
            "manufact_ids": [100, 200, 300, 400],
            "sales_date": "2001-06-02",
        }

    def test_q34_collects_the_eight_counties_in_template_order(self):
        logged = {f"COUNTY_{letter}.01": f"County {letter}" for letter in "ABCDEFGH"}
        logged.update({"YEAR.01": "1999", "BPONE.01": ">10000", "BPTWO.01": "Unknown"})
        parameters = ADAPTERS[34](logged)
        assert parameters["counties"] == [f"County {letter}" for letter in "ABCDEFGH"]
        assert parameters["year"] == 1999

    def test_q41_binds_every_drawn_attribute(self):
        logged = {"MANUFACT.01": "742"}
        logged.update({f"COLOR.{n:02d}": f"color{n}" for n in range(1, 17)})
        logged.update({f"UNIT.{n:02d}": f"unit{n}" for n in range(1, 17)})
        logged.update({f"SIZE.{n:02d}": f"size{n}" for n in range(1, 7)})
        assert ADAPTERS[41](logged) == {
            "manufact": 742,
            "colors": [f"color{n}" for n in range(1, 17)],
            "units": [f"unit{n}" for n in range(1, 17)],
            "sizes": [f"size{n}" for n in range(1, 7)],
        }

    def test_q24_takes_both_statements_colors(self):
        logged = {"COLOR.01": "orchid", "COLOR.02": "chiffon", "AMOUNTONE.01": "ss_sales_price", "MARKET.01": "7"}
        assert ADAPTERS[24](logged) == {
            "market_id": 7,
            "amount_column": "ss_sales_price",
            "color": "orchid",
            "second_color": "chiffon",
        }

    def test_q74_keeps_the_drawn_column_order(self):
        logged = {"ORDERC.01": "2", "ORDERC.02": "1", "ORDERC.03": "3", "AGGONE.01": "max", "YEAR.01": "2001"}
        assert ADAPTERS[74](logged) == {"year": 2001, "aggone": "max", "order_by": [2, 1, 3]}

    def test_q47_and_q57_pass_the_drawn_select_list_and_tie_break_through(self):
        logged = {
            "YEAR.01": "2001",
            "SELECTONE.01": "v1.s_store_name, v1.s_company_name",
            "SELECTTWO.01": ",v1.d_year",
            "ORDERBY.01": "psum",
        }
        expected = {
            "year": 2001,
            "select_one": "v1.s_store_name, v1.s_company_name",
            "select_two": ",v1.d_year",
            "order_by": "psum",
        }
        assert ADAPTERS[47](logged) == expected
        assert ADAPTERS[57](logged) == expected

    def test_q48_groups_three_demographics_and_three_state_triples(self):
        logged = {
            "YEAR.01": "2000",
            **{f"MS.0{i}": m for i, m in enumerate("MDU", 1)},
            **{f"ES.0{i}": e for i, e in enumerate(("A", "B", "C"), 1)},
            **{f"STATE.0{i}": f"S{i}" for i in range(1, 10)},
        }
        parameters = ADAPTERS[48](logged)
        assert parameters["demo2_marital"] == "D"
        assert parameters["demo3_education"] == "C"
        assert parameters["states1"] == ["S1", "S2", "S3"]
        assert parameters["states3"] == ["S7", "S8", "S9"]

    def test_q55_takes_the_drawn_manager(self):
        logged = {"YEAR.01": "2001", "MONTH.01": "12", "MANAGER.01": "36"}
        assert ADAPTERS[55](logged) == {"year": 2001, "month": 12, "manager_id": 36}

    def test_q64_binds_the_price_floor_and_all_six_colors(self):
        logged = {"YEAR.01": "2000", "PRICE.01": "35", **{f"COLOR.0{i}": f"c{i}" for i in range(1, 7)}}
        assert ADAPTERS[64](logged) == {
            "year": 2000,
            "price_min": 35,
            "colors": ["c1", "c2", "c3", "c4", "c5", "c6"],
        }

    def test_adapted_queries_are_the_known_ones(self):
        assert adapter_query_ids() == tuple(range(1, 100))

    def test_q1_lowers_the_drawn_return_column(self):
        logged = {"AGG_FIELD.01": "SR_FEE", "YEAR.01": "2000", "STATE.01": "TN", "COUNTY.01": "1"}
        assert ADAPTERS[1](logged) == {"year": 2000, "state": "TN", "agg_field": "sr_fee"}

    def test_q3_takes_the_drawn_summed_column(self):
        logged = {"MANUFACT.01": "436", "MONTH.01": "12", "AGGC.01": "ss_ext_sales_price"}
        assert ADAPTERS[3](logged) == {"manufact_id": 436, "month": 12, "agg_column": "ss_ext_sales_price"}

    @pytest.mark.parametrize("query_id", [4, 11])
    def test_q4_and_q11_take_the_year_and_the_drawn_customer_column(self, query_id):
        logged = {"YEAR.01": "2001", "SELECTONE.01": "t_s_secyear.customer_email_address"}
        assert ADAPTERS[query_id](logged) == {"year": 2001, "select_one": "t_s_secyear.customer_email_address"}

    def test_q19_takes_the_drawn_manager_month_and_year(self):
        logged = {"YEAR.01": "2001", "MONTH.01": "12", "MGR_IDX.01": "3", "MANAGER.01": "94"}
        assert ADAPTERS[19](logged) == {"year": 2001, "month": 12, "manager_id": 94}

    def test_q7_takes_the_demographic_values(self):
        logged = {"YEAR.01": "1998", "ES.01": "Primary", "MS.01": "W", "GEN.01": "F"}
        assert ADAPTERS[7](logged) == {"year": 1998, "education": "Primary", "marital_status": "W", "gender": "F"}

    def test_q8_takes_every_drawn_zip_code_in_order(self):
        logged = {
            "YEAR.01": "1998",
            "QOY.01": "2",
            **{f"ZIP.{index:02d}": str(10000 + index) for index in (1, 2, 10, 100)},
        }
        assert ADAPTERS[8](logged) == {
            "year": 1998,
            "qoy": 2,
            "zip_codes": ["10001", "10002", "10010", "10100"],
        }

    def test_q10_takes_the_first_five_of_the_ten_drawn_counties(self):
        logged = {
            "YEAR.01": "2002",
            "MONTH.01": "4",
            **{f"COUNTY.{index:02d}": f"County {index}" for index in range(1, 11)},
        }
        assert ADAPTERS[10](logged) == {
            "year": 2002,
            "month": 4,
            "counties": [f"County {index}" for index in range(1, 6)],
        }

    def test_q12_takes_the_categories_and_the_start_date(self):
        logged = {
            "CATEGORY.01": "Jewelry",
            "CATEGORY.02": "Sports",
            "CATEGORY.03": "Books",
            "SDATE.01": "2001-01-12",
            "YEAR.01": "2001",
        }
        assert ADAPTERS[12](logged) == {"item_categories": ["Jewelry", "Sports", "Books"], "sales_date": "2001-01-12"}

    def test_q13_groups_the_three_demographics_and_nine_states(self):
        logged = {f"MS.{index:02d}": "DSW"[index - 1] for index in (1, 2, 3)}
        logged |= {f"ES.{index:02d}": f"education {index}" for index in (1, 2, 3)}
        logged |= {f"STATE.{index:02d}": f"S{index}" for index in range(1, 10)}
        assert ADAPTERS[13](logged) == {
            "year": 2001,
            "demo1_marital": "D",
            "demo1_education": "education 1",
            "states1": ["S1", "S2", "S3"],
            "demo2_marital": "S",
            "demo2_education": "education 2",
            "states2": ["S4", "S5", "S6"],
            "demo3_marital": "W",
            "demo3_education": "education 3",
            "states3": ["S7", "S8", "S9"],
        }

    def test_q14_binds_both_statements_and_q17_binds_the_fixed_quarter(self):
        assert ADAPTERS[14]({"YEAR.01": "1999", "DAY.01": "16"}) == {"year": 1999, "day": 16}
        assert ADAPTERS[17]({"YEAR.01": "2001"}) == {"year": 2001, "quarter": 1}


class TestBinding:
    def test_binding_records_where_the_values_came_from(self, tmp_path):
        binary = tmp_path / "dsqgen"
        binary.write_bytes(b"binary bytes")
        dsqgen = _StubDSQGen({"STORE.01": "3", "NULLCOLSS.01": "ss_addr_sk"}, binary)

        binding = bind_parameters(44, scale_factor=0.1, seed=11, stream_id=2, dsqgen=dsqgen)

        assert binding.parameters == {"store_sk": 3, "null_col": "ss_addr_sk"}
        assert (binding.query_id, binding.scale_factor, binding.seed, binding.stream_id) == (44, 0.1, 11, 2)
        assert dict(binding.logged) == {"STORE.01": "3", "NULLCOLSS.01": "ss_addr_sk"}
        assert len(binding.dsqgen_sha256) == 64
        assert dsqgen.calls == [(44, {"seed": 11, "scale_factor": 0.1, "stream_id": 2})]

    def test_a_query_without_an_adapter_does_not_fall_back_to_the_defaults(self, tmp_path, monkeypatch):
        monkeypatch.delitem(ADAPTERS, UNADAPTED)
        assert UNADAPTED not in ADAPTERS
        with pytest.raises(KeyError, match=f"Q{UNADAPTED} has no parameter adapter"):
            bind_parameters(UNADAPTED, scale_factor=1.0, dsqgen=_StubDSQGen({}, tmp_path / "dsqgen"))

    def test_a_value_dsqgen_did_not_log_is_reported(self, tmp_path):
        binary = tmp_path / "dsqgen"
        binary.write_bytes(b"x")

        with pytest.raises(ValueError, match="did not log 'MONTH.01' for Q39"):
            bind_parameters(39, scale_factor=1.0, dsqgen=_StubDSQGen({"YEAR.01": "2000"}, binary))


@pytest.fixture(scope="module")
def dsqgen():
    from benchbox.core.tpcds.c_tools import DSQGenBinary, TPCDSError

    try:
        return DSQGenBinary()
    except (TPCDSError, FileNotFoundError, RuntimeError) as exc:
        pytest.skip(f"dsqgen binary or templates unavailable: {exc}")


@pytest.mark.parametrize("scale_factor", [0.01, 1.0])
def test_adapted_values_are_the_values_in_the_sql_for_the_same_seed_and_scale(dsqgen, scale_factor):

    def squashed(query_id):
        return "".join(dsqgen.generate(query_id, seed=7, scale_factor=scale_factor).split())

    def bound(query_id):
        return bind_parameters(query_id, scale_factor=scale_factor, seed=7, dsqgen=dsqgen).parameters

    q39, q44, q49, q93 = bound(39), bound(44), bound(49), bound(93)
    assert f"d_year={q39['year']}" in squashed(39)
    assert f"inv1.d_moy={q39['months'][0]}" in squashed(39)
    assert f"inv2.d_moy={q39['months'][0]}+1" in squashed(39)
    assert f"ss_store_sk={q44['store_sk']}" in squashed(44)
    assert f"{q44['null_col']}isnull" in squashed(44)
    assert f"d_year={q49['year']}" in squashed(49)
    assert f"d_moy={q49['month']}" in squashed(49)
    assert "r_reason_desc='" + "".join(q93["reason"].split()) + "'" in squashed(93)


@pytest.mark.parametrize("seed,stream_id", [(None, 0), (None, 1), (42, 0), (42, 1)])
@pytest.mark.parametrize("query_id", [72, 75, 77, 78, 80, 81, 94, 95, 96])
def test_remaining_adapters_match_the_rendered_sql(dsqgen, query_id, seed, stream_id):
    binding = bind_parameters(query_id, scale_factor=0.01, seed=seed, stream_id=stream_id, dsqgen=dsqgen)
    sql = dsqgen.generate_with_parameters(query_id, dict(binding.logged), scale_factor=0.01, seed=seed or 1)
    sql = "".join(sql.split()).lower()
    parameters = binding.parameters

    if query_id == 72:
        assert f"d1.d_year={parameters['year']}" in sql
        assert f"hd_buy_potential='{parameters['buy_potential'].lower()}'" in sql
        assert f"cd_marital_status='{parameters['marital_status'].lower()}'" in sql
    elif query_id == 75:
        assert f"curr_yr.d_year={parameters['year']}" in sql
        assert f"prev_yr.d_year={parameters['year']}-1" in sql
        assert f"i_category='{parameters['category'].lower()}'" in sql
    elif query_id in (77, 80):
        date = parameters["sales_date"]
        assert f"d_datebetweencast('{date}'asdate)and(cast('{date}'asdate)+30days)" in sql
    elif query_id == 78:
        columns = "".join(parameters["select_columns"].split()).lower()
        assert f"{columns},round(ss_qty/" in sql
        assert f"orderby{columns},ss_qtydesc,ss_wcdesc,ss_spdesc" in sql
        assert f"ss_sold_year={parameters['year']}" in sql
    elif query_id == 81:
        assert f"d_year={parameters['year']}" in sql
        assert f"ca_state='{parameters['state'].lower()}'" in sql
    elif query_id in (94, 95):
        date = f"{binding.logged['YEAR.01']}-{binding.logged['MONTH.01']}-01"
        assert (parameters["year"], parameters["month"]) == (
            int(binding.logged["YEAR.01"]),
            int(binding.logged["MONTH.01"]),
        )
        assert f"d_datebetween'{date}'and(cast('{date}'asdate)+60days)" in sql
        assert len(parameters["states"]) == 1
        assert f"ca_state='{parameters['states'][0].lower()}'" in sql
    elif query_id == 96:
        assert f"time_dim.t_hour={parameters['hour']}" in sql
        assert "time_dim.t_minute>=30" in sql
        assert f"household_demographics.hd_dep_count={parameters['dep_count']}" in sql
        assert "store.s_store_name='ese'" in sql


def test_the_batch_adapters_bind_the_values_that_are_in_the_sql(dsqgen):
    def squash(text):
        return "".join(str(text).split())

    def squashed(query_id):
        return "".join(dsqgen.generate(query_id, seed=7, scale_factor=0.1).split())

    def bound(query_id):
        return bind_parameters(query_id, scale_factor=0.1, seed=7, dsqgen=dsqgen).parameters

    sql, q = squashed(1).lower(), bound(1)
    assert f"d_year={q['year']}" in sql and f"s_state='{q['state'].lower()}'" in sql and f"sum({q['agg_field']})" in sql

    sql, q = squashed(3).lower(), bound(3)
    assert f"i_manufact_id={q['manufact_id']}" in sql and f"d_moy={q['month']}" in sql
    assert f"sum({q['agg_column']})" in sql

    sql, q = squashed(7), bound(7)
    for column, key in (
        ("cd_gender", "gender"),
        ("cd_marital_status", "marital_status"),
        ("cd_education_status", "education"),
    ):
        assert f"{column}='{squash(q[key])}'" in sql
    assert f"d_year={q['year']}" in sql

    sql, q = squashed(8), bound(8)
    assert len(q["zip_codes"]) == 400
    assert all(f"'{zip_code}'" in sql for zip_code in q["zip_codes"])
    assert f"d_qoy={q['qoy']}" in sql and f"d_year={q['year']}" in sql

    sql, q = squashed(10), bound(10)
    assert "ca_countyin(" + ",".join(f"'{squash(county)}'" for county in q["counties"]) + ")" in sql
    assert f"d_year={q['year']}" in sql and f"d_moybetween{q['month']}and{q['month']}+3" in sql.lower()

    sql, q = squashed(12), bound(12)
    assert "i_categoryin(" + ",".join(f"'{squash(category)}'" for category in q["item_categories"]) + ")" in sql
    assert f"'{q['sales_date']}'" in sql

    sql, q = squashed(13), bound(13)
    for group in (1, 2, 3):
        assert f"cd_marital_status='{q[f'demo{group}_marital']}'" in sql
        assert f"cd_education_status='{squash(q[f'demo{group}_education'])}'" in sql
        assert "ca_statein(" + ",".join(f"'{state}'" for state in q[f"states{group}"]) + ")" in sql

    sql, q = squashed(14), bound(14)
    assert f"d_year={q['year']}+2" in sql or f"d_year={q['year']}+1" in sql

    sql, q = squashed(17), bound(17)
    assert f"'{q['year']}Q1'" in sql


def test_binding_follows_the_seed_and_the_scale(dsqgen):
    seeds = {bind_parameters(39, scale_factor=1.0, seed=seed, dsqgen=dsqgen).parameters["year"] for seed in range(1, 9)}
    assert len(seeds) > 1

    stores = {
        bind_parameters(44, scale_factor=scale, seed=7, dsqgen=dsqgen).parameters["store_sk"] for scale in (0.01, 1, 10)
    }
    assert len(stores) == 3


def _bound_keys(seen, binding):
    return {key: seen[key] for key in binding.parameters}


class TestGateBuilderWiring:
    @pytest.fixture
    def gate(self, monkeypatch, tmp_path):
        import dataclasses

        import benchbox.core.equivalence.builders.base as base
        import benchbox.tpcds as tpcds_module
        from benchbox.core.equivalence.builders.tpcds import TPCDS_GATE_QUERY_IDS, build_tpcds_duckdb
        from benchbox.core.tpcds.dataframe_queries import TPCDS_DATAFRAME_QUERIES, get_tpcds_query
        from benchbox.core.tpcds.dataframe_queries.parameters import get_parameters
        from benchbox.core.tpcds.dataframe_queries.production_binding import clear_binding_cache, query_number

        monkeypatch.setenv("BENCHBOX_CACHE_DIR", str(tmp_path / "cache"))
        clear_binding_cache()

        def recording(query_id):
            def run(ctx):
                if ctx == "boom":
                    raise RuntimeError("boom")
                return dict(get_parameters(query_id).params)

            return run

        registry = {
            query_id: dataclasses.replace(
                get_tpcds_query(f"Q{query_id}"),
                pandas_impl=recording(query_number(query_id)),
                expression_impl=recording(query_number(query_id)),
            )
            for query_id in TPCDS_GATE_QUERY_IDS
        }
        monkeypatch.setattr(TPCDS_DATAFRAME_QUERIES, "get_or_raise", lambda name: registry[name[1:]])
        monkeypatch.setattr(tpcds_module.TPCDS, "generate_data", lambda self: [])
        monkeypatch.setattr(base, "_load_duckdb_cell", lambda *args, **kwargs: object())
        data = build_tpcds_duckdb(0.01, tmp_path)
        return SimpleNamespace(data=data, registry=registry, get_parameters=get_parameters)

    def test_the_binding_applies_only_while_the_query_runs(self, gate, dsqgen):
        from benchbox.core.tpcds.dataframe_queries.parameters import TPCDS_DEFAULT_PARAMS

        query = gate.data.dataframe_query("39")
        binding = gate.data.dataframe_query.bindings[39]

        assert gate.get_parameters(39).params == TPCDS_DEFAULT_PARAMS[39]
        for implementation in (query.pandas_impl, query.expression_impl):
            assert _bound_keys(implementation(None), binding) == dict(binding.parameters)
        assert gate.get_parameters(39).params == TPCDS_DEFAULT_PARAMS[39]
        assert (binding.scale_factor, binding.seed, binding.stream_id) == (0.01, 1001, 0)
        assert re.fullmatch(r"[0-9a-f]{64}", binding.dsqgen_sha256)

    def test_other_lookups_between_lookup_and_execution_do_not_lose_the_binding(self, gate, dsqgen):
        q39 = gate.data.dataframe_query("39")
        gate.data.dataframe_query(str(UNADAPTED))
        q44 = gate.data.dataframe_query("44")

        for query, number in ((q44, 44), (q39, 39)):
            binding = gate.data.dataframe_query.bindings[number]
            assert _bound_keys(query.pandas_impl(None), binding) == dict(binding.parameters)

    def test_the_binding_is_removed_even_when_the_query_fails(self, gate, dsqgen):
        from benchbox.core.tpcds.dataframe_queries.parameters import TPCDS_DEFAULT_PARAMS

        query = gate.data.dataframe_query("44")

        with pytest.raises(RuntimeError, match="boom"):
            query.pandas_impl("boom")

        assert gate.get_parameters(44).params == TPCDS_DEFAULT_PARAMS[44]

    def test_the_shared_registry_object_is_not_modified_and_every_query_is_bound(self, gate, dsqgen):
        original = gate.registry["39"].pandas_impl

        adapted = gate.data.dataframe_query("39")

        assert adapted is not gate.registry["39"]
        assert gate.registry["39"].pandas_impl is original
        assert gate.data.dataframe_query(str(UNADAPTED)) is not gate.registry[str(UNADAPTED)]
        assert set(gate.data.dataframe_query.bindings) == set(range(1, 100))
