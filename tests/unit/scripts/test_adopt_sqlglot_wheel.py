from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from scripts.adopt_sqlglot_wheel import adopt, main
from scripts.sync_sqlglot_runtime import NAMESPACE, PREFIX, REPOSITORY, adopt_wheel

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def build_wheel(directory: Path, version: str, dirty: bool = False) -> Path:
    payload = {
        "__init__.py": b"from ._version import __version__\n",
        "_version.py": f"__version__ = {version!r}\n".encode(),
        "py.typed": b"",
        "LICENSE": f"License {version}\n".encode(),
    }
    provenance = {
        "schema": 1,
        "distribution": "benchbox-sqlglot",
        "version": version,
        "repository": REPOSITORY,
        "namespace": NAMESPACE,
        "fork_sha": "a" * 40,
        "upstream_sha": "b" * 40,
        "dirty": dirty,
        "manifest": {"version": version, "upstream": {"sha": "b" * 40}},
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in payload.items()},
    }
    path = directory / f"benchbox_sqlglot-{version}-py3-none-any.whl"
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


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    runtime = root / "benchbox" / "_vendor"
    (root / "scripts").mkdir(parents=True)
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    manifest = adopt_wheel(build_wheel(incoming, "30.23.0.2"), runtime)
    baseline = {"schema": 2, "identity": manifest, "cases": []}
    (root / "scripts" / "sqlglot_runtime_baseline.json").write_text(json.dumps(baseline, indent=2) + "\n")
    return root


def state(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_adoption_updates_pin_baseline_and_runs_checks(repo: Path, tmp_path: Path) -> None:
    wheel = build_wheel(tmp_path, "30.23.0.3")
    calls: list[Path] = []
    manifest = adopt(wheel, digest(wheel), root=repo, checks=calls.append)
    runtime = repo / "benchbox" / "_vendor"
    assert calls == [repo]
    assert json.loads((runtime / "sqlglot.json").read_text()) == manifest
    assert sorted(p.name for p in runtime.iterdir()) == [
        "SQLGLOT_LICENSE.txt",
        "benchbox_sqlglot-30.23.0.3-py3-none-any.whl.zip",
        "sqlglot.json",
    ]
    baseline = json.loads((repo / "scripts" / "sqlglot_runtime_baseline.json").read_text())
    assert baseline["identity"] == manifest
    assert baseline["cases"] == []


def test_dry_run_changes_nothing(repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wheel = build_wheel(tmp_path, "30.23.0.3")
    before = state(repo)
    adopt(wheel, digest(wheel), root=repo, dry_run=True, checks=pytest.fail)
    assert state(repo) == before
    assert "30.23.0.2 -> 30.23.0.3" in capsys.readouterr().out


def test_unverified_wheel_refused(repo: Path, tmp_path: Path) -> None:
    wheel = build_wheel(tmp_path, "30.23.0.3")
    with pytest.raises(ValueError, match="unverified"):
        adopt(wheel, None, root=repo, checks=None)
    with pytest.raises(ValueError, match="does not match"):
        adopt(wheel, "0" * 64, root=repo, checks=None)


def test_sidecar_digest_accepted(repo: Path, tmp_path: Path) -> None:
    wheel = build_wheel(tmp_path, "30.23.0.3")
    (tmp_path / (wheel.name + ".sha256")).write_text(f"{digest(wheel)}  {wheel.name}\n")
    assert adopt(wheel, None, root=repo, checks=None)["version"] == "30.23.0.3"


def test_dirty_wheel_refused_without_changes(repo: Path, tmp_path: Path) -> None:
    wheel = build_wheel(tmp_path, "30.23.0.3", dirty=True)
    before = state(repo)
    with pytest.raises(ValueError, match="development"):
        adopt(wheel, digest(wheel), root=repo, checks=None)
    assert state(repo) == before


@pytest.mark.parametrize("version", ["30.23.0.2", "30.23.0.1"])
def test_not_newer_wheel_refused(repo: Path, tmp_path: Path, version: str) -> None:
    wheel = build_wheel(tmp_path, version)
    with pytest.raises(ValueError, match="not newer"):
        adopt(wheel, digest(wheel), root=repo, checks=None)


def test_failed_check_restores_previous_runtime(repo: Path, tmp_path: Path) -> None:
    wheel = build_wheel(tmp_path, "30.23.0.3")
    before = state(repo)

    def failing(_: Path) -> None:
        raise ValueError("retirement and baseline check failed")

    with pytest.raises(ValueError, match="retirement"):
        adopt(wheel, digest(wheel), root=repo, checks=failing)
    assert state(repo) == before


def test_main_reports_refusal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wheel = build_wheel(tmp_path, "30.23.0.3")
    assert main(["--wheel", str(wheel), "--dry-run"]) == 1
    assert "unverified" in capsys.readouterr().err
