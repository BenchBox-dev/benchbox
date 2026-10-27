from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.duration_policy import collect_junit_durations, current_test_tier, write_duration_file

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_current_test_tier_defaults_to_t1_and_rejects_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BENCHBOX_TEST_TIER", raising=False)
    assert current_test_tier() == "t1"

    monkeypatch.setenv("BENCHBOX_TEST_TIER", "t2")
    assert current_test_tier() == "t2"

    monkeypatch.setenv("BENCHBOX_TEST_TIER", "nightly")
    with pytest.raises(ValueError, match="BENCHBOX_TEST_TIER"):
        current_test_tier()


def test_collect_junit_durations_computes_p95_across_reports(tmp_path: Path) -> None:
    reports = []
    for index, duration in enumerate(["0.1", "0.2", "0.4", "0.8"]):
        report = tmp_path / f"report-{index}.xml"
        report.write_text(
            f'<testsuite><testcase file="tests/unit/test_example.py" name="test_case" time="{duration}"/></testsuite>',
            encoding="utf-8",
        )
        reports.append(report)

    durations = collect_junit_durations(reports)

    assert durations == {"tests/unit/test_example.py::test_case": pytest.approx(0.74)}


def test_write_duration_file_is_sorted_and_loadable(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "test_durations.json"
    write_duration_file(
        path,
        {
            "tests/z.py::test_z": 0.5,
            "tests/a.py::test_a": 0.25,
        },
        generated_at="2026-09-28T20:00:00Z",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert list(payload["tests"]) == ["tests/a.py::test_a", "tests/z.py::test_z"]
    assert payload["tests"]["tests/a.py::test_a"] == {"p95_seconds": 0.25}


def test_collection_hook_skips_quarantine_outside_t3(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests import conftest as benchbox_conftest

    marker = SimpleNamespace(kwargs={"owner": "team", "expiry": "2099-01-01", "issue": "#1"})

    class Item:
        nodeid = "tests/unit/test_example.py::test_case"

        def __init__(self) -> None:
            self.markers: list[object] = []

        def get_closest_marker(self, name: str) -> object | None:
            return marker if name == "quarantine" else None

        def add_marker(self, value: object) -> None:
            self.markers.append(value)

    monkeypatch.setenv("BENCHBOX_TEST_TIER", "t2")
    item = Item()
    monkeypatch.setattr(benchbox_conftest, "_items_require_test_databases", lambda items: False)

    benchbox_conftest.pytest_collection_modifyitems(None, None, [item])

    assert len(item.markers) == 1


def test_collection_hook_does_not_skip_quarantine_in_t3(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests import conftest as benchbox_conftest

    marker = SimpleNamespace(kwargs={"owner": "team", "expiry": "2099-01-01", "issue": "#1"})

    class Item:
        nodeid = "tests/unit/test_example.py::test_case"

        def get_closest_marker(self, name: str) -> object | None:
            return marker if name == "quarantine" else None

        def add_marker(self, value: object) -> None:
            raise AssertionError(f"unexpected skip marker: {value}")

    monkeypatch.setenv("BENCHBOX_TEST_TIER", "t3")
    monkeypatch.setattr(benchbox_conftest, "_items_require_test_databases", lambda items: False)

    benchbox_conftest.pytest_collection_modifyitems(None, None, [Item()])


def test_collect_junit_durations_derives_nodeid_from_classname_without_file(tmp_path: Path) -> None:
    report = tmp_path / "pytest_report.xml"
    report.write_text(
        "<testsuite>"
        '<testcase classname="tests.unit.test_example" name="test_func" time="0.1"/>'
        '<testcase classname="tests.unit.test_example.TestClass" name="test_method" time="0.2"/>'
        '<testcase classname="" name="tests.unit.skipped_module" time="0.0">'
        '<skipped message="collection skipped"/></testcase>'
        "</testsuite>",
        encoding="utf-8",
    )

    durations = collect_junit_durations([report])
    assert durations == {
        "tests/unit/test_example.py::test_func": 0.1,
        "tests/unit/test_example.py::TestClass::test_method": 0.2,
        "tests/unit/skipped_module.py": 0.0,
    }
