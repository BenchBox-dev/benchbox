from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from scripts import check_api_contract_symbols as tool

pytestmark = [pytest.mark.unit, pytest.mark.medium]

INSTALLED_VERSION = "1.2.3"


@pytest.fixture
def fake_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    site = tmp_path / "site"
    pkg = site / "fakepkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        textwrap.dedent(
            """
            _EXPORTS = {"Widget": "fakepkg.widget", "make": "fakepkg.widget", "LIMIT": "fakepkg.widget"}

            def __getattr__(name):
                if name in _EXPORTS:
                    import importlib
                    return getattr(importlib.import_module(_EXPORTS[name]), name)
                raise AttributeError(name)
            """
        ),
        encoding="utf-8",
    )
    (pkg / "widget.py").write_text(
        textwrap.dedent(
            """
            class Widget:
                def __init__(self, size: int = 1) -> None:
                    self.size = size

            def make(a, b=2):
                return a + b

            LIMIT = 5
            """
        ),
        encoding="utf-8",
    )
    (pkg / "other.py").write_text("class Widget:\n    pass\n", encoding="utf-8")
    dist = site / f"fakepkg-{INSTALLED_VERSION}.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: fakepkg\nVersion: {INSTALLED_VERSION}\n", encoding="utf-8"
    )
    monkeypatch.setenv("PYTHONPATH", str(site))
    return tmp_path


def _inventory(path: Path, **overrides: object) -> Path:
    symbols = [
        {
            "symbol": "fakepkg.Widget",
            "module": "fakepkg",
            "name": "Widget",
            "kind": "class",
            "aliases": ["fakepkg.widget.Widget"],
            "extras": [],
        },
        {
            "symbol": "fakepkg.make",
            "module": "fakepkg",
            "name": "make",
            "kind": "function",
            "aliases": [],
            "extras": [],
        },
        {
            "symbol": "fakepkg.LIMIT",
            "module": "fakepkg",
            "name": "LIMIT",
            "kind": "constant",
            "aliases": [],
            "extras": [],
        },
        {
            "symbol": "fakepkg.widget",
            "module": "fakepkg.widget",
            "name": None,
            "kind": "module",
            "aliases": [],
            "extras": [],
        },
    ]
    data = {
        "source": {"package": "fakepkg", "version": overrides.pop("version", INSTALLED_VERSION)},
        "symbols": symbols,
    }
    data.update(overrides)
    target = path / "symbols.json"
    target.write_text(json.dumps(data), encoding="utf-8")
    return target


def _run(argv: list[str]) -> int:
    return tool.main(argv)


