# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import importlib
import inspect
import logging
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from benchbox.core.benchmark_registry import (
    get_benchmark_default_scale,
    get_public_benchmark_class,
)
from benchbox.utils.dependencies import BENCHMARK_LOAD_ERRORS

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]

REFERENCE_DIALECT = "datafusion"
REFERENCE_DATAFRAME_FAMILY = "expression"

_TEMPLATE_TOKEN_RE = re.compile(r"(?<![\w'])(:[0-9]+\b|:[xon]\b|\{[a-zA-Z_]\w*\})")


@dataclass(frozen=True)
class SqlRender:
    sql: str
    dialect: str
    is_template: bool


@dataclass(frozen=True)
class DataFrameRender:
    source: str
    family: str
    query_name: str | None
    description: str | None


@cache
def _benchmark_instance(benchmark_id: str) -> Any | None:
    try:
        cls = get_public_benchmark_class(benchmark_id)
    except BENCHMARK_LOAD_ERRORS:
        logger.debug("query_catalog: cannot load benchmark %r", benchmark_id, exc_info=True)
        return None
    if cls is None:
        return None
    scale = get_benchmark_default_scale(benchmark_id)
    for kwargs in ({"scale_factor": scale, "seed": 0}, {"scale_factor": scale}):
        try:
            return cls(**kwargs)
        except TypeError:
            continue
        except Exception:
            logger.debug("query_catalog: cannot instantiate benchmark %r", benchmark_id, exc_info=True)
            return None
    return None


@cache
def _query_dict(benchmark_id: str, dialect: str | None) -> dict[str, str]:
    bm = _benchmark_instance(benchmark_id)
    if bm is None:
        return {}
    if dialect is not None:
        try:
            queries = bm.get_queries(dialect=dialect)
        except TypeError:
            return {}
        except Exception:
            logger.debug("query_catalog: get_queries(dialect=%r) failed for %r", dialect, benchmark_id, exc_info=True)
            return {}
        return {str(k): v for k, v in (queries or {}).items()}
    try:
        queries = bm.get_queries()
    except Exception:
        return {}
    return {str(k): v for k, v in (queries or {}).items()}


def _normalize_query_key(query_id: str) -> str:
    match = re.fullmatch(r"[Qq](\d.*)", query_id)
    return match.group(1) if match else query_id.lower()


def _get_query_accepts_dialect(bm: Any) -> bool:
    try:
        params = inspect.signature(bm.get_query).parameters
    except (TypeError, ValueError):
        return False
    return "dialect" in params


def _get_query_single(benchmark_id: str, query_id: str, dialect: str | None) -> str | None:
    bm = _benchmark_instance(benchmark_id)
    if bm is None:
        return None
    candidates: list[Any] = [query_id]
    if query_id.isdigit():
        candidates.append(int(query_id))
    stripped = query_id[1:] if query_id[:1] in {"Q", "q"} else f"Q{query_id}"
    candidates.append(stripped)
    kwargs = {"dialect": dialect} if dialect else {}
    for candidate in candidates:
        try:
            sql = bm.get_query(candidate, **kwargs)
        except (KeyError, ValueError, TypeError):
            continue
        except Exception:
            logger.debug("query_catalog: get_query(%r) failed", candidate, exc_info=True)
            continue
        if sql:
            return sql
    return None


@cache
def _raw_query_keys(benchmark_id: str) -> tuple[Any, ...]:
    bm = _benchmark_instance(benchmark_id)
    if bm is None:
        return ()
    try:
        return tuple(bm.get_queries().keys())
    except Exception:
        return ()


def list_query_ids(benchmark_id: str) -> list[str]:
    return list(_query_dict(benchmark_id, None).keys())


def native_query_key(benchmark_id: str, query_id: str) -> Any:
    keys = _raw_query_keys(benchmark_id)
    if query_id in keys:
        return query_id
    if query_id.isdigit() and int(query_id) in keys:
        return int(query_id)
    wanted = _normalize_query_key(query_id)
    for key in keys:
        if _normalize_query_key(str(key)) == wanted:
            return key
    return query_id


def _collect_translation_targets(bm: Any) -> list[Any]:
    objs: list[Any] = []
    if bm is not None:
        objs.append(bm)
        impl = getattr(bm, "_impl", None)
        if impl is not None:
            objs.append(impl)
            qm = getattr(impl, "query_manager", None)
            if qm is not None:
                objs.append(qm)
        qm = getattr(bm, "query_manager", None)
        if qm is not None and qm not in objs:
            objs.append(qm)
    return objs


