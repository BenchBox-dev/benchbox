from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import heavy_tier_needed as heavy

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[3]
POLICY_FILES = (
    "_project/scripts/soundness_paths.py",
    ".github/soundness-paths.txt",
)


HELPER, MANIFEST = 0, 1


@pytest.fixture
def policy_sources() -> dict[str, str]:
    return {path: (REPO_ROOT / path).read_text(encoding="utf-8") for path in POLICY_FILES}


def _decision(paths: list[str]) -> dict:
    return {"needs_code_ci": True, "packaging_needed": False, "changed_paths": paths}


def _classify(paths: list[str], base: dict[str, str], pr: dict[str, str]) -> dict:
    return heavy.heavy_needed(
        _decision(paths),
        "base",
        REPO_ROOT,
        read_base=lambda *_: base,
        read_pr=lambda *_: pr,
    )


def test_real_base_policy_does_not_expand_an_ordinary_code_change(
    tmp_path: Path, policy_sources: dict[str, str]
) -> None:
    repo, base_head, _pr_head = _snapshot_repo(tmp_path, policy_sources)
    assert subprocess.check_output(["git", "-C", str(repo), "remote"], text=True) == ""
    result = heavy.heavy_needed(_decision(["benchbox/core/platform_registry.py"]), base_head, repo)
    assert result["heavy_needed"] is False, result["reason"]


@pytest.mark.parametrize(
    "base_rule,pr_rule,expected", [(True, False, True), (False, True, True), (False, False, False)]
)
def test_policy_union_keeps_base_removals_and_pr_additions(
    policy_sources: dict[str, str], base_rule: bool, pr_rule: bool, expected: bool
) -> None:
    manifest = policy_sources[POLICY_FILES[MANIFEST]].replace("file\tscripts/pr_arm.py\n", "")
    base = {**policy_sources, POLICY_FILES[MANIFEST]: manifest + ("file\tscripts/pr_arm.py\n" if base_rule else "")}
    pr = {**policy_sources, POLICY_FILES[MANIFEST]: manifest + ("file\tscripts/pr_arm.py\n" if pr_rule else "")}
    result = _classify(["scripts/pr_arm.py"], base, pr)
    assert result["heavy_needed"] is expected, result["reason"]


def test_ambient_imports_and_manifest_cannot_replace_either_policy(
    policy_sources: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "soundness_paths.py").write_text("raise RuntimeError('ambient module used')\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setenv("SOUNDNESS_PATH_MANIFEST", str(tmp_path / "absent"))
    monkeypatch.setitem(sys.modules, "soundness_paths", object())
    assert _classify(["scripts/pr_arm.py"], policy_sources, policy_sources)["heavy_needed"] is True
    result = _classify(["ordinary.py"], policy_sources, policy_sources)
    assert result["heavy_needed"] is False, result["reason"]


def test_installed_startup_hook_cannot_replace_the_snapshot_helper(
    policy_sources: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = tmp_path / "interpreter"
    subprocess.run(["uv", "venv", "--python", sys.executable, str(environment)], check=True, capture_output=True)
    if os.name == "nt":
        interpreter = environment / "Scripts" / "python.exe"
        site_packages = environment / "Lib" / "site-packages"
    else:
        interpreter = environment / "bin" / "python"
        site_packages = (
            environment / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
        )
    original = policy_sources[POLICY_FILES[HELPER]]
    hostile = original.replace("return any(is_soundness_path(path) for path in paths)", "return False")
    assert hostile != original
    (site_packages / "soundness_paths.py").write_text(hostile, encoding="utf-8")
    (site_packages / "policy-startup.pth").write_text("import soundness_paths\n", encoding="utf-8")
    pr = {
        **policy_sources,
        POLICY_FILES[MANIFEST]: policy_sources[POLICY_FILES[MANIFEST]].replace("file\tscripts/pr_arm.py\n", ""),
    }
    monkeypatch.setattr(heavy.sys, "executable", str(interpreter))
    result = _classify(["scripts/pr_arm.py"], policy_sources, pr)
    assert result["heavy_needed"] is True, result["reason"]
    assert "soundness path touched" in result["reason"]


@pytest.mark.parametrize("present", [(HELPER,), (MANIFEST,)])
@pytest.mark.parametrize("bad_copy", ["base", "pr"])
def test_partial_snapshot_cannot_emit_a_valid_false_verdict(
    policy_sources: dict[str, str], present: tuple[int, ...], bad_copy: str
) -> None:
    partial = {POLICY_FILES[index]: policy_sources[POLICY_FILES[index]] for index in present}
    result = _classify(
        ["ordinary.py"],
        partial if bad_copy == "base" else policy_sources,
        partial if bad_copy == "pr" else policy_sources,
    )
    assert result["heavy_needed"] is True
    assert "invalid file set" in result["reason"]


def _snapshot_repo(tmp_path: Path, sources: dict[str, str] | None = None) -> tuple[Path, str, str]:
    repo = tmp_path / "policy-repo"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    git("config", "commit.gpgsign", "false")
    heads = []
    for label in ("base", "pr"):
        for relative in POLICY_FILES:
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(sources[relative] if sources is not None else f"{label}:{relative}\n", encoding="utf-8")
        git("add", "--", *POLICY_FILES)
        git("commit", "--allow-empty", "-m", label)
        heads.append(git("rev-parse", "HEAD"))
    git("branch", "policy-base", heads[0])
    return repo, heads[0], heads[1]


def test_base_reader_pins_all_files_before_a_ref_moves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _, pr_head = _snapshot_repo(tmp_path)
    for relative in POLICY_FILES:
        (repo / relative).write_text("dirty worktree contents\n", encoding="utf-8")
    original = subprocess.check_output

    def read_and_move(cmd: list[str], *, text: bool, stderr: int) -> str:
        assert text is True
        result = original(cmd, text=True, stderr=stderr)
        if "rev-parse" in cmd:
            subprocess.run(["git", "-C", str(repo), "update-ref", "refs/heads/policy-base", pr_head], check=True)
        return result

    monkeypatch.setattr(heavy.subprocess, "check_output", read_and_move)
    sources = heavy._read_base_copy("policy-base", repo)
    assert sources == {relative: f"base:{relative}\n" for relative in POLICY_FILES}


def test_base_reader_ignores_git_replacement_trees(tmp_path: Path) -> None:
    repo, base_head, pr_head = _snapshot_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "replace", base_head, pr_head], check=True)
    sources = heavy._read_base_copy("policy-base", repo)
    assert sources == {relative: f"base:{relative}\n" for relative in POLICY_FILES}


