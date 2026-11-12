from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.utilities.paths import REPO_ROOT
from tests.utilities.posix_shell import run_posix_shell, skip_without_posix_shell

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
    pytest.mark.skipif(os.name == "nt", reason="the guard is exercised through a POSIX shebang and symlinks"),
]

GUARD = REPO_ROOT / "scripts" / "primary_clone_write_guard.py"
CLEAN_ENV_KEYS = (
    "BENCHBOX_ALLOW_MAIN_CLONE_WRITE",
    "ALLOW_MAIN_CLONE_WRITE",
    "BENCHBOX_AGENT_PRIMARY_CLONE",
    "PI_SESSION_ID",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_SESSION_ID",
    "AGENT_SESSION_ID",
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, Path]:
    primary = (tmp_path / "BenchBox").resolve()
    primary.mkdir()
    _git(primary, "init", "-q")
    _git(primary, "config", "user.name", "Test")
    _git(primary, "config", "user.email", "test@example.com")
    (primary / "app.txt").write_text("base\n", encoding="utf-8")
    _git(primary, "add", "app.txt")
    _git(primary, "commit", "-qm", "base")
    linked = primary.parent / "BenchBox.wt-fix"
    _git(primary, "worktree", "add", "-q", "-b", "fix/guard", str(linked))
    return primary, linked.resolve()


def _payload(tool_name: str, cwd: Path, **tool_input: object) -> dict[str, object]:
    return {"tool_name": tool_name, "cwd": str(cwd), "tool_input": tool_input}


def _run_guard(payload: object, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if k not in CLEAN_ENV_KEYS}
    base.update(env or {})
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([str(GUARD)], input=stdin, env=base, capture_output=True, text=True, check=False)


def test_relative_path_resolved_against_primary_cwd_is_refused_with_worktree_path(repo: tuple[Path, Path]) -> None:
    primary, linked = repo

    result = _run_guard(_payload("Edit", primary, file_path="app.txt"))

    assert result.returncode == 2
    assert str(linked / "app.txt") in result.stderr
    assert str(primary / "app.txt") in result.stderr


def test_absolute_primary_path_is_refused(repo: tuple[Path, Path]) -> None:
    primary, _ = repo

    assert _run_guard(_payload("Write", primary, file_path=str(primary / "sub" / "new.py"))).returncode == 2


def test_dotdot_escape_into_primary_is_refused(repo: tuple[Path, Path]) -> None:
    primary, linked = repo

    assert _run_guard(_payload("Write", linked, file_path="../BenchBox/app.txt")).returncode == 2


def test_symlink_into_primary_is_refused(repo: tuple[Path, Path], tmp_path: Path) -> None:
    primary, linked = repo
    link = tmp_path / "elsewhere"
    link.symlink_to(primary, target_is_directory=True)

    assert _run_guard(_payload("Write", linked, file_path=str(link / "app.txt"))).returncode == 2


def test_symlink_out_of_primary_into_worktree_is_allowed(repo: tuple[Path, Path]) -> None:
    primary, linked = repo
    (primary / "alias").symlink_to(linked, target_is_directory=True)

    assert _run_guard(_payload("Write", primary, file_path="alias/app.txt")).returncode == 0


@pytest.mark.parametrize("relative", ["app.txt", "new/dir/file.py"])
def test_paths_inside_linked_worktree_are_allowed(repo: tuple[Path, Path], relative: str) -> None:
    primary, linked = repo

    assert _run_guard(_payload("Write", primary, file_path=str(linked / relative))).returncode == 0
    assert _run_guard(_payload("Write", linked, file_path=relative)).returncode == 0


def test_linked_worktree_nested_inside_primary_is_allowed(repo: tuple[Path, Path]) -> None:
    primary, _ = repo
    nested = primary / ".claude" / "worktrees" / "nested"
    _git(primary, "worktree", "add", "-q", "-b", "fix/nested", str(nested))

    assert _run_guard(_payload("Write", primary, file_path=str(nested / "app.txt"))).returncode == 0


