#!/usr/bin/env python3

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ORACLE_EXPECTED_RESULTS = "expected-results"
ORACLE_VARIANT_EQUIVALENCE = "variant-equivalence"
ORACLE_CROSS_SURFACE_VARIANT = "cross-surface-variant"
ORACLE_CROSS_SURFACE = "cross-surface"
ORACLE_NONE = "NONE"

_ORACLE_PRIORITY = [
    ORACLE_EXPECTED_RESULTS,
    ORACLE_VARIANT_EQUIVALENCE,
    ORACLE_CROSS_SURFACE_VARIANT,
    ORACLE_CROSS_SURFACE,
]

STRENGTH_VALUE = "value-level"
STRENGTH_CARDINALITY = "cardinality-only"
STRENGTH_VALUE_AND_CARDINALITY = "value+cardinality"
STRENGTH_NONE = "—"

INDEPENDENCE_INDEPENDENT = "independent"
INDEPENDENCE_SEMI = "semi-independent"
INDEPENDENCE_SELF = "self-referential"
INDEPENDENCE_NONE = "—"

PROVENANCE_SHARED_SPEC = "shared-spec"
PROVENANCE_MIXED = "mixed-provenance"
PROVENANCE_SEPARATE = "separate-handwritten"
PROVENANCE_NONE = "—"

_KNOWN_PROVENANCE_LABELS = frozenset({PROVENANCE_SHARED_SPEC, PROVENANCE_MIXED, PROVENANCE_SEPARATE})

SCALE_NONE = "—"

_REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = _REPO_ROOT / "_project" / "analysis"
JSON_ARTIFACT = ARTIFACT_DIR / "oracle-coverage-map.json"
MARKDOWN_ARTIFACT = ARTIFACT_DIR / "oracle-coverage-map.md"


CLI_DESCRIPTION = (
    "Generate the benchmark correctness-oracle coverage map.\n"
    "\n"
    'The map is the authoritative, *generated* answer to "which correctness oracle, if\n'
    'any, guards each shipped benchmark?" It is derived from live sources so it cannot\n'
    "drift from reality:\n"
    "\n"
    "  - the benchmark registry (``benchbox.core.benchmark_registry``) for the set of\n"
    "    shipped benchmarks and their surfaces (SQL / DataFrame);\n"
    "  - the expected-results provider registry\n"
    "    (``benchbox.core.expected_results.registry``) for stored answer keys;\n"
    "  - the cross-surface gate registry\n"
    "    (``benchbox.core.equivalence.cross_surface.GATES``) for SQL<->DataFrame gates;\n"
    "  - module presence for the bespoke TPC-Havoc variant / variant-DataFrame gates.\n"
    "\n"
    "Run ``python _project/scripts/generate_oracle_coverage_map.py`` to (re)write the\n"
    "checked-in artifacts under ``_project/analysis/``. Run with ``--check`` to fail if\n"
    "those artifacts are stale (used by ``tests/unit/test_oracle_coverage_map.py``).\n"
    "\n"
    "This script owns w0 (the matrix) and feeds w3 (drift/UNGUARDED visibility) of the\n"
    "``benchmark-correctness-oracle-coverage-map`` TODO. It deliberately does NOT build\n"
    "per-benchmark oracles (w1/w2); it makes the gap legible and dispatchable.\n"
)


def _module_exists(dotted: str) -> bool:
    try:
        return importlib.util.find_spec(dotted) is not None
    except ModuleNotFoundError:
        return False


def classify_oracles(
    benchmark_id: str,
    *,
    expected_results_providers: set[str],
    cross_surface_gates: set[str],
    staged_cross_surface_gates: set[str],
) -> list[str]:
    oracles: list[str] = []
    if benchmark_id in expected_results_providers:
        oracles.append(ORACLE_EXPECTED_RESULTS)
    if benchmark_id == "tpchavoc":
        if _module_exists("benchbox.core.tpchavoc.equivalence"):
            oracles.append(ORACLE_VARIANT_EQUIVALENCE)
        if _module_exists("benchbox.core.tpchavoc.dataframe_equivalence"):
            oracles.append(ORACLE_CROSS_SURFACE_VARIANT)
    if benchmark_id in cross_surface_gates or benchmark_id in staged_cross_surface_gates:
        oracles.append(ORACLE_CROSS_SURFACE)
    return oracles


_PRIMARY_ORACLE_OVERRIDES = {"ssb": ORACLE_CROSS_SURFACE}


