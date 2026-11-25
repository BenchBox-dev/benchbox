from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from benchbox.utils.clock import elapsed_seconds, mono_time

BENCHMARKS = [
    "tpch",
    "tpcds",
    "ssb",
    "h2odb",
    "tsbs_devops",
    "vector_search",
    "coffeeshop",
    "amplab",
    "clickbench",
    "joinorder_synthetic",
    "tpch_skew",
]


def _make_benchmark(name: str, scale: float, out: Path) -> Any:
    if name == "tpch":
        from benchbox.core.tpch.benchmark import TPCHBenchmark

        return TPCHBenchmark(
            scale_factor=scale, output_dir=out, force_regenerate=True, enable_compression=False, quiet=True
        )
    if name == "tpcds":
        from benchbox.core.tpcds.benchmark import TPCDSBenchmark

        return TPCDSBenchmark(
            scale_factor=scale, output_dir=out, force_regenerate=True, enable_compression=False, quiet=True
        )
    if name == "ssb":
        from benchbox.core.ssb.benchmark import SSBBenchmark

        return SSBBenchmark(scale_factor=scale, output_dir=out, enable_compression=False, quiet=True)
    if name == "h2odb":
        from benchbox.core.h2odb.benchmark import H2OBenchmark

        return H2OBenchmark(scale_factor=scale, output_dir=out, enable_compression=False, quiet=True)
    if name == "tsbs_devops":
        from benchbox.core.tsbs_devops.benchmark import TSBSDevOpsBenchmark

        return TSBSDevOpsBenchmark(
            scale_factor=scale, output_dir=out, force_regenerate=True, enable_compression=False, quiet=True
        )
    if name == "vector_search":
        from benchbox.core.vector_search.benchmark import VectorSearchBenchmark

        return VectorSearchBenchmark(scale_factor=scale, output_dir=out, enable_compression=False, quiet=True)
    if name == "coffeeshop":
        from benchbox.core.coffeeshop.benchmark import CoffeeShopBenchmark

        return CoffeeShopBenchmark(scale_factor=scale, output_dir=out, enable_compression=False, quiet=True)
    if name == "amplab":
        from benchbox.core.amplab.benchmark import AMPLabBenchmark

        return AMPLabBenchmark(scale_factor=scale, output_dir=out, enable_compression=False, quiet=True)
    if name == "clickbench":
        from benchbox.core.clickbench.benchmark import ClickBenchBenchmark
        from benchbox.core.clickbench.generator import ClickBenchDataGenerator

        bench = ClickBenchBenchmark(scale_factor=scale, output_dir=out, enable_compression=False, quiet=True)
        bench._synthetic_gen = ClickBenchDataGenerator(scale_factor=scale, output_dir=out, enable_compression=False)
        return bench
    if name == "joinorder_synthetic":
        from benchbox.core.joinorder_synthetic.benchmark import JoinOrderSyntheticBenchmark

        return JoinOrderSyntheticBenchmark(
            scale_factor=scale, output_dir=out, force_regenerate=True, enable_compression=False, quiet=True
        )
    if name == "tpch_skew":
        from benchbox.core.tpch_skew.benchmark import TPCHSkewBenchmark

        return TPCHSkewBenchmark(
            scale_factor=scale, output_dir=out, force_regenerate=True, enable_compression=False, quiet=True
        )
    raise ValueError(f"Unknown benchmark: {name}")