def test_paths_outside_repository_and_under_primary_git_dir_are_allowed(
    repo: tuple[Path, Path], tmp_path: Path
) -> None:
    primary, _ = repo

    assert _run_guard(_payload("Write", primary, file_path=str(tmp_path / "scratch.txt"))).returncode == 0
    assert _run_guard(_payload("Write", primary, file_path=".git/info/exclude")).returncode == 0
    assert _run_guard(_payload("Write", primary, file_path=str(primary / ".git" / "worktrees" / "x"))).returncode == 0


def test_override_allows_primary_write(repo: tuple[Path, Path]) -> None:
    primary, _ = repo

    result = _run_guard(_payload("Edit", primary, file_path="app.txt"), env={"BENCHBOX_ALLOW_MAIN_CLONE_WRITE": "1"})

    assert result.returncode == 0
    assert result.stderr == ""


def test_primary_without_linked_worktree_is_allowed(tmp_path: Path) -> None:
    primary = (tmp_path / "solo").resolve()
    primary.mkdir()
    _git(primary, "init", "-q")

    assert _run_guard(_payload("Write", primary, file_path="a.txt")).returncode == 0


def test_cwd_outside_any_repository_is_allowed(tmp_path: Path) -> None:
    assert _run_guard(_payload("Write", tmp_path, file_path="a.txt")).returncode == 0


def test_read_only_tools_are_not_guarded(repo: tuple[Path, Path]) -> None:
    primary, _ = repo

    assert _run_guard(_payload("Read", primary, file_path="app.txt")).returncode == 0
    assert _run_guard(_payload("grep", primary, path="app.txt")).returncode == 0


def test_apply_patch_headers_are_resolved_against_cwd(repo: tuple[Path, Path]) -> None:
    primary, linked = repo
    patch = "*** Begin Patch\n*** Update File: app.txt\n@@\n-base\n+stray\n*** End Patch\n"

    refused = _run_guard(_payload("apply_patch", primary, command=patch))
    allowed = _run_guard(_payload("apply_patch", linked, command=patch))

    assert refused.returncode == 2
    assert allowed.returncode == 0


def test_omp_edit_paths_list_is_checked(repo: tuple[Path, Path]) -> None:
    primary, linked = repo

    mixed = _run_guard(_payload("edit", primary, paths=[str(linked / "ok.py"), "app.txt"]))

    assert mixed.returncode == 2
    assert str(linked / "app.txt") in mixed.stderr


def test_omp_hashline_edit_headers_are_checked(repo: tuple[Path, Path]) -> None:
    primary, _ = repo

    result = _run_guard(_payload("edit", primary, input="[app.txt#1A2B]\nPUT 1:\n+x\n"))

    assert result.returncode == 2


def test_refusal_names_the_sessions_own_worktree_when_several_exist(repo: tuple[Path, Path]) -> None:
    primary, linked = repo
    other = primary.parent / "BenchBox.wt-other"
    _git(primary, "worktree", "add", "-q", "-b", "fix/other", str(other))
    own = _run_guard(_payload("Edit", linked, file_path="../BenchBox/app.txt"))
    assert str(linked / "app.txt") in own.stderr
    assert str(other / "app.txt") not in own.stderr


NON_FILESYSTEM_TARGETS = [
    "xd://mcp__tractable_take",
    "local://notes.md",
    "ssh://host/x",
    "conflict://1",
    "proc://id/kill",
    "agent://name",
    "out://x.md",
    "artifact://1",
]


@pytest.mark.parametrize("target", NON_FILESYSTEM_TARGETS)
@pytest.mark.parametrize("tool", ["write", "edit"])
def test_non_filesystem_targets_are_never_treated_as_paths(repo: tuple[Path, Path], target: str, tool: str) -> None:
    primary, _ = repo

    assert _run_guard(_payload(tool, primary, path=target)).returncode == 0
    assert _run_guard(_payload("edit", primary, input=f"[{target}#1A2B]\nPUT 1:\n+x\n")).returncode == 0
    assert _run_guard(_payload("ast_edit", primary, paths=[target])).returncode == 0


def test_dot_slash_prefix_keeps_a_uri_shaped_name_a_real_path(repo: tuple[Path, Path]) -> None:
    primary, _ = repo

    assert _run_guard(_payload("write", primary, path="./xd://literal")).returncode == 2


