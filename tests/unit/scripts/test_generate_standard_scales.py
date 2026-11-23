from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import scripts.generate_corpus_rule_data as generator
from benchbox.core import benchmark_registry
from benchbox.core.benchmark_registry import get_scale_factor_rule, list_benchmark_ids
from benchbox.core.results import subscale_policy

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_generator_writes_and_checks_projection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "corpus_rule_data.json"
    monkeypatch.setattr(generator, "DATA_PATH", path)
    assert generator.main(["--write"]) == 0
    assert generator.main(["--check"]) == 0
    path.write_text("{}")
    assert generator.main(["--check"]) == 1
    path.unlink()
    assert generator.main(["--check"]) == 1


def test_generator_requires_one_mode() -> None:
    for args in ([], ["--check", "--write"]):
        with pytest.raises(SystemExit) as raised:
            generator.parse_args(args)
        assert raised.value.code == 2


@pytest.mark.parametrize("benchmark_id", list_benchmark_ids())
def test_projection_lookup_agrees_with_registry(benchmark_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    rule = get_scale_factor_rule(benchmark_id)
    values = [max(rule.standard) + 1.0, float("nan"), float("inf"), "2.0", True, None, "x"]
    for value in rule.standard:
        values.extend((value, value + 1e-10, value - 1e-10, value + 1e-6, value - 1e-6))
    registry_lookup = benchmark_registry.is_standard_scale_factor
    monkeypatch.setitem(sys.modules, "benchbox.core.benchmark_registry", None)
    for candidate in (benchmark_id, benchmark_id.upper()):
        for value in values:
            if isinstance(value, bool) or value is None:
                expected = False
            else:
                try:
                    float(value)
                    expected = not registry_lookup(candidate, value)
                except (TypeError, ValueError):
                    expected = False
            assert subscale_policy.is_nonstandard_scale(candidate, value) is expected


def test_projection_covers_registry() -> None:
    projection = subscale_policy._corpus_rule.load_data()
    assert sorted(projection["scales"]) == sorted(list_benchmark_ids())
    for benchmark in list_benchmark_ids():
        assert projection["scales"][benchmark]["standard"] == sorted(get_scale_factor_rule(benchmark).standard)


def test_unknown_benchmark_keeps_recorded_class() -> None:
    assert subscale_policy.is_nonstandard_scale("not-a-registered-benchmark", 2.0) is False
    assert subscale_policy.effective_compliance_class("not-a-registered-benchmark", 2.0, None) is None


@pytest.mark.parametrize("damage", ["missing", "not-json", "wrong-schema", "no-scales", "empty-scales"])
def test_unusable_projection_fails_closed_without_registry(
    damage: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "corpus_rule_data.json"
    if damage == "not-json":
        path.write_text("{", encoding="utf-8")
    elif damage == "wrong-schema":
        path.write_text(json.dumps({"schema_version": 99, "scales": {}}), encoding="utf-8")
    elif damage == "no-scales":
        path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    elif damage == "empty-scales":
        path.write_text(json.dumps({"schema_version": 1, "scales": {}}), encoding="utf-8")
    rule = subscale_policy._corpus_rule
    original = rule.load_data
    monkeypatch.setattr(rule, "load_data", lambda: original(path))
    monkeypatch.setitem(sys.modules, "benchbox.core.benchmark_registry", None)
    with pytest.raises(RuntimeError, match="corpus_rule_data.json is missing or unreadable"):
        subscale_policy.is_nonstandard_scale("tpch", 2.0)


def test_unusable_projection_uses_registry_when_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rule = subscale_policy._corpus_rule
    original = rule.load_data
    monkeypatch.setattr(rule, "load_data", lambda: original(tmp_path / "absent.json"))
    assert subscale_policy.is_nonstandard_scale("tpch", 2.0) is True
    assert subscale_policy.is_nonstandard_scale("tpch", 1.0) is False
