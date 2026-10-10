from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from benchbox.core.equivalence.builders import (
    CrossSurfaceData,
    build_amplab_duckdb,
    build_clickbench_duckdb,
    build_coffeeshop_duckdb,
    build_datavault_duckdb,
    build_flightdata_duckdb,
    build_h2odb_duckdb,
    build_joinorder_synthetic_duckdb,
    build_nyctaxi_duckdb,
    build_read_primitives_duckdb,
    build_ssb_duckdb,
    build_tpcds_duckdb,
    build_tpch_duckdb,
    build_tpch_skew_duckdb,
    build_tsbs_devops_duckdb,
)
from benchbox.core.equivalence.dataframe_surface import (
    DATAFRAME_BACKENDS,
    SurfaceDivergence,
    fetch_reference_rows,
    find_surface_divergences,
    materialize_rows,
)
from benchbox.core.execution_engine import resolve_requested
from benchbox.utils.printing import quiet_console as console

_BACKEND_FAMILIES = {"expression": "expression", "pandas": "pandas", "datafusion": "expression"}

if TYPE_CHECKING:  # pragma: no cover
    from benchbox.core.tpchavoc.validation import ResultValidator


def _load_known_divergences_baseline() -> dict[str, dict[str, str]]:
    payload = yaml.safe_load(
        resources.files("benchbox.core.equivalence").joinpath("cross_surface_baseline.yaml").read_text(encoding="utf-8")
    )
    if not isinstance(payload, dict):
        raise RuntimeError("cross_surface_baseline.yaml must contain a top-level mapping")
    return payload


_KNOWN_DIVERGENCES_BASELINE: dict[str, dict[str, str]] = _load_known_divergences_baseline()

EQUIVALENCE_SCALE = 0.1

SURFACE_INDEPENDENCE_SHARED_SPEC = "shared-spec"
SURFACE_INDEPENDENCE_MIXED = "mixed-provenance"
SURFACE_INDEPENDENCE_SEPARATE = "separate-handwritten"

_DEFAULT_SURFACE_INDEPENDENCE_RATIONALE = (
    "SQL and DataFrame surfaces are maintained from a shared benchmark spec; the gate catches "
    "transcription drift between surfaces, not shared conceptual mistakes."
)

_TRAILING_LIMIT_RE = re.compile(r"(?is)(\blimit\s+)(\d+)(\s*(?:offset\s+\d+\s*)?;?\s*)$")
_TRAILING_COMMENT_RE = re.compile(r"\s*--[^\n]*$")


def _strip_trailing_comment(sql: str) -> str:
    return _TRAILING_COMMENT_RE.sub("", sql.strip())


def _is_truncated_top_n(sql: str) -> bool:
    return bool(_TRAILING_LIMIT_RE.search(_strip_trailing_comment(sql)))


def _bump_trailing_limit(sql: str) -> str | None:
    stripped = _strip_trailing_comment(sql)
    match = _TRAILING_LIMIT_RE.search(stripped)
    if match is None:
        return None
    bumped = int(match.group(2)) + 1
    return f"{stripped[: match.start()]}{match.group(1)}{bumped}{match.group(3)}"


def _final_key_tied_beyond_limit(
    connection: Any,
    sql: str,
    order_by: Sequence[int],
    reference: list[tuple[Any, ...]],
) -> bool:
    if not reference or not order_by:
        return False
    n = len(reference)
    width = len(reference[-1])
    if any(c < 0 or c >= width for c in order_by):
        return False
    final_key = tuple(reference[-1][c] for c in order_by)
    probe_sql = _bump_trailing_limit(sql)
    if probe_sql is None:
        return False
    try:
        probe = fetch_reference_rows(connection, probe_sql)
    except Exception:
        return False
    if len(probe) <= n or len(probe[n]) <= max(order_by):
        return False
    key_beyond = tuple(probe[n][c] for c in order_by)
    return final_key == key_beyond


def _is_star_projection(projection: Any) -> bool:
    from sqlglot import exp

    return isinstance(projection, exp.Star) or (
        isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star)
    )


def _order_by_result_key(sql: str) -> list[int] | None:
    import sqlglot
    from sqlglot import exp

    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return None
    if tree is None:
        return None
    select = tree.find(exp.Select)
    if select is None:
        return None
    order = select.args.get("order")
    if order is None:
        return None

    projections = select.expressions
    if any(_is_star_projection(proj) for proj in projections):
        return None

    alias_to_index: dict[str, int] = {}
    expr_to_index: dict[str, int] = {}
    for index, proj in enumerate(projections):
        name = proj.alias_or_name
        if name:
            alias_to_index.setdefault(name.lower(), index)
        inner = proj.this if isinstance(proj, exp.Alias) else proj
        expr_to_index.setdefault(inner.sql(dialect="duckdb", normalize=True).lower(), index)

    resolved: list[int] = []
    for ordered in order.expressions:
        target = ordered.this
        index = _resolve_order_term(target, projections, alias_to_index, expr_to_index)
        if index is None:
            return None
        resolved.append(index)
    return resolved


@dataclass(frozen=True)
class OrderStatus:
    kind: str
    reason: str = ""


ORDER_VERIFIED = "verified"
ORDER_UNORDERED = "unordered"
ORDER_UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True)
class _DerivedOrderPlan:
    terms: tuple[tuple[str, str], ...] = ()
    columns: tuple[tuple[str, str], ...] = ()
    status: OrderStatus = OrderStatus(ORDER_UNORDERED, "no ORDER BY")


def _projection_column(projection: Any) -> Any | None:
    from sqlglot import exp

    inner = projection.this if isinstance(projection, exp.Alias) else projection
    return inner if isinstance(inner, exp.Column) and not isinstance(inner.this, exp.Star) else None


def _lone_source_qualifier(tree: Any) -> str | None:
    from sqlglot import exp

    from_node = tree.args.get("from")
    if from_node is None:
        from_node = tree.args.get("from_")
    if from_node is None or tree.args.get("joins"):
        return None
    source = from_node.this
    if source is None:
        return None
    if isinstance(source, exp.Table):
        return source.alias_or_name.lower() or None
    alias = source.args.get("alias")
    return alias.alias_or_name.lower() if alias is not None else None


def _resolve_output_column(
    column: Any, names: Sequence[str], projections: Sequence[Any] | None, lone_qualifier: str | None
) -> int | None:
    name = column.name.lower()
    if not column.table:
        matches = [index for index, output in enumerate(names) if output == name]
        return matches[0] if len(matches) == 1 else None
    if projections is None or len(projections) != len(names):
        return None
    qualifier = column.table.lower()
    candidates = []
    for index, projection in enumerate(projections):
        source = _projection_column(projection)
        if source is None or source.name.lower() != name:
            continue
        if source.table:
            if source.table.lower() != qualifier:
                continue
        elif lone_qualifier != qualifier:
            continue
        candidates.append(index)
    return candidates[0] if len(candidates) == 1 else None


def _rewrite_order_term(
    target: Any, names: Sequence[str], projections: Sequence[Any] | None, lone_qualifier: str | None
) -> Any:
    from sqlglot import exp

    if isinstance(target, exp.Literal) and target.is_int:
        ordinal = int(target.name)
        if not 1 <= ordinal <= len(names):
            return f"ORDER BY position {ordinal} is out of range"
        return exp.column(f"__c{ordinal - 1}")
    if target.find(exp.Subquery, exp.Select):
        return "an ORDER BY term contains a subquery"
    if not target.find(exp.Column):
        return "an ORDER BY term has no column reference"
    for column in list(target.find_all(exp.Column)):
        position = _resolve_output_column(column, names, projections, lone_qualifier)
        if position is None:
            return f"ORDER BY column {column.sql(dialect='duckdb')} is not a unique output column"
        replacement = exp.column(f"__c{position}")
        if column is target:
            target = replacement
        else:
            column.replace(replacement)
    return target


def _unverifiable(reason: str) -> _DerivedOrderPlan:
    return _DerivedOrderPlan(status=OrderStatus(ORDER_UNVERIFIABLE, reason))


def _derived_order_plan(sql: str, describe: Callable[[], Sequence[tuple[str, str]]]) -> _DerivedOrderPlan:
    import sqlglot
    from sqlglot import exp

    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return _unverifiable("the query does not parse")
    order = tree.args.get("order") if isinstance(tree, exp.Select) else None
    if order is None:
        return _DerivedOrderPlan()
    if any(projection.find(exp.Collate) is not None for projection in tree.expressions):
        return _unverifiable("an output column specifies a collation the order check cannot reproduce")
    try:
        columns = tuple(describe())
    except Exception:
        columns = ()
    if not columns:
        return _unverifiable("the output columns cannot be described")
    names = [name.lower() for name, _ in columns]
    projections = tree.expressions if not any(_is_star_projection(item) for item in tree.expressions) else None
    lone_qualifier = _lone_source_qualifier(tree)

    entries = []
    for ordered in order.expressions:
        target = ordered.this
        if isinstance(target, exp.Var) and target.name.upper() == "ALL" and len(order.expressions) == 1:
            if "all" in names:
                return _unverifiable("ORDER BY ALL with an output column named all")
            if any(_is_text_type(declared) for _, declared in columns):
                return _unverifiable("ORDER BY ALL over a text column cannot reproduce the output collation")
            entries.extend((exp.column(f"__c{index}"), ordered, True) for index in range(len(names)))
        else:
            entries.append((target.copy(), ordered, False))

    terms = []
    for index, (target, ordered, resolved) in enumerate(entries):
        if not resolved:
            rewritten = _rewrite_order_term(target, names, projections, lone_qualifier)
            if isinstance(rewritten, str):
                return _unverifiable(rewritten)
            target = rewritten
        key = ordered.copy()
        key.set("this", exp.column(f"__k{index}"))
        terms.append((target.sql(dialect="duckdb"), key.sql(dialect="duckdb")))
    return _DerivedOrderPlan(tuple(terms), columns, OrderStatus(ORDER_VERIFIED))


