from __future__ import annotations

import ast
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator, Literal

from benchbox.utils.printing import emit

CompatKind = Literal["skip", "rewrite", "ddl", "type_mapping", "session_setting", "benchmark_gate"]
DdlGovernanceStatus = Literal[
    "registered_runtime_behavior",
    "registered_governance_only_intent",
    "explicit_exemption",
    "unregistered_detected_behavior",
    "unknown_uninspectable_behavior",
]

_DIALECT_PLATFORMS = {
    "duckdb",
    "clickhouse",
    "starrocks",
    "datafusion",
    "snowflake",
    "bigquery",
    "spark",
    "redshift",
    "postgresql",
    "postgres",
    "sqlite",
    "mysql",
    "trino",
    "timescale",
    "timescaledb",
    "influxdb",
    "lakesail",
    "polars",
    "pandas",
    "motherduck",
    "firebolt",
    "netezza",
    "greenplum",
    "vertica",
}

_SKIP_FUNC_NAMES = {"get_platform_skip_queries", "get_df_platform_skip_queries"}
_DDL_FUNC_NAMES = {"_supports_primary_keys"}
_REWRITE_FUNC_NAMES = {
    "_inject_missing_subquery_aliases",
    "add_subquery_aliases",
    "add_query_settings",
}
_DDL_OPTIMIZE_FUNC_NAMES = {
    "_optimize_table_definition",
    "_transform_create_statement",
    "_inject_doris_ddl_clauses",
}
_DDL_REWRITE_FUNCTION_NAME_FRAGMENTS = (
    "create",
    "ddl",
    "external_table",
    "table",
)
_DDL_TEXT_TRANSFORM_CALL_NAMES = {
    "replace",
    "sub",
    "strip_foreign_keys",
    "strip_with_properties",
}
_DDL_REWRITE_OUTPUT_MARKERS = (
    "create external table",
    "create or replace table",
    "distributed by",
    "diststyle",
    "duplicate key",
    "engine =",
    "location",
    "order by",
    "partition by",
    "primary key",
    "sort key",
    "sortkey",
    "stored as",
    "tblproperties",
    "using delta",
)
_DDL_STATEMENT_ARG_NAMES = {"statement", "stmt", "table_sql"}
_DDL_STATUS_ORDER: tuple[DdlGovernanceStatus, ...] = (
    "registered_runtime_behavior",
    "registered_governance_only_intent",
    "explicit_exemption",
    "unregistered_detected_behavior",
    "unknown_uninspectable_behavior",
)
_TYPE_MAP_FUNC_NAMES = {"_map_type_to_dialect"}

_FILE_STEM_TO_PLATFORM_KEY: dict[str, str] = {
    "azure_synapse": "synapse",
    "fabric_warehouse": "fabric_dw",
}

_DDL_HELPER_PLATFORM_KEYS: dict[tuple[str, str], tuple[str, ...]] = {
    ("_spark_helpers.py", "optimize_spark_table_definition"): ("lakesail", "spark", "velox"),
    ("presto_trino_adapter_base.py", "_optimize_table_definition"): ("presto", "trino"),
}
_DDL_GOVERNANCE_TRANSFORMER_ALIASES: dict[tuple[str, str], tuple[str, ...]] = {
    ("athena", "_convert_to_external_table"): ("athena_convert_to_external_table",),
    ("clickhouse", "_optimize_table_definition"): ("clickhouse_ddl_optimizer",),
    ("clickhouse", "_resolve_tuned_ddl_clauses"): ("clickhouse_ddl_optimizer",),
    ("clickhouse", "_strip_primary_key_constraints"): ("clickhouse_ddl_optimizer",),
    ("databricks", "_convert_to_delta_table"): ("databricks_delta_ddl_optimizer",),
    ("databricks", "_convert_to_hudi_table"): ("databricks_delta_ddl_optimizer",),
    ("databricks", "_hudi_table_properties"): ("databricks_delta_ddl_optimizer",),
    ("doris", "_inject_doris_ddl_clauses"): ("doris_inject_ddl_clauses",),
    ("lakesail", "optimize_spark_table_definition"): ("lakesail_ddl_optimizer",),
    ("presto", "_optimize_table_definition"): ("presto_ddl_optimizer",),
    ("spark", "optimize_spark_table_definition"): ("spark_ddl_optimizer",),
    ("starrocks", "_optimize_table_definition"): ("starrocks_ddl_optimizer",),
    ("starrocks", "_resolve_tuned_ddl_clauses"): ("starrocks_ddl_optimizer",),
    ("trino", "_optimize_table_definition"): ("trino_ddl_optimizer",),
    ("velox", "optimize_spark_table_definition"): ("velox_ddl_optimizer",),
}

