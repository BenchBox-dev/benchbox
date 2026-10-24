#!/usr/bin/env python3

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

ARTIFACT = _REPO_ROOT / "_project" / "analysis" / "cross-surface-applicability.md"

_INSTANTIATE_SCALES = (0.01, 1.0)

GATEABLE = "gateable"
CANDIDATE_UNVERIFIED = "candidate-unverified"
NOT_CHEAPLY_GATEABLE = "not-cheaply-gateable"
NO_DF_QUERY_SURFACE = "no-df-query-surface"
BLOCKED = "blocked"
ABANDONED = "abandoned"

_ABANDONED_CORRESPONDENCE: dict[str, str] = {
    "tpcds_obt": (
        "DataFrame obt_* queries are OBT-native analytics while SQL ids denote "
        "TPC-DS queries; no correspondence exists between the two sides "
        "(see tests/unit/core/tpcds_obt/test_tpcds_obt_id_mapping_decision.py)"
    ),
}

_GATEABLE_STATUSES = frozenset({GATEABLE})


CLI_DESCRIPTION = (
    "Cross-surface applicability sweep (benchmark-cross-surface-equivalence-gate w2).\n"
    "\n"
    "The oracle coverage map flags a benchmark as a cross-surface candidate when it is\n"
    "dual-surface (ships SQL queries AND has ``supports_dataframe=True``) and currently\n"
    "unguarded. But ``supports_dataframe`` is a *loading* capability flag: it does NOT\n"
    "mean the benchmark ships comparable DataFrame *query* implementations. The\n"
    "cross-surface gate compares a query's SQL result to its DataFrame result, so it is\n"
    "only applicable to benchmarks that ship DataFrame *queries*.\n"
    "\n"
    "Signal: each benchmark that ships a DataFrame query surface exposes a\n"
    "``QueryRegistry`` (a ``<BENCH>_DATAFRAME_QUERIES`` instance) in its\n"
    "``benchbox.core.<bench>.dataframe_queries`` module/package -- this is the registry\n"
    "the cross-surface gate builders (e.g. ``build_clickbench_duckdb``) consume\n"
    "directly. Detecting that registry is the authoritative gate-applicability signal.\n"
    "\n"
    "History: an earlier version of this sweep used the production query *resolver*\n"
    "(``get_dataframe_queries_for_benchmark``). That under-counted: the resolver only\n"
    "special-cases tpch/tpcds/clickbench plus a generic ``get_dataframe_queries()``\n"
    "method, so benchmarks that expose only a ``<BENCH>_DATAFRAME_QUERIES`` registry\n"
    "(e.g. coffeeshop -- which was nonetheless successfully gated in #842) resolved to\n"
    "zero and were wrongly dispatched to a fallback oracle. Registry detection fixes\n"
    "that.\n"
    "\n"
    "Classification per candidate:\n"
    "  - ``gateable``: has a registry whose ids overlap the SQL ids as-is -> wire a\n"
    "    cross-surface gate on the overlapping ids (w3). A non-zero VERIFIED id overlap\n"
    "    is what makes a benchmark genuinely gateable.\n"
    "  - ``candidate-unverified``: has a registry but ZERO ids overlap the SQL ids\n"
    "    verbatim, so there is no verified SQL<->DataFrame query correspondence. A gate\n"
    "    here would require GUESSING which DataFrame query maps to which SQL query, and\n"
    '    the campaign\'s own TODO warns "do NOT guess" (e.g. nyctaxi/tsbs). This is NOT\n'
    "    counted as gateable coverage: it needs an independent, per-benchmark id mapping\n"
    "    to be confirmed first (some, like tpcds_obt at 3 DF vs ~89 SQL queries, may\n"
    "    never be a clean correspondence). The honest status the M2 review demanded.\n"
    "  - ``not-cheaply-gateable``: would need a full canonical dataset fetch, a\n"
    "    downloader-backed network fetch, or a non-bounded scale (rejects SF=0.01,\n"
    "    ships ``data_manifest.toml``, or ships a network-backed ``downloader.py``;\n"
    "    e.g. joinorder accepts only SF=1.0 via its IMDb 2013 manifest, nyctaxi\n"
    "    downloads the pinned TLC Parquet months before sampling) -> NOT wired as\n"
    "    a routine-PR gate, no matter the id overlap. The reason names the scale /\n"
    "    provenance evidence; joinorder_synthetic (already CI-enforced) is the\n"
    "    scaled stand-in for joinorder.\n"
    "  - ``no-df-query-surface``: no DataFrame query registry -> NOT cross-surface\n"
    "    gateable; needs a w2 fallback oracle (differential second-engine or a curated\n"
    "    expected-results subset).\n"
    "  - ``blocked``: could not instantiate the benchmark or read its registry.\n"
    "\n"
    "Report-mode (regenerate the committed artifact). Detecting a registry + reading\n"
    "SQL ids needs only an import + a benchmark instance (no generated data), so the\n"
    "sweep is cheap. Enumerating real divergences still requires a load-faithful\n"
    "per-benchmark gate builder (see ``build_ssb_duckdb`` /\n"
    "``build_clickbench_duckdb``).\n"
)