def primary_oracle(oracles: list[str], benchmark_id: str = "") -> str:
    override = _PRIMARY_ORACLE_OVERRIDES.get(benchmark_id)
    if override is not None and override in oracles:
        return override
    for kind in _ORACLE_PRIORITY:
        if kind in oracles:
            return kind
    return ORACLE_NONE


def _expected_results_has_value_digests(benchmark_id: str) -> bool:
    try:
        if benchmark_id == "tpch":
            from benchbox.core.expected_results.loader import load_tpch_value_digests

            return bool(load_tpch_value_digests(1.0))
    except Exception:  # pragma: no cover
        return False
    return False


def _bounded_value_gate_scale(benchmark_id: str) -> str:
    from benchbox.core.equivalence.cross_surface import EQUIVALENCE_SCALE, GATES, STAGED_GATES

    gate = GATES.get(benchmark_id) or STAGED_GATES.get(benchmark_id)
    scale = gate.scale_factor if gate is not None else EQUIVALENCE_SCALE
    return f"SF={scale}"


def oracle_strength_and_scale(primary: str, benchmark_id: str) -> tuple[str, str]:
    if primary == ORACLE_EXPECTED_RESULTS:
        strength = (
            STRENGTH_VALUE_AND_CARDINALITY
            if _expected_results_has_value_digests(benchmark_id)
            else STRENGTH_CARDINALITY
        )
        if benchmark_id == "tpcds":
            from benchbox.core.expected_results.tpcds_results import TPCDS_SUBSCALE_ROW_COUNTS

            if TPCDS_SUBSCALE_ROW_COUNTS:
                subscales = [f"{scale:g}" for scale in sorted(TPCDS_SUBSCALE_ROW_COUNTS)]
                return strength, f"SF=1, {', '.join(subscales)}"
        return strength, "SF=1"
    if primary in (ORACLE_VARIANT_EQUIVALENCE, ORACLE_CROSS_SURFACE_VARIANT, ORACLE_CROSS_SURFACE):
        return STRENGTH_VALUE, _bounded_value_gate_scale(benchmark_id)
    return STRENGTH_NONE, SCALE_NONE


def oracle_reference_independence(primary: str, strength: str) -> str:
    if primary in (ORACLE_CROSS_SURFACE, ORACLE_VARIANT_EQUIVALENCE, ORACLE_CROSS_SURFACE_VARIANT):
        return INDEPENDENCE_SELF
    if primary == ORACLE_EXPECTED_RESULTS:
        if strength == STRENGTH_VALUE_AND_CARDINALITY:
            return INDEPENDENCE_SELF
        return INDEPENDENCE_SEMI
    return INDEPENDENCE_NONE


def oracle_independence_and_rationale(primary: str, strength: str, benchmark_id: str | None = None) -> tuple[str, str]:
    independence = oracle_reference_independence(primary, strength)
    if independence == INDEPENDENCE_SELF:
        if primary == ORACLE_CROSS_SURFACE:
            return (
                independence,
                "Both compared surfaces are benchbox's own implementations; see Surface provenance "
                "for how far apart they were authored.",
            )
        if primary in (ORACLE_VARIANT_EQUIVALENCE, ORACLE_CROSS_SURFACE_VARIANT):
            return (
                independence,
                "Reference is another benchbox surface of the same benchmark family, not an external authority.",
            )
        return (
            independence,
            "Reference is a frozen benchbox snapshot, not an external authority.",
        )
    if independence == INDEPENDENCE_SEMI:
        if benchmark_id == "tpcds":
            return (
                independence,
                "External TPC answer sets provide SF=1 row-count authority; subscale row counts are verified against cross-engine (DuckDB/DataFusion) reference counts.",
            )
        return independence, "External TPC answer sets provide row-count authority only; result values are not checked."
    if independence == INDEPENDENCE_INDEPENDENT:
        return independence, "Full result values are checked against an external authority."
    return independence, INDEPENDENCE_NONE


def oracle_surface_provenance(primary: str, benchmark_id: str) -> tuple[str, str]:
    from benchbox.core.equivalence.cross_surface import GATES, STAGED_GATES

    gate = GATES.get(benchmark_id) or STAGED_GATES.get(benchmark_id)
    if gate is None:
        return PROVENANCE_NONE, PROVENANCE_NONE
    if gate.surface_independence not in _KNOWN_PROVENANCE_LABELS:
        raise ValueError(
            f"{benchmark_id}: unknown surface-provenance label {gate.surface_independence!r}. "
            f"Known labels: {sorted(_KNOWN_PROVENANCE_LABELS)}. Add a PROVENANCE_* constant in "
            "this module AND describe the new label in the Surface-provenance disclosure prose "
            "in render_markdown(), otherwise the map renders a label its own legend never defines."
        )
    return gate.surface_independence, gate.surface_independence_rationale


