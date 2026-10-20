from __future__ import annotations

import os
import subprocess
import sys
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
METADATA_SCRIPT = REPO_ROOT / "scripts" / "worktree_lifecycle_metadata.py"


def run(cmd: list[str], cwd: Path, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, check=True, text=True, capture_output=True, **kwargs)


def init_repo_with_origin(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "-q"], path)
    run(["git", "config", "user.email", "test@example.com"], path)
    run(["git", "config", "user.name", "BenchBox Test"], path)
    (path / "README.md").write_text("test repo\n", encoding="utf-8")
    run(["git", "add", "README.md"], path)
    run(["git", "commit", "-m", "initial"], path)
    run(["git", "branch", "-M", "develop"], path)
    origin = path.parent / f"{path.name}-origin.git"
    run(["git", "clone", "--bare", "-q", str(path), str(origin)], path.parent)
    run(["git", "remote", "add", "origin", str(origin)], path)
    return path


def make_test_env(home: Path) -> dict[str, str]:
    home.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HOME": str(home), "GIT_CONFIG_GLOBAL": str(home / ".gitconfig")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
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


def _fake_uv_for_lifecycle(bin_dir: Path, calls: Path, install_attempt: Path) -> None:

    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = bin_dir / "uv"
    fake.write_text(
        "#!/bin/sh\n"
        f'for arg in "$@"; do printf "%s\\n" "$arg" >> "{calls}"; done\n'
        f'for arg in "$@"; do case "$arg" in pre-commit|install) touch "{install_attempt}" ;; esac; done\n'
        'if [ "${1:-}" = run ]; then\n'
        "  shift 3\n"
        '  [ "${1:-}" = python ] || exit 1\n'
        "  shift\n"
        f'  exec "{sys.executable}" "$@"\n'
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)


