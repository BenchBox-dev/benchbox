# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark(TM) DS (TPC-DS) - Copyright (c) Transaction Processing Performance Council

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import ast
import re
from collections import defaultdict
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml

from benchbox.core.tpcds.dataframe_queries import parameter_adapters
from benchbox.core.tpcds.dataframe_queries.parameter_adapters import ADAPTERS

pytestmark = [pytest.mark.unit, pytest.mark.fast, pytest.mark.tpcds]

PACKAGE = Path(parameter_adapters.__file__).parent

PENDING_ADAPTER: frozenset[int] = frozenset()

STRUCTURAL_KEYS: dict[tuple[int, str], str] = {}

PARAMETER_MODULES = {"queries.py", "parameters.py"}
FILTER_CARRIERS = {"_equal_filter_expression", "_equal_filter_pandas"}
RESOLVED_ELSEWHERE = {"_filter_value", "_resolve_joined_agg_value", *FILTER_CARRIERS}
SPEC_ENGINES = {
    "joined_aggregate": ("_joined_agg_expression_impl", "_joined_agg_pandas_impl"),
    "state_average_returns": ("_state_average_returns_expression_impl", "_state_average_returns_pandas_impl"),
}


class UnresolvedRead(AssertionError):
    pass


@dataclass(frozen=True)
class Read:
    query_id: int
    key: str
    fallback: bool
    where: str


def _is_none(node: ast.expr | None) -> bool:
    return node is None or (isinstance(node, ast.Constant) and node.value is None)


def _params_receiver(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "params"
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "get_parameters"


def _literal_query_ids(function: ast.FunctionDef) -> set[int]:
    return {
        node.args[0].value
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "get_parameters"
        and isinstance(node.args[0], ast.Constant)
    }


def _evaluate(node: ast.expr, environment: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in environment:
        return environment[node.id]
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        return _evaluate(node.value, environment)[node.slice.value]
    raise UnresolvedRead(ast.unparse(node))


def _helper_environment(function: ast.FunctionDef, arguments: list[Any]) -> dict[str, Any]:
    names = [argument.arg for argument in function.args.args]
    defaults = function.args.defaults
    environment = {
        name: ast.literal_eval(default) for name, default in zip(names[len(names) - len(defaults) :], defaults)
    }
    environment.update(zip(names[2:], arguments))
    return environment


def _reads_parameters(function: ast.FunctionDef) -> bool:
    return any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == "get" and _params_receiver(node.func.value))
            or (isinstance(node.func, ast.Name) and node.func.id in FILTER_CARRIERS | {"_filter_value"})
        )
        for node in ast.walk(function)
    )


def _sites(function: ast.FunctionDef, environment: dict[str, Any]) -> list[tuple[str, bool]]:
    if function.name in RESOLVED_ELSEWHERE:
        return []
    found = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if isinstance(callee, ast.Attribute) and callee.attr == "get" and _params_receiver(callee.value):
            fallback = len(node.args) > 1 and not _is_none(node.args[1])
            found.append((_evaluate(node.args[0], environment), fallback))
        elif isinstance(callee, ast.Name) and callee.id == "_filter_value":
            key = _evaluate(node.args[1], environment)
            if key is not None:
                found.append((key, True))
        elif isinstance(callee, ast.Name) and callee.id in FILTER_CARRIERS:
            found.extend(
                (key, default is not None)
                for _column, key, default in _evaluate(node.args[2], environment)
                if key is not None
            )
    return found


def _yaml_param_reads(node: Any) -> list[tuple[str, bool]]:
    if isinstance(node, dict):
        reads = [(node["param"], node.get("default") is not None)] if "param" in node else []
        return reads + [read for value in node.values() for read in _yaml_param_reads(value)]
    if isinstance(node, list):
        return [read for value in node for read in _yaml_param_reads(value)]
    return []


Entries = dict[int, list[tuple[str, dict[str, Any]]]]


def _entry_points(functions: dict[str, ast.FunctionDef]) -> tuple[Entries, list[Read]]:
    entries: Entries = defaultdict(list)
    for name in functions:
        match = re.fullmatch(r"q(\d+)_(?:expression|pandas)_impl", name)
        if match:
            entries[int(match.group(1))].append((name, {}))
    helper_file = (PACKAGE / "helper_query_specs.yaml").read_text(encoding="utf-8")
    for spec in yaml.safe_load(helper_file)["helper_queries"]:
        for family in ("expression", "pandas"):
            helper = spec[f"{family}_helper"]
            entries[spec["query_id"]].append((helper, _helper_environment(functions[helper], spec["args"])))
    query_specs = yaml.safe_load((PACKAGE / "query_specs.yaml").read_text(encoding="utf-8"))
    assert set(query_specs) == set(SPEC_ENGINES), "teach this test the new kind of query spec"
    spec_reads = []
    for kind, specs in query_specs.items():
        for spec in specs:
            entries[spec["query_id"]].extend((engine, {}) for engine in SPEC_ENGINES[kind])
            spec_reads.extend(
                Read(spec["query_id"], key, fallback, "query_specs.yaml") for key, fallback in _yaml_param_reads(spec)
            )
    return entries, spec_reads


