from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
    pytest.mark.skipif(shutil.which("make") is None, reason="make is required"),
]

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "preflight_receipt.py"
KNOWN_TEST = "tests/unit/test_standardized_test_commands.py::TestMakefileCommands::test_unlock_without_uv"
ALLOWLIST = {
    "schema": 1,
    "entries": [
        {
            "step": "linux-pytest",
            "test": KNOWN_TEST,
            "reason": "the Linux VM has no uv on the test PATH",
            "tracker": "pr-preflight-linux-vm-env-failures-20261102",
        }
    ],
}
GITHUB_URL = "git@github.com:BenchBox-dev/BenchBox.git"


def _load():
    spec = importlib.util.spec_from_file_location("preflight_receipt", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


preflight_receipt = _load()


def _env(tmp_path: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        {
            "GIT_AUTHOR_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "T",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "BENCHBOX_PREFLIGHT_RECEIPT_DIR": str(tmp_path / "receipts"),
        }
    )
    return env


def _git(repo: Path, env: dict[str, str], *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, encoding="utf-8")
    return done.stdout.strip()


def _commit(repo: Path, env: dict[str, str], name: str, text: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, env, "add", name)
    _git(repo, env, "commit", "-q", "-m", name)


def _script(repo: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=repo, env=env, check=False, capture_output=True, text=True
    )


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    env = _env(tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, env, "init", "-q", "-b", "feat/x")
    _commit(root, env, "a.txt", "one\n")
    _git(root, env, "update-ref", "refs/remotes/origin/develop", "HEAD")
    _commit(root, env, "b.txt", "two\n")
    return root, env


def _write_allowlist(tmp_path: Path, payload: object = ALLOWLIST) -> Path:
    path = tmp_path / "allow.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


REFERENCE_GUARD_STEPS = [("docs-reference-inventory", "ok", 0), ("project-references", "ok", 0)]


def _steps(
    repo: Path,
    env: dict[str, str],
    tmp_path: Path,
    steps: list[tuple[str, str, int]],
    allow: Path | None,
    guards: bool = True,
):
    if guards:
        steps = [*steps, *(guard for guard in REFERENCE_GUARD_STEPS if guard[0] not in {name for name, _, _ in steps})]
    stage = tmp_path / "stage"
    assert _script(repo, env, "begin", "--stage", str(stage), "--base-ref", "origin/develop").returncode == 0
    codes = []
    for name, output, code in steps:
        command = [sys.executable, "-c", f"import sys; print({output!r}); sys.exit({code})"]
        extra = ["--allowlist", str(allow)] if allow else []
        codes.append(_script(repo, env, "step", "--stage", str(stage), "--name", name, *extra, "--", *command))
    return stage, codes


def _finalize(repo: Path, env: dict[str, str], stage: Path, status: int, allow: Path | None):
    extra = ["--allowlist", str(allow)] if allow else []
    return _script(repo, env, "finalize", "--stage", str(stage), "--exit-status", str(status), *extra)


def _verify(repo: Path, env: dict[str, str], allow: Path | None, *extra: str):
    flags = ["--allowlist", str(allow)] if allow else []
    return _script(repo, env, "verify", *flags, *extra)


def _receipt(repo: Path, env: dict[str, str], tmp_path: Path) -> dict:
    tree = _git(repo, env, "rev-parse", "HEAD^{tree}")
    return json.loads((tmp_path / "receipts" / f"{tree}.json").read_text(encoding="utf-8"))


def test_verify_refuses_when_no_receipt_exists(repo, tmp_path: Path) -> None:
    root, env = repo
    done = _verify(root, env, None)
    assert done.returncode == 1
    assert "no preflight receipt for tree" in done.stderr and "make pr-preflight" in done.stderr


def test_verify_accepts_a_matching_passing_receipt_and_renders_it(repo, tmp_path: Path) -> None:
    root, env = repo
    stage, codes = _steps(
        root,
        env,
        tmp_path,
        [("ruff-check", "All checks passed!", 0), ("pytest", "12 passed, 2 skipped in 3.40s", 0)],
        None,
    )
    assert [done.returncode for done in codes[:2]] == [0, 0]
    assert _finalize(root, env, stage, 0, None).returncode == 0
    receipt = _receipt(root, env, tmp_path)
    assert receipt["status"] == "passed"
    assert receipt["tree"] == _git(root, env, "rev-parse", "HEAD^{tree}")
    assert receipt["steps"][1]["counts"] == {"passed": 12, "skipped": 2}
    assert [step["exit_code"] for step in receipt["steps"][:2]] == [0, 0]
    summary = tmp_path / "summary.md"
    done = _verify(root, env, None, "--summary-out", str(summary))
    assert done.returncode == 0, done.stderr
    text = summary.read_text(encoding="utf-8")
    assert text.startswith(f"<!-- preflight-receipt: v1 begin tree={receipt['tree']} -->")
    assert "`pytest` exit 0: passed (12 passed, 2 skipped)" in text
    assert text.rstrip().endswith("<!-- preflight-receipt: v1 end -->")


def test_verify_refuses_a_stale_receipt_after_the_tree_changes(repo, tmp_path: Path) -> None:
    root, env = repo
    stage, _ = _steps(root, env, tmp_path, [("pytest", "1 passed in 0.10s", 0)], None)
    assert _finalize(root, env, stage, 0, None).returncode == 0
    assert _verify(root, env, None).returncode == 0
    _commit(root, env, "c.txt", "three\n")
    done = _verify(root, env, None)
    assert done.returncode == 1
    assert "no preflight receipt for tree" in done.stderr


def test_a_commit_that_keeps_the_same_tree_keeps_the_receipt_valid(repo, tmp_path: Path) -> None:
    root, env = repo
    stage, _ = _steps(root, env, tmp_path, [("pytest", "1 passed in 0.10s", 0)], None)
    assert _finalize(root, env, stage, 0, None).returncode == 0
    _git(root, env, "commit", "-q", "--amend", "-m", "reworded")
    assert _verify(root, env, None).returncode == 0


def test_verify_refuses_a_receipt_with_a_failed_step(repo, tmp_path: Path) -> None:
    root, env = repo
    stage, codes = _steps(
        root,
        env,
        tmp_path,
        [("ruff-check", "ok", 0), ("pytest", "FAILED tests/x.py::test_a - boom\n1 failed, 3 passed in 1.00s", 1)],
        None,
    )
    assert codes[1].returncode == 1
    assert _finalize(root, env, stage, 1, None).returncode == 0
    receipt = _receipt(root, env, tmp_path)
    assert receipt["status"] == "failed"
    assert receipt["steps"][1]["outcome"] == "failed"
    done = _verify(root, env, None)
    assert done.returncode == 1
    assert "shows a failed step (pytest)" in done.stderr


def test_verify_refuses_when_the_recorded_exit_status_is_nonzero_even_if_steps_look_fine(repo, tmp_path: Path) -> None:
    root, env = repo
    stage, _ = _steps(root, env, tmp_path, [("pytest", "1 passed in 0.10s", 0)], None)
    assert _finalize(root, env, stage, 3, None).returncode == 0
    done = _verify(root, env, None)
    assert done.returncode == 1 and "shows a failed step" in done.stderr


def test_no_receipt_is_written_for_a_dirty_tree(repo, tmp_path: Path) -> None:
    root, env = repo
    (root / "scratch.txt").write_text("x", encoding="utf-8")
    stage, _ = _steps(root, env, tmp_path, [("pytest", "1 passed in 0.10s", 0)], None)
    done = _finalize(root, env, stage, 0, None)
    assert done.returncode == 0
    assert "receipt not written" in done.stderr
    assert not (tmp_path / "receipts").exists()
    assert _verify(root, env, None).returncode == 1


def test_no_receipt_is_written_when_the_tree_changes_during_the_run(repo, tmp_path: Path) -> None:
    root, env = repo
    stage, _ = _steps(root, env, tmp_path, [("pytest", "1 passed in 0.10s", 0)], None)
    _commit(root, env, "c.txt", "three\n")
    done = _finalize(root, env, stage, 0, None)
    assert "the tree changed while preflight ran" in done.stderr
    assert not (tmp_path / "receipts").exists()


LINUX_FAILURE = f"FAILED {KNOWN_TEST} - AssertionError\n1 failed, 40 passed in 9.00s"


def test_an_allowlisted_environment_failure_is_accepted_and_listed(repo, tmp_path: Path) -> None:
    root, env = repo
    allow = _write_allowlist(tmp_path)
    stage, codes = _steps(root, env, tmp_path, [("linux-pytest", LINUX_FAILURE, 1)], allow)
    assert codes[0].returncode == 0
    assert "allowlisted environment-only failures" in codes[0].stderr
    assert _finalize(root, env, stage, 0, allow).returncode == 0
    receipt = _receipt(root, env, tmp_path)
    assert receipt["steps"][0]["outcome"] == "known-failure"
    assert [entry["test"] for entry in receipt["known_failures"]] == [KNOWN_TEST]
    assert receipt["allowlist_sha256"]
    summary = tmp_path / "summary.md"
    assert _verify(root, env, allow, "--summary-out", str(summary)).returncode == 0
    text = summary.read_text(encoding="utf-8")
    assert "known environment-only failure" in text
    assert f"`linux-pytest` `{KNOWN_TEST}`" in text
    assert "pr-preflight-linux-vm-env-failures-20261102" in text


def test_a_failure_outside_the_allowlist_is_never_accepted(repo, tmp_path: Path) -> None:
    root, env = repo
    allow = _write_allowlist(tmp_path)
    output = f"FAILED {KNOWN_TEST} - x\nFAILED tests/unit/test_other.py::test_real - y\n2 failed, 5 passed in 1.00s"
    stage, codes = _steps(root, env, tmp_path, [("linux-pytest", output, 1)], allow)
    assert codes[0].returncode == 1
    assert _receipt_status(root, env, tmp_path, stage, allow) == "failed"


def test_the_allowlist_is_keyed_by_step(repo, tmp_path: Path) -> None:
    root, env = repo
    allow = _write_allowlist(tmp_path)
    stage, codes = _steps(root, env, tmp_path, [("pytest", LINUX_FAILURE, 1)], allow)
    assert codes[0].returncode == 1


def test_a_failure_count_the_output_does_not_account_for_is_not_accepted(repo, tmp_path: Path) -> None:
    root, env = repo
    allow = _write_allowlist(tmp_path)
    output = f"FAILED {KNOWN_TEST} - x\n2 failed, 5 passed in 1.00s"
    _, codes = _steps(root, env, tmp_path, [("linux-pytest", output, 1)], allow)
    assert codes[0].returncode == 1


def test_a_crash_without_a_pytest_summary_is_not_accepted(repo, tmp_path: Path) -> None:
    root, env = repo
    allow = _write_allowlist(tmp_path)
    _, codes = _steps(root, env, tmp_path, [("linux-pytest", "Traceback: container died", 1)], allow)
    assert codes[0].returncode == 1


def test_removing_an_allowlist_entry_invalidates_an_existing_receipt(repo, tmp_path: Path) -> None:
    root, env = repo
    allow = _write_allowlist(tmp_path)
    stage, _ = _steps(root, env, tmp_path, [("linux-pytest", LINUX_FAILURE, 1)], allow)
    assert _finalize(root, env, stage, 0, allow).returncode == 0
    assert _verify(root, env, allow).returncode == 0
    emptied = _write_allowlist(tmp_path, {"schema": 1, "entries": []})
    done = _verify(root, env, emptied)
    assert done.returncode == 1 and "shows a failed step (linux-pytest)" in done.stderr
    assert _verify(root, env, None).returncode == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"schema": 2, "entries": []},
        {"schema": 1, "entries": [{"step": "linux-pytest", "test": "t"}]},
        {"schema": 1, "entries": [{**ALLOWLIST["entries"][0], "extra": "x"}]},
        {"schema": 1, "entries": [{**ALLOWLIST["entries"][0], "tracker": " "}]},
        {"schema": 1, "entries": [ALLOWLIST["entries"][0], ALLOWLIST["entries"][0]]},
        [],
    ],
)
def test_a_malformed_allowlist_is_refused(tmp_path: Path, payload: object) -> None:
    with pytest.raises(preflight_receipt.ReceiptError):
        preflight_receipt.load_allowlist(_write_allowlist(tmp_path, payload))