def test_file_url_into_primary_is_refused_and_into_worktree_is_allowed(repo: tuple[Path, Path]) -> None:
    primary, linked = repo

    assert _run_guard(_payload("write", linked, path=(primary / "app.txt").as_uri())).returncode == 2
    assert _run_guard(_payload("write", primary, path=(linked / "app.txt").as_uri())).returncode == 0


def test_write_path_wrapped_in_hashline_header_is_unwrapped(repo: tuple[Path, Path]) -> None:
    primary, linked = repo

    assert _run_guard(_payload("write", primary, path="[app.txt#1A2B]")).returncode == 2
    assert _run_guard(_payload("write", primary, path=f"[{linked / 'app.txt'}#1A2B]")).returncode == 0


def test_multi_file_edit_input_is_refused_when_any_section_targets_primary(repo: tuple[Path, Path]) -> None:
    primary, linked = repo
    own = linked / "ok.py"
    edit_input = f"*** Begin Patch\n[{own}#1A2B]\nPUT 1:\n+x\n[app.txt#3C4D]\nPUT 1:\n+y\n*** End Patch\n"

    result = _run_guard(_payload("edit", primary, input=edit_input))

    assert result.returncode == 2
    assert str(linked / "app.txt") in result.stderr
    assert str(own) not in result.stderr.split("Use the absolute path")[0]
    clean = f"[{own}#1A2B]\nPUT 1:\n+x\n[{linked / 'b.py'}#3C4D]\nPUT 1:\n+y\n"
    assert _run_guard(_payload("edit", primary, input=clean)).returncode == 0


@pytest.mark.parametrize("destination", ["app.txt", '"app.txt"', "'app.txt'"])
def test_edit_move_destination_into_primary_is_refused(repo: tuple[Path, Path], destination: str) -> None:
    primary, linked = repo
    edit_input = f"[{linked / 'a.py'}#1A2B]\nMV {destination}\n"

    assert _run_guard(_payload("edit", primary, input=edit_input)).returncode == 2
    assert (
        _run_guard(_payload("edit", primary, input=f"[{linked / 'a.py'}#1A2B]\nMV {linked / 'b.py'}\n")).returncode == 0
    )


def test_absolute_primary_write_is_refused_when_session_cwd_is_outside_any_repository(
    repo: tuple[Path, Path], tmp_path: Path
) -> None:
    primary, _ = repo
    scripts = primary / "scripts"
    scripts.mkdir()
    shutil.copy(GUARD, scripts / GUARD.name)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    payload = json.dumps(_payload("write", outside, path=str(primary / "app.txt")))
    base = {k: v for k, v in os.environ.items() if k not in CLEAN_ENV_KEYS}

    result = subprocess.run([str(scripts / GUARD.name)], input=payload, env=base, capture_output=True, text=True)

    assert result.returncode == 2


def test_unrelated_repository_primary_with_worktrees_is_not_guarded(repo: tuple[Path, Path], tmp_path: Path) -> None:
    other = (tmp_path / "other").resolve()
    other.mkdir()
    _git(other, "init", "-q")
    _git(other, "config", "user.name", "Test")
    _git(other, "config", "user.email", "test@example.com")
    (other / "f.txt").write_text("x\n", encoding="utf-8")
    _git(other, "add", "f.txt")
    _git(other, "commit", "-qm", "base")
    _git(other, "worktree", "add", "-q", "-b", "x", str(tmp_path / "other.wt"))
    primary, _ = repo

    assert _run_guard(_payload("write", primary, path=str(other / "f.txt"))).returncode == 0


def test_differently_cased_primary_path_is_refused_on_case_insensitive_filesystems(repo: tuple[Path, Path]) -> None:
    primary, _ = repo
    swapped = Path(str(primary).swapcase()) / "app.txt"
    if not swapped.exists():
        pytest.skip("filesystem is case sensitive")

    assert _run_guard(_payload("write", primary, path=str(swapped))).returncode == 2


def _js_runtime() -> list[str] | None:
    bun = shutil.which("bun")
    if bun:
        return [bun]
    node = shutil.which("node")
    return [node, "--experimental-strip-types", "--no-warnings"] if node else None


