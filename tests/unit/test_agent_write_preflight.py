from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tests.utilities.paths import REPO_ROOT
from tests.utilities.posix_shell import run_posix_shell, skip_without_posix_shell

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


SCRIPT = REPO_ROOT / "scripts" / "agent_write_preflight.sh"

HUMAN_IDENTITY = {
    "GIT_AUTHOR_NAME": "Joe Harris",
    "GIT_AUTHOR_EMAIL": "joeharris76@gmail.com",
}
AGENT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Claude",
    "GIT_AUTHOR_EMAIL": "noreply@anthropic.com",
}


def _run_script(script: Path, *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    skip_without_posix_shell()
    return run_posix_shell(
        f"sh {shlex.quote(script.as_posix())}",
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _configured_write_hook() -> dict[str, object]:
    import yaml

    config = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hooks = [hook for repo in config["repos"] for hook in repo.get("hooks", [])]
    return next(hook for hook in hooks if hook["id"] == "agent-write-preflight")


def _install_configured_write_hook(repo: Path) -> None:
    hook = _configured_write_hook()
    scripts = repo / "scripts"
    scripts.mkdir()
    (scripts / SCRIPT.name).write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    guard = REPO_ROOT / "scripts" / "agent_pre_commit_guard.sh"
    (scripts / guard.name).write_text(guard.read_text(encoding="utf-8"), encoding="utf-8")
    (repo / ".pre-commit-config.yaml").write_text(
        "repos:\n"
        "  - repo: local\n"
        "    hooks:\n"
        f"      - id: {hook['id']}\n"
        f"        name: {hook['name']}\n"
        f"        entry: {hook['entry']}\n"
        "        language: system\n"
        f"        pass_filenames: {str(hook['pass_filenames']).lower()}\n"
        f"        always_run: {str(hook['always_run']).lower()}\n"
        "        stages: [pre-commit]\n",
        encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, "-m", "pre_commit", "install", "--hook-type", "pre-commit"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _run_preflight(*, primary_clone: Path, allow: bool = False) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        **HUMAN_IDENTITY,
        "BENCHBOX_AGENT_PRIMARY_CLONE": str(primary_clone),
    }
    env.pop("BENCHBOX_EPHEMERAL_CLONE", None)
    if allow:
        env["BENCHBOX_ALLOW_MAIN_CLONE_WRITE"] = "1"
    else:
        env.pop("BENCHBOX_ALLOW_MAIN_CLONE_WRITE", None)
        env.pop("ALLOW_MAIN_CLONE_WRITE", None)

    return _run_script(SCRIPT, cwd=REPO_ROOT, env=env)


def test_preflight_rejects_primary_clone_without_override() -> None:
    result = _run_preflight(primary_clone=REPO_ROOT)

    assert result.returncode == 1
    assert "Refusing BenchBox write preflight in the primary clone" in result.stderr
    assert (
        "make worktree-create BRANCH=fix/descriptive-slug WORKTREE_PATH=../BenchBox.wt-fix-descriptive-slug"
        in result.stderr
    )


def test_preflight_allows_explicit_primary_clone_override(tmp_path: Path) -> None:
    repo = _init_clone(tmp_path / "BenchBox")
    result = _run_in_clone(repo, extra_env={"BENCHBOX_ALLOW_MAIN_CLONE_WRITE": "1"})

    assert result.returncode == 0
    assert "BenchBox write preflight OK" in result.stdout


def test_preflight_allows_non_primary_worktree(tmp_path: Path) -> None:
    primary = _init_clone(tmp_path / "BenchBox primary")
    linked = primary.parent / ".tmp-preflight-linked"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "fix/preflight-fixture", str(linked), "HEAD"],
        cwd=primary,
        check=True,
    )

    try:
        result = _run_script(
            SCRIPT.resolve(),
            cwd=linked,
            env={
                **os.environ,
                **HUMAN_IDENTITY,
                "BENCHBOX_AGENT_PRIMARY_CLONE": str(primary),
                "GIT_CONFIG_NOSYSTEM": "1",
            },
        )
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(linked)], cwd=primary, check=True)
        subprocess.run(["git", "branch", "-D", "fix/preflight-fixture"], cwd=primary, check=True)

    assert result.returncode == 0
    assert "BenchBox write preflight OK" in result.stdout