def _key_column_type(declared: str) -> str:
    return "DOUBLE" if declared.upper().startswith(("FLOAT", "REAL")) else declared


def _is_text_type(declared: str) -> bool:
    return declared.upper().startswith(("VARCHAR", "CHAR", "BPCHAR", "TEXT"))


def _plan_order_violation(plan: _DerivedOrderPlan, rows: list[tuple[Any, ...]]) -> str | None:
    import duckdb

    columns = plan.columns
    if plan.status.kind != ORDER_VERIFIED or len(rows) < 2:
        return None

    def cell(value: Any) -> Any:
        return None if isinstance(value, float) and value != value else value

    connection = duckdb.connect()
    try:
        definitions = ", ".join(
            f"__c{index} {_key_column_type(declared)}" for index, (_, declared) in enumerate(columns)
        )
        connection.execute(f"CREATE TABLE candidate (__pos BIGINT, {definitions})")
        placeholders = ", ".join("?" for _ in range(len(columns) + 1))
        connection.executemany(
            f"INSERT INTO candidate VALUES ({placeholders})",
            [(position, *(cell(value) for value in row)) for position, row in enumerate(rows)],
        )
        keys = ", ".join(f"{expression} AS __k{index}" for index, (expression, _) in enumerate(plan.terms))
        keyed = f"SELECT __pos, {keys} FROM candidate"
        returned = connection.execute(f"SELECT * EXCLUDE (__pos) FROM ({keyed}) ORDER BY __pos").fetchall()
        required = connection.execute(
            f"SELECT * EXCLUDE (__pos) FROM ({keyed}) ORDER BY {', '.join(key for _, key in plan.terms)}, __pos"
        ).fetchall()
    except duckdb.Error as exc:
        return f"the ORDER BY check could not evaluate the sort keys over the returned rows: {exc}"
    finally:
        connection.close()
    for position, (actual, expected) in enumerate(zip(returned, required, strict=True)):
        if actual != expected:
            return f"returned row {position} breaks the ORDER BY: sort key {actual}, expected {expected}"
    return None


def _derived_order_violation(sql: str, columns: Sequence[tuple[str, str]], rows: list[tuple[Any, ...]]) -> str | None:
    return _plan_order_violation(_derived_order_plan(sql, lambda: columns), rows)


def _resolve_order_term(
    target: Any,
    projections: Sequence[Any],
    alias_to_index: dict[str, int],
    expr_to_index: dict[str, int],
) -> int | None:
    from sqlglot import exp

    if isinstance(target, exp.Literal) and target.is_int:
        ordinal = int(target.name)
        return ordinal - 1 if 1 <= ordinal <= len(projections) else None
    if isinstance(target, exp.Column):
        index = alias_to_index.get(target.name.lower())
        if index is not None:
            return index
    return expr_to_index.get(target.sql(dialect="duckdb", normalize=True).lower())


@dataclass(frozen=True)
class ClassifiedDivergence:
    reason: str
    accepts: Callable[[SurfaceDivergence], bool]
    requires_live_divergence: bool = True
    review_by: date | None = None


@dataclass(frozen=True)
class CrossSurfaceGate:
    name: str
    build: Callable[[float, Path], CrossSurfaceData]
    known_divergences: dict[str, str | ClassifiedDivergence] = field(default_factory=dict)
    dtype_skip_keys: frozenset[str] = frozenset()
    legitimately_empty: dict[Any, str] = field(default_factory=dict)
    vacuity_classified: bool = True
    backends: tuple[str, ...] = DATAFRAME_BACKENDS
    tolerance: float = 1e-10
    surface_independence: str = SURFACE_INDEPENDENCE_SHARED_SPEC
    surface_independence_rationale: str = _DEFAULT_SURFACE_INDEPENDENCE_RATIONALE
    treat_nan_as_null: bool = False
    strip_strings: bool = False
    scale_factor: float = EQUIVALENCE_SCALE

    def build_validator(self) -> ResultValidator:
        from benchbox.core.tpchavoc.validation import ResultValidator

        return ResultValidator(
            tolerance=self.tolerance,
            treat_nan_as_null=self.treat_nan_as_null,
            strip_strings=self.strip_strings,
        )


def find_cross_surface_divergences(
    connection: Any,
    *,
    query_ids: Iterable[Any],
    reference_sql: Callable[[Any], str],
    dataframe_query: Callable[[Any], Any],
    contexts: dict[str, Any],
    validator: ResultValidator,
    backends: tuple[str, ...] = DATAFRAME_BACKENDS,
    reference_row_counts: dict[Any, int] | None = None,
    all_null_references: set[Any] | None = None,
    order_statuses: dict[tuple[Any, str], OrderStatus] | None = None,
) -> list[SurfaceDivergence]:
    from benchbox.core.tpchavoc.validation import ValidationError

    def reference_rows(query_id: Any) -> list[tuple[Any, ...]]:
        rows = fetch_reference_rows(connection, reference_sql(query_id))
        if reference_row_counts is not None:
            if len(rows) == 1 and all(value is None for value in rows[0]):
                reference_row_counts[query_id] = 0
                if all_null_references is not None:
                    all_null_references.add(query_id)
            else:
                reference_row_counts[query_id] = len(rows)
        return rows

    def candidate_cells(
        query_id: Any,
    ) -> Iterable[tuple[str, Callable[[list[tuple[Any, ...]]], None]]]:
        query = dataframe_query(query_id)
        sql = reference_sql(query_id)
        tie_aware = _is_truncated_top_n(sql)
        order_by = _order_by_result_key(sql)
        order_aware = order_by is not None
        probe_result: bool | None = None
        derived_plan: _DerivedOrderPlan | None = None

        def order_plan() -> _DerivedOrderPlan:
            nonlocal derived_plan
            if derived_plan is None:
                derived_plan = _derived_order_plan(
                    sql, lambda: [(row[0], row[1]) for row in connection.execute(f"DESCRIBE {sql}").fetchall()]
                )
            return derived_plan

        def final_key_tied(reference: list[tuple[Any, ...]]) -> bool:
            nonlocal probe_result
            if probe_result is None:
                probe_result = bool(
                    tie_aware
                    and order_by is not None
                    and _final_key_tied_beyond_limit(connection, sql, order_by, reference)
                )
            return probe_result

        for backend in backends:
            impl = query.get_impl_for_family(_BACKEND_FAMILIES.get(backend, backend))
            if impl is None:
                continue

            def check(
                reference: list[tuple[Any, ...]],
                *,
                impl: Any = impl,
                backend: str = backend,
                query_id: Any = query_id,
                tie_aware: bool = tie_aware,
                order_aware: bool = order_aware,
                order_by: list[int] | None = order_by,
            ) -> None:
                candidate = materialize_rows(impl(contexts[backend]))
                plan = None if order_by is not None else order_plan()
                if order_statuses is not None:
                    order_statuses[(query_id, backend)] = (
                        plan.status if plan is not None else OrderStatus(ORDER_VERIFIED)
                    )
                validator.validate_results_exact(
                    reference,
                    candidate,
                    query_id,
                    0,
                    tie_aware=tie_aware,
                    order_aware=order_aware,
                    order_by=order_by,
                    final_key_tied_beyond_limit=final_key_tied(reference),
                )
                violation = _plan_order_violation(plan, candidate) if plan is not None else None
                if violation is not None:
                    raise ValidationError(f"Q{query_id}: {violation}")

            yield backend, check

    return find_surface_divergences(
        query_ids,
        reference_rows=reference_rows,
        candidate_cells=candidate_cells,
        validation_error=ValidationError,
        reference_failure_cell="reference",
    )


def count_executed_cells(
    query_ids: Iterable[Any],
    dataframe_query: Callable[[Any], Any],
    backends: tuple[str, ...],
) -> dict[str, int]:
    coverage = dict.fromkeys(backends, 0)
    for query_id in query_ids:
        query = dataframe_query(query_id)
        for backend in backends:
            if query.get_impl_for_family(_BACKEND_FAMILIES.get(backend, backend)) is not None:
                coverage[backend] += 1
    return coverage


