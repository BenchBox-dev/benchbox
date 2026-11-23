from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.core.execution_engine import ExecutionEngineHook
from benchbox.core.platform_manifest import PLATFORM_MANIFEST

pytestmark = [pytest.mark.unit, pytest.mark.fast]

TESTS_ROOT = Path(__file__).resolve().parents[2]
GUARDED_MODES = frozenset({"dataframe"})
OBSERVATION_TEST_GLOB = "unit/**/test_*observed_engine*.py"
GENERIC_TEST_FILES = frozenset({"test_observed_engine_guard.py", "test_observed_engine_contract.py"})


def _import_adapter(path: str) -> type:
    module_name, class_name = path.split(":")
    return getattr(importlib.import_module(module_name), class_name)


def _adapter_tests(class_name: str) -> list[Path]:
    return [
        path
        for path in TESTS_ROOT.glob(OBSERVATION_TEST_GLOB)
        if path.name not in GENERIC_TEST_FILES and class_name in path.read_text(encoding="utf-8")
    ]


def observation_gaps(
    entries: Iterable[Any],
    resolve: Callable[[str], type] = _import_adapter,
    find_tests: Callable[[str], list[Path]] = _adapter_tests,
) -> list[str]:
    gaps: list[str] = []
    for entry in entries:
        capabilities = entry.capabilities
        if not capabilities.get("execution_engines") or capabilities.get("default_mode") not in GUARDED_MODES:
            continue
        declared = capabilities.get("observed_engine")
        if not declared:
            gaps.append(f"{entry.key}: declares engines with neither an observation implementation nor a reason")
            continue
        if "not_captured" in declared:
            reason = declared["not_captured"]
            if not (reason.get("reason") and reason.get("evidence")):
                gaps.append(f"{entry.key}: not_captured needs a reason and the evidence the library cannot report")
            continue
        adapter = resolve(declared["adapter"])
        if getattr(adapter, "OBSERVED_ENGINE_SOURCE", "none") == "none":
            gaps.append(f"{entry.key}: {adapter.__name__} declares no observation source")
        if adapter.observe_execution_engine is ExecutionEngineHook.__dict__.get("observe_execution_engine"):
            gaps.append(f"{entry.key}: {adapter.__name__} does not implement observe_execution_engine")
        if not find_tests(adapter.__name__):
            gaps.append(f"{entry.key}: {adapter.__name__} has no observed-engine test")
    return gaps


def test_every_manifest_platform_with_engines_observes_or_declares_why_not():
    assert observation_gaps(PLATFORM_MANIFEST) == []


def test_guard_covers_at_least_one_platform():
    guarded = [
        entry
        for entry in PLATFORM_MANIFEST
        if entry.capabilities.get("execution_engines") and entry.capabilities.get("default_mode") in GUARDED_MODES
    ]
    assert guarded


def _entry(**capabilities: Any) -> SimpleNamespace:
    return SimpleNamespace(
        key="synthetic",
        capabilities={"execution_engines": {"a": {}, "b": {}}, "default_mode": "dataframe", **capabilities},
    )


class _Observing:
    OBSERVED_ENGINE_SOURCE = "explain"

    def observe_execution_engine(self, lazy):
        return "a"


class _SourceOnly(ExecutionEngineHook):
    OBSERVED_ENGINE_SOURCE = "explain"


def _resolver(cls: type) -> Callable[[str], type]:
    return lambda path: cls


def test_guard_fails_for_a_platform_with_neither_implementation_nor_reason():
    gaps = observation_gaps([_entry()])
    assert len(gaps) == 1 and "neither an observation implementation nor a reason" in gaps[0]


def test_guard_fails_for_a_not_captured_declaration_without_evidence():
    assert observation_gaps([_entry(observed_engine={"not_captured": {"reason": "cannot", "evidence": ""}})])


def test_guard_accepts_a_not_captured_declaration_with_reason_and_evidence():
    declared = {"not_captured": {"reason": "cannot", "evidence": "API doc line"}}
    assert observation_gaps([_entry(observed_engine=declared)]) == []


def test_guard_fails_for_an_adapter_that_inherits_the_unimplemented_hook():
    gaps = observation_gaps(
        [_entry(observed_engine={"adapter": "x:y"})], _resolver(_SourceOnly), lambda name: [Path("t.py")]
    )
    assert any("does not implement observe_execution_engine" in gap for gap in gaps)


def test_guard_fails_for_an_implementation_without_a_test():
    gaps = observation_gaps([_entry(observed_engine={"adapter": "x:y"})], _resolver(_Observing), lambda name: [])
    assert gaps == ["synthetic: _Observing has no observed-engine test"]


def test_guard_accepts_an_implementation_with_a_test():
    gaps = observation_gaps(
        [_entry(observed_engine={"adapter": "x:y"})], _resolver(_Observing), lambda name: [Path("t.py")]
    )
    assert gaps == []