def test_an_unverified_linux_leg_is_recorded_and_rendered(repo, tmp_path: Path) -> None:
    root, env = repo
    output = "WARNING: Linux not verified -- container is not installed."
    stage, codes = _steps(root, env, tmp_path, [("linux-pytest", output, 0)], None)
    assert codes[0].returncode == 0
    assert _finalize(root, env, stage, 0, None).returncode == 0
    assert _receipt(root, env, tmp_path)["steps"][0]["outcome"] == "unverified"
    summary = tmp_path / "summary.md"
    assert _verify(root, env, None, "--summary-out", str(summary)).returncode == 0
    assert "was not verified: Linux not verified" in summary.read_text(encoding="utf-8")


def test_pytest_exit_five_is_a_pass_with_its_raw_code(repo, tmp_path: Path) -> None:
    root, env = repo
    stage = tmp_path / "stage"
    _script(root, env, "begin", "--stage", str(stage), "--base-ref", "origin/develop")
    command = [sys.executable, "-c", "import sys; print('no tests ran in 0.01s'); sys.exit(5)"]
    done = _script(root, env, "step", "--stage", str(stage), "--name", "pytest", "--ok-codes", "0,5", "--", *command)
    assert done.returncode == 5
    assert _finalize(root, env, stage, 0, None).returncode == 0
    assert _receipt(root, env, tmp_path)["steps"][0]["outcome"] == "passed"