def _surfaces(metadata: dict[str, Any]) -> tuple[bool, bool]:
    has_sql = bool(metadata.get("num_queries") or 0)
    has_dataframe = bool(metadata.get("supports_dataframe", False))
    return has_sql, has_dataframe


def build_coverage_map() -> list[dict[str, Any]]:
    from benchbox.core.benchmark_registry import get_benchmark_metadata, list_benchmark_ids
    from benchbox.core.equivalence.cross_surface import GATES, STAGED_GATES
    from benchbox.core.expected_results.registry import get_registry

    expected_results_providers = set(get_registry().list_available_benchmarks())
    cross_surface_gates = set(GATES.keys())
    staged_cross_surface_gates = set(STAGED_GATES.keys())

    rows: list[dict[str, Any]] = []
    for benchmark_id in sorted(list_benchmark_ids()):
        metadata = get_benchmark_metadata(benchmark_id) or {}
        has_sql, has_dataframe = _surfaces(metadata)
        surfaces = [s for s, present in (("sql", has_sql), ("dataframe", has_dataframe)) if present]
        oracles = classify_oracles(
            benchmark_id,
            expected_results_providers=expected_results_providers,
            cross_surface_gates=cross_surface_gates,
            staged_cross_surface_gates=staged_cross_surface_gates,
        )
        primary = primary_oracle(oracles, benchmark_id)
        strength, scale = oracle_strength_and_scale(primary, benchmark_id)
        independence, independence_rationale = oracle_independence_and_rationale(primary, strength, benchmark_id)
        surface_provenance, surface_provenance_rationale = oracle_surface_provenance(primary, benchmark_id)
        dual_surface = has_sql and has_dataframe
        if ORACLE_CROSS_SURFACE in oracles:
            cross_surface_enforced: bool | None = benchmark_id in cross_surface_gates
        else:
            cross_surface_enforced = None
        rows.append(
            {
                "benchmark": benchmark_id,
                "surfaces": surfaces,
                "dual_surface": dual_surface,
                "oracles": oracles,
                "primary_oracle": primary,
                "strength": strength,
                "scale": scale,
                "independence": independence,
                "independence_rationale": independence_rationale,
                "surface_provenance": surface_provenance,
                "surface_provenance_rationale": surface_provenance_rationale,
                "guarded": primary != ORACLE_NONE,
                "cross_surface_enforced": cross_surface_enforced,
                "cross_surface_applicable": dual_surface and primary == ORACLE_NONE,
            }
        )
    return rows


def _enforcement_label(row: dict[str, Any]) -> str:
    enforced = row.get("cross_surface_enforced")
    if enforced is True:
        return "enforced (CI-blocking)"
    if enforced is False:
        return "staged (NOT CI-enforced)"
    return "—"


_SF1_VALUE_DISCLOSURE = " (SF=1 only; values UNGUARDED above SF=1)"


def _strength_cell(row: dict[str, Any]) -> str:
    if row["primary_oracle"] == ORACLE_EXPECTED_RESULTS and row["strength"] == STRENGTH_VALUE_AND_CARDINALITY:
        return row["strength"] + _SF1_VALUE_DISCLOSURE
    return row["strength"]


