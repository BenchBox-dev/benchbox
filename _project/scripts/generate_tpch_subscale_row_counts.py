#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from benchbox.core.expected_results.loader import (
    TPCH_SUBSCALE_ROW_COUNT_SCALE_FACTORS,
    tpch_subscale_row_count_reference_path,
)
from benchbox.core.expected_results.tpch_results import PARAMETER_SENSITIVE_QUERY_IDS

_REPO_ROOT = Path(__file__).resolve().parents[2]

CLI_MODULE = "benchbox.cli.main"
QUERY_IDS = [str(query_id) for query_id in range(1, 23)]
REFERENCE_ENGINES = ("duckdb", "datafusion")
DEFAULT_PROBE_SEEDS = (1, 7, 42, 99, 123, 2024, 31337, 65537, 1000003, 17039360)
TEMPLATES_DIR = _REPO_ROOT / "_sources" / "tpc-h" / "dbgen"
TEMPLATE_FILES = [*(f"queries/{query_id}.sql" for query_id in QUERY_IDS if query_id != "15"), "variants/15a.sql"]

_NOTE = (
    "Exact per-query row counts for TPC-H at a scale factor below 1, with qgen default substitution "
    "parameters (qgen -d, no seed). TPC publishes answer files only at SF 1, so these counts are a "
    "BenchBox reference: DuckDB returned exactly these counts in every iteration, and every other "
    "listed engine returned them too, on data from the bundled dbgen. parameter_sensitive_query_ids lists queries whose count changed under at least one probe "
    "seed or that are parameter-sensitive at SF 1; runs with other parameters skip them. Regenerate "
    "with `make tpch-subscale-row-counts-regen`; do not hand-edit."
)


def _template_digest() -> str:
    digest = hashlib.sha256()
    for relative in TEMPLATE_FILES:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update((TEMPLATES_DIR / relative).read_bytes())
    return digest.hexdigest()


def _run_counts(
    runs_root: Path, platform: str, scale_factor: float, seed: int | None, iterations: int
) -> dict[str, set[int]]:
    from benchbox.core.results.loader import find_latest_result

    results_dir = runs_root / "results"
    before = set(results_dir.glob("*.json")) if results_dir.exists() else set()
    command = [
        sys.executable,
        "-m",
        CLI_MODULE,
        "run",
        "--platform",
        platform,
        "--benchmark",
        "tpch",
        "--scale",
        str(scale_factor),
        "--phases",
        "generate,load,power",
        "--iterations",
        str(iterations),
        "--validation",
        "disabled",
        "--non-interactive",
    ]
    if seed is not None:
        command.extend(["--seed", str(seed)])
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["BENCHBOX_OUTPUT_DIR"] = str(runs_root)
    print(f"Running: {' '.join(command[3:])}", file=sys.stderr)
    proc = subprocess.run(
        command,
        cwd=str(runs_root),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3600,
        check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-4000:])
        sys.stderr.write(proc.stderr[-4000:])
        raise SystemExit(f"{platform} SF {scale_factor} seed {seed} run failed (exit {proc.returncode})")
    result_path = find_latest_result(results_dir, benchmark="tpch")
    if result_path is None or result_path in before:
        raise SystemExit(f"no new result JSON under {results_dir}")
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    counts: dict[str, set[int]] = {}
    for query in payload.get("queries", []):
        if query.get("run_type") != "measurement" or int(query.get("stream") or 0) != 0:
            continue
        if query.get("status") != "SUCCESS" or not isinstance(query.get("rows"), int):
            raise SystemExit(f"{platform} SF {scale_factor} seed {seed}: query {query.get('id')} did not succeed")
        counts.setdefault(str(query["id"]), set()).add(query["rows"])
    if sorted(counts, key=int) != QUERY_IDS:
        raise SystemExit(
            f"{platform} SF {scale_factor} seed {seed}: expected 22 queries, got {sorted(counts, key=int)}"
        )
    return counts


def _engine_versions() -> dict[str, str]:
    import datafusion
    import duckdb

    return {"duckdb": duckdb.__version__, "datafusion": datafusion.__version__}