def _dataframe_query_registry(benchmark_id: str) -> Any | None:
    from benchbox.core.dataframe.query import QueryRegistry

    target = f"benchbox.core.{benchmark_id}.dataframe_queries"
    try:
        module = importlib.import_module(target)
    except ModuleNotFoundError as exc:
        if exc.name is None or exc.name == target:
            return None
        raise
    for value in vars(module).values():
        if isinstance(value, QueryRegistry):
            return value
    return None


_BOUNDED_OFFLINE_DOWNLOADERS = frozenset({"flightdata"})


def _data_provenance(benchmark_id: str) -> str:
    benchmark_dir = _REPO_ROOT / "benchbox" / "core" / benchmark_id
    try:
        if (benchmark_dir / "data_manifest.toml").exists():
            return "manifest-fetch"
        if benchmark_id not in _BOUNDED_OFFLINE_DOWNLOADERS and (benchmark_dir / "downloader.py").exists():
            return "network-fetch"
    except OSError:
        pass
    return "generated"


def _instantiate(benchmark_id: str) -> tuple[Any | None, float | None, str, str]:
    from benchbox.core.benchmark_loader import get_core_benchmark_class

    cls = get_core_benchmark_class(benchmark_id)
    bounded_scale_error = ""
    last_error = ""
    for scale in _INSTANTIATE_SCALES:
        try:
            return cls(scale_factor=scale), scale, "", bounded_scale_error
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if abs(float(scale) - _INSTANTIATE_SCALES[0]) < 1e-9:
                bounded_scale_error = last_error
    return None, None, last_error, bounded_scale_error


def classify_applicability(benchmark_id: str) -> tuple[str, dict[str, Any]]:
    try:
        registry = _dataframe_query_registry(benchmark_id)
    except ImportError as exc:
        return BLOCKED, {"error": f"dataframe_queries import {type(exc).__name__}: {exc}"}
    if registry is None:
        return NO_DF_QUERY_SURFACE, {"df_queries": 0}

    try:
        df_ids = [str(q) for q in registry.get_query_ids()]
    except Exception as exc:
        return BLOCKED, {"error": f"registry {type(exc).__name__}: {exc}"}

    instance, used_scale, error, bounded_scale_error = _instantiate(benchmark_id)
    if instance is None:
        return BLOCKED, {"error": error, "df_queries": len(df_ids)}

    sql_ids = [str(q) for q in instance.get_queries().keys()]
    raw_overlap = len(set(sql_ids) & set(df_ids))
    detail = {
        "sql_queries": len(sql_ids),
        "df_queries": len(df_ids),
        "raw_id_overlap": raw_overlap,
        "scale": used_scale,
    }
    if benchmark_id in _ABANDONED_CORRESPONDENCE:
        return ABANDONED, {**detail, "reason": _ABANDONED_CORRESPONDENCE[benchmark_id]}
    provenance = _data_provenance(benchmark_id)
    bounded_ok = used_scale is not None and abs(float(used_scale) - _INSTANTIATE_SCALES[0]) < 1e-9
    if not bounded_ok or provenance in ("manifest-fetch", "network-fetch"):
        reasons: list[str] = []
        if not bounded_ok:
            why = bounded_scale_error or error
            reasons.append(f"rejects bounded scale SF=0.01 ({why}); requires SF={used_scale}")
        if provenance == "manifest-fetch":
            reasons.append("canonical manifest fetch (data_manifest.toml)")
        if provenance == "network-fetch":
            reasons.append("downloader-backed network fetch at the bounded scale (downloader.py)")
        if benchmark_id == "joinorder":
            reasons.append("use joinorder_synthetic (already CI-enforced) for scaled smoke-test data")
        return NOT_CHEAPLY_GATEABLE, {**detail, "data_source": provenance, "reason": "; ".join(reasons)}
    status = GATEABLE if raw_overlap > 0 else CANDIDATE_UNVERIFIED
    return status, {**detail, "data_source": provenance}


