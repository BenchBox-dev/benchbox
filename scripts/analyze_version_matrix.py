#!/usr/bin/env python3

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.version_matrix_specs import SPECS, MatrixSpec

CLI_DESCRIPTION = "Compute median engine version-matrix metrics from a run manifest."
ACCEPTED_STATUSES = {"SUCCESS", "PASS", "PASSED"}
EVIDENCE_REQUIRED = {"uncertain", "not_run"}
PINNED_KEYS = ("benchbox_commit", "pyarrow", "thread_count", "datagen_manifests")

CellKey = tuple[str, str, str]


def _median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def _result_path(root: Path, record: dict[str, Any]) -> Path:
    path = root / str(record["path"])
    if not path.is_file():
        raise ValueError(f"missing result bundle: {path}")
    return path


def _query_medians(payload: dict[str, Any]) -> dict[str, float]:
    values: defaultdict[str, list[float]] = defaultdict(list)
    queries = payload.get("queries")
    if not isinstance(queries, list):
        return {}
    for query in queries:
        if not isinstance(query, dict) or not isinstance(query.get("ms"), (int, float)):
            continue
        if query.get("run_type") not in (None, "measurement"):
            continue
        if str(query.get("status", "SUCCESS")).upper() not in {"SUCCESS", "PASS", "PASSED"}:
            continue
        values[str(query.get("id"))].append(float(query["ms"]))
    return {query_id: float(statistics.median(samples)) for query_id, samples in sorted(values.items())}


def _total_ms(payload: dict[str, Any]) -> float:
    timing = payload.get("summary", {}).get("timing", {})
    value = timing.get("total_ms") if isinstance(timing, dict) else None
    if not isinstance(value, (int, float)):
        raise ValueError("power result has no numeric summary.timing.total_ms")
    return float(value)