_SESSION_SETTING_RES = [
    re.compile(r"SETTINGS\s+\w+\s*=\s*\d+"),
    re.compile(r"joined_subquery_requires_alias"),
    re.compile(r"SET\s+enable_result_cache"),
]

_VARIANT_CONST_RE = re.compile(r"^_([A-Z]+)_Q(\d+)$")

_QUERY_VARIANTS_NAME = "QUERY_VARIANTS"


@dataclass
class InventoryEntry:
    file: str
    line: int
    kind: CompatKind
    platforms: list[str]
    suggested_phase: str
    description: str


BENCHMARK_GATE_FILES: tuple[str, ...] = (
    "cli/commands/run.py",
    "cli/run_platform_resolution.py",
)


def is_benchmark_gate_site(entry: InventoryEntry) -> bool:
    if entry.kind != "benchmark_gate":
        return False
    normalized = entry.file.replace("\\", "/")
    return any(normalized == candidate or normalized.endswith("/" + candidate) for candidate in BENCHMARK_GATE_FILES)


@dataclass(frozen=True)
class DdlDriftExemption:
    platform_key: str
    func_name: str | None
    reason: str


_DDL_DRIFT_EXEMPTIONS: tuple[DdlDriftExemption, ...] = ()


@dataclass(frozen=True)
class _DdlRegistrationInfo:
    runtime_transformers: frozenset[str]
    governance_only_transformers: frozenset[str]

    @property
    def has_runtime_dispatch(self) -> bool:
        return bool(self.runtime_transformers)

    @property
    def has_governance_only(self) -> bool:
        return bool(self.governance_only_transformers)


def _extract_string_literals(node: ast.AST) -> list[str]:
    result = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            result.append(child.value.lower())
    return result


def _extract_platforms(node: ast.expr) -> list[str]:
    strings = _extract_string_literals(node)
    return sorted({s for s in strings if s in _DIALECT_PLATFORMS})


def _rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root.parent))


def _normalize_sql_marker(value: str) -> str:
    marker = value.lower()
    marker = re.sub(r"\\s[+*]?", " ", marker)
    marker = marker.replace("\\b", " ")
    marker = re.sub(r"\s+", " ", marker)
    return marker.strip()


def _call_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _has_create_table_literal(node: ast.AST) -> bool:
    return any("create table" in _normalize_sql_marker(value) for value in _extract_string_literals(node))


def _has_ddl_rewrite_output_marker(node: ast.AST) -> bool:
    normalized_values = [_normalize_sql_marker(value) for value in _extract_string_literals(node)]
    return any(marker in value for marker in _DDL_REWRITE_OUTPUT_MARKERS for value in normalized_values)


def _has_ddl_text_transform_call(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Call) and (_call_name(child) in _DDL_TEXT_TRANSFORM_CALL_NAMES)
        for child in ast.walk(node)
    )


