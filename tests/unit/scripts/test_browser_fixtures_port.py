from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from _project.scripts.explorer_pipeline import browser_fixtures

pytestmark = [pytest.mark.unit, pytest.mark.medium]

STAGED_SOURCE_SHA256 = "5bbe1df3a3b7c469f556f0b97e0dda4a61b673abb9a83489e6076e5f0fc3c48e"


def _tree_digest(root: Path) -> str:
    lines = [
        f"{path.relative_to(root).as_posix()}:{hashlib.sha256(path.read_bytes()).hexdigest()}"
        for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix())
        if path.is_file()
    ]
    return hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest()


def test_staged_sources_match_the_retired_javascript_generator(tmp_path: Path) -> None:
    out = tmp_path / "bundles"
    out.mkdir()
    browser_fixtures.verify_source_bindings()
    browser_fixtures.stage_sources(browser_fixtures.SOURCE_ROOT, out)
    assert _tree_digest(tmp_path) == STAGED_SOURCE_SHA256


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (6.0, "6"),
        (-2.0, "-2"),
        (0.0, "0"),
        (-0.0, "0"),
        (0.1, "0.1"),
        (1e-7, "1e-7"),
        (1.5e-6, "0.0000015"),
        (123456789012345680000.0, "123456789012345680000"),
        (1e21, "1e+21"),
        (1.234e22, "1.234e+22"),
    ],
)
def test_numbers_serialize_like_json_stringify(value: float, expected: str) -> None:
    assert browser_fixtures.js_stringify(value) == expected


def test_containers_serialize_like_json_stringify_with_two_space_indent() -> None:
    payload = {"a": [1, {"b": None}], "e": {}, "l": [], "s": "é\n"}
    assert browser_fixtures.js_stringify(payload) == (
        '{\n  "a": [\n    1,\n    {\n      "b": null\n    }\n  ],\n  "e": {},\n  "l": [],\n  "s": "é\\n"\n}'
    )


def test_output_root_must_be_a_dedicated_directory(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="dedicated"):
        browser_fixtures.generate(tmp_path / "elsewhere")


def test_tampered_source_binding_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(browser_fixtures.SOURCE_ROOT, source)
    bundle = source / "bundles" / browser_fixtures.DUCKDB_SOURCE_BUNDLE
    bundle.write_text(bundle.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(RuntimeError, match="hash mismatch"):
        browser_fixtures.verify_source_bindings(source)


def test_generate_builds_a_read_model_with_every_fixture_role(tmp_path: Path) -> None:
    data = browser_fixtures.generate(tmp_path / f"{browser_fixtures.OUTPUT_ROOT_PREFIX}test")
    ids = json.loads((data / "fixture-ids.json").read_text(encoding="utf-8"))
    short_ids = browser_fixtures.short_ids(data / "results.duckdb")
    assert sorted(ids["ids"]) == sorted(browser_fixtures.FIXTURE_ROLES.values())
    assert ids["shortIds"] == {role: short_ids[result_id] for role, result_id in ids["ids"].items()}
    assert browser_fixtures.digest(data / "results.duckdb")["tables"]
