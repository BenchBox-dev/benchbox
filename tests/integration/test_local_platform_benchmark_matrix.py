from __future__ import annotations

import json
import os
import re
import socket
from pathlib import Path

import pytest

from benchbox.core.benchmark_registry import get_benchmark_metadata, list_benchmark_ids
from benchbox.core.expected_results.loader import load_tpch_value_digest_seed, load_tpch_value_digests
from benchbox.core.expected_results.models import ValidationMode
from benchbox.core.expected_results.registry import get_registry
from benchbox.core.results.loader import find_latest_result
from benchbox.core.validation.query_validation import QueryValidator
from tests.e2e.utils import is_dataframe_available, is_gpu_available, is_platform_available
from tests.integration._cli_e2e_utils import run_cli_command
from tests.required_local_cases import (
    SQLITE_CASE_ITERATIONS,
    SQLITE_CASE_QUERY_IDS,
    SQLITE_CASE_SCALE_FACTOR,
    SQLITE_CASE_SEED,
    TPCH_TABLE_NAMES,
    RequiredCaseError,
    check_measurement_multiset,
    check_rows_match,
    check_tables_populated,
    expected_measurement_multiset,
)
from tests.uat.matrix import LOCAL_SQL_PLATFORMS

pytestmark = [
    pytest.mark.integration,
    pytest.mark.stress,
]


ALL_BENCHMARKS: tuple[str, ...] = tuple(list_benchmark_ids())
DEFAULT_LOCAL_SQL_BENCHMARKS: tuple[str, ...] = ("tpch", "tpcds")
LOCAL_SQL_STABLE_MATRIX: dict[str, set[str]] = {
    "duckdb": {"tpch", "tpcds"},
    "datafusion": {"tpch"},
    "sqlite": set(),
}
LOCAL_SQL_BENCHMARKS: tuple[str, ...] = (
    ALL_BENCHMARKS
    if os.environ.get("BENCHBOX_FULL_LOCAL_SQL_MATRIX", "").strip().lower() in {"1", "true", "yes", "on"}
    else DEFAULT_LOCAL_SQL_BENCHMARKS
)
LOCAL_DATAFRAME_PLATFORMS: tuple[str, ...] = (
    "pandas-df",
    "polars-df",
    "dask-df",
    "pyspark-df",
    "datafusion-df",
    "cudf-df",
)
_DATAFRAME_XFAIL_COMBINATIONS: dict[tuple[str, str], str] = {}
DATAFRAME_BENCHMARKS: tuple[str, ...] = tuple(
    benchmark
    for benchmark in ALL_BENCHMARKS
    if bool((get_benchmark_metadata(benchmark) or {}).get("supports_dataframe"))
)
SERVICE_LOCAL_PLATFORMS: tuple[str, ...] = ("postgresql", "timescaledb", "trino", "presto", "firebolt")

MATRIX_CASE_TIMEOUT = 1200.0
MAINTENANCE_TIMEOUT = 1500.0
CORRECTNESS_GATE_QUERY_IDS_ENV = "BENCHBOX_CORRECTNESS_GATE_QUERY_IDS"


def _select_scale(benchmark_name: str, platform_name: str | None = None) -> float:
    if benchmark_name == "tpch":
        if platform_name in {"sqlite", "datafusion"}:
            return 0.01
        return 1.0
    if benchmark_name == "tpcds":
        return 1.0

    metadata = get_benchmark_metadata(benchmark_name) or {}
    min_scale = metadata.get("min_scale", metadata.get("default_scale", 0.01))
    return float(min_scale)


def _load_result_payload(work_dir: Path, benchmark_name: str) -> tuple[Path, dict]:
    results_dir = work_dir / "benchmark_runs" / "results"
    result_path = find_latest_result(results_dir, benchmark=benchmark_name)
    assert result_path is not None, f"No result JSON found in {results_dir} for {benchmark_name}"
    return result_path, json.loads(result_path.read_text(encoding="utf-8"))


def _measurement_queries(payload: dict) -> list[dict]:
    queries = payload.get("queries", [])
    return [q for q in queries if q.get("run_type") == "measurement"]


