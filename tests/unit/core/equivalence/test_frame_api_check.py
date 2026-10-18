from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from benchbox.core.equivalence.frame_api_check import (
    check_module_frame_api,
    iter_query_modules,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

_REPO_ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def frame_api() -> frozenset[str]:
    from benchbox.platforms.dataframe.unified_frame import UnifiedLazyFrame

    return frozenset(dir(UnifiedLazyFrame))


def _check_source(source: str, frame_api: frozenset[str]):
    tree = ast.parse(textwrap.dedent(source))
    from benchbox.core.equivalence.frame_api_check import check_expression_impl_frame_api

    (func,) = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    return check_expression_impl_frame_api(func, module="synthetic", frame_api=frame_api)


def test_missing_frame_method_is_flagged(frame_api):
    violations = _check_source(
        """
        def q1_expression_impl(ctx):
            df = ctx.get_table("t")
            return df.rename_columns({"a": "b"})
        """,
        frame_api,
    )
    assert [v.method for v in violations] == ["rename_columns"]


def test_singular_with_column_is_flagged(frame_api):
    violations = _check_source(
        """
        def q1_expression_impl(ctx):
            df = ctx.get_table("t")
            return df.with_column("x")
        """,
        frame_api,
    )
    assert [v.method for v in violations] == ["with_column"]


def test_real_frame_methods_are_clean(frame_api):
    violations = _check_source(
        """
        def q1_expression_impl(ctx):
            col, lit = ctx.col, ctx.lit
            df = ctx.get_table("t").filter(col("a") > lit(0))
            grouped = df.group_by("k").agg(col("v").sum().alias("s"), col("v").count().alias("n"))
            return grouped.select(ctx.when(col("n") > lit(0)).then(col("s")).otherwise(lit(None)))
        """,
        frame_api,
    )
    assert violations == []


def test_expression_calls_are_not_checked_against_the_frame(frame_api):
    violations = _check_source(
        """
        def q1_expression_impl(ctx):
            col, lit = ctx.col, ctx.lit
            df = ctx.get_table("t")
            return df.with_columns((col("a") * lit(2)).alias("b"))
        """,
        frame_api,
    )
    assert violations == []


def test_all_registered_query_modules_call_real_frame_methods(frame_api):
    import ast as _ast

    modules = iter_query_modules(_REPO_ROOT / "benchbox")
    names = [path.name for path in modules]
    assert len(modules) > 10, f"expected many query modules, found {names}"
    assert "queries.py" in names
    assert any("datavault" in str(path) for path in modules), names

    impl_count = 0
    violations = []
    for path in modules:
        tree = _ast.parse(path.read_text(encoding="utf-8"))
        impl_count += sum(
            1
            for node in _ast.walk(tree)
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and node.name.endswith("_expression_impl")
        )
        violations.extend(v.describe() for v in check_module_frame_api(path, frame_api=frame_api))
    assert impl_count > 100, f"expected many checked impls, found {impl_count}"
    assert not violations, "query modules call missing UnifiedLazyFrame methods:\n" + "\n".join(violations)
