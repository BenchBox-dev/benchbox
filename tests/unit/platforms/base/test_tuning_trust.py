from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.tuning import applied_ledger
from benchbox.core.tuning.applied_ledger import (
    EXECUTED,
    FAILED,
    LEDGER_PHASES,
    NOOP,
    PHASE_DDL,
    PHASE_POST_LOAD,
    AppliedTuningLedger,
)
from benchbox.platforms.base import tuning_trust
from benchbox.platforms.base.adapter import PlatformAdapter
from benchbox.platforms.clickhouse.adapter import ClickHouseAdapter
from benchbox.platforms.duckdb import DuckDBAdapter
from benchbox.platforms.snowflake import SnowflakeAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[4]
ADAPTER_PATH = ROOT / "benchbox" / "platforms" / "base" / "adapter.py"

PHASE_POSITIONAL_INDEX = {"record": 1, "recording_connection": 2}
FORWARDED_PHASE_NAMES = frozenset({"phase", "_phase"})
LEDGER_PRODUCER_MARKERS = frozenset(
    {
        "AppliedTuningLedger",
        "recording_connection",
        "_applied_tuning_ledger",
        "_applied_layout_operations",
        "_skipped_layout_operations",
        "_record_layout_operation",
    }
)
CLOSED_SET_PHASE_CONSTANTS = frozenset(
    name
    for name, value in vars(applied_ledger).items()
    if name.startswith("PHASE_") and isinstance(value, str) and value in LEDGER_PHASES
)
EXPECTED_PHASE_PRODUCERS = frozenset(
    {
        "benchbox/core/tuning/applied_ledger.py",
        "benchbox/platforms/base/adapter.py",
        "benchbox/platforms/base/sorted_ingestion.py",
        "benchbox/platforms/base/tuning_trust.py",
        "benchbox/platforms/clickhouse/workload.py",
        "benchbox/platforms/databricks/adapter.py",
        "benchbox/platforms/duckdb.py",
        "benchbox/platforms/starrocks/workload.py",
    }
)


@pytest.mark.parametrize(
    ("method", "function", "args"),
    [
        ("_corroborate_applied_ledger", "corroborate_applied_ledger", ("conn", "applied_unverified")),
        ("_attach_applied_ledger_payload", "attach_applied_ledger_payload", ("result", "applied_unverified")),
        ("_build_drift_check_payload", "build_drift_check_payload", ()),
        ("_fold_layout_operations_into_ledger", "fold_layout_operations_into_ledger", ()),
        ("_reconcile_requested_tuning", "reconcile_requested_tuning", ("config",)),
    ],
)
def test_adapter_method_delegates_to_tuning_trust(method: str, function: str, args: tuple) -> None:
    adapter = MagicMock()
    sentinel = object()
    with patch.object(tuning_trust, function, return_value=sentinel) as delegate:
        returned = getattr(PlatformAdapter, method)(adapter, *args)
    delegate.assert_called_once_with(adapter, *args)
    if method in {"_corroborate_applied_ledger", "_build_drift_check_payload"}:
        assert returned is sentinel
    else:
        assert returned is None


def test_read_back_degrades_to_apply_phase_status_when_ledger_missing() -> None:
    adapter = MagicMock()
    adapter._applied_tuning_ledger = None
    result = tuning_trust.read_back_applied_ledger(adapter, "conn", "applied_unverified", True)
    assert result == ("applied_unverified", None, None, None)


def test_read_back_carries_receipt_status_and_ledger_payload() -> None:
    adapter = MagicMock()
    ledger = adapter._applied_tuning_ledger
    ledger.overall_status.return_value = "applied_unverified"
    ledger.is_empty.return_value = False
    adapter._corroborate_applied_ledger.return_value = ("applied_verified", {"receipt": 1})
    adapter._build_drift_check_payload.return_value = None
    status, payload, ledger_hash, receipt = tuning_trust.read_back_applied_ledger(adapter, "conn", "noop", True)
    assert status == "applied_verified"
    assert receipt == {"receipt": 1}
    assert payload is ledger.to_payload.return_value
    assert ledger_hash is ledger.applied_ledger_hash.return_value
    ledger.overall_status.assert_called_once_with(tuning_enabled=adapter.tuning_enabled, has_config=True)
    ledger.to_payload.assert_called_once_with(status="applied_verified", receipt={"receipt": 1}, drift_check=None)


