from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from _project.scripts.generate_oracle_coverage_map import (
    INDEPENDENCE_NONE,
    INDEPENDENCE_SELF,
    INDEPENDENCE_SEMI,
    ORACLE_CROSS_SURFACE,
    ORACLE_CROSS_SURFACE_VARIANT,
    ORACLE_EXPECTED_RESULTS,
    ORACLE_NONE,
    ORACLE_VARIANT_EQUIVALENCE,
    PROVENANCE_MIXED,
    PROVENANCE_NONE,
    PROVENANCE_SEPARATE,
    PROVENANCE_SHARED_SPEC,
    STRENGTH_CARDINALITY,
    STRENGTH_NONE,
    STRENGTH_VALUE,
    STRENGTH_VALUE_AND_CARDINALITY,
    build_coverage_map,
    check_artifacts,
    oracle_reference_independence,
    oracle_strength_and_scale,
    oracle_surface_provenance,
    render_markdown,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    return build_coverage_map()


def test_checked_in_artifacts_are_current(rows):
    problems = check_artifacts(rows)
    assert not problems, "oracle coverage map is stale:\n" + "\n".join(problems)


def test_check_artifacts_fix_hint_names_exact_regen_command(rows, monkeypatch):
    from _project.scripts import generate_oracle_coverage_map as gocm

    missing_path = gocm.ARTIFACT_DIR / "does-not-exist-guard-messages-test.md"
    monkeypatch.setattr(gocm, "MARKDOWN_ARTIFACT", missing_path)

    problems = check_artifacts(rows)
    assert problems, "expected a missing-artifact problem to be reported"
    hint_problems = [p for p in problems if "does-not-exist-guard-messages-test.md" in p]
    assert hint_problems, problems
    assert any("make oracle-coverage-map" in p and "make guards-fix" in p for p in hint_problems)


def test_every_shipped_benchmark_is_classified(rows):
    from benchbox.core.benchmark_registry import list_benchmark_ids

    mapped = {r["benchmark"] for r in rows}
    assert mapped == set(list_benchmark_ids()), "coverage map and registry disagree on benchmark set"
    assert len(rows) == len(mapped), "duplicate benchmark rows in coverage map"
    for r in rows:
        assert r["primary_oracle"], f"{r['benchmark']} has no primary_oracle classification"
        assert (r["primary_oracle"] == ORACLE_NONE) == (not r["guarded"]), (
            f"{r['benchmark']} guarded/primary_oracle disagree"
        )


def test_known_oracles_stay_classified(rows):
    by_id = {r["benchmark"]: r for r in rows}
    assert ORACLE_EXPECTED_RESULTS in by_id["tpch"]["oracles"]
    assert ORACLE_EXPECTED_RESULTS in by_id["tpcds"]["oracles"]
    assert ORACLE_VARIANT_EQUIVALENCE in by_id["tpchavoc"]["oracles"]
    assert ORACLE_CROSS_SURFACE in by_id["ssb"]["oracles"]


def test_cross_surface_applicable_implies_dual_surface_and_unguarded(rows):
    for r in rows:
        if r["cross_surface_applicable"]:
            assert r["dual_surface"], f"{r['benchmark']} flagged cross-surface but is single-surface"
            assert not r["guarded"], f"{r['benchmark']} flagged cross-surface but already guarded"


def test_strength_and_scale_columns_present(rows):
    for r in rows:
        assert "strength" in r and r["strength"], f"{r['benchmark']} missing strength"
        assert "scale" in r and r["scale"], f"{r['benchmark']} missing scale"
        if not r["guarded"]:
            assert r["strength"] == STRENGTH_NONE, f"{r['benchmark']} unguarded but strength={r['strength']}"


def test_known_oracle_strength_and_scale_truth(rows):
    by_id = {r["benchmark"]: r for r in rows}

    assert by_id["tpch"]["strength"] == STRENGTH_VALUE_AND_CARDINALITY
    assert by_id["tpch"]["scale"] == "SF=1"
    assert by_id["tpcds"]["strength"] == STRENGTH_CARDINALITY
    assert by_id["tpcds"]["scale"] == "SF=1, 0.01, 0.1"

    for benchmark in ("ssb", "amplab", "coffeeshop", "tpchavoc"):
        assert by_id[benchmark]["strength"] == STRENGTH_VALUE, f"{benchmark} should be value-level"
        assert by_id[benchmark]["scale"].startswith("SF="), f"{benchmark} missing bounded scale"


def test_expected_results_strength_is_derived_not_hardcoded(monkeypatch):
    import _project.scripts.generate_oracle_coverage_map as gen

    monkeypatch.setattr(gen, "_expected_results_has_value_digests", lambda _b: True)
    strength, scale = oracle_strength_and_scale(ORACLE_EXPECTED_RESULTS, "tpch")
    assert strength == STRENGTH_VALUE_AND_CARDINALITY
    assert scale == "SF=1"

    monkeypatch.setattr(gen, "_expected_results_has_value_digests", lambda _b: False)
    strength, _ = oracle_strength_and_scale(ORACLE_EXPECTED_RESULTS, "tpch")
    assert strength == STRENGTH_CARDINALITY


def test_value_level_oracle_scale_is_read_live(rows):
    from benchbox.core.equivalence.cross_surface import EQUIVALENCE_SCALE

    by_id = {r["benchmark"]: r for r in rows}
    assert by_id["ssb"]["scale"] == f"SF={EQUIVALENCE_SCALE}"


def test_independence_column_present(rows):
    for r in rows:
        assert "independence" in r and r["independence"], f"{r['benchmark']} missing independence"
        assert "independence_rationale" in r and r["independence_rationale"], (
            f"{r['benchmark']} missing independence rationale"
        )
        if not r["guarded"]:
            assert r["independence"] == INDEPENDENCE_NONE, (
                f"{r['benchmark']} unguarded but independence={r['independence']}"
            )
            assert r["independence_rationale"] == INDEPENDENCE_NONE, (
                f"{r['benchmark']} unguarded but rationale={r['independence_rationale']}"
            )


def test_known_oracle_independence_truth(rows):
    by_id = {r["benchmark"]: r for r in rows}

    assert by_id["tpch"]["independence"] == INDEPENDENCE_SELF
    assert by_id["tpcds"]["independence"] == INDEPENDENCE_SEMI

    for benchmark in ("ssb", "joinorder_synthetic", "clickbench", "read_primitives", "amplab", "coffeeshop", "h2odb"):
        assert by_id[benchmark]["independence"] == INDEPENDENCE_SELF, (
            f"{benchmark} cross-surface reference is benchbox's own DataFrame code, not an external authority"
        )

    assert by_id["tpchavoc"]["independence"] == INDEPENDENCE_SELF

    provenance_labels = {PROVENANCE_SHARED_SPEC, PROVENANCE_MIXED, PROVENANCE_SEPARATE}
    leaked = {r["benchmark"] for r in rows if r["independence"] in provenance_labels}
    assert not leaked, f"surface-provenance labels leaked into the Independence column: {sorted(leaked)}"


def test_surface_provenance_column_present(rows):
    for r in rows:
        assert "surface_provenance" in r and r["surface_provenance"], f"{r['benchmark']} missing surface_provenance"
        assert "surface_provenance_rationale" in r and r["surface_provenance_rationale"], (
            f"{r['benchmark']} missing surface_provenance_rationale"
        )
        from benchbox.core.equivalence.cross_surface import GATES, STAGED_GATES

        has_gate = r["benchmark"] in GATES or r["benchmark"] in STAGED_GATES
        if r["primary_oracle"] != ORACLE_CROSS_SURFACE and not has_gate:
            assert r["surface_provenance"] == PROVENANCE_NONE, (
                f"{r['benchmark']} is not cross-surface but discloses provenance={r['surface_provenance']}"
            )
            assert r["surface_provenance_rationale"] == PROVENANCE_NONE, (
                f"{r['benchmark']} is not cross-surface but carries a provenance rationale"
            )


def test_known_surface_provenance_truth(rows):
    by_id = {r["benchmark"]: r for r in rows}

    for benchmark in ("ssb", "joinorder_synthetic"):
        assert by_id[benchmark]["surface_provenance"] == PROVENANCE_SHARED_SPEC
    for benchmark in ("clickbench", "read_primitives"):
        assert by_id[benchmark]["surface_provenance"] == PROVENANCE_MIXED
    for benchmark in ("amplab", "coffeeshop", "h2odb"):
        assert by_id[benchmark]["surface_provenance"] == PROVENANCE_SEPARATE
    assert "separately handwritten" in by_id["coffeeshop"]["surface_provenance_rationale"]


def test_surface_provenance_is_read_live_from_gate_metadata(rows):
    from benchbox.core.equivalence.cross_surface import (
        GATES,
        STAGED_GATES,
        SURFACE_INDEPENDENCE_MIXED,
        SURFACE_INDEPENDENCE_SEPARATE,
        SURFACE_INDEPENDENCE_SHARED_SPEC,
    )

    assert PROVENANCE_SHARED_SPEC == SURFACE_INDEPENDENCE_SHARED_SPEC
    assert PROVENANCE_MIXED == SURFACE_INDEPENDENCE_MIXED
    assert PROVENANCE_SEPARATE == SURFACE_INDEPENDENCE_SEPARATE

    by_id = {r["benchmark"]: r for r in rows}
    for benchmark_id, gate in {**GATES, **STAGED_GATES}.items():
        assert by_id[benchmark_id]["surface_provenance"] == gate.surface_independence, (
            f"{benchmark_id} provenance is not read live from CrossSurfaceGate metadata"
        )
        assert by_id[benchmark_id]["surface_provenance_rationale"] == gate.surface_independence_rationale


def test_provenance_never_changes_independence(rows):
    by_id = {r["benchmark"]: r for r in rows}
    spread = {by_id[b]["surface_provenance"] for b in ("ssb", "clickbench", "coffeeshop")}
    assert spread == {PROVENANCE_SHARED_SPEC, PROVENANCE_MIXED, PROVENANCE_SEPARATE}
    assert {by_id[b]["independence"] for b in ("ssb", "clickbench", "coffeeshop")} == {INDEPENDENCE_SELF}

    assert oracle_reference_independence(ORACLE_CROSS_SURFACE, STRENGTH_VALUE) == INDEPENDENCE_SELF
    assert oracle_reference_independence(ORACLE_VARIANT_EQUIVALENCE, STRENGTH_VALUE) == INDEPENDENCE_SELF
    assert oracle_reference_independence(ORACLE_CROSS_SURFACE_VARIANT, STRENGTH_VALUE) == INDEPENDENCE_SELF
    assert oracle_surface_provenance(ORACLE_VARIANT_EQUIVALENCE, "tpchavoc") == (PROVENANCE_NONE, PROVENANCE_NONE)
    assert oracle_surface_provenance(ORACLE_CROSS_SURFACE, "coffeeshop")[0] == PROVENANCE_SEPARATE


def test_staged_gate_scale_is_read_from_its_own_metadata(monkeypatch):
    from benchbox.core.equivalence.cross_surface import (
        EQUIVALENCE_SCALE,
        GATES,
        STAGED_GATES,
        SURFACE_INDEPENDENCE_MIXED,
    )

    staged_scale = EQUIVALENCE_SCALE / 100
    assert staged_scale != EQUIVALENCE_SCALE, "probe scale must differ from the shared default"

    probe_id = "tpcdi"
    assert probe_id not in GATES, f"{probe_id} gained an enforced gate; pick another staged probe"
    template = GATES["coffeeshop"]
    staged = replace(
        template,
        name=probe_id,
        surface_independence=SURFACE_INDEPENDENCE_MIXED,
        scale_factor=staged_scale,
    )
    monkeypatch.setitem(STAGED_GATES, probe_id, staged)

    row = {r["benchmark"]: r for r in build_coverage_map()}[probe_id]
    assert row["primary_oracle"] == ORACLE_CROSS_SURFACE
    assert row["cross_surface_enforced"] is False, "a STAGED gate must not report as CI-enforced"
    assert row["scale"] == f"SF={staged_scale}", (
        f"staged gate scale must be read from its own metadata, got {row['scale']!r}"
    )


def test_unknown_surface_provenance_label_is_rejected(monkeypatch):
    from benchbox.core.equivalence.cross_surface import GATES

    bogus = replace(GATES["coffeeshop"], surface_independence="transpiled-from-sql")
    monkeypatch.setitem(GATES, "coffeeshop", bogus)

    with pytest.raises(ValueError, match="unknown surface-provenance label"):
        oracle_surface_provenance(ORACLE_CROSS_SURFACE, "coffeeshop")


def test_markdown_renders_independence_and_provenance_as_distinct_columns(rows):
    markdown = render_markdown(rows)
    header = next(line for line in markdown.splitlines() if line.startswith("| Benchmark |"))
    columns = [c.strip() for c in header.strip().strip("|").split("|")]
    assert "Independence" in columns
    assert "Surface provenance" in columns
    assert columns.index("Independence") != columns.index("Surface provenance")

    coffeeshop = next(line for line in markdown.splitlines() if line.startswith("| coffeeshop |"))
    cells = [c.strip() for c in coffeeshop.strip().strip("|").split("|")]
    assert cells[columns.index("Independence")] == INDEPENDENCE_SELF
    assert cells[columns.index("Surface provenance")] == PROVENANCE_SEPARATE

    assert "Surface-provenance disclosure:" in markdown
    assert "not** as independence" in markdown


def test_independence_is_derived_from_value_digest_signal(monkeypatch):
    import _project.scripts.generate_oracle_coverage_map as gen

    monkeypatch.setattr(gen, "_expected_results_has_value_digests", lambda _b: True)
    flipped_on = {r["benchmark"]: r for r in gen.build_coverage_map()}
    assert flipped_on["tpch"]["strength"] == STRENGTH_VALUE_AND_CARDINALITY
    assert flipped_on["tpch"]["independence"] == INDEPENDENCE_SELF

    monkeypatch.setattr(gen, "_expected_results_has_value_digests", lambda _b: False)
    flipped_off = {r["benchmark"]: r for r in gen.build_coverage_map()}
    assert flipped_off["tpch"]["strength"] == STRENGTH_CARDINALITY
    assert flipped_off["tpch"]["independence"] == INDEPENDENCE_SEMI

    assert oracle_reference_independence(ORACLE_CROSS_SURFACE, STRENGTH_VALUE) == INDEPENDENCE_SELF
    assert oracle_reference_independence(ORACLE_VARIANT_EQUIVALENCE, STRENGTH_VALUE) == INDEPENDENCE_SELF


def test_tpch_row_discloses_sf1_value_blindness(rows):
    markdown = render_markdown(rows)
    tpch_line = next(line for line in markdown.splitlines() if line.startswith("| tpch |"))
    cells = [c.strip() for c in tpch_line.strip().strip("|").split("|")]
    strength_cell = cells[3]
    assert "UNGUARDED above SF=1" in strength_cell, (
        f"tpch Strength cell must disclose SF>1 value-blindness; got {strength_cell!r}"
    )
    tpcds_line = next(line for line in markdown.splitlines() if line.startswith("| tpcds |"))
    assert "UNGUARDED above SF=1" not in tpcds_line


def test_independence_does_not_overload_strength(rows):
    by_id = {r["benchmark"]: r for r in rows}
    assert by_id["tpchavoc"]["strength"] == STRENGTH_VALUE
    assert by_id["tpch"]["strength"] == STRENGTH_VALUE_AND_CARDINALITY
    assert by_id["tpchavoc"]["independence"] == by_id["tpch"]["independence"] == INDEPENDENCE_SELF


def test_cross_surface_enforced_distinguishes_registered_from_verified_green(rows):
    from benchbox.core.equivalence.cross_surface import GATES, STAGED_GATES

    by_id = {r["benchmark"]: r for r in rows}
    for benchmark_id in GATES:
        assert by_id[benchmark_id]["cross_surface_enforced"] is True, (
            f"{benchmark_id} is in enforced GATES but not reported as CI-enforced"
        )
    for benchmark_id in STAGED_GATES:
        assert by_id[benchmark_id]["cross_surface_enforced"] is False, (
            f"{benchmark_id} is STAGED (not CI-enforced) but reported as enforced"
        )
    assert {"ssb", "coffeeshop", "amplab", "clickbench", "joinorder_synthetic"} <= set(GATES), (
        "expected enforced cross-surface gate set changed; update the coverage-map honesty test"
    )
    for r in rows:
        if ORACLE_CROSS_SURFACE not in r["oracles"]:
            assert r["cross_surface_enforced"] is None, (
                f"{r['benchmark']} has no cross-surface gate but carries an enforcement flag"
            )
