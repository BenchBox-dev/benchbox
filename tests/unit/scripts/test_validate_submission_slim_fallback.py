from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def slim_leak_detector():
    spec = importlib.util.spec_from_file_location("_validation_bundle_slim", ROOT / "benchbox/validation/bundle.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]

    detector = module.find_public_path_leaks
    assert detector.__module__ == "_validation_bundle_slim", "fixture did not exercise the stdlib detector"
    return detector


def _canonical():
    from benchbox.core.results.anonymization import find_public_path_leaks

    return find_public_path_leaks


PAYLOADS = [
    pytest.param({"metadata": {"working_dir": "/Users/alice/private"}}, id="value-leak"),
    pytest.param({"per_path_timings": {"/Users/alice/db": 1.2}}, id="key-leak"),
    pytest.param({"/Users/alice/db": 1}, id="root-key-leak"),
    pytest.param({"a": {"/Users/alice/x": {"working_dir": "/home/bob/private"}}}, id="nested-under-leaking-key"),
    pytest.param({"queries": [{"id": "q1"}, {"dir": "~/secret"}]}, id="list-leak"),
    pytest.param({"queries": {"q1": 1.0, "tpch/q2": 2.0}}, id="clean"),
]


@pytest.mark.parametrize("payload", PAYLOADS)
def test_slim_fallback_matches_canonical_detector(slim_leak_detector, payload) -> None:
    assert sorted(slim_leak_detector(payload)) == sorted(_canonical()(payload))


def test_slim_fallback_flags_a_key_encoded_private_path(slim_leak_detector) -> None:
    assert slim_leak_detector({"per_path_timings": {"/Users/alice/db": 1.2}}) == ["per_path_timings.<key>"]


def test_slim_fallback_does_not_echo_a_leaking_key(slim_leak_detector) -> None:
    leaks = slim_leak_detector({"/Users/alice/secret": {"working_dir": "/home/bob/private"}})
    joined = " ".join(leaks)
    assert leaks
    assert "alice" not in joined
    assert "secret" not in joined
    assert "bob" not in joined


def test_slim_fallback_still_reports_ordinary_value_leaks(slim_leak_detector) -> None:
    assert slim_leak_detector({"nested": [{"working_dir": "/Users/alice/private"}]}) == ["nested.0.working_dir"]


BUNDLE_POLICY_CANONICAL = "benchbox.core.results.schema_policy"

VERSION_MATRIX = [
    pytest.param({"result_schema_version": "2.2"}, "2.2", id="new-key"),
    pytest.param({"version": "2.1"}, "2.1", id="legacy-key"),
    pytest.param({"schema_version": "2.0"}, "2.0", id="oldest-key"),
    pytest.param(
        {"result_schema_version": "2.2", "version": "2.2", "schema_version": "2.0"},
        "2.2",
        id="aliases-match",
    ),
    pytest.param({"version": "2.1", "schema_version": "2.0"}, "2.1", id="legacy-wins"),
    pytest.param({}, None, id="missing"),
]


@pytest.fixture(scope="module")
def slim_version_helper():
    saved = sys.modules.get(BUNDLE_POLICY_CANONICAL, ...)
    sys.modules[BUNDLE_POLICY_CANONICAL] = None
    try:
        spec = importlib.util.spec_from_file_location("_bundle_slim", ROOT / "benchbox/validation/bundle.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if saved is ...:
            del sys.modules[BUNDLE_POLICY_CANONICAL]
        else:
            sys.modules[BUNDLE_POLICY_CANONICAL] = saved

    helper = module.result_schema_version_value
    assert helper.__module__ == "_benchbox_schema_policy", "fixture did not exercise the slim file-load path"
    return helper


def _canonical_version_helper():
    from benchbox.core.results.schema_policy import result_schema_version_value

    return result_schema_version_value


@pytest.mark.parametrize("bundle,expected", VERSION_MATRIX)
def test_slim_version_helper_matches_canonical(slim_version_helper, bundle, expected) -> None:
    assert slim_version_helper(bundle) == _canonical_version_helper()(bundle) == expected