def test_the_step_keeps_the_last_forty_lines_of_output(repo, tmp_path: Path) -> None:
    root, env = repo
    stage = tmp_path / "stage"
    _script(root, env, "begin", "--stage", str(stage), "--base-ref", "origin/develop")
    command = [sys.executable, "-c", "print('\\n'.join(f'line {i}' for i in range(100)))"]
    done = _script(root, env, "step", "--stage", str(stage), "--name", "ruff-check", "--", *command)
    assert "line 99" in done.stdout
    assert _finalize(root, env, stage, 0, None).returncode == 0
    tail = _receipt(root, env, tmp_path)["steps"][0]["output_tail"]
    assert tail[0] == "line 60" and tail[-1] == "line 99"


def _receipt_status(repo: Path, env: dict[str, str], tmp_path: Path, stage: Path, allow: Path | None) -> str:
    assert _finalize(repo, env, stage, 1, allow).returncode == 0
    return _receipt(repo, env, tmp_path)["status"]


STUB_UV = """#!/bin/sh
echo "$*" >> "$STUB_LOG"
case "$*" in
*pr_open_rest.py*)
  while [ "$#" -gt 0 ]; do
    if [ "$1" = --receipt-file ]; then cp "$2" "$STUB_RECEIPT_COPY"; fi
    shift
  done
  echo "created 7 https://github.com/BenchBox-dev/BenchBox/pull/7"
  ;;
esac
exit 0
"""


