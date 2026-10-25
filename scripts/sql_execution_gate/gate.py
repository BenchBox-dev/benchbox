from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from scripts.sql_execution_gate import augmentation
from scripts.sql_execution_gate.engines import Dataset, QueryTimeout, available_engines
from scripts.sql_execution_gate.obt import OBT_AUGMENTED, OBT_BENCHMARK, build_obt_dataset
from scripts.sql_execution_gate.rows import first_difference, is_vacuous, vacuity_detail

ROOT = Path(__file__).resolve().parents[2]
EMIT = Path(__file__).with_name("emit.py")
EXPECTED = Path(__file__).with_name("expected_differences.json")
CHECKS = ("error", "row-count", "reference", "vacuous", "baseline", "base-equivalence")
PARTS = "tpcds-parts"
DATASET_OF = {"tpchavoc": "tpch", PARTS: "tpcds"}


def _augmentation_pairs() -> tuple[tuple[str, str], ...]:
    return ((augmentation.BASE, augmentation.AUGMENTED), (OBT_BENCHMARK, OBT_AUGMENTED))


def _base_of(benchmark: str) -> str | None:
    return {OBT_BENCHMARK: augmentation.BASE, OBT_AUGMENTED: augmentation.AUGMENTED}.get(benchmark)


TPCDS_MULTI_PART = {"14": "14a", "23": "23a", "24": "24a", "39": "39a"}
REFERENCE_ENGINE = "duckdb"


@dataclass
class Outcome:
    rows: list[tuple] | None = None
    error: str | None = None


@dataclass
class Finding:
    benchmark: str
    query: str
    engine: str
    check: str
    detail: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.benchmark, self.query, self.engine, self.check)


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    improvements: list[Finding] = field(default_factory=list)
    unexplained: list[Finding] = field(default_factory=list)
    explained: list[Finding] = field(default_factory=list)
    stale: list[dict] = field(default_factory=list)
    compared: dict[str, int] = field(default_factory=dict)
    cells: dict[tuple[str, str], int] = field(default_factory=dict)
    proven: list[Finding] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)
    reasons: dict[tuple[str, str, str, str], str] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.unexplained and not self.stale


def load_expected(path: Path = EXPECTED) -> list[dict]:
    expanded = []
    seen = set()
    for entry in json.loads(path.read_text(encoding="utf-8"))["differences"]:
        label = f"{entry.get('engine')}/{entry.get('benchmark')}/{entry.get('check')}"
        if entry["check"] not in CHECKS:
            raise ValueError(f"{label}: unknown check {entry['check']!r}")
        for field_name in ("justification", "match"):
            if not str(entry.get(field_name, "")).strip():
                raise ValueError(f"{label}: every expected difference needs a non-empty {field_name}")
        if not entry.get("queries"):
            raise ValueError(f"{label}: list the queries the justification covers")
        engines = entry["engine"] if isinstance(entry["engine"], list) else [entry["engine"]]
        for query in entry["queries"]:
            for engine in engines:
                key = (entry["benchmark"], str(query), engine, entry["check"])
                if key in seen:
                    raise ValueError(f"{key}: duplicate expected difference")
                seen.add(key)
                expanded.append(
                    {
                        **{k: v for k, v in entry.items() if k not in ("queries", "engine")},
                        "query": str(query),
                        "engine": engine,
                    }
                )
    return expanded


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def emit_queries(ref: str | None, scale: float, benchmarks: str, scratch: Path, tag: str) -> dict:
    checkout = None
    root = ROOT
    if ref:
        checkout = scratch / f"checkout-{tag}"
        _git("worktree", "add", "--detach", str(checkout), ref)
        root = checkout
    try:
        helper = scratch / f"emit_{tag}.py"
        shutil.copy(EMIT, helper)
        out = scratch / f"queries_{tag}.json"
        environment = {**os.environ, "PYTHONPATH": str(root)}
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import runpy, sys; sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')",
                str(helper),
                "--out",
                str(out),
                "--scale",
                str(scale),
                "--benchmarks",
                benchmarks,
                "--augment-ids",
                ",".join(augmentation.QUERY_IDS),
                "--augment-seeds",
                ",".join(augmentation.SEEDS),
            ],
            cwd=root,
            env=environment,
            check=True,
        )
        payload = json.loads(out.read_text(encoding="utf-8"))
        payload["ref"] = ref or "working tree"
        return payload
    finally:
        if checkout is not None:
            subprocess.run(["git", "worktree", "remove", "--force", str(checkout)], cwd=ROOT, check=False)


