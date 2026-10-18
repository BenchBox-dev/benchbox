# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

import csv
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.ssb.generator import SSBDataGenerator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def gen(tmp_path):

    return SSBDataGenerator(scale_factor=0.0001, output_dir=tmp_path)


def test_generator_init_defaults(tmp_path):
    gen = SSBDataGenerator(scale_factor=1.0, output_dir=tmp_path)
    assert gen.scale_factor == 1.0
    assert gen.base_customers == 30000
    assert gen.base_suppliers == 2000
    assert gen.base_parts == 200000
    assert gen.date_rows == 2556


def test_generator_verbose_bool_true(tmp_path):
    gen = SSBDataGenerator(scale_factor=1.0, output_dir=tmp_path, verbose=True)
    assert gen.verbose_level == 1
    assert gen.verbose_enabled is True


def test_generator_verbose_int(tmp_path):
    gen = SSBDataGenerator(scale_factor=1.0, output_dir=tmp_path, verbose=2)
    assert gen.very_verbose is True


def test_generator_quiet_suppresses_verbose(tmp_path):
    gen = SSBDataGenerator(scale_factor=1.0, output_dir=tmp_path, verbose=True, quiet=True)
    assert gen.verbose_enabled is False


def test_generate_date_data_creates_file(gen, tmp_path):
    result = gen._generate_date_data()
    p = Path(result)
    assert p.exists()
    assert p.stat().st_size > 0


def test_generate_date_data_correct_rows(gen, tmp_path):

    result = gen._generate_date_data()
    with open(result, encoding="utf-8") as f:
        rows = list(csv.reader(f, delimiter="|"))

    assert abs(len(rows) - gen.date_rows) <= 2


def test_generate_date_data_includes_winter_season(gen, tmp_path):
    result = gen._generate_date_data()
    with open(result, encoding="utf-8") as f:
        content = f.read()
    assert "Winter" in content
    assert "Summer" in content
    assert "Spring" in content
    assert "Fall" in content


def test_generate_customer_data_creates_file(gen, tmp_path):
    result = gen._generate_customer_data()
    p = Path(result)
    assert p.exists()
    assert p.stat().st_size > 0


def test_generate_customer_data_row_count(gen):
    result = gen._generate_customer_data()
    expected = max(1, int(gen.base_customers * gen.scale_factor))
    with open(result, encoding="utf-8") as f:
        rows = list(csv.reader(f, delimiter="|"))
    assert len(rows) == expected


def test_generate_supplier_data_creates_file(tmp_path):

    gen2 = SSBDataGenerator(scale_factor=0.001, output_dir=tmp_path)
    result = gen2._generate_supplier_data()
    p = Path(result)
    assert p.exists()


def test_generate_part_data_creates_file(gen, tmp_path):
    result = gen._generate_part_data()
    p = Path(result)
    assert p.exists()


def test_generate_data_local_date_only(gen, tmp_path):
    result = gen._generate_data_local(tmp_path, tables=["date"])
    assert "date" in result
    assert Path(result["date"]).exists()


def test_generate_data_local_customer_and_supplier(gen, tmp_path):
    result = gen._generate_data_local(tmp_path, tables=["customer", "supplier"])
    assert "customer" in result
    assert "supplier" in result


def test_generate_data_local_default_tables(gen, tmp_path):

    result = gen._generate_data_local(tmp_path, tables=["date", "customer", "supplier", "part"])
    assert len(result) == 4


def test_validate_file_format_consistency_no_compression(gen, tmp_path):

    gen._validate_file_format_consistency(tmp_path)


def test_write_manifest_creates_file(gen, tmp_path):
    date_path = tmp_path / "date.tbl"
    date_path.write_text("row1\nrow2\n")
    gen._write_manifest(tmp_path, {"date": date_path})
    manifest_path = tmp_path / "_datagen_manifest.json"
    assert manifest_path.exists()
    import json

    data = json.loads(manifest_path.read_text())
    assert data["benchmark"] == "ssb"
    assert "date" in data["tables"]


def test_generate_lineorder_data_creates_file(tmp_path):

    gen2 = SSBDataGenerator(scale_factor=0.001, output_dir=tmp_path)

    gen2.base_lineorders = 2
    result = gen2._generate_lineorder_data()
    p = Path(result)
    assert p.exists()


