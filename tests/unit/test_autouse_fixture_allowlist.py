from __future__ import annotations

import ast
import json
from datetime import date
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = ROOT / "quality" / "autouse-fixture-allowlist.json"
REQUIRED_FIELDS = ("name", "scope", "owner", "rationale", "expiry")
OWNER = "CI maintainer"


def _fixture_options(decorator: ast.expr) -> dict[str, ast.expr] | None:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
    if name != "fixture":
        return None
    if not isinstance(decorator, ast.Call):
        return {}
    return {keyword.arg: keyword.value for keyword in decorator.keywords if keyword.arg}


def _literal(node: ast.expr | None, default: object) -> object:
    if node is None:
        return default
    return node.value if isinstance(node, ast.Constant) else "<dynamic>"


def _plugin_files(tree: ast.Module, root: Path) -> list[Path]:
    files: list[Path] = []
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "pytest_plugins" for t in node.targets)):
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            continue
        for element in node.value.elts:
            if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
                continue
            base = root.joinpath(*element.value.split("."))
            for candidate in (base.with_suffix(".py"), base / "__init__.py"):
                if candidate.is_file():
                    files.append(candidate)
                    break
    return files


def _non_function_autouse(path: Path, root: Path) -> tuple[list[str], list[Path]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    relative = path.relative_to(root).as_posix()
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            options = _fixture_options(decorator)
            if options is None:
                continue
            if _literal(options.get("autouse"), False) is False:
                continue
            scope = _literal(options.get("scope"), "function")
            if scope != "function":
                found.append(f"{relative}::{node.name}")
    return found, _plugin_files(tree, root)


def discover_non_function_autouse(root: Path = ROOT) -> set[str]:
    pending = sorted((root / "tests").rglob("conftest.py"))
    seen: set[Path] = set()
    found: set[str] = set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        names, plugins = _non_function_autouse(path, root)
        found.update(names)
        pending.extend(plugins)
    return found


def _load_entries() -> list[dict[str, str]]:
    return json.loads(ALLOWLIST.read_text(encoding="utf-8"))["fixtures"]


def allowlist_errors(found: set[str], entries: list[dict[str, str]], today: date) -> list[str]:
    errors: list[str] = []
    registered = {entry.get("name", "") for entry in entries}
    where = ALLOWLIST.relative_to(ROOT).as_posix()
    for name in sorted(found - registered):
        errors.append(
            f"{name} is a non-function-scoped autouse fixture not registered in {where}; "
            "it runs for every test in its reach and can multiply fast-tier time, "
            f"so add an entry with owner, rationale and expiry, or narrow its scope"
        )
    for name in sorted(registered - found):
        errors.append(f"{where} lists {name} but no such non-function-scoped autouse fixture exists; remove the entry")
    for entry in entries:
        name = entry.get("name", "<unnamed>")
        missing = [field for field in REQUIRED_FIELDS if not str(entry.get(field, "")).strip()]
        if missing:
            errors.append(f"{where} entry {name} is missing {', '.join(missing)}")
            continue
        if entry["owner"] != OWNER:
            errors.append(f"{where} entry {name} owner must be {OWNER!r}")
        if date.fromisoformat(entry["expiry"]) < today:
            errors.append(
                f"{where} entry {name} expired on {entry['expiry']}; re-justify the fixture and set a new expiry"
            )
    return errors


def test_non_function_autouse_fixtures_match_allowlist() -> None:
    errors = allowlist_errors(discover_non_function_autouse(), _load_entries(), date.today())

    assert not errors, "\n".join(errors)


def test_expired_entry_asks_for_rejustification() -> None:
    entries = [{**_load_entries()[0], "expiry": "2020-01-01"}]

    errors = allowlist_errors({entries[0]["name"]}, entries, date(2026, 1, 1))

    assert len(errors) == 1
    assert "expired" in errors[0]
    assert "re-justify" in errors[0]


def test_unregistered_fixture_names_fixture_and_allowlist(tmp_path: Path) -> None:
    conftest = tmp_path / "tests" / "conftest.py"
    conftest.parent.mkdir()
    conftest.write_text(
        "import pytest\n\n@pytest.fixture(scope='module', autouse=True)\ndef _synthetic():\n    yield\n",
        encoding="utf-8",
    )

    names = discover_non_function_autouse(tmp_path)
    errors = allowlist_errors(names, [], date(2026, 1, 1))

    assert len(errors) == 1
    assert "_synthetic" in errors[0]
    assert "autouse-fixture-allowlist.json" in errors[0]