def _accepts_statement_text(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    return any(arg.arg in _DDL_STATEMENT_ARG_NAMES for arg in args)


def _looks_like_create_table_rewrite(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if not _accepts_statement_text(node):
        return False
    if not _has_create_table_literal(node):
        return False

    name = node.name.lower()
    if any(fragment in name for fragment in _DDL_REWRITE_FUNCTION_NAME_FRAGMENTS):
        return _has_ddl_text_transform_call(node) or _has_ddl_rewrite_output_marker(node)

    return _has_ddl_text_transform_call(node) and _has_ddl_rewrite_output_marker(node)


def _detect_dialect_branches(tree: ast.Module, filepath: Path, root: Path) -> Iterator[InventoryEntry]:
    rel = _rel(filepath, root)

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test

        if isinstance(test, ast.Compare) and len(test.ops) == 1:
            left = test.left
            comparators = test.comparators

            if isinstance(left, ast.Name) and left.id == "dialect":
                op = test.ops[0]
                if isinstance(op, (ast.In, ast.Eq)):
                    platforms = _extract_platforms(comparators[0])
                    if platforms:
                        kind: CompatKind = "ddl"
                        suggested = "schema_emit"
                        yield InventoryEntry(
                            file=rel,
                            line=node.lineno,
                            kind=kind,
                            platforms=platforms,
                            suggested_phase=suggested,
                            description=f"Dialect branch: dialect {'in' if isinstance(op, ast.In) else '=='} {platforms}",
                        )
                        continue

            for comp in comparators:
                if isinstance(comp, ast.Name) and comp.id == "dialect":
                    plats = _extract_platforms(left)
                    if plats:
                        yield InventoryEntry(
                            file=rel,
                            line=node.lineno,
                            kind="ddl",
                            platforms=plats,
                            suggested_phase="schema_emit",
                            description=f"Dialect membership check: {plats} in dialect",
                        )

        if isinstance(test, ast.Call):
            continue

        if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
            platforms: list[str] = []
            for val in test.values:
                if isinstance(val, ast.Compare) and isinstance(val.ops[0], ast.Eq):
                    if isinstance(val.left, ast.Name) and val.left.id == "dialect":
                        platforms.extend(_extract_platforms(val.comparators[0]))
            platforms = sorted(set(platforms))
            if platforms:
                yield InventoryEntry(
                    file=rel,
                    line=node.lineno,
                    kind="ddl",
                    platforms=platforms,
                    suggested_phase="schema_emit",
                    description=f"Dialect OR-branch: {platforms}",
                )


def _detect_named_functions(tree: ast.Module, filepath: Path, root: Path) -> Iterator[InventoryEntry]:
    rel = _rel(filepath, root)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = node.name

        if name in _SKIP_FUNC_NAMES:
            platforms = sorted(
                {
                    s.lower()
                    for child in ast.walk(node)
                    if isinstance(child, ast.Constant) and isinstance(child.value, str)
                    for s in [child.value.lower()]
                    if s in _DIALECT_PLATFORMS
                }
            )
            is_df = "df" in name
            yield InventoryEntry(
                file=rel,
                line=node.lineno,
                kind="skip",
                platforms=platforms,
                suggested_phase="dataframe_filter" if is_df else "execution_filter",
                description=f"Skip-query predicate: {name}()",
            )

        elif name in _DDL_FUNC_NAMES:
            platforms = sorted(
                {
                    s.lower()
                    for child in ast.walk(node)
                    if isinstance(child, ast.Constant) and isinstance(child.value, str)
                    for s in [child.value.lower()]
                    if s in _DIALECT_PLATFORMS
                }
            )
            yield InventoryEntry(
                file=rel,
                line=node.lineno,
                kind="ddl",
                platforms=platforms,
                suggested_phase="schema_emit",
                description=f"PK support predicate: {name}()",
            )

        elif name in _REWRITE_FUNC_NAMES:
            yield InventoryEntry(
                file=rel,
                line=node.lineno,
                kind="session_setting" if name == "add_query_settings" else "rewrite",
                platforms=[],
                suggested_phase="query_adapter",
                description=f"Query rewrite / session policy function: {name}()",
            )

        elif name in _DDL_OPTIMIZE_FUNC_NAMES:
            yield InventoryEntry(
                file=rel,
                line=node.lineno,
                kind="ddl",
                platforms=[],
                suggested_phase="ddl_optimize",
                description=f"DDL optimization function: {name}()",
            )

        elif name in _TYPE_MAP_FUNC_NAMES:
            platforms = sorted(
                {
                    s.lower()
                    for child in ast.walk(node)
                    if isinstance(child, ast.Constant) and isinstance(child.value, str)
                    for s in [child.value.lower()]
                    if s in _DIALECT_PLATFORMS
                }
            )
            yield InventoryEntry(
                file=rel,
                line=node.lineno,
                kind="type_mapping",
                platforms=platforms,
                suggested_phase="schema_emit",
                description=f"Legitimate type-mapping function: {name}()",
            )


def _detect_query_variants(tree: ast.Module, filepath: Path, root: Path) -> Iterator[InventoryEntry]:
    rel = _rel(filepath, root)

    def _check_assign(target_name: str, value_node: ast.expr | None, lineno: int) -> InventoryEntry | None:
        if target_name != _QUERY_VARIANTS_NAME or value_node is None:
            return None
        platforms: list[str] = []
        if isinstance(value_node, ast.Dict):
            for key in value_node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    k = key.value.lower()
                    if k in _DIALECT_PLATFORMS:
                        platforms.append(k)
        platforms = sorted(set(platforms))
        return InventoryEntry(
            file=rel,
            line=lineno,
            kind="rewrite",
            platforms=platforms,
            suggested_phase="query_source",
            description=f"QUERY_VARIANTS: per-platform SQL variant dict ({len(platforms)} platforms)",
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    entry = _check_assign(target.id, node.value, node.lineno)
                    if entry:
                        yield entry
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                entry = _check_assign(node.target.id, node.value, node.lineno)
                if entry:
                    yield entry


def _detect_platform_query_constants(tree: ast.Module, filepath: Path, root: Path) -> Iterator[InventoryEntry]:
    rel = _rel(filepath, root)

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if not isinstance(item, ast.Assign):
                continue
            for target in item.targets:
                if not isinstance(target, ast.Name):
                    continue
                m = _VARIANT_CONST_RE.match(target.id)
                if m:
                    platform = m.group(1).lower()
                    qid = m.group(2)
                    yield InventoryEntry(
                        file=rel,
                        line=item.lineno,
                        kind="rewrite",
                        platforms=[platform],
                        suggested_phase="query_source",
                        description=f"Class-level variant constant {target.id} - Q{qid} for {platform}",
                    )


def _detect_unsupported_benchmarks(tree: ast.Module, filepath: Path, root: Path) -> Iterator[InventoryEntry]:
    rel = _rel(filepath, root)

    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and key.value == "unsupported_benchmarks":
                    yield InventoryEntry(
                        file=rel,
                        line=key.lineno,
                        kind="benchmark_gate",
                        platforms=[],
                        suggested_phase="benchmark_gate",
                        description="unsupported_benchmarks dict entry in platform capabilities",
                    )
        if isinstance(node, ast.Attribute) and node.attr == "unsupported_benchmarks":
            yield InventoryEntry(
                file=rel,
                line=node.lineno,
                kind="benchmark_gate",
                platforms=[],
                suggested_phase="benchmark_gate",
                description="caps.unsupported_benchmarks access - benchmark_gate preflight",
            )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "unsupported_benchmarks"
        ):
            yield InventoryEntry(
                file=rel,
                line=node.lineno,
                kind="benchmark_gate",
                platforms=[],
                suggested_phase="benchmark_gate",
                description="getattr(caps, 'unsupported_benchmarks') - benchmark_gate preflight",
            )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr
            in (
                "get_benchmark_block_reason",
                "get_unsupported_benchmarks",
                "is_benchmark_supported",
            )
        ):
            yield InventoryEntry(
                file=rel,
                line=node.lineno,
                kind="benchmark_gate",
                platforms=[],
                suggested_phase="benchmark_gate",
                description=f"{node.func.attr}() - benchmark_gate preflight via compatibility API",
            )


def _docstring_line_ranges(tree: ast.Module) -> set[int]:
    docstring_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
            and hasattr(first, "end_lineno")
        ):
            for ln in range(first.lineno, first.end_lineno + 1):
                docstring_lines.add(ln)
    return docstring_lines