def _run_worker(mode: str, bench: str, sf: float, data_dir: Path, pq_dir: Path, row_group_size: int) -> None:
    if mode == "generate":
        data_dir.mkdir(parents=True, exist_ok=True)
        t0 = mono_time()
        instance = _make_benchmark(bench, sf, data_dir)
        if bench == "clickbench":
            instance._synthetic_gen.generate_data()
        else:
            instance.generate_data()
        dt = elapsed_seconds(t0)
        files = [p for p in data_dir.rglob("*") if p.is_file() and p.name != "_datagen_manifest.json"]
        total_bytes = sum(p.stat().st_size for p in files)
        manifest_path = data_dir / "_datagen_manifest.json"
        tables: list[str] = []
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            tables = sorted((manifest.get("tables") or {}).keys())
        print(
            "RESULT:"
            + json.dumps(
                {
                    "wall_s": round(dt, 4),
                    "output_bytes": total_bytes,
                    "file_count": len(files),
                    "tables": tables,
                }
            )
        )
        return

    if mode == "convert":
        from benchbox.core.runner.conversion import FormatConversionOrchestrator
        from benchbox.core.runner.runner import _get_table_schemas_from_benchmark
        from benchbox.utils.format_converters.base import ConversionOptions

        instance = _make_benchmark(bench, sf, data_dir)
        manifest_path = data_dir / "_datagen_manifest.json"
        schemas = _get_table_schemas_from_benchmark(instance)
        pq_dir.mkdir(parents=True, exist_ok=True)
        opts = ConversionOptions(
            compression="snappy",
            row_group_size=row_group_size,
            merge_shards=True,
            validate_row_count=True,
            output_dir=pq_dir,
        )
        orchestrator = FormatConversionOrchestrator()
        t0 = mono_time()
        errors: list[str] = []
        converted: list[str] = []
        row_counts: dict[str, int] = {}
        row_groups: dict[str, Any] = {}
        try:
            res = orchestrator.convert_benchmark_tables(
                manifest_path=manifest_path,
                output_dir=data_dir,
                target_format="parquet",
                schemas=schemas,
                options=opts,
            )
            converted = sorted(res.keys())
            row_counts = {k: v.row_count for k, v in res.items()}
            row_groups = {k: v.metadata.get("row_groups") for k, v in res.items()}
        except Exception as exc:
            errors.append(str(exc))
        dt = elapsed_seconds(t0)
        pq_files = [p for p in pq_dir.rglob("*.parquet") if p.is_file()]
        pq_bytes = sum(p.stat().st_size for p in pq_files)
        print(
            "RESULT:"
            + json.dumps(
                {
                    "wall_s": round(dt, 4),
                    "parquet_bytes": pq_bytes,
                    "converted_tables": converted,
                    "row_counts": row_counts,
                    "row_groups": row_groups,
                    "errors": errors,
                }
            )
        )
        return

    if mode == "legacy_reference":
        import pyarrow as pa
        import pyarrow.csv as pacsv
        import pyarrow.parquet as pq

        from benchbox.core.runner.runner import _get_table_schemas_from_benchmark
        from benchbox.utils.file_format import (
            get_column_names_with_trailing,
            get_delimiter_for_file,
            has_trailing_delimiter,
        )
        from benchbox.utils.format_converters.base import ArrowTypeMapper

        instance = _make_benchmark(bench, sf, data_dir)
        manifest_path = data_dir / "_datagen_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        schemas = _get_table_schemas_from_benchmark(instance)
        pq_dir.mkdir(parents=True, exist_ok=True)
        csv_delim_meta = manifest.get("csv_delimiter")
        csv_header_meta = manifest.get("csv_has_header")
        t0 = mono_time()
        converted = []
        errors = []
        for table_name, table_info in (manifest.get("tables") or {}).items():
            schema = schemas.get(table_name)
            if not schema:
                continue
            formats_map = table_info.get("formats") or {}
            entries = formats_map.get("tbl") or next(iter(formats_map.values()), [])
            src_files = [data_dir / e["path"] for e in entries if (data_dir / e["path"]).exists()]
            if not src_files:
                continue
            arrow_schema = ArrowTypeMapper.build_arrow_schema(schema)
            col_names = [f.name for f in arrow_schema]
            try:
                tables = []
                for entry in entries:
                    src = data_dir / entry["path"]
                    if not src.exists():
                        continue
                    entry_meta = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
                    entry_delim = entry_meta.get("csv_delimiter", csv_delim_meta)
                    entry_header = entry_meta.get("csv_has_header", csv_header_meta)
                    first_line = src.open("r", encoding="utf-8", errors="replace").readline().rstrip("\r\n")
                    if isinstance(entry_delim, str) and len(entry_delim) == 1:
                        delim = entry_delim
                    else:
                        delim = get_delimiter_for_file(src)
                        if delim == "," and len(col_names) > 1 and "|" in first_line:
                            if len(first_line.split("|")) in (len(col_names), len(col_names) + 1):
                                delim = "|"
                    if isinstance(entry_header, bool):
                        has_header = entry_header
                    else:
                        hdr_parts = [p.strip().strip('"').lower() for p in first_line.split(delim)]
                        has_header = hdr_parts[: len(col_names)] == [c.lower() for c in col_names]
                    if has_header:
                        with src.open("r", encoding="utf-8", errors="replace") as fh:
                            fh.readline()
                            second = fh.readline().rstrip("\r\n")
                        trailing = bool(second) and len(second.split(delim)) > len(col_names) and second.endswith(delim)
                    else:
                        trailing = has_trailing_delimiter(src, delim, col_names)
                    read_cols = get_column_names_with_trailing(col_names, trailing)
                    tbl = pacsv.read_csv(
                        src,
                        read_options=pacsv.ReadOptions(
                            column_names=read_cols,
                            autogenerate_column_names=False,
                            skip_rows=1 if has_header else 0,
                        ),
                        parse_options=pacsv.ParseOptions(delimiter=delim, quote_char='"', escape_char="\\"),
                        convert_options=pacsv.ConvertOptions(
                            column_types=arrow_schema,
                            null_values=[""],
                            strings_can_be_null=True,
                            include_columns=col_names,
                        ),
                    )
                    tables.append(tbl)
                combined = pa.concat_tables(tables) if len(tables) > 1 else tables[0]
                pq.write_table(combined, pq_dir / f"{table_name}.parquet", compression="SNAPPY")
                converted.append(table_name)
            except Exception as exc:
                errors.append(f"{table_name}: {exc}")
        dt = elapsed_seconds(t0)
        print(
            "RESULT:"
            + json.dumps(
                {
                    "wall_s": round(dt, 4),
                    "converted_tables": sorted(converted),
                    "errors": errors,
                }
            )
        )
        return

    raise ValueError(f"Unknown worker mode: {mode}")


