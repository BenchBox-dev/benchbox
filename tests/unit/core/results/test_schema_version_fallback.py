from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchbox.core.results.loader import UnsupportedSchemaError, load_result_file
from benchbox.core.results.schema_policy import result_schema_version_value
from tests.fixtures.result_dict_fixtures import make_v2_result_dict

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.mark.parametrize(
    ("bundle", "expected"),
    [
        ({"result_schema_version": "2.2"}, "2.2"),
        ({"version": "2.1"}, "2.1"),
        ({"schema_version": "2.0"}, "2.0"),
        ({"result_schema_version": "2.2", "version": "2.2", "schema_version": "2.0"}, "2.2"),
        ({"version": "2.1", "schema_version": "2.0"}, "2.1"),
        ({"result_schema_version": "2.2", "schema_version": "2.0"}, "2.2"),
        ({}, None),
        ({"result_schema_version": None}, None),
    ],
)
def test_result_schema_version_value_fallback_matrix(bundle: dict, expected: str | None) -> None:
    assert result_schema_version_value(bundle) == expected


def test_result_schema_version_value_rejects_conflicting_aliases() -> None:
    with pytest.raises(ValueError, match="must match"):
        result_schema_version_value({"result_schema_version": "2.2", "version": "2.1"})


@pytest.mark.parametrize("not_a_dict", [None, "2.2", 22, ["2.2"]])
def test_result_schema_version_value_rejects_non_dicts(not_a_dict: object) -> None:
    assert result_schema_version_value(not_a_dict) is None


def _write_bundle(tmp_path: Path, data: dict) -> Path:
    file_path = tmp_path / "result.json"
    file_path.write_text(json.dumps(data), encoding="utf-8")
    return file_path


def test_load_oldest_schema_version_key(tmp_path: Path) -> None:
    data = make_v2_result_dict(version="2.1")
    del data["version"]
    data["schema_version"] = "2.1"

    result, _raw_data = load_result_file(_write_bundle(tmp_path, data))

    assert result.benchmark_id == data["benchmark"]["id"]


def test_load_accepts_matching_new_and_legacy_keys(tmp_path: Path) -> None:
    data = make_v2_result_dict(version="2.2")
    data["result_schema_version"] = "2.2"
    data["schema_version"] = "2.0"

    result, _raw_data = load_result_file(_write_bundle(tmp_path, data))

    assert result.benchmark_id == data["benchmark"]["id"]


def test_load_missing_all_version_keys_raises(tmp_path: Path) -> None:
    data = make_v2_result_dict(version="2.2")
    del data["version"]

    with pytest.raises(UnsupportedSchemaError):
        load_result_file(_write_bundle(tmp_path, data))