def _stub_git(real_git: str) -> str:
    return textwrap.dedent(
        f"""\
        #!/bin/sh
        if [ "$1 $2 $3 $4" = "remote get-url --push origin" ]; then echo {GITHUB_URL}; exit 0; fi
        exec {real_git} "$@"
        """
    )


@pytest.fixture
def open_world(tmp_path: Path) -> dict[str, object]:
    env = _env(tmp_path)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "develop", str(origin)], check=True, env=env)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True, env=env)
    _git(work, env, "checkout", "-q", "-b", "develop")
    hook = work / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    hook.chmod(0o755)
    (work / "scripts").mkdir()
    for name in (
        "preflight_receipt.py",
        "agent_write_preflight.sh",
        "check_primary_clone_clean.sh",
        "make_timing_shell.sh",
    ):
        shutil.copy(ROOT / "scripts" / name, work / "scripts" / name)
    (work / "_project" / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "_project" / "scripts" / "preflight_targets.py", work / "_project" / "scripts")
    shutil.copy(ROOT / "Makefile", work / "Makefile")
    shutil.copytree(ROOT / "make", work / "make")
    (work / "_project" / "decisions").mkdir(parents=True)
    (work / "_project" / "decisions" / "keep").write_text("x", encoding="utf-8")
    _git(work, env, "add", "-A")
    _git(work, env, "commit", "-q", "-m", "seed")
    _git(work, env, "push", "-q", "origin", "develop")
    _git(work, env, "checkout", "-q", "-b", "feat/receipt")
    _commit(work, env, "change.txt", "change\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "uv.log"
    copy = tmp_path / "receipt-copy.md"
    real_git = shutil.which("git")
    assert real_git is not None
    stubs = {
        "uv": STUB_UV,
        "git": _stub_git(real_git),
        "gh": "#!/bin/sh\nexit 1\n",
        "python3": f'#!/bin/sh\ncase "$1" in *local_review.py) exit 0 ;; esac\nexec {sys.executable} "$@"\n',
    }
    for name, text in stubs.items():
        (bin_dir / name).write_text(text, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "STUB_LOG": str(log),
            "STUB_RECEIPT_COPY": str(copy),
            "BENCHBOX_ALLOW_MAIN_CLONE_WRITE": "1",
        }
    )
    return {"work": work, "origin": origin, "env": env, "log": log, "copy": copy, "tmp": tmp_path}


