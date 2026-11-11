import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
    pytest.mark.tpcds,
]


@pytest.fixture(scope="module")
def dsqgen():
    from benchbox.core.tpcds.c_tools import DSQGenBinary, TPCDSError

    try:
        return DSQGenBinary()
    except (TPCDSError, FileNotFoundError, RuntimeError) as exc:
        pytest.skip(f"dsqgen binary or templates unavailable: {exc}")


def _squash(sql):
    return " ".join(sql.split())


def test_explicit_values_win_over_the_seed(dsqgen):
    for seed in (1, 77):
        sql = _squash(dsqgen.generate_with_parameters(39, {"YEAR.01": 2001, "MONTH.01": 1}, seed=seed))

        assert "d_year =2001" in sql
        assert "inv1.d_moy=1" in sql
        assert "inv2.d_moy=1+1" in sql


def test_text_values_and_dependent_defines(dsqgen):
    q93 = _squash(dsqgen.generate_with_parameters(93, {"REASON.01": "reason 28"}))
    q44 = _squash(dsqgen.generate_with_parameters(44, {"STORE.01": 4, "NULLCOLSS.01": "ss_addr_sk"}))

    assert "r_reason_desc = 'reason 28'" in q93
    assert "ss_store_sk = 4" in q44
    assert "ss_addr_sk is null" in q44


def test_a_list_of_four_hundred_values(dsqgen):
    zips = {f"ZIP.{index:03d}": str(10000 + index) for index in range(1, 401)}

    sql = _squash(dsqgen.generate_with_parameters(8, {**zips, "YEAR.01": 2001, "QOY.01": 2}))

    assert all(f"'{10000 + index}'" in sql for index in (1, 200, 400))
    assert "d_year = 2001" in sql


def test_multi_part_variant_selects_its_part(dsqgen):
    sql = dsqgen.generate_with_parameters("39a", {"YEAR.01": 2001, "MONTH.01": 1})

    assert "d_year =2001" in _squash(sql)
    assert sql != dsqgen.generate_with_parameters("39b", {"YEAR.01": 2001, "MONTH.01": 1})


VARIANT_QUERY_IDS = ["5a", "10a", "14a", "18a", "22a", "27a", "35a", "36a", "51a", "67a", "70a", "77a", "80a", "86a"]


@pytest.mark.parametrize("query_id", [*range(1, 100), *VARIANT_QUERY_IDS])
def test_values_from_a_seed_reproduce_that_seed(dsqgen, query_id):
    values = dsqgen.generate_parameter_log(query_id, seed=7, scale_factor=1.0).substitutions
    reference = dsqgen.generate(query_id, seed=7)

    rendered = dsqgen.generate_with_parameters(query_id, values, seed=99)

    assert _squash(rendered) == _squash(reference)


@pytest.mark.parametrize("name", ["YEAR", "YEAR.", "YEAR.x", ".01"])
def test_malformed_names_are_rejected(dsqgen, name):
    with pytest.raises(ValueError, match="Parameter name"):
        dsqgen.generate_with_parameters(39, {name: 1})


@pytest.mark.parametrize("name", ["NOPE.01", "year.01"])
def test_a_name_the_template_does_not_define_is_rejected(dsqgen, name):
    with pytest.raises(ValueError, match=f"no substitution for parameter '{name}'"):
        dsqgen.generate_with_parameters(39, {name: 1})


def test_a_defined_value_the_template_does_not_use_is_accepted(dsqgen):
    used = _squash(dsqgen.generate_with_parameters(10, {"COUNTY.01": "Aa County"}))
    with_unused = _squash(dsqgen.generate_with_parameters(10, {"COUNTY.01": "Aa County", "COUNTY.10": "Zz County"}))

    assert "Aa County" in used
    assert with_unused == used


def test_invalid_query_id_is_rejected(dsqgen):
    with pytest.raises(ValueError):
        dsqgen.generate_with_parameters(100, {"YEAR.01": 2001})