def test_apply_phase_status_matches_ledger_overall_status() -> None:
    adapter = MagicMock()
    assert tuning_trust.apply_phase_status(adapter, True) is adapter._applied_tuning_ledger.overall_status.return_value
    adapter._applied_tuning_ledger.overall_status.assert_called_once_with(
        tuning_enabled=adapter.tuning_enabled, has_config=True
    )


@pytest.mark.parametrize(
    ("adapter_cls", "factory", "attrs", "factory_args"),
    [
        (DuckDBAdapter, "duckdb_tuning_introspector", {}, ()),
        (ClickHouseAdapter, "clickhouse_tuning_introspector", {}, ()),
        (SnowflakeAdapter, "snowflake_tuning_introspector", {"schema": "PUBLIC"}, ("PUBLIC",)),
    ],
)
def test_introspector_override_delegates_to_gated_factory(
    adapter_cls, factory: str, attrs: dict, factory_args: tuple
) -> None:
    adapter = object.__new__(adapter_cls)
    for key, value in attrs.items():
        setattr(adapter, key, value)
    sentinel = object()
    with patch.object(tuning_trust, factory, return_value=sentinel) as delegate:
        returned = adapter.get_tuning_introspector()
    delegate.assert_called_once_with(*factory_args)
    assert returned is sentinel


def _run_enhanced_benchmark_source() -> str:
    source = ADAPTER_PATH.read_text(encoding="utf-8")
    start = source.index("    def run_enhanced_benchmark(")
    end = source.index("\n    def ", start + 1)
    return source[start:end]


def _fold_adapter(
    applied_ops: list[dict] | None = None, skipped_ops: list[dict] | None = None
) -> tuple[MagicMock, AppliedTuningLedger]:
    adapter = MagicMock()
    ledger = AppliedTuningLedger()
    adapter._applied_tuning_ledger = ledger
    adapter._applied_layout_operations = list(applied_ops or [])
    adapter._skipped_layout_operations = list(skipped_ops or [])
    return adapter, ledger


def test_fold_records_applied_as_executed() -> None:
    adapter, ledger = _fold_adapter(
        [{"statement": "OPTIMIZE T", "phase": PHASE_POST_LOAD, "status": "applied", "mechanism": "optimize"}]
    )
    tuning_trust.fold_layout_operations_into_ledger(adapter)
    assert [s.status for s in ledger.statements] == [EXECUTED]


def test_fold_records_skipped_as_dropped_not_failed() -> None:
    adapter, ledger = _fold_adapter(
        [{"statement": "OPTIMIZE T", "phase": PHASE_POST_LOAD, "status": "skipped", "mechanism": "optimize"}]
    )
    tuning_trust.fold_layout_operations_into_ledger(adapter)
    assert ledger.statements == []
    assert len(ledger.dropped) == 1
    assert ledger.dropped[0].reason.startswith("skipped:")


def test_fold_records_skipped_with_error_as_failed() -> None:
    adapter, ledger = _fold_adapter(
        [
            {
                "statement": "OPTIMIZE T",
                "phase": PHASE_POST_LOAD,
                "status": "skipped",
                "mechanism": "optimize",
                "error_message": "boom",
            }
        ]
    )
    tuning_trust.fold_layout_operations_into_ledger(adapter)
    assert ledger.dropped == []
    assert [s.status for s in ledger.statements] == [FAILED]


def test_fold_records_skipped_with_empty_error_message_as_failed() -> None:
    adapter, ledger = _fold_adapter(
        [
            {
                "statement": "OPTIMIZE T",
                "phase": PHASE_POST_LOAD,
                "status": "skipped",
                "mechanism": "optimize",
                "error_class": "TimeoutError",
                "error_message": "",
            }
        ]
    )
    tuning_trust.fold_layout_operations_into_ledger(adapter)
    assert ledger.dropped == []
    assert [s.status for s in ledger.statements] == [FAILED]


def test_fold_records_unknown_status_as_failed() -> None:
    adapter, ledger = _fold_adapter(
        [{"statement": "OPTIMIZE T", "phase": PHASE_POST_LOAD, "status": "weird", "mechanism": "optimize"}]
    )
    tuning_trust.fold_layout_operations_into_ledger(adapter)
    assert [s.status for s in ledger.statements] == [FAILED]


def test_fold_consumes_skipped_layout_operations_as_dropped() -> None:
    adapter, ledger = _fold_adapter(
        skipped_ops=[
            {
                "statement": "OPTIMIZE LINEITEM ZORDER BY (L_ORDERKEY)",
                "phase": PHASE_DDL,
                "status": "skipped",
                "mechanism": "z_order",
                "table": "LINEITEM",
            }
        ]
    )
    tuning_trust.fold_layout_operations_into_ledger(adapter)
    assert ledger.statements == []
    assert [d.intent for d in ledger.dropped] == ["OPTIMIZE LINEITEM ZORDER BY (L_ORDERKEY)"]