def _pr_open(world: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "-s", "pr-open"],
        cwd=world["work"],
        env=world["env"],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _origin_branches(world: dict[str, object]) -> str:
    return _git(world["origin"], world["env"], "branch", "--list")


def _record_receipt(world: dict[str, object], steps: list[tuple[str, str, int]], status: int) -> None:
    work, env, tmp = world["work"], world["env"], world["tmp"]
    stage, _ = _steps(work, env, tmp, steps, None)
    assert _finalize(work, env, stage, status, None).returncode == 0


def test_pr_open_refuses_without_a_receipt_and_pushes_nothing(open_world) -> None:
    proc = _pr_open(open_world)
    assert proc.returncode != 0
    assert "no preflight receipt for tree" in proc.stderr
    assert "Refusing to open PR: no passing preflight receipt matches the tree being pushed" in proc.stderr
    assert "feat/receipt" not in _origin_branches(open_world)
    assert not open_world["copy"].exists()


def test_pr_open_refuses_a_stale_receipt_and_pushes_nothing(open_world) -> None:
    _record_receipt(open_world, [("pytest", "3 passed in 0.10s", 0)], 0)
    _commit(open_world["work"], open_world["env"], "later.txt", "later\n")
    proc = _pr_open(open_world)
    assert proc.returncode != 0
    assert "no preflight receipt for tree" in proc.stderr
    assert "feat/receipt" not in _origin_branches(open_world)


def test_pr_open_refuses_a_failed_receipt_and_pushes_nothing(open_world) -> None:
    _record_receipt(
        open_world,
        [("pytest", "FAILED tests/x.py::test_a - boom\n1 failed, 2 passed in 0.10s", 1)],
        1,
    )
    proc = _pr_open(open_world)
    assert proc.returncode != 0
    assert "shows a failed step (pytest)" in proc.stderr
    assert "feat/receipt" not in _origin_branches(open_world)