def _parse_peak_rss_mib(stderr_text: str) -> float | None:
    mac_match = re.search(r"^\s*(\d+)\s+maximum resident set size", stderr_text, re.MULTILINE)
    if mac_match:
        return round(int(mac_match.group(1)) / (1024 * 1024), 2)
    linux_match = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", stderr_text)
    if linux_match:
        return round(int(linux_match.group(1)) / 1024, 2)
    return None


def run_step(
    mode: str,
    bench: str,
    sf: float,
    data_dir: Path,
    pq_dir: Path,
    timeout_s: int,
    *,
    row_group_size: int = 122_880,
) -> dict[str, Any]:
    worker_cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker-mode",
        mode,
        "--worker-benchmark",
        bench,
        "--worker-scale",
        str(sf),
        "--worker-data-dir",
        str(data_dir),
        "--worker-pq-dir",
        str(pq_dir),
        "--row-group-size",
        str(row_group_size),
    ]
    if Path("/usr/bin/time").exists():
        cmd = ["/usr/bin/time", "-l" if sys.platform == "darwin" else "-v", *worker_cmd]
    else:
        cmd = worker_cmd
    t0 = mono_time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"status": "capped_timeout", "timeout_s": timeout_s, "wall_s": round(elapsed_seconds(t0), 2)}
    dt = elapsed_seconds(t0)
    payload: dict[str, Any] = {
        "status": "ok" if proc.returncode == 0 else "error",
        "returncode": proc.returncode,
        "proc_wall_s": round(dt, 4),
        "peak_rss_mib": _parse_peak_rss_mib(proc.stderr),
    }
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT:"):
            payload.update(json.loads(line[len("RESULT:") :]))
            break
    if proc.returncode != 0:
        payload["stderr_tail"] = proc.stderr[-800:]
    if payload.get("errors"):
        payload["status"] = "error"
    return payload