def _detect_session_settings(
    source: str, filepath: Path, root: Path, docstring_lines: set[int] | None = None
) -> Iterator[InventoryEntry]:
    rel = _rel(filepath, root)
    lines = source.splitlines()
    excluded = docstring_lines or set()
    for lineno, line in enumerate(lines, 1):
        if lineno in excluded:
            continue
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for pattern in _SESSION_SETTING_RES:
            if pattern.search(line):
                yield InventoryEntry(
                    file=rel,
                    line=lineno,
                    kind="session_setting",
                    platforms=[],
                    suggested_phase="query_adapter",
                    description=f"Session policy pattern: {stripped[:80]}",
                )
                break


def _detect_dataframe_skip_calls(source: str, filepath: Path, root: Path) -> Iterator[InventoryEntry]:
    rel = _rel(filepath, root)
    lines = source.splitlines()
    call_re = re.compile(r"(get_platform_skip_queries|get_df_platform_skip_queries)\s*\(")
    for lineno, line in enumerate(lines, 1):
        if line.strip().startswith("#"):
            continue
        m = call_re.search(line)
        if m:
            is_df = "df" in m.group(1)
            yield InventoryEntry(
                file=rel,
                line=lineno,
                kind="skip",
                platforms=[],
                suggested_phase="dataframe_filter" if is_df else "execution_filter",
                description=f"Call site: {m.group(1)}() - skip-query lookup",
            )


