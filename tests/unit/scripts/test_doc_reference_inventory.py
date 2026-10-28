from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]

if not (ROOT / "_project/design/api-public-symbols.json").exists():
    pytest.skip("the documentation contract tooling needs the development tree (release tree)", allow_module_level=True)
SPEC = importlib.util.spec_from_file_location(
    "doc_reference_inventory_under_test", ROOT / "scripts" / "doc_reference_inventory.py"
)
assert SPEC is not None and SPEC.loader is not None
inventory = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = inventory
SPEC.loader.exec_module(inventory)


@pytest.fixture
def targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    manifest = {
        "files": {
            "docs/guide/page.md": "site-owned",
            "docs/guide/other.md": "site-owned",
            "docs/agent/notes.md": "core-only",
            "docs/publish-allowlist.txt": "core-only",
            "docs/reference/generated.md": "core-generated",
        },
        "generated": [{"path": "docs/generated/queries/", "generator": "scripts/vanished_docs_gen.py"}],
        "transitions": {"publication_controls": {"sources": ["docs/publish-allowlist.txt"]}},
    }
    path = tmp_path / "ownership.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(inventory, "OWNERSHIP_PATH", path)
    return inventory.Targets(["scripts/vanished_docs_gen.py"])


def _kinds(text: str, targets: Any) -> set[tuple[str, str]]:
    return set(inventory.extract(text, targets))


def test_site_owned_file_and_directory_references_are_classified(targets: Any) -> None:
    found = _kinds("see docs/guide/page.md and docs/guide/ for more", targets)
    assert found == {("docs/guide/page.md", "file"), ("docs/guide/", "dir")}


def test_trailing_punctuation_is_not_part_of_the_target(targets: Any) -> None:
    assert _kinds("(docs/guide/page.md).", targets) == {("docs/guide/page.md", "file")}


def test_core_only_pages_are_not_site_references(targets: Any) -> None:
    assert _kinds("docs/agent/notes.md and docs/agent/", targets) == set()


def test_transition_sources_become_site_references(targets: Any) -> None:
    assert _kinds("docs/publish-allowlist.txt", targets) == {("docs/publish-allowlist.txt", "file")}


def test_generated_outputs_and_extras(targets: Any) -> None:
    found = _kinds("docs/generated/queries/tpch/q1.md via scripts/vanished_docs_gen.py", targets)
    assert found == {
        ("docs/generated/queries/tpch/q1.md", "generated"),
        ("scripts/vanished_docs_gen.py", "extra"),
    }


def test_patterns_roots_built_urls_and_unresolved(targets: Any) -> None:
    found = _kinds("docs/**/*.md docs/ docs/guide/page.html docs/missing.md", targets)
    assert found == {
        ("docs/**/*.md", "pattern"),
        ("docs/", "root"),
        ("docs/guide/page.html", "built-url"),
        ("docs/missing.md", "unresolved"),
    }


def test_public_urls_and_nested_docs_directories_are_ignored(targets: Any) -> None:
    assert _kinds("https://benchbox.dev/docs/guide/page.md and other/docs/guide/page.md", targets) == set()


def test_path_segment_forms_are_joined(targets: Any) -> None:
    text = 'ROOT / "docs" / "guide" / "page.md" and Path("docs", "guide", "other.md") and root = ROOT / "docs"'
    assert _kinds(text, targets) == {
        ("docs/guide/page.md", "file"),
        ("docs/guide/other.md", "file"),
        ("docs/", "root"),
    }


def test_rule_matching_is_first_match_wins() -> None:
    rules = [
        {"id": "specific", "match": ["tests/unit/docs/*.py"]},
        {"id": "generic", "match": ["tests/**"]},
    ]
    assert inventory.match_rule("tests/unit/docs/test_x.py", rules)["id"] == "specific"
    assert inventory.match_rule("tests/unit/other.py", rules)["id"] == "generic"
    assert inventory.match_rule("scripts/x.py", rules) is None


def test_pointer_exists_handles_escapes_and_lists() -> None:
    document = {"a/b": {"c": [10, 20]}, "x": 1}
    assert inventory.pointer_exists(document, "/a~1b/c/1")
    assert not inventory.pointer_exists(document, "/a~1b/c/2")
    assert not inventory.pointer_exists(document, "/x/y")


def test_unclassified_references_fail_the_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rules = {"schema": 1, "extra_targets": [], "rules": []}
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps(rules), encoding="utf-8")
    monkeypatch.setattr(inventory, "RULES_PATH", rules_path)
    with pytest.raises(ValueError, match="unclassified references"):
        inventory.build_inventory()