def test_record_then_strict_check_passes(fake_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    symbols = _inventory(fake_env)

    assert _run(["record", "--python", sys.executable, "--symbols", str(symbols)]) == 0
    recorded = json.loads(symbols.read_text(encoding="utf-8"))
    by_symbol = {s["symbol"]: s for s in recorded["symbols"]}
    assert by_symbol["fakepkg.Widget"]["signature"] == "(size: int = 1) -> None"
    assert by_symbol["fakepkg.make"]["signature"] == "(a, b=2)"
    assert by_symbol["fakepkg.LIMIT"]["signature"] is None
    assert symbols.read_text(encoding="utf-8").endswith("}\n")

    capsys.readouterr()
    assert _run(["check", "--python", sys.executable, "--symbols", str(symbols), "--strict"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["passed"] == 4
    assert report["failures"] == []


def test_unrecorded_is_reported_but_only_fails_when_strict(fake_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    symbols = _inventory(fake_env)

    assert _run(["check", "--python", sys.executable, "--symbols", str(symbols)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report["unrecorded"]) == 4

    assert _run(["check", "--python", sys.executable, "--symbols", str(symbols), "--strict"]) == 1


def test_import_failure_fails(fake_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    symbols = _inventory(fake_env)
    data = json.loads(symbols.read_text(encoding="utf-8"))
    data["symbols"].append(
        {
            "symbol": "fakepkg.Missing",
            "module": "fakepkg",
            "name": "Missing",
            "kind": "class",
            "aliases": [],
            "extras": [],
        }
    )
    symbols.write_text(json.dumps(data), encoding="utf-8")

    assert _run(["check", "--python", sys.executable, "--symbols", str(symbols)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert any("fakepkg.Missing: import failure" in failure for failure in report["failures"])


def test_alias_mismatch_fails(fake_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    symbols = _inventory(fake_env)
    data = json.loads(symbols.read_text(encoding="utf-8"))
    data["symbols"][0]["aliases"] = ["fakepkg.other.Widget"]
    symbols.write_text(json.dumps(data), encoding="utf-8")

    assert _run(["check", "--python", sys.executable, "--symbols", str(symbols)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert any("alias mismatch" in failure for failure in report["failures"])


def test_signature_drift_fails(fake_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    symbols = _inventory(fake_env)
    assert _run(["record", "--python", sys.executable, "--symbols", str(symbols)]) == 0
    data = json.loads(symbols.read_text(encoding="utf-8"))
    data["symbols"][1]["signature"] = "(a)"
    symbols.write_text(json.dumps(data), encoding="utf-8")

    capsys.readouterr()
    assert _run(["check", "--python", sys.executable, "--symbols", str(symbols)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert any("signature drift" in failure for failure in report["failures"])


def test_version_mismatch_fails(fake_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    symbols = _inventory(fake_env, version="9.9.9")

    assert _run(["check", "--python", sys.executable, "--symbols", str(symbols)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert any(failure.startswith("version:") for failure in report["failures"])
    assert _run(["record", "--python", sys.executable, "--symbols", str(symbols)]) == 1


def test_record_refuses_broken_symbols(fake_env: Path) -> None:
    symbols = _inventory(fake_env)
    data = json.loads(symbols.read_text(encoding="utf-8"))
    data["symbols"][0]["aliases"] = ["fakepkg.other.Widget"]
    symbols.write_text(json.dumps(data), encoding="utf-8")

    assert _run(["record", "--python", sys.executable, "--symbols", str(symbols)]) == 1
    assert "signature" not in json.loads(symbols.read_text(encoding="utf-8"))["symbols"][1]


def test_editable_install_fails(fake_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    symbols = _inventory(fake_env)
    dist = fake_env / "site" / f"fakepkg-{INSTALLED_VERSION}.dist-info"
    (dist / "direct_url.json").write_text('{"url": "file:///src", "dir_info": {"editable": true}}', encoding="utf-8")

    assert _run(["check", "--python", sys.executable, "--symbols", str(symbols)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert any("direct_url.json" in failure for failure in report["failures"])
    assert _run(["record", "--python", sys.executable, "--symbols", str(symbols)]) == 1


def test_package_inside_repo_root_fails(fake_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    symbols = _inventory(fake_env)

    assert _run(["check", "--python", sys.executable, "--symbols", str(symbols), "--repo-root", str(fake_env)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert any("inside the repo" in failure for failure in report["failures"])


def test_missing_interpreter_exits_2(fake_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    symbols = _inventory(fake_env)

    assert _run(["check", "--python", str(fake_env / "nope" / "python"), "--symbols", str(symbols)]) == 2
    assert "not found" in capsys.readouterr().err


def test_relative_python_is_made_absolute(fake_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    link = fake_env / "pyexe_link"
    link.symlink_to(sys.executable)
    monkeypatch.chdir(fake_env)

    assert tool.resolve_python("pyexe_link") == str(link)
    assert tool.resolve_python("./pyexe_link") == str(link)


def test_symbols_are_probed_in_separate_processes(fake_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = fake_env / "imports.log"
    monkeypatch.setenv("IMPORT_LOG", str(log))
    (fake_env / "site" / "fakepkg" / "widget.py").write_text(
        "import os\nwith open(os.environ['IMPORT_LOG'], 'a') as fh:\n    fh.write('imported\\n')\n"
        "class Widget: pass\ndef make(): pass\nLIMIT = 1\n",
        encoding="utf-8",
    )
    symbols = [
        {"symbol": "fakepkg.Widget", "module": "fakepkg.widget", "name": "Widget", "aliases": []},
        {"symbol": "fakepkg.make", "module": "fakepkg.widget", "name": "make", "aliases": []},
    ]

    probe = tool.run_probe(sys.executable, symbols, "fakepkg")

    assert log.read_text(encoding="utf-8").count("imported") == 2
    assert all(result["import_error"] is None for result in probe["results"].values())


def test_invalid_dotted_name_is_rejected(fake_env: Path) -> None:
    entry = {"package": "fakepkg", "module": "fakepkg", "name": "Widget; import os", "aliases": []}
    report = tool.probe_one(sys.executable, str(fake_env), entry)

    assert "invalid attribute name" in report["result"]["import_error"]


def test_submodule_fallback(fake_env: Path) -> None:
    entry = {"package": "fakepkg", "module": "fakepkg", "name": "other", "aliases": []}
    report = tool.probe_one(sys.executable, str(fake_env), entry)

    assert report["result"]["import_error"] is None


def _known_failure_inventory(fake_env: Path, *, module: str, match: str) -> Path:
    (fake_env / "site" / "fakepkg" / "cyclic.py").write_text(
        "raise ImportError('cannot import name x from partially initialized module')\n", encoding="utf-8"
    )
    symbols = _inventory(fake_env)
    data = json.loads(symbols.read_text(encoding="utf-8"))
    data["symbols"].append(
        {
            "symbol": "fakepkg.Cyclic",
            "module": module,
            "name": "Cyclic",
            "kind": "class",
            "aliases": [],
            "extras": [],
            "known_import_failure": {"reason": "cycle", "match": match},
        }
    )
    symbols.write_text(json.dumps(data), encoding="utf-8")
    return symbols


def test_known_failure_is_expected_and_not_recorded(fake_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    symbols = _known_failure_inventory(fake_env, module="fakepkg.cyclic", match="partially initialized module")

    assert _run(["record", "--python", sys.executable, "--symbols", str(symbols)]) == 0
    recorded = {s["symbol"]: s for s in json.loads(symbols.read_text(encoding="utf-8"))["symbols"]}
    assert "signature" not in recorded["fakepkg.Cyclic"]
    assert "signature" in recorded["fakepkg.make"]

    capsys.readouterr()
    assert _run(["check", "--python", sys.executable, "--symbols", str(symbols), "--strict"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["expected_failures"] == ["fakepkg.Cyclic"]
    assert report["failures"] == []


def test_known_failure_that_now_passes_fails(fake_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    symbols = _known_failure_inventory(fake_env, module="fakepkg.widget", match="partially initialized module")
    (fake_env / "site" / "fakepkg" / "widget.py").write_text(
        "class Widget:\n    pass\n\nclass Cyclic:\n    pass\n\ndef make(a, b=2):\n    return a\n\nLIMIT = 5\n",
        encoding="utf-8",
    )

    assert _run(["check", "--python", sys.executable, "--symbols", str(symbols)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert any("known failure now passes" in failure for failure in report["failures"])


def test_known_failure_with_different_error_fails(fake_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    symbols = _known_failure_inventory(fake_env, module="fakepkg.cyclic", match="some other error")

    assert _run(["check", "--python", sys.executable, "--symbols", str(symbols)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert any("different error" in failure for failure in report["failures"])
    assert _run(["record", "--python", sys.executable, "--symbols", str(symbols)]) == 1
