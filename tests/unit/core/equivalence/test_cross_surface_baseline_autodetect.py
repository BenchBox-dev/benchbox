from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import duckdb
import pytest
import yaml

from benchbox.core.equivalence import cross_surface
from benchbox.core.equivalence.builders.base import CrossSurfaceData
from benchbox.core.equivalence.cross_surface import CrossSurfaceGate

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[4]


def _load_script():
    name = "cross_surface_baseline_autodetect"
    path = REPO_ROOT / "_project" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


autodetect = _load_script()


class _FakeFrame:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def rows(self) -> list[tuple[Any, ...]]:
        return self._rows


class _FakeQuery:
    def __init__(self, impls: dict[str, list[tuple[Any, ...]]]) -> None:
        self._impls = impls

    def get_impl_for_family(self, backend: str):
        if backend not in self._impls:
            return None
        rows = self._impls[backend]
        return lambda _ctx, _rows=rows: _FakeFrame(_rows)


def _build_gate(name: str, known_divergences: dict, *, q2_pandas_matches: bool) -> CrossSurfaceGate:

    def build(_scale: float, _tmp: Path) -> CrossSurfaceData:
        connection = duckdb.connect(":memory:")
        queries = {
            "Q1": _FakeQuery({"expression": [(1,)], "pandas": [(1,)]}),
            "Q2": _FakeQuery({"expression": [(2,)], "pandas": [(2,)] if q2_pandas_matches else [(999,)]}),
        }
        return CrossSurfaceData(
            connection=connection,
            query_ids=["Q1", "Q2"],
            reference_sql=lambda qid: {"Q1": "SELECT 1", "Q2": "SELECT 2"}[qid],
            dataframe_query=lambda qid: queries[qid],
            benchmark=None,
            data_dir=Path("/unused"),
        )

    return CrossSurfaceGate(name=name, build=build, known_divergences=dict(known_divergences))


@pytest.fixture(autouse=True)
def _stub_production_contexts(monkeypatch):
    monkeypatch.setattr(
        cross_surface,
        "build_production_contexts",
        lambda *a, **k: {"expression": None, "pandas": None},
    )


@pytest.fixture
def baseline_file(tmp_path, monkeypatch):
    path = tmp_path / "cross_surface_baseline.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "fake": {
                    "Q1_pandas": "documented test baseline (now fixed)",
                    "Q2_pandas": "documented test baseline (still live)",
                },
                "unrelated": {"SomeKey_pandas": "untouched by fake's prune"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cross_surface, "_BASELINE_YAML_PATH", path)
    return path


def test_detect_and_prune_surfaces_exactly_the_resolved_entry_and_prunes_only_it(baseline_file):
    gate = _build_gate(
        "fake",
        {
            "Q1_pandas": "documented test baseline (now fixed)",
            "Q2_pandas": "documented test baseline (still live)",
        },
        q2_pandas_matches=False,
    )

    outcome = autodetect.detect_and_prune(gate)

    assert outcome.detect_exit_code == 1

    assert outcome.resolved_detected == ["Q1_pandas"]

    assert outcome.pruned == ["Q1_pandas"]
    assert outcome.code_only == []
    assert outcome.refused is False
    assert outcome.needs_attention is False

    assert outcome.prune_exit_code == 0

    data = yaml.safe_load(baseline_file.read_text(encoding="utf-8"))

    assert data["fake"] == {"Q2_pandas": "documented test baseline (still live)"}

    assert data["unrelated"] == {"SomeKey_pandas": "untouched by fake's prune"}


def test_detect_and_prune_is_a_no_op_once_the_baseline_is_already_clean(baseline_file):
    before = baseline_file.read_text(encoding="utf-8")
    gate = _build_gate("fake", {"Q2_pandas": "documented test baseline (still live)"}, q2_pandas_matches=False)

    outcome = autodetect.detect_and_prune(gate)

    assert outcome.detect_exit_code == 0
    assert outcome.resolved_detected == []
    assert outcome.prune_ran is False
    assert outcome.prune_exit_code is None
    assert outcome.pruned == []
    assert outcome.needs_attention is False
    assert baseline_file.read_text(encoding="utf-8") == before


def test_still_reproducing_divergence_alone_is_never_treated_as_resolved(baseline_file):
    gate = _build_gate("fake", {"Q2_pandas": "documented test baseline (still live)"}, q2_pandas_matches=False)

    outcome = autodetect.detect_and_prune(gate)

    assert outcome.detect_exit_code == 0
    assert outcome.resolved_detected == []
    assert outcome.prune_ran is False