def test_configured_hook_requires_primary_clone_declaration_and_preserves_escape_hatches(tmp_path: Path) -> None:
    repo = _init_clone(tmp_path / "BenchBox")
    _install_configured_write_hook(repo)
    before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    (repo / "README.md").write_text("primary change\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md", ".pre-commit-config.yaml", "scripts"], cwd=repo, check=True)

    human_commit = subprocess.run(
        ["git", "commit", "-m", "human primary"],
        cwd=repo,
        env={**os.environ, **HUMAN_IDENTITY},
        capture_output=True,
        text=True,
        check=False,
    )

    assert human_commit.returncode != 0
    assert "Refusing BenchBox write preflight in the primary clone" in human_commit.stderr

    allowed = subprocess.run(
        ["git", "commit", "-m", "authorized primary"],
        cwd=repo,
        env={**os.environ, **HUMAN_IDENTITY, "BENCHBOX_ALLOW_MAIN_CLONE_WRITE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    assert (
        subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()
        != before
    )

    (repo / "README.md").write_text("ephemeral change\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    ephemeral = subprocess.run(
        ["git", "commit", "-m", "disposable clone"],
        cwd=repo,
        env={**os.environ, **HUMAN_IDENTITY, "BENCHBOX_EPHEMERAL_CLONE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert ephemeral.returncode == 0, ephemeral.stdout + ephemeral.stderr


def test_configured_hook_allows_linked_worktree_commit(tmp_path: Path) -> None:
    primary = _init_clone(tmp_path / "BenchBox")
    _install_configured_write_hook(primary)
    subprocess.run(["git", "add", ".pre-commit-config.yaml", "scripts"], cwd=primary, check=True)
    subprocess.run(["git", "commit", "--no-verify", "-m", "install fixture hook"], cwd=primary, check=True)
    linked = primary.parent / "BenchBox.wt-linked"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "fix/linked-commit", str(linked), "HEAD"],
        cwd=primary,
        check=True,
    )
    (linked / "README.md").write_text("linked change\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=linked, check=True)

    result = subprocess.run(
        ["git", "commit", "-m", "linked commit"],
        cwd=linked,
        env={**os.environ, **HUMAN_IDENTITY},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_claude_pr_command_runs_write_preflight_before_pr_workflow() -> None:
    command = (REPO_ROOT / ".claude" / "commands" / "pr.md").read_text(encoding="utf-8")

    assert "make agent-write-preflight" in command
    assert "make worktree-create BRANCH=<name> WORKTREE_PATH=<path>" in command
    assert "make worktree-add" not in command


def test_skill_sync_write_target_runs_preflight() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("\nskill-sync:", maxsplit=1)[1].split("\nskill-sync-check:", maxsplit=1)[0]

    assert "$(MAKE) -s agent-write-preflight" in target


def _init_clone(path: Path, *, with_hook: bool = True) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "BenchBox Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=path, check=True)
    if with_hook:
        _write_generated_hook(path, sys.executable)
    return path


def _run_in_clone(
    repo: Path,
    *,
    ephemeral: bool = False,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **HUMAN_IDENTITY}
    env.pop("BENCHBOX_AGENT_PRIMARY_CLONE", None)
    env.pop("BENCHBOX_ALLOW_MAIN_CLONE_WRITE", None)
    env.pop("ALLOW_MAIN_CLONE_WRITE", None)
    if ephemeral:
        env["BENCHBOX_EPHEMERAL_CLONE"] = "1"
    else:
        env.pop("BENCHBOX_EPHEMERAL_CLONE", None)
    env.update(extra_env or {})

    return _run_script(SCRIPT.resolve(), cwd=repo, env=env)


def test_preflight_still_refuses_an_undeclared_plain_clone(tmp_path: Path) -> None:
    result = _run_in_clone(_init_clone(tmp_path / "BenchBox"))

    assert result.returncode == 1
    assert "Refusing BenchBox write preflight in the primary clone" in result.stderr


def test_primary_clone_comparison_uses_filesystem_identity_across_path_spellings(tmp_path: Path) -> None:
    repo = _init_clone(tmp_path / "BenchBox")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_realpath = fake_bin / "realpath"
    fake_realpath.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = ".git" ]; then\n'
        "  printf '%s/.git\\n' \"$BENCHBOX_FAKE_PRIMARY_SPELLING\"\n"
        "else\n"
        "  printf '%s\\n' \"$1\"\n"
        "fi\n",
        encoding="utf-8",
    )
    fake_realpath.chmod(0o755)
    equivalent_spelling = f"{repo.as_posix()}/../{repo.name}"

    result = _run_in_clone(
        repo,
        extra_env={
            "BENCHBOX_FAKE_PRIMARY_SPELLING": equivalent_spelling,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        },
    )

    assert result.returncode == 1
    assert "Refusing BenchBox write preflight in the primary clone" in result.stderr


def test_preflight_allows_a_declared_ephemeral_clone(tmp_path: Path) -> None:
    result = _run_in_clone(_init_clone(tmp_path / "BenchBox"), ephemeral=True)

    assert result.returncode == 0
    assert "ephemeral clone" in result.stdout


def test_ephemeral_declaration_is_not_sibling_path_dependent(tmp_path: Path) -> None:
    repo = _init_clone(tmp_path / "BenchBox")
    (tmp_path / "BenchBox.sibling-worktree").mkdir()

    result = _run_in_clone(repo, ephemeral=True)

    assert result.returncode == 0
    assert "ephemeral clone" in result.stdout


def test_preflight_refuses_an_agent_author_identity(tmp_path: Path) -> None:
    result = _run_in_clone(_init_clone(tmp_path / "BenchBox"), ephemeral=True, extra_env=AGENT_IDENTITY)

    assert result.returncode == 1
    assert "Git author identity resolves to a known" in result.stderr
    assert "agent/service identity" in result.stderr
    assert "origins:" in result.stderr


def test_preflight_agent_identity_refusal_is_declarable(tmp_path: Path) -> None:
    result = _run_in_clone(
        _init_clone(tmp_path / "BenchBox"),
        ephemeral=True,
        extra_env={**AGENT_IDENTITY, "BENCHBOX_ALLOW_AGENT_GIT_IDENTITY": "1"},
    )

    assert result.returncode == 0
    assert "ephemeral clone" in result.stdout


def test_preflight_identity_check_is_not_confused_by_a_human_named_like_a_vendor(tmp_path: Path) -> None:
    result = _run_in_clone(
        _init_clone(tmp_path / "BenchBox"),
        ephemeral=True,
        extra_env={"GIT_AUTHOR_NAME": "Claudia Gemini-Lopez", "GIT_AUTHOR_EMAIL": "claudia@example.com"},
    )

    assert result.returncode == 0
    assert "ephemeral clone" in result.stdout


def test_refusal_names_the_ephemeral_escape_not_only_the_broad_override(tmp_path: Path) -> None:
    result = _run_in_clone(_init_clone(tmp_path / "BenchBox"))

    assert "BENCHBOX_EPHEMERAL_CLONE=1" in result.stderr
    assert "ephemeral" in result.stderr
    assert "make worktree-create" in result.stderr


def _fake_uv(bin_dir: Path, record: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = bin_dir / "uv"
    fake.write_text(f'#!/bin/sh\npwd >> "{record}"\nexit 0\n', encoding="utf-8")
    fake.chmod(0o755)


def _linked_worktree(tmp_path: Path) -> tuple[Path, Path]:
    primary = _init_clone(tmp_path / "BenchBox", with_hook=False)
    linked = primary.parent / "BenchBox.wt-hooks"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "fix/hook-health", str(linked), "HEAD"],
        cwd=primary,
        check=True,
    )
    return primary, linked


