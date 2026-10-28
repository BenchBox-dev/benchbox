from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from scripts import check_site_linked_paths as check

pytestmark = [pytest.mark.unit, pytest.mark.fast]

TREE = ["examples/run.py", "examples/unified/main.py", "benchbox/core/queries.py", "tools/a/b/c.py"]


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
            {"path": "examples/run.py", "kind": "file", "sources": ["site/a.md:3"]},
            {"path": "examples/unified", "kind": "directory", "sources": ["site/post.md:9"]},
        ],
    )

    assert check.main(["--inventory-file", str(inventory)]) == 0
    assert "OK" in capsys.readouterr().out


def test_missing_path_fails_and_names_site_sources(
    tmp_path: Path, tree: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = _inventory(
        tmp_path,
        [{"path": "scripts/generate_compat_docs.py", "kind": "file", "sources": ["site/adr.md:218", "site/b.md:4"]}],
    )

    assert check.main(["--inventory-file", str(inventory)]) == 1
    err = capsys.readouterr().err
    assert "scripts/generate_compat_docs.py" in err
    assert "linked from site/adr.md:218" in err
    assert "linked from site/b.md:4" in err
    assert "drop the link in benchbox-site or pin it" in err
    assert "then relink" in err


def test_emptied_directory_fails(tmp_path: Path, tree: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    inventory = _inventory(tmp_path, [{"path": "examples/unified", "kind": "directory", "sources": ["site/a.md:7"]}])
    tree.remove("examples/unified/main.py")

    assert check.main(["--inventory-file", str(inventory)]) == 1
    assert "examples/unified" in capsys.readouterr().err


def test_directory_without_direct_files_passes(tmp_path: Path, tree: list[str]) -> None:
    inventory = _inventory(
        tmp_path,
        [
            {"path": "tools", "kind": "directory", "sources": ["site/a.md:1"]},
            {"path": "tools/a", "kind": "directory", "sources": ["site/a.md:2"]},
        ],
    )

    assert check.main(["--inventory-file", str(inventory)]) == 0


@pytest.mark.parametrize("body", ["not json", "[]", '{"paths": [{"path": 3, "sources": []}]}', '{"paths": {}}'])
def test_malformed_inventory_warns_and_passes(
    tmp_path: Path, tree: list[str], capsys: pytest.CaptureFixture[str], body: str
) -> None:
    inventory = tmp_path / "inventory.json"
    inventory.write_text(body, encoding="utf-8")

    assert check.main(["--inventory-file", str(inventory)]) == 0
    assert "warning" in capsys.readouterr().err


@pytest.fixture
def server() -> Iterator[tuple[str, dict[str, tuple[int, bytes]]]]:
    routes: dict[str, tuple[int, bytes]] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            status, body = routes.get(self.path, (404, b""))
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return None

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}", routes
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def test_fetch_failure_warns_and_passes(
    tree: list[str], server: tuple[str, dict[str, tuple[int, bytes]]], capsys: pytest.CaptureFixture[str]
) -> None:
    base, routes = server
    routes["/inventory.json"] = (503, b"unavailable")

    assert check.main(["--inventory-url", f"{base}/inventory.json"]) == 0
    assert "warning: site-linked path check skipped" in capsys.readouterr().err


def test_unreachable_server_warns_and_passes(tree: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert check.main(["--inventory-url", "http://127.0.0.1:9/inventory.json"]) == 0
    assert "warning: site-linked path check skipped" in capsys.readouterr().err


def test_fetched_inventory_with_missing_path_fails(
    tree: list[str], server: tuple[str, dict[str, tuple[int, bytes]]], capsys: pytest.CaptureFixture[str]
) -> None:
    base, routes = server
    body = json.dumps({"paths": [{"path": "gone.py", "kind": "file", "sources": ["site/a.md:1"]}]}).encode()
    routes["/inventory.json"] = (200, body)

    assert check.main(["--inventory-url", f"{base}/inventory.json"]) == 1
    assert "gone.py" in capsys.readouterr().err