def build_reference(
    scale_factor: float,
    engine_counts: dict[str, dict[str, set[int]]],
    seed_counts: dict[int, dict[str, set[int]]],
    engine_versions: dict[str, str],
    template_digest: str,
) -> dict:
    reference_engine = REFERENCE_ENGINES[0]
    unstable = sorted((qid for qid in QUERY_IDS if len(engine_counts[reference_engine][qid]) != 1), key=int)
    if unstable:
        raise SystemExit(
            f"SF {scale_factor}: {reference_engine} returned different counts across iterations for {unstable}"
        )
    reference_counts = {qid: next(iter(engine_counts[reference_engine][qid])) for qid in QUERY_IDS}
    for engine, counts in engine_counts.items():
        missing = sorted((qid for qid in QUERY_IDS if reference_counts[qid] not in counts[qid]), key=int)
        if missing:
            raise SystemExit(f"SF {scale_factor}: {engine} never returned the {reference_engine} count for {missing}")
        for qid in QUERY_IDS:
            others = sorted(counts[qid] - {reference_counts[qid]})
            if others:
                print(
                    f"WARNING SF {scale_factor}: {engine} Q{qid} also returned {others} "
                    f"(reference {reference_counts[qid]}); that engine answers this query inconsistently",
                    file=sys.stderr,
                )
    varying = {qid for counts in seed_counts.values() for qid in QUERY_IDS if counts[qid] != {reference_counts[qid]}}
    sensitive = sorted(varying | set(PARAMETER_SENSITIVE_QUERY_IDS), key=int)
    return {
        "benchmark": "tpch",
        "scale_factor": scale_factor,
        "reference_seed": None,
        "query_parameters": "qgen -d (TPC-H default substitution parameters)",
        "provenance": {
            "data_generator": "_sources/tpc-h/dbgen (dbgen)",
            "query_generator": "_sources/tpc-h/dbgen (qgen -d)",
            "query_template_digest": {
                "algorithm": "sha256 over path, NUL, contents for each template in order",
                "templates": TEMPLATE_FILES,
                "sha256": template_digest,
            },
            "engines": {engine: engine_versions[engine] for engine in engine_counts},
            "probe_seeds": sorted(seed_counts),
            "phases": "generate,load,power",
            "note": _NOTE,
        },
        "parameter_sensitive_query_ids": sensitive,
        "row_counts": reference_counts,
    }


def render(reference: dict) -> str:
    return json.dumps(reference, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale-factor", type=float, action="append", dest="scale_factors")
    parser.add_argument("--probe-seed", type=int, action="append", dest="probe_seeds")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args(argv)
    scale_factors = args.scale_factors or list(TPCH_SUBSCALE_ROW_COUNT_SCALE_FACTORS)
    probe_seeds = args.probe_seeds or list(DEFAULT_PROBE_SEEDS)
    unsupported = [sf for sf in scale_factors if sf not in TPCH_SUBSCALE_ROW_COUNT_SCALE_FACTORS]
    if unsupported:
        raise SystemExit(
            f"unsupported scale factor(s) {unsupported}; add them to TPCH_SUBSCALE_ROW_COUNT_SCALE_FACTORS"
        )

    template_digest = _template_digest()
    engine_versions = _engine_versions()
    with tempfile.TemporaryDirectory(prefix="benchbox-row-count-regen-") as tmp:
        runs_root = args.work_dir or Path(tmp)
        runs_root.mkdir(parents=True, exist_ok=True)
        for scale_factor in scale_factors:
            engine_counts = {
                engine: _run_counts(runs_root, engine, scale_factor, None, args.iterations)
                for engine in REFERENCE_ENGINES
            }
            seed_counts = {
                seed: _run_counts(runs_root, REFERENCE_ENGINES[0], scale_factor, seed, 1) for seed in probe_seeds
            }
            reference = build_reference(scale_factor, engine_counts, seed_counts, engine_versions, template_digest)
            path = tpch_subscale_row_count_reference_path(scale_factor)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(render(reference), encoding="utf-8")
            print(
                f"Wrote {path.relative_to(_REPO_ROOT)}: "
                f"{' '.join(str(reference['row_counts'][qid]) for qid in QUERY_IDS)}; "
                f"parameter-sensitive {reference['parameter_sensitive_query_ids']}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