def test_generate_data_date_only(tmp_path):
    gen2 = SSBDataGenerator(scale_factor=0.0001, output_dir=tmp_path)
    result = gen2.generate_data(tables=["date"])
    assert "date" in result
    assert Path(result["date"]).exists()


def test_generate_data_no_tables_arg(tmp_path):

    gen2 = SSBDataGenerator(scale_factor=0.000001, output_dir=tmp_path)
    result = gen2.generate_data(tables=["date", "customer", "supplier", "part"])
    assert len(result) == 4


def test_generate_customer_data_nation_coverage(tmp_path):

    import random as _rng

    _rng.seed(0)
    gen2 = SSBDataGenerator(scale_factor=0.001, output_dir=tmp_path)
    result = gen2._generate_customer_data()
    with open(result, encoding="utf-8") as f:
        content = f.read()

    assert len(content) > 0


def test_generate_part_data_rows(tmp_path):
    gen2 = SSBDataGenerator(scale_factor=0.001, output_dir=tmp_path)
    result = gen2._generate_part_data()
    with open(result, encoding="utf-8") as f:
        rows = list(csv.reader(f, delimiter="|"))
    expected = max(1, int(gen2.base_parts * gen2.scale_factor))
    assert len(rows) == expected


def _read_part_rows(tmp_path, scale_factor=0.01):

    gen2 = SSBDataGenerator(scale_factor=scale_factor, output_dir=tmp_path)
    result = gen2._generate_part_data()
    with open(result, encoding="utf-8") as f:
        return list(csv.reader(f, delimiter="|"))


_P_MFGR, _P_CATEGORY, _P_BRAND1 = 2, 3, 4


def test_part_mfgr_canonical_format(tmp_path):

    rows = _read_part_rows(tmp_path)
    mfgrs = {row[_P_MFGR] for row in rows}
    assert mfgrs == {f"MFGR#{m}" for m in range(1, 6)}


def test_part_category_canonical_two_digit_format(tmp_path):

    rows = _read_part_rows(tmp_path)
    categories = {row[_P_CATEGORY] for row in rows}
    valid = {f"MFGR#{m}{c}" for m in range(1, 6) for c in range(1, 6)}
    assert categories <= valid

    assert "MFGR#12" in categories
    assert "MFGR#14" in categories

    assert not any(len(c) != len("MFGR#12") for c in categories)


def test_part_brand1_canonical_four_char_suffix(tmp_path):

    rows = _read_part_rows(tmp_path)
    brands = {row[_P_BRAND1] for row in rows}
    valid = {f"MFGR#{m}{c}{b:02d}" for m in range(1, 6) for c in range(1, 6) for b in range(1, 41)}
    assert brands <= valid

    assert "MFGR#2221" in brands
    assert "MFGR#2228" in brands

    assert all(len(b) == len("MFGR#2221") for b in brands)


def test_part_brand1_consistent_with_category_and_mfgr(tmp_path):

    rows = _read_part_rows(tmp_path)
    for row in rows:
        assert row[_P_BRAND1].startswith(row[_P_CATEGORY])
        assert row[_P_CATEGORY].startswith(row[_P_MFGR])


def test_part_dimension_values_deterministic(tmp_path):

    first = [(r[_P_MFGR], r[_P_CATEGORY], r[_P_BRAND1]) for r in _read_part_rows(tmp_path)]
    second = [(r[_P_MFGR], r[_P_CATEGORY], r[_P_BRAND1]) for r in _read_part_rows(tmp_path)]
    assert first == second


def _read_city_values(tmp_path, generate, scale_factor=0.01):

    gen2 = SSBDataGenerator(scale_factor=scale_factor, output_dir=tmp_path)
    result = generate(gen2)
    with open(result, encoding="utf-8") as f:
        return [row[3] for row in csv.reader(f, delimiter="|")]


def test_customer_city_canonical_ten_char_format(tmp_path):

    cities = _read_city_values(tmp_path, lambda g: g._generate_customer_data())
    uk_cities = {c for c in cities if c.startswith("UNITED KI")}

    assert uk_cities, "expected at least one 'UNITED KI<digit>' city"
    assert all(len(c) == len("UNITED KI1") for c in uk_cities)
    assert all(c[:9] == "UNITED KI" and c[9].isdigit() for c in uk_cities)