def _write_generated_hook(primary: Path, interpreter: str, hook_name: str = "pre-commit") -> None:
    hooks = primary / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / hook_name
    hook.write_text(
        "#!/usr/bin/env bash\n"
        "# File generated by pre-commit: https://pre-commit.com\n"
        "# start templated\n"
        f"INSTALL_PYTHON={interpreter}\n"
        "# end templated\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)


def test_preflight_fails_when_shared_hook_interpreter_no_longer_exists(tmp_path: Path) -> None:
    primary, linked = _linked_worktree(tmp_path)
    dead_interpreter = tmp_path / "BenchBox.wt-deleted" / ".venv" / "bin" / "python"
    _write_generated_hook(primary, str(dead_interpreter))
    hook = primary / ".git" / "hooks" / "pre-commit"
    before = hook.read_text(encoding="utf-8")
    record = tmp_path / "uv-cwd.txt"
    _fake_uv(tmp_path / "fake-bin", record)

    result = _run_in_clone(
        linked,
        extra_env={"PATH": f"{tmp_path / 'fake-bin'}{os.pathsep}{os.environ['PATH']}"},
    )

    assert result.returncode == 1
    assert "shared pre-commit hook is not usable" in result.stderr
    assert str(dead_interpreter) in result.stderr
    assert not record.exists()
    assert hook.read_text(encoding="utf-8") == before


def test_preflight_leaves_a_healthy_hook_alone(tmp_path: Path) -> None:
    primary, linked = _linked_worktree(tmp_path)
    _write_generated_hook(primary, sys.executable)
    record = tmp_path / "uv-cwd.txt"
    _fake_uv(tmp_path / "fake-bin", record)

    result = _run_in_clone(
        linked,
        extra_env={"PATH": f"{tmp_path / 'fake-bin'}{os.pathsep}{os.environ['PATH']}"},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Repaired commit-time hooks" not in result.stdout
    assert not record.exists()


def test_preflight_fails_when_shared_hook_is_missing(tmp_path: Path) -> None:
    primary, linked = _linked_worktree(tmp_path)
    record = tmp_path / "uv-cwd.txt"
    _fake_uv(tmp_path / "fake-bin", record)

    result = _run_in_clone(
        linked,
        extra_env={"PATH": f"{tmp_path / 'fake-bin'}{os.pathsep}{os.environ['PATH']}"},
    )

    assert result.returncode == 1
    assert "shared pre-commit hook is missing" in result.stderr
    assert "uv run -- pre-commit install" in result.stderr
    assert not record.exists()


def test_preflight_cold_start_does_not_repair_missing_shared_hooks(tmp_path: Path) -> None:
    primary, linked = _linked_worktree(tmp_path)
    record = tmp_path / "uv-cwd.txt"
    _fake_uv(tmp_path / "fake-bin", record)

    result = _run_in_clone(
        linked,
        extra_env={"PATH": f"{tmp_path / 'fake-bin'}{os.pathsep}{os.environ['PATH']}"},
    )

    assert result.returncode == 1
    assert "shared pre-commit hook is missing" in result.stderr
    assert "Do not run pre-commit install from a linked worktree" in result.stderr
    assert not record.exists()
    assert not (primary / ".git" / "hooks" / "pre-commit").exists()


@pytest.mark.parametrize("stale_stage", ["pre-push", "commit-msg"])
def test_preflight_fails_when_configured_sibling_hook_interpreter_is_stale(tmp_path: Path, stale_stage: str) -> None:
    primary, linked = _linked_worktree(tmp_path)
    (linked / ".pre-commit-config.yaml").write_text(
        "default_install_hook_types: [pre-commit, pre-push, commit-msg]\n",
        encoding="utf-8",
    )
    for hook_name in ("pre-commit", "pre-push", "commit-msg"):
        interpreter = sys.executable if hook_name != stale_stage else str(tmp_path / "deleted" / "python")
        _write_generated_hook(primary, interpreter, hook_name)

    record = tmp_path / "uv-cwd.txt"
    _fake_uv(tmp_path / "fake-bin", record)
    result = _run_in_clone(
        linked,
        extra_env={"PATH": f"{tmp_path / 'fake-bin'}{os.pathsep}{os.environ['PATH']}"},
    )

    assert result.returncode == 1
    assert f"shared {stale_stage} hook is not usable" in result.stderr
    assert str(tmp_path / "deleted" / "python") in result.stderr
    assert not record.exists()


@pytest.mark.parametrize("missing_stage", ["pre-push", "commit-msg"])
def test_preflight_fails_when_configured_sibling_hook_is_missing(tmp_path: Path, missing_stage: str) -> None:
    primary, linked = _linked_worktree(tmp_path)
    (linked / ".pre-commit-config.yaml").write_text(
        "default_install_hook_types: [pre-commit, pre-push, commit-msg]\n",
        encoding="utf-8",
    )
    for hook_name in ("pre-commit", "pre-push", "commit-msg"):
        if hook_name != missing_stage:
            _write_generated_hook(primary, sys.executable, hook_name)

    result = _run_in_clone(linked)

    assert result.returncode == 1
    assert f"shared {missing_stage} hook is missing" in result.stderr


def test_preflight_reads_block_style_configured_hook_types(tmp_path: Path) -> None:
    primary, linked = _linked_worktree(tmp_path)
    (linked / ".pre-commit-config.yaml").write_text(
        "default_install_hook_types:\n  - pre-commit\n  - pre-push\n  - commit-msg\n",
        encoding="utf-8",
    )
    for hook_name in ("pre-commit", "pre-push", "commit-msg"):
        _write_generated_hook(primary, sys.executable, hook_name)

    result = _run_in_clone(linked)

    assert result.returncode == 0, result.stderr


def test_worktree_removal_hook_isolation(tmp_path: Path) -> None:
    primary = _init_clone(tmp_path / "BenchBox")
    wt1 = tmp_path / "BenchBox.wt-one"
    wt2 = tmp_path / "BenchBox.wt-two"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "fix/one", str(wt1), "HEAD"], cwd=primary, check=True)
    subprocess.run(["git", "worktree", "add", "-q", "-b", "fix/two", str(wt2), "HEAD"], cwd=primary, check=True)

    record = tmp_path / "hook-ran.txt"
    hooks = primary / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        "# File generated by pre-commit: https://pre-commit.com\n"
        f"INSTALL_PYTHON={sys.executable}\n"
        f'echo "hook ran from $(pwd)" >> "{record}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    subprocess.run(["git", "worktree", "remove", str(wt1)], cwd=primary, check=True)

    result = _run_in_clone(wt2)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Repaired commit-time hooks" not in result.stdout

    test_file = wt2 / "test.txt"
    test_file.write_text("change\n", encoding="utf-8")
    subprocess.run(["git", "add", "test.txt"], cwd=wt2, check=True)
    commit_res = subprocess.run(
        ["git", "commit", "-m", "test commit"],
        cwd=wt2,
        env={**os.environ, **HUMAN_IDENTITY},
        capture_output=True,
        text=True,
        check=False,
    )
    assert commit_res.returncode == 0, commit_res.stdout + commit_res.stderr
    assert record.exists()
    assert wt2.name in record.read_text(encoding="utf-8")


def test_agent_identity_lists_match_the_instruction_audit() -> None:
    import importlib.util
    import re

    spec = importlib.util.spec_from_file_location(
        "agent_instruction_audit", REPO_ROOT / "_project" / "scripts" / "agent_instruction_audit.py"
    )
    audit = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = audit
    try:
        spec.loader.exec_module(audit)
    finally:
        sys.modules.pop(spec.name, None)
    script = SCRIPT.read_text(encoding="utf-8")

    def case_values(variable: str) -> set[str]:
        match = re.search(rf'case "\${variable}" in\n\s*([^)]+)\) agent_identity=yes', script)
        assert match, variable
        return {value.strip() for value in match.group(1).split("|")}

    assert case_values("author_email") == audit.AGENT_EMAILS
    assert case_values("author_name") == audit.AGENT_NAMES
    assert audit.SIGNING_SERVICE_EMAILS <= audit.AGENT_EMAILS