def test_run_autodetect_processes_every_enforced_gate_in_sorted_order(monkeypatch, baseline_file):
    resolved_gate = _build_gate(
        "fake",
        {"Q1_pandas": "now fixed", "Q2_pandas": "still live"},
        q2_pandas_matches=False,
    )
    clean_gate = _build_gate("zzz-clean", {}, q2_pandas_matches=True)
    monkeypatch.setattr(cross_surface, "GATES", {"fake": resolved_gate, "zzz-clean": clean_gate})

    outcomes = autodetect.run_autodetect()

    assert [outcome.gate for outcome in outcomes] == ["fake", "zzz-clean"]
    by_name = {outcome.gate: outcome for outcome in outcomes}
    assert by_name["fake"].pruned == ["Q1_pandas"]
    assert by_name["zzz-clean"].resolved_detected == []


def test_run_autodetect_honors_an_explicit_gate_subset(monkeypatch, baseline_file):
    resolved_gate = _build_gate(
        "fake",
        {"Q1_pandas": "now fixed", "Q2_pandas": "still live"},
        q2_pandas_matches=False,
    )
    other_gate = _build_gate("other", {}, q2_pandas_matches=True)
    monkeypatch.setattr(cross_surface, "GATES", {"fake": resolved_gate, "other": other_gate})

    outcomes = autodetect.run_autodetect(["fake"])

    assert [outcome.gate for outcome in outcomes] == ["fake"]


def test_summarize_flags_any_pruned_and_needs_attention(baseline_file):
    resolved_gate = _build_gate(
        "fake",
        {"Q1_pandas": "now fixed", "Q2_pandas": "still live"},
        q2_pandas_matches=False,
    )
    outcome = autodetect.detect_and_prune(resolved_gate)

    summary = autodetect.summarize([outcome])

    assert summary["any_pruned"] is True
    assert summary["needs_attention"] == []
    assert summary["gates"]["fake"]["pruned"] == ["Q1_pandas"]


def test_extract_list_reads_the_resolved_report_line_verbatim():
    text = (
        "GATE FAILURE - previously-known divergences now equivalent: "
        "['Q1_pandas'] - remove the stale baseline entry in a reviewed change"
    )
    assert autodetect._extract_list(autodetect._RESOLVED_RE, text) == ["Q1_pandas"]


def test_extract_list_reads_the_removed_report_line_verbatim():
    text = "fake: removed resolved known-divergence baseline entries: ['Q1_pandas', 'Q2_pandas']"
    assert autodetect._extract_list(autodetect._REMOVED_RE, text) == ["Q1_pandas", "Q2_pandas"]


def test_extract_list_returns_empty_when_the_marker_is_absent():
    assert autodetect._extract_list(autodetect._RESOLVED_RE, "SQL and DataFrame surfaces are equivalent.") == []


def test_code_only_entry_is_pruned_where_possible_but_flagged_needs_attention(tmp_path, monkeypatch):
    from benchbox.core.equivalence.cross_surface import ClassifiedDivergence

    path = tmp_path / "cross_surface_baseline.yaml"
    path.write_text(
        yaml.safe_dump({"fake": {"Q1_pandas": "documented test baseline (now fixed)"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cross_surface, "_BASELINE_YAML_PATH", path)

    gate = _build_gate(
        "fake",
        {
            "Q1_pandas": "documented test baseline (now fixed)",
            "Q2_pandas": ClassifiedDivergence(reason="code-only", accepts=lambda d: False),
        },
        q2_pandas_matches=True,
    )

    outcome = autodetect.detect_and_prune(gate)

    assert set(outcome.resolved_detected) == {"Q1_pandas", "Q2_pandas"}
    assert outcome.pruned == ["Q1_pandas"]
    assert outcome.code_only == ["Q2_pandas"]
    assert outcome.needs_attention is True
    assert outcome.prune_exit_code == 1

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["fake"] == {}


def test_refusal_is_never_masked_when_an_unrelated_divergence_rides_along(tmp_path, monkeypatch):
    path = tmp_path / "cross_surface_baseline.yaml"
    path.write_text(
        yaml.safe_dump({"fake": {"Q1_pandas": "documented test baseline (now fixed)"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cross_surface, "_BASELINE_YAML_PATH", path)
    before = path.read_text(encoding="utf-8")

    gate = _build_gate(
        "fake",
        {"Q1_pandas": "documented test baseline (now fixed)"},
        q2_pandas_matches=False,
    )

    outcome = autodetect.detect_and_prune(gate)

    assert outcome.resolved_detected == ["Q1_pandas"]
    assert outcome.prune_ran is True

    assert autodetect._REFUSED_MARKER in outcome.report
    assert outcome.refused is True
    assert outcome.pruned == []
    assert outcome.prune_exit_code == 1

    assert path.read_text(encoding="utf-8") == before


