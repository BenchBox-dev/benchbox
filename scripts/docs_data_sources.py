from __future__ import annotations

import importlib
import logging
import pkgutil
import re

import benchbox.sql_compat.rules as _rules_pkg
from benchbox.core.benchmark_registry import get_benchmark_class_name, get_core_benchmark_class_name
from benchbox.core.query_catalog import query_groups
from benchbox.core.tpcds.c_tools import SUBSCALE_TEMPLATE_OVERRIDES
from benchbox.core.tpcds.qualification.classification import KNOWN as TPCDS_KNOWN_DIFFERENCES, RETURNS_DIFFERENCE

logging.getLogger("sqlglot").setLevel(logging.ERROR)

_TPC_DERIVED = {"tpch", "tpcds", "tpcdi", "tpch_skew", "tpchavoc", "tpcds_obt", "datavault"}
_TPC_NOTICE = (
    "> TPC Benchmark, TPC-H, TPC-DS, and TPC-DI are trademarks of the Transaction "
    "Processing Performance Council. Query text shown here is derived from TPC "
    "materials."
)

_SEED_DRIVEN = {"tpch", "tpcds", "tpch_skew", "tpchavoc"}

_OVERVIEW_SLUG_OVERRIDES = {
    "tpch": "tpc-h",
    "tpcds": "tpc-ds",
    "tpcdi": "tpc-di",
    "tpcds_obt": "tpc-ds-obt",
    "tpchavoc": "tpc-havoc",
    "joinorder": "join-order",
}

_GROUP_THRESHOLD = 30
_BUCKET_SIZE = 25


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "query"


def _query_slug(query_id: str) -> str:
    slug = _slugify(query_id)
    return f"q{slug}" if slug[:1].isdigit() else slug


def _query_label(query_id: str) -> str:
    if query_id[:1].isdigit():
        return f"Q{query_id}"
    if query_id[:1] in {"q", "Q"} and query_id[1:2].isdigit():
        return "Q" + query_id[1:]
    return query_id


def _overview_slug(benchmark_id: str) -> str:
    return _OVERVIEW_SLUG_OVERRIDES.get(benchmark_id, benchmark_id.replace("_", "-"))


def _prettify_sql(sql: str, dialect: str) -> str:
    from benchbox.utils.dialect_utils import translate_sql_query

    if dialect == "default":
        return sql
    pretty = translate_sql_query(sql, dialect, source_dialect=dialect, identify=False, strict=False, pretty=True)
    return pretty if pretty.strip() else sql


def _recipe_class(benchmark_id: str) -> tuple[str, str]:
    import benchbox

    public = get_benchmark_class_name(benchmark_id)
    if public and hasattr(benchbox, public):
        return f"from benchbox import {public}", public
    core = get_core_benchmark_class_name(benchmark_id) or public or benchmark_id
    return f"from benchbox.core.{benchmark_id}.benchmark import {core}", core


_TPCDS_PARITY_DOC_URL = "https://benchbox.dev/docs/development/tpcds-dataframe-parity.html"
_TPCDS_PARITY_NOTES_URL = f"{_TPCDS_PARITY_DOC_URL}#q85-and-q93-reason-table-size"
_TPCDS_SUBSCALE_NOTES_URL = f"{_TPCDS_PARITY_DOC_URL}#q93-below-sf-1-reason-draw"


def _answer_set_note(benchmark_id: str, query_id: str) -> list[str]:
    if benchmark_id != "tpcds":
        return []
    known = TPCDS_KNOWN_DIFFERENCES.get(query_id)
    if known is None or RETURNS_DIFFERENCE not in known.classes:
        return []
    return [
        "> **Results differ from the official TPC-DS answer set.** BenchBox generates data with the official "
        "TPC-DS v4.0.0 dsdgen, whose `reason` table has 75 rows from SF 1 up. The official answer files were "
        "produced from a 35-row table and were not regenerated for v4.0.0, so this query's rows do not match them. "
        "Every platform runs on the same generated data, so results remain comparable across platforms. "
        f"See the [TPC-DS reason table notes]({_TPCDS_PARITY_NOTES_URL}).",
        "",
    ]


def _subscale_note(benchmark_id: str, query_id: str) -> list[str]:
    if benchmark_id != "tpcds" or f"query{query_id}.tpl" not in SUBSCALE_TEMPLATE_OVERRIDES:
        return []
    return [
        "> **Below SF 1 the reason is drawn from the generated rows.** dsdgen shrinks the `reason` table below "
        "SF 1 (7 rows at SF 0.1, 1 at SF 0.01), but dsqgen draws this query's reason from all 75 descriptions, "
        "so the official draw usually matches no row. Below SF 1, BenchBox draws the reason from the rows that "
        "exist at that scale, deterministically for a given seed. From SF 1 up the query text is exactly the "
        f"official one. See the [sub-SF 1 reason draw notes]({_TPCDS_SUBSCALE_NOTES_URL}).",
        "",
    ]


def _grouping(benchmark_id: str, query_ids: list[str]) -> dict[str, list[str]] | None:
    if len(query_ids) <= _GROUP_THRESHOLD:
        return None
    groups = query_groups(benchmark_id)
    if groups:
        return groups
    buckets: dict[str, list[str]] = {}
    for start in range(0, len(query_ids), _BUCKET_SIZE):
        chunk = query_ids[start : start + _BUCKET_SIZE]
        buckets[f"queries {start + 1}-{start + len(chunk)}"] = chunk
    return buckets


def _load_all_rules() -> None:
    failed: list[str] = []
    for _, mod_name, ispkg in pkgutil.walk_packages(_rules_pkg.__path__, _rules_pkg.__name__ + "."):
        if ispkg:
            continue
        try:
            importlib.import_module(mod_name)
        except Exception as exc:
            failed.append(f"{mod_name}: {exc}")
    if failed:
        raise RuntimeError(
            "Cannot export compat data - failed to import rule module(s):\n" + "\n".join(f"  {m}" for m in failed)
        )


def _scope_label(benchmark: str | None, query_id: str | None) -> str:
    if benchmark is None and query_id is None:
        return "platform-wide"
    if query_id is None:
        return f"benchmark={benchmark}"
    return f"benchmark={benchmark}, query={query_id}"


_ADAPTER_NAMES = {"azure-synapse": "synapse", "fabric-warehouse": "fabric_dw"}


def _adapter_has_introspector(platform: str) -> bool | None:
    from benchbox.core.platform_registry import PlatformRegistry
    from benchbox.platforms.base.adapter import PlatformAdapter

    for candidate in (_ADAPTER_NAMES.get(platform, platform), platform.replace("-", "_"), platform.split("-", 1)[-1]):
        try:
            adapter_class = PlatformRegistry.get_adapter_class(candidate)
        except Exception:
            continue
        return adapter_class.get_tuning_introspector is not PlatformAdapter.get_tuning_introspector
    return None
