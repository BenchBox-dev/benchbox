from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="make worktree-* targets are POSIX shell tooling; not supported on Windows",
    ),
]

REPO_ROOT = Path(__file__).resolve().parents[3]


def run(cmd: list[str], cwd: Path, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, check=True, text=True, capture_output=True, **kwargs)


def init_feature_repo(path: Path) -> tuple[Path, Path]:

    path.mkdir()
    run(["git", "init", "-q"], path)
    run(["git", "config", "user.email", "test@example.com"], path)
    run(["git", "config", "user.name", "BenchBox Test"], path)
    (path / "README.md").write_text("test repo\n", encoding="utf-8")
    run(["git", "add", "README.md"], path)
    run(["git", "commit", "-m", "initial"], path)
    run(["git", "branch", "-M", "develop"], path)
    linked = path.parent / "BenchBox wt feature"
    run(["git", "worktree", "add", "-b", "feature/test-removal", str(linked), "develop"], path)
    return path, linked


def init_repo_with_origin(path: Path) -> Path:

    path.mkdir()
    run(["git", "init", "-q"], path)
    run(["git", "config", "user.email", "test@example.com"], path)
    run(["git", "config", "user.name", "BenchBox Test"], path)
    (path / "README.md").write_text("test repo\n", encoding="utf-8")
    run(["git", "add", "README.md"], path)
    run(["git", "commit", "-m", "initial"], path)
    run(["git", "branch", "-M", "develop"], path)
    origin = path.parent / "origin.git"
    run(["git", "clone", "--bare", "-q", str(path), str(origin)], path.parent)
    run(["git", "remote", "add", "origin", str(origin)], path)
    return path


def make_test_env(home: Path, *, identity: bool = True) -> dict[str, str]:
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "GIT_CONFIG_GLOBAL": str(home / ".gitconfig")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    if identity:
        run(["git", "config", "--file", str(home / ".gitconfig"), "user.name", "BenchBox Test"], home)
        run(["git", "config", "--file", str(home / ".gitconfig"), "user.email", "test@example.com"], home)
    return env


def make_target(
    repo: Path,
    target: str,
    *variables: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "-f", str(REPO_ROOT / "Makefile"), "-s", target, *variables],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def remove_worktree(repo: Path, linked: Path) -> subprocess.CompletedProcess[str]:
    return make_target(repo, "worktree-remove", f"WORKTREE_PATH={linked}")


def test_worktree_create_creates_a_new_linked_worktree(tmp_path: Path) -> None:
    repo = init_repo_with_origin(tmp_path / "BenchBox repo")
    home = tmp_path / "home"
    env = make_test_env(home)
    branch = f"fix/test-worktree-create-{tmp_path.name}"
    linked = tmp_path / "BenchBox created wt"
    result = make_target(
        repo,
        "worktree-create",
        f"BRANCH={branch}",
        f"WORKTREE_PATH={linked}",
        env=env,
    )

    try:
        assert result.returncode == 0, result.stderr
        assert linked.is_dir()
        assert (linked / "README.md").is_file()
        assert result.stdout.splitlines()[-1] == f"WORKTREE_PATH={linked.resolve()}"
        assert run(["git", "config", "--worktree", "--get", "user.name"], linked).stdout.strip() == "BenchBox Test"
    finally:
        if linked.exists():
            cleanup = subprocess.run(
                ["git", "worktree", "remove", "--force", str(linked)],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )
            assert cleanup.returncode == 0, cleanup.stderr
        subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
        )