def test_rules_with_unknown_disposition_or_missing_fields_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rules_path = tmp_path / "rules.json"
    monkeypatch.setattr(inventory, "RULES_PATH", rules_path)
    bad = {"rules": [{"id": "r", "match": ["*"], "disposition": "ignore"}]}
    rules_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown disposition"):
        inventory.load_rules()
    missing = {"rules": [{"id": "r", "match": ["*"], "disposition": "contract-replaced"}]}
    rules_path.write_text(json.dumps(missing), encoding="utf-8")
    with pytest.raises(ValueError, match="without contract_fields"):
        inventory.load_rules()


def test_committed_inventory_is_current_and_fully_dispositioned() -> None:
    committed = json.loads(inventory.INVENTORY_PATH.read_text(encoding="utf-8"))
    assert inventory.render(inventory.build_inventory()) == inventory.INVENTORY_PATH.read_text(encoding="utf-8")
    assert committed["summary"]["unclassified"] == 0
    assert {entry["disposition"] for entry in committed["files"]} <= set(inventory.DISPOSITIONS)
    assert all(entry["rule"] for entry in committed["files"])


def test_every_lock_test_file_has_a_lock_test_disposition() -> None:
    committed = json.loads(inventory.INVENTORY_PATH.read_text(encoding="utf-8"))
    table = inventory.read_json(inventory.LOCK_TESTS_PATH)
    lock_files = {row["file"] for row in table["lock_tests"]}
    dispositioned = {entry["path"] for entry in committed["files"] if entry["disposition"] == "lock-test"}
    assert dispositioned <= lock_files


def test_lock_test_rows_use_known_classifications_and_name_existing_tests() -> None:
    table = inventory.read_json(inventory.LOCK_TESTS_PATH)
    ids = [row["id"] for row in table["lock_tests"]]
    assert len(ids) == len(set(ids))
    for row in table["lock_tests"]:
        assert row["classification"] in inventory.LOCK_CLASSIFICATIONS
        text = (ROOT / row["file"]).read_text(encoding="utf-8")
        for name in row["tests"]:
            assert name in text, f"{row['id']}: {name}"


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("tests/a.py", "tests/*", True),
        ("tests/unit/a.py", "tests/*", False),
        ("tests/unit/a.py", "tests/**", True),
        ("tests/unit/deep/a.py", "tests/**/a.py", True),
        ("tests/a.py", "tests/**/a.py", True),
        ("tests/unit/a.py", "tests/unit/?.py", True),
        ("tests/unit/ab.py", "tests/unit/?.py", False),
        ("make/a.mk", "make/**", True),
        ("Makefile", "make/**", False),
    ],
)
def test_glob_star_stays_within_one_path_segment(path: str, pattern: str, expected: bool) -> None:
    assert inventory.glob_matches(path, pattern) is expected


def test_a_new_file_that_references_a_site_owned_page_fails_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, targets: Any
) -> None:
    (tmp_path / "tests/unit").mkdir(parents=True)
    (tmp_path / "tests/unit/test_new.py").write_text('PAGE = "docs/guide/page.md"\n', encoding="utf-8")
    rules = {
        "schema": 1,
        "extra_targets": [],
        "rules": [
            {"id": "samples", "match": ["tests/*"], "disposition": "fixture-data", "reason": "narrow"},
        ],
    }
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps(rules), encoding="utf-8")
    monkeypatch.setattr(inventory, "RULES_PATH", rules_path)
    monkeypatch.setattr(inventory, "ROOT", tmp_path)
    monkeypatch.setattr(inventory, "tracked_files", lambda: ["tests/unit/test_new.py"])
    with pytest.raises(ValueError, match=r"unclassified references.*tests/unit/test_new.py"):
        inventory.build_inventory()


def test_no_rule_pattern_is_a_bare_single_star_over_a_whole_directory_tree() -> None:
    rules = inventory.read_json(inventory.RULES_PATH)["rules"]
    broad = [pattern for rule in rules for pattern in rule["match"] if pattern in {"tests/*", "scripts/*", "*"}]
    assert broad == []


def _windows_locale_run(real_run: Any) -> Any:
    def run(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("text") and "encoding" not in kwargs:
            kwargs["encoding"] = "cp1252"
        return real_run(*args, **kwargs)

    return run


def test_tracked_files_decode_smart_quote_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    quoted = "notes/right" + chr(0x201D) + "quote.md"
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "core.quotepath", "false"], check=True, capture_output=True)
    target = repo / quoted
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("page\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", "add", "."],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(inventory, "ROOT", repo)
    monkeypatch.setattr(inventory, "subprocess", SimpleNamespace(run=_windows_locale_run(subprocess.run)))
    assert quoted in inventory.tracked_files()
