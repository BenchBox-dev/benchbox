# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark(TM) DS (TPC-DS) - Copyright (c) Transaction Processing Performance Council

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import collections
import re
import signal
from dataclasses import dataclass, field
from typing import Any

import pytest

from benchbox.utils.clock import elapsed_seconds, mono_time

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
    pytest.mark.tpcds,
    pytest.mark.skipif(not hasattr(signal, "SIGALRM"), reason="needs SIGALRM"),
]

LITERAL_FALLBACK: frozenset[int] = frozenset()

INCOMPLETE_RUNS = frozenset({"5:pandas"})

HARD_CODED: frozenset[int] = frozenset()

BINDING_GAP: frozenset[int] = frozenset()

HARD_CODED_BUDGET = 15

_DEFINE = re.compile(r"define\s+(\w+)\s*=\s*(.*?);", re.S)
_TOKEN = re.compile(r"\[(\w+?)(?:\.(\d+))?\]")
_ABBREVIATIONS = {
    "ES": ["education"],
    "MS": ["marital"],
    "GEN": ["gender"],
    "CC": ["category"],
    "SMC": ["carrier"],
    "HOUR": ["dep"],
}


class _Stub:
    def __getattr__(self, name: str) -> Any:
        if name == "npartitions" or (name.startswith("__") and name.endswith("__")):
            raise AttributeError(name)
        return _Stub()

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return _Stub()

    def __iter__(self):
        return iter([_Stub(), _Stub()])

    def __len__(self) -> int:
        return 2

    def __bool__(self) -> bool:
        return True

    def __contains__(self, item: Any) -> bool:
        return True

    def __enter__(self):
        return self

    def __exit__(self, *args: Any) -> bool:
        return False

    def __setitem__(self, key: Any, value: Any) -> None:
        return None

    def __delitem__(self, key: Any) -> None:
        return None

    def __array__(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError("the stand-in is not array-like")

    __hash__ = object.__hash__


_OPERATORS = (
    "lt",
    "le",
    "gt",
    "ge",
    "eq",
    "ne",
    "add",
    "radd",
    "sub",
    "rsub",
    "mul",
    "rmul",
    "truediv",
    "rtruediv",
    "floordiv",
    "rfloordiv",
    "mod",
    "rmod",
    "pow",
    "rpow",
    "and",
    "rand",
    "or",
    "ror",
    "xor",
    "rxor",
    "lshift",
    "rshift",
    "neg",
    "pos",
    "invert",
    "abs",
    "getitem",
    "matmul",
)

for _operator in _OPERATORS:
    setattr(_Stub, f"__{_operator}__", lambda self, *args: _Stub())


_RUN_SECONDS = 3.0
_RETRY_SECONDS = 0.5


class _Timeout(Exception):
    pass


def _raise_timeout(signum: int, frame: Any) -> None:
    if frame is not None and frame.f_code is _read_keys.__code__:
        return
    raise _Timeout


@dataclass
class QueryInventory:
    query_id: int
    category: str = "a"
    derived: bool = False
    incomplete: set[str] = field(default_factory=set)
    consumed: set[str] = field(default_factory=set)
    fallback: set[str] = field(default_factory=set)
    dead: set[str] = field(default_factory=set)
    shortfall: list[str] = field(default_factory=list)
    data_shortfall: list[str] = field(default_factory=list)
    unmatched_names: list[str] = field(default_factory=list)


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _plural_forms(name: str) -> set[str]:
    forms = {name, name + "s", name + "es"}
    if name.endswith("y"):
        forms.add(name[:-1] + "ies")
    return forms


def _matching_keys(name: str, keys: list[str]) -> list[str]:
    normalized = _normalize(name)
    base = _normalize(re.sub(r"(number|_?[a-h]|\d)$", "", name.lower()))
    stems = {stem for stem in {normalized, base} | _plural_forms(normalized) | _plural_forms(base) if len(stem) >= 2}
    found = []
    for key in keys:
        norm_key = _normalize(key)
        abbreviated = name in _ABBREVIATIONS and any(part in norm_key for part in _ABBREVIATIONS[name])
        contained = any(stem in norm_key for stem in stems if len(stem) >= 3)
        if abbreviated or contained or norm_key in stems or ("date" in normalized and "date" in key):
            found.append(key)
    return found


def _template_usage(
    text: str, logged_names: frozenset[str] = frozenset()
) -> tuple[dict[str, set[int]], bool, dict[str, set[str]], set[str]]:
    text = re.sub(r"--[^\n]*", "", text)
    defines = {match.group(1): match.group(2) for match in _DEFINE.finditer(text)}
    body = _DEFINE.sub("", text)
    used: dict[str, set[int]] = collections.defaultdict(set)

    def note(source: str) -> None:
        for match in _TOKEN.finditer(source):
            used[match.group(1)].add(int(match.group(2) or 0))

    note(body)
    changed = True
    while changed:
        changed = False
        for name, expression in defines.items():
            if name in used and name not in logged_names:
                before = {key: set(value) for key, value in used.items()}
                note(expression)
                changed = changed or before != {key: set(value) for key, value in used.items()}
    inside_defines: dict[str, set[str]] = collections.defaultdict(set)
    for name, expression in defines.items():
        for match in _TOKEN.finditer(expression):
            inside_defines[match.group(1)].add(name)
    body_names = {match.group(1) for match in _TOKEN.finditer(body)}
    derived = bool(re.search(r"\[\w+(?:\.\d+)?\]\s*[+\-*/]\s*\d|\d\s*[+\-*/]\s*\[\w+(?:\.\d+)?\]", body))
    return used, derived, inside_defines, body_names


def _group_lettered_names(logged: collections.Counter, used: dict[str, set[int]], body_names: set[str]) -> None:
    lettered: dict[str, list[str]] = collections.defaultdict(list)
    for name in list(used):
        match = re.fullmatch(r"(\w+)_[A-H]", name)
        if match and logged[name] == 1:
            lettered[match.group(1)].append(name)
    for base, members in lettered.items():
        if len(members) < 2 or base in logged:
            continue
        logged[base] = len(members)
        used[base] = set(range(1, len(members) + 1))
        for member in members:
            del used[member]
        if body_names.intersection(members):
            body_names.add(base)


def _read_keys(query_id: int, family: str, defaults: dict[int, dict[str, Any]]) -> tuple[set[str], bool]:
    import pandas as pd

    import benchbox.core.dataframe.benchmark_suite
    from benchbox.core.tpcds.dataframe_queries import get_tpcds_query, queries as query_module, rollup_helper
    from benchbox.core.tpcds.dataframe_queries.parameter_adapters import ADAPTERS
    from benchbox.core.tpcds.dataframe_queries.parameters import TPCDSParameters

    read: set[str] = set()

    class Recorder(TPCDSParameters):
        def get(self, key: str, default: Any = None) -> Any:
            read.add(key)
            return self.params.get(key, default)

    original_get_parameters = query_module.get_parameters
    original_to_datetime = pd.to_datetime
    original_rollup = query_module._sales_returns_rollup_pandas
    original_expand_rollup = rollup_helper.expand_rollup_pandas
    query_module.get_parameters = lambda qid: Recorder(query_id=qid, params=dict(defaults.get(qid, {})))
    if query_id in ADAPTERS:
        query_module._sales_returns_rollup_pandas = lambda ctx, combined: _Stub()
        rollup_helper.expand_rollup_pandas = lambda *args, **kwargs: _Stub()
    pd.to_datetime = lambda value, *args, **kwargs: (
        _Stub() if isinstance(value, _Stub) else original_to_datetime(value, *args, **kwargs)
    )
    previous_handler = signal.signal(signal.SIGALRM, _raise_timeout)
    started = mono_time()
    outer_delay, outer_interval = signal.setitimer(signal.ITIMER_REAL, 0)
    complete = True
    try:
        implementations = [get_tpcds_query(f"Q{query_id}")]
        second_statement = get_tpcds_query(f"Q{query_id}b")
        if second_statement is not None:
            implementations.append(second_statement)
        for query in implementations:
            implementation = query.expression_impl if family == "expression" else query.pandas_impl
            signal.setitimer(signal.ITIMER_REAL, _RUN_SECONDS, _RETRY_SECONDS)
            implementation(_Stub())
    except Exception:
        complete = False
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if outer_delay:
            remaining = max(outer_delay - elapsed_seconds(started), 0.001)
            signal.setitimer(signal.ITIMER_REAL, remaining, outer_interval)
        query_module.get_parameters = original_get_parameters
        query_module._sales_returns_rollup_pandas = original_rollup
        rollup_helper.expand_rollup_pandas = original_expand_rollup
        pd.to_datetime = original_to_datetime
    return read, complete


def _inventory(query_id: int, dsqgen: Any, defaults: dict[int, dict[str, Any]]) -> QueryInventory:
    from benchbox.core.tpcds.dataframe_queries.parameter_adapters import ADAPTERS

    result = QueryInventory(query_id)
    for family in ("expression", "pandas"):
        keys, complete = _read_keys(query_id, family, defaults)
        result.consumed |= keys
        if not complete:
            result.incomplete.add(family)
    yaml_keys = set(defaults.get(query_id, {}))
    result.fallback = result.consumed - yaml_keys
    result.dead = yaml_keys - result.consumed

    logged = collections.Counter(key.rsplit(".", 1)[0] for key in dsqgen.generate_parameter_log(query_id).substitutions)
    template = (dsqgen.templates_dir / f"query{query_id}.tpl").read_text(encoding="utf-8", errors="replace")
    used, result.derived, inside_defines, body_names = _template_usage(template, frozenset(logged))
    _group_lettered_names(logged, used, body_names)
    consumed = sorted(result.consumed)
    matched = {name: _matching_keys(name, consumed) for name in logged if name in used}

    memo: dict[str, bool] = {}

    def unmatched(name: str) -> bool:
        if name in memo:
            return memo[name]
        memo[name] = False
        if matched.get(name):
            return False
        memo[name] = name in body_names or any(
            unmatched(outer) for outer in inside_defines.get(name, ()) if outer in used
        )
        return memo[name]

    result.unmatched_names = sorted(name for name in matched if unmatched(name))
    for name, keys in matched.items():
        multi_valued = logged[name] >= 2 and len(used[name]) >= 2
        if not multi_valued or any(key not in defaults.get(query_id, {}) for key in keys):
            continue
        takes_a_list = any(isinstance(defaults[query_id][key], (list, tuple)) for key in keys)
        capacity = sum(
            len(defaults[query_id][key]) if isinstance(defaults[query_id][key], (list, tuple)) else 1 for key in keys
        )
        if len(used[name]) <= capacity:
            continue
        if takes_a_list:
            if query_id not in ADAPTERS:
                result.data_shortfall.append(
                    f"{name}: {len(used[name])} values reach the SQL, {capacity} in the defaults"
                )
        else:
            result.shortfall.append(f"{name}: {len(used[name])} values reach the SQL, the implementation reads one")

    if result.shortfall:
        result.category = "c"
    elif result.fallback or result.data_shortfall or (result.dead and not result.incomplete):
        result.category = "b"
    return result


@pytest.fixture(scope="module")
def inventory() -> dict[int, QueryInventory]:
    from benchbox.core.tpcds.c_tools import DSQGenBinary, TPCDSError
    from benchbox.core.tpcds.dataframe_queries.parameters import TPCDS_DEFAULT_PARAMS

    try:
        dsqgen = DSQGenBinary()
    except (TPCDSError, FileNotFoundError, RuntimeError) as exc:
        pytest.skip(f"dsqgen binary or templates unavailable: {exc}")
    return {query_id: _inventory(query_id, dsqgen, TPCDS_DEFAULT_PARAMS) for query_id in range(1, 100)}


def _expected_category(query_id: int) -> str:
    return "c" if query_id in HARD_CODED else ("b" if query_id in BINDING_GAP else "a")


def test_every_query_is_classified(inventory):
    assert sorted(inventory) == list(range(1, 100))
    assert {entry.category for entry in inventory.values()} <= {"a", "b", "c"}


def test_incomplete_runs_are_the_known_ones(inventory):
    incomplete = {f"{entry.query_id}:{family}" for entry in inventory.values() for family in entry.incomplete}
    assert incomplete <= INCOMPLETE_RUNS


def test_literal_fallback_queries_are_the_known_ones(inventory):
    assert {entry.query_id for entry in inventory.values() if entry.fallback} == LITERAL_FALLBACK


def test_lettered_names_count_as_one_list():
    from benchbox.core.tpcds.c_tools import DSQGenBinary, TPCDSError

    try:
        dsqgen = DSQGenBinary()
    except (TPCDSError, FileNotFoundError, RuntimeError) as exc:
        pytest.skip(f"dsqgen binary or templates unavailable: {exc}")
    logged = collections.Counter(key.rsplit(".", 1)[0] for key in dsqgen.generate_parameter_log(16).substitutions)
    template = (dsqgen.templates_dir / "query16.tpl").read_text(encoding="utf-8", errors="replace")
    used, _, _, body_names = _template_usage(template, frozenset(logged))
    _group_lettered_names(logged, used, body_names)
    assert used["COUNTY"] == {1, 2, 3, 4, 5}
    assert logged["COUNTY"] == 5
    assert "COUNTY_A" not in used
    assert "COUNTYNUMBER" not in used


def test_both_statements_count():
    template = (
        "define COLOR=ulist(dist(colors,1,1),2);\nselect 1 where c = '[COLOR.1]';\nselect 2 where c = '[COLOR.2]';"
    )
    used, _, _, _ = _template_usage(template)
    assert used["COLOR"] == {1, 2}


def test_derived_value_flag_marks_arithmetic_on_a_drawn_value(inventory):
    assert inventory[39].derived
    assert not inventory[93].derived


@pytest.mark.parametrize("query_id", range(1, 100))
def test_parameters_are_bound_to_dsqgen(inventory, request, query_id):
    expected = _expected_category(query_id)
    if expected != "a":
        request.applymarker(
            pytest.mark.xfail(
                strict=True, reason=f"category {expected}: remove from the list in this module once it is bound"
            )
        )
    entry = inventory[query_id]
    assert entry.category == "a", (
        f"Q{query_id}: category {entry.category}; fallback={sorted(entry.fallback)} "
        f"dead={sorted(entry.dead)} shortfall={entry.shortfall}"
    )


def test_listed_categories_match_the_inventory(inventory):
    for query_id, entry in inventory.items():
        assert entry.category == _expected_category(query_id), f"Q{query_id}"


def test_hard_coded_queries_stay_within_the_adapter_budget(inventory):
    hard_coded = [entry.query_id for entry in inventory.values() if entry.category == "c"]
    assert len(hard_coded) <= HARD_CODED_BUDGET, f"{len(hard_coded)} hard-coded queries: budget the adapters separately"


def test_numpy_refuses_the_stand_in_instead_of_expanding_it():
    import subprocess
    import sys
    from pathlib import Path

    code = (
        "import numpy as np\n"
        "from tests.unit.core.tpcds.test_parameter_consumption_inventory import _Stub\n"
        "try:\n"
        "    np.array([False, False]) | _Stub()\n"
        "except TypeError:\n"
        "    pass\n"
        "else:\n"
        "    raise SystemExit('numpy coerced the stand-in')\n"
    )

    subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[4],
        timeout=30,
        check=True,
    )


def test_reading_keys_keeps_the_outer_timer_armed():
    from benchbox.core.tpcds.dataframe_queries.parameters import TPCDS_DEFAULT_PARAMS

    previous_handler = signal.signal(signal.SIGALRM, lambda signum, frame: None)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 30)
    try:
        _read_keys(88, "pandas", TPCDS_DEFAULT_PARAMS)
        remaining, _ = signal.getitimer(signal.ITIMER_REAL)
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)
    assert 0 < remaining <= 30
