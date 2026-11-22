from __future__ import annotations

import importlib.util
import itertools
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "_project" / "scripts" / "preflight_targets.py"


def _git(repo: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    done = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, encoding="utf-8", env=env)
    return done.stdout.strip()


_revisions = itertools.count()


def _commit(repo: Path, rel: str) -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"x = {next(_revisions)}\n", encoding="utf-8")
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", rel)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
        ["config", "commit.gpgsign", "false"],
    ):
        _git(root, *args)
    _commit(root, "tests/unit/test_alpha.py")
    _commit(root, "tests/unit/test_beta.py")
    _commit(root, "scripts/alpha.py")
    _commit(root, "scripts/beta.py")
    _git(root, "update-ref", "refs/remotes/origin/develop", "HEAD")
    _git(root, "checkout", "-q", "-b", "feature")
    return root


def _select(repo: Path, kind: str, base: str | None = None) -> list[str]:
    args = [sys.executable, str(SCRIPT), kind, "--repo-root", str(repo)]
    if base is not None:
        args += ["--base-ref", base]
    done = subprocess.run(args, check=True, capture_output=True, encoding="utf-8")
    return done.stdout.split()


def test_selection_since_a_commit_excludes_paths_changed_before_it(repo: Path) -> None:
    _commit(repo, "scripts/alpha.py")
    integration = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "scripts/beta.py")

    assert _select(repo, "tests", "origin/develop") == ["tests/unit/test_alpha.py", "tests/unit/test_beta.py"]
    assert _select(repo, "tests", integration) == ["tests/unit/test_beta.py"]
    assert _select(repo, "python", integration) == ["scripts/beta.py"]


def _write_selection(tmp_path: Path, changed: tuple[str, ...], deleted: tuple[str, ...]) -> dict[str, Path]:
    spec = importlib.util.spec_from_file_location("preflight_targets_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    targets = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(targets)
    tree = tmp_path / "selection-tree"
    for name in changed:
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        (tree / name).write_text("x = 1\n", encoding="utf-8")
    paths = [*changed, *deleted]
    selected = {
        "python": targets.changed_python(tree, paths),
        "lint": targets.lint_steps(tree, paths),
        "typecheck-files": targets.typecheck_files(tree, paths),
        "marker-files": targets.marker_files(tree, paths),
    }
    files: dict[str, Path] = {}
    for kind, lines in selected.items():
        files[kind] = tmp_path / f"selected-{kind}.txt"
        files[kind].write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return files


def _run_preflight(
    tmp_path: Path,
    base: str | None,
    extra_env: dict[str, str] | None = None,
    expect_code: int = 0,
    changed: tuple[str, ...] = (),
    deleted: tuple[str, ...] = (),
    collect_exit: int = 0,
) -> tuple[list[str], str]:
    work = tmp_path / "work"
    (work / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "Makefile", work / "Makefile")
    shutil.copytree(ROOT / "make", work / "make")
    for helper in ("check_primary_clone_clean.sh", "make_timing_shell.sh", "preflight_receipt.py"):
        shutil.copy(ROOT / "scripts" / helper, work / "scripts" / helper)
    (work / "_project" / "decisions").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "uv.log"
    stub = bin_dir / "uv"
    selection = _write_selection(tmp_path, changed, deleted)
    cases = "".join(f'*"preflight_targets.py {kind} "*) cat {path};; ' for kind, path in selection.items())
    cases += f'*"pytest --collect-only"*) exit {collect_exit};; '
    stub.write_text(f'#!/bin/sh\necho "$*" >> {log}\ncase "$*" in {cases}esac\n', encoding="utf-8")
    stub.chmod(0o755)
    git_log = tmp_path / "git.log"
    real_git = shutil.which("git")
    assert real_git is not None
    git_stub = bin_dir / "git"
    git_stub.write_text(
        f'#!/bin/sh\nif [ "$1" = fetch ]; then echo "$*" >> {git_log}; exit 0; fi\nexec {real_git} "$@"\n',
        encoding="utf-8",
    )
    git_stub.chmod(0o755)
    _git(work, "init", "-q", "-b", "main")
    args = ["make", "-s", "pr-preflight"]
    if base is not None:
        args.append(f"BASE={base}")
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("BASE", "BENCHBOX_TEST_SESSION_OWNER") and not key.startswith("GIT_")
    }
    env["BENCHBOX_PREFLIGHT_RECEIPT_DIR"] = str(tmp_path / "receipts")
    env.update(extra_env or {})
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    done = subprocess.run(args, cwd=work, env=env, encoding="utf-8", capture_output=True, check=False)
    assert done.returncode == expect_code, done.stdout + done.stderr
    if expect_code != 0:
        return [], done.stderr
    selections = [line for line in log.read_text(encoding="utf-8").splitlines() if "preflight_targets.py" in line]
    fetches = git_log.read_text(encoding="utf-8") if git_log.exists() else ""
    return selections, fetches