def generate_datasets(scale: float, benchmarks: list[str], scratch: Path) -> dict[str, Dataset]:
    datasets: dict[str, Dataset] = {}
    if {"tpch", "tpchavoc"} & set(benchmarks):
        from benchbox.core.tpch.benchmark import TPCHBenchmark
        from benchbox.core.tpch.schema import TABLES, get_create_all_tables_sql

        directory = scratch / "data-tpch"
        TPCHBenchmark(scale_factor=scale, output_dir=directory).generate_data()
        datasets["tpch"] = Dataset(
            "tpch",
            get_create_all_tables_sql(enable_primary_keys=False, enable_foreign_keys=False),
            {table.name: directory / f"{table.name}.tbl" for table in TABLES},
        )
    if {"tpcds", OBT_BENCHMARK} & set(benchmarks):
        from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark
        from benchbox.core.tpcds.schema import TABLES, get_create_all_tables_sql

        directory = scratch / "data-tpcds"
        TPCDSBenchmark(scale_factor=scale, output_dir=directory).generate_data()
        datasets["tpcds"] = Dataset(
            "tpcds",
            get_create_all_tables_sql(enable_primary_keys=False, enable_foreign_keys=False),
            {table.name: directory / f"{table.name}.dat" for table in TABLES},
        )
    if OBT_BENCHMARK in benchmarks:
        datasets[OBT_BENCHMARK] = build_obt_dataset(OBT_BENCHMARK, datasets["tpcds"], scale, scratch)
    return datasets


def expected_row_counts(scale: float) -> dict[str, dict[str, int]]:
    from benchbox.core.expected_results.loader import load_tpch_subscale_row_counts
    from benchbox.core.expected_results.tpcds_results import tpcds_subscale_expected_row_counts

    counts: dict[str, dict[str, int]] = {}
    try:
        counts["tpch"] = dict(load_tpch_subscale_row_counts(scale).row_counts)
    except FileNotFoundError:
        pass
    tpcds = tpcds_subscale_expected_row_counts(scale)
    if tpcds is not None:
        counts["tpcds"] = {
            query: tpcds[TPCDS_MULTI_PART.get(query, query)]
            for query in map(str, range(1, 100))
            if TPCDS_MULTI_PART.get(query, query) in tpcds
        }
        lettered = {query: rows for query, rows in tpcds.items() if not query.isdigit()}
        counts[PARTS] = lettered
        counts[OBT_BENCHMARK] = {
            **{query: tpcds[query] for query in map(str, range(1, 100)) if query in tpcds},
            **lettered,
        }
    return counts


def _execute(engine, sql: str) -> Outcome:
    if sql.startswith("<") and sql.endswith(">"):
        return Outcome(error=f"translation failed {sql}")
    try:
        return Outcome(rows=engine.execute(sql))
    except QueryTimeout as error:
        return Outcome(error=f"timeout: {error}")
    except Exception as error:
        return Outcome(error=f"{type(error).__name__}: {str(error).splitlines()[0][:200] if str(error) else ''}")