def _load_power_payloads(spec: MatrixSpec, manifest_path: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if tuple(manifest.get("versions", ())) != spec.versions:
        raise ValueError(f"unexpected version matrix: {manifest.get('versions')}")
    benchmark_entries = tuple((item.get("id"), float(item.get("scale"))) for item in manifest.get("benchmarks", ()))
    if benchmark_entries != spec.benchmarks:
        raise ValueError(f"unexpected benchmark matrix: {manifest.get('benchmarks')}")
    records = [record for record in manifest.get("records", ()) if record.get("phase") == "power"]
    expected_count = len(spec.versions) * len(spec.benchmarks) * spec.repetitions
    if len(records) != expected_count:
        raise ValueError(f"expected {expected_count} power records, found {len(records)}")

    grouped: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    root = manifest_path.parent
    for record in records:
        version = str(record["requested_version"])
        benchmark = str(record["benchmark"])
        payload = json.loads(_result_path(root, record).read_text(encoding="utf-8"))
        if payload.get("summary", {}).get("validation") != spec.required_validation:
            raise ValueError(f"non-passing validation in {record['path']}")
        grouped[(version, benchmark)].append(payload)
    return grouped


def _median_field(payloads: list[dict[str, Any]], path: tuple[str, ...]) -> float | None:
    values: list[float] = []
    for payload in payloads:
        value: Any = payload
        for key in path:
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        if not isinstance(value, (int, float)):
            return None
        values.append(float(value))
    return float(statistics.median(values)) if values else None


def _query_key(query: dict[str, Any]) -> tuple[Any, ...]:
    return (
        query.get("id"),
        query.get("run_type"),
        query.get("stream"),
        query.get("iter"),
        query.get("test_type"),
    )


def aggregate_payloads(
    spec: MatrixSpec, payloads: list[dict[str, Any]], *, version: str, benchmark: str
) -> dict[str, Any]:
    if len(payloads) != spec.repetitions:
        raise ValueError(f"expected {spec.repetitions} repetitions for {version}/{benchmark}")
    if any(payload.get("summary", {}).get("validation") != spec.required_validation for payload in payloads):
        raise ValueError(f"non-passing validation in {version}/{benchmark}")

    aggregate = copy.deepcopy(payloads[0])
    aggregate.setdefault("export", {})["aggregation"] = {
        "method": "median",
        "repetitions": len(payloads),
        "source_run_ids": [str(payload.get("run", {}).get("id")) for payload in payloads],
    }
    source_ids = "|".join([version, benchmark, *aggregate["export"]["aggregation"]["source_run_ids"]])
    aggregate.setdefault("run", {})["id"] = hashlib.sha256(source_ids.encode("utf-8")).hexdigest()[:8]

    timing = aggregate.get("summary", {}).get("timing")
    if isinstance(timing, dict):
        for field in timing:
            median = _median_field(payloads, ("summary", "timing", field))
            if median is not None:
                timing[field] = median

    for path in (("summary", "data", "load_time_ms"), ("summary", "tpc_metrics", "power_at_size")):
        median = _median_field(payloads, path)
        if median is not None:
            target: dict[str, Any] = aggregate
            for key in path[:-1]:
                value = target.get(key)
                if not isinstance(value, dict):
                    value = {}
                    target[key] = value
                target = value
            target[path[-1]] = median

    for field in ("query_time_ms", "total_duration_ms"):
        median = _median_field(payloads, ("run", field))
        if median is not None:
            aggregate["run"][field] = median

    query_maps = [
        {_query_key(query): query for query in payload.get("queries", []) if isinstance(query, dict)}
        for payload in payloads
    ]
    if any(set(query_map) != set(query_maps[0]) for query_map in query_maps[1:]):
        raise ValueError(f"repetitions have different query sets for {version}/{benchmark}")
    for query in aggregate.get("queries", []):
        if not isinstance(query, dict):
            continue
        samples = [query_map[_query_key(query)].get("ms") for query_map in query_maps]
        if all(isinstance(value, (int, float)) for value in samples):
            query["ms"] = float(statistics.median([float(value) for value in samples]))
    return aggregate


def _write_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_explorer_bundles(spec: MatrixSpec, manifest_path: Path, output_dir: Path) -> list[Path]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"Explorer bundle output directory must be new or empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    grouped = _load_power_payloads(spec, manifest_path)
    paths: list[Path] = []
    for version in spec.versions:
        for benchmark in spec.benchmark_ids:
            aggregate = aggregate_payloads(spec, grouped[(version, benchmark)], version=version, benchmark=benchmark)
            path = output_dir / spec.bundle_filename(benchmark, version)
            _write_json(path, aggregate)
            paths.append(path)
    return paths


def analyze(spec: MatrixSpec, manifest_path: Path) -> dict[str, Any]:
    grouped = _load_power_payloads(spec, manifest_path)
    cells: list[dict[str, Any]] = []

    baseline: dict[str, float] = {}
    for version in spec.versions:
        for benchmark in spec.benchmark_ids:
            samples = grouped[(version, benchmark)]
            if len(samples) != spec.repetitions:
                raise ValueError(f"expected {spec.repetitions} repetitions for {version}/{benchmark}")
            query_medians_by_sample = [_query_medians(payload) for payload in samples]
            query_ids = sorted({query_id for queries in query_medians_by_sample for query_id in queries})
            query_medians = {
                query_id: float(
                    statistics.median([queries[query_id] for queries in query_medians_by_sample if query_id in queries])
                )
                for query_id in query_ids
            }
            total_median = float(statistics.median([_total_ms(payload) for payload in samples]))
            power_scores = [
                float(payload.get("summary", {}).get("tpc_metrics", {}).get("power_at_size"))
                for payload in samples
                if isinstance(payload.get("summary", {}).get("tpc_metrics", {}).get("power_at_size"), (int, float))
            ]
            if version == spec.baseline_version:
                baseline[benchmark] = total_median
            cells.append(
                {
                    "version": version,
                    "benchmark": benchmark,
                    "scale": spec.scales[benchmark],
                    "repetitions": len(samples),
                    "median_total_ms": total_median,
                    spec.speedup_field: baseline[benchmark] / total_median if total_median else None,
                    "median_power_at_size": _median(power_scores),
                    "query_medians_ms": query_medians,
                }
            )

    return {
        "schema_version": "1",
        "source_manifest": manifest_path.name,
        "versions": list(spec.versions),
        "benchmarks": list(spec.benchmark_ids),
        "repetitions": spec.repetitions,
        "cells": cells,
    }


def load_qualification(path: Path, spec: MatrixSpec) -> dict[str, Any]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != spec.qualification_sha256:
        raise ValueError(f"qualification file hash {digest} differs from the spec hash {spec.qualification_sha256}")
    payload = json.loads(raw)
    clean = {
        (row["version"], row["setup"], row["benchmark"]): bool(row["clean"]) for row in payload.get("eligibility", ())
    }
    reference = payload.get("reference", {}).get("row_counts", {})
    divergences = payload.get("known_divergences", {})
    return {
        "scale_factor": float(payload["scale_factor"]),
        "clean": clean,
        "reference": reference,
        "divergences": divergences,
        "sha256": digest,
    }


def load_manifests(paths: list[Path], spec: MatrixSpec) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    manifests = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    if not manifests:
        raise ValueError("no manifests given")
    for path, manifest in zip(paths, manifests, strict=True):
        if manifest.get("spec") != spec.snapshot():
            raise ValueError(f"{path} was produced by a different spec")
        if not manifest.get("complete"):
            raise ValueError(f"{path} is incomplete")
        if manifest.get("workload_seed") is not None:
            raise ValueError(f"{path} used a workload seed")
        if any(manifest["pins"].get(key) != manifests[0]["pins"].get(key) for key in PINNED_KEYS):
            raise ValueError(f"{path} has different pinned values from {paths[0]}")
    records = [record for manifest in manifests for record in manifest["records"]]
    return manifests, records


def group_records(records: list[dict[str, Any]], spec: MatrixSpec) -> dict[CellKey, dict[int, dict[str, Any]]]:
    grouped: dict[CellKey, dict[int, dict[str, Any]]] = {}
    for record in records:
        key = (record["benchmark"], record["version"], record["setup"])
        rounds = grouped.setdefault(key, {})
        if record["round"] in rounds:
            raise ValueError(f"duplicate record for {key} round {record['round']}")
        rounds[record["round"]] = record
    expected_rounds = set(range(1, spec.effective_rounds + 1))
    for cell in spec.cells():
        key = (cell.benchmark, cell.version, cell.setup)
        if set(grouped.get(key, {})) != expected_rounds:
            raise ValueError(f"cell {cell.cell_id} does not have exactly rounds {sorted(expected_rounds)}")
    unexpected = set(grouped) - {(c.benchmark, c.version, c.setup) for c in spec.cells()}
    if unexpected:
        raise ValueError(f"records for cells outside the spec: {sorted(unexpected)}")
    return grouped


def assess_record(spec: MatrixSpec, qualification: dict[str, Any], record: dict[str, Any]) -> list[str]:
    benchmark, version, setup = record["benchmark"], record["version"], record["setup"]
    if record["outcome"] != "completed":
        return [f"outcome {record['outcome']}"]
    reasons: list[str] = []
    if float(record["scale"]) != qualification["scale_factor"]:
        reasons.append(f"scale {record['scale']} differs from the qualification scale {qualification['scale_factor']}")
    if record.get("rechunk_effective") is not False:
        reasons.append("rechunk_effective is not false")
    receipt = record.get("execution_engine") if isinstance(record.get("execution_engine"), dict) else {}
    requested = receipt.get("requested", record.get("engine_requested"))
    if requested != record["engine"]:
        reasons.append(f"engine_requested {requested} differs from {record['engine']}")
    if record.get("polars_version") != version or record.get("installed", {}).get("polars") != version:
        reasons.append("recorded Polars version differs from the cell version")
    if record.get("polars_runtime_version") not in (version, "absent"):
        reasons.append(f"runtime version {record.get('polars_runtime_version')} differs from {version}")

    validation = record.get("validation")
    if validation in EVIDENCE_REQUIRED:
        if not qualification["clean"].get((version, setup, benchmark), False):
            reasons.append(f"validation {validation} without clean qualification evidence")
    elif validation != "passed":
        reasons.append(f"validation status {validation}")

    queries = record.get("queries", {})
    if any(str(query.get("status", "")).upper() not in ACCEPTED_STATUSES for query in queries.values()):
        reasons.append("a measured query did not succeed")
    reference = qualification["reference"].get(benchmark, {})
    streams = {key.split(":", 1)[0] for key in queries}
    expected_keys = {key for key in reference if key.split(":", 1)[0] in streams}
    if set(queries) != expected_keys:
        reasons.append("measured query set differs from the reference query set")
    divergences = qualification["divergences"].get(benchmark, {})
    differing = []
    for key, query in queries.items():
        accepted = divergences[key]["observed"] if key in divergences else reference.get(key)
        if query.get("rows") != accepted:
            differing.append(key)
    if differing:
        reasons.append(f"row counts differ from the DuckDB reference: {', '.join(sorted(differing)[:5])}")
    return reasons


def _totals(record: dict[str, Any], excluded: set[str]) -> tuple[float, float]:
    queries = record["queries"]
    full = sum(float(query["ms"]) for query in queries.values())
    common = sum(float(query["ms"]) for key, query in queries.items() if key not in excluded)
    return full, common


def _stats(values: list[float]) -> dict[str, float]:
    return {"median": float(statistics.median(values)), "min": min(values), "max": max(values)}


def timing_stats(records: list[dict[str, Any]], excluded: set[str]) -> dict[str, Any] | None:
    try:
        totals = [_totals(record, excluded) for record in records]
    except (KeyError, TypeError, ValueError):
        return None
    return {"full_ms": _stats([full for full, _ in totals]), "common_ms": _stats([common for _, common in totals])}


def assess_cells(
    spec: MatrixSpec, qualification: dict[str, Any], grouped: dict[CellKey, dict[int, dict[str, Any]]]
) -> list[dict[str, Any]]:
    cells = []
    for cell in spec.cells():
        key = (cell.benchmark, cell.version, cell.setup)
        records = [grouped[key][index] for index in sorted(grouped[key])]
        reasons = [
            f"round {record['round']}: {reason}"
            for record in records
            for reason in assess_record(spec, qualification, record)
        ]
        excluded = set(qualification["divergences"].get(cell.benchmark, {}))
        stats = timing_stats(records, excluded)
        completed = all(record["outcome"] == "completed" for record in records)
        if stats is None and not reasons:
            reasons.append("measured queries have no timing")
        qualified = not reasons
        cells.append(
            {
                "benchmark": cell.benchmark,
                "version": cell.version,
                "setup": cell.setup,
                "engine": cell.engine,
                "scale": spec.scales[cell.benchmark],
                "rounds": len(records),
                "outcomes": [record["outcome"] for record in records],
                "qualified": qualified,
                "reasons": reasons,
                "common_query_exclusions": sorted(excluded),
                "peak_rss_gib": max(record["peak_rss_gib"] for record in records),
                "swap_out_growth_gib": max(record["swap_out_growth_gib"] for record in records),
                "headline": stats if qualified else None,
                "reported_separately": stats if completed and not qualified else None,
            }
        )
    return cells


def compare_cells(spec: MatrixSpec, cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    index = {(cell["benchmark"], cell["version"], cell["setup"]): cell for cell in cells}
    comparisons = []
    frames = (("oldest", spec.baseline_version), ("reference", spec.reference_version))
    for cell in cells:
        if not cell["qualified"]:
            continue
        for frame, frame_version in frames:
            for baseline_setup in dict.fromkeys((cell["setup"], "A")):
                base = index.get((cell["benchmark"], frame_version, baseline_setup))
                if base is not None and base["qualified"]:
                    break
            else:
                continue
            if base is cell:
                continue
            value = cell["headline"]["common_ms"]
            reference = base["headline"]["common_ms"]
            change = value["median"] / reference["median"] - 1
            overlapping = not (value["max"] < reference["min"] or value["min"] > reference["max"])
            comparisons.append(
                {
                    "benchmark": cell["benchmark"],
                    "version": cell["version"],
                    "setup": cell["setup"],
                    "baseline_frame": frame,
                    "baseline_version": frame_version,
                    "baseline_setup": baseline_setup,
                    "median_ms": value["median"],
                    "baseline_median_ms": reference["median"],
                    "speedup": reference["median"] / value["median"],
                    "median_change": change,
                    "ranges_overlap": overlapping,
                    "candidate_mover": (not overlapping) and abs(change) >= spec.mover_effect,
                }
            )
    return comparisons


def analyze_polars(spec: MatrixSpec, manifest_paths: list[Path], qualification_path: Path) -> dict[str, Any]:
    qualification = load_qualification(qualification_path, spec)
    manifests, records = load_manifests(manifest_paths, spec)
    cells = assess_cells(spec, qualification, group_records(records, spec))
    comparisons = compare_cells(spec, cells)
    return {
        "schema_version": "1",
        "source_manifests": [path.name for path in manifest_paths],
        "shuffle_seeds": [manifest["shuffle_seed"] for manifest in manifests],
        "qualification_sha256": qualification["sha256"],
        "rounds": spec.effective_rounds,
        "cells": cells,
        "comparison_count": len(comparisons),
        "comparisons": comparisons,
        "candidate_movers": [item for item in comparisons if item["candidate_mover"]],
    }


def write_polars_outputs(result: dict[str, Any], output_dir: Path) -> None:
    (output_dir / "polars-version-matrix-analysis.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    fields = (
        "benchmark",
        "version",
        "setup",
        "baseline_frame",
        "baseline_version",
        "baseline_setup",
        "median_ms",
        "baseline_median_ms",
        "speedup",
        "median_change",
        "ranges_overlap",
        "candidate_mover",
    )
    with (output_dir / "polars-version-matrix-comparisons.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(result["comparisons"])


def write_outputs(spec: MatrixSpec, result: dict[str, Any], output_dir: Path) -> None:
    (output_dir / f"{spec.analysis_stem}-analysis.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with (output_dir / f"{spec.analysis_stem}-analysis.csv").open("w", encoding="utf-8", newline="") as handle:
        fieldnames = (
            "version",
            "benchmark",
            "scale",
            "repetitions",
            "median_total_ms",
            spec.speedup_field,
            "median_power_at_size",
        )
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows({key: cell.get(key) for key in fieldnames} for cell in result["cells"])


def main(argv: list[str] | None = None, *, spec: MatrixSpec | None = None, description: str = CLI_DESCRIPTION) -> int:
    parser = argparse.ArgumentParser(description=description)
    if spec is None:
        parser.add_argument("--engine", choices=sorted(SPECS), required=True, help="Engine spec of the manifest")
    parser.add_argument("manifest", type=Path, help="matrix-manifest.json emitted by the runner")
    parser.add_argument("extra_manifests", type=Path, nargs="*", help="additional manifests for a setup matrix")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="directory for the analysis JSON and CSV (default: the manifest's directory)",
    )
    parser.add_argument(
        "--explorer-bundles-dir",
        type=Path,
        help="also write one median bundle per version/benchmark cell to this directory",
    )
    parser.add_argument("--qualification", type=Path, default=None, help="Qualification file named by the spec")
    args = parser.parse_args(argv)
    selected = spec or SPECS[args.engine]
    if selected.setup_versions:
        if args.explorer_bundles_dir is not None:
            parser.error("--explorer-bundles-dir is not supported for a setup matrix")
        if args.qualification is None:
            parser.error("--qualification is required for a setup matrix")
        if args.output_dir is None:
            parser.error("--output-dir is required for a setup matrix")
        try:
            result = analyze_polars(selected, [args.manifest, *args.extra_manifests], args.qualification)
            args.output_dir.mkdir(parents=True, exist_ok=True)
            write_polars_outputs(result, args.output_dir)
        except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
            parser.error(str(exc))
        qualified = sum(1 for cell in result["cells"] if cell["qualified"])
        print(
            f"Analyzed {len(result['cells'])} cells ({qualified} qualified); {result['comparison_count']} comparisons"
        )
        return 0
    if args.extra_manifests or args.qualification is not None:
        parser.error("extra manifests and --qualification apply only to a setup matrix")
    try:
        result = analyze(selected, args.manifest)
        output_dir = args.output_dir or args.manifest.parent
        output_dir.mkdir(parents=True, exist_ok=True)
        write_outputs(selected, result, output_dir)
        explorer_paths = (
            write_explorer_bundles(selected, args.manifest, args.explorer_bundles_dir)
            if args.explorer_bundles_dir
            else []
        )
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(f"Analyzed {len(result['cells'])} median cells")
    if explorer_paths:
        print(f"Wrote {len(explorer_paths)} median Explorer bundles to {args.explorer_bundles_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
