from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchbox.core.results import bundle_json_schema
from benchbox.core.results.bundle_json_schema import (
    COMPATIBILITY_PROMISE,
    DEFAULT_OUTPUT,
    SCHEMA_ID,
    build_result_bundle_schema,
    check_result_bundle_schema,
)
from benchbox.core.results.environment import HOST_LOAD_SAMPLE_FIELDS, build_host_load_block, host_load_sample
from benchbox.core.results.schema import SchemaV2ValidationError, SchemaV2Validator
from benchbox.core.results.schema_policy import CURRENT_SCHEMA_VERSION, KNOWN_SCHEMA_V2_VERSIONS
from benchbox.core.results.timing_fields import PLATFORM_TIMING_REASON_STATUSES

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[4]
BUNDLES = ROOT / "results-data" / "bundles"
COMPANION_SUFFIXES = (".manifest.json", ".plans.json", ".tuning.json", ".applied.json", ".override.json")
PREVIOUS_SCHEMA_VERSION = "2.4"
BLOCK_REQUIRED = (
    ("run", SchemaV2Validator.RUN_REQUIRED),
    ("benchmark", SchemaV2Validator.BENCHMARK_REQUIRED),
    ("platform", SchemaV2Validator.PLATFORM_REQUIRED),
    ("summary", SchemaV2Validator.SUMMARY_REQUIRED),
)


def _validator() -> Draft202012Validator:
    schema = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _bundle_paths() -> list[Path]:
    return sorted(path for path in BUNDLES.glob("*.json") if not path.name.endswith(COMPANION_SUFFIXES))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _current_bundle() -> dict:
    for path in _bundle_paths():
        data = _load(path)
        if data.get("result_schema_version") == PREVIOUS_SCHEMA_VERSION:
            data["result_schema_version"] = data["version"] = CURRENT_SCHEMA_VERSION
            return data
    raise AssertionError("no bundle at the previous schema version in the corpus")


def test_committed_schema_matches_generator() -> None:
    assert check_result_bundle_schema()


def test_schema_identity_and_version() -> None:
    schema = build_result_bundle_schema()
    assert schema["$id"] == SCHEMA_ID == "https://benchbox.dev/schemas/result-bundle-2.schema.json"
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["result_schema_version"] == CURRENT_SCHEMA_VERSION
    assert COMPATIBILITY_PROMISE in schema["description"]
    assert set(schema["properties"]["result_schema_version"]["enum"]) == set(KNOWN_SCHEMA_V2_VERSIONS)


def test_required_keys_come_from_validator() -> None:
    schema = build_result_bundle_schema()
    expected = [key for key in SchemaV2Validator.REQUIRED_KEYS if key != "result_schema_version"]
    assert schema["required"] == expected
    assert set(schema["properties"]) == set(SchemaV2Validator.REQUIRED_KEYS) | set(SchemaV2Validator.OPTIONAL_KEYS)
    for block, required in BLOCK_REQUIRED:
        assert schema["properties"][block]["required"] == list(required)


def test_whole_corpus_validates_and_agrees_with_validator() -> None:
    validator = _validator()
    paths = _bundle_paths()
    assert len(paths) > 400
    failures: list[str] = []
    for path in paths:
        data = _load(path)
        errors = [error.message for error in validator.iter_errors(data)]
        if errors:
            failures.append(f"{path.name}: {errors[0][:200]}")
        SchemaV2Validator().validate(data)
    assert not failures, failures[:5]


@pytest.mark.parametrize("key", [key for key in SchemaV2Validator.REQUIRED_KEYS if key != "result_schema_version"])
def test_bundle_missing_required_top_key_fails(key: str) -> None:
    data = _current_bundle()
    del data[key]
    assert not _validator().is_valid(data)
    with pytest.raises(SchemaV2ValidationError):
        SchemaV2Validator().validate(data)


def test_bundle_without_any_version_fails() -> None:
    data = _current_bundle()
    data.pop("result_schema_version", None)
    data.pop("version", None)
    assert not _validator().is_valid(data)