def test_hudi_skips_yield_dropped_intents_and_non_failed_status() -> None:
    adapter, ledger = _fold_adapter(
        skipped_ops=[
            {
                "statement": "OPTIMIZE LINEITEM ZORDER BY (L_ORDERKEY)",
                "phase": "ddl",
                "status": "skipped",
                "mechanism": "z_order",
                "table": "LINEITEM",
            },
            {
                "statement": "OPTIMIZE LINEITEM",
                "phase": "ddl",
                "status": "skipped",
                "mechanism": "optimize",
                "table": "LINEITEM",
            },
        ]
    )
    tuning_trust.fold_layout_operations_into_ledger(adapter)
    assert len(ledger.dropped) == 2
    assert ledger.overall_status(tuning_enabled=True, has_config=True) == NOOP


def _phase_expression_sites(tree: ast.AST) -> list[ast.expr]:
    sites: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            sites.extend(keyword.value for keyword in node.keywords if keyword.arg == "phase")
            callee = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            positional_index = PHASE_POSITIONAL_INDEX.get(callee)
            if positional_index is not None and len(node.args) > positional_index:
                sites.append(node.args[positional_index])
        elif isinstance(node, ast.Dict):
            sites.extend(
                value
                for key, value in zip(node.keys, node.values, strict=True)
                if isinstance(key, ast.Constant) and key.value == "phase"
            )
        elif isinstance(node, ast.arguments):
            positional = node.posonlyargs + node.args
            sites.extend(
                default
                for arg, default in zip(positional[len(positional) - len(node.defaults) :], node.defaults, strict=True)
                if arg.arg == "phase"
            )
            sites.extend(
                default
                for arg, default in zip(node.kwonlyargs, node.kw_defaults, strict=True)
                if arg.arg == "phase" and default is not None
            )
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id.startswith("PHASE_") for t in targets):
                sites.extend(node.value.values if isinstance(node.value, ast.Dict) else [node.value])
    return sites


def _is_ledger_record_call(node: ast.AST) -> bool:
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "record"):
        return False
    receiver = node.func.value
    receiver_name = receiver.attr if isinstance(receiver, ast.Attribute) else getattr(receiver, "id", "")
    return "ledger" in receiver_name.lower()


def _is_ledger_producer_module(tree: ast.AST, relative_path: str) -> bool:
    if relative_path.endswith("core/tuning/applied_ledger.py"):
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("applied_ledger"):
            if any(alias.name in LEDGER_PRODUCER_MARKERS or alias.name.startswith("PHASE_") for alias in node.names):
                return True
        if isinstance(node, ast.Name) and node.id in LEDGER_PRODUCER_MARKERS:
            return True
        if isinstance(node, ast.Attribute) and node.attr in LEDGER_PRODUCER_MARKERS:
            return True
        if _is_ledger_record_call(node):
            return True
    return False


def _phase_expression_leaves(expression: ast.expr) -> list[ast.expr]:
    if isinstance(expression, ast.BoolOp):
        return [leaf for value in expression.values for leaf in _phase_expression_leaves(value)]
    if isinstance(expression, ast.IfExp):
        return _phase_expression_leaves(expression.body) + _phase_expression_leaves(expression.orelse)
    return [expression]


def _is_member_or_forwarded_phase(leaf: ast.expr) -> bool:
    if isinstance(leaf, ast.Constant):
        return isinstance(leaf.value, str) and leaf.value in LEDGER_PHASES
    if isinstance(leaf, ast.Name):
        return leaf.id in CLOSED_SET_PHASE_CONSTANTS or leaf.id in FORWARDED_PHASE_NAMES
    if isinstance(leaf, ast.Attribute):
        return leaf.attr in CLOSED_SET_PHASE_CONSTANTS or leaf.attr in FORWARDED_PHASE_NAMES
    if isinstance(leaf, ast.Call):
        callee = leaf.func.attr if isinstance(leaf.func, ast.Attribute) else getattr(leaf.func, "id", None)
        if callee == "normalize_ledger_phase":
            return True
        first = leaf.args[0] if leaf.args else None
        return callee == "get" and isinstance(first, ast.Constant) and first.value == "phase"
    return False