def _dtype_category(dtype: Any) -> str:
    text = str(dtype).lower()
    if text.startswith(("int", "uint")):
        return "integer"
    if text.startswith(("float", "double", "halffloat")):
        return "float"
    if text.startswith("decimal"):
        return "decimal"
    if text in ("string", "large_string", "utf8", "large_utf8", "string_view", "utf8_view"):
        return "string"
    if text in ("bool", "boolean"):
        return "boolean"
    if text.startswith(("date", "timestamp", "datetime", "time", "duration", "interval", "month_day_nano")):
        return "temporal"
    if text.startswith(("list", "large_list", "fixed_size_list", "array", "struct", "map")):
        return "nested"
    if text == "null":
        return "null"
    if text.startswith(("binary", "large_binary")):
        return "binary"
    return f"other:{text}"


def _dtype_categories_equivalent(reference: str, candidate: str) -> bool:
    if reference == candidate:
        return True
    return reference == "decimal" and candidate == "float"


def _frame_polars_categories(result: Any) -> list[str] | None:
    native = getattr(result, "native", result)
    if hasattr(native, "collect"):
        native = native.collect()
    schema = getattr(native, "schema", None)
    if schema is None:
        return None
    values = schema.values() if hasattr(schema, "values") else schema
    return [_dtype_category(dtype) for dtype in values]


def find_cross_surface_dtype_divergences(
    connection: Any,
    *,
    query_ids: Iterable[Any],
    reference_sql: Callable[[Any], str],
    dataframe_query: Callable[[Any], Any],
    contexts: dict[str, Any],
    backends: tuple[str, ...] = ("expression",),
    skip_keys: frozenset[str] = frozenset(),
    skip_query_ids: frozenset[Any] = frozenset(),
) -> tuple[list[SurfaceDivergence], dict[str, int]]:
    divergences: list[SurfaceDivergence] = []
    compared = dict.fromkeys(backends, 0)
    for query_id in query_ids:
        if query_id in skip_query_ids:
            continue
        try:
            reference_schema = connection.execute(reference_sql(query_id)).fetch_arrow_table().schema
        except Exception as exc:
            divergences.append(SurfaceDivergence(query_id=query_id, cell="reference", detail=f"error: {exc}"))
            continue
        reference_categories = [_dtype_category(field.type) for field in reference_schema]
        query = dataframe_query(query_id)
        for backend in backends:
            key = f"{query_id}_{backend}"
            if key in skip_keys:
                continue
            impl = query.get_impl_for_family(_BACKEND_FAMILIES.get(backend, backend))
            if impl is None:
                continue
            try:
                candidate_categories = _frame_polars_categories(impl(contexts[backend]))
            except Exception as exc:
                divergences.append(SurfaceDivergence(query_id=query_id, cell=backend, detail=f"error: {exc}"))
                continue
            if candidate_categories is None:
                divergences.append(
                    SurfaceDivergence(
                        query_id=query_id,
                        cell=backend,
                        detail="error: frame has no Polars-style schema to compare",
                    )
                )
                continue
            compared[backend] += 1
            if len(candidate_categories) != len(reference_categories):
                divergences.append(
                    SurfaceDivergence(
                        query_id=query_id,
                        cell=backend,
                        detail=(
                            "dtype width mismatch: reference has "
                            f"{len(reference_categories)} columns, frame has {len(candidate_categories)}"
                        ),
                    )
                )
                continue
            for index, (reference, candidate) in enumerate(zip(reference_categories, candidate_categories)):
                if not _dtype_categories_equivalent(reference, candidate):
                    divergences.append(
                        SurfaceDivergence(
                            query_id=query_id,
                            cell=backend,
                            detail=(f"dtype mismatch at column {index}: reference {reference}, frame {candidate}"),
                        )
                    )
    return divergences, compared


_PRODUCTION_ADAPTERS: dict[str, str] = {
    "expression": "benchbox.platforms.dataframe.polars_df:PolarsDataFrameAdapter",
    "pandas": "benchbox.platforms.dataframe.pandas_df:PandasDataFrameAdapter",
    "datafusion": "benchbox.platforms.dataframe.datafusion_df:DataFusionDataFrameAdapter",
}


def build_production_contexts(
    benchmark: Any,
    data_dir: Path,
    *,
    backends: tuple[str, ...] = DATAFRAME_BACKENDS,
    scale_factor: float = EQUIVALENCE_SCALE,
    execution_engine: str | None = None,
) -> dict[str, Any]:
    import importlib

    if execution_engine is not None:
        resolve_requested("polars", execution_engine)
        if "expression" not in backends:
            raise ValueError("An execution engine requires the expression (Polars DataFrame) backend")
    contexts: dict[str, Any] = {}
    for backend in backends:
        target = _PRODUCTION_ADAPTERS.get(backend)
        if target is None:
            raise ValueError(f"No production DataFrame adapter registered for backend {backend!r}")
        module_name, _, class_name = target.partition(":")
        adapter_cls = getattr(importlib.import_module(module_name), class_name)
        adapter_options = {"execution_engine": execution_engine} if backend == "expression" and execution_engine else {}
        adapter = adapter_cls(**adapter_options)
        contexts[backend] = adapter.load_benchmark_into_context(benchmark, Path(data_dir), scale_factor=scale_factor)
    return contexts


_CLICKBENCH_TIE_AMBIGUOUS_REASON = (
    "Q18 is LIMIT 10 with no ORDER BY over ~97k groups - an arbitrary, order-less top-N selection"
)


def _is_clickbench_q18_arbitrary_topn(divergence: SurfaceDivergence) -> bool:
    return "Value mismatch" in str(divergence.detail)


_CLICKBENCH_TIE_AMBIGUOUS = ClassifiedDivergence(
    reason=_CLICKBENCH_TIE_AMBIGUOUS_REASON,
    accepts=_is_clickbench_q18_arbitrary_topn,
    requires_live_divergence=False,
    review_by=date(2027, 1, 3),
)

_AMPLAB_VACUOUS_Q3 = (
    "0 reference rows at the bounded cell (still empty at SF=1.0): GROUP BY sourceIP "
    "HAVING COUNT(*)>10 over a 3-day visitDate window AND searchWord LIKE '%database%' "
    "- no source IP reaches >10 visits in that narrow slice; data density, not a bug."
)
_AMPLAB_VACUOUS_Q5 = (
    "0 reference rows at the bounded SF=0.1 cell: JOIN rankings ON pageRank>1000 "
    "(~111 qualifying rows) then GROUP BY countryCode HAVING COUNT(*)>10 - the join "
    "survivors do not reach 10 per country at the bounded cell; data density, not a bug."
)
_AMPLAB_LEGITIMATELY_EMPTY: dict[Any, str] = {"3": _AMPLAB_VACUOUS_Q3, "5": _AMPLAB_VACUOUS_Q5}

_CLICKBENCH_VACUOUS = (
    "0 reference rows at the bounded SF=0.1 (100k-row) cell: the canonical ClickBench "
    "query keeps an UPSTREAM literal/threshold (a specific UserID/RefererHash/URLHash, "
    "CounterID=62 + the July-2013 EventDate window, HAVING COUNT(*)>100000, or a "
    "LIMIT...OFFSET past the filtered slice) tuned for the ~100M-row upstream dataset; "
    "the synthetic 100k-row generator emits different ids/dates so the filter selects "
    "nothing on either surface. Data/literal artifact, not a load or logic bug. "
    "Tracked: a larger discriminating cell or upstream-faithful literals (do NOT change "
    "the canonical ClickBench query)."
)
_CLICKBENCH_LEGITIMATELY_EMPTY: dict[Any, str] = dict.fromkeys(
    ("Q20", "Q23", "Q28", "Q29", "Q39", "Q40", "Q41", "Q42"), _CLICKBENCH_VACUOUS
)

_DATAVAULT_Q17_VACUOUS = (
    "0 discriminating rows at the bounded gate cell: Q17 is a scalar SUM(...) with no "
    "GROUP BY over the small-quantity-order conjunction (brand + container literals "
    "against part/partsupp/lineitem). Verified 2026-09-26 by executing the reference "
    "SQL over every brand/container combination present in the built cell (25 brands x "
    "40 containers = 1000 combos): every combination returns the single all-NULL row, "
    "so neither surface can discriminate anything. Data/literal artifact of the bounded "
    "cell, not a load or logic bug. Tracked: plant a satisfying brand/container pair "
    "in the generator or gate a larger cell (do NOT change the canonical Q17 query)."
)
_DATAVAULT_LEGITIMATELY_EMPTY: dict[Any, str] = dict.fromkeys(("Q17",), _DATAVAULT_Q17_VACUOUS)

