from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import scripts.generate_standard_scales as generator
from benchbox.core import benchmark_registry
from benchbox.core.benchmark_registry import get_scale_factor_rule, list_benchmark_ids
from benchbox.core.results import subscale_policy

pytestmark = [pytest.mark.unit, pytest.mark.fast]

TOLERANCE_INSIDE = 1e-10
TOLERANCE_OUTSIDE = 1e-6


def _probe_values(benchmark_id: str) -> list[object]:
    standard = get_scale_factor_rule(benchmark_id).standard
    values: list[object] = [max(standard) + 1.0, float("nan"), float("inf"), "2.0", True, None, "x"]
    for value in standard:
        values.extend(
            [
                value,
                value + TOLERANCE_INSIDE,
                value - TOLERANCE_INSIDE,
                value + TOLERANCE_OUTSIDE,
                value - TOLERANCE_OUTSIDE,
            ]
        )
    return values


@pytest.fixture
def without_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "benchbox.core.benchmark_registry", None)
    subscale_policy._standard_scales.cache_clear()
    yield
    subscale_policy._standard_scales.cache_clear()


def test_the_committed_projection_is_what_the_generator_writes() -> None:
    assert generator.PROJECTION_PATH.read_text(encoding="utf-8") == generator.render_projection()


def test_the_projection_covers_every_registry_benchmark_with_the_registry_tolerance() -> None:
    projection = json.loads(generator.PROJECTION_PATH.read_text(encoding="utf-8"))

    assert projection["schema_version"] == subscale_policy.STANDARD_SCALES_SCHEMA_VERSION
    assert projection["match_tolerance"] == benchmark_registry._SCALE_MATCH_TOLERANCE
    assert sorted(projection["benchmarks"]) == sorted(list_benchmark_ids())
    assert projection["generated_by"] == generator.REGENERATE_COMMAND


def test_check_passes_on_the_committed_projection(capsys: pytest.CaptureFixture[str]) -> None:
    assert generator.main(["--check"]) == 0
    assert "OK" in capsys.readouterr().out


@pytest.mark.parametrize("stale", ["missing", "edited"])
def test_check_fails_and_names_the_command_when_the_projection_is_stale(
    stale: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "standard_scales.json"
    if stale == "edited":
        path.write_text(generator.render_projection().replace("100000.0", "99999.0"), encoding="utf-8")
    monkeypatch.setattr(generator, "PROJECTION_PATH", path)

    assert generator.main(["--check"]) == 1
    assert (
        "benchbox/core/results/standard_scales.json is out of date with the benchmark registry. "
        "Regenerate it in this pull request: uv run -- python scripts/generate_standard_scales.py --write"
    ) in capsys.readouterr().err


def test_write_then_check_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(generator, "PROJECTION_PATH", tmp_path / "standard_scales.json")
    monkeypatch.setattr(generator, "CHECKOUT_ROOT", tmp_path)

    assert generator.main(["--write"]) == 0
    assert generator.main(["--check"]) == 0


def test_generator_requires_exactly_one_mode() -> None:
    for argv in ([], ["--check", "--write"]):
        with pytest.raises(SystemExit) as raised:
            generator.parse_args(argv)
        assert raised.value.code == 2


@pytest.mark.parametrize("benchmark_id", list_benchmark_ids())
def test_the_projection_lookup_agrees_with_the_registry(benchmark_id: str, without_registry: None) -> None:
    is_standard_scale_factor = benchmark_registry.is_standard_scale_factor

    for candidate in (benchmark_id, benchmark_id.upper()):
        for value in _probe_values(benchmark_id):
            expected = _registry_verdict(is_standard_scale_factor, candidate, value)
            assert subscale_policy.is_nonstandard_scale(candidate, value) is expected, (candidate, value)


def _registry_verdict(is_standard_scale_factor, benchmark_id: str, value: object) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    try:
        return not is_standard_scale_factor(benchmark_id, value)
    except ValueError:
        return False


def test_the_projection_lookup_leaves_an_unknown_benchmark_to_the_recorded_class(without_registry: None) -> None:
    assert subscale_policy.is_nonstandard_scale("not-a-registered-benchmark", 2.0) is False
    assert subscale_policy.effective_compliance_class("not-a-registered-benchmark", 2.0, None) is None


@pytest.mark.parametrize("damage", ["missing", "not-json", "wrong-schema", "no-benchmarks"])
def test_registry_less_classification_fails_closed_when_the_projection_is_unusable(
    damage: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, without_registry: None
) -> None:
    path = tmp_path / "standard_scales.json"
    if damage == "not-json":
        path.write_text("{", encoding="utf-8")
    elif damage == "wrong-schema":
        path.write_text(json.dumps({"schema_version": 99, "match_tolerance": 1e-9, "benchmarks": {}}), encoding="utf-8")
    elif damage == "no-benchmarks":
        path.write_text(json.dumps({"schema_version": 1, "match_tolerance": 1e-9}), encoding="utf-8")
    monkeypatch.setattr(subscale_policy, "STANDARD_SCALES_PATH", path)

    with pytest.raises(RuntimeError, match="standard_scales.json is missing or unreadable") as raised:
        subscale_policy.is_nonstandard_scale("tpch", 2.0)

    assert str(raised.value) == (
        "standard-scale projection benchbox/core/results/standard_scales.json is missing or unreadable; "
        "registry-less validation cannot classify scale factors"
    )


def test_a_damaged_projection_is_not_consulted_when_the_registry_is_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subscale_policy, "STANDARD_SCALES_PATH", tmp_path / "absent.json")
    subscale_policy._standard_scales.cache_clear()

    assert subscale_policy.is_nonstandard_scale("tpch", 2.0) is True
    assert subscale_policy.is_nonstandard_scale("tpch", 1.0) is False