def _run_unit(
    engine_class, dataset_name: str, dataset: Dataset, sides: dict[str, dict], timeout: float, scratch: Path
) -> tuple[dict, str, float]:
    results: dict[tuple[str, str, str, str], Outcome] = {}
    started = time.monotonic()
    engine = engine_class(timeout)
    parquet_dir = scratch / f"parquet-{dataset_name}"
    parquet_dir.mkdir(exist_ok=True)
    engine.open(dataset, parquet_dir)
    version = engine.version
    try:
        cache: dict[str, Outcome] = {}
        for side, emitted in sides.items():
            for benchmark, queries in emitted["queries"].items():
                if DATASET_OF.get(benchmark, benchmark) != dataset_name:
                    continue
                for query, per_dialect in queries.items():
                    sql = per_dialect[engine.dialect]
                    outcome = cache.get(sql)
                    if outcome is None:
                        outcome = cache[sql] = _execute(engine, sql)
                    results[(engine.name, benchmark, query, side)] = outcome
    finally:
        engine.close()
    return results, version, time.monotonic() - started


def _query_count(sides: dict[str, dict], dataset_name: str) -> int:
    return sum(
        len(queries)
        for emitted in sides.values()
        for benchmark, queries in emitted["queries"].items()
        if DATASET_OF.get(benchmark, benchmark) == dataset_name
    )


def _worker_count(units: int) -> int:
    return min(units, max(2, (os.cpu_count() or 2) - 1))


def run_engines(
    engine_classes, datasets: dict[str, Dataset], sides: dict[str, dict], timeout: float, scratch: Path
) -> tuple[dict, dict[str, str], dict[str, float]]:
    results: dict[tuple[str, str, str, str], Outcome] = {}
    versions: dict[str, str] = {}
    seconds: dict[str, float] = {}

    def collect(engine_class, outcome: tuple[dict, str, float]) -> None:
        results.update(outcome[0])
        versions[engine_class.name] = outcome[1]
        seconds[engine_class.name] = seconds.get(engine_class.name, 0.0) + outcome[2]

    for engine_class in engine_classes:
        if engine_class.name == REFERENCE_ENGINE:
            for name, dataset in datasets.items():
                collect(engine_class, _run_unit(engine_class, name, dataset, sides, timeout, scratch))
    ordered = sorted(datasets.items(), key=lambda item: -_query_count(sides, item[0]))
    units = [
        (engine_class, name, dataset)
        for name, dataset in ordered
        for engine_class in engine_classes
        if engine_class.name != REFERENCE_ENGINE
    ]
    if units:
        with ProcessPoolExecutor(
            max_workers=_worker_count(len(units)), mp_context=multiprocessing.get_context("spawn")
        ) as pool:
            futures = [
                (unit[0], pool.submit(_run_unit, unit[0], unit[1], unit[2], sides, timeout, scratch)) for unit in units
            ]
            for engine_class, future in futures:
                collect(engine_class, future.result())
    return results, {name: versions[name] for name in sorted(versions, key=_engine_order(engine_classes))}, seconds


def _engine_order(engine_classes):
    order = {engine.name: index for index, engine in enumerate(engine_classes)}
    return order.__getitem__


def evaluate(
    results: dict,
    sides: dict[str, dict],
    counts: dict[str, dict[str, int]],
    expected: list[dict],
    engines: list[str],
) -> Report:
    report = Report()
    for benchmark, queries in sides["candidate"]["queries"].items():
        for query in queries:
            reference = results.get((REFERENCE_ENGINE, benchmark, query, "candidate"))
            for engine in engines:
                _evaluate_cell(report, results, benchmark, query, engine, reference, counts)
    for base, augmented in _augmentation_pairs():
        _apply_augmented_proof(report, sides["candidate"]["queries"], engines, base, augmented)
        for query in sides["candidate"]["queries"].get(augmented, {}):
            reference_result = results.get((REFERENCE_ENGINE, augmented, query, "candidate"))
            if reference_result is not None and reference_result.rows is not None:
                report.evidence[f"{base} q{query} {REFERENCE_ENGINE}"] = repr(reference_result.rows)
    matched: set[int] = set()
    for finding in report.findings:
        index = next((i for i, entry in enumerate(expected) if _explains(entry, finding)), None)
        if index is None:
            report.unexplained.append(finding)
        else:
            matched.add(index)
            report.explained.append(finding)
            report.reasons[finding.key] = expected[index]["justification"]
    scope = sides["candidate"]["queries"]
    report.stale = [
        entry
        for i, entry in enumerate(expected)
        if i not in matched and entry["engine"] in engines and entry["benchmark"] in scope and not entry.get("optional")
    ]
    return report