_TPCH_Q17_VACUOUS = (
    "0 discriminating rows at the bounded gate cell (SF=0.01): Q17 is a scalar SUM(...) with "
    "no GROUP BY whose default literals (Brand#23, MED BOX) match no qualifying part/lineitem "
    "rows in the small cell, so the reference returns the single all-NULL row and neither "
    "surface can discriminate anything: the gate asserts only that both surfaces also return "
    "NULL, so a defect in the join, the correlated subquery or the aggregate that still "
    "returns NULL would pass. Verified 2026-10-01 by executing the reference SQL over every "
    "brand/container combination present in the built cell (25 brands x 40 containers = "
    "1000 combos): 856 combinations return a non-NULL value and the other 144, including "
    "the default pair, return the all-NULL row. The all-NULL result is a literal artifact "
    "of the default parameters against the small cell, not a load or logic bug, and the "
    "query is not vacuous in general. Remediation: gate Q17 with a "
    "brand/container pair the cell satisfies on both surfaces (do NOT change the canonical "
    "Q17 query)."
)

_TPCH_SKEW_Q17_VACUOUS = (
    "0 discriminating rows at the bounded gate cell (SF=0.01, seed 42): Q17 is a scalar "
    "SUM(...) with no GROUP BY whose default literals (Brand#23, MED BOX) match no "
    "qualifying part/lineitem rows in the skewed data, so the reference returns the single "
    "all-NULL row and neither surface can discriminate anything: the gate asserts only that "
    "both surfaces also return NULL, so a defect in the join, the correlated subquery or the "
    "aggregate that still returns NULL would pass. Verified 2026-10-01 by executing the "
    "reference SQL over every brand/container combination present in the built cell (25 "
    "brands x 40 containers = 1000 combos): 496 combinations return a non-NULL value and "
    "the other 504, including the default pair, return the all-NULL row. The all-NULL "
    "result is a literal artifact of the default parameters against the skewed "
    "distribution, not a load or logic bug, and the query is not vacuous in general. "
    "Remediation: gate Q17 with a brand/container pair the cell satisfies on "
    "both surfaces (do NOT change the canonical Q17 query)."
)

_JOINORDER_SYNTHETIC_VACUOUS = (
    "Synthetic selectivity, not a bug: the query's multi-table conjunction needs coordinated "
    "real-world literals (specific keywords, notes, countries, ratings, link types) that the "
    "bounded synthetic cell does not plant on one entity set, so the reference join is empty on "
    "BOTH surfaces. Golden entities back the highest-traffic conjunctions (34 discriminating); "
    "this tail stays classified until its literals are planted too. Any query NOT listed here "
    "that goes vacuous fails the gate."
)
_JOINORDER_SYNTHETIC_LEGITIMATELY_EMPTY: dict[Any, str] = dict.fromkeys(
    (
        "1a",
        "1c",
        "2b",
        "2c",
        "3b",
        "5b",
        "7a",
        "7b",
        "7c",
        "8a",
        "8b",
        "9a",
        "9b",
        "9c",
        "9d",
        "11a",
        "11b",
        "11c",
        "12b",
        "13b",
        "13c",
        "14a",
        "14b",
        "14c",
        "15a",
        "15b",
        "15c",
        "15d",
        "17a",
        "17b",
        "17c",
        "17d",
        "18a",
        "18b",
        "18c",
        "19a",
        "19b",
        "19c",
        "19d",
        "20a",
        "20b",
        "20c",
        "21a",
        "21b",
        "21c",
        "22a",
        "22b",
        "22c",
        "22d",
        "23a",
        "23b",
        "23c",
        "24a",
        "24b",
        "25a",
        "25b",
        "25c",
        "26a",
        "26b",
        "26c",
        "27a",
        "27b",
        "27c",
        "28a",
        "28b",
        "28c",
        "29a",
        "29b",
        "29c",
        "30a",
        "30b",
        "30c",
        "31a",
        "31b",
        "31c",
        "32a",
        "33a",
        "33b",
        "33c",
    ),
    _JOINORDER_SYNTHETIC_VACUOUS,
)

_H2ODB_SCALE = 0.01

_H2ODB_PERCENTILE_DECIMAL = (
    "PERCENTILE_CONT over the DECIMAL(8,2) fare_amount column returns a DECIMAL(8,2) "
    "on the SQL surface (continuous percentile rounded to the column's 2-decimal "
    "scale), while the DataFrame surface computes the same linear-interpolated "
    "percentile over float64 (DECIMAL->float at load) and keeps full precision. Both "
    "DataFrame backends use linear interpolation and agree; casting the SQL to DOUBLE "
    "yields the DataFrame value, so the only difference is DuckDB's DECIMAL result "
    "scale - a deterministic, sub-cent presentational/dtype difference, not a logic bug."
)
_H2ODB_Q9_P90_COLUMN = 2
_H2ODB_Q9_RESIDUE_MAX = 0.005 + 1e-6
_VALUE_MISMATCH_RE = re.compile(
    r"Value mismatch at row \d+, column (?P<col>\d+)\. "
    r"Original:\s*(?P<orig>.+?),\s*Variant:\s*(?P<variant>.+?)"
    r"(?:; also columns \[(?P<also>[\d, ]*)\])?$"
)
_NUMBER_RE = re.compile(r"[-+]?\d*\.\d+|[-+]?\d+")


def _mismatched_columns(match: re.Match[str]) -> set[int]:
    columns = {int(match.group("col"))}
    also = match.group("also")
    if also:
        columns.update(int(token) for token in re.findall(r"\d+", also))
    return columns


def _first_number(text: str) -> float | None:
    match = _NUMBER_RE.search(text)
    return float(match.group()) if match else None


def _is_two_decimal_scale(value: str) -> bool:
    number = _NUMBER_RE.search(value)
    if number is None:
        return False
    _, _, frac = number.group().partition(".")
    return len(frac) <= 2


def _h2odb_q9_decimal_residue(divergence: SurfaceDivergence) -> bool:
    match = _VALUE_MISMATCH_RE.search(str(divergence.detail))
    if match is None:
        return False
    if _mismatched_columns(match) != {_H2ODB_Q9_P90_COLUMN}:
        return False
    orig_text = match.group("orig")
    variant_text = match.group("variant")
    orig = _first_number(orig_text)
    variant = _first_number(variant_text)
    if orig is None or variant is None:
        return False
    if not _is_two_decimal_scale(orig_text):
        return False
    return abs(orig - variant) <= _H2ODB_Q9_RESIDUE_MAX


_H2ODB_KNOWN_DIVERGENCES: dict[str, str | ClassifiedDivergence] = {
    **_KNOWN_DIVERGENCES_BASELINE.get("h2odb", {}),
    **dict.fromkeys(
        ("Q9_expression", "Q9_pandas"),
        ClassifiedDivergence(_H2ODB_PERCENTILE_DECIMAL, _h2odb_q9_decimal_residue),
    ),
}


_READ_PRIMITIVES_APPROX = (
    "DuckDB APPROX_COUNT_DISTINCT (HyperLogLog) and APPROX_QUANTILE (T-Digest) are "
    "inherently approximate sketches; the DataFrame computes the exact distinct "
    "count / quantile, so the values differ by the sketch's error - approximate by "
    "construction, not a logic divergence."
)
_READ_PRIMITIVES_SKETCH_RELATIVE_ERROR_MAX = 0.15
_READ_PRIMITIVES_SKETCH_COLUMNS: dict[str, tuple[int, ...]] = {
    "approx_count_distinct_simple": (0,),
    "approx_count_distinct_groupby": (2, 3),
    "approx_quantile_groupby": (1,),
}


def _read_primitives_sketch_residue(divergence: SurfaceDivergence) -> bool:
    match = _VALUE_MISMATCH_RE.search(str(divergence.detail))
    if match is None:
        return False
    query_id = str(divergence.query_id)
    allowed_columns = next(
        (columns for name, columns in _READ_PRIMITIVES_SKETCH_COLUMNS.items() if name in query_id),
        None,
    )
    if allowed_columns is None or not (_mismatched_columns(match) <= set(allowed_columns)):
        return False
    orig = _first_number(match.group("orig"))
    variant = _first_number(match.group("variant"))
    if orig is None or variant is None:
        return False
    denominator = abs(orig) if orig != 0 else abs(variant)
    if denominator == 0:
        return orig == variant
    return abs(orig - variant) / denominator <= _READ_PRIMITIVES_SKETCH_RELATIVE_ERROR_MAX


_READ_PRIMITIVES_DECIMAL_PERCENTILE = (
    "PERCENTILE_CONT over DECIMAL(15,2) returns a 2-decimal value on the SQL surface; "
    "the DataFrame interpolates in float64. Both use linear interpolation - only the "
    "DECIMAL result scale differs (sub-cent), the same presentational class as h2odb Q9."
)
_READ_PRIMITIVES_DECIMAL_ROUND = (
    "ROUND() of a DECIMAL revenue (DuckDB, exact) vs the same product computed in "
    "float64 (DataFrame) flips at a half-cent rounding boundary; a DECIMAL-vs-float "
    "presentational residue (every other column matches), not a logic divergence."
)
_READ_PRIMITIVES_PERCENTILE_P95_COLUMN = 6
_READ_PRIMITIVES_PERCENTILE_RESIDUE_MAX = 0.005 + 1e-6
_READ_PRIMITIVES_ROUND_COLUMN = 6
_READ_PRIMITIVES_ROUND_RESIDUE_MAX = 0.01 + 1e-6