def _query_subset_from_env() -> tuple[str, ...]:
    raw = os.environ.get(CORRECTNESS_GATE_QUERY_IDS_ENV, "").strip()
    if not raw:
        return ()
    return tuple(query_id.strip() for query_id in raw.split(",") if query_id.strip())


def _service_matrix_enabled() -> bool:
    return os.environ.get("BENCHBOX_SERVICE_LOCAL_MATRIX", "").strip().lower() in {"1", "true", "yes", "on"}


def _maintenance_matrix_enabled() -> bool:
    return os.environ.get("BENCHBOX_ENABLE_MAINTENANCE_MATRIX", "").strip().lower() in {"1", "true", "yes", "on"}


def _local_sql_case_enabled(platform_name: str, benchmark_name: str) -> bool:
    if os.environ.get("BENCHBOX_FULL_LOCAL_SQL_MATRIX", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return benchmark_name in LOCAL_SQL_STABLE_MATRIX.get(platform_name, set())


def _tcp_open(host: str, port: int, timeout_seconds: float = 1.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout_seconds)
        return sock.connect_ex((host, port)) == 0


def _service_endpoint(platform_name: str) -> tuple[str, int]:
    if platform_name in {"postgresql", "timescaledb"}:
        host = os.environ.get("BENCHBOX_PG_HOST", "127.0.0.1")
        port = int(os.environ.get("BENCHBOX_PG_PORT", "5432"))
        return host, port
    if platform_name in {"trino", "presto"}:
        host = os.environ.get("BENCHBOX_TRINO_HOST", "127.0.0.1")
        port = int(os.environ.get("BENCHBOX_TRINO_PORT", "8080"))
        return host, port
    if platform_name == "firebolt":
        host = os.environ.get("BENCHBOX_FIREBOLT_HOST", "127.0.0.1")
        port = int(os.environ.get("BENCHBOX_FIREBOLT_PORT", "3473"))
        return host, port
    raise ValueError(f"Unsupported service local platform: {platform_name}")


def _service_platform_options(platform_name: str) -> list[str]:
    host, port = _service_endpoint(platform_name)

    if platform_name == "postgresql":
        options = [
            f"host={host}",
            f"port={port}",
            f"username={os.environ.get('BENCHBOX_PG_USER', 'postgres')}",
            f"database={os.environ.get('BENCHBOX_PG_DATABASE', 'benchbox')}",
            f"schema={os.environ.get('BENCHBOX_PG_SCHEMA', 'public')}",
        ]
        password = os.environ.get("BENCHBOX_PG_PASSWORD")
        if password:
            options.append(f"password={password}")
        return options

    if platform_name == "timescaledb":
        options = [
            f"host={host}",
            f"port={port}",
            f"username={os.environ.get('BENCHBOX_PG_USER', 'postgres')}",
            f"database={os.environ.get('BENCHBOX_TS_DATABASE', 'benchbox')}",
            f"schema={os.environ.get('BENCHBOX_PG_SCHEMA', 'public')}",
            "deployment_mode=self-hosted",
        ]
        password = os.environ.get("BENCHBOX_PG_PASSWORD")
        if password:
            options.append(f"password={password}")
        return options

    if platform_name in {"trino", "presto"}:
        return [
            f"host={host}",
            f"port={port}",
            f"username={os.environ.get('BENCHBOX_TRINO_USER', platform_name)}",
            f"catalog={os.environ.get('BENCHBOX_TRINO_CATALOG', 'memory')}",
            f"schema={os.environ.get('BENCHBOX_TRINO_SCHEMA', 'default')}",
        ]

    if platform_name == "firebolt":
        return [
            "deployment_mode=core",
            f"url=http://{host}:{port}",
            f"database={os.environ.get('BENCHBOX_FIREBOLT_DATABASE', 'benchbox')}",
        ]

    raise ValueError(f"Unsupported service local platform: {platform_name}")


def _phases_for_benchmark(benchmark_name: str, platform_name: str | None = None) -> list[str]:
    is_dataframe = platform_name is not None and platform_name.endswith("-df")
    phases = ["load", "power"] if is_dataframe else ["generate", "load", "power"]
    metadata = get_benchmark_metadata(benchmark_name) or {}
    include_throughput = bool(metadata.get("supports_streams")) and os.environ.get(
        "BENCHBOX_ENABLE_THROUGHPUT_MATRIX", ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    if include_throughput and platform_name in {"sqlite", "datafusion"}:
        include_throughput = False
    if include_throughput:
        phases.append("throughput")
    return phases


def _phase_keys_for_name(phase_name: str) -> tuple[str, ...]:
    mapping = {
        "generate": ("data_generation",),
        "load": ("data_loading",),
        "power": ("power_test",),
        "throughput": ("throughput_test",),
        "maintenance": ("maintenance_test", "maintenance"),
    }
    return mapping.get(phase_name, (phase_name,))


def _validate_phase_coverage(payload: dict, requested_phases: list[str]) -> None:
    config_phases = payload.get("config", {}).get("phases", [])
    if isinstance(config_phases, list):
        assert config_phases == requested_phases, (
            f"Result config phases mismatch: expected {requested_phases}, got {config_phases}"
        )

    phases_payload = payload.get("phases", {})
    for requested in requested_phases:
        phase_keys = _phase_keys_for_name(requested)
        phase_block = None
        for key in phase_keys:
            phase_block = phases_payload.get(key)
            if phase_block:
                break
        if phase_block is None and requested == "maintenance":
            maintenance_queries = [
                q for q in payload.get("queries", []) if str(q.get("test_type", "")).lower() == "maintenance"
            ]
            assert maintenance_queries, "Missing maintenance phase block and no maintenance query executions found"
            continue
        assert phase_block is not None, f"Missing phase block for '{requested}' in result payload"
        status = str(phase_block.get("status", "")).upper()
        assert status not in {"", "NOT_RUN"}, f"Phase '{requested}' was not executed (status={status})"


def _canonical_query_id(benchmark_name: str, raw_id: object) -> str:
    value = str(raw_id).strip()
    if benchmark_name in ("tpcds", "tpcds_obt"):
        match = re.match(r"^q?(\d+)", value, flags=re.IGNORECASE)
        if match:
            return str(int(match.group(1)))
    return value


def _dataframe_query_count(benchmark_name: str) -> int | None:
    from benchbox.core.dataframe.query_resolution import registry_dataframe_queries

    query_ids = {
        _canonical_query_id(benchmark_name, query.query_id) for query in registry_dataframe_queries(benchmark_name)
    }
    return len(query_ids) or None


def _validate_completion(payload: dict, benchmark_name: str, expected_query_count: int | None = None) -> None:
    summary = payload.get("summary", {})
    query_summary = summary.get("queries", {})

    total = int(query_summary.get("total", 0))
    passed = int(query_summary.get("passed", 0))
    failed = int(query_summary.get("failed", 0))

    assert total > 0, f"{benchmark_name}: no measurement queries were recorded"
    assert failed == 0, f"{benchmark_name}: query failures detected ({failed}/{total})"
    assert passed == total, f"{benchmark_name}: not all queries passed ({passed}/{total})"

    metadata = get_benchmark_metadata(benchmark_name) or {}
    expected_query_count = expected_query_count or int(metadata.get("num_queries", 0))
    measured = _measurement_queries(payload)

    unique_measurement_ids = {
        _canonical_query_id(benchmark_name, query.get("id")) for query in measured if query.get("status") == "SUCCESS"
    }

    if expected_query_count > 0:
        assert len(unique_measurement_ids) == expected_query_count, (
            f"{benchmark_name}: expected {expected_query_count} unique measurement queries, "
            f"got {len(unique_measurement_ids)}"
        )


def _expected_result_queries(payload: dict, expected_query_ids: set[str] | None) -> list[dict]:
    if not expected_query_ids:
        return _measurement_queries(payload)

    return [
        query
        for query in payload.get("queries", [])
        if str(query.get("id")) in expected_query_ids and int(query.get("stream") or 0) == 0
    ]


def _stored_value_digest(
    validator: QueryValidator,
    benchmark_name: str,
    query_id: str,
    scale_factor: float,
    stream_id: int,
) -> str | None:
    expected = validator.registry.get_expected_result(benchmark_name, str(query_id), scale_factor, stream_id)
    if expected is None or expected.value_digest is None:
        return None
    if expected.scale_factor != scale_factor:
        return None
    return expected.value_digest


def _validate_against_expected_results(
    payload: dict,
    benchmark_name: str,
    scale_factor: float,
    expected_query_ids: set[str] | None = None,
) -> None:
    from benchbox.core.results.result_digest import digests_match

    validator = QueryValidator()
    checked = 0
    checked_ids: set[str] = set()
    digest_evaluated = 0
    digest_reference_count = 0
    failures: list[str] = []

    strict = os.environ.get("BENCHBOX_STRICT_EXPECTED_RESULTS", "").strip().lower() in {"1", "true", "yes", "on"}

    for query in _expected_result_queries(payload, expected_query_ids):
        if query.get("status") != "SUCCESS":
            continue
        if query.get("rows") is None:
            continue

        stream_id = int(query.get("stream") or 0)
        validation = validator.validate_query_result(
            benchmark_type=benchmark_name,
            query_id=str(query["id"]),
            actual_row_count=int(query["rows"]),
            scale_factor=scale_factor,
            stream_id=stream_id,
        )

        if validation.validation_mode == ValidationMode.SKIP:
            continue

        checked += 1
        checked_ids.add(str(query["id"]))
        if not validation.is_valid:
            failures.append(
                f"{benchmark_name} query {validation.query_id}: expected={validation.expected_row_count}, "
                f"actual={validation.actual_row_count}, mode={validation.validation_mode.value}"
            )

        stored_digest = _stored_value_digest(validator, benchmark_name, query["id"], scale_factor, stream_id)
        if stored_digest is not None:
            digest_reference_count += 1
            emitted_digest = query.get("digest")
            if emitted_digest is not None:
                digest_evaluated += 1
                if not digests_match(stored_digest, emitted_digest):
                    failures.append(
                        f"{benchmark_name} query {validation.query_id}: VALUE DIGEST mismatch "
                        f"expected={stored_digest}, actual={emitted_digest} (row count matched -- a "
                        f"wrong-but-same-cardinality answer)"
                    )

    if strict:
        configured = set(expected_query_ids or ())
        if configured:
            assert checked_ids == configured, (
                f"strict expected-results: validated {sorted(checked_ids)} of {len(configured)} configured "
                f"{benchmark_name} queries (sf={scale_factor}, {checked} evaluations); every configured query "
                f"must produce a non-SKIP row-count validation. Missing: {sorted(configured - checked_ids)}. "
                f"Missing queries indicate missing answer files, a skipped/failed query, or scale/benchmark "
                f"drift that disarmed the gate."
            )
        else:
            assert checked > 0, (
                f"strict expected-results: no {benchmark_name} queries (sf={scale_factor}) were validated "
                f"against stored expected results"
            )
        if digest_reference_count:
            assert digest_evaluated == digest_reference_count, (
                f"strict value-digest: evaluated {digest_evaluated} of {digest_reference_count} configured "
                f"{benchmark_name} value digests (sf={scale_factor}); every configured query with a stored "
                f"reference digest must emit a value digest. A missing/unevaluated digest indicates the value "
                f"oracle was disarmed (digest emission off, query skipped, or scale/seed drift)."
            )

    assert not failures, "Expected-results validation failures (row count and/or value digest):\n" + "\n".join(failures)


@pytest.mark.integration
@pytest.mark.stress
@pytest.mark.parametrize("platform_name", LOCAL_SQL_PLATFORMS)
@pytest.mark.parametrize("benchmark_name", LOCAL_SQL_BENCHMARKS)
def test_local_platform_benchmark_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform_name: str,
    benchmark_name: str,
) -> None:
    if not is_platform_available(platform_name):
        pytest.skip(f"{platform_name} dependencies not available")
    if not _local_sql_case_enabled(platform_name, benchmark_name):
        pytest.skip(f"{platform_name}/{benchmark_name} is excluded from default stable local SQL matrix")

    if benchmark_name == "tpcds":
        monkeypatch.setenv("BENCHBOX_QUERY_VALIDATION_MODE", "loose")
        get_registry().clear_cache()

    scale_factor = _select_scale(benchmark_name, platform_name)
    phases = _phases_for_benchmark(benchmark_name, platform_name)
    query_subset = _query_subset_from_env()
    case_dir = tmp_path / f"{platform_name}_{benchmark_name}"
    case_dir.mkdir(parents=True, exist_ok=True)

    command = [
        "run",
        "--platform",
        platform_name,
        "--benchmark",
        benchmark_name,
        "--scale",
        str(scale_factor),
        "--phases",
        ",".join(phases),
        "--non-interactive",
    ]
    if query_subset:
        command.extend(["--queries", ",".join(query_subset)])
    if (
        query_subset
        and benchmark_name == "tpch"
        and platform_name == "duckdb"
        and scale_factor == 1.0
        and os.environ.get("BENCHBOX_STRICT_EXPECTED_RESULTS", "").strip().lower() in {"1", "true", "yes", "on"}
        and os.environ.get("BENCHBOX_EMIT_RESULT_DIGEST", "").strip().lower() in {"1", "true", "yes", "on"}
    ):
        seed = load_tpch_value_digest_seed()
        if not set(query_subset) <= load_tpch_value_digests(scale_factor).keys():
            raise ValueError("TPC-H digest gate query subset is not covered by the stored snapshot")
        if seed is not None:
            command.extend(["--seed", str(seed)])

    result = run_cli_command(command, cwd=case_dir, timeout=MATRIX_CASE_TIMEOUT)

    assert result.returncode == 0, (
        f"CLI failed for {platform_name}/{benchmark_name} (sf={scale_factor})\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    _, payload = _load_result_payload(case_dir, benchmark_name)
    _validate_phase_coverage(payload, phases)
    _validate_completion(payload, benchmark_name, expected_query_count=len(query_subset) or None)
    _validate_against_expected_results(
        payload,
        benchmark_name,
        scale_factor,
        expected_query_ids=set(query_subset) or None,
    )


def _load_duckdb_from_generated_files(datagen_dir: Path):
    import duckdb
    import zstandard

    from benchbox.core.tpch.schema import TABLES
    from benchbox.platforms.base.data_loading import escape_sql_string_literal

    con = duckdb.connect(":memory:")
    scratch = datagen_dir.parent / "duckdb_reference_input"
    scratch.mkdir(exist_ok=True)
    for table in TABLES:
        con.execute(table.get_create_table_sql(enable_primary_keys=False, enable_foreign_keys=False))
        chunks = sorted(datagen_dir.glob(f"{table.name}.tbl*"))
        assert chunks, f"no generated data files for {table.name} in {datagen_dir}"
        for index, chunk in enumerate(chunks):
            raw = chunk.read_bytes()
            if chunk.suffix == ".zst":
                raw = zstandard.ZstdDecompressor().stream_reader(__import__("io").BytesIO(raw)).read()
            lines = [line[:-1] if line.endswith(b"|") else line for line in raw.splitlines() if line]
            staged = scratch / f"{table.name}.{index}.csv"
            staged.write_bytes(b"\n".join(lines) + b"\n")
            escaped_path = escape_sql_string_literal(str(staged))
            con.execute(f"COPY {table.name} FROM '{escaped_path}' (DELIMITER '|', HEADER false)")
    return con


@pytest.mark.integration
@pytest.mark.stress
def test_sqlite_tpch_fixed_seed_value_parity(tmp_path: Path) -> None:
    import sqlite3

    from benchbox.core.tpch.benchmark import TPCHBenchmark
    from benchbox.platforms.sqlite import SQLiteAdapter

    if not is_platform_available("sqlite"):
        pytest.fail("sqlite is required for this case and must not be skipped")

    checkout = Path(__file__).resolve().parents[2]
    assert not tmp_path.resolve().is_relative_to(checkout), (
        "SQLite case outputs must be outside the checkout; use an external --basetemp"
    )
    case_dir = tmp_path / "sqlite_tpch_value_parity"
    case_dir.mkdir()
    output_dir = case_dir / "benchmark_runs"
    db_path = output_dir / "databases" / "tpch_sf001" / "tpch_sf001_notuning_noconstraints.sqlite"
    assert not output_dir.exists(), "case requires fresh output"

    result = run_cli_command(
        [
            "run",
            "--platform",
            "sqlite",
            "--benchmark",
            "tpch",
            "--scale",
            str(SQLITE_CASE_SCALE_FACTOR),
            "--phases",
            "generate,load,power",
            "--queries",
            ",".join(SQLITE_CASE_QUERY_IDS),
            "--seed",
            str(SQLITE_CASE_SEED),
            "--iterations",
            str(SQLITE_CASE_ITERATIONS),
            "--non-interactive",
        ],
        cwd=case_dir,
        env={"BENCHBOX_OUTPUT_DIR": str(output_dir)},
        timeout=MATRIX_CASE_TIMEOUT,
    )
    assert result.returncode == 0, f"CLI failed\nstdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"

    _, payload = _load_result_payload(case_dir, "tpch")
    _validate_phase_coverage(payload, ["generate", "load", "power"])
    check_measurement_multiset(payload.get("queries", []), expected_measurement_multiset())

    assert db_path.is_file(), "SQLite database was not created at the fresh path"
    raw = sqlite3.connect(str(db_path))
    try:
        row_counts = {name: raw.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in TPCH_TABLE_NAMES}
    finally:
        raw.close()
    check_tables_populated(row_counts)

    datagen_dir = output_dir / "datagen" / "tpch_sf001"
    reference = _load_duckdb_from_generated_files(datagen_dir)
    adapter = SQLiteAdapter(database_path=str(db_path))
    benchmark = TPCHBenchmark(scale_factor=SQLITE_CASE_SCALE_FACTOR)
    connection = adapter.create_connection()
    try:
        for query_id in SQLITE_CASE_QUERY_IDS:
            sqlite_sql = benchmark.get_query(
                int(query_id), seed=SQLITE_CASE_SEED, scale_factor=SQLITE_CASE_SCALE_FACTOR, dialect="sqlite"
            )
            duckdb_sql = benchmark.get_query(
                int(query_id), seed=SQLITE_CASE_SEED, scale_factor=SQLITE_CASE_SCALE_FACTOR, dialect="duckdb"
            )
            executed = adapter.execute_query(connection, sqlite_sql, query_id, validate_row_count=False)
            assert executed["status"] == "SUCCESS", f"Q{query_id} failed on SQLite: {executed}"
            sqlite_rows = [tuple(row) for row in executed["results"]]
            duckdb_rows = [tuple(row) for row in reference.execute(duckdb_sql).fetchall()]

            check_rows_match(duckdb_rows, sqlite_rows, query_id)

            corrupted = [tuple(row) for row in sqlite_rows]
            first = list(corrupted[0])
            column = next(i for i, v in enumerate(first) if isinstance(v, (int, float)) and not isinstance(v, bool))
            first[column] = first[column] * 1.001 + 1
            corrupted[0] = tuple(first)
            assert len(corrupted) == len(duckdb_rows)
            with pytest.raises(RequiredCaseError):
                check_rows_match(duckdb_rows, corrupted, query_id)
    finally:
        adapter.close_connection(connection) if hasattr(adapter, "close_connection") else connection.close()
        reference.close()


@pytest.mark.integration
@pytest.mark.stress
@pytest.mark.parametrize("platform_name", LOCAL_SQL_PLATFORMS)
@pytest.mark.parametrize("benchmark_name", ("tpch", "tpcds"))
def test_local_platform_maintenance_phase_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform_name: str,
    benchmark_name: str,
) -> None:
    if not _maintenance_matrix_enabled():
        pytest.skip("Set BENCHBOX_ENABLE_MAINTENANCE_MATRIX=1 to enable maintenance stress matrix tests")
    if not is_platform_available(platform_name):
        pytest.skip(f"{platform_name} dependencies not available")
    if not _local_sql_case_enabled(platform_name, benchmark_name):
        pytest.skip(f"{platform_name}/{benchmark_name} is excluded from default stable local SQL matrix")

    if benchmark_name == "tpcds":
        monkeypatch.setenv("BENCHBOX_QUERY_VALIDATION_MODE", "loose")
        get_registry().clear_cache()

    scale_factor = _select_scale(benchmark_name, platform_name)
    phases = ["generate", "load", "maintenance"]
    case_dir = tmp_path / f"{platform_name}_{benchmark_name}_maintenance"
    case_dir.mkdir(parents=True, exist_ok=True)

    result = run_cli_command(
        [
            "run",
            "--platform",
            platform_name,
            "--benchmark",
            benchmark_name,
            "--scale",
            str(scale_factor),
            "--phases",
            ",".join(phases),
            "--non-interactive",
        ],
        cwd=case_dir,
        timeout=MAINTENANCE_TIMEOUT,
    )

    assert result.returncode == 0, (
        f"CLI maintenance failed for {platform_name}/{benchmark_name} (sf={scale_factor})\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    _, payload = _load_result_payload(case_dir, benchmark_name)
    _validate_phase_coverage(payload, phases)


@pytest.mark.integration
@pytest.mark.stress
@pytest.mark.parametrize("platform_name", LOCAL_DATAFRAME_PLATFORMS)
@pytest.mark.parametrize("benchmark_name", DATAFRAME_BENCHMARKS)
def test_local_dataframe_platform_benchmark_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform_name: str,
    benchmark_name: str,
) -> None:
    if platform_name == "cudf-df" and not is_gpu_available():
        pytest.skip("cudf-df requires NVIDIA GPU with CUDA")
    if not is_dataframe_available(platform_name):
        pytest.skip(f"{platform_name} dependencies not available")
    xfail_reason = _DATAFRAME_XFAIL_COMBINATIONS.get((platform_name, benchmark_name))
    if xfail_reason:
        pytest.xfail(xfail_reason)

    if benchmark_name == "tpcds":
        monkeypatch.setenv("BENCHBOX_QUERY_VALIDATION_MODE", "loose")
        get_registry().clear_cache()

    scale_factor = _select_scale(benchmark_name, platform_name)
    phases = _phases_for_benchmark(benchmark_name, platform_name)
    case_dir = tmp_path / f"{platform_name}_{benchmark_name}"
    case_dir.mkdir(parents=True, exist_ok=True)

    result = run_cli_command(
        [
            "run",
            "--platform",
            platform_name,
            "--benchmark",
            benchmark_name,
            "--scale",
            str(scale_factor),
            "--phases",
            ",".join(phases),
            "--non-interactive",
        ],
        cwd=case_dir,
        timeout=MATRIX_CASE_TIMEOUT,
    )

    assert result.returncode == 0, (
        f"CLI failed for {platform_name}/{benchmark_name} (sf={scale_factor})\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    _, payload = _load_result_payload(case_dir, benchmark_name)
    _validate_phase_coverage(payload, phases)
    _validate_completion(payload, benchmark_name, expected_query_count=_dataframe_query_count(benchmark_name))


@pytest.mark.integration
@pytest.mark.stress
@pytest.mark.parametrize("platform_name", SERVICE_LOCAL_PLATFORMS)
def test_service_local_platform_tpch_matrix(
    tmp_path: Path,
    platform_name: str,
) -> None:
    if not _service_matrix_enabled():
        pytest.skip("Set BENCHBOX_SERVICE_LOCAL_MATRIX=1 to enable service-backed local matrix tests")
    if not is_platform_available(platform_name):
        pytest.skip(f"{platform_name} dependencies not available")

    host, port = _service_endpoint(platform_name)
    if not _tcp_open(host, port):
        pytest.skip(f"{platform_name} service not reachable at {host}:{port}")

    benchmark_name = "tpch"
    scale_factor = _select_scale(benchmark_name, platform_name)
    phases = _phases_for_benchmark(benchmark_name, platform_name)
    case_dir = tmp_path / f"{platform_name}_{benchmark_name}_service_local"
    case_dir.mkdir(parents=True, exist_ok=True)

    args = [
        "run",
        "--platform",
        platform_name,
        "--benchmark",
        benchmark_name,
        "--scale",
        str(scale_factor),
        "--phases",
        ",".join(phases),
        "--non-interactive",
    ]
    for option in _service_platform_options(platform_name):
        args.extend(["--platform-option", option])

    result = run_cli_command(args, cwd=case_dir, timeout=MATRIX_CASE_TIMEOUT)

    assert result.returncode == 0, (
        f"CLI failed for {platform_name}/{benchmark_name} service-local run\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )

    _, payload = _load_result_payload(case_dir, benchmark_name)
    _validate_phase_coverage(payload, phases)
    _validate_completion(payload, benchmark_name)
    _validate_against_expected_results(payload, benchmark_name, scale_factor)