def _should_scan(path: Path) -> bool:
    parts = path.parts
    return (
        path.suffix == ".py"
        and "__pycache__" not in parts
        and ".git" not in parts
        and "sql_compat" not in parts
        and not path.stem.startswith("test_")
        and path.stem != "conftest"
    )


def _reclassify_inside_type_mapping(entries: list[InventoryEntry], tree: ast.Module, rel: str) -> None:
    ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in _TYPE_MAP_FUNC_NAMES and hasattr(node, "end_lineno"):
                ranges.append((node.lineno, node.end_lineno))
    if not ranges:
        return
    for entry in entries:
        if entry.file == rel and entry.kind == "ddl":
            for start, end in ranges:
                if start <= entry.line <= end:
                    entry.kind = "type_mapping"
                    break


def scan(root: Path) -> list[InventoryEntry]:
    entries: list[InventoryEntry] = []
    seen: set[tuple[str, int, str]] = set()

    for filepath in sorted(root.rglob("*.py")):
        if not _should_scan(filepath):
            continue
        try:
            source = filepath.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(filepath))
        except (SyntaxError, UnicodeDecodeError):
            continue

        rel = _rel(filepath, root)
        file_entries: list[InventoryEntry] = []
        doc_lines = _docstring_line_ranges(tree)

        detectors = [
            _detect_dialect_branches(tree, filepath, root),
            _detect_named_functions(tree, filepath, root),
            _detect_query_variants(tree, filepath, root),
            _detect_platform_query_constants(tree, filepath, root),
            _detect_unsupported_benchmarks(tree, filepath, root),
            _detect_session_settings(source, filepath, root, doc_lines),
            _detect_dataframe_skip_calls(source, filepath, root),
        ]
        for detector in detectors:
            for entry in detector:
                key = (entry.file, entry.line, entry.kind)
                if key not in seen:
                    seen.add(key)
                    file_entries.append(entry)

        _reclassify_inside_type_mapping(file_entries, tree, rel)
        entries.extend(file_entries)

    entries.sort(key=lambda e: (e.file, e.line))
    return entries


def write_jsonl(entries: list[InventoryEntry], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(asdict(entry)) + "\n")


def _validate_mandatory_sites(entries: list[InventoryEntry]) -> list[str]:
    errors: list[str] = []

    gate_sites = [e for e in entries if is_benchmark_gate_site(e)]
    if not gate_sites:
        errors.append(
            "MISSING: benchmark_gate site in cli/commands/run.py or cli/run_platform_resolution.py not detected"
        )

    variants_sites = [e for e in entries if e.suggested_phase == "query_source" and "vector_search" in e.file]
    if not variants_sites:
        errors.append("MISSING: QUERY_VARIANTS / version-gated site in core/vector_search/queries.py not detected")

    return errors


def _platform_key_from_adapter_path(filepath: Path) -> str:
    stem = filepath.stem
    if stem in ("workload", "adapter"):
        stem = filepath.parent.name
    return _FILE_STEM_TO_PLATFORM_KEY.get(stem, stem)


@dataclass
class DdlDriftEntry:
    file: str
    line: int
    func_name: str
    inferred_platform_key: str
    detection_kind: str = "known-function-name"
    status: DdlGovernanceStatus = "unregistered_detected_behavior"
    detail: str = ""


def _load_ddl_rule_modules() -> None:
    import importlib
    import pkgutil

    import benchbox.sql_compat.rules.ddl_optimize as _ddl_pkg

    broken_modules: list[str] = []
    for _, mod_name, _ispkg in pkgutil.walk_packages(_ddl_pkg.__path__, _ddl_pkg.__name__ + "."):
        if not _ispkg:
            try:
                importlib.import_module(mod_name)
            except Exception as exc:
                broken_modules.append(f"{mod_name}: {exc}")
    if broken_modules:
        raise RuntimeError(
            "Cannot complete drift check — failed to import DDL rule module(s):\n"
            + "\n".join(f"  {m}" for m in broken_modules)
        )