def test_omp_hook_module_blocks_primary_writes_and_ignores_everything_else(
    repo: tuple[Path, Path], tmp_path: Path
) -> None:
    runtime = _js_runtime()
    if runtime is None:
        pytest.skip("neither bun nor node is installed")
    primary, linked = repo
    scripts = primary / "scripts"
    scripts.mkdir()
    shutil.copy(GUARD, scripts / GUARD.name)
    shim = REPO_ROOT / ".omp" / "hooks" / "pre" / "primary-clone-write-guard.ts"
    runner = tmp_path / "run_hook.mjs"
    calls = [
        ["edit", {"input": "[app.txt#1A2B]\nPUT 1:\n+x\n"}],
        ["write", {"path": "app.txt", "content": "x"}],
        ["write", {"path": str(linked / "app.txt"), "content": "x"}],
        ["write", {"path": "xd://mcp__tractable_take", "content": "{}"}],
        ["read", {"path": "app.txt"}],
        ["bash", {"command": "echo app.txt"}],
        ["constructor", {}],
    ]
    runner.write_text(
        f"""import hook from {json.dumps(shim.as_uri())};
let handler;
hook({{ on: (_name, fn) => {{ handler = fn; }} }});
const ctx = {{ cwd: {json.dumps(str(primary))} }};
const calls = {json.dumps(calls)};
const out = [];
for (const [toolName, input] of calls) out.push((await handler({{ toolName, input }}, ctx)) ?? null);
console.log(JSON.stringify(out));
""",
        encoding="utf-8",
    )
    base = {k: v for k, v in os.environ.items() if k not in CLEAN_ENV_KEYS}

    skip_without_posix_shell()
    command = " ".join(shlex.quote(part) for part in [*runtime, str(runner)])
    result = run_posix_shell(
        command, cwd=tmp_path, env=base, capture_output=True, text=True, encoding="utf-8", check=False
    )

    assert result.returncode == 0, result.stderr
    outcomes = json.loads(result.stdout.strip().splitlines()[-1])
    assert outcomes[0]["block"] is True and str(linked / "app.txt") in outcomes[0]["reason"]
    assert outcomes[1]["block"] is True
    assert outcomes[2:] == [None] * 5


@pytest.mark.parametrize("stdin", ["not json", "[]"])
def test_malformed_payload_fails_open_and_says_so(stdin: str) -> None:
    result = _run_guard(stdin)

    assert result.returncode == 0
    assert "failed open" in result.stderr


def _run_configured(command: str, cwd: Path, payload: dict[str, object], env: dict[str, str]) -> int:
    skip_without_posix_shell()
    base = {k: v for k, v in os.environ.items() if k not in CLEAN_ENV_KEYS}
    base.update(env)
    result = run_posix_shell(
        command,
        cwd=cwd,
        env=base,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return result.returncode


def _install_scripts(target: Path) -> None:
    scripts = target / "scripts"
    scripts.mkdir(exist_ok=True)
    for name in ("primary_clone_write_guard.py", "worktree_lifecycle_metadata.py"):
        (scripts / name).write_text((REPO_ROOT / "scripts" / name).read_text(encoding="utf-8"), encoding="utf-8")


@pytest.mark.parametrize(
    ("config", "project_env", "tool"),
    [
        (".claude/settings.json", "CLAUDE_PROJECT_DIR", "Edit"),
        (".gemini/settings.json", "GEMINI_PROJECT_DIR", "replace"),
        (".codex/hooks.json", None, "apply_patch"),
    ],
)
def test_repo_tracked_harness_hook_refuses_primary_write_and_tolerates_missing_script(
    repo: tuple[Path, Path], config: str, project_env: str | None, tool: str
) -> None:
    primary, linked = repo
    document = json.loads((REPO_ROOT / config).read_text(encoding="utf-8"))["hooks"]
    group = next(iter(document.values()))[0]
    command = group["hooks"][0]["command"]
    env = {project_env: str(primary)} if project_env else {}
    tool_input = {"command": "*** Update File: app.txt\n"} if tool == "apply_patch" else {"file_path": "app.txt"}
    payload = {"tool_name": tool, "cwd": str(primary), "tool_input": tool_input}

    assert _run_configured(command, primary, payload, env) == 0
    _install_scripts(primary)
    assert _run_configured(command, primary, payload, env) == 2

    allowed_payload = {**payload, "tool_input": {"file_path": str(linked / "app.txt")}}
    assert _run_configured(command, primary, allowed_payload, env) == 0
