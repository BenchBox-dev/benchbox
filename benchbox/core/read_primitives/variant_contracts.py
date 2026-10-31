from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from benchbox._vendor import sqlglot
from benchbox._vendor.sqlglot import exp

if TYPE_CHECKING:
    from benchbox.core.read_primitives.catalog import PrimitiveCatalog, PrimitiveQuery


@dataclass(frozen=True)
class VariantContractIssue:
    query_id: str
    dialect: str | None
    kind: str
    detail: str


_DIALECT_READ = {
    "bigquery": "bigquery",
    "clickhouse": "clickhouse",
    "duckdb": "duckdb",
    "redshift": "redshift",
    "singlestore": "singlestore",
    "snowflake": "snowflake",
    "spark": "spark",
}

_HIGH_RISK_CATEGORIES = {
    "array",
    "fulltext",
    "json",
    "lambda",
    "map",
    "statistical",
    "struct",
    "timeseries",
    "window",
}

_HIGH_RISK_QUERY_IDS = {
    "any_value_simple",
    "any_value_with_filter",
}


def collect_variant_contract_issues(
    catalog: PrimitiveCatalog,
    *,
    require_contracts: bool = True,
) -> list[VariantContractIssue]:
    issues: list[VariantContractIssue] = []

    for query_id, entry in catalog.queries.items():
        variants = entry.variants or {}
        if not variants:
            continue

        base_columns = extract_projection_names(entry.sql, dialect="duckdb")
        if base_columns is None:
            issues.append(
                VariantContractIssue(
                    query_id=query_id,
                    dialect=None,
                    kind="base_parse_error",
                    detail="Could not parse base query projection columns",
                )
            )

        issues.extend(_contract_metadata_issues(query_id, entry, base_columns, require_contracts=require_contracts))
        contract_columns = (
            tuple(column.name for column in entry.result_contract.columns)
            if entry.result_contract is not None
            else None
        )
        issues.extend(
            _variant_projection_issues(
                query_id,
                variants,
                expected_columns=contract_columns or base_columns,
                expected_source="result_contract" if contract_columns is not None else "base",
            )
        )

    return issues


def extract_projection_names(sql: str, *, dialect: str) -> tuple[str, ...] | None:
    try:
        expression = sqlglot.parse_one(sql, read=_DIALECT_READ.get(dialect, "duckdb"))
    except sqlglot.errors.SqlglotError:
        return None

    select = expression if isinstance(expression, exp.Select) else expression.find(exp.Select)
    if select is None:
        return None

    projections = _resolve_star_wrapper_projections(select)
    names = []
    for projection in projections:
        if isinstance(projection, exp.Star):
            return None
        name = projection.alias_or_name or projection.output_name
        if not name:
            return None
        names.append(name)
    return tuple(names)


def _contract_metadata_issues(
    query_id: str,
    entry: PrimitiveQuery,
    base_columns: tuple[str, ...] | None,
    *,
    require_contracts: bool,
) -> list[VariantContractIssue]:
    contract = entry.result_contract
    if contract is None:
        if require_contracts and _requires_result_contract(entry):
            return [
                VariantContractIssue(
                    query_id=query_id,
                    dialect=None,
                    kind="missing_result_contract",
                    detail="Variant-bearing query must declare result_contract",
                )
            ]
        return []

    issues = []
    contract_columns = tuple(column.name for column in contract.columns)
    if base_columns is not None and contract_columns != base_columns:
        issues.append(
            VariantContractIssue(
                query_id=query_id,
                dialect=None,
                kind="contract_column_mismatch",
                detail=f"base columns {base_columns!r} != result_contract columns {contract_columns!r}",
            )
        )
    if not contract.capability:
        issues.append(
            VariantContractIssue(
                query_id=query_id,
                dialect=None,
                kind="missing_capability",
                detail="result_contract must declare measured capability",
            )
        )
    return issues


def _variant_projection_issues(
    query_id: str,
    variants: dict[str, str],
    *,
    expected_columns: tuple[str, ...] | None,
    expected_source: str,
) -> list[VariantContractIssue]:
    issues = []
    for dialect, sql in variants.items():
        variant_columns = extract_projection_names(sql, dialect=dialect)
        if variant_columns is None:
            issues.append(
                VariantContractIssue(
                    query_id=query_id,
                    dialect=dialect,
                    kind="variant_parse_error",
                    detail="Could not parse variant projection columns",
                )
            )
        elif expected_columns is not None and variant_columns != expected_columns:
            issues.append(
                VariantContractIssue(
                    query_id=query_id,
                    dialect=dialect,
                    kind="column_mismatch",
                    detail=(f"{expected_source} columns {expected_columns!r} != variant columns {variant_columns!r}"),
                )
            )
    return issues


def _resolve_star_wrapper_projections(select: exp.Select) -> list[exp.Expression]:
    projections = list(select.expressions)
    if len(projections) != 1 or not isinstance(projections[0], exp.Star):
        return projections

    from_clause = select.args.get("from_")
    if from_clause is None:
        return projections
    source = from_clause.this
    if not isinstance(source, exp.Subquery):
        return projections

    inner_select = source.this.find(exp.Select)
    if inner_select is None:
        return projections
    return list(inner_select.expressions)


def _requires_result_contract(entry: PrimitiveQuery) -> bool:
    return bool(entry.variants) or entry.category in _HIGH_RISK_CATEGORIES or entry.id in _HIGH_RISK_QUERY_IDS


def summarize_variant_comparability(catalog=None) -> dict:
    if catalog is None:
        import copy as _copy

        return _copy.deepcopy(_default_comparability_summary())
    return _summarize_catalog(catalog)


@lru_cache(maxsize=1)
def _default_comparability_summary() -> dict:
    from benchbox.core.read_primitives.catalog.loader import load_primitives_catalog

    return _summarize_catalog(load_primitives_catalog())


def _summarize_catalog(catalog) -> dict:
    issues = collect_variant_contract_issues(catalog)
    queries = getattr(catalog, "queries", {}) or {}
    per_query: dict[str, dict] = {}
    for query_id, entry in queries.items():
        variants = getattr(entry, "variants", None) or {}
        per_query[str(query_id)] = {
            "variant_dialects": sorted(str(d) for d in variants),
            "has_variants": bool(variants),
        }
    issues_by_query: dict[str, list] = {}
    for issue in issues:
        issues_by_query.setdefault(str(issue.query_id), []).append(
            {
                "dialect": issue.dialect,
                "kind": issue.kind,
                "detail": issue.detail,
            }
        )
    for query_id, entries in issues_by_query.items():
        per_query.setdefault(str(query_id), {}).update({"issue_count": len(entries), "issues": entries})
    return {
        "total_queries": len(queries),
        "queries_with_variants": sum(1 for v in per_query.values() if v.get("has_variants")),
        "issue_count": len(issues),
        "comparable": not issues,
        "per_query": per_query,
    }


__all__ = [
    "VariantContractIssue",
    "collect_variant_contract_issues",
    "extract_projection_names",
    "summarize_variant_comparability",
]