def _explains(entry: dict, finding: Finding) -> bool:
    return (entry["benchmark"], entry["query"], entry["engine"], entry["check"]) == finding.key and entry[
        "match"
    ] in finding.detail


def _apply_augmented_proof(report: Report, scope: dict, engines: list[str], base: str, augmented: str) -> None:
    covered = {(engine, augmentation.base_query(query)) for query in scope.get(augmented, {}) for engine in engines}
    failing = {(f.engine, augmentation.base_query(f.query)) for f in report.findings if f.benchmark == augmented}
    proven = covered - failing
    kept = []
    for finding in report.findings:
        if finding.check == "vacuous" and finding.benchmark == base and (finding.engine, finding.query) in proven:
            report.proven.append(finding)
        else:
            kept.append(finding)
    report.findings = kept


def _base_difference(results: dict, benchmark: str, query: str, engine: str, candidate: Outcome) -> str | None:
    base_benchmark = _base_of(benchmark)
    if base_benchmark is None:
        return None
    if base_benchmark == augmentation.BASE and not augmentation.base_query(query).isdigit():
        base_benchmark = PARTS
    base = results.get((engine, base_benchmark, query, "candidate"))
    if base is None or base.rows is None:
        return None
    return first_difference(candidate.rows, base.rows)


def _evaluate_cell(report, results, benchmark, query, engine, reference, counts) -> None:
    candidate = results.get((engine, benchmark, query, "candidate"))
    baseline = results.get((engine, benchmark, query, "baseline"))
    if candidate is None:
        return
    report.compared[engine] = report.compared.get(engine, 0) + 1
    report.cells[(benchmark, engine)] = report.cells.get((benchmark, engine), 0) + 1

    def add(check: str, detail: str, into=report.findings) -> None:
        into.append(Finding(benchmark, query, engine, check, detail))

    if candidate.error is not None:
        add("error", candidate.error)
        return
    count = counts.get(benchmark, {}).get(query)
    if count is not None and len(candidate.rows) != count:
        add("row-count", f"{len(candidate.rows)} rows, TPC reference {count}")
    reference_difference = None
    if engine != REFERENCE_ENGINE and reference is not None and reference.rows is not None:
        reference_difference = first_difference(candidate.rows, reference.rows)
        if reference_difference:
            add("reference", f"differs from DuckDB: {reference_difference}")
    base_difference = _base_difference(results, benchmark, query, engine, candidate)
    if base_difference:
        add("base-equivalence", f"differs from the TPC-DS result: {base_difference}")
    reference_rows = reference.rows if reference is not None else None
    if is_vacuous(candidate.rows) and (
        engine == REFERENCE_ENGINE or reference_rows is None or is_vacuous(reference_rows)
    ):
        add("vacuous", vacuity_detail(candidate.rows))
    if baseline is None:
        return
    if baseline.error is not None:
        add("baseline", f"candidate runs, baseline failed: {baseline.error}", report.improvements)
        return
    baseline_difference = first_difference(candidate.rows, baseline.rows, strict=True)
    if not baseline_difference:
        return
    baseline_reference = (
        first_difference(baseline.rows, reference.rows)
        if engine != REFERENCE_ENGINE and reference is not None and reference.rows is not None
        else "unverifiable"
    )
    if engine != REFERENCE_ENGINE and reference_difference is None and baseline_reference:
        add("baseline", f"baseline was wrong: {baseline_reference}", report.improvements)
    else:
        add("baseline", f"differs from baseline: {baseline_difference}")


