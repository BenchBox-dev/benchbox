from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from benchbox.core.tpcds import c_tools
from benchbox.core.tpcds.c_tools import (
    SUBSCALE_TEMPLATE_OVERRIDES,
    DSQGenBinary,
    TPCDSError,
    _resolve_tpcds_tool_and_template_paths,
    apply_subscale_template_overrides,
)

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]

_REASON = re.compile(r"r_reason_desc = '([^']*)'")
_SEEDS = (1, 2, 42, 987654)


def _template_copy(tmp_path: Path, *names: str) -> Path:
    _, templates_dir = _resolve_tpcds_tool_and_template_paths()
    query_dir = tmp_path / "q"
    query_dir.mkdir()
    for name in names:
        shutil.copy2(templates_dir / name, query_dir / name)
    return query_dir


@pytest.mark.parametrize("scale_factor", [1, 1.0, 10, 100])
def test_official_template_is_untouched_from_sf1_up(tmp_path, scale_factor):
    query_dir = _template_copy(tmp_path, "query93.tpl")
    before = (query_dir / "query93.tpl").read_bytes()
    apply_subscale_template_overrides(query_dir, scale_factor)
    assert (query_dir / "query93.tpl").read_bytes() == before


@pytest.mark.parametrize("scale_factor", [0.01, 0.1, 0.5])
def test_reason_draw_is_rewritten_below_sf1(tmp_path, scale_factor):
    query_dir = _template_copy(tmp_path, "query93.tpl")
    official, override = SUBSCALE_TEMPLATE_OVERRIDES["query93.tpl"]
    apply_subscale_template_overrides(query_dir, scale_factor)
    text = (query_dir / "query93.tpl").read_text(encoding="utf-8")
    assert official not in text
    assert text.count(override) == 1


def test_override_touches_only_the_selected_templates(tmp_path):
    query_dir = tmp_path / "q"
    query_dir.mkdir()
    (query_dir / "query93.tpl").write_text("define REASON= dist(other, 1, 1);\n", encoding="utf-8")
    apply_subscale_template_overrides(query_dir, 0.1, {"query1.tpl"})
    assert (query_dir / "query93.tpl").read_text(encoding="utf-8") == "define REASON= dist(other, 1, 1);\n"


@pytest.mark.parametrize("copies", [0, 2])
def test_override_requires_exactly_one_official_line(tmp_path, copies):
    official, _ = SUBSCALE_TEMPLATE_OVERRIDES["query93.tpl"]
    query_dir = tmp_path / "q"
    query_dir.mkdir()
    (query_dir / "query93.tpl").write_text(f"{official}\n" * copies, encoding="utf-8")
    with pytest.raises(TPCDSError, match="exactly once"):
        apply_subscale_template_overrides(query_dir, 0.1)


def _reason_rows(scale_factor: float, output_dir: Path) -> list[str]:
    from benchbox.core.tpcds.generator.manager import TPCDSDataGenerator

    generator = TPCDSDataGenerator(scale_factor=scale_factor, output_dir=output_dir, compress_data=False, quiet=True)
    assert generator.dsdgen_available
    (path,) = generator.generate_tables(["reason"])["reason"]
    return [line.split("|")[2] for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.parametrize(("scale_factor", "row_count"), [(0.01, 1), (0.05, 3), (0.1, 7), (0.25, 18), (0.5, 37)])
def test_q93_draws_a_reason_the_generated_table_contains(tmp_path, scale_factor, row_count):
    reasons = _reason_rows(scale_factor, tmp_path)
    assert len(reasons) == row_count
    for seed in _SEEDS:
        (drawn,) = _REASON.findall(DSQGenBinary().generate(93, seed=seed, scale_factor=scale_factor))
        assert drawn in reasons
        (repeated,) = _REASON.findall(DSQGenBinary().generate(93, seed=seed, scale_factor=scale_factor))
        assert repeated == drawn
        logged = DSQGenBinary().generate_parameter_log(93, seed=seed, scale_factor=scale_factor)
        assert logged.values["REASON.01"] == drawn


@pytest.mark.parametrize("scale_factor", [1.0, 10.0])
def test_q93_text_is_official_from_sf1_up(monkeypatch, scale_factor):
    with_override = {seed: DSQGenBinary().generate(93, seed=seed, scale_factor=scale_factor) for seed in _SEEDS}
    monkeypatch.setattr(c_tools, "SUBSCALE_TEMPLATE_OVERRIDES", {})
    official = {seed: DSQGenBinary().generate(93, seed=seed, scale_factor=scale_factor) for seed in _SEEDS}
    assert with_override == official


def test_other_queries_are_untouched_below_sf1(monkeypatch):
    with_override = {query: DSQGenBinary().generate(query, seed=7, scale_factor=0.1) for query in (1, 9, 85, 92, 94)}
    monkeypatch.setattr(c_tools, "SUBSCALE_TEMPLATE_OVERRIDES", {})
    official = {query: DSQGenBinary().generate(query, seed=7, scale_factor=0.1) for query in (1, 9, 85, 92, 94)}
    assert with_override == official