@pytest.mark.parametrize(
    ("block", "key"),
    [(block, key) for block, keys in BLOCK_REQUIRED for key in keys],
)
def test_bundle_missing_required_block_key_fails(block: str, key: str) -> None:
    data = _current_bundle()
    del data[block][key]
    assert not _validator().is_valid(data)


def test_unexpected_top_level_key_fails() -> None:
    data = _current_bundle()
    data["surprise"] = 1
    assert not _validator().is_valid(data)


def test_host_load_block_validates_and_is_structured_from_builder_fields() -> None:
    validator = _validator()
    snapshot = {
        "available": True,
        "timestamp": "2026-11-16T10:00:00",
        "cpu": {"load_average": [1.234, 2.0, 3.0], "frequency_mhz": 3200.0},
        "memory": {"available_mb": 4096.5},
        "swap": {"used_mb": 0.0, "percent": 0.0},
    }
    data = _current_bundle()
    data.setdefault("environment", {})["host_load"] = build_host_load_block(snapshot, None)
    assert validator.is_valid(data), [e.message for e in validator.iter_errors(data)]
    assert set(data["environment"]["host_load"]["start"]) == set(HOST_LOAD_SAMPLE_FIELDS)
    assert set(host_load_sample(None)) == set(HOST_LOAD_SAMPLE_FIELDS)

    sample_schema = build_result_bundle_schema()["properties"]["environment"]["properties"]["host_load"]["properties"]
    assert sample_schema["start"]["required"] == list(HOST_LOAD_SAMPLE_FIELDS)

    broken = copy.deepcopy(data)
    del broken["environment"]["host_load"]["start"]["swap_percent"]
    assert not validator.is_valid(broken)
    broken = copy.deepcopy(data)
    broken["environment"]["host_load"]["end"]["load_average"] = "high"
    assert not validator.is_valid(broken)


def test_row_count_validation_rules() -> None:
    validator = _validator()
    data = _current_bundle()
    data["queries"][0]["row_count_validation"] = {"status": "PASSED", "expected": 1, "actual": 1}
    assert validator.is_valid(data)
    data["queries"][0]["row_count_validation"] = {"status": "BOGUS", "expected": 1, "actual": 1}
    assert not validator.is_valid(data)
    data["queries"][0]["row_count_validation"] = {"status": "PASSED", "expected": 1}
    assert not validator.is_valid(data)


def test_row_count_validation_requires_schema_2_2() -> None:
    data = _current_bundle()
    data["result_schema_version"] = "2.1"
    data["version"] = "2.1"
    data["queries"][0]["row_count_validation"] = {"status": "PASSED", "expected": 1, "actual": 1}
    assert not _validator().is_valid(data)


def test_current_version_requires_timing_fields() -> None:
    validator = _validator()
    data = _current_bundle()
    del data["run"]["timing_method"]
    assert not validator.is_valid(data)
    data = _current_bundle()
    data["run"]["timing_method"] = 99
    assert not validator.is_valid(data)


def test_drift_check_fails_when_validator_and_schema_disagree(monkeypatch: pytest.MonkeyPatch) -> None:
    assert check_result_bundle_schema()
    monkeypatch.setattr(SchemaV2Validator, "RUN_REQUIRED", (*SchemaV2Validator.RUN_REQUIRED, "invented_key"))
    assert not check_result_bundle_schema()


def test_drift_check_fails_on_stale_file(tmp_path: Path) -> None:
    stale = tmp_path / "schema.json"
    stale.write_text(DEFAULT_OUTPUT.read_text(encoding="utf-8").replace('"title"', '"headline"'), encoding="utf-8")
    assert not check_result_bundle_schema(stale)
    assert bundle_json_schema.main(["--check", "--output", str(stale)]) == 1
    assert bundle_json_schema.main(["--output", str(stale)]) == 0
    assert bundle_json_schema.main(["--check", "--output", str(stale)]) == 0


def _timing_bundle() -> dict:
    data = _current_bundle()
    data["run"]["timing_method"] = 2
    data["run"]["net_estimate_status"] = "embedded"
    data["platform"].pop("deployment", None)
    return data


def _assert_both_reject(data: dict) -> None:
    assert not _validator().is_valid(data)
    with pytest.raises(SchemaV2ValidationError):
        SchemaV2Validator().validate(data)


