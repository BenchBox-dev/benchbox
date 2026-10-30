from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location("oracle_local_review", ROOT / "scripts/oracle_local_review.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap = _load()
REVIEWER_FILE = "_project/scripts/oracle_reviewers/cli.py"


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, files: dict[str, str]) -> str:
    for name, text in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        _git(repo, "add", name)
    _git(repo, "commit", "--quiet", "-m", "change")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def clones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "--quiet", "--initial-branch=develop")
    seed = {path: f"trusted {path}\n" for path in bootstrap.REVIEWER_PATHS if "." in Path(path).name}
    seed[REVIEWER_FILE] = "trusted cli\n"
    seed["unrelated.txt"] = "unrelated\n"
    _commit(origin, seed)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "--quiet", str(origin), str(work)], check=True, capture_output=True)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(work)
    return origin, work


def _args(*extra: str):
    return bootstrap.parser().parse_args(["--head", "HEAD", "--body-file", "body.md", *extra])


def test_the_export_holds_only_the_reviewer_paths_of_the_chosen_commit(clones: tuple[Path, Path]) -> None:
    _, work = clones
    (work / REVIEWER_FILE).write_text("branch edit\n", encoding="utf-8")
    commit = _git(work, "rev-parse", "origin/develop")

    target = bootstrap.export_reviewer(work, commit, Path.home() / bootstrap.EXPORT_PARENT)

    assert (target / REVIEWER_FILE).read_text(encoding="utf-8") == "trusted cli\n"
    assert not (target / "unrelated.txt").exists()
    assert target.name == commit


def test_a_complete_export_is_reused_for_the_same_commit(clones: tuple[Path, Path]) -> None:
    _, work = clones
    commit = _git(work, "rev-parse", "origin/develop")
    parent = Path.home() / bootstrap.EXPORT_PARENT
    first = bootstrap.export_reviewer(work, commit, parent)
    (first / "marker").write_text("kept\n", encoding="utf-8")

    second = bootstrap.export_reviewer(work, commit, parent)

    assert second == first and (second / "marker").is_file()


def test_the_review_launches_from_the_fetched_develop_export_with_the_pinned_runner(clones: tuple[Path, Path]) -> None:
    origin, work = clones
    advanced = _commit(origin, {REVIEWER_FILE: "advanced cli\n"})
    launched: list[tuple[list[str], dict[str, str]]] = []

    code = bootstrap.run(_args(), lambda argv, env: launched.append((argv, env)) or 0)

    assert code == 0
    ((argv, env),) = launched
    export = Path(env["PYTHONPATH"])
    assert export.name == advanced
    assert (export / REVIEWER_FILE).read_text(encoding="utf-8") == "advanced cli\n"
    assert argv[:2] == ["uv", "run"] and "--isolated" in argv and "--no-project" in argv
    assert argv[argv.index("python") + 1] == "-P"
    assert argv[argv.index("local-review") :][:3] == ["local-review", "--policy", ".github/oracle-reviewers.yml"]
    assert ("--base-ref", "origin/develop") in set(zip(argv, argv[1:], strict=False))


def test_the_review_refuses_without_launching_when_develop_cannot_be_fetched(
    clones: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    origin, work = clones
    _git(work, "remote", "set-url", "origin", str(origin) + "-missing")
    launched: list[object] = []

    code = bootstrap.run(_args(), lambda argv, env: launched.append(argv) or 0)

    assert code == bootstrap.EXIT_UNAVAILABLE
    assert launched == []
    error = capsys.readouterr().err
    assert "origin/develop" in error and "cannot run" in error
    assert not (Path.home() / bootstrap.EXPORT_PARENT).exists()


def test_an_explicit_reviewer_ref_is_exported_without_fetching(clones: tuple[Path, Path]) -> None:
    origin, work = clones
    _git(work, "remote", "set-url", "origin", str(origin) + "-missing")
    _git(work, "switch", "--quiet", "-c", "feature")
    branch_head = _commit(work, {REVIEWER_FILE: "branch cli\n"})
    launched: list[dict[str, str]] = []

    code = bootstrap.run(_args("--reviewer-ref", "HEAD"), lambda argv, env: launched.append(env) or 0)

    assert code == 0
    export = Path(launched[0]["PYTHONPATH"])
    assert export.name == branch_head
    assert (export / REVIEWER_FILE).read_text(encoding="utf-8") == "branch cli\n"


def test_only_the_newest_exports_are_kept(clones: tuple[Path, Path]) -> None:
    _, work = clones
    parent = Path.home() / bootstrap.EXPORT_PARENT
    parent.mkdir(parents=True)
    for index in range(5):
        stale = parent / f"{index:040d}"
        stale.mkdir()
        os.utime(stale, (index, index))
    current = bootstrap.export_reviewer(work, _git(work, "rev-parse", "origin/develop"), parent)

    bootstrap.prune_exports(parent, current)

    kept = sorted(item.name for item in parent.iterdir())
    assert len(kept) == bootstrap.EXPORT_KEEP
    assert current.name in kept and f"{4:040d}" in kept and f"{0:040d}" not in kept