def render_markdown(rows: list[dict[str, Any]]) -> str:
    guarded = [r for r in rows if r["guarded"]]
    unguarded = [r for r in rows if not r["guarded"]]
    dispatchable = [r for r in unguarded if r["cross_surface_applicable"]]
    single_surface_gap = [r for r in unguarded if not r["cross_surface_applicable"]]
    enforced_cross_surface = [r for r in rows if r.get("cross_surface_enforced") is True]
    staged_cross_surface = [r for r in rows if r.get("cross_surface_enforced") is False]

    lines: list[str] = []
    lines.append("# Benchmark correctness-oracle coverage map")
    lines.append("")
    lines.append(
        "**Generated** by `_project/scripts/generate_oracle_coverage_map.py` from the "
        "benchmark registry, the expected-results provider registry, and the "
        "equivalence-gate registries. Do not edit by hand — run the generator and "
        "commit. `tests/unit/test_oracle_coverage_map.py` fails if this drifts."
    )
    lines.append("")
    lines.append(
        "**What an oracle here means.** A benchmark is listed as *guarded* when an "
        "oracle is REGISTERED for it — it is **not** a claim that the oracle is "
        "currently green. The distinction matters most for cross-surface gates: only "
        "a gate in the enforced `GATES` registry is run as a CI-blocking step (so it "
        "is green or CI fails); a gate in `STAGED_GATES` is registered but not run "
        "in CI, so its registration proves nothing about correctness. The "
        "**Enforced** column reports this *cross-surface* enforcement status: "
        "`enforced (CI-blocking)`, `staged (NOT CI-enforced)`, or `—` for any "
        "benchmark that is not cross-surface-gated. A `—` therefore says nothing "
        "about whether a *non*-cross-surface oracle is enforced: the expected-results "
        "(tpch, tpcds) and TPC-Havoc variant oracles are CI-enforced via their own "
        "test suites despite showing `—` here."
    )
    lines.append("")
    lines.append(
        f"**Summary:** {len(rows)} shipped benchmarks — {len(guarded)} guarded "
        f"(oracle registered), {len(unguarded)} UNGUARDED ({len(dispatchable)} "
        f"reachable by the cross-surface gate, {len(single_surface_gap)} "
        f"single-surface needing a fallback oracle). Cross-surface gates: "
        f"{len(enforced_cross_surface)} CI-enforced, {len(staged_cross_surface)} "
        f"staged (not CI-enforced)."
    )
    lines.append("")
    lines.append(
        "**Strength + scale disclosure:** a guarded cell is not a uniform guarantee. "
        "The **Strength** column says what the oracle proves — `value-level` (full "
        "result values compared) vs `cardinality-only` (row counts only) vs "
        "`value+cardinality` (both) — and the **Scale** column says at which scale it "
        "actually holds. Both are derived from live sources (the provider's stored "
        "answers/digests and the equivalence gate's bounded scale), not hand-labelled. "
        "The `tpch` expected-results oracle exists only at SF=1 and the `tpcds` "
        "oracle exists at SF=1 plus recorded subscale row counts, so "
        "`tpch`/`tpcds` values are unguarded above SF=1 — the tpch "
        "**Strength** cell states this inline so the row is self-contained."
    )
    lines.append("")
    lines.append(
        "**Reference-independence disclosure:** the **Independence** column answers one "
        "question — is the oracle's reference an authority *outside* benchbox? It is "
        "orthogonal to Strength (a `value-level` oracle can still be weak). "
        "`semi-independent` means an external authority is consulted for part of the "
        "claim only: the published TPC answer sets pin `tpcds` row counts, never its "
        "values. `self-referential` means benchbox is compared against benchbox — "
        "either two of its own surfaces (every cross-surface and variant gate) or a "
        "frozen snapshot of its own past output (the `tpch` value digests). A "
        "self-referential oracle catches drift and regressions; it cannot catch a "
        "mistake both sides share. The **Independence rationale** column gives the "
        "per-row reason."
    )
    lines.append("")
    lines.append(
        "**Surface-provenance disclosure:** the **Surface provenance** column is a "
        "SEPARATE axis that grades, for a cross-surface gate, how far apart the two "
        "compared surfaces were *authored*: `shared-spec` (both DataFrame backends "
        "generated/maintained from one shared spec), `mixed-provenance` (some cells "
        "shared/generated, some bespoke), or `separate-handwritten` (the DataFrame "
        "families are separately written implementations). It is read per-gate from "
        "the live `CrossSurfaceGate` metadata and is `—` for every non-cross-surface "
        "row, where the axis does not apply. Read it as *how much internal signal the "
        "gate carries* — separately handwritten surfaces catch more than two surfaces "
        "generated from one spec — and **not** as independence: a "
        "`separate-handwritten` gate is still benchbox checking its own DataFrame code "
        "against its own SQL, which is why its Independence stays `self-referential`. "
        "The **Provenance rationale** column gives the per-gate reason."
    )
    lines.append("")
    lines.append(
        "| Benchmark | Surfaces | Oracle | Strength | Scale | Independence | Independence rationale | "
        "Surface provenance | Provenance rationale | Enforced | Notes |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in rows:
        surfaces = "+".join(r["surfaces"]) or "—"
        oracle = r["primary_oracle"]
        if r["guarded"]:
            note = ", ".join(r["oracles"])
            if r.get("cross_surface_enforced") is False:
                note += " (registered, NOT CI-enforced)"
        elif r["cross_surface_applicable"]:
            note = "dual-surface → dispatch to cross-surface gate (w1)"
        else:
            note = "single-surface → needs fallback oracle (w2)"
        enforced = _enforcement_label(r)
        lines.append(
            f"| {r['benchmark']} | {surfaces} | {oracle} | {_strength_cell(r)} | {r['scale']} | "
            f"{r['independence']} | {r['independence_rationale']} | "
            f"{r['surface_provenance']} | {r['surface_provenance_rationale']} | {enforced} | {note} |"
        )
    lines.append("")
    lines.append("## UNGUARDED benchmarks")
    lines.append("")
    lines.append(
        "These ship with no automated correctness oracle today. Dual-surface ones "
        "are dispatched to `benchmark-cross-surface-equivalence-gate` (w1); "
        "single-surface ones need a per-benchmark fallback — differential vs a "
        "second engine, a small curated expected-results subset, or a documented "
        "structural invariant (w2)."
    )
    lines.append("")
    lines.append(
        "> Caveat (w2 oracle choice): write/DML/nondeterministic benchmarks "
        "(`write_primitives`, `transaction_primitives`, `metadata_primitives`, "
        "`tpcdi`) are listed as dual-surface, but their two surfaces may not be "
        "result-comparable; prefer structural-invariant oracles (row counts, "
        "post-state assertions) over cross-surface equality for those."
    )
    lines.append("")
    lines.append(
        "- Dual-surface (cross-surface candidates): " + (", ".join(r["benchmark"] for r in dispatchable) or "none")
    )
    lines.append(
        "- Single-surface (fallback-oracle needed): "
        + (", ".join(r["benchmark"] for r in single_surface_gap) or "none")
    )
    lines.append("")
    return "\n".join(lines)


def render_json(rows: list[dict[str, Any]]) -> str:
    return json.dumps({"benchmarks": rows}, indent=2, sort_keys=True) + "\n"


_PROVENANCE_START = "<!-- PROVENANCE"
_PROVENANCE_END = "-->"


def _content_revision(rows: list[dict[str, Any]]) -> str:
    import hashlib

    body = render_markdown(rows) + render_json(rows)
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _provenance_header(rows: list[dict[str, Any]]) -> str:
    import datetime

    generated = datetime.date.today().isoformat()
    return (
        f"{_PROVENANCE_START}\n"
        f"generated: {generated}\n"
        f"content-revision: {_content_revision(rows)}\n"
        "This header is drift-IGNORED by `--check` (see _strip_provenance). content-revision\n"
        "is a hash of the generated body (markdown + json), NOT a git SHA: a PR-branch SHA is\n"
        "orphaned by squash-merge, so to verify this artifact, regenerate it with\n"
        "`make oracle-coverage-map` on develop and confirm the body matches (the drift check\n"
        "does this). Do not rely on this header for diffs.\n"
        f"{_PROVENANCE_END}\n"
    )


def _strip_provenance(text: str) -> str:
    if not text.startswith(_PROVENANCE_START):
        return text
    end = text.find(_PROVENANCE_END)
    if end == -1:
        return text
    rest = text[end + len(_PROVENANCE_END) :]
    return rest.lstrip("\n")


def write_artifacts(rows: list[dict[str, Any]]) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_ARTIFACT.write_text(render_json(rows), encoding="utf-8")
    MARKDOWN_ARTIFACT.write_text(_provenance_header(rows) + render_markdown(rows), encoding="utf-8")


def check_artifacts(rows: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    checks = (
        (JSON_ARTIFACT, render_json(rows), False),
        (MARKDOWN_ARTIFACT, render_markdown(rows), True),
    )
    fix_hint = "run `make oracle-coverage-map` (or `make guards-fix`) and commit"
    for path, expected, strip in checks:
        if not path.exists():
            problems.append(f"missing artifact: {path.relative_to(_REPO_ROOT)} ({fix_hint})")
            continue
        actual = path.read_text(encoding="utf-8")
        if strip:
            actual = _strip_provenance(actual)
        if actual != expected:
            problems.append(f"stale artifact: {path.relative_to(_REPO_ROOT)} ({fix_hint})")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the checked-in artifacts are current; non-zero exit if stale.",
    )
    args = parser.parse_args(argv)

    rows = build_coverage_map()
    if args.check:
        problems = check_artifacts(rows)
        if problems:
            print("oracle coverage map is out of date:")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print("oracle coverage map is up to date.")
        return 0

    write_artifacts(rows)
    unguarded = [r["benchmark"] for r in rows if not r["guarded"]]
    print(f"Wrote {MARKDOWN_ARTIFACT.relative_to(_REPO_ROOT)} and {JSON_ARTIFACT.relative_to(_REPO_ROOT)}")
    print(f"{len(rows)} benchmarks, {len(unguarded)} UNGUARDED: {', '.join(unguarded)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