def coverage(report: Report) -> dict[str, dict[str, int]]:
    table: dict[str, dict[str, int]] = {}
    for (benchmark, engine), compared in sorted(report.cells.items()):
        mine = [f for f in (*report.explained, *report.unexplained) if (f.benchmark, f.engine) == (benchmark, engine)]
        table[f"{benchmark}/{engine}"] = {
            "compared": compared,
            "errors": sum(1 for f in mine if f.check == "error"),
            "vacuous_unproven": sum(1 for f in mine if f.check == "vacuous"),
            "expected_differences": sum(
                1 for f in report.explained if (f.benchmark, f.engine) == (benchmark, engine) and f.check != "vacuous"
            ),
            "unexplained": sum(1 for f in report.unexplained if (f.benchmark, f.engine) == (benchmark, engine)),
        }
    return table


def render(report: Report, versions: dict[str, str], seconds: dict[str, float], sides: dict[str, dict]) -> str:
    lines = ["SQL execution evidence gate"]
    for side, payload in sides.items():
        pin = payload.get("runtime_pin", {})
        lines.append(f"  {side}: {payload['ref']} sqlglot runtime {pin.get('version', 'unknown')}")
    for engine, version in versions.items():
        lines.append(f"  {engine} {version}: {report.compared.get(engine, 0)} cells in {seconds.get(engine, 0.0):.1f}s")
    lines.append("coverage per benchmark and engine:")
    lines.extend(
        f"  {name}: {row['compared']} compared, {row['errors']} errors, {row['vacuous_unproven']} vacuous (unproven), "
        f"{row['expected_differences']} expected differences, {row['unexplained']} unexplained"
        for name, row in coverage(report).items()
    )
    unproven = [f for f in (*report.explained, *report.unexplained) if f.check == "vacuous"]
    lines.append(f"vacuous comparisons proven by gate-only augmented data: {len(report.proven)}")
    lines.extend(
        f"  {benchmark} q{query} on {', '.join(engines)} [{detail}]"
        for (benchmark, query, detail), engines in _group(report.proven, lambda f: (f.benchmark, f.query, f.detail))
    )
    lines.extend(f"  {query}: {rows}" for query, rows in sorted(report.evidence.items()))
    lines.append(f"NOT PROVEN, vacuous comparisons: {len(unproven)}")
    for (benchmark, engine), count in sorted(_unproven_counts(unproven).items()):
        lines.append(f"  {benchmark} {engine}: {count} not proven")
    lines.extend(
        f"  {benchmark} q{query} on {', '.join(engines)} [{detail}] {reason}"
        for (benchmark, query, detail, reason), engines in _group(
            unproven,
            lambda f: (
                f.benchmark,
                f.query,
                f.detail,
                report.reasons.get(f.key, "NO ALLOWLIST ENTRY: needs non-vacuous evidence or a reasoned entry"),
            ),
        )
    )
    for title, items in (
        ("verified improvements over the baseline", report.improvements),
        ("expected differences", [f for f in report.explained if f.check != "vacuous"]),
        ("UNEXPLAINED DIFFERENCES", [f for f in report.unexplained if f.check != "vacuous"]),
    ):
        lines.append(f"{title}: {len(items)}")
        lines.extend(f"  {f.engine} {f.benchmark} q{f.query} [{f.check}] {f.detail}" for f in items)
    unexplained_vacuous = sum(1 for f in report.unexplained if f.check == "vacuous")
    if unexplained_vacuous:
        lines.append(f"UNEXPLAINED VACUOUS COMPARISONS (fail closed): {unexplained_vacuous}")
    if report.stale:
        lines.append(f"STALE expected differences (no longer occur): {len(report.stale)}")
        lines.extend(
            f"  {e['engine']} {e['benchmark']} q{e['query']} [{e['check']}] {e['justification']}" for e in report.stale
        )
    lines.append("RESULT: " + ("PASS" if report.passed else "FAIL"))
    return "\n".join(lines)


def _group(findings: list[Finding], key) -> list[tuple[tuple, list[str]]]:
    grouped: dict[tuple, list[str]] = {}
    for finding in findings:
        grouped.setdefault(key(finding), []).append(finding.engine)
    return list(grouped.items())