def _benchmark_supports_dialect(bm: Any, dialect: str | None) -> bool:
    if bm is None or dialect is None:
        return False
    dialect_lower = dialect.lower().strip()
    objs = _collect_translation_targets(bm)
    for obj in objs:
        if hasattr(obj, "supported_dialects"):
            try:
                supported = [str(d).lower().strip() for d in obj.supported_dialects()]
                return dialect_lower in supported
            except Exception:
                pass
    for obj in objs:
        if hasattr(obj, "translate_query_text") or hasattr(obj, "has_dialect_variant"):
            return True
    return False


def supports_dialect_translation(benchmark_id: str, dialect: str | None = None) -> bool:
    bm = _benchmark_instance(benchmark_id)
    if bm is None:
        return False
    if dialect is not None:
        return _benchmark_supports_dialect(bm, dialect)
    objs = _collect_translation_targets(bm)
    for obj in objs:
        if hasattr(obj, "supported_dialects") and bool(obj.supported_dialects()):
            return True
        if hasattr(obj, "translate_query_text") or hasattr(obj, "has_dialect_variant"):
            return True
    return False


def _lookup(queries: dict[str, str], query_id: str) -> str | None:
    if query_id in queries:
        return queries[query_id]
    wanted = _normalize_query_key(query_id)
    for key, sql in queries.items():
        if _normalize_query_key(key) == wanted:
            return sql
    return None


def get_sql_render(
    benchmark_id: str,
    query_id: str,
    *,
    dialect: str | None = REFERENCE_DIALECT,
    bulk: bool = False,
) -> SqlRender | None:
    bm = _benchmark_instance(benchmark_id)
    if bm is None:
        return None

    if dialect is not None and _benchmark_supports_dialect(bm, dialect):
        if not bulk and _get_query_accepts_dialect(bm):
            sql = _get_query_single(benchmark_id, query_id, dialect)
            if sql:
                return _make_sql_render(sql, dialect)
        sql = _lookup(_query_dict(benchmark_id, dialect), query_id)
        if sql:
            return _make_sql_render(sql, dialect)

    sql = _lookup(_query_dict(benchmark_id, None), query_id)
    if sql:
        return _make_sql_render(sql, "default")

    sql = _get_query_single(benchmark_id, query_id, None)
    if sql:
        return _make_sql_render(sql, "default")

    return None


def _make_sql_render(sql: str, dialect: str) -> SqlRender:
    text = sql.strip()
    return SqlRender(sql=text, dialect=dialect, is_template=bool(_TEMPLATE_TOKEN_RE.search(text)))


def _dataframe_registry(benchmark_id: str) -> list[Any]:
    importlib.import_module("benchbox.core.dataframe")
    from benchbox.core.dataframe.query_resolution import registry_dataframe_queries

    try:
        return registry_dataframe_queries(benchmark_id)
    except Exception:
        logger.debug("query_catalog: no DataFrame registry for %r", benchmark_id, exc_info=True)
        return []


def get_dataframe_query(benchmark_id: str, query_id: str) -> Any | None:
    wanted = _normalize_query_key(query_id)
    for query in _dataframe_registry(benchmark_id):
        if _normalize_query_key(str(query.query_id)) == wanted:
            return query
    return None


def get_dataframe_render(
    benchmark_id: str,
    query_id: str,
    *,
    family: str = REFERENCE_DATAFRAME_FAMILY,
) -> DataFrameRender | None:
    query = get_dataframe_query(benchmark_id, query_id)
    if query is None:
        return None

    impl, resolved_family = _resolve_impl(query, family)
    if impl is None:
        return None
    try:
        source = inspect.getsource(impl).strip()
    except (OSError, TypeError):
        return None

    return DataFrameRender(
        source=source,
        family=resolved_family,
        query_name=getattr(query, "query_name", None),
        description=getattr(query, "description", None),
    )


def _resolve_impl(query: Any, family: str) -> tuple[Any | None, str]:
    expr = getattr(query, "expression_impl", None)
    pandas = getattr(query, "pandas_impl", None)
    if family == "pandas" and pandas is not None:
        return pandas, "pandas"
    if family == "expression" and expr is not None:
        return expr, "expression"
    if expr is not None:
        return expr, "expression"
    if pandas is not None:
        return pandas, "pandas"
    return None, family