def _read_primitives_decimal_scale_residue(divergence: SurfaceDivergence, *, column: int, max_residue: float) -> bool:
    match = _VALUE_MISMATCH_RE.search(str(divergence.detail))
    if match is None:
        return False
    if _mismatched_columns(match) != {column}:
        return False
    orig_text = match.group("orig")
    orig = _first_number(orig_text)
    variant = _first_number(match.group("variant"))
    if orig is None or variant is None:
        return False
    if not _is_two_decimal_scale(orig_text):
        return False
    return abs(orig - variant) <= max_residue


def _read_primitives_percentile_decimal_residue(divergence: SurfaceDivergence) -> bool:
    return _read_primitives_decimal_scale_residue(
        divergence,
        column=_READ_PRIMITIVES_PERCENTILE_P95_COLUMN,
        max_residue=_READ_PRIMITIVES_PERCENTILE_RESIDUE_MAX,
    )


def _read_primitives_round_decimal_residue(divergence: SurfaceDivergence) -> bool:
    return _read_primitives_decimal_scale_residue(
        divergence,
        column=_READ_PRIMITIVES_ROUND_COLUMN,
        max_residue=_READ_PRIMITIVES_ROUND_RESIDUE_MAX,
    )


_READ_PRIMITIVES_ARGMIN_TIE = (
    "ARG_MIN(p_name, p_retailprice) is non-deterministic when several parts in a brand "
    "share the minimum retail price; DuckDB's tie pick is engine-defined and not "
    "reproducible by a fixed DataFrame tie-break (the min_price value matches exactly)."
)
_READ_PRIMITIVES_ARGMIN_TIE_COLUMNS = (1, 2)


def _read_primitives_argmin_tie(divergence: SurfaceDivergence) -> bool:
    match = _VALUE_MISMATCH_RE.search(str(divergence.detail))
    if match is None:
        return False
    return _mismatched_columns(match) <= set(_READ_PRIMITIVES_ARGMIN_TIE_COLUMNS)


_READ_PRIMITIVES_JSON_TEXT = (
    "JSON_GROUP_ARRAY/JSON_GROUP_OBJECT return JSON *text* with engine-defined element/"
    "key order and number formatting; the DataFrame produces native list/dict "
    "containers. Representational difference, not a logic divergence."
)
_READ_PRIMITIVES_BRAND_DOMAIN = frozenset(f"Brand#{m}{n}" for m in range(1, 6) for n in range(1, 6))
_ORDER_KEY_MISMATCH_RE = re.compile(
    r"Original key:\s*\('(?P<orig>[^']*)',\),\s*Variant key:\s*\('(?P<variant>[^']*)',\)"
)


def _read_primitives_json_parse(text: str) -> Any:
    import ast
    import json

    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return ast.literal_eval(text)


def _read_primitives_json_equivalent(original: str, variant: str) -> bool:

    def normalize(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): normalize(item) for key, item in value.items()}
        if isinstance(value, list):
            return sorted((normalize(item) for item in value), key=str)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return round(float(value), 2)
        return value

    try:
        return normalize(_read_primitives_json_parse(original)) == normalize(_read_primitives_json_parse(variant))
    except (ValueError, SyntaxError):
        return False


def _read_primitives_json_agg_accepts(divergence: SurfaceDivergence) -> bool:
    detail = str(divergence.detail)
    order_match = _ORDER_KEY_MISMATCH_RE.search(detail)
    if order_match is not None:
        return (
            order_match.group("orig") in _READ_PRIMITIVES_BRAND_DOMAIN
            and order_match.group("variant") in _READ_PRIMITIVES_BRAND_DOMAIN
        )
    value_match = _VALUE_MISMATCH_RE.search(detail)
    if value_match is None:
        return False
    if not (_mismatched_columns(value_match) <= {1, 2}):
        return False
    return _read_primitives_json_equivalent(value_match.group("orig"), value_match.group("variant"))


_READ_PRIMITIVES_POLARS_MAP = (
    "Polars (the expression backend) has no native Map dtype, so MAP_FROM_ENTRIES is "
    "unsupported (the query is in the benchmark's SKIP_FOR_POLARS set); there is no "
    "expression-surface result to compare. The pandas surface matches its SQL."
)
_READ_PRIMITIVES_POLARS_MAP_ERROR_SIGNATURE = "not supported on Polars (no native Map dtype)"


def _read_primitives_polars_map_gap(divergence: SurfaceDivergence) -> bool:
    detail = str(divergence.detail)
    return detail.startswith("error: ") and detail.endswith(_READ_PRIMITIVES_POLARS_MAP_ERROR_SIGNATURE)


_READ_PRIMITIVES_SCALE = 0.05

_READ_PRIMITIVES_KNOWN_DIVERGENCES: dict[str, str | ClassifiedDivergence] = {
    **_KNOWN_DIVERGENCES_BASELINE.get("read_primitives", {}),
    **dict.fromkeys(
        (
            "approx_count_distinct_simple_expression",
            "approx_count_distinct_simple_pandas",
            "approx_count_distinct_groupby_expression",
            "approx_count_distinct_groupby_pandas",
            "approx_quantile_groupby_expression",
            "approx_quantile_groupby_pandas",
        ),
        ClassifiedDivergence(_READ_PRIMITIVES_APPROX, _read_primitives_sketch_residue),
    ),
    **dict.fromkeys(
        ("statistical_percentiles_expression", "statistical_percentiles_pandas"),
        ClassifiedDivergence(_READ_PRIMITIVES_DECIMAL_PERCENTILE, _read_primitives_percentile_decimal_residue),
    ),
    **dict.fromkeys(
        ("optimizer_common_subexpression_expression", "optimizer_common_subexpression_pandas"),
        ClassifiedDivergence(_READ_PRIMITIVES_DECIMAL_ROUND, _read_primitives_round_decimal_residue),
    ),
    **dict.fromkeys(
        ("min_by_complex_expression", "min_by_complex_pandas"),
        ClassifiedDivergence(_READ_PRIMITIVES_ARGMIN_TIE, _read_primitives_argmin_tie),
    ),
    **dict.fromkeys(
        ("json_aggregates_expression", "json_aggregates_pandas"),
        ClassifiedDivergence(_READ_PRIMITIVES_JSON_TEXT, _read_primitives_json_agg_accepts),
    ),
    **dict.fromkeys(
        ("map_access_expression", "map_construction_expression", "map_keys_values_expression"),
        ClassifiedDivergence(_READ_PRIMITIVES_POLARS_MAP, _read_primitives_polars_map_gap),
    ),
}
_READ_PRIMITIVES_LEGITIMATELY_EMPTY: dict[Any, str] = {
    **dict.fromkeys(
        ("filter_bigint_selective", "filter_decimal_selective", "filter_decimal_in_list_selective"),
        "Highly selective exact-match filter whose literal does not occur in the bounded "
        "SF=0.05 cell (an orderkey outside the generated range, or an exact DECIMAL price "
        "that the generator never produces), so the SQL reference is empty.",
    ),
    "json_extract_nested": (
        "TPC-H c_comment is free natural-language text, never valid JSON, so "
        "WHERE JSON_VALID(c_comment) matches nothing at any scale - the benchmark has no "
        "JSON source data. The DataFrame surface mirrors the empty result."
    ),
}

