from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

CLI_DESCRIPTION = "Export the data behind the site-rendered query, compat and tuning pages as deterministic JSON."
SCHEMA_VERSION = 1

_SCRIPT_CACHE: dict[str, ModuleType] = {}


def _script(name: str) -> ModuleType:
    module = _SCRIPT_CACHE.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load scripts/{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        _SCRIPT_CACHE[name] = module
    return module


def _sql_rendering(render, *, prettify) -> dict:
    return {
        "dialect": render.dialect,
        "is_template": render.is_template,
        "sql": render.sql,
        "pretty_sql": prettify(render.sql, render.dialect),
    }


def _dataframe_renderings(benchmark_id: str, query_id: str) -> list[dict]:
    from benchbox.core.query_catalog import get_dataframe_render

    seen: dict[str, str] = {}
    for family in ("expression", "pandas"):
        render = get_dataframe_render(benchmark_id, query_id, family=family)
        if render is not None and render.family not in seen:
            seen[render.family] = render.source
    return [{"family": family, "source": source} for family, source in sorted(seen.items())]


def _query_record(
    sources: ModuleType, benchmark_id: str, display: str, query_id: str, groups: dict[str, list[str]]
) -> dict:
    from benchbox.core.query_catalog import (
        REFERENCE_DIALECT,
        get_dataframe_render,
        get_sql_render,
        native_query_key,
        query_description,
        query_display_name,
        query_source_path,
    )

    native_key = native_query_key(benchmark_id, query_id)
    label = sources._query_label(query_id)
    name = query_display_name(benchmark_id, query_id)
    if label == query_id and name:
        heading = f"{display} — {name}"
    else:
        heading = f"{display} {label}" + (f" — {name}" if name else "")

    render = get_sql_render(benchmark_id, query_id, bulk=True)
    native = get_sql_render(benchmark_id, query_id, dialect=None, bulk=True)
    sql = _sql_rendering(render, prettify=sources._prettify_sql) if render is not None else None
    sql_native = None
    if native is not None and (render is None or native.dialect != render.dialect):
        sql_native = _sql_rendering(native, prettify=sources._prettify_sql)

    displayed_df = get_dataframe_render(benchmark_id, query_id)
    answer_set = sources._answer_set_note(benchmark_id, query_id)
    subscale = sources._subscale_note(benchmark_id, query_id)

    return {
        "id": query_id,
        "slug": sources._query_slug(query_id),
        "label": label,
        "display_name": name,
        "heading": heading,
        "description": query_description(benchmark_id, query_id),
        "groups": [group for group, members in groups.items() if query_id in members],
        "native_key": native_key,
        "native_key_repr": repr(native_key),
        "source_path": query_source_path(benchmark_id, query_id),
        "seed_driven": benchmark_id in sources._SEED_DRIVEN and query_id.isdigit(),
        "answer_set_note": answer_set[0] if answer_set else None,
        "subscale_note": subscale[0] if subscale else None,
        "sql": sql,
        "sql_native": sql_native,
        "reference_dialect_label": (
            "DataFusion" if render is not None and render.dialect == REFERENCE_DIALECT else None
        ),
        "dataframe": (
            {"family": displayed_df.family, "source": displayed_df.source} if displayed_df is not None else None
        ),
        "dataframe_renderings": _dataframe_renderings(benchmark_id, query_id),
    }


def _benchmark_record(sources: ModuleType, benchmark_id: str, display: str, query_ids: list[str]) -> dict:
    from benchbox.core.benchmark_registry import get_benchmark_default_scale
    from benchbox.core.query_catalog import supports_dialect_translation

    grouping = sources._grouping(benchmark_id, query_ids)
    groups = grouping or {}
    import_line, class_name = sources._recipe_class(benchmark_id)
    return {
        "id": benchmark_id,
        "display_name": display,
        "overview_slug": sources._overview_slug(benchmark_id),
        "query_count": len(query_ids),
        "tpc_derived": benchmark_id in sources._TPC_DERIVED,
        "seed_driven": benchmark_id in sources._SEED_DRIVEN,
        "supports_dialect_translation": supports_dialect_translation(benchmark_id),
        "default_scale": get_benchmark_default_scale(benchmark_id),
        "recipe": {"import_line": import_line, "class_name": class_name},
        "groups": (
            [{"name": name, "slug": sources._slugify(name), "query_ids": members} for name, members in groups.items()]
            if grouping is not None
            else None
        ),
        "queries": [_query_record(sources, benchmark_id, display, query_id, groups) for query_id in query_ids],
    }


def export_queries(benchmarks: set[str] | None = None) -> dict:
    from benchbox.core.benchmark_registry import get_all_benchmarks, get_benchmark_surface
    from benchbox.core.query_catalog import REFERENCE_DIALECT, list_query_ids

    sources = _script("docs_data_sources")
    records = []
    for benchmark_id, meta in sorted(get_all_benchmarks().items()):
        if get_benchmark_surface(benchmark_id) != "public":
            continue
        if benchmarks is not None and benchmark_id not in benchmarks:
            continue
        query_ids = list_query_ids(benchmark_id)
        if not query_ids:
            continue
        records.append(_benchmark_record(sources, benchmark_id, meta.get("display_name", benchmark_id), query_ids))
    return {
        "reference_dialect": REFERENCE_DIALECT,
        "tpc_notice": sources._TPC_NOTICE,
        "group_threshold": sources._GROUP_THRESHOLD,
        "bucket_size": sources._BUCKET_SIZE,
        "tpcds_parity_notes_url": sources._TPCDS_PARITY_NOTES_URL,
        "tpcds_subscale_notes_url": sources._TPCDS_SUBSCALE_NOTES_URL,
        "benchmarks": records,
    }


def export_compat() -> dict:
    from benchbox.sql_compat.context import Phase
    from benchbox.sql_compat.decision import SupportLevel
    from benchbox.sql_compat.registry import REGISTRY

    sources = _script("docs_data_sources")
    sources._load_all_rules()
    phase_order = [phase.value for phase in Phase]
    entries = []
    for (phase, platform, benchmark, query_id), entry in REGISTRY.all_rules():
        decision = entry.decision
        entries.append(
            {
                "platform": platform,
                "phase": phase.value,
                "benchmark": benchmark,
                "query_id": query_id,
                "scope": sources._scope_label(benchmark, query_id),
                "action": decision.action.value,
                "support_level": decision.support_level.value,
                "failure_mode": decision.failure_mode.value,
                "rule_id": decision.rule_id,
                "reason": decision.reason,
            }
        )
    entries.sort(
        key=lambda r: (
            r["platform"],
            phase_order.index(r["phase"]) if r["phase"] in phase_order else len(phase_order),
            r["scope"],
            r["rule_id"],
        )
    )
    return {
        "phase_order": phase_order,
        "will_not_run_levels": sorted([SupportLevel.BLOCKED.value, SupportLevel.SKIPPED_QUERY.value]),
        "runs_with_caveats_levels": sorted([SupportLevel.SKIPPED_DDL_FRAGMENT.value, SupportLevel.INFORMATIONAL.value]),
        "rules": entries,
    }


def export_tuning() -> dict:
    from benchbox.core.tuning.capability_registry import (
        PLATFORM_ALIASES,
        PLATFORM_TUNING_CAPABILITIES,
        SESSION_CONFIG_CAPABILITIES,
        is_not_rendered_at_execution,
    )
    from benchbox.core.tuning.packaged_templates import list_packaged_templates

    sources = _script("docs_data_sources")
    platforms = []
    for platform in sorted(PLATFORM_TUNING_CAPABILITIES.keys() | SESSION_CONFIG_CAPABILITIES.keys()):
        paths = list_packaged_templates(platform).get(platform, [])
        templates = sorted({p.name.removesuffix("_tuned.yaml") for p in paths if p.name.endswith("_tuned.yaml")})
        applied: list[dict] = []
        not_applied: list[str] = []
        for tuning_type, capability in sorted(
            PLATFORM_TUNING_CAPABILITIES.get(platform, {}).items(), key=lambda item: item[0].value
        ):
            if is_not_rendered_at_execution(capability):
                not_applied.append(tuning_type.value)
            else:
                applied.append(
                    {"type": tuning_type.value, "requires_sorted_ingestion": bool(capability.requires_sorted_ingestion)}
                )
        introspector = sources._adapter_has_introspector(platform)
        platforms.append(
            {
                "platform": platform,
                "shipped_templates": templates,
                "applied": applied,
                "not_applied": not_applied,
                "session_config": platform in SESSION_CONFIG_CAPABILITIES,
                "catalog_introspector": introspector,
                "can_reach_applied_verified": bool(introspector and applied),
            }
        )
    return {"aliases": dict(sorted(PLATFORM_ALIASES.items())), "platforms": platforms}


def build_docs_data(benchmarks: set[str] | None = None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "queries": export_queries(benchmarks),
        "compat": export_compat(),
        "tuning": export_tuning(),
    }


def render_json(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--output", type=Path, help="Write the export here; stdout when omitted.")
    parser.add_argument("--check", action="store_true", help="Exit non-zero if --output differs from a fresh export.")
    args = parser.parse_args(argv)

    if args.check and args.output is None:
        parser.error("--check requires --output")
    text = render_json(build_docs_data())
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current != text:
            sys.stderr.write(f"{args.output} is out of date; rerun scripts/export_docs_data.py without --check\n")
            return 1
        print(f"{args.output} matches the catalog")
        return 0
    if args.output is None:
        sys.stdout.write(text)
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
