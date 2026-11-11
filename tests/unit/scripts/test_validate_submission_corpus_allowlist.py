from __future__ import annotations

import os
import stat
import sys

import pytest

from scripts.validate_submission import corpus_permit_rejections, main

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _rejects(changed_paths: list[str]) -> list[str]:
    return corpus_permit_rejections(changed_paths)


def test_allows_primary_bundle_at_root(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    (tmp_path / "results-data" / "bundles").mkdir(parents=True)
    (tmp_path / "results-data" / "bundles" / "bundle.json").write_text("{}")
    monkeypatch.chdir(tmp_path)

    assert _rejects(["results-data/bundles/bundle.json"]) == []


def test_allows_bundle_in_subdir(tmp_path, monkeypatch) -> None:
    (tmp_path / "results-data" / "bundles" / "vendor").mkdir(parents=True)
    (tmp_path / "results-data" / "bundles" / "vendor" / "v.json").write_text("{}")
    monkeypatch.chdir(tmp_path)

    assert _rejects(["results-data/bundles/vendor/v.json"]) == []


@pytest.mark.parametrize(
    "name",
    [
        "b.plans.json",
        "b.tuning.json",
        "b.applied.json",
        "b.manifest.json",
        "submission-manifest.json",
        "corpus-inventory.json",
    ],
)
def test_allows_json_companion_and_manifest_types(name, tmp_path, monkeypatch) -> None:
    (tmp_path / "results-data" / "bundles").mkdir(parents=True)
    (tmp_path / "results-data" / "bundles" / name).write_text("{}")
    monkeypatch.chdir(tmp_path)

    assert _rejects([f"results-data/bundles/{name}"]) == []


@pytest.mark.parametrize(
    "path",
    [
        "results-data/bundles/trojan.yml",
        "results-data/bundles/evil.sh",
        "results-data/bundles/run.py",
        "results-data/bundles/pyproject.toml",
        "results-data/bundles/requirements.txt",
        "results-data/bundles/README.md",
        "results-data/bundles/noext",
    ],
)
def test_rejects_non_json_data_files(path, tmp_path, monkeypatch) -> None:
    (tmp_path / "results-data" / "bundles").mkdir(parents=True)
    (tmp_path / "results-data" / "bundles" / path.rsplit("/", 1)[-1]).write_text("x")
    monkeypatch.chdir(tmp_path)

    assert any("only supported corpus data files" in reason for reason in _rejects([path]))


@pytest.mark.parametrize(
    "name",
    ["package.json", "package-lock.json", "tsconfig.json", "npm-shrinkwrap.json", "composer.json"],
)
def test_rejects_json_named_non_data_manifest(name, tmp_path, monkeypatch) -> None:
    (tmp_path / "results-data" / "bundles").mkdir(parents=True)
    (tmp_path / "results-data" / "bundles" / name).write_text("{}")
    monkeypatch.chdir(tmp_path)

    assert any("only supported corpus data files" in reason for reason in _rejects([f"results-data/bundles/{name}"]))


def test_rejects_symlink(tmp_path, monkeypatch) -> None:
    bundles = tmp_path / "results-data" / "bundles"
    bundles.mkdir(parents=True)
    target = tmp_path / "outside.json"
    target.write_text("{}")
    (bundles / "link.json").symlink_to(target)
    monkeypatch.chdir(tmp_path)

    assert any("symlinks are not allowed" in reason for reason in _rejects(["results-data/bundles/link.json"]))


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no POSIX executable bit")
def test_rejects_executable_json(tmp_path, monkeypatch) -> None:
    bundles = tmp_path / "results-data" / "bundles"
    bundles.mkdir(parents=True)
    path = bundles / "evil.json"
    path.write_text("{}")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    monkeypatch.chdir(tmp_path)

    assert any("executable file is not allowed" in reason for reason in _rejects(["results-data/bundles/evil.json"]))


@pytest.mark.parametrize(
    "path",
    [
        "results-data/bundles/../escape.json",
        "results-data/bundles/../escape/../x.json",
        "../results-data/bundles/x.json",
        "/absolute/results-data/bundles/x.json",
        "results-data/bundles",
        "results-data/bundles/",
    ],
)
def test_rejects_or_skips_traversal_and_root(path, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "results-data" / "bundles").mkdir(parents=True)
    rejections = _rejects([path])
    if path in ("results-data/bundles", "results-data/bundles/"):
        assert rejections == []
    elif path.startswith("/"):
        assert any("absolute paths are not allowed" in reason for reason in rejections)
    else:
        assert any("path traversal is not allowed" in reason for reason in rejections)


def test_rejects_hidden_control_dir_under_corpus(tmp_path, monkeypatch) -> None:
    (tmp_path / "results-data" / "bundles" / ".github").mkdir(parents=True)
    (tmp_path / "results-data" / "bundles" / ".github" / "x.json").write_text("{}")
    (tmp_path / "results-data" / "bundles" / ".hidden.json").write_text("{}")
    monkeypatch.chdir(tmp_path)

    assert any("hidden control" in r for r in _rejects(["results-data/bundles/.github/x.json"]))
    assert any("hidden control" in r for r in _rejects(["results-data/bundles/.hidden.json"]))


def test_ignores_paths_outside_corpus_root(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    os.makedirs(tmp_path / "results-data" / "other")
    os.makedirs(tmp_path / "scripts")
    (tmp_path / "results-data" / "other" / "x.yml").write_text("x")
    (tmp_path / "scripts" / "tool.py").write_text("x")

    assert _rejects(["results-data/other/x.yml", "scripts/tool.py"]) == []


def _write_changed_paths(tmp_path, lines: list[str]):
    path = tmp_path / "changed.txt"
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def test_cli_fails_when_corpus_changed_paths_rejected(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    bundles = tmp_path / "results-data" / "bundles"
    bundles.mkdir(parents=True)
    (bundles / "bundle.json").write_text("{}")
    (bundles / "evil.sh").write_text("x")

    changed = _write_changed_paths(tmp_path, ["results-data/bundles/bundle.json", "results-data/bundles/evil.sh"])
    rc = main(["--corpus-changed-paths", changed])
    out = capsys.readouterr().out

    assert rc == 1
    assert "disallowed corpus path" in out


def test_cli_allows_only_supported_corpus_data(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    bundles = tmp_path / "results-data" / "bundles"
    bundles.mkdir(parents=True)
    (bundles / "bundle.json").write_text("{}")
    (bundles / "bundle.plans.json").write_text("{}")

    changed = _write_changed_paths(
        tmp_path, ["results-data/bundles/bundle.json", "results-data/bundles/bundle.plans.json"]
    )
    rc = main(["--corpus-changed-paths", changed])

    assert rc == 0


def test_cli_corpus_gate_runs_without_bundle_paths(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "results-data" / "bundles").mkdir(parents=True)

    changed = _write_changed_paths(tmp_path, ["results-data/bundles/trojan.yml"])
    rc = main(["--corpus-changed-paths", changed])

    assert rc == 1
    assert "disallowed corpus path" in capsys.readouterr().out