_TPCDS_LEGITIMATELY_EMPTY: dict[Any, str] = {
    "3": "No November store sale is for manufacturer 808; dropping that filter alone yields 100 rows.",
    "4": (
        "No customer has web-channel year totals for both 1998 and 1999 beside the store and catalog totals; "
        "dropping either web-year requirement yields rows."
    ),
    "8": (
        "The only preferred-customer ZIP group with more than ten customers is the NULL ZIP (12 customers), "
        "so the ZIP cohort the store prefix must match is empty."
    ),
    "10": (
        "No customer in the five drawn counties with a January-April 2002 store purchase also bought on the web "
        "or by catalog then; dropping that requirement yields a row."
    ),
    "11": (
        "No customer has both store and web year totals for 1998 and 1999; dropping any one channel-year "
        "requirement yields rows."
    ),
    "13": (
        "No store sale satisfies both the demographic and price bands and the "
        "address and profit bands; dropping either group yields a row."
    ),
    "18": (
        "No 2000 catalog sale matches male, Advanced Degree, the six birth months and "
        "the seven states together; dropping any one of those filters yields rows."
    ),
    "20": "Catalog sales end on 2001-11-11, before the drawn 30-day window from 2002-05-11.",
    "23": (
        "No item sells more than four times on one day in 1999-2002 (the maximum "
        "is four), so the frequent-item set is empty."
    ),
    "23b": "No item sells more than four times on one day in 1999-2002, so the frequent-item set is empty.",
    "24": "The only store is in market 2, but the draw binds market 10.",
    "24b": "The only store is in market 2, but the draw binds market 10.",
    "31": "No county has web sales in each of the first three quarters of 2000.",
    "32": (
        "No catalog sale in the 90 days from 2000-01-13 is for manufacturer 942; dropping that filter yields a row."
    ),
    "37": (
        "No item from manufacturers 894, 865, 737 or 959 meets the price, date and inventory filters; dropping "
        "the manufacturer filter alone yields rows."
    ),
    "39": (
        "No warehouse item has a January and February 2000 inventory coefficient of variation above one in both "
        "months; dropping either month filter yields rows."
    ),
    "39b": "The highest January 2000 inventory coefficient of variation is 1.31, below the 1.5 bound.",
    "41": (
        "No item from manufacturers 894-934 has a manufacturer with the correlated color, unit and size "
        "attributes; dropping that correlated count yields rows."
    ),
    "49": (
        "No December 2000 sale in any channel has a return amount above 10000 with the other sale filters; "
        "dropping the year, the month or any one channel's threshold yields rows."
    ),
    "54": "No customer address is in Williamson County TN, the only store's county.",
    "55": ("No November 1999 store sale is for an item of manager 8; dropping the manager filter yields 66 rows."),
    "58": (
        "Five items sell in all three channels in the week of 2000-04-24, but none has revenues within ten "
        "percent of each other in every channel."
    ),
    "61": ("The only store has GMT offset -5, but the draw binds -6; dropping that filter yields a row."),
    "64": (
        "No cross-year store sale and catalog return pair meets the item-color filter; dropping the six drawn "
        "colors yields 47 rows."
    ),
    "65": (
        "No item's store revenue in month sequence 1176-1187 is at or below ten percent of its store's average; "
        "dropping that bound yields 90 rows."
    ),
    "69": (
        "No WY, IA or KY customer has an April-June 2003 store purchase without web or catalog purchases then; "
        "dropping the store requirement or the year yields rows."
    ),
    "73": (
        "No 1998-2000 Williamson County ticket on days one or two has one to five items for the household "
        "filters; dropping the item-count bound yields 12 rows."
    ),
    "82": (
        "No item from manufacturers 759, 224, 231 or 687 meets the price, date and inventory filters; dropping "
        "the manufacturer filter alone yields rows."
    ),
    "85": ("No 2000 web return meets the demographic, state and profit bands; dropping the year filter yields a row."),
    "90": (
        "Web page character counts never reach the template's fixed 5000-5200 range; dropping that range yields a row."
    ),
    "91": (
        "No December 1998 call-center return meets the demographic and GMT-offset filters; dropping the month "
        "filter yields a row."
    ),
    "92": ("No web sale in the 90 days from 2000-01-13 is for manufacturer 942; dropping that filter yields a row."),
    "93": "The reason table has one row, and it is not the drawn 'reason 33'.",
}


_FLIGHTDATA_SCALE = 0.01
_DATAVAULT_SCALE = 0.01
_TPCH_SCALE = 0.01
_TPCDS_SCALE = 0.01
_NYCTAXI_SCALE = 0.01
_TSBS_DEVOPS_SCALE = 0.01
_TPCH_SKEW_SCALE = 0.01

GATES: dict[str, CrossSurfaceGate] = {
    "ssb": CrossSurfaceGate(
        name="ssb",
        build=build_ssb_duckdb,
        surface_independence=SURFACE_INDEPENDENCE_SHARED_SPEC,
        surface_independence_rationale=(
            "Both DataFrame backends are generated from compact SSB query metadata; the independent "
            "signal is SQL text versus the shared generated DataFrame spec."
        ),
    ),
    "amplab": CrossSurfaceGate(
        name="amplab",
        build=build_amplab_duckdb,
        legitimately_empty=_AMPLAB_LEGITIMATELY_EMPTY,
        surface_independence=SURFACE_INDEPENDENCE_SEPARATE,
        surface_independence_rationale=(
            "Expression and pandas DataFrame implementations are separately handwritten for each query, "
            "so the gate has stronger cross-implementation signal than shared-spec generators."
        ),
    ),
    "coffeeshop": CrossSurfaceGate(
        name="coffeeshop",
        build=build_coffeeshop_duckdb,
        surface_independence=SURFACE_INDEPENDENCE_SEPARATE,
        surface_independence_rationale=(
            "Expression and pandas DataFrame implementations are separately handwritten for each query, "
            "so the gate has stronger cross-implementation signal than shared-spec generators."
        ),
    ),
    "clickbench": CrossSurfaceGate(
        name="clickbench",
        build=build_clickbench_duckdb,
        known_divergences={
            **_KNOWN_DIVERGENCES_BASELINE.get("clickbench", {}),
            "Q18_expression": _CLICKBENCH_TIE_AMBIGUOUS,
            "Q18_pandas": _CLICKBENCH_TIE_AMBIGUOUS,
        },
        legitimately_empty=_CLICKBENCH_LEGITIMATELY_EMPTY,
        surface_independence=SURFACE_INDEPENDENCE_MIXED,
        surface_independence_rationale=(
            "Most ClickBench DataFrame cells are generated from shared compact specs, with a small set "
            "of bespoke implementations; read as mixed provenance, not fully independent implementations."
        ),
    ),
    "joinorder_synthetic": CrossSurfaceGate(
        name="joinorder_synthetic",
        build=build_joinorder_synthetic_duckdb,
        legitimately_empty=_JOINORDER_SYNTHETIC_LEGITIMATELY_EMPTY,
        surface_independence=SURFACE_INDEPENDENCE_SHARED_SPEC,
        surface_independence_rationale=(
            "Both DataFrame families are generated through shared JoinOrder translation helpers, so the "
            "gate primarily catches SQL-vs-generated-DataFrame transcription drift."
        ),
    ),
    "h2odb": CrossSurfaceGate(
        name="h2odb",
        build=build_h2odb_duckdb,
        known_divergences=_H2ODB_KNOWN_DIVERGENCES,
        surface_independence=SURFACE_INDEPENDENCE_SEPARATE,
        surface_independence_rationale=(
            "H2O-DB expression and pandas DataFrame implementations are separately handwritten for each query."
        ),
        scale_factor=_H2ODB_SCALE,
    ),
    "read_primitives": CrossSurfaceGate(
        name="read_primitives",
        build=build_read_primitives_duckdb,
        known_divergences=_READ_PRIMITIVES_KNOWN_DIVERGENCES,
        dtype_skip_keys=frozenset(
            {
                "approx_quantile_groupby_expression",
                "json_aggregates_expression",
                "map_access_expression",
                "map_construction_expression",
                "map_keys_values_expression",
            }
        ),
        legitimately_empty=_READ_PRIMITIVES_LEGITIMATELY_EMPTY,
        treat_nan_as_null=True,
        surface_independence=SURFACE_INDEPENDENCE_MIXED,
        surface_independence_rationale=(
            "Read Primitives combines explicit family implementations with factory-built/query-catalog "
            "implementations, so provenance is mixed rather than wholly independent."
        ),
        scale_factor=_READ_PRIMITIVES_SCALE,
    ),
    "flightdata": CrossSurfaceGate(
        name="flightdata",
        build=build_flightdata_duckdb,
        surface_independence=SURFACE_INDEPENDENCE_SEPARATE,
        surface_independence_rationale=(
            "FlightData expression and pandas DataFrame implementations are separately handwritten for each "
            "query (expression helpers plus a compact pandas metadata DSL), so the gate has stronger "
            "cross-implementation signal than shared-spec generators."
        ),
        scale_factor=_FLIGHTDATA_SCALE,
    ),
    "datavault": CrossSurfaceGate(
        name="datavault",
        build=build_datavault_duckdb,
        legitimately_empty=_DATAVAULT_LEGITIMATELY_EMPTY,
        surface_independence=SURFACE_INDEPENDENCE_SEPARATE,
        surface_independence_rationale=(
            "Data Vault expression and pandas DataFrame implementations are separately handwritten for each "
            "query, so the gate has stronger cross-implementation signal than shared-spec generators."
        ),
        scale_factor=_DATAVAULT_SCALE,
    ),
    "tpcds": CrossSurfaceGate(
        name="tpcds",
        build=build_tpcds_duckdb,
        legitimately_empty=_TPCDS_LEGITIMATELY_EMPTY,
        backends=("expression", "pandas", "datafusion"),
        surface_independence=SURFACE_INDEPENDENCE_SEPARATE,
        surface_independence_rationale=(
            "TPC-DS expression and pandas DataFrame implementations are separately handwritten for each query."
        ),
        scale_factor=_TPCDS_SCALE,
    ),
    "tpch": CrossSurfaceGate(
        name="tpch",
        build=build_tpch_duckdb,
        legitimately_empty={"17": _TPCH_Q17_VACUOUS},
        surface_independence=SURFACE_INDEPENDENCE_SEPARATE,
        surface_independence_rationale=(
            "TPC-H expression and pandas DataFrame implementations are separately handwritten for each query."
        ),
        scale_factor=_TPCH_SCALE,
    ),
    "nyctaxi": CrossSurfaceGate(
        name="nyctaxi",
        build=build_nyctaxi_duckdb,
        legitimately_empty={
            "airport-trips": "Synthetic generator hardcodes rate_code_id=1; query filters rate_code_id IN (2, 3), so the reference returns 0 rows.",
        },
        surface_independence=SURFACE_INDEPENDENCE_SEPARATE,
        surface_independence_rationale=(
            "NYC Taxi expression and pandas DataFrame implementations are separately handwritten for each query."
        ),
        scale_factor=_NYCTAXI_SCALE,
    ),
    "tsbs_devops": CrossSurfaceGate(
        name="tsbs_devops",
        build=build_tsbs_devops_duckdb,
        legitimately_empty={
            "high-cpu-1-hr": "Generator caps usage_user at ~55; usage_user > 90 never fires at SF 0.01-0.1.",
            "high-cpu-12-hr": "Generator caps usage_user at ~55; HAVING COUNT(*) > 10 over usage_user > 90 never fires.",
            "low-memory-hosts": "Generator floor for available_percent is ~17; MIN(available_percent) < 10 never fires.",
        },
        surface_independence=SURFACE_INDEPENDENCE_SEPARATE,
        surface_independence_rationale=(
            "TSBS DevOps expression and pandas DataFrame implementations are separately handwritten for each query."
        ),
        scale_factor=_TSBS_DEVOPS_SCALE,
    ),
    "tpch_skew": CrossSurfaceGate(
        name="tpch_skew",
        build=build_tpch_skew_duckdb,
        legitimately_empty={
            "8": "Skewed generator emits zero 'ECONOMY ANODIZED STEEL' part rows at SF 0.01-0.1; reference returns 0 rows.",
            "17": _TPCH_SKEW_Q17_VACUOUS,
        },
        surface_independence=SURFACE_INDEPENDENCE_SEPARATE,
        surface_independence_rationale=(
            "TPC-H Skew expression and pandas DataFrame implementations are separately handwritten for each query."
        ),
        scale_factor=_TPCH_SKEW_SCALE,
    ),
}