def _unproven_counts(findings: list[Finding]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for finding in findings:
        key = (finding.benchmark, finding.engine)
        counts[key] = counts.get(key, 0) + 1
    return counts


def default_baseline_ref() -> str | None:
    base = os.environ.get("BASE_REF")
    if base:
        return _git("merge-base", "HEAD", base)
    for candidate in ("origin/develop", "develop"):
        probe = subprocess.run(["git", "rev-parse", "--verify", "-q", candidate], cwd=ROOT, capture_output=True)
        if probe.returncode == 0:
            return _git("merge-base", "HEAD", candidate)
    return None


def run(args: argparse.Namespace) -> int:
    benchmarks = args.benchmarks.split(",")
    candidate_ref = None if args.candidate_ref in (None, "working-tree") else args.candidate_ref
    baseline_ref = (
        default_baseline_ref()
        if args.baseline_ref == "auto"
        else (None if args.baseline_ref == "none" else args.baseline_ref)
    )
    engine_classes = available_engines(args.engines.split(",") if args.engines else None)
    if REFERENCE_ENGINE not in {engine.name for engine in engine_classes}:
        engine_classes = [*available_engines([REFERENCE_ENGINE]), *engine_classes]
    scratch = Path(tempfile.mkdtemp(prefix="sql_execution_gate_"))
    try:
        sides = {"candidate": emit_queries(candidate_ref, args.scale, args.benchmarks, scratch, "candidate")}
        if baseline_ref:
            sides["baseline"] = emit_queries(baseline_ref, args.scale, args.benchmarks, scratch, "baseline")
        datasets = generate_datasets(args.scale, benchmarks, scratch)
        augmented_queries = sides["candidate"]["queries"].get(augmentation.AUGMENTED)
        if augmented_queries and augmentation.BASE in datasets:
            datasets[augmentation.AUGMENTED] = augmentation.augment(
                datasets[augmentation.BASE], augmented_queries, scratch
            )
            if OBT_BENCHMARK in datasets and sides["candidate"]["queries"].get(OBT_AUGMENTED):
                datasets[OBT_AUGMENTED] = build_obt_dataset(
                    OBT_AUGMENTED, datasets[augmentation.AUGMENTED], args.scale, scratch
                )
        results, versions, seconds = run_engines(engine_classes, datasets, sides, args.timeout, scratch)
        selected = [
            engine.name for engine in engine_classes if not args.engines or engine.name in args.engines.split(",")
        ]
        report = evaluate(results, sides, expected_row_counts(args.scale), load_expected(Path(args.expected)), selected)
        print(render(report, versions, seconds, sides))
        if args.report:
            Path(args.report).write_text(
                json.dumps(
                    {
                        "passed": report.passed,
                        "versions": versions,
                        "seconds": seconds,
                        "coverage": coverage(report),
                        "unexplained": [vars(f) for f in report.unexplained],
                        "explained": [vars(f) for f in report.explained],
                        "improvements": [vars(f) for f in report.improvements],
                        "stale": report.stale,
                        "proven_by_augmentation": [vars(f) for f in report.proven],
                        "augmented_reference_rows": report.evidence,
                        "not_proven": [
                            {**vars(f), "reason": report.reasons.get(f.key)}
                            for f in (*report.explained, *report.unexplained)
                            if f.check == "vacuous"
                        ],
                    },
                    indent=1,
                ),
                encoding="utf-8",
            )
        return 0 if report.passed else 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", type=float, default=0.01)
    parser.add_argument("--benchmarks", default="tpch,tpcds,tpchavoc")
    parser.add_argument("--engines", default="", help="comma-separated subset of sqlite,duckdb,datafusion,postgres")
    parser.add_argument("--candidate-ref", default="working-tree")
    parser.add_argument(
        "--baseline-ref", default="auto", help="git ref, 'auto' (merge-base with the base branch) or 'none'"
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--expected", default=str(EXPECTED))
    parser.add_argument("--report")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