def _registered_ddl_info_by_platform() -> dict[str, _DdlRegistrationInfo]:
    from benchbox.sql_compat.context import Phase
    from benchbox.sql_compat.decision import RewriteDDLPayload
    from benchbox.sql_compat.registry import REGISTRY

    runtime_transformers: dict[str, set[str]] = {}
    governance_only_transformers: dict[str, set[str]] = {}
    for (phase, platform, _, _), entry in REGISTRY.all_rules():
        if phase != Phase.DDL_OPTIMIZE:
            continue
        payload = entry.decision.payload
        if isinstance(payload, RewriteDDLPayload) and not payload.governance_only:
            runtime_transformers.setdefault(platform, set()).add(payload.transformer_id)
        elif isinstance(payload, RewriteDDLPayload):
            governance_only_transformers.setdefault(platform, set()).add(payload.transformer_id)
    platforms = set(runtime_transformers) | set(governance_only_transformers)
    return {
        platform: _DdlRegistrationInfo(
            runtime_transformers=frozenset(runtime_transformers.get(platform, set())),
            governance_only_transformers=frozenset(governance_only_transformers.get(platform, set())),
        )
        for platform in platforms
    }


def _ddl_exemption_for(platform_key: str, func_name: str) -> DdlDriftExemption | None:
    for exemption in _DDL_DRIFT_EXEMPTIONS:
        if exemption.platform_key != platform_key:
            continue
        if exemption.func_name is not None and exemption.func_name != func_name:
            continue
        return exemption
    return None


def _platform_keys_for_ddl_function(filepath: Path, func_name: str) -> tuple[str, ...]:
    helper_platforms = _DDL_HELPER_PLATFORM_KEYS.get((filepath.name, func_name))
    if helper_platforms is not None:
        return helper_platforms
    if filepath.stem.startswith("_"):
        return ()
    return (_platform_key_from_adapter_path(filepath),)


def _ddl_status_for_function(
    registration_info: _DdlRegistrationInfo | None,
    platform_key: str,
    func_name: str,
    detection_kind: str,
) -> DdlGovernanceStatus:
    if registration_info is None:
        return "unregistered_detected_behavior"
    transformer_names = {func_name, *_DDL_GOVERNANCE_TRANSFORMER_ALIASES.get((platform_key, func_name), ())}
    if transformer_names & registration_info.runtime_transformers:
        return "registered_runtime_behavior"
    if detection_kind == "known-function-name" and registration_info.has_runtime_dispatch:
        return "registered_runtime_behavior"
    if transformer_names & registration_info.governance_only_transformers:
        return "registered_governance_only_intent"
    return "unregistered_detected_behavior"


def _iter_ddl_rewrite_functions(
    tree: ast.Module,
) -> Iterator[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str, str]]:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name in _DDL_OPTIMIZE_FUNC_NAMES:
            yield node, "known-function-name", "function name is registered as a DDL optimizer signature"
        elif _looks_like_create_table_rewrite(node):
            yield (
                node,
                "create-table-rewrite-signature",
                "function contains CREATE TABLE markers plus SQL text rewrite calls or output clauses",
            )


def collect_ddl_governance_statuses(root: Path) -> list[DdlDriftEntry]:
    _load_ddl_rule_modules()
    registered_info = _registered_ddl_info_by_platform()

    entries: list[DdlDriftEntry] = []
    platforms_dir = root / "platforms"
    if not platforms_dir.exists():
        return entries

    for filepath in sorted(platforms_dir.rglob("*.py")):
        if "__pycache__" in filepath.parts:
            continue
        if filepath.parent.name == "base":
            continue

        platform_key = _platform_key_from_adapter_path(filepath)
        rel = str(filepath.relative_to(root.parent))
        try:
            source = filepath.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(filepath))
        except (SyntaxError, UnicodeDecodeError) as exc:
            entries.append(
                DdlDriftEntry(
                    file=rel,
                    line=0,
                    func_name="<module>",
                    inferred_platform_key=platform_key,
                    detection_kind=type(exc).__name__,
                    status="unknown_uninspectable_behavior",
                    detail=str(exc),
                )
            )
            continue

        for node, detection_kind, detail in _iter_ddl_rewrite_functions(tree):
            for detected_platform_key in _platform_keys_for_ddl_function(filepath, node.name):
                exemption = _ddl_exemption_for(detected_platform_key, node.name)
                status: DdlGovernanceStatus
                status_detail = detail
                if exemption is not None:
                    status = "explicit_exemption"
                    status_detail = exemption.reason
                else:
                    status = _ddl_status_for_function(
                        registered_info.get(detected_platform_key),
                        detected_platform_key,
                        node.name,
                        detection_kind,
                    )

                entries.append(
                    DdlDriftEntry(
                        file=rel,
                        line=node.lineno,
                        func_name=node.name,
                        inferred_platform_key=detected_platform_key,
                        detection_kind=detection_kind,
                        status=status,
                        detail=status_detail,
                    )
                )

    return entries