STAGED_GATES: dict[str, CrossSurfaceGate] = {}


def get_gate(name: str) -> CrossSurfaceGate:
    if name in GATES:
        return GATES[name]
    return STAGED_GATES[name]


_BASELINE_YAML_PATH = Path(__file__).parent / "cross_surface_baseline.yaml"


def _resolved_baseline_keys(
    known: dict[str, str | ClassifiedDivergence], divergences: list[SurfaceDivergence]
) -> list[str]:
    found = {d.key for d in divergences}
    return sorted(key for key, entry in known.items() if key not in found and _requires_live_divergence(entry))


def _apply_baseline_update(
    gate: CrossSurfaceGate,
    divergences: list[SurfaceDivergence],
    total: int,
    coverage: dict[str, int],
    reference_row_counts: dict[Any, int],
    vacuous_cells: int,
    all_null_references: set[Any] | None = None,
) -> int:
    from benchbox.core.equivalence.known_divergences_baseline import update_baseline_file

    resolved = _resolved_baseline_keys(gate.known_divergences, divergences)
    known_after_update = {key: entry for key, entry in gate.known_divergences.items() if key not in resolved}
    exit_code = _report(
        divergences,
        total,
        coverage,
        known_after_update,
        benchmark=gate.name,
        reference_row_counts=reference_row_counts,
        legitimately_empty=gate.legitimately_empty,
        scale_factor=gate.scale_factor,
        vacuous_cells=vacuous_cells,
        all_null_references=all_null_references,
    )

    if exit_code != 0:
        print(f"{gate.name}: refusing to update baseline - gate reported other failure(s) above; nothing written.")
        return exit_code

    if not resolved:
        print(f"{gate.name}: no resolved known-divergence entries; baseline unchanged.")
        return exit_code

    removed = update_baseline_file(_BASELINE_YAML_PATH, resolved, gate.name)
    if removed:
        print(f"{gate.name}: removed resolved known-divergence baseline entries: {removed}")
    code_only = sorted(set(resolved) - set(removed))
    if code_only:
        print(
            f"{gate.name}: resolved entries {code_only} are code-only (ClassifiedDivergence) and cannot be "
            "auto-pruned; remove them manually from cross_surface.py in a reviewed change."
        )
        return 1
    return exit_code


def find_flaky_cells(runs: list[list[SurfaceDivergence]]) -> list[tuple[str, str, list[str | None]]]:
    per_run = [{(str(d.query_id), d.cell): d.detail for d in divergences} for divergences in runs]
    keys = {key for seen in per_run for key in seen}
    outcomes = {key: [seen.get(key) for seen in per_run] for key in keys}
    flaky = [(query, cell, results) for (query, cell), results in outcomes.items() if len(set(results)) > 1]
    return sorted(flaky, key=lambda item: (item[0].zfill(8), item[1]))


def _report_flaky(name: str, flaky: list[tuple[str, str, list[str | None]]], repeats: int, *, enforced: bool) -> None:
    from rich.text import Text

    from benchbox.utils.printing import emit

    def line(message: str) -> None:
        emit(Text(message, no_wrap=True, overflow="ignore"), quiet=False)

    verdict = "FAIL (enforced gate)" if enforced else "report only (staged gate)"
    line(f"\n[flaky - outcome changed between {repeats} identical runs] {name}: {len(flaky)} cell(s), {verdict}")
    for query, cell, results in flaky:
        diverged = sum(1 for result in results if result is not None)
        detail = next(result for result in results if result is not None)
        line(f"  {query}_{cell}: diverged in {diverged} of {repeats} runs; e.g. {detail[:200]}")


def run_gate(
    gate: CrossSurfaceGate,
    *,
    update_baseline: bool = False,
    repeats: int = 1,
    execution_engine: str | None = None,
) -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        data = gate.build(gate.scale_factor, Path(tmp))
        connection = data.connection
        reference_row_counts: dict[Any, int] = {}
        all_null_references: set[Any] = set()
        order_statuses: dict[tuple[Any, str], OrderStatus] = {}
        try:
            if gate.name == "tpcds":
                from benchbox.core.equivalence.builders.tpcds import validate_tpcds_gate_data
                from benchbox.core.results.canonical_json import canonical_json_text

                if not gate.backends or len(set(gate.backends)) != len(gate.backends):
                    raise ValueError("TPC-DS requires distinct nonempty backend selection")
                validate_tpcds_gate_data(data)
                console.print(
                    canonical_json_text(data.query_parameters), markup=False, highlight=False, soft_wrap=True, end=""
                )
            contexts = build_production_contexts(
                data.benchmark,
                data.data_dir,
                backends=gate.backends,
                scale_factor=gate.scale_factor,
                execution_engine=execution_engine,
            )
            divergences = find_cross_surface_divergences(
                connection,
                query_ids=data.query_ids,
                reference_sql=data.reference_sql,
                dataframe_query=data.dataframe_query,
                contexts=contexts,
                validator=gate.build_validator(),
                backends=gate.backends,
                reference_row_counts=reference_row_counts,
                all_null_references=all_null_references,
                order_statuses=order_statuses,
            )
            repeat_runs = [divergences]
            for _ in range(repeats - 1):
                repeat_runs.append(
                    find_cross_surface_divergences(
                        connection,
                        query_ids=data.query_ids,
                        reference_sql=data.reference_sql,
                        dataframe_query=data.dataframe_query,
                        contexts=contexts,
                        validator=gate.build_validator(),
                        backends=gate.backends,
                    )
                )
            coverage = count_executed_cells(data.query_ids, data.dataframe_query, gate.backends)
            vacuous_cells = count_executed_cells(
                [qid for qid, n in reference_row_counts.items() if n == 0],
                data.dataframe_query,
                gate.backends,
            )
        finally:
            connection.close()

    total = len(data.query_ids) * len(gate.backends)
    vacuous_cells_total = sum(vacuous_cells.values())
    flaky = find_flaky_cells(repeat_runs) if repeats > 1 else []
    enforced = gate.name in GATES

    if update_baseline:
        if flaky:
            _report_flaky(gate.name, flaky, repeats, enforced=True)
            return 1
        return _apply_baseline_update(
            gate, divergences, total, coverage, reference_row_counts, vacuous_cells_total, all_null_references
        )

    exit_code = _report(
        divergences,
        total,
        coverage,
        gate.known_divergences,
        benchmark=gate.name,
        reference_row_counts=reference_row_counts,
        legitimately_empty=gate.legitimately_empty,
        scale_factor=gate.scale_factor,
        vacuous_cells=vacuous_cells_total,
        all_null_references=all_null_references,
        enforce_vacuity=gate.vacuity_classified,
        order_statuses=order_statuses,
    )
    if flaky:
        _report_flaky(gate.name, flaky, repeats, enforced=enforced)
        if enforced:
            return exit_code or 1
    return exit_code


def _classification(known: dict[str, str | ClassifiedDivergence], divergence: SurfaceDivergence) -> str | None:
    entry = known.get(divergence.key)
    if entry is None:
        return None
    if isinstance(entry, ClassifiedDivergence):
        return entry.reason if entry.accepts(divergence) else None
    return entry


