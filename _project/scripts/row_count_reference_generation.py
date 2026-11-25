from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from benchbox.core.expected_results.row_count_references import (
    KIND_ENGINE_AGREEMENT,
    QUERY_IDS,
    SCHEMA_VERSION,
    write_reference,
)

REFERENCE_ENGINES = ("duckdb", "datafusion")
MANIFEST_NAME = "_datagen_manifest.json"
CHECKSUM_ALGORITHM = (
    "sha256 over each table's decompressed shards in natural shard order, then over table name and digest; "
    "the dbgen_version table is excluded because it records the generation time"
)
VOLATILE_TABLES = frozenset({"dbgen_version"})
_NUMBER = re.compile(r"\d+")

CountQuery = Callable[[str], int]


def scale_tag(scale_factor: float) -> str:
    return f"{scale_factor:g}".replace(".", "")


def engine_versions() -> dict[str, str]:
    import datafusion
    import duckdb

    return {"duckdb": duckdb.__version__, "datafusion": datafusion.__version__}


def load_engine(work_dir: Path, platform: str, benchmark: str, scale_factor: float) -> Path:
    root = work_dir / platform
    if next(iter((root / "databases").glob("*/*")), None) is not None and next(
        iter((root / "datagen").glob(f"*/{MANIFEST_NAME}")), None
    ):
        return root
    root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["BENCHBOX_OUTPUT_DIR"] = str(root)
    env["PYTHONUTF8"] = "1"
    command = [
        sys.executable,
        "-m",
        "benchbox.cli.main",
        "run",
        "--platform",
        platform,
        "--benchmark",
        benchmark,
        "--scale",
        str(scale_factor),
        "--phases",
        "generate,load",
        "--non-interactive",
    ]
    print(f"Running: {' '.join(command[3:])} (output {root})", file=sys.stderr, flush=True)
    proc = subprocess.run(
        command, cwd=str(root), env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-4000:])
        sys.stderr.write(proc.stderr[-4000:])
        raise SystemExit(f"{platform} {benchmark} SF {scale_factor} load failed (exit {proc.returncode})")
    return root


def datagen_directory(root: Path) -> Path:
    manifests = sorted((root / "datagen").glob(f"*/{MANIFEST_NAME}"))
    if len(manifests) != 1:
        raise SystemExit(f"expected exactly one data manifest under {root / 'datagen'}, found {len(manifests)}")
    return manifests[0].parent


def _shard_order(name: str) -> tuple[tuple[int, ...], str]:
    return tuple(int(number) for number in _NUMBER.findall(name)), name


def _stream_bytes(path: Path):
    if path.suffix == ".zst":
        import zstandard

        with path.open("rb") as handle, zstandard.ZstdDecompressor().stream_reader(handle) as reader:
            while chunk := reader.read(1 << 20):
                yield chunk
    else:
        with path.open("rb") as handle:
            while chunk := handle.read(1 << 20):
                yield chunk


def data_checksum(datagen: Path) -> dict[str, Any]:
    manifest = json.loads((datagen / MANIFEST_NAME).read_text(encoding="utf-8"))
    tables: dict[str, str] = {}
    for table, spec in sorted(manifest["tables"].items()):
        if table in VOLATILE_TABLES:
            continue
        entries = spec if isinstance(spec, list) else [entry for group in spec["formats"].values() for entry in group]
        files = sorted({entry["path"] for entry in entries}, key=_shard_order)
        digest = hashlib.sha256()
        for name in files:
            for chunk in _stream_bytes(datagen / name):
                digest.update(chunk)
        tables[table] = digest.hexdigest()
    total = hashlib.sha256()
    for table, digest in tables.items():
        total.update(f"{table}\0{digest}\n".encode())
    return {"algorithm": CHECKSUM_ALGORITHM, "sha256": total.hexdigest(), "tables": tables}


def generator_identity(benchmark: str, data_generator: str, query_generator: str, datagen: Path) -> dict[str, Any]:
    from benchbox.utils.query_generation import current_query_generation_stamp

    return {
        "data_generator": data_generator,
        "query_generator": query_generator,
        "data_stamp": generator_stamp(datagen),
        "query_generation": current_query_generation_stamp(benchmark),
    }


def generator_stamp(datagen: Path) -> dict[str, Any]:
    manifest = json.loads((datagen / MANIFEST_NAME).read_text(encoding="utf-8"))
    return {
        key: manifest[key]
        for key in ("generator_version", "data_generation_version", "generator_binary_sha256", "base_constants_hash")
        if key in manifest
    }