def _phase_violations(source: str, relative_path: str) -> tuple[bool, int, list[str]]:
    tree = ast.parse(source)
    if not _is_ledger_producer_module(tree, relative_path):
        return False, 0, []
    sites = _phase_expression_sites(tree)
    violations = [
        f"{relative_path}:{expression.lineno}: {ast.unparse(expression)}"
        for expression in sites
        for leaf in _phase_expression_leaves(expression)
        if not _is_member_or_forwarded_phase(leaf)
    ]
    return True, len(sites), violations


def _scan_in_tree_phase_producers() -> dict[str, tuple[int, list[str]]]:
    scanned: dict[str, tuple[int, list[str]]] = {}
    for path in sorted((ROOT / "benchbox").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        is_producer, site_count, violations = _phase_violations(path.read_text(encoding="utf-8"), relative)
        if is_producer:
            scanned[relative] = (site_count, violations)
    return scanned


def test_every_in_tree_ledger_phase_producer_uses_a_closed_set_member() -> None:
    scanned = _scan_in_tree_phase_producers()
    producers_with_sites = {path for path, (site_count, _) in scanned.items() if site_count}
    assert producers_with_sites >= EXPECTED_PHASE_PRODUCERS
    assert [violation for _, violations in scanned.values() for violation in violations] == []


def test_no_module_emits_a_retired_phase_alias() -> None:
    retired = frozenset(applied_ledger.PHASE_ALIASES) | {"manual"}
    stray = [
        f"{path.relative_to(ROOT).as_posix()}:{site.lineno}: {site.value}"
        for path in sorted((ROOT / "benchbox").rglob("*.py"))
        for site in _phase_expression_sites(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(site, ast.Constant) and site.value in retired
    ]
    assert stray == []


@pytest.mark.parametrize(
    "snippet",
    [
        'ledger.record("SELECT 1", "pre_load")',
        'ledger.record("SELECT 1", phase="manual")',
        'self.ledger.record("SELECT 1", "pre_load")',
        'recording_connection(conn, ledger, "maintenance")',
        'op = {"phase": "pre_load", "status": "applied"}',
        'def produce(phase="schema"):\n    return phase',
        'PHASE_EXTRA = "pre_load"',
        'self._record_layout_operation(mechanism="x", phase=compute_phase())',
    ],
)
def test_phase_producer_scan_flags_values_outside_closed_set(snippet: str) -> None:
    source = "from benchbox.core.tuning.applied_ledger import PHASE_DDL\n" + snippet + "\n"
    is_producer, _, violations = _phase_violations(source, "benchbox/synthetic.py")
    assert is_producer
    assert violations


def test_phase_producer_scan_accepts_closed_set_members_and_forwarding() -> None:
    source = (
        "from benchbox.core.tuning.applied_ledger import PHASE_DDL, PHASE_SESSION\n"
        'ledger.record("SELECT 1", PHASE_DDL)\n'
        'ledger.record("SELECT 1", phase="post_load")\n'
        "recording_connection(conn, ledger, PHASE_SESSION)\n"
        'op = {"phase": op.get("phase") or PHASE_DDL}\n'
        "def forward(phase):\n    return record(phase=phase)\n"
    )
    is_producer, site_count, violations = _phase_violations(source, "benchbox/synthetic.py")
    assert is_producer
    assert site_count == 5
    assert violations == []


def test_run_enhanced_benchmark_routes_trust_through_tuning_trust() -> None:
    body = _run_enhanced_benchmark_source()
    assert "tuning_trust.read_back_applied_ledger(" in body
    assert "tuning_trust.apply_phase_status(" in body
    assert "_attach_applied_ledger_payload(" in body
    assert "._applied_tuning_ledger.overall_status(" not in body


def test_run_enhanced_benchmark_reconciles_once_after_load_and_layout_fold() -> None:
    body = _run_enhanced_benchmark_source()
    assert body.count("self._reconcile_requested_tuning(") == 1
    load = body.index("self._setup_fresh_database_phases(")
    fold = body.index("self._fold_layout_operations_into_ledger()")
    reconcile = body.index("self._reconcile_requested_tuning(effective_tuning_config)")
    read_back = body.index("tuning_trust.read_back_applied_ledger(")
    assert load < fold < reconcile < read_back


def test_apply_step_never_reconciles() -> None:
    source = ADAPTER_PATH.read_text(encoding="utf-8")
    start = source.index("    def _setup_fresh_database_phases(")
    end = source.index("\n    def ", start + 1)
    assert "reconcile" not in source[start:end]
