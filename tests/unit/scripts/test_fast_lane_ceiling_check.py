from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "_project" / "scripts" / "fast_lane_ceiling_check.py"


def _load():
    spec = importlib.util.spec_from_file_location("_fast_lane_ceiling_check", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()


def _policy(**overrides):
    policy = {"enabled": True}
    policy.update(overrides)
    return policy


def _write_manifest(path: Path, items: list[tuple[str, list[str], bool]], version: int = 1) -> Path:
    path.write_text(
        json.dumps(
            {
                "version": version,
                "items": [
                    {"id": nodeid, "markers": markers, "selected": selected} for nodeid, markers, selected in items
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


_FORBIDDEN = ["resource_heavy", "stress", "live_integration"]


def test_empty_or_disabled_policy_reads_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "absent.json"
    assert mod._check_fast_lane_policy(Path("/nonexistent"), {}, missing) == []
    assert (
        mod._check_fast_lane_policy(
            Path("/nonexistent"), {"enabled": False, "forbidden_marker_expressions": ["x"]}, missing
        )
        == []
    )
    assert mod._check_fast_lane_policy(Path("/nonexistent"), _policy(), missing) == []


@pytest.mark.parametrize("marker", _FORBIDDEN)
def test_fast_test_with_each_forbidden_marker_is_a_violation_even_when_deselected(tmp_path: Path, marker: str) -> None:
    manifest = _write_manifest(
        tmp_path / "m.json",
        [
            ("tests/a.py::test_ok", ["fast", "unit"], True),
            (f"tests/a.py::test_bad_{marker}", ["fast", marker], False),
            (f"tests/a.py::test_slow_{marker}", ["slow", marker], False),
        ],
    )
    violations = mod._check_fast_lane_policy(
        Path("/nonexistent"), _policy(forbidden_marker_expressions=_FORBIDDEN), manifest
    )
    assert violations == [f"fast lane unexpectedly includes 1 test(s) matching 'fast and {marker}'"]


def test_counts_are_per_expression_and_a_test_can_hit_several(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path / "m.json",
        [
            ("t.py::a", ["fast", "stress", "resource_heavy"], False),
            ("t.py::b", ["fast", "stress"], False),
            ("t.py::c", ["fast"], True),
        ],
    )
    violations = mod._check_fast_lane_policy(
        Path("/nonexistent"), _policy(forbidden_marker_expressions=_FORBIDDEN), manifest
    )
    assert violations == [
        "fast lane unexpectedly includes 1 test(s) matching 'fast and resource_heavy'",
        "fast lane unexpectedly includes 2 test(s) matching 'fast and stress'",
    ]


def test_compound_marker_expressions_follow_pytest_semantics(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path / "m.json",
        [("t.py::a", ["fast", "duckdb"], True), ("t.py::b", ["fast", "duckdb", "sqlite"], True)],
    )
    policy = _policy(forbidden_marker_expressions=["duckdb and not sqlite", "sqlite or missing"])
    assert mod._check_fast_lane_policy(Path("/nonexistent"), policy, manifest) == [
        "fast lane unexpectedly includes 1 test(s) matching 'fast and duckdb and not sqlite'",
        "fast lane unexpectedly includes 1 test(s) matching 'fast and sqlite or missing'",
    ]


def test_clean_manifest_passes(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path / "m.json",
        [("t.py::a", ["fast"], True), ("t.py::b", ["slow", "stress"], False)],
    )
    assert (
        mod._check_fast_lane_policy(Path("/nonexistent"), _policy(forbidden_marker_expressions=_FORBIDDEN), manifest)
        == []
    )


def test_forbidden_path_applies_to_fast_tests_only(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path / "m.json",
        [
            ("tests/unit/spark/test_x.py::test_a", ["fast"], True),
            ("tests/unit/spark/test_slow.py::test_s", ["slow"], True),
            ("tests/unit/core/test_y.py::test_b", ["fast"], True),
        ],
    )
    violations = mod._check_fast_lane_policy(
        Path("/nonexistent"), _policy(forbidden_path_substrings=["/spark/"]), manifest
    )
    assert len(violations) == 1
    assert "tests/unit/spark/test_x.py::test_a" in violations[0]
    assert "test_slow.py" not in violations[0]
    assert "test_y.py" not in violations[0]


@pytest.mark.parametrize("content", ["", "not json", '{"version": 2, "items": []}', '{"version": 1}'])
def test_unreadable_manifest_is_an_environment_error(tmp_path: Path, content: str) -> None:
    manifest = tmp_path / "m.json"
    manifest.write_text(content, encoding="utf-8")
    with pytest.raises(mod.FastLaneCollectError, match="marker manifest"):
        mod._check_fast_lane_policy(Path("/nonexistent"), _policy(forbidden_marker_expressions=["stress"]), manifest)


def test_missing_manifest_is_an_environment_error(tmp_path: Path) -> None:
    with pytest.raises(mod.FastLaneCollectError, match="marker manifest"):
        mod._check_fast_lane_policy(
            Path("/nonexistent"), _policy(forbidden_marker_expressions=["stress"]), tmp_path / "absent.json"
        )


@pytest.mark.parametrize("expr", ["stress and", "stress(reason='x')"])
def test_unsupported_marker_expression_is_an_environment_error(tmp_path: Path, expr: str) -> None:
    manifest = _write_manifest(tmp_path / "m.json", [("t.py::a", ["fast", "stress"], True)])
    with pytest.raises(mod.FastLaneCollectError):
        mod._check_fast_lane_policy(Path("/nonexistent"), _policy(forbidden_marker_expressions=[expr]), manifest)


def _planted_project(root: Path, files: dict[str, str], addopts: str = '-m "not stress"') -> Path:
    (root / "pytest.ini").write_text(
        "[pytest]\n"
        f"addopts = --strict-markers --strict-config {addopts}\n"
        "markers =\n    fast: f\n    slow: s\n    stress: st\n    resource_heavy: r\n    live_integration: l\n",
        encoding="utf-8",
    )
    for name, body in files.items():
        (root / name).write_text(body, encoding="utf-8")
    return root


def test_single_real_collection_finds_planted_violations_in_deselected_tests(tmp_path: Path) -> None:
    project = _planted_project(
        tmp_path,
        {
            "test_planted.py": (
                "import pytest\n"
                "@pytest.mark.fast\n@pytest.mark.stress\ndef test_fast_stress(): pass\n"
                "@pytest.mark.fast\n@pytest.mark.resource_heavy\ndef test_fast_heavy(): pass\n"
                "@pytest.mark.fast\n@pytest.mark.live_integration\ndef test_fast_live(): pass\n"
                "@pytest.mark.slow\n@pytest.mark.stress\ndef test_slow_stress(): pass\n"
                "@pytest.mark.fast\ndef test_fast_clean(): pass\n"
            )
        },
    )
    manifest = tmp_path / "out" / "manifest.json"
    mod._collect_manifest(project, manifest)

    recorded = {item["id"]: item for item in json.loads(manifest.read_text(encoding="utf-8"))["items"]}
    assert recorded["test_planted.py::test_fast_clean"]["selected"] is True
    assert recorded["test_planted.py::test_fast_stress"]["selected"] is False
    assert len(recorded) == 5

    violations = mod._check_fast_lane_policy(project, _policy(forbidden_marker_expressions=_FORBIDDEN))
    assert violations == [
        "fast lane unexpectedly includes 1 test(s) matching 'fast and resource_heavy'",
        "fast lane unexpectedly includes 1 test(s) matching 'fast and stress'",
        "fast lane unexpectedly includes 1 test(s) matching 'fast and live_integration'",
    ]


def test_unregistered_marker_fails_the_collection(tmp_path: Path) -> None:
    project = _planted_project(
        tmp_path, {"test_bad.py": "import pytest\n@pytest.mark.fast\n@pytest.mark.unregistered\ndef test_a(): pass\n"}
    )
    with pytest.raises(mod.FastLaneCollectError, match="could not run pytest --collect-only"):
        mod._check_fast_lane_policy(project, _policy(forbidden_marker_expressions=["stress"]))


def test_collection_error_fails_the_check(tmp_path: Path) -> None:
    project = _planted_project(
        tmp_path,
        {"test_ok.py": "def test_a(): pass\n", "test_broken.py": "def test_b(:\n"},
    )
    with pytest.raises(mod.FastLaneCollectError, match="could not run pytest --collect-only"):
        mod._check_fast_lane_policy(project, _policy(forbidden_marker_expressions=["stress"]))


def test_policy_file_carries_only_the_guard_fields() -> None:
    import json

    policy = json.loads((_ROOT / "_project" / "config" / "fast_test_lane_policy.json").read_text(encoding="utf-8"))
    assert set(policy) == {"enabled", "forbidden_marker_expressions", "forbidden_path_substrings"}