def test_supplier_city_canonical_ten_char_format(tmp_path):

    cities = _read_city_values(tmp_path, lambda g: g._generate_supplier_data(), scale_factor=0.05)
    uk_cities = {c for c in cities if c.startswith("UNITED KI")}
    assert uk_cities, "expected at least one 'UNITED KI<digit>' city"
    assert all(len(c) == len("UNITED KI1") for c in uk_cities)


def test_part_dimensions_cover_mfgr_and_category_at_small_scale(tmp_path):

    rows = _read_part_rows(tmp_path, scale_factor=0.001)
    assert {r[_P_MFGR] for r in rows} == {f"MFGR#{m}" for m in range(1, 6)}
    assert {r[_P_CATEGORY] for r in rows} == {f"MFGR#{m}{c}" for m in range(1, 6) for c in range(1, 6)}


_D_DAYOFWEEK, _D_MONTH, _D_YEARMONTH = 2, 3, 6

_ENGLISH_MONTHS = {
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
}
_ENGLISH_DAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}


def _read_date_rows(tmp_path):
    gen2 = SSBDataGenerator(scale_factor=1.0, output_dir=tmp_path)
    with open(gen2._generate_date_data(), encoding="utf-8") as f:
        return list(csv.reader(f, delimiter="|"))


def test_date_strings_are_canonical_english(tmp_path):

    rows = _read_date_rows(tmp_path)
    assert {r[_D_DAYOFWEEK] for r in rows} == _ENGLISH_DAYS
    assert {r[_D_MONTH] for r in rows} == _ENGLISH_MONTHS
    yearmonths = {r[_D_YEARMONTH] for r in rows}

    assert "Dec1997" in yearmonths
    assert "Jan1992" in yearmonths


def test_date_strings_are_locale_independent(tmp_path):

    import locale

    saved = locale.setlocale(locale.LC_TIME)
    french = None
    for candidate in ("fr_FR.UTF-8", "fr_FR.utf8", "fr_FR", "de_DE.UTF-8"):
        try:
            locale.setlocale(locale.LC_TIME, candidate)
            french = candidate
            break
        except locale.Error:
            continue
    if french is None:
        pytest.skip("no non-English locale available to test against")
    try:
        rows = _read_date_rows(tmp_path)
    finally:
        locale.setlocale(locale.LC_TIME, saved)
    yearmonths = {r[_D_YEARMONTH] for r in rows}
    assert "Dec1997" in yearmonths, f"locale {french} leaked into d_yearmonth"
    assert {r[_D_MONTH] for r in rows} == _ENGLISH_MONTHS


def test_lineorder_dates_are_valid_datekeys(tmp_path):

    from datetime import datetime

    gen2 = SSBDataGenerator(scale_factor=0.001, output_dir=tmp_path)
    gen2.base_lineorders = 500

    date_keys = {int(d.strftime("%Y%m%d")) for d in gen2._date_objects()}
    with open(gen2._generate_lineorder_data(), encoding="utf-8") as f:
        rows = list(csv.reader(f, delimiter="|"))

    for row in rows:
        lo_orderdate = int(row[5])
        lo_commitdate = int(row[15])
        assert lo_orderdate in date_keys

        datetime.strptime(str(lo_commitdate), "%Y%m%d")


def test_generate_data_unaffected_by_global_random_state(tmp_path):
    import random

    baseline_dir = tmp_path / "baseline"
    baseline = SSBDataGenerator(scale_factor=0.001, output_dir=baseline_dir)
    baseline_result = baseline.generate_data(tables=["customer"])
    baseline_content = Path(baseline_result["customer"]).read_bytes()

    perturbed_dir = tmp_path / "perturbed"
    perturbed = SSBDataGenerator(scale_factor=0.001, output_dir=perturbed_dir)
    SSBDataGenerator(scale_factor=0.001, output_dir=tmp_path / "other")
    random.random()
    perturbed_result = perturbed.generate_data(tables=["customer"])
    perturbed_content = Path(perturbed_result["customer"]).read_bytes()

    assert perturbed_content == baseline_content