def open_counters(root_by_engine: Mapping[str, Path]) -> tuple[dict[str, CountQuery], Callable[[], None]]:
    import duckdb
    from datafusion import SessionContext

    counters: dict[str, CountQuery] = {}
    closers: list[Callable[[], None]] = []
    if "duckdb" in root_by_engine:
        database = next((root_by_engine["duckdb"] / "databases").glob("*/*.duckdb"))
        connection = duckdb.connect(str(database), read_only=True)
        counters["duckdb"] = lambda sql: len(connection.execute(sql).fetchall())
        closers.append(connection.close)
    if "datafusion" in root_by_engine:
        directory = next((root_by_engine["datafusion"] / "databases").glob("*/*.datafusion"))
        context = SessionContext()
        for parquet_file in sorted(directory.glob("*.parquet")):
            context.register_parquet(parquet_file.stem, str(parquet_file))
        counters["datafusion"] = lambda sql: sum(batch.num_rows for batch in context.sql(sql).collect())

    def close_all() -> None:
        for close in closers:
            close()

    return counters, close_all


def count_cache_key(engine: str, data_sha256: str, sql: str, label: str, query_id: str) -> str:
    version = engine_versions()[engine]
    return f"{engine}|{version}|{data_sha256}|{hashlib.sha256(sql.encode()).hexdigest()}|{label}|{query_id}"


def counts_for_stream(
    counters: Mapping[str, CountQuery],
    cache: dict[str, int],
    cache_path: Path,
    engine: str,
    label: str,
    query_ids: tuple[str, ...],
    sql_for: Callable[[str, str], str],
    *,
    data_sha256: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for query_id in query_ids:
        sql = sql_for(query_id, engine)
        key = count_cache_key(engine, data_sha256, sql, label, query_id)
        if key not in cache:
            cache[key] = counters[engine](sql)
            cache_path.write_text(json.dumps(cache, indent=1), encoding="utf-8")
            print(f"{engine} {label} Q{query_id}: {cache[key]}", file=sys.stderr, flush=True)
        counts[query_id] = cache[key]
    return counts


def load_cache(path: Path) -> dict[str, int]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def agree(
    label: str, query_ids: tuple[str, ...], counts_by_engine: Mapping[str, Mapping[str, int]]
) -> tuple[dict[str, int], list[str]]:
    agreed: dict[str, int] = {}
    disagreements: list[str] = []
    for query_id in query_ids:
        observed = {engine: counts[query_id] for engine, counts in counts_by_engine.items()}
        if len(set(observed.values())) != 1:
            disagreements.append(f"{label} Q{query_id}: {observed}")
        else:
            agreed[query_id] = next(iter(observed.values()))
    return agreed, disagreements


def report_disagreements(benchmark: str, scale_factor: float, disagreements: list[str], report_path: Path) -> None:
    lines = [f"{benchmark} SF {scale_factor:g}: engines disagree on {len(disagreements)} query/queries", *disagreements]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    raise SystemExit("\n".join(lines))


def build_payload(
    *,
    benchmark: str,
    scale_factor: float,
    streams: Mapping[int, dict[str, Any]],
    versions: Mapping[str, str],
    generator: dict[str, Any],
    checksum: dict[str, Any],
    agreement: dict[str, Any],
    regenerate: str,
    answer_set_check: dict[str, Any] | None = None,
    integrity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provenance: dict[str, Any] = {
        "generator": generator,
        "data_checksum": checksum,
        "engines": {engine: versions[engine] for engine in REFERENCE_ENGINES},
        "generated_on": datetime.datetime.now(datetime.timezone.utc).date().isoformat(),
        "agreement": agreement,
        "regenerate": regenerate,
    }
    if answer_set_check is not None:
        provenance["answer_set_check"] = answer_set_check
    return {
        "schema_version": SCHEMA_VERSION,
        "benchmark": benchmark,
        "scale_factor": float(scale_factor),
        "reference_kind": KIND_ENGINE_AGREEMENT,
        "streams": {str(stream): dict(entry) for stream, entry in sorted(streams.items())},
        "provenance": provenance,
        **({"integrity": integrity} if integrity is not None else {}),
    }


def agreement_record(benchmark: str, stream_ids: list[int], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "result": "identical",
        "engines": list(REFERENCE_ENGINES),
        "streams": stream_ids,
        "queries_per_stream": len(QUERY_IDS[benchmark]),
        "rule": "every query on every stream returned the same row count on every listed engine, on data with one checksum",
        **(extra or {}),
    }


def write(payload: dict[str, Any]) -> Path:
    return write_reference(payload)
