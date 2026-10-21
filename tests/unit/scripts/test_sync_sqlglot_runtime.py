from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scripts.sync_sqlglot_runtime import NAMESPACE, PREFIX, REPOSITORY, adopt_wheel, check_runtime, inspect_wheel

pytestmark = [pytest.mark.unit, pytest.mark.fast]
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def runtime_wheel(tmp_path: Path) -> Path:
    version = "30.23.0.2"
    payload = {
        "__init__.py": b"from ._version import __version__\n",
        "_version.py": f"__version__ = {version!r}\n".encode(),
        "py.typed": b"",
        "LICENSE": b"Fixture license\n",
    }
    provenance = {
        "schema": 1,
        "distribution": "benchbox-sqlglot",
        "version": version,
        "repository": REPOSITORY,
        "namespace": NAMESPACE,
        "fork_sha": "a" * 40,
        "upstream_sha": "b" * 40,
        "dirty": False,
        "manifest": {"version": version, "upstream": {"sha": "b" * 40}},
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in payload.items()},
    }
    path = tmp_path / f"benchbox_sqlglot-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in payload.items():
            archive.writestr(PREFIX + name, content)
        archive.writestr(PREFIX + "benchbox_provenance.json", json.dumps(provenance))
        archive.writestr(
            f"benchbox_sqlglot-{version}.dist-info/METADATA", f"Name: benchbox-sqlglot\nVersion: {version}\n"
        )
        archive.writestr(
            f"benchbox_sqlglot-{version}.dist-info/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
    return path


def rewrite_wheel(path: Path, name: str, content: bytes) -> None:
    with zipfile.ZipFile(path) as archive:
        payload = {member: archive.read(member) for member in archive.namelist()}
    payload[name] = content
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member, data in payload.items():
            archive.writestr(member, data)


def test_adoption_keeps_only_wheel_and_pin(runtime_wheel: Path, tmp_path: Path) -> None:
    destination = tmp_path / "bundled"
    manifest = adopt_wheel(runtime_wheel, destination)
    assert manifest["sha256"] == hashlib.sha256(runtime_wheel.read_bytes()).hexdigest()
    archive_name = runtime_wheel.name + ".zip"
    assert sorted(path.name for path in destination.iterdir()) == ["SQLGLOT_LICENSE.txt", archive_name, "sqlglot.json"]
    assert (destination / archive_name).read_bytes() == runtime_wheel.read_bytes()
    assert (destination / "SQLGLOT_LICENSE.txt").read_bytes() == b"Fixture license\n"


def test_same_version_changed_artifact_requires_new_pin(runtime_wheel: Path, tmp_path: Path) -> None:
    destination = tmp_path / "bundled"
    adopt_wheel(runtime_wheel, destination)
    with zipfile.ZipFile(destination / (runtime_wheel.name + ".zip"), "a") as archive:
        archive.comment = b"different artifact bytes, same version and runtime"
    with pytest.raises(ValueError, match="immutable pin"):
        check_runtime(destination)


def test_plain_text_notice_must_match_bundled_license(runtime_wheel: Path, tmp_path: Path) -> None:
    destination = tmp_path / "bundled"
    adopt_wheel(runtime_wheel, destination)
    (destination / "SQLGLOT_LICENSE.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="notice"):
        check_runtime(destination)


def test_repository_runtime_loads_from_nested_archive_without_import_path_changes() -> None:
    probe = """
import importlib.resources, pickle, sys, zipimport
original = list(sys.path)
from benchbox._vendor import sqlglot
assert sys.path == original
assert isinstance(sqlglot.__loader__, zipimport.zipimporter)
assert '.whl.zip/benchbox/_vendor/sqlglot/' in sqlglot.__file__.replace(chr(92), '/')
assert importlib.resources.files(sqlglot).joinpath('LICENSE').read_text().startswith('MIT License')
expression = sqlglot.parse_one('SELECT 1')
assert pickle.loads(pickle.dumps(expression)) == expression
assert not any(name == 'sqlglot' or name.startswith('sqlglot.') for name in sys.modules)
"""
    subprocess.run([sys.executable, "-B", "-I", "-c", probe], cwd=ROOT, check=True)


def test_changed_runtime_payload_rejected(runtime_wheel: Path) -> None:
    rewrite_wheel(runtime_wheel, PREFIX + "__init__.py", b"raise RuntimeError('changed')\n")
    with pytest.raises(ValueError, match="payload hashes"):
        inspect_wheel(runtime_wheel)


@pytest.mark.parametrize("member", ["sqlglot/__init__.py", PREFIX + "extension.so", "../escape.py"])
def test_foreign_or_compiled_payload_rejected(runtime_wheel: Path, member: str) -> None:
    rewrite_wheel(runtime_wheel, member, b"invalid")
    with pytest.raises(ValueError):
        inspect_wheel(runtime_wheel)


def test_development_wheel_rejected_without_replacing_pin(runtime_wheel: Path, tmp_path: Path) -> None:
    destination = tmp_path / "bundled"
    adopt_wheel(runtime_wheel, destination)
    original = (destination / "sqlglot.json").read_bytes()
    with zipfile.ZipFile(runtime_wheel) as archive:
        provenance = json.loads(archive.read(PREFIX + "benchbox_provenance.json"))
    provenance["dirty"] = True
    rewrite_wheel(runtime_wheel, PREFIX + "benchbox_provenance.json", json.dumps(provenance).encode())
    with pytest.raises(ValueError, match="development"):
        adopt_wheel(runtime_wheel, destination)
    assert (destination / "sqlglot.json").read_bytes() == original


def test_unpacked_sources_rejected(runtime_wheel: Path, tmp_path: Path) -> None:
    destination = tmp_path / "bundled"
    adopt_wheel(runtime_wheel, destination)
    (destination / "sqlglot").mkdir()
    with pytest.raises(ValueError, match="Unpacked"):
        check_runtime(destination)


def test_extra_wheel_rejected(runtime_wheel: Path, tmp_path: Path) -> None:
    destination = tmp_path / "bundled"
    adopt_wheel(runtime_wheel, destination)
    (destination / "other.zip").write_bytes(b"extra")
    with pytest.raises(ValueError, match="Exactly"):
        check_runtime(destination)


def test_public_sqlglot_runtime_import_is_banned() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--config",
            str(ROOT / "pyproject.toml"),
            "--stdin-filename",
            "benchbox/forbidden_import.py",
            "--output-format",
            "json",
            "-",
        ],
        cwd=ROOT,
        input="import sqlglot\nprint(sqlglot.__version__)\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert any(finding["code"] == "TID251" for finding in json.loads(result.stdout))