def test_pr_preflight_selects_against_base_without_fetching_develop(tmp_path: Path) -> None:
    selections, fetches = _run_preflight(tmp_path, "abc1234")

    assert {line.split()[-1] for line in selections} == {"abc1234"}
    assert {line.split()[-3] for line in selections} == {"python", "tests", "guards", "lint"}
    assert fetches == ""


def test_pr_preflight_defaults_to_a_fetched_origin_develop(tmp_path: Path) -> None:
    selections, fetches = _run_preflight(tmp_path, None)

    assert {line.split()[-1] for line in selections} == {"origin/develop"}
    assert fetches.split() == ["fetch", "origin", "develop", "--quiet"]


def test_pr_preflight_refuses_to_run_inside_a_lock_holder(tmp_path: Path) -> None:
    selections, stderr = _run_preflight(
        tmp_path, "abc1234", {"BENCHBOX_TEST_SESSION_OWNER": "123:456.0"}, expect_code=2
    )

    assert selections == []
    assert "never wrap the whole pipeline" in stderr
    assert not (tmp_path / "uv.log").exists()


def test_pr_preflight_light_steps_ignore_an_unrelated_lock_holder(tmp_path: Path) -> None:
    from benchbox.utils import host_lock

    lock_dir = tmp_path / "lock"
    env = {host_lock.LOCK_DIR_ENV: str(lock_dir), host_lock.WAIT_ENV: "0"}
    handle = host_lock.acquire(lock_dir / host_lock.LOCK_NAME, wait_seconds=0, phase="unrelated")
    try:
        selections, _ = _run_preflight(tmp_path, "abc1234", env)
    finally:
        handle.release()

    assert {line.split()[-1] for line in selections} == {"abc1234"}


@pytest.mark.parametrize("branch", ["develop", "release/0.5.x"])
def test_pr_preflight_takes_a_branch_name_as_that_branchs_fetched_origin_ref(tmp_path: Path, branch: str) -> None:
    selections, fetches = _run_preflight(tmp_path, branch)

    assert {line.split()[-1] for line in selections} == {f"origin/{branch}"}
    assert fetches.split() == ["fetch", "origin", branch, "--quiet"]


def _uv_calls(tmp_path: Path) -> list[str]:
    return (tmp_path / "uv.log").read_text(encoding="utf-8").splitlines()


STRATEGY_CALL = "run -- python -m pytest tests/unit/test_marker_strategy.py -q"


def _collect_calls(calls: list[str]) -> list[str]:
    return [call for call in calls if "pytest --collect-only" in call]


def _lint_calls(calls: list[str]) -> list[str]:
    return [call for call in calls if "lint-imports" in call or "scripts.typecheck" in call or "marker" in call]


def test_pr_preflight_runs_the_cheap_lint_guards_for_changed_python(tmp_path: Path) -> None:
    _run_preflight(
        tmp_path,
        "abc1234",
        changed=("benchbox/utils/a.py", "tests/unit/test_a.py", "scripts/b.py", "tests/fixtures/f.py"),
    )
    calls = _uv_calls(tmp_path)

    assert "run -- lint-imports" in calls
    assert "run -- python -m scripts.typecheck benchbox/utils/a.py tests/unit/test_a.py" in calls
    assert any(
        "pytest --collect-only" in call and call.endswith("tests.utilities.marker_manifest tests/unit/test_a.py")
        for call in calls
    )
    assert STRATEGY_CALL in calls


