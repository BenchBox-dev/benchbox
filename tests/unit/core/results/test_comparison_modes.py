from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchbox.core.results.comparison_modes import (
    RUN_MODE_FIELDS,
    IncomparableModesError,
    bundle_maintenance_method,
    bundle_mode_differences,
    bundle_timing_method,
    modes_comparable,
    require_comparable_modes,
    results_maintenance_method,
    results_mode_differences,
    results_timing_method,
    stored_timing_method,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

NEW = "dsdgen_refresh_sets"
LEGACY = "synthetic_rows"


def _bundle(run_method: str | None = None, phase: dict | None = None) -> dict:
    bundle: dict = {"run": {}, "phases": {}}
    if run_method is not None:
        bundle["run"]["maintenance_method"] = run_method
    if phase is not None:
        bundle["phases"]["maintenance_test"] = phase
    return bundle


def _results(phase_method: str | None, with_phase: bool = True) -> SimpleNamespace:
    phase = SimpleNamespace(maintenance_method=phase_method) if with_phase else None
    return SimpleNamespace(execution_phases=SimpleNamespace(maintenance_test=phase))


def test_run_mode_fields_include_maintenance_method():
    assert any(field.read is bundle_maintenance_method for field in RUN_MODE_FIELDS)
    assert any(field.name == "maintenance method" for field in RUN_MODE_FIELDS)


def test_run_maintenance_method_wins_over_phase():
    bundle = _bundle(NEW, {"status": "COMPLETED", "maintenance_method": LEGACY})
    assert bundle_maintenance_method(bundle) == NEW


def test_phase_without_method_is_legacy():
    assert bundle_maintenance_method(_bundle(phase={"status": "COMPLETED"})) == LEGACY


def test_phase_method_is_used_without_run_method():
    assert bundle_maintenance_method(_bundle(phase={"status": "COMPLETED", "maintenance_method": NEW})) == NEW


def test_not_run_phase_has_no_method():
    assert bundle_maintenance_method(_bundle(phase={"status": "NOT_RUN"})) is None


def test_missing_phase_has_no_method():
    assert bundle_maintenance_method(_bundle()) is None
    assert bundle_maintenance_method({}) is None


def test_different_methods_differ():
    differences = bundle_mode_differences(_bundle(NEW), _bundle(LEGACY))
    assert len(differences) == 1
    assert "maintenance method differs" in differences[0]
    assert NEW in differences[0]
    assert LEGACY in differences[0]


def test_same_method_is_comparable():
    assert bundle_mode_differences(_bundle(NEW), _bundle(NEW)) == []
    assert modes_comparable(_bundle(NEW), _bundle(NEW))


def test_bundle_without_maintenance_compares_with_either_method():
    assert modes_comparable(_bundle(), _bundle(NEW))
    assert modes_comparable(_bundle(LEGACY), _bundle())


def test_legacy_phase_differs_from_new_method():
    legacy = _bundle(phase={"status": "COMPLETED"})
    assert not modes_comparable(legacy, _bundle(NEW))


def test_require_comparable_modes_raises_for_different_methods():
    with pytest.raises(IncomparableModesError, match="maintenance method differs") as raised:
        require_comparable_modes(_bundle(NEW), _bundle(LEGACY))
    assert raised.value.differences == bundle_mode_differences(_bundle(NEW), _bundle(LEGACY))
    assert str(raised.value).startswith("Runs were measured under different modes")


def test_require_comparable_modes_accepts_same_method():
    require_comparable_modes(_bundle(NEW), _bundle(NEW))


def test_three_results_with_two_methods_report_one_difference():
    differences = results_mode_differences([_results(NEW), _results(LEGACY), _results(None, with_phase=False)])
    assert len(differences) == 1
    assert "maintenance method differs" in differences[0]


def test_three_results_with_one_method_report_nothing():
    assert results_mode_differences([_results(NEW), _results(NEW), _results(None, with_phase=False)]) == []


def test_results_method_reads_execution_phase():
    assert results_maintenance_method(_results(NEW)) == NEW


def test_results_method_is_legacy_when_unrecorded():
    assert results_maintenance_method(_results(None)) == LEGACY


def test_results_method_is_none_without_phase():
    assert results_maintenance_method(_results(None, with_phase=False)) is None
    assert results_maintenance_method(SimpleNamespace(execution_phases=None)) is None


def _timed(method: int | None) -> dict:
    return {"run": {} if method is None else {"timing_method": method}, "phases": {}}


def test_run_mode_fields_include_timing_method():
    assert any(field.name == "timing method" for field in RUN_MODE_FIELDS)


def test_bundle_without_timing_method_is_legacy():
    assert bundle_timing_method(_timed(None)) == "1"
    assert bundle_timing_method(_timed(2)) == "2"


def test_unrecorded_timing_method_compares_with_method_one():
    assert modes_comparable(_timed(None), _timed(1))


def test_different_timing_methods_differ():
    assert bundle_mode_differences(_timed(1), _timed(2)) == ["timing method differs (1 vs 2)"]
    assert bundle_mode_differences(_timed(None), _timed(2)) == ["timing method differs (1 vs 2)"]


def test_require_comparable_modes_raises_for_different_timing_methods():
    with pytest.raises(IncomparableModesError, match="timing method differs"):
        require_comparable_modes(_timed(2), _timed(1))


def test_results_timing_method_defaults_to_legacy():
    assert results_timing_method(SimpleNamespace(timing_method=None)) == "1"
    assert results_timing_method(SimpleNamespace(timing_method=2)) == "2"
    assert results_timing_method(SimpleNamespace()) == "1"


def test_results_with_two_timing_methods_report_one_difference():
    differences = results_mode_differences(
        [SimpleNamespace(timing_method=2), SimpleNamespace(timing_method=1), SimpleNamespace(timing_method=2)]
    )
    assert len(differences) == 1
    assert "timing method differs" in differences[0]


def test_stored_timing_method_defaults_to_legacy():
    assert stored_timing_method({}) == 1
    assert stored_timing_method({"timing_method": 2}) == 2
    assert stored_timing_method({"timing_method": "2"}) == 1
