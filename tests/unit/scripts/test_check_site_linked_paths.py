from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from scripts import check_site_linked_paths as check

pytestmark = [pytest.mark.unit, pytest.mark.fast]

TREE = ["examples/run.py", "examples/unified/main.py", "benchbox/core/queries.py"]


def _inventory(tmp_path: Path, paths: list[dict[str, object]]) -> Path:
    target = tmp_path / "inventory.json"
    target.write_text(json.dumps({"paths": paths}), encoding="utf-8")
    return target


@pytest.fixture
def tree(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    files = list(TREE)
    monkeypatch.setattr("scripts.site_inputs.git_ls_files", lambda: files)
    return files


def test_present_files_and_directories_pass(
    tmp_path: Path, tree: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = _inventory(
        tmp_path,
        [
            {"path": "examples/run.py", "kind": "file", "sources": ["docs/a.md:3"]},
            {"path": "examples/unified", "kind": "directory", "sources": ["blog/post.md:9"]},
        ],
    )

    assert check.main(["--inventory-file", str(inventory)]) == 0
    assert "OK" in capsys.readouterr().out


def test_missing_path_fails_and_names_site_sources(
    tmp_path: Path, tree: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = _inventory(
        tmp_path,
        [{"path": "scripts/generate_compat_docs.py", "kind": "file", "sources": ["docs/adr.md:218", "docs/b.md:4"]}],
    )

    assert check.main(["--inventory-file", str(inventory)]) == 1
    err = capsys.readouterr().err
    assert "scripts/generate_compat_docs.py" in err
    assert "linked from docs/adr.md:218" in err
    assert "linked from docs/b.md:4" in err
    assert "drop the link in benchbox-site or pin it" in err
    assert "then relink" in err


def test_emptied_directory_fails(tmp_path: Path, tree: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    inventory = _inventory(tmp_path, [{"path": "examples/unified", "kind": "directory", "sources": ["docs/a.md:7"]}])
    tree.remove("examples/unified/main.py")

    assert check.main(["--inventory-file", str(inventory)]) == 1
    assert "examples/unified" in capsys.readouterr().err


@pytest.mark.parametrize("body", ["not json", "[]", '{"paths": [{"path": 3, "sources": []}]}', '{"paths": {}}'])
def test_malformed_inventory_warns_and_passes(
    tmp_path: Path, tree: list[str], capsys: pytest.CaptureFixture[str], body: str
) -> None:
    inventory = tmp_path / "inventory.json"
    inventory.write_text(body, encoding="utf-8")

    assert check.main(["--inventory-file", str(inventory)]) == 0
    assert "warning" in capsys.readouterr().err


def test_fetch_failure_warns_and_passes(
    tree: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> object:
        raise urllib.error.URLError("unavailable")

    monkeypatch.setattr(check.urllib.request, "urlopen", refuse)

    assert check.main(["--inventory-url", "https://example.invalid/inventory.json"]) == 0
    assert "warning: site-linked path check skipped" in capsys.readouterr().err


def test_fetched_inventory_with_missing_path_fails(
    tree: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"paths": [{"path": "gone.py", "kind": "file", "sources": ["docs/a.md:1"]}]}).encode()

    monkeypatch.setattr(check.urllib.request, "urlopen", lambda *_a, **_k: Response())

    assert check.main([]) == 1
    assert "gone.py" in capsys.readouterr().err
