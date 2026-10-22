from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from benchbox.core.tpcds.c_tools import SUBSCALE_TEMPLATE_OVERRIDES, DSQGenBinary

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]


@pytest.fixture(scope="module")
def dsqgen() -> DSQGenBinary:
    return DSQGenBinary()


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _stage(dsqgen: DSQGenBinary, tmp_path: Path, query_id: int, scale_factor: float, template_arg: str) -> Path:
    dsqgen._stage_dsqgen_workdir(tmp_path, scale_factor, query_id, template_arg)
    return tmp_path / "q"


@pytest.mark.parametrize(
    ("query_id", "variant", "is_multi_part", "expected_template"),
    [
        (5, None, False, "q/query5.tpl"),
        (14, "a", True, "q/query14.tpl"),
        (10, "a", False, "query_variants/query10a.tpl"),
    ],
)
def test_workdir_holds_support_files_and_only_the_requested_template(
    dsqgen, tmp_path, query_id, variant, is_multi_part, expected_template
):
    template_arg = dsqgen._resolve_template_arg(query_id, variant, is_multi_part)
    _stage(dsqgen, tmp_path, query_id, 1.0, template_arg)
    staged_templates = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("query*.tpl") if p.is_file())
    assert staged_templates == [expected_template]
    for support in ("ansi.tpl", "netezza.tpl", "templates.lst"):
        assert (tmp_path / "q" / support).is_file()
    assert (tmp_path / "tpcds.dst").is_file()
    assert (tmp_path / "tpcds.idx").is_file()


def test_subscale_override_edits_exactly_one_private_file(dsqgen, tmp_path):
    source_before = _tree_digest(dsqgen.templates_dir)
    query_dir = _stage(dsqgen, tmp_path, 93, 0.01, "query93.tpl")
    differing = [p.name for p in query_dir.iterdir() if p.read_bytes() != (dsqgen.templates_dir / p.name).read_bytes()]
    assert differing == ["query93.tpl"]
    official, override = SUBSCALE_TEMPLATE_OVERRIDES["query93.tpl"]
    staged_text = (query_dir / "query93.tpl").read_text(encoding="utf-8")
    assert override in staged_text
    assert official not in staged_text
    assert not os.path.samefile(query_dir / "query93.tpl", dsqgen.templates_dir / "query93.tpl")
    assert _tree_digest(dsqgen.templates_dir) == source_before


def test_calls_leave_template_tree_unchanged_and_threads_agree_with_serial(dsqgen):
    jobs = [(q, sf) for q in (1, 5, 14, 23, 39, 93, 99) for sf in (0.01, 1.0)] * 4
    expected = {(q, sf): dsqgen.generate_with_parameters(q, {}, scale_factor=sf, seed=3) for q, sf in set(jobs)}
    before = _tree_digest(dsqgen.templates_dir.parent)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda job: dsqgen.generate_with_parameters(job[0], {}, scale_factor=job[1], seed=3), jobs)
        )
    assert dict(zip(jobs, results)) == expected
    assert _tree_digest(dsqgen.templates_dir.parent) == before