def test_pr_open_proceeds_on_a_matching_receipt_and_renders_it_into_the_pr_body(open_world) -> None:
    _record_receipt(open_world, [("ruff-check", "ok", 0), ("pytest", "3 passed in 0.10s", 0)], 0)
    proc = _pr_open(open_world)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "feat/receipt" in _origin_branches(open_world)
    body = open_world["copy"].read_text(encoding="utf-8")
    tree = _git(open_world["work"], open_world["env"], "rev-parse", "HEAD^{tree}")
    assert f"<!-- preflight-receipt: v1 begin tree={tree} -->" in body
    assert "`pytest` exit 0: passed (3 passed)" in body
    assert "https://github.com/BenchBox-dev/BenchBox/pull/7" in proc.stdout


def _diverge_from_develop(world: dict[str, object], branch_subject: str) -> None:
    work, env = world["work"], world["env"]
    _commit(work, env, "shared.txt", "branch\n")
    _git(work, env, "commit", "-q", "--amend", "-m", branch_subject)
    _git(work, env, "checkout", "-q", "develop")
    _commit(work, env, "shared.txt", "develop\n")
    _git(work, env, "commit", "-q", "--amend", "-m", "fix thing (#5)")
    _git(work, env, "push", "-q", "origin", "develop")
    _git(work, env, "checkout", "-q", "feat/receipt")


def test_pr_open_names_an_amended_develop_squash_commit_when_the_branch_conflicts_with_develop(open_world) -> None:
    _diverge_from_develop(open_world, "fix thing (#5)")
    proc = _pr_open(open_world)
    assert proc.returncode != 0
    assert "HEAD conflicts with origin/develop" in proc.stderr
    assert "fix thing (#5)" in proc.stderr
    assert "git commit --amend" in proc.stderr
    assert "feat/receipt" not in _origin_branches(open_world)


def test_pr_open_gives_the_plain_conflict_message_when_no_branch_commit_looks_like_a_develop_squash(open_world) -> None:
    _diverge_from_develop(open_world, "fix thing")
    proc = _pr_open(open_world)
    assert proc.returncode != 0
    assert "HEAD conflicts with origin/develop" in proc.stderr
    assert "amend" not in proc.stderr


def test_pr_open_refuses_a_title_given_only_through_the_pr_open_title_environment_variable(open_world) -> None:
    _record_receipt(open_world, [("ruff-check", "ok", 0), ("pytest", "3 passed in 0.10s", 0)], 0)
    open_world["env"]["PR_OPEN_TITLE"] = "fix(x): a title"
    proc = _pr_open(open_world)
    assert proc.returncode != 0
    assert "TITLE" in proc.stderr
    assert "feat/receipt" not in _origin_branches(open_world)