def check_ddl_drift(root: Path) -> list[DdlDriftEntry]:
    return [
        entry
        for entry in collect_ddl_governance_statuses(root)
        if entry.status in {"unregistered_detected_behavior", "unknown_uninspectable_behavior"}
    ]


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="BenchBox compatibility inventory tool")
    parser.add_argument(
        "--root",
        default="benchbox",
        help="Root directory to scan (default: benchbox/)",
    )
    parser.add_argument(
        "--output",
        default="_project/compat/inventory.jsonl",
        help="Output JSONL path (default: _project/compat/inventory.jsonl)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print summary to stdout after writing",
    )
    parser.add_argument(
        "--check-ddl-drift",
        action="store_true",
        help=(
            "After scanning, verify every adapter with a DDL-optimize method has a "
            "registered Phase.DDL_OPTIMIZE rule.  Exits 1 on any drift finding."
        ),
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    output = Path(args.output)

    if not root.exists():
        emit(f"ERROR: root directory not found: {root}", stderr=True)
        return 1

    emit(f"Scanning {root} ...", stderr=True)
    entries = scan(root)
    write_jsonl(entries, output)
    emit(f"Wrote {len(entries)} entries to {output}", stderr=True)

    exit_code = 0

    errors = _validate_mandatory_sites(entries)
    if errors:
        for err in errors:
            emit(f"VALIDATION ERROR: {err}", stderr=True)
        exit_code = 1

    if args.summary:
        kind_counts = Counter(e.kind for e in entries)
        phase_counts = Counter(e.suggested_phase for e in entries)
        emit("\nKind distribution:")
        for kind, count in sorted(kind_counts.items()):
            emit(f"  {kind:20s} {count}")
        emit("\nSuggested phase distribution:")
        for phase, count in sorted(phase_counts.items()):
            emit(f"  {phase:20s} {count}")

    if args.check_ddl_drift:
        emit("\nChecking DDL drift ...", stderr=True)
        statuses = collect_ddl_governance_statuses(root)
        counts = Counter(entry.status for entry in statuses)
        summary = ", ".join(f"{status}={counts.get(status, 0)}" for status in _DDL_STATUS_ORDER)
        emit(f"DDL governance status: {summary}", stderr=True)

        drift = [
            entry
            for entry in statuses
            if entry.status in {"unregistered_detected_behavior", "unknown_uninspectable_behavior"}
        ]
        if drift:
            emit(
                f"DDL DRIFT: {len(drift)} unregistered or uninspectable DDL-optimize transform(s):",
                stderr=True,
            )
            for d in drift:
                emit(
                    f"  {d.file}:{d.line}  {d.func_name}()  "
                    f"[platform={d.inferred_platform_key!r}, status={d.status}, detector={d.detection_kind}]",
                    stderr=True,
                )
                if d.detail:
                    emit(f"    {d.detail}", stderr=True)
            emit(
                "Fix by one of:\n"
                "  1) register the transform's rule in "
                "benchbox/sql_compat/rules/ddl_optimize/{platform}_ddl_rewrites.py, or\n"
                "  2) if it IS registered but under a different function name, add a "
                "(platform_key, func_name) -> (registered_name, ...) entry to "
                "_DDL_GOVERNANCE_TRANSFORMER_ALIASES in benchbox/sql_compat/inventory.py, or\n"
                "  3) add an explicit drift exemption (_DDL_DRIFT_EXEMPTIONS) with rationale.\n"
                "No regen mode exists for this guard -- it is always a reviewed hand edit, "
                "not covered by `make guards-fix`.",
                stderr=True,
            )
            exit_code = 1
        else:
            emit("DDL drift check: CLEAN (0 unregistered or uninspectable transforms)", stderr=True)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