def test_worktree_create_rolls_back_exact_resources_when_identity_setup_fails(tmp_path: Path) -> None:
    repo = init_repo_with_origin(tmp_path / "BenchBox repo")
    home = tmp_path / "home"
    env = make_test_env(home, identity=False)
    branch = f"fix/test-worktree-rollback-{tmp_path.name}"
    linked = tmp_path / "BenchBox rollback wt"

    result = make_target(
        repo,
        "worktree-create",
        f"BRANCH={branch}",
        f"WORKTREE_PATH={linked}",
        env=env,
    )

    assert result.returncode != 0
    assert "no global Git identity" in result.stderr
    assert not linked.exists()
    assert run(["git", "branch", "--list", branch], repo).stdout.strip() == ""
    assert str(linked) not in run(["git", "worktree", "list", "--porcelain"], repo).stdout


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal and executable shell-wrapper regression test")
def test_worktree_create_term_trap_exits_nonzero_after_exact_cleanup(tmp_path: Path) -> None:
    real_git = shutil.which("git")
    assert real_git is not None
    repo = init_repo_with_origin(tmp_path / "BenchBox repo")
    home = tmp_path / "home"
    env = make_test_env(home)
    branch = f"fix/test-worktree-signal-{tmp_path.name}"
    linked = tmp_path / "BenchBox interrupted wt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git_wrapper = bin_dir / "git"
    git_wrapper.write_text(
        f"""#!/bin/sh
if [ "${{1:-}}" = worktree ] && [ "${{2:-}}" = add ]; then
  "{real_git}" "$@"
  kill -TERM "$PPID"
  exit 0
fi
exec "{real_git}" "$@"
""",
        encoding="utf-8",
    )
    git_wrapper.chmod(0o755)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    result = make_target(
        repo,
        "worktree-create",
        f"BRANCH={branch}",
        f"WORKTREE_PATH={linked}",
        env=env,
    )

    assert result.returncode != 0
    assert "WORKTREE_PATH=" not in result.stdout
    assert not linked.exists()
    assert run(["git", "branch", "--list", branch], repo).stdout.strip() == ""
    assert str(linked) not in run(["git", "worktree", "list", "--porcelain"], repo).stdout