def test_worktree_create_does_not_install_shared_hooks_from_linked_worktree(tmp_path: Path) -> None:

    repo = init_repo_with_origin(tmp_path / "repo")
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text(
        f"#!/bin/sh\nINSTALL_PYTHON={sys.executable}\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    before = hook.read_text(encoding="utf-8")

    fake_bin = tmp_path / "fake-bin"
    calls = tmp_path / "uv-calls.txt"
    install_attempt = tmp_path / "install-attempted"
    _fake_uv_for_lifecycle(fake_bin, calls, install_attempt)
    env = {**make_test_env(tmp_path / "home"), "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}
    branch = "fix/test-no-linked-hook"
    linked = tmp_path / "wt-no-hook"

    result = make_target(
        repo,
        "worktree-create",
        f"BRANCH={branch}",
        f"WORKTREE_PATH={linked}",
        env=env,
    )

    try:
        assert result.returncode == 0, result.stderr
        assert not install_attempt.exists()

        call_lines = calls.read_text(encoding="utf-8").splitlines()
        assert "pre-commit" not in call_lines
        assert "install" not in call_lines
        assert hook.read_text(encoding="utf-8") == before
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


def test_worktree_create_publishes_immutable_provenance(tmp_path: Path) -> None:
    repo = init_repo_with_origin(tmp_path / "repo")
    env = make_test_env(tmp_path / "home")
    branch = "feat/test-metadata-create"
    linked = tmp_path / "wt-metadata"

    res = make_target(repo, "worktree-create", f"BRANCH={branch}", f"WORKTREE_PATH={linked}", env=env)
    assert res.returncode == 0, res.stderr

    meta_res = run([sys.executable, str(METADATA_SCRIPT), "read", "--worktree-path", str(linked), "--json"], linked)
    import json

    data = json.loads(meta_res.stdout)
    assert data["provenance_state"] == "managed"
    assert data["owner_state"] == "caller-owned"
    assert data["lifecycle_id"] is not None and len(data["lifecycle_id"]) == 32
    assert data["branch"] == branch
    assert data["base_ref"] == "origin/develop"
    assert len(data["base_oid"]) == 40
    assert len(data["initial_head"]) == 40
    assert data["deletion_authorized"] is False


@pytest.mark.parametrize(
    "prefix", ["feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore", "revert"]
)
def test_worktree_create_accepts_every_conventional_commit_type(tmp_path: Path, prefix: str) -> None:
    repo = init_repo_with_origin(tmp_path / "repo")
    env = make_test_env(tmp_path / "home")
    linked = tmp_path / "wt-prefix"

    res = make_target(repo, "worktree-create", f"BRANCH={prefix}/some-slug", f"WORKTREE_PATH={linked}", env=env)

    assert res.returncode == 0, res.stderr
    assert run(["git", "branch", "--show-current"], linked).stdout.strip() == f"{prefix}/some-slug"


@pytest.mark.parametrize("branch", ["wip/some-slug", "test/", "feature/x", "plain"])
def test_worktree_create_rejection_lists_allowed_prefixes(tmp_path: Path, branch: str) -> None:
    repo = init_repo_with_origin(tmp_path / "repo")
    env = make_test_env(tmp_path / "home")
    linked = tmp_path / "wt-bad"

    res = make_target(repo, "worktree-create", f"BRANCH={branch}", f"WORKTREE_PATH={linked}", env=env)

    assert res.returncode != 0
    assert "feat/ fix/ docs/ style/ refactor/ perf/ test/ build/ ci/ chore/ revert/" in res.stderr
    assert not linked.exists()


def test_worktree_create_with_controller_binding(tmp_path: Path) -> None:
    repo = init_repo_with_origin(tmp_path / "repo")
    env = make_test_env(tmp_path / "home")
    branch = "feat/test-controller-binding"
    linked = tmp_path / "wt-controller"

    res = make_target(
        repo,
        "worktree-create",
        f"BRANCH={branch}",
        f"WORKTREE_PATH={linked}",
        "CONTROLLER_KIND=bossmode",
        "CONTROLLER_ID=task-xyz",
        env=env,
    )
    assert res.returncode == 0, res.stderr

    meta_res = run([sys.executable, str(METADATA_SCRIPT), "read", "--worktree-path", str(linked), "--json"], linked)
    import json

    data = json.loads(meta_res.stdout)
    assert data["provenance_state"] == "managed"
    assert data["owner_state"] == "controller"
    assert data["controller_kind"] == "bossmode"
    assert data["controller_id"] == "task-xyz"
    assert data["deletion_authorized"] is False


def test_worktree_isolation_and_no_cross_leakage(tmp_path: Path) -> None:
    repo = init_repo_with_origin(tmp_path / "repo")
    env = make_test_env(tmp_path / "home")
    branch1 = "feat/test-iso-1"
    branch2 = "feat/test-iso-2"
    linked1 = tmp_path / "wt-iso-1"
    linked2 = tmp_path / "wt-iso-2"

    res1 = make_target(repo, "worktree-create", f"BRANCH={branch1}", f"WORKTREE_PATH={linked1}", env=env)
    assert res1.returncode == 0, res1.stderr
    res2 = make_target(repo, "worktree-create", f"BRANCH={branch2}", f"WORKTREE_PATH={linked2}", env=env)
    assert res2.returncode == 0, res2.stderr

    import json

    m1 = json.loads(
        run([sys.executable, str(METADATA_SCRIPT), "read", "--worktree-path", str(linked1), "--json"], linked1).stdout
    )
    m2 = json.loads(
        run([sys.executable, str(METADATA_SCRIPT), "read", "--worktree-path", str(linked2), "--json"], linked2).stdout
    )

    assert m1["lifecycle_id"] != m2["lifecycle_id"]
    assert m1["branch"] == branch1
    assert m2["branch"] == branch2


def test_manual_release_caller_owned_worktree(tmp_path: Path) -> None:
    repo = init_repo_with_origin(tmp_path / "repo")
    env = make_test_env(tmp_path / "home")
    branch = "feat/test-release-caller"
    linked = tmp_path / "wt-release"

    res = make_target(repo, "worktree-create", f"BRANCH={branch}", f"WORKTREE_PATH={linked}", env=env)
    assert res.returncode == 0, res.stderr

    rel_res = make_target(repo, "worktree-release", f"WORKTREE_PATH={linked}", env=env)
    assert rel_res.returncode == 0, rel_res.stderr
    assert "Manual owner release recorded" in rel_res.stdout

    import json

    data = json.loads(
        run([sys.executable, str(METADATA_SCRIPT), "read", "--worktree-path", str(linked), "--json"], linked).stdout
    )
    assert data["owner_state"] == "released"
    assert data["manual_released_at"] is not None
    assert data["manual_released_by"] == "BenchBox Test"
    assert data["deletion_authorized"] is False

    rel2_res = make_target(repo, "worktree-release", f"WORKTREE_PATH={linked}", env=env)
    assert rel2_res.returncode == 0


def test_manual_release_refuses_controller_bound_worktree(tmp_path: Path) -> None:
    repo = init_repo_with_origin(tmp_path / "repo")
    env = make_test_env(tmp_path / "home")
    branch = "feat/test-refuse-controller-rel"
    linked = tmp_path / "wt-controller-rel"

    res = make_target(
        repo,
        "worktree-create",
        f"BRANCH={branch}",
        f"WORKTREE_PATH={linked}",
        "CONTROLLER_KIND=bossmode",
        env=env,
    )
    assert res.returncode == 0, res.stderr

    rel_res = make_target(repo, "worktree-release", f"WORKTREE_PATH={linked}", env=env)
    assert rel_res.returncode != 0
    assert "controller-owned" in rel_res.stderr


def test_legacy_worktree_evaluates_to_unknown_owner(tmp_path: Path) -> None:
    repo = init_repo_with_origin(tmp_path / "repo")
    branch = "feat/test-legacy-wt"
    linked = tmp_path / "wt-legacy"

    run(["git", "worktree", "add", "-b", branch, str(linked), "develop"], repo)

    import json

    data = json.loads(
        run([sys.executable, str(METADATA_SCRIPT), "read", "--worktree-path", str(linked), "--json"], linked).stdout
    )
    assert data["provenance_state"] == "legacy"
    assert data["owner_state"] == "unknown"
    assert data["lifecycle_id"] is None
    assert data["deletion_authorized"] is False


def test_foreign_metadata_evaluates_to_unknown_owner(tmp_path: Path) -> None:
    repo = init_repo_with_origin(tmp_path / "repo")
    env = make_test_env(tmp_path / "home")
    branch = "feat/test-foreign-wt"
    linked = tmp_path / "wt-foreign"

    res = make_target(repo, "worktree-create", f"BRANCH={branch}", f"WORKTREE_PATH={linked}", env=env)
    assert res.returncode == 0, res.stderr

    run(["git", "config", "--worktree", "benchbox.worktree.controller-kind", "external-system-xyz"], linked)

    import json

    data = json.loads(
        run([sys.executable, str(METADATA_SCRIPT), "read", "--worktree-path", str(linked), "--json"], linked).stdout
    )
    assert data["provenance_state"] == "foreign"
    assert data["owner_state"] == "unknown"
    assert data["deletion_authorized"] is False


def test_malformed_metadata_evaluates_safely(tmp_path: Path) -> None:
    repo = init_repo_with_origin(tmp_path / "repo")
    env = make_test_env(tmp_path / "home")
    branch = "feat/test-malformed-wt"
    linked = tmp_path / "wt-malformed"

    res = make_target(repo, "worktree-create", f"BRANCH={branch}", f"WORKTREE_PATH={linked}", env=env)
    assert res.returncode == 0, res.stderr

    run(["git", "config", "--worktree", "benchbox.worktree.created-at", "not-a-timestamp"], linked)

    import json

    data = json.loads(
        run([sys.executable, str(METADATA_SCRIPT), "read", "--worktree-path", str(linked), "--json"], linked).stdout
    )
    assert data["provenance_state"] == "malformed"
    assert data["owner_state"] == "unknown"
    assert data["deletion_authorized"] is False


def test_metadata_failure_triggers_exact_creation_rollback(tmp_path: Path) -> None:
    repo = init_repo_with_origin(tmp_path / "repo")
    env = make_test_env(tmp_path / "home")
    branch = "feat/test-rollback-on-metadata-fail"
    linked = tmp_path / "wt-rollback-meta"

    res = make_target(
        repo,
        "worktree-create",
        f"BRANCH={branch}",
        f"WORKTREE_PATH={linked}",
        "CONTROLLER_KIND=invalid-controller-xyz",
        env=env,
    )
    assert res.returncode != 0
    assert not linked.exists()
    assert run(["git", "branch", "--list", branch], repo).stdout.strip() == ""