def build_applicability_sweep() -> list[dict[str, Any]]:
    from _project.scripts.generate_oracle_coverage_map import build_coverage_map

    coverage = {
        row["benchmark"]: row
        for row in build_coverage_map()
        if row["dual_surface"] and (not row["guarded"] or row.get("cross_surface_enforced") is False)
    }
    rows: list[dict[str, Any]] = []
    for benchmark_id, cov in coverage.items():
        status, detail = classify_applicability(benchmark_id)
        staged = cov.get("cross_surface_enforced") is False
        rows.append({"benchmark": benchmark_id, "status": status, "staged": staged, **detail})
    return rows


def render_markdown(rows: list[dict[str, Any]]) -> str:
    gateable = [r for r in rows if r["status"] in _GATEABLE_STATUSES]
    candidate_unverified = [r for r in rows if r["status"] == CANDIDATE_UNVERIFIED]
    not_cheaply = [r for r in rows if r["status"] == NOT_CHEAPLY_GATEABLE]
    no_surface = [r for r in rows if r["status"] == NO_DF_QUERY_SURFACE]
    blocked = [r for r in rows if r["status"] == BLOCKED]
    abandoned = [r for r in rows if r["status"] == ABANDONED]

    lines: list[str] = []
    lines.append("# Cross-surface applicability sweep")
    lines.append("")
    lines.append(
        "**Generated** by `_project/scripts/cross_surface_applicability_sweep.py`. "
        "Drills into the dual-surface unguarded-or-staged benchmarks from the oracle "
        "coverage map (staged gates are registered but NOT CI-enforced) and detects "
        "which ship a DataFrame query `QueryRegistry` (the registry "
        "the cross-surface gate builders consume). `supports_dataframe` (the coverage "
        "map's signal) is a DataFrame *loading* flag and over-counts candidates; the "
        "production query *resolver* under-counts (it misses per-benchmark "
        "`<BENCH>_DATAFRAME_QUERIES` registries). Registry detection is the "
        "authoritative gate-applicability signal."
    )
    lines.append("")
    lines.append(
        "**Gateable means VERIFIED, not merely registered.** A benchmark is counted "
        "as `gateable` only when its DataFrame query ids overlap the SQL query ids "
        "verbatim — a confirmed SQL<->DataFrame correspondence the gate can compare. "
        "A benchmark whose registry has ZERO verbatim id overlap is "
        "`candidate-unverified`, NOT gateable: wiring a gate would require *guessing* "
        "which DataFrame query answers which SQL query, and the campaign's own TODO "
        'warns "do NOT guess". These need an independent, per-benchmark id mapping '
        "confirmed first. Correspondences investigated and ruled out are "
        "`abandoned` instead (e.g. `tpcds_obt`: OBT-native obt_* ids vs TPC-DS "
        "numbered SQL ids), so a later bounded-scale fix cannot re-invite "
        "investigation."
    )
    lines.append("")
    lines.append(
        "**Gateable also means CHEAP, not merely overlapping.** A benchmark that "
        "rejects the bounded SF=0.01 cell, fetches a canonical dataset via "
        "`data_manifest.toml`, or performs downloader-backed network fetches at "
        "the bounded scale is `not-cheaply-gateable`, NOT gateable, no matter "
        "its id overlap: wiring it would drag a full dataset fetch into routine "
        "PRs. The table reason names the scale/provenance evidence."
    )
    lines.append("")
    lines.append(
        f"**Summary:** {len(rows)} dual-surface candidates (unguarded + staged, "
        f"registered but not CI-enforced) — "
        f"{len(gateable)} cross-surface gateable (verified verbatim id overlap at a bounded scale), "
        f"{len(candidate_unverified)} candidate-unverified (registry exists but ZERO "
        f"verified id overlap — needs a confirmed id mapping first), "
        f"{len(not_cheaply)} not-cheaply-gateable (rejects a bounded scale or needs a canonical fetch), "
        f"{len(no_surface)} have no DataFrame query surface (need a w2 fallback oracle), "
        f"{len(abandoned)} abandoned (correspondence investigated and ruled out), "
        f"{len(blocked)} blocked."
    )
    lines.append("")
    lines.append("| Benchmark | Status | SQL queries | DataFrame queries | Raw id overlap | Note |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for r in rows:
        staged_suffix = " [staged, not CI-enforced]" if r.get("staged") else ""
        if r["status"] == BLOCKED:
            lines.append(
                f"| {r['benchmark']} | {BLOCKED} | — | {r.get('df_queries', '—')} | — | "
                f"{r.get('error', '')}{staged_suffix} |"
            )
            continue
        if r["status"] == NO_DF_QUERY_SURFACE:
            note = "→ w2 fallback oracle (no DataFrame query registry)"
            lines.append(f"| {r['benchmark']} | {r['status']} | — | 0 | — | {note}{staged_suffix} |")
            continue
        if r["status"] == NOT_CHEAPLY_GATEABLE:
            note = f"→ NOT a routine-PR gate: {r.get('reason', 'needs a canonical fetch or non-bounded scale')}"
            lines.append(
                f"| {r['benchmark']} | {r['status']} | {r.get('sql_queries', '—')} | "
                f"{r.get('df_queries', '—')} | {r.get('raw_id_overlap', '—')} | {note}{staged_suffix} |"
            )
            continue
        if r["status"] == ABANDONED:
            note = f"→ correspondence abandoned: {r.get('reason', 'see the per-benchmark id-mapping decision test')}"
            scale = r.get("scale")
            if scale is not None and abs(float(scale) - _INSTANTIATE_SCALES[0]) >= 1e-9:
                note += f" Also rejects bounded scale SF={_INSTANTIATE_SCALES[0]:g} (requires SF={scale:g})."
            lines.append(
                f"| {r['benchmark']} | {r['status']} | {r.get('sql_queries', '—')} | "
                f"{r.get('df_queries', '—')} | {r.get('raw_id_overlap', '—')} | {note}{staged_suffix} |"
            )
            continue
        note = (
            "→ cross-surface gate (w3)"
            if r["status"] == GATEABLE
            else "→ confirm an independent SQL↔DataFrame id mapping before gating (do NOT guess)"
        )
        lines.append(
            f"| {r['benchmark']} | {r['status']} | {r.get('sql_queries', '—')} | "
            f"{r.get('df_queries', '—')} | {r.get('raw_id_overlap', '—')} | {note}{staged_suffix} |"
        )
    lines.append("")
    lines.append("## Campaign dispatch")
    lines.append("")
    direct = [r["benchmark"] for r in rows if r["status"] == GATEABLE]
    unverified = [r["benchmark"] for r in candidate_unverified]
    not_cheap_names = [r["benchmark"] for r in not_cheaply]
    lines.append("- **Cross-surface gate, ids overlap as-is (w3):** " + (", ".join(direct) or "none") + ".")
    lines.append(
        "- **Candidate-unverified (NOT gateable yet):** "
        + (", ".join(unverified) or "none")
        + " — a DataFrame query registry exists but ZERO ids overlap the SQL ids "
        "verbatim, so there is no verified query correspondence. Each needs an "
        "independent, per-benchmark id mapping confirmed (the campaign TODO says "
        '"do NOT guess") before a gate can be wired; do not count these as coverage.'
    )
    lines.append(
        "- **Not-cheaply-gateable (NOT a routine-PR gate):** "
        + (", ".join(not_cheap_names) or "none")
        + " — rejects the bounded SF=0.01 cell, needs a canonical manifest fetch, "
        "or performs downloader-backed network fetches at the bounded scale; "
        "do not wire as a routine-PR gate."
    )
    lines.append(
        "- **Abandoned (do NOT re-investigate):** "
        + (", ".join(r["benchmark"] for r in abandoned) or "none")
        + " — the SQL↔DataFrame id correspondence was investigated and ruled "
        "out; the row reason records the verdict. Do not re-open as "
        "candidate-unverified without renumbering one side first."
    )
    lines.append(
        "- **w2 fallback oracle** — no DataFrame query registry, so the cross-surface "
        "gate cannot reach them; they need a differential second-engine check or a "
        "curated expected-results subset: " + (", ".join(r["benchmark"] for r in no_surface) or "none") + "."
    )
    if blocked:
        lines.append("- **Blocked** (instantiate/registry): " + ", ".join(r["benchmark"] for r in blocked) + ".")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--check", action="store_true", help="Fail if the committed artifact is stale.")
    args = parser.parse_args(argv)

    rows = build_applicability_sweep()
    rendered = render_markdown(rows)
    if args.check:
        if not ARTIFACT.exists():
            print(f"missing artifact: {ARTIFACT.relative_to(_REPO_ROOT)} (run the sweep)")
            return 1
        if ARTIFACT.read_text(encoding="utf-8") != rendered:
            print(f"stale artifact: {ARTIFACT.relative_to(_REPO_ROOT)} (run the sweep and commit)")
            return 1
        print("cross-surface applicability sweep is up to date.")
        return 0

    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(rendered, encoding="utf-8")
    gateable = [r["benchmark"] for r in rows if r["status"] in _GATEABLE_STATUSES]
    print(f"Wrote {ARTIFACT.relative_to(_REPO_ROOT)}")
    print(f"{len(rows)} candidates, {len(gateable)} gateable: {', '.join(gateable)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