def _verify_except_all(ref_dir: Path, test_dir: Path) -> dict[str, Any]:
    import duckdb
    import pyarrow.parquet as pq

    conn = duckdb.connect(":memory:")
    try:
        ref_files = sorted(ref_dir.glob("*.parquet"))
        test_files = sorted(test_dir.glob("*.parquet"))
        ref_names = {f.name for f in ref_files}
        mismatches: dict[str, dict[str, Any]] = {}
        checked_tables: list[str] = []
        for test_file in test_files:
            if test_file.name not in ref_names:
                mismatches[test_file.stem] = {"missing_in_ref": 1}
        for ref_file in ref_files:
            table_name = ref_file.stem
            test_file = test_dir / ref_file.name
            if not test_file.exists():
                mismatches[table_name] = {"missing_in_test": 1}
                continue
            ref_schema = pq.read_schema(ref_file)
            test_schema = pq.read_schema(test_file)
            if ref_schema.names != test_schema.names or ref_schema.types != test_schema.types:
                mismatches[table_name] = {
                    "schema_mismatch": {
                        "ref": [(f.name, str(f.type)) for f in ref_schema],
                        "test": [(f.name, str(f.type)) for f in test_schema],
                    }
                }
                checked_tables.append(table_name)
                continue
            ref_sql = str(ref_file).replace("'", "''")
            test_sql = str(test_file).replace("'", "''")
            left_diff = conn.execute(
                f"SELECT COUNT(*) FROM (SELECT * FROM read_parquet('{ref_sql}') EXCEPT ALL SELECT * FROM read_parquet('{test_sql}'))"
            ).fetchone()[0]
            right_diff = conn.execute(
                f"SELECT COUNT(*) FROM (SELECT * FROM read_parquet('{test_sql}') EXCEPT ALL SELECT * FROM read_parquet('{ref_sql}'))"
            ).fetchone()[0]
            checked_tables.append(table_name)
            if left_diff or right_diff:
                mismatches[table_name] = {"ref_minus_test": int(left_diff), "test_minus_ref": int(right_diff)}
        return {
            "passed": bool(ref_files) and not mismatches and len(checked_tables) == len(ref_files),
            "tables_checked": checked_tables,
            "mismatches": mismatches,
        }
    finally:
        conn.close()