def test_pr_preflight_skips_marker_lint_and_typecheck_when_no_checked_file_changed(tmp_path: Path) -> None:
    _run_preflight(tmp_path, "abc1234", changed=("scripts/b.py",))
    calls = _uv_calls(tmp_path)

    assert "run -- lint-imports" in calls
    assert not [call for call in calls if "scripts.typecheck" in call or "marker" in call]


def test_pr_preflight_skips_the_lint_guards_when_no_python_changed(tmp_path: Path) -> None:
    _run_preflight(tmp_path, "abc1234", changed=("notes.txt",))

    assert _lint_calls(_uv_calls(tmp_path)) == []


@pytest.mark.parametrize("support_file", ["tests/conftest.py", "tests/utilities/helper.py"])
def test_pr_preflight_runs_the_marker_strategy_without_collecting_a_support_module(
    tmp_path: Path, support_file: str
) -> None:
    _run_preflight(tmp_path, "abc1234", changed=(support_file,))
    calls = _uv_calls(tmp_path)

    assert STRATEGY_CALL in calls
    assert _collect_calls(calls) == []


def test_pr_preflight_collects_a_changed_fixture_test_module_pytest_would_collect(tmp_path: Path) -> None:
    _run_preflight(tmp_path, "abc1234", changed=("tests/fixtures/test_sample.py",))
    calls = _uv_calls(tmp_path)

    assert STRATEGY_CALL in calls
    assert [call.split()[-1] for call in _collect_calls(calls)] == ["tests/fixtures/test_sample.py"]
    assert not [call for call in calls if "scripts.typecheck" in call]


def test_pr_preflight_collects_only_test_modules_among_changed_test_tree_files(tmp_path: Path) -> None:
    _run_preflight(
        tmp_path,
        "abc1234",
        changed=("tests/conftest.py", "tests/unit/test_a.py", "tests/unit/b_test.py", "tests/fixtures/test_f.py"),
    )
    calls = _uv_calls(tmp_path)

    assert STRATEGY_CALL in calls
    assert len(_collect_calls(calls)) == 1
    assert _collect_calls(calls)[0].endswith(
        "tests.utilities.marker_manifest tests/unit/test_a.py tests/unit/b_test.py tests/fixtures/test_f.py"
    )


def test_pr_preflight_runs_the_guards_for_a_deleted_test_module(tmp_path: Path) -> None:
    _run_preflight(tmp_path, "abc1234", deleted=("tests/unit/test_gone.py",))
    calls = _uv_calls(tmp_path)

    assert "run -- lint-imports" in calls
    assert STRATEGY_CALL in calls
    assert "run -- python -m scripts.typecheck" in calls
    assert _collect_calls(calls) == []


def test_pr_preflight_typechecks_only_files_that_still_exist_beside_a_deletion(tmp_path: Path) -> None:
    _run_preflight(tmp_path, "abc1234", changed=("benchbox/utils/a.py",), deleted=("benchbox/utils/gone.py",))

    assert "run -- python -m scripts.typecheck benchbox/utils/a.py" in _uv_calls(tmp_path)


def test_pr_preflight_runs_no_guards_for_a_deleted_non_python_file(tmp_path: Path) -> None:
    _run_preflight(tmp_path, "abc1234", deleted=("gone.txt",))

    assert _lint_calls(_uv_calls(tmp_path)) == []


def test_pr_preflight_accepts_a_collection_that_deselects_every_test(tmp_path: Path) -> None:
    _run_preflight(tmp_path, "abc1234", changed=("tests/unit/test_slow_only.py",), collect_exit=5)
    calls = _uv_calls(tmp_path)

    assert len(_collect_calls(calls)) == 1
    assert STRATEGY_CALL in calls