def _reached_by(entries: Entries, functions: dict[str, ast.FunctionDef]) -> dict[str, set[int]]:
    references = {
        name: {
            node.id
            for node in ast.walk(function)
            if isinstance(node, ast.Name) and node.id in functions and node.id != name
        }
        for name, function in functions.items()
    }
    reached_by: dict[str, set[int]] = defaultdict(set)
    for query_id, roots in entries.items():
        pending = [name for name, _ in roots]
        while pending:
            name = pending.pop()
            if query_id not in reached_by[name]:
                reached_by[name].add(query_id)
                pending.extend(references[name])
    return reached_by


@cache
def _reads() -> tuple[Read, ...]:
    tree = ast.parse((PACKAGE / "queries.py").read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    entries, spec_reads = _entry_points(functions)
    reached_by = _reached_by(entries, functions)

    reads = list(spec_reads)
    orphans = []
    for name, function in functions.items():
        queries = _literal_query_ids(function) or reached_by[name]
        if not queries and _reads_parameters(function) and name not in RESOLVED_ELSEWHERE:
            orphans.append(name)
        for query_id in queries:
            environment = next((env for root, env in entries.get(query_id, []) if root == name), {})
            try:
                found = _sites(function, environment)
            except UnresolvedRead as error:
                raise AssertionError(f"{name}: cannot resolve the parameter key {error}") from error
            reads.extend(Read(query_id, key, fallback, name) for key, fallback in found)
    assert not orphans, f"parameter reads that no query reaches: {sorted(orphans)}"
    return tuple(sorted(set(reads), key=lambda read: (read.query_id, read.key, read.where, read.fallback)))


@cache
def _adapter_keys(query_id: int) -> tuple[frozenset[str], tuple[re.Pattern[str], ...]]:
    tree = ast.parse((PACKAGE / "parameter_adapters.py").read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    registry = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "ADAPTERS"
    )
    assert isinstance(registry, ast.Dict)
    entry = {key.value: value.id for key, value in zip(registry.keys, registry.values)}[query_id]

    literal: set[str] = set()
    patterns: list[re.Pattern[str]] = []
    visited: set[str] = set()
    pending = [entry]
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        for node in ast.walk(functions[name]):
            if isinstance(node, ast.Dict):
                literal.update(key.value for key in node.keys if isinstance(key, ast.Constant))
            elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
                if isinstance(node.slice, ast.JoinedStr):
                    parts = [
                        re.escape(part.value) if isinstance(part, ast.Constant) else ".+" for part in node.slice.values
                    ]
                    patterns.append(re.compile("".join(parts)))
                elif isinstance(node.slice, ast.Constant):
                    literal.add(node.slice.value)
            elif isinstance(node, ast.Name) and node.id in functions:
                pending.append(node.id)
    return frozenset(literal), tuple(patterns)


def _supplies(query_id: int, key: str) -> bool:
    literal, patterns = _adapter_keys(query_id)
    return key in literal or any(pattern.fullmatch(key) for pattern in patterns)


def _checked_reads() -> list[Read]:
    return [read for read in _reads() if read.query_id in ADAPTERS]


def test_only_known_modules_read_parameters():
    readers = {path.name for path in PACKAGE.glob("*.py") if "get_parameters" in path.read_text(encoding="utf-8")} - {
        "parameter_adapters.py"
    }
    assert readers <= PARAMETER_MODULES, f"{sorted(readers - PARAMETER_MODULES)} read parameters; teach this test"


def test_pending_adapter_queries_have_no_adapter():
    assert set(range(1, 100)) - set(ADAPTERS) == PENDING_ADAPTER, (
        "PENDING_ADAPTER must list exactly the unadapted queries"
    )


def test_reads_are_found_for_adapted_queries():
    unread = set(ADAPTERS) - {read.query_id for read in _reads()}
    assert not unread, f"no parameter reads found for adapted queries {sorted(unread)}; the extraction is broken"


def test_no_implementation_falls_back_for_a_key_the_adapter_does_not_supply():
    violations = [
        f"Q{read.query_id}: {read.where} falls back for {read.key!r}, which the adapter does not supply"
        for read in _checked_reads()
        if read.fallback and not _supplies(read.query_id, read.key) and (read.query_id, read.key) not in STRUCTURAL_KEYS
    ]
    assert not violations, "\n".join(violations)


def test_structural_declarations_are_still_needed():
    needed = {
        (read.query_id, read.key)
        for read in _checked_reads()
        if read.fallback and not _supplies(read.query_id, read.key)
    }
    stale = sorted(set(STRUCTURAL_KEYS) - needed)
    assert not stale, f"declared structural but no longer a fallback the adapter lacks: {stale}"
    assert all(STRUCTURAL_KEYS.values()), "every structural declaration needs a reason"