@pytest.mark.skipif(os.name == "nt", reason="POSIX process and executable shell-wrapper regression test")
def test_concurrent_worktree_create_cannot_rollback_the_winner(tmp_path: Path) -> None:
    real_git = shutil.which("git")
    assert real_git is not None
    repo = init_repo_with_origin(tmp_path / "BenchBox repo")
    home = tmp_path / "home"
    env = make_test_env(home)
    branch = f"fix/test-worktree-create-race-{tmp_path.name}"
    linked = tmp_path / "BenchBox race wt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    add_started = tmp_path / "worktree-add-started"
    git_wrapper = bin_dir / "git"
    git_wrapper.write_text(
        f"""#!/bin/sh
if [ "${{1:-}}" = worktree ] && [ "${{2:-}}" = add ]; then
  "{real_git}" "$@"
  touch "{add_started}"
  sleep 2
  exit 0
fi
exec "{real_git}" "$@"
""",
        encoding="utf-8",
    )
    git_wrapper.chmod(0o755)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    command = [
        "make",
        "-f",
        str(REPO_ROOT / "Makefile"),
        "-s",
        "worktree-create",
        f"BRANCH={branch}",
        f"WORKTREE_PATH={linked}",
    ]
    winner = subprocess.Popen(command, cwd=repo, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(40):
            if add_started.exists():
                break
            winner.poll()
            assert winner.returncode is None, winner.communicate()[1]
            time.sleep(0.05)
        assert add_started.exists(), "winner did not reach git worktree add"

        loser = subprocess.run(command, cwd=repo, env=env, text=True, capture_output=True, check=False)
        winner_stdout, winner_stderr = winner.communicate(timeout=10)
    finally:
        if winner.poll() is None:
            winner.kill()
            winner.communicate()

    assert loser.returncode != 0
    assert "another worktree-create is in progress" in loser.stderr
    assert winner.returncode == 0, winner_stderr
    assert "WORKTREE_PATH=" in winner_stdout
    assert linked.is_dir()
    assert run(["git", "symbolic-ref", "--short", "HEAD"], linked).stdout.strip() == branch
    assert run(["git", "branch", "--list", branch], repo).stdout.strip().lstrip("+ ") == branch

    cleanup = subprocess.run(
        ["git", "worktree", "remove", "--force", str(linked)],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    assert cleanup.returncode == 0, cleanup.stderr
    subprocess.run(["git", "branch", "-D", branch], cwd=repo, text=True, capture_output=True, check=False)


def test_worktree_create_refuses_when_lock_directory_cannot_be_created(tmp_path: Path) -> None:
    repo = init_repo_with_origin(tmp_path / "BenchBox repo")
    home = tmp_path / "home"
    env = make_test_env(home)
    branch = f"fix/test-worktree-perm-{tmp_path.name}"
    linked = tmp_path / "BenchBox perm wt"

    git_dir = repo / ".git"
    original_mode = git_dir.stat().st_mode
    git_dir.chmod(0o555)
    try:
        result = make_target(
            repo,
            "worktree-create",
            f"BRANCH={branch}",
            f"WORKTREE_PATH={linked}",
            env=env,
        )
    finally:
        git_dir.chmod(original_mode)

    assert result.returncode != 0
    assert "Could not create worktree-create lock directory" in result.stderr
    assert "without a live owner" not in result.stderr
    assert not linked.exists()


def test_worktree_create_refuses_stale_lock_without_live_owner(tmp_path: Path) -> None:
    repo = init_repo_with_origin(tmp_path / "BenchBox repo")
    home = tmp_path / "home"
    env = make_test_env(home)
    branch = f"fix/test-worktree-stale-{tmp_path.name}"
    linked = tmp_path / "BenchBox stale wt"

    lock_dir = repo / ".git" / "worktree-create.lock"
    lock_dir.mkdir()

    result = make_target(
        repo,
        "worktree-create",
        f"BRANCH={branch}",
        f"WORKTREE_PATH={linked}",
        env=env,
    )

    assert result.returncode != 0
    assert "worktree-create lock exists without a live owner" in result.stderr
    assert not linked.exists()


def test_worktree_create_refuses_broken_symlink_lock(tmp_path: Path) -> None:
    repo = init_repo_with_origin(tmp_path / "BenchBox repo")
    home = tmp_path / "home"
    env = make_test_env(home)
    branch = f"fix/test-worktree-symlink-lock-{tmp_path.name}"
    linked = tmp_path / "BenchBox symlink lock wt"

    lock_dir = repo / ".git" / "worktree-create.lock"
    lock_dir.symlink_to(tmp_path / "nonexistent-target")

    result = make_target(
        repo,
        "worktree-create",
        f"BRANCH={branch}",
        f"WORKTREE_PATH={linked}",
        env=env,
    )

    assert result.returncode != 0
    assert "worktree-create lock exists without a live owner" in result.stderr
    assert not linked.exists()


def test_worktree_create_refuses_protected_branch_before_remote_access(tmp_path: Path) -> None:
    repo = init_repo_with_origin(tmp_path / "BenchBox repo")
    linked = tmp_path / "protected wt"

    result = make_target(
        repo,
        "worktree-create",
        "BRANCH=develop",
        f"WORKTREE_PATH={linked}",
        env=make_test_env(tmp_path / "home"),
    )

    assert result.returncode != 0
    assert "protected branch" in result.stderr
    assert not linked.exists()


def test_make_worktree_values_are_not_shell_interpreted(tmp_path: Path) -> None:
    repo = init_repo_with_origin(tmp_path / "BenchBox repo")
    backtick_marker = tmp_path / "backtick-marker"
    semicolon_marker = tmp_path / "semicolon-marker"
    make_branch_marker = tmp_path / "make-branch-marker"
    make_path_marker = tmp_path / "make-path-marker"
    env = make_test_env(tmp_path / "home")

    create_result = make_target(
        repo,
        "worktree-create",
        f"BRANCH=fix/injection-`touch {backtick_marker}`",
        f"WORKTREE_PATH={tmp_path / 'unused wt'}",
        env=env,
    )
    remove_result = make_target(
        repo,
        "worktree-remove",
        f"WORKTREE_PATH={tmp_path / 'missing'}; touch {semicolon_marker}",
        env=env,
    )
    make_create_result = make_target(
        repo,
        "worktree-create",
        f"BRANCH=$(shell touch {make_branch_marker})",
        f"WORKTREE_PATH={tmp_path / 'unused make wt'}",
        env=env,
    )
    make_remove_result = make_target(
        repo,
        "worktree-remove",
        f"WORKTREE_PATH=$(shell touch {make_path_marker})",
        env=env,
    )

    assert create_result.returncode != 0
    assert remove_result.returncode != 0
    assert make_create_result.returncode != 0
    assert make_remove_result.returncode != 0
    assert not backtick_marker.exists()
    assert not semicolon_marker.exists()
    assert not make_branch_marker.exists()
    assert not make_path_marker.exists()


def test_worktree_remove_refuses_staged_changes(tmp_path: Path) -> None:
    repo, linked = init_feature_repo(tmp_path / "BenchBox")
    (linked / "dirty.txt").write_text("do not discard\n", encoding="utf-8")
    run(["git", "add", "dirty.txt"], linked)

    result = remove_worktree(repo, linked)

    assert result.returncode != 0
    assert "Refusing to remove dirty worktree" in result.stderr
    assert "A  dirty.txt" in result.stderr
    assert linked.exists()


def test_worktree_remove_refuses_untracked_files_including_benchbox_state(tmp_path: Path) -> None:
    repo, linked = init_feature_repo(tmp_path / "BenchBox")
    (linked / ".benchbox").mkdir()
    (linked / ".benchbox" / "state.json").write_text("do not discard\n", encoding="utf-8")

    result = remove_worktree(repo, linked)

    assert result.returncode != 0
    assert "Refusing to remove dirty worktree" in result.stderr
    assert "?? .benchbox/state.json" in result.stderr
    assert linked.exists()


def test_worktree_remove_refuses_locked_worktree(tmp_path: Path) -> None:
    repo, linked = init_feature_repo(tmp_path / "BenchBox")
    run(["git", "worktree", "lock", "--reason", "test guard", str(linked)], repo)

    result = remove_worktree(repo, linked)

    assert result.returncode != 0
    assert "worktree is locked" in result.stderr
    assert linked.exists()
    run(["git", "worktree", "unlock", str(linked)], repo)


def test_worktree_remove_refuses_detached_worktree_with_unreferenced_commit(tmp_path: Path) -> None:
    repo, linked = init_feature_repo(tmp_path / "BenchBox")
    run(["git", "switch", "--detach"], linked)
    (linked / "valuable.txt").write_text("preserve this commit\n", encoding="utf-8")
    run(["git", "add", "valuable.txt"], linked)
    run(["git", "commit", "-m", "valuable detached work"], linked)
    detached_oid = run(["git", "rev-parse", "HEAD"], linked).stdout.strip()

    result = remove_worktree(repo, linked)

    assert result.returncode != 0
    assert "worktree is detached" in result.stderr
    assert linked.exists()
    assert run(["git", "cat-file", "-t", detached_oid], repo).stdout.strip() == "commit"


def test_worktree_remove_refuses_primary_clone(tmp_path: Path) -> None:
    repo, linked = init_feature_repo(tmp_path / "BenchBox")

    result = remove_worktree(repo, repo)

    assert result.returncode != 0
    assert "primary clone" in result.stderr
    assert repo.exists()
    assert linked.exists()


def test_worktree_remove_refuses_unregistered_path(tmp_path: Path) -> None:
    repo, linked = init_feature_repo(tmp_path / "BenchBox")
    candidate = tmp_path / "unregistered"
    candidate.mkdir()

    result = remove_worktree(repo, candidate)

    assert result.returncode != 0
    assert "not an exact registered worktree" in result.stderr
    assert candidate.exists()
    assert linked.exists()


def test_worktree_remove_refuses_missing_registered_directory(tmp_path: Path) -> None:
    repo, linked = init_feature_repo(tmp_path / "BenchBox")
    shutil.rmtree(linked)

    result = remove_worktree(repo, linked)

    assert result.returncode != 0
    assert "registered worktree directory is missing" in result.stderr


def test_worktree_remove_accepts_a_symlink_spelling_of_the_exact_worktree(tmp_path: Path) -> None:
    repo, linked = init_feature_repo(tmp_path / "BenchBox")
    alias = tmp_path / "BenchBox worktree alias"
    alias.symlink_to(linked, target_is_directory=True)

    result = remove_worktree(repo, alias)

    assert result.returncode == 0, result.stderr
    assert not linked.exists()
    assert alias.is_symlink()


def test_worktree_remove_removes_only_clean_exact_path_and_keeps_branch(tmp_path: Path) -> None:
    repo, linked = init_feature_repo(tmp_path / "BenchBox")

    result = remove_worktree(repo, linked)

    assert result.returncode == 0, result.stderr
    assert not linked.exists()
    branches = run(["git", "branch", "--format=%(refname:short)"], repo).stdout.splitlines()
    assert "feature/test-removal" in branches
    worktrees = run(["git", "worktree", "list", "--porcelain"], repo).stdout
    assert str(linked) not in worktrees