def _assert_both_accept(data: dict) -> None:
    errors = [error.message for error in _validator().iter_errors(data)]
    assert not errors, errors
    SchemaV2Validator().validate(data)


def test_eligible_net_estimate_requires_networked_endpoint_and_timing_method_2() -> None:
    data = _timing_bundle()
    data["run"]["net_estimate_status"] = "eligible"
    data["platform"]["deployment"] = {"endpoint_class": "embedded"}
    _assert_both_reject(data)
    data["platform"]["deployment"] = {"endpoint_class": "remote_host"}
    _assert_both_accept(data)
    data["run"]["timing_method"] = 1
    _assert_both_reject(data)
    data["run"]["timing_method"] = 2
    del data["platform"]["deployment"]
    _assert_both_reject(data)


def test_valid_platform_timing_requires_platform_ms() -> None:
    data = _timing_bundle()
    data["run"]["platform_timing"] = {"source": "s", "definition": "server_complete", "coverage": 1}
    data["queries"][0]["platform_timing"] = {"status": "valid"}
    _assert_both_reject(data)
    data["queries"][0]["platform_timing"] = {"status": "valid", "platform_ms": None}
    _assert_both_reject(data)
    data["queries"][0]["platform_timing"] = {"status": "valid", "platform_ms": 3.5}
    _assert_both_accept(data)


@pytest.mark.parametrize("status", sorted(PLATFORM_TIMING_REASON_STATUSES))
def test_suspect_and_unavailable_platform_timing_require_a_reason(status: str) -> None:
    data = _timing_bundle()
    data["run"]["platform_timing"] = {"source": "s", "definition": "server_complete", "coverage": 1}
    data["queries"][0]["platform_timing"] = {"status": status}
    _assert_both_reject(data)
    data["queries"][0]["platform_timing"] = {"status": status, "reason": "   "}
    _assert_both_reject(data)
    data["queries"][0]["platform_timing"] = {"status": status, "reason": "probe failed"}
    _assert_both_accept(data)


def test_run_platform_timing_is_required_when_a_query_carries_platform_timing() -> None:
    data = _timing_bundle()
    data["queries"][0]["platform_timing"] = {"status": "valid", "platform_ms": 1}
    _assert_both_reject(data)
    data["run"]["platform_timing"] = {"source": "s", "definition": "includes_fetch", "coverage": 0.5}
    _assert_both_accept(data)


@pytest.mark.parametrize("field", ["probed_at", "platform_timing"])
def test_client_link_timing_fields_require_schema_2_4(field: str) -> None:
    value = {"probed_at": "2026-11-16T10:00:00", "platform_timing": {"status": "valid", "platform_ms": 1}}[field]
    legacy = _current_bundle()
    for key in ("timing_method", "net_estimate_status", "platform_timing"):
        legacy["run"].pop(key, None)
    for query in legacy["queries"]:
        for key in ("request_count", "result_bytes", "platform_timing"):
            query.pop(key, None)
    legacy["result_schema_version"] = legacy["version"] = "2.3"
    legacy.setdefault("environment", {})["client_link"] = {}
    _assert_both_accept(legacy)
    legacy["environment"]["client_link"] = {field: value}
    _assert_both_reject(legacy)

    current = _current_bundle()
    current.setdefault("environment", {})["client_link"] = {field: value}
    _assert_both_accept(current)
    assert not _validator().is_valid({**current, "environment": {"client_link": {field: ""}}})


def test_version_and_result_schema_version_must_match() -> None:
    data = _current_bundle()
    data["result_schema_version"] = "2.4"
    data["version"] = "2.3"
    assert not _validator().is_valid(data)
    with pytest.raises(ValueError):
        SchemaV2Validator().validate(data)


def test_null_run_and_client_link_platform_timing_are_accepted_by_both() -> None:
    data = _current_bundle()
    data["run"]["platform_timing"] = None
    data.setdefault("environment", {})["client_link"] = {"probed_at": None, "platform_timing": None}
    _assert_both_accept(data)
    data["queries"][0]["platform_timing"] = {"status": "valid", "platform_ms": 1}
    _assert_both_reject(data)