@pytest.mark.parametrize("bad_copy", ["base", "pr"])
@pytest.mark.parametrize(
    "defect",
    ["missing-helper", "missing-manifest", "invalid-manifest", "bad-output", "execution-error"],
)
def test_invalid_policy_copies_fail_closed(policy_sources: dict[str, str], bad_copy: str, defect: str) -> None:
    invalid = dict(policy_sources)
    if defect.startswith("missing-"):
        invalid.pop(POLICY_FILES[{"missing-helper": HELPER, "missing-manifest": MANIFEST}[defect]])
    elif defect == "invalid-manifest":
        invalid[POLICY_FILES[MANIFEST]] = "not a rule\n"
    elif defect == "bad-output":
        invalid[POLICY_FILES[HELPER]] = "print('soundness_path=false\\nsoundness_path=true')\n"
    else:
        invalid[POLICY_FILES[HELPER]] = "raise RuntimeError('broken policy')\n"
    result = _classify(
        ["ordinary.py"],
        invalid if bad_copy == "base" else policy_sources,
        invalid if bad_copy == "pr" else policy_sources,
    )
    assert result["heavy_needed"] is True
    assert "failed closed" in result["reason"]


def test_unreadable_base_ref_fails_closed() -> None:
    result = heavy.heavy_needed(_decision(["ordinary.py"]), "refs/heads/not-present", REPO_ROOT)
    assert result["heavy_needed"] is True
    assert "failed closed" in result["reason"]


@pytest.mark.parametrize(
    "raw",
    [
        "null",
        "[]",
        "not json",
        "{}",
        '{"needs_code_ci": null, "packaging_needed": false, "changed_paths": []}',
        '{"needs_code_ci": 0, "packaging_needed": false, "changed_paths": []}',
        '{"needs_code_ci": false, "packaging_needed": null, "changed_paths": []}',
        '{"needs_code_ci": false, "changed_paths": []}',
        '{"needs_code_ci": false, "packaging_needed": false}',
        '{"needs_code_ci": false, "packaging_needed": false, "changed_paths": [42]}',
        '{"needs_code_ci": false, "packaging_needed": false, "changed_paths": [""]}',
    ],
)
def test_invalid_cli_decisions_publish_the_safe_direction(tmp_path: Path, raw: str) -> None:
    decision = tmp_path / "decision.json"
    decision.write_text(raw, encoding="utf-8")
    output = tmp_path / "github-output"
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/heavy_tier_needed.py"),
            "--decision-in",
            str(decision),
            "--github-output",
            str(output),
        ],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text().strip() == "heavy-needed=true"


def test_packaging_carve_out_and_non_code_routing_preserve_the_expected_tier() -> None:
    decision = {**_decision([]), "packaging_needed": True}
    assert heavy.heavy_needed(decision, "absent", REPO_ROOT)["heavy_needed"] is True
    assert heavy.heavy_needed({**_decision([]), "needs_code_ci": False}, "absent", REPO_ROOT)["heavy_needed"] is False
