from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from benchbox.core.tpcds.c_tools import (
    SUBSCALE_TEMPLATE_OVERRIDES,
    TPCDSError,
    _resolve_tpcds_tool_and_template_paths,
    apply_subscale_template_overrides,
)

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]

_REASON = re.compile(r"r_reason_desc = '([^']*)'")


def _template_copy(tmp_path: Path) -> Path:
    _, templates_dir = _resolve_tpcds_tool_and_template_paths()
    query_dir = tmp_path / "q"
    query_dir.mkdir()
    shutil.copy2(templates_dir / "query93.tpl", query_dir / "query93.tpl")
    return query_dir


@pytest.mark.parametrize("scale_factor", [1, 1.0, 10, 100])
def test_official_template_is_untouched_from_sf1_up(tmp_path, scale_factor):
    query_dir = _template_copy(tmp_path)
    before = (query_dir / "query93.tpl").read_bytes()
    apply_subscale_template_overrides(query_dir, scale_factor)
    assert (query_dir / "query93.tpl").read_bytes() == before


@pytest.mark.parametrize("scale_factor", [0.01, 0.1, 0.5])
def test_reason_draw_is_bounded_by_the_reason_row_count_below_sf1(tmp_path, scale_factor):
    query_dir = _template_copy(tmp_path)
    official, override = SUBSCALE_TEMPLATE_OVERRIDES["query93.tpl"]
    apply_subscale_template_overrides(query_dir, scale_factor)
    text = (query_dir / "query93.tpl").read_text(encoding="utf-8")
    assert official not in text
    assert override in text


def test_override_refuses_a_template_without_the_official_line(tmp_path):
    query_dir = tmp_path / "q"
    query_dir.mkdir()
    (query_dir / "query93.tpl").write_text("define REASON= dist(other, 1, 1);\n", encoding="utf-8")
    with pytest.raises(TPCDSError, match="sub-SF1 override"):
        apply_subscale_template_overrides(query_dir, 0.1)


@pytest.fixture(scope="module")
def query_manager():
    from benchbox.core.tpcds.queries import TPCDSQueryManager

    manager = TPCDSQueryManager()
    if not manager.available:
        pytest.skip("dsqgen is not available")
    return manager


def _reason_rows(scale_factor: float, output_dir: Path) -> list[str]:
    from benchbox.core.tpcds.generator.manager import TPCDSDataGenerator

    generator = TPCDSDataGenerator(scale_factor=scale_factor, output_dir=output_dir, compress_data=False, quiet=True)
    if not generator.dsdgen_available:
        pytest.skip("dsdgen is not available")
    (path,) = generator.generate_tables(["reason"])["reason"]
    return [line.split("|")[2] for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.parametrize(("scale_factor", "row_count"), [(0.01, 1), (0.1, 7)])
def test_q93_draws_a_reason_the_generated_table_contains(query_manager, tmp_path, scale_factor, row_count):
    reasons = _reason_rows(scale_factor, tmp_path)
    assert len(reasons) == row_count
    for seed in (None, 1, 2, 42, 987654):
        sql = query_manager.get_query(93, seed=seed, scale_factor=scale_factor)
        (drawn,) = _REASON.findall(sql)
        assert drawn in reasons
        assert query_manager.get_query(93, seed=seed, scale_factor=scale_factor) == sql
        logged = query_manager.dsqgen.generate_parameter_log(93, seed=seed, scale_factor=scale_factor)
        assert logged.values["REASON.01"] == drawn


def test_q93_keeps_the_official_draw_from_sf1_up(query_manager):
    (drawn,) = _REASON.findall(query_manager.get_query(93, scale_factor=1.0))
    assert drawn == "Did not like the warranty"