def test_pr_open_accepts_the_title_given_as_a_make_variable(open_world) -> None:
    _record_receipt(open_world, [("ruff-check", "ok", 0), ("pytest", "3 passed in 0.10s", 0)], 0)
    proc = subprocess.run(
        ["make", "-s", "pr-open", "TITLE=fix(x): a title"],
        cwd=open_world["work"],
        env=open_world["env"],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "feat/receipt" in _origin_branches(open_world)


def test_pr_open_accepts_an_allowlisted_failure_from_the_committed_allowlist_and_names_it(open_world) -> None:
    work, env, tmp = open_world["work"], open_world["env"], open_world["tmp"]
    (work / ".github").mkdir()
    allow = work / ".github" / "preflight-known-failures.json"
    allow.write_text(json.dumps(ALLOWLIST), encoding="utf-8")
    _git(work, env, "add", ".github/preflight-known-failures.json")
    _git(work, env, "commit", "-q", "-m", "allowlist")
    stage, _ = _steps(work, env, tmp, [("linux-pytest", LINUX_FAILURE, 1)], allow)
    assert _finalize(work, env, stage, 0, allow).returncode == 0
    proc = _pr_open(open_world)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    body = open_world["copy"].read_text(encoding="utf-8")
    assert "known environment-only failure" in body
    assert f"`linux-pytest` `{KNOWN_TEST}`" in body
    assert "pr-preflight-linux-vm-env-failures-20261102" in body


def test_pr_open_refuses_an_allowlisted_failure_when_the_committed_allowlist_lacks_the_entry(open_world) -> None:
    work, env, tmp = open_world["work"], open_world["env"], open_world["tmp"]
    allow = _write_allowlist(tmp)
    stage, _ = _steps(work, env, tmp, [("linux-pytest", LINUX_FAILURE, 1)], allow)
    assert _finalize(work, env, stage, 0, allow).returncode == 0
    proc = _pr_open(open_world)
    assert proc.returncode != 0
    assert "shows a failed step (linux-pytest)" in proc.stderr
    assert "feat/receipt" not in _origin_branches(open_world)


@pytest.mark.parametrize("code", [2, 3, 4, 137])
def test_a_non_failure_exit_code_is_never_accepted_as_a_known_failure(repo, tmp_path: Path, code: int) -> None:
    root, env = repo
    allow = _write_allowlist(tmp_path)
    _, codes = _steps(root, env, tmp_path, [("linux-pytest", LINUX_FAILURE, code)], allow)
    assert codes[0].returncode == code


def test_only_the_linux_step_can_be_allowlisted(tmp_path: Path) -> None:
    entry = {**ALLOWLIST["entries"][0], "step": "pytest"}
    with pytest.raises(preflight_receipt.ReceiptError, match="cannot be allowlisted"):
        preflight_receipt.load_allowlist(_write_allowlist(tmp_path, {"schema": 1, "entries": [entry]}))


def _note(root: Path, env: dict[str, str], stage: Path, **counts: int) -> None:
    for key, value in counts.items():
        assert _script(root, env, "note", "--stage", str(stage), f"{key}={value}").returncode == 0


def test_a_receipt_missing_a_step_its_selection_required_is_refused(repo, tmp_path: Path) -> None:
    root, env = repo
    stage, _ = _steps(root, env, tmp_path, [("changelog-check", "ok", 0), ("ruff-check", "ok", 0)], None)
    _note(root, env, stage, python_files=1, test_files=2, linux_files=1)
    assert _finalize(root, env, stage, 0, None).returncode == 0
    done = _verify(root, env, None)
    assert done.returncode == 1
    assert "lacks required step(s) ruff-format, pytest, linux-pytest" in done.stderr


def test_a_receipt_with_every_required_step_is_accepted(repo, tmp_path: Path) -> None:
    root, env = repo
    steps = [(name, "ok", 0) for name in ("changelog-check", "ruff-check", "ruff-format", "pytest", "linux-pytest")]
    stage, _ = _steps(root, env, tmp_path, steps, None)
    _note(root, env, stage, python_files=1, test_files=2, linux_files=1)
    assert _finalize(root, env, stage, 0, None).returncode == 0
    assert _verify(root, env, None, "--base-ref", "origin/develop").returncode == 0


def test_a_receipt_lacking_a_guard_step_the_changed_paths_require_is_refused(repo, tmp_path: Path) -> None:
    root, env = repo
    stage, _ = _steps(root, env, tmp_path, [("changelog-check", "ok", 0)], None, guards=False)
    assert _finalize(root, env, stage, 0, None).returncode == 0
    done = _verify(root, env, None)
    assert done.returncode == 1
    assert "lacks required step(s) docs-reference-inventory, project-references" in done.stderr


def test_a_receipt_lacks_the_ownership_guard_when_docs_changed_and_is_accepted_with_it(repo, tmp_path: Path) -> None:
    root, env = repo
    (root / "docs").mkdir()
    _commit(root, env, "docs/x.md", "# x\n")
    stage, _ = _steps(root, env, tmp_path, [("changelog-check", "ok", 0)], None)
    assert _finalize(root, env, stage, 0, None).returncode == 0
    refused = _verify(root, env, None, "--base-ref", "origin/develop")
    assert refused.returncode == 1
    assert "lacks required step(s) docs-ownership" in refused.stderr
    (tmp_path / "stage").unlink()
    stage, _ = _steps(root, env, tmp_path, [("changelog-check", "ok", 0), ("docs-ownership", "ok", 0)], None)
    assert _finalize(root, env, stage, 0, None).returncode == 0
    assert _verify(root, env, None, "--base-ref", "origin/develop").returncode == 0


def test_a_receipt_written_before_guards_were_recorded_is_refused_with_its_own_message(repo, tmp_path: Path) -> None:
    root, env = repo
    stage, _ = _steps(root, env, tmp_path, [("changelog-check", "ok", 0)], None)
    assert _finalize(root, env, stage, 0, None).returncode == 0
    receipt_file = next((tmp_path / "receipts").glob("*.json"))
    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    receipt["schema"] = 1
    receipt_file.write_text(json.dumps(receipt), encoding="utf-8")
    done = _verify(root, env, None)
    assert done.returncode == 1
    assert "predates guard-step recording" in done.stderr
    assert "rerun `make pr-preflight`" in done.stderr


def test_a_receipt_without_a_selection_is_refused(repo, tmp_path: Path) -> None:
    root, env = repo
    stage, _ = _steps(root, env, tmp_path, [("changelog-check", "ok", 0)], None)
    assert _finalize(root, env, stage, 0, None).returncode == 0
    receipt_file = next((tmp_path / "receipts").glob("*.json"))
    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    del receipt["selection"]
    receipt_file.write_text(json.dumps(receipt), encoding="utf-8")
    done = _verify(root, env, None)
    assert done.returncode == 1 and "records no step selection" in done.stderr


def test_a_receipt_selected_against_head_does_not_cover_the_branch(repo, tmp_path: Path) -> None:
    root, env = repo
    stage = tmp_path / "stage"
    assert _script(root, env, "begin", "--stage", str(stage), "--base-ref", "HEAD").returncode == 0
    command = [sys.executable, "-c", "print('ok')"]
    _script(root, env, "step", "--stage", str(stage), "--name", "changelog-check", "--", *command)
    assert _finalize(root, env, stage, 0, None).returncode == 0
    assert _verify(root, env, None).returncode == 0
    done = _verify(root, env, None, "--base-ref", "origin/develop")
    assert done.returncode == 1
    assert "does not cover the changes" in done.stderr


def test_a_receipt_selected_against_an_older_base_still_covers_the_branch(repo, tmp_path: Path) -> None:
    root, env = repo
    stage, _ = _steps(root, env, tmp_path, [("changelog-check", "ok", 0)], None)
    assert _finalize(root, env, stage, 0, None).returncode == 0
    _git(root, env, "update-ref", "refs/remotes/origin/develop", "HEAD")
    assert _verify(root, env, None, "--base-ref", "origin/develop").returncode == 0


def test_verify_binds_to_the_named_head_not_to_the_current_head(repo, tmp_path: Path) -> None:
    root, env = repo
    stage, _ = _steps(root, env, tmp_path, [("pytest", "1 passed in 0.10s", 0)], None)
    assert _finalize(root, env, stage, 0, None).returncode == 0
    reviewed = _git(root, env, "rev-parse", "HEAD")
    _commit(root, env, "later.txt", "later\n")
    assert _verify(root, env, None, "--head", reviewed, "--base-ref", "origin/develop").returncode == 0
    assert _verify(root, env, None, "--head", "HEAD").returncode == 1


def test_pr_open_resolves_the_pushed_commit_once_before_the_receipt_check() -> None:
    recipe = (ROOT / "make" / "pr-landing.mk").read_text(encoding="utf-8")
    recipe = recipe[recipe.index("\npr-open:\n") :]
    resolved = "REVIEWED=$$(git rev-parse HEAD)"
    assert recipe.count(resolved) == 1
    assert recipe.index(resolved) < recipe.index("preflight_receipt.py verify")
    assert '--head "$$REVIEWED"' in recipe
    assert recipe.index("preflight_receipt.py verify") < recipe.index('git push origin "$$REVIEWED:')