def _summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = [r.get("status") for r in runs]
    if not statuses:
        overall_status = "skipped"
    elif all(s == "ok" for s in statuses):
        overall_status = "ok"
    else:
        overall_status = next((str(s) for s in statuses if s != "ok"), "error")
    ok_runs = [r for r in runs if r.get("status") == "ok"]
    walls = [r["wall_s"] for r in ok_runs if isinstance(r.get("wall_s"), (int, float))]
    rss_values = [r["peak_rss_mib"] for r in ok_runs if isinstance(r.get("peak_rss_mib"), (int, float))]
    return {
        "status": overall_status,
        "median_wall_s": round(statistics.median(walls), 4) if overall_status == "ok" and walls else None,
        "median_peak_rss_mib": round(statistics.median(rss_values), 2)
        if overall_status == "ok" and rss_values
        else None,
        "runs": runs,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-mode", default="")
    parser.add_argument("--worker-benchmark", default="")
    parser.add_argument("--worker-scale", type=float, default=1.0)
    parser.add_argument("--worker-data-dir", default="")
    parser.add_argument("--worker-pq-dir", default="")
    parser.add_argument("--scales", default="1.0,10.0")
    parser.add_argument("--benchmarks", default=",".join(BENCHMARKS))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--sf1-timeout", type=int, default=300)
    parser.add_argument("--sf10-timeout", type=int, default=300)
    parser.add_argument("--row-group-size", type=int, default=122_880)
    parser.add_argument("--skip-convert", action="store_true")
    parser.add_argument("--reuse-generated", action="store_true")
    parser.add_argument("--verify-except-all", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    if args.worker_mode:
        _run_worker(
            args.worker_mode,
            args.worker_benchmark,
            args.worker_scale,
            Path(args.worker_data_dir),
            Path(args.worker_pq_dir),
            args.row_group_size,
        )
        return

    scales = [float(x) for x in args.scales.split(",") if x.strip()]
    benches = [x.strip() for x in args.benchmarks.split(",") if x.strip()]
    out_path = Path(args.out) if args.out else None

    import duckdb
    import pyarrow

    report: dict[str, Any] = {
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
            "pyarrow": pyarrow.__version__,
            "duckdb": duckdb.__version__,
            "cpus": os.cpu_count(),
        },
        "repeats": args.repeats,
        "row_group_size": args.row_group_size,
        "sf1_timeout_s": args.sf1_timeout,
        "sf10_timeout_s": args.sf10_timeout,
        "results": {},
    }

    with tempfile.TemporaryDirectory(prefix="benchbox-datagen-measure-") as tmp_root_str:
        work_root = Path(tmp_root_str)
        for sf in scales:
            sf_key = f"sf{sf:g}"
            report["results"][sf_key] = {}
            timeout_s = args.sf1_timeout if sf <= 1.0 else args.sf10_timeout
            for bench in benches:
                print(f"=== {bench} {sf_key} (repeats={args.repeats}, cap={timeout_s}s) ===", flush=True)
                gen_runs: list[dict[str, Any]] = []
                conv_runs: list[dict[str, Any]] = []
                except_all_res: dict[str, Any] | None = None

                shared_data_dir = work_root / f"{bench}_{sf_key}_shared"
                manifest_backup: bytes | None = None

                if args.reuse_generated:
                    gen_res = run_step("generate", bench, sf, shared_data_dir, work_root / "unused", timeout_s)
                    gen_runs.append(gen_res)
                    manifest_file = shared_data_dir / "_datagen_manifest.json"
                    if manifest_file.exists():
                        manifest_backup = manifest_file.read_bytes()

                for rep in range(args.repeats):
                    if args.reuse_generated:
                        data_dir = shared_data_dir
                        if manifest_backup is not None:
                            (data_dir / "_datagen_manifest.json").write_bytes(manifest_backup)
                        gen_ok = gen_runs[0].get("status") == "ok"
                    else:
                        data_dir = work_root / f"{bench}_{sf_key}_r{rep}_data"
                        gen_res = run_step("generate", bench, sf, data_dir, work_root / "unused", timeout_s)
                        gen_runs.append(gen_res)
                        gen_ok = gen_res.get("status") == "ok"

                    if gen_ok and not args.skip_convert:
                        pq_dir = work_root / f"{bench}_{sf_key}_r{rep}_pq"
                        conv_res = run_step(
                            "convert",
                            bench,
                            sf,
                            data_dir,
                            pq_dir,
                            timeout_s,
                            row_group_size=args.row_group_size,
                        )
                        conv_runs.append(conv_res)
                        if rep == 0 and args.verify_except_all and conv_res.get("status") == "ok":
                            if manifest_backup is not None:
                                (data_dir / "_datagen_manifest.json").write_bytes(manifest_backup)
                            ref_pq_dir = work_root / f"{bench}_{sf_key}_ref_pq"
                            ref_res = run_step(
                                "legacy_reference",
                                bench,
                                sf,
                                data_dir,
                                ref_pq_dir,
                                timeout_s,
                            )
                            if ref_res.get("status") == "ok":
                                except_all_res = _verify_except_all(ref_pq_dir, pq_dir)
                            else:
                                except_all_res = {"passed": False, "reference_error": ref_res}
                            shutil.rmtree(ref_pq_dir, ignore_errors=True)
                        shutil.rmtree(pq_dir, ignore_errors=True)

                    if not args.reuse_generated:
                        shutil.rmtree(data_dir, ignore_errors=True)

                shutil.rmtree(shared_data_dir, ignore_errors=True)
                gen_summary = _summarize_runs(gen_runs)
                conv_summary = _summarize_runs(conv_runs)
                entry: dict[str, Any] = {
                    "generate": gen_summary,
                    "convert": conv_summary,
                }
                if except_all_res is not None:
                    entry["except_all"] = except_all_res
                report["results"][sf_key][bench] = entry
                print(
                    f"  gen={gen_summary['status']} med_wall={gen_summary['median_wall_s']}s med_rss={gen_summary['median_peak_rss_mib']}MiB | "
                    f"conv={conv_summary['status']} med_wall={conv_summary['median_wall_s']}s med_rss={conv_summary['median_peak_rss_mib']}MiB"
                    + (f" | except_all={except_all_res['passed']}" if except_all_res is not None else ""),
                    flush=True,
                )
                if out_path is not None:
                    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    rendered = json.dumps(report, indent=2)
    if out_path is not None:
        out_path.write_text(rendered, encoding="utf-8")
        print(f"Wrote report to {out_path}", flush=True)
    else:
        print(rendered, flush=True)


if __name__ == "__main__":
    main()
