from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

import benchbox.utils.dialect_utils as dialect_utils
from benchbox.core.benchmark_registry import get_all_benchmarks, get_benchmark_surface
from benchbox.core.query_catalog import _benchmark_instance
from benchbox.core.query_catalog_base import is_native_dialect

pytestmark = [pytest.mark.unit, pytest.mark.fast]

DIALECTS = (
    "duckdb",
    "sqlite",
    "datafusion",
    "postgres",
    "clickhouse",
    "snowflake",
    "bigquery",
    "databricks",
    "spark",
    "starrocks",
    "doris",
)

DUCKDB_SOURCE_BENCHMARKS = (
    "tsbs_devops",
    "datavault",
    "nyctaxi",
    "flightdata",
    "vector_search",
    "write_primitives",
    "metadata_primitives",
)

ALLOWLIST_PATH = Path(__file__).with_name("translation_bypass_allowlist.json")


def _allowlist() -> dict[str, dict]:
    return json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))


def _implementation(benchmark):
    return getattr(benchmark, "_impl", benchmark)


def _install_spy(monkeypatch, on_result):
    original = dialect_utils.translate_sql_query

    def spy(*args, **kwargs):
        result = original(*args, **kwargs)
        on_result(args, kwargs, result)
        return result

    for module in list(sys.modules.values()):
        if getattr(module, "translate_sql_query", None) is original:
            monkeypatch.setattr(module, "translate_sql_query", spy)


@pytest.mark.parametrize("benchmark_id", DUCKDB_SOURCE_BENCHMARKS)
def test_hand_written_benchmarks_declare_duckdb_source_dialect(benchmark_id):
    benchmark = _implementation(_benchmark_instance(benchmark_id))
    assert benchmark._source_dialect == "duckdb"
    assert benchmark._native_source_passthrough is True


def test_read_primitives_declares_netezza_source_dialect():
    benchmark = _implementation(_benchmark_instance("read_primitives"))
    assert benchmark._source_dialect == "netezza"
    assert benchmark._native_source_passthrough is False


@pytest.mark.parametrize("benchmark_id", ("flightdata", "tsbs_devops", "datavault"))
def test_duckdb_source_translates_every_query_for_non_duckdb_dialects(monkeypatch, benchmark_id):
    sources = []
    _install_spy(
        monkeypatch,
        lambda args, kwargs, result: sources.append(kwargs.get("source_dialect", args[2] if len(args) > 2 else None)),
    )

    benchmark = _benchmark_instance(benchmark_id)
    for dialect in DIALECTS:
        sources.clear()
        queries = benchmark.get_queries(dialect=dialect)
        if is_native_dialect("duckdb", dialect):
            assert sources == [], dialect
        else:
            assert len(sources) == len(queries), dialect
            assert set(sources) == {"duckdb"}, dialect


@pytest.fixture
def quiet_logging():
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(previous)


def _observed_bypasses(monkeypatch) -> dict[str, dict[str, set[str]]]:
    produced: set[str] = set()
    _install_spy(monkeypatch, lambda args, kwargs, result: produced.add(result))

    observed: dict[str, dict[str, set[str]]] = {}
    for benchmark_id in sorted(get_all_benchmarks()):
        if get_benchmark_surface(benchmark_id) != "public":
            continue
        benchmark = _benchmark_instance(benchmark_id)
        assert benchmark is not None, benchmark_id
        implementation = _implementation(benchmark)
        native_source = (
            implementation._source_dialect if getattr(implementation, "_native_source_passthrough", False) else None
        )
        per_dialect: dict[str, set[str]] = {}
        for dialect in DIALECTS:
            if native_source is not None and is_native_dialect(native_source, dialect):
                continue
            produced.clear()
            queries = benchmark.get_queries(dialect=dialect)
            bypassed = {str(query_id) for query_id, sql in queries.items() if sql not in produced}
            if bypassed:
                per_dialect[dialect] = bypassed
        if per_dialect:
            observed[benchmark_id] = per_dialect
    return observed


def _allowed_bypasses() -> dict[str, dict[str, set[str]]]:
    return {
        benchmark_id: {dialect: set(ids) for dialect, ids in entry["dialects"].items()}
        for benchmark_id, entry in _allowlist().items()
    }


def _pairs(bypasses: dict[str, dict[str, set[str]]]) -> set[tuple[str, str, str]]:
    return {
        (benchmark_id, dialect, query_id)
        for benchmark_id, per_dialect in bypasses.items()
        for dialect, ids in per_dialect.items()
        for query_id in ids
    }


def test_allowlist_entries_carry_reasons():
    for benchmark_id, entry in _allowlist().items():
        assert entry["reason"].strip(), benchmark_id
        assert entry["dialects"], benchmark_id
        assert all(entry["dialects"].values()), benchmark_id
        assert set(entry["dialects"]) <= set(DIALECTS), benchmark_id


def test_no_benchmark_bypasses_translation_without_an_allowlisted_reason(monkeypatch, quiet_logging):
    observed = _pairs(_observed_bypasses(monkeypatch))
    allowed = _pairs(_allowed_bypasses())

    unexplained = sorted(observed - allowed)
    stale = sorted(allowed - observed)

    assert unexplained == [], (
        f"(benchmark, dialect, query) reaches a platform without translate_sql_query: {unexplained}"
    )
    assert stale == [], f"allowlist entries no longer bypass translation: {stale}"