def _requires_live_divergence(entry: str | ClassifiedDivergence) -> bool:
    return entry.requires_live_divergence if isinstance(entry, ClassifiedDivergence) else True


def _emit_report_lines(lines: list[str]) -> None:
    from benchbox.utils.printing import get_console

    output = get_console(quiet=False)
    for line in lines:
        output.print(line, markup=False, highlight=False, soft_wrap=True)


def _order_report_lines(order_statuses: dict[tuple[Any, str], OrderStatus]) -> list[str]:
    if not order_statuses:
        return []
    kinds = Counter(status.kind for status in order_statuses.values())
    unordered = kinds[ORDER_UNORDERED] + kinds[ORDER_UNVERIFIABLE]
    lines = [
        f"  returned order: {kinds[ORDER_VERIFIED]} of {len(order_statuses)} cells verified, {unordered} compared "
        f"unordered ({kinds[ORDER_UNORDERED]} without ORDER BY, {kinds[ORDER_UNVERIFIABLE]} with an ORDER BY "
        "that cannot be checked)"
    ]
    reasons = sorted(
        {
            (str(query_id), status.reason)
            for (query_id, _), status in order_statuses.items()
            if status.kind == ORDER_UNVERIFIABLE
        }
    )
    lines.extend(f"    unverifiable ORDER BY, query {query_id}: {reason}" for query_id, reason in reasons)
    return lines


def _report(
    divergences: list[SurfaceDivergence],
    total: int,
    coverage: dict[str, int],
    known: dict[str, str | ClassifiedDivergence],
    *,
    benchmark: str,
    reference_row_counts: dict[Any, int] | None = None,
    legitimately_empty: dict[Any, str] | None = None,
    scale_factor: float = EQUIVALENCE_SCALE,
    vacuous_cells: int | None = None,
    all_null_references: set[Any] | None = None,
    enforce_vacuity: bool = True,
    order_statuses: dict[tuple[Any, str], OrderStatus] | None = None,
) -> int:
    legitimately_empty = legitimately_empty or {}
    reference_row_counts = reference_row_counts or {}
    all_null_references = all_null_references or set()

    found = {d.key for d in divergences}
    new = sorted({d.key for d in divergences if _classification(known, d) is None})
    resolved = sorted(key for key, entry in known.items() if key not in found and _requires_live_divergence(entry))
    missing_backends = sorted(backend for backend, count in coverage.items() if count == 0)
    executed = sum(coverage.values())

    review_due = sorted(
        key
        for key, entry in known.items()
        if isinstance(entry, ClassifiedDivergence) and entry.review_by is not None and entry.review_by < date.today()
    )

    vacuous = sorted(qid for qid, count in reference_row_counts.items() if count == 0)
    classified_empty = [qid for qid in vacuous if qid in legitimately_empty]
    unclassified_empty = [qid for qid in vacuous if qid not in legitimately_empty]
    all_null = [qid for qid in vacuous if qid in all_null_references]
    stale_empty = sorted(
        (qid for qid in legitimately_empty if reference_row_counts.get(qid, 0) > 0), key=lambda qid: str(qid)
    )
    if not enforce_vacuity:
        classified_empty, unclassified_empty, stale_empty = [], [], []

    if vacuous_cells is not None:
        vacuous_executed = vacuous_cells
    else:
        implemented_backends = sum(1 for count in coverage.values() if count)
        vacuous_executed = len(vacuous) * implemented_backends if reference_row_counts else 0
    discriminating = max(executed - vacuous_executed, 0)

    print(f"{benchmark} cross-surface SQL<->DataFrame equivalence @ SF={scale_factor} (DuckDB-backed)")
    print(
        f"  compared {discriminating} of {total} query-backend cells "
        f"({total - executed} not implemented by the DataFrame surface, "
        f"{vacuous_executed} vacuous empty-vs-empty) - {len(divergences)} divergent"
    )
    lines = []
    if vacuous:
        lines.append(f"  vacuous queries: {len(vacuous) - len(all_null)} zero-row, {len(all_null)} single all-NULL row")
        if not enforce_vacuity:
            lines.append(
                "  vacuity is classified for the default parameter draw only; this draw lists it without failing: "
                f"{vacuous}"
            )
    lines.extend(_order_report_lines(order_statuses or {}))
    _emit_report_lines([*lines, ""])

    by_class: dict[str, list[SurfaceDivergence]] = {}
    for divergence in sorted(divergences, key=lambda d: d.key):
        klass = _classification(known, divergence) or "UNCLASSIFIED"
        by_class.setdefault(klass, []).append(divergence)
    for klass in sorted(by_class):
        print(f"  [{klass}]")
        for divergence in by_class[klass]:
            print(f"    {divergence.key}: {divergence.detail}")
        print()

    if classified_empty:
        print("  [legitimately-empty - classified, NON-discriminating]")
        for qid in classified_empty:
            label = " [all-NULL row]" if qid in all_null_references else ""
            print(f"    {qid}{label}: {legitimately_empty[qid]}")
        print()

    if missing_backends:
        print(f"GATE FAILURE - gated backend(s) implement no queries (nothing compared): {missing_backends}")
    if new:
        print(f"GATE FAILURE - unclassified cross-surface divergences: {new}")
    if unclassified_empty:
        print(
            "GATE FAILURE - vacuous empty-vs-empty queries (0 reference rows) not classified "
            f"legitimately_empty: {unclassified_empty} - make them discriminating or classify them with a rationale"
        )
    if stale_empty:
        _emit_report_lines(
            [
                f"GATE FAILURE - legitimately_empty entries whose reference now returns rows: {stale_empty} "
                "- remove the stale classification in a reviewed change"
            ]
        )
    if resolved:
        print(
            "GATE FAILURE - previously-known divergences now equivalent: "
            f"{resolved} - remove the stale baseline entry in a reviewed change"
        )
    if review_due:
        for key in review_due:
            entry = known[key]
            assert isinstance(entry, ClassifiedDivergence)
            print(f"WAIVER REVIEW DUE - {key}: review_by {entry.review_by} has passed - {entry.reason}")
    if not new and not resolved and not missing_backends and not unclassified_empty and not stale_empty:
        suffix = " (modulo classified exceptions)" if (known or classified_empty) else ""
        print(f"SQL and DataFrame surfaces are equivalent{suffix}.")
    return 1 if (new or resolved or missing_backends or unclassified_empty or stale_empty) else 0


def main(argv: list[str] | None = None) -> int:
    import argparse
    from dataclasses import replace
    from functools import partial

    parser = argparse.ArgumentParser(description="Cross-surface SQL<->DataFrame equivalence gate.")
    parser.add_argument(
        "--benchmark",
        choices=sorted({**GATES, **STAGED_GATES}),
        default="ssb",
        help="Benchmark to gate (default: ssb). Staged gates run in report mode but may diverge.",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help=(
            "Explicit maintenance writer: drop any known-divergence entries that no longer "
            "reproduce from this benchmark's YAML baseline (cross_surface_baseline.yaml). "
            "Only writes when the run is otherwise completely clean - any other gate failure "
            "(unclassified divergences, vacuous queries, missing backends) leaves the baseline "
            "untouched and still fails the command (non-zero exit). Idempotent on a second run."
        ),
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help=(
            "Run the comparison this many times against the same data. A cell whose outcome differs "
            "between runs is reported as flaky and fails an enforced gate (a staged gate only reports "
            "it). Default 1: output, exit code and runtime are unchanged."
        ),
    )
    parser.add_argument("--backend", action="append", choices=sorted(_PRODUCTION_ADAPTERS), help="Backend to compare.")
    parser.add_argument("--seed", type=int, help="TPC-DS Power Test query seed.")
    parser.add_argument("--power-stream", type=int, help="TPC-DS Power stream ID (default: 0).")
    parser.add_argument("--execution-engine", help="Execution engine for the Polars DataFrame surface.")
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if args.benchmark != "tpcds" and (args.seed is not None or args.power_stream is not None):
        parser.error("--seed and --power-stream apply only to TPC-DS")
    if (args.seed is not None and args.seed < 0) or (args.power_stream is not None and args.power_stream < 0):
        parser.error("--seed and --power-stream must be nonnegative")
    if args.backend is not None and len(set(args.backend)) != len(args.backend):
        parser.error("--backend must not repeat a backend")
    if args.update_baseline and (args.backend is not None or args.seed is not None or args.power_stream is not None):
        parser.error("--update-baseline requires the default draw and backend selection")
    gate = get_gate(args.benchmark)
    if args.backend is not None:
        gate = replace(gate, backends=tuple(args.backend))
    if args.benchmark == "tpcds":
        gate = replace(gate, build=partial(build_tpcds_duckdb, seed=args.seed, stream_id=args.power_stream or 0))
        if args.seed is not None or args.power_stream:
            gate = replace(gate, vacuity_classified=False)
    return run_gate(
        gate,
        update_baseline=args.update_baseline,
        repeats=args.repeats,
        execution_engine=args.execution_engine,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