def _query_info(benchmark_id: str, query_id: str) -> dict[str, Any] | None:
    bm = _benchmark_instance(benchmark_id)
    if bm is None or not hasattr(bm, "get_query_info"):
        return None
    for candidate in (query_id, f"Q{query_id}", query_id.lstrip("Qq")):
        try:
            info = bm.get_query_info(candidate)
        except Exception:
            continue
        if isinstance(info, dict):
            return info
    return None


def query_display_name(benchmark_id: str, query_id: str) -> str | None:
    info = _query_info(benchmark_id, query_id)
    if info and info.get("name"):
        return str(info["name"])
    query = get_dataframe_query(benchmark_id, query_id)
    if query is not None and getattr(query, "query_name", None):
        return str(query.query_name)
    return None


def query_description(benchmark_id: str, query_id: str) -> str | None:
    info = _query_info(benchmark_id, query_id)
    if info and info.get("description"):
        return str(info["description"])
    query = get_dataframe_query(benchmark_id, query_id)
    if query is not None and getattr(query, "description", None):
        return str(query.description)
    return None


def query_groups(benchmark_id: str) -> dict[str, list[str]] | None:
    ids = list_query_ids(benchmark_id)
    if not ids:
        return None
    known = set(ids)
    bm = _benchmark_instance(benchmark_id)
    raw: dict[str, list[str]] = {}

    categories = None
    if bm is not None and hasattr(bm, "get_query_categories"):
        try:
            categories = bm.get_query_categories()
        except Exception:
            categories = None

    if isinstance(categories, dict) and categories:
        raw = {str(name): [str(q) for q in members] for name, members in categories.items()}
    elif isinstance(categories, (list, tuple)) and categories and hasattr(bm, "get_queries_by_category"):
        for name in categories:
            try:
                members = bm.get_queries_by_category(name)
            except Exception:
                continue
            keys = list(members.keys()) if isinstance(members, dict) else list(members)
            if keys:
                raw[str(name)] = [str(q) for q in keys]
    else:
        by_info: dict[str, list[str]] = {}
        for query_id in ids:
            info = _query_info(benchmark_id, query_id)
            category = info.get("category") if info else None
            if category:
                by_info.setdefault(str(category), []).append(query_id)
        raw = by_info

    if not raw:
        return None

    grouped: dict[str, list[str]] = {}
    seen: set[str] = set()
    for name, members in raw.items():
        member_set = set(members)
        kept = [q for q in ids if q in member_set and q in known and q not in seen]
        if kept:
            grouped[name] = kept
            seen.update(kept)
    leftover = [q for q in ids if q not in seen]
    if leftover:
        grouped["other"] = leftover
    return grouped or None


def query_source_path(benchmark_id: str, query_id: str) -> str | None:
    if benchmark_id in {"tpch", "tpch_skew"} and query_id.isdigit():
        path = f"benchbox/_binaries/tpc-h/templates/queries/{query_id}.sql"
        if (_REPO_ROOT / path).exists():
            return path
    if benchmark_id == "tpcds" and query_id.isdigit():
        path = f"_sources/tpc-ds/query_templates/query{query_id}.tpl"
        if (_REPO_ROOT / path).exists():
            return path
    if benchmark_id == "tpchavoc":
        base_id = query_id.split("_")[0]
        if base_id.isdigit():
            path = f"benchbox/core/tpchavoc/variant_sets/q{int(base_id):02d}.py"
            if (_REPO_ROOT / path).exists():
                return path
    if benchmark_id == "tpcdi":
        qid = query_id.upper()
        if qid.startswith("AQ"):
            path = "benchbox/core/tpcdi/query_analytics.py"
        elif qid.startswith("EQ"):
            path = "benchbox/core/tpcdi/query_etl.py"
        elif qid.startswith("VQ"):
            path = "benchbox/core/tpcdi/query_validation.py"
        else:
            path = "benchbox/core/tpcdi/queries.py"
        if (_REPO_ROOT / path).exists():
            return path

    candidates: list[str] = [
        f"benchbox/core/{benchmark_id}/queries.py",
        f"benchbox/core/{benchmark_id}/operations.py",
    ]
    for rel in candidates:
        if (_REPO_ROOT / rel).exists():
            return rel
    return None
