from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from path_filter_decision import (
    classify_paths,
    git_changed_paths,
    load_rules,
    load_rules_from_base_ref,
    pattern_matches,
    write_github_output,
)

from tests.utilities.posix_shell import run_posix_shell, skip_without_posix_shell

pytestmark = [pytest.mark.unit, pytest.mark.fast]


REPO_RULES = Path(__file__).resolve().parents[3] / ".github" / "path-filters.yml"


@pytest.fixture(scope="module")
def rules() -> dict[str, list[str]]:
    return load_rules(REPO_RULES)


def test_pattern_matches_root_glob() -> None:
    assert pattern_matches("README.md", "*.md")
    assert not pattern_matches("docs/README.md", "*.md")
    assert not pattern_matches("Makefile", "*.md")


def test_pattern_matches_directory_recursive() -> None:
    assert pattern_matches("docs/foo.md", "docs/**")
    assert pattern_matches("docs/sub/foo.md", "docs/**")
    assert not pattern_matches("doc.md", "docs/**")


def test_safe_content_only_for_todo(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(["_project/blind-spots/foo.md"], rules)
    assert decision["safe_content_only"] is True
    assert decision["needs_code_ci"] is False
    assert decision["content_guard_needed"] is True


def test_code_change_triggers_code_ci(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(["benchbox/cli/run.py"], rules)
    assert decision["safe_content_only"] is False
    assert decision["needs_code_ci"] is True


def test_unknown_path_fails_closed(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(["quality/example.txt"], rules)
    assert decision["safe_content_only"] is False
    assert decision["needs_code_ci"] is True
    assert "quality/example.txt" in decision["unknown_paths"]


def test_mixed_safe_and_code_takes_full_ci(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(["docs/development/foo.md", "benchbox/cli/run.py"], rules)
    assert decision["safe_content_only"] is False
    assert decision["needs_code_ci"] is True


def test_empty_diff_routes_to_full_ci(rules: dict[str, list[str]]) -> None:
    decision = classify_paths([], rules)
    assert decision["safe_content_only"] is False
    assert decision["needs_code_ci"] is True


def test_skill_integrity_only_skips_product_ci(rules: dict[str, list[str]]) -> None:
    paths = [
        ".claude/skills/todo/SKILL.md",
        ".claude/skills/todo/references/batch.md",
        "skill-sync.conf",
        "tools/skill-sync",
    ]

    decision = classify_paths(paths, rules)

    assert decision["skill_integrity_needed"] is True
    assert decision["skill_integrity_only"] is True
    assert decision["needs_code_ci"] is False
    assert decision["safe_content_only"] is False
    assert decision["unknown_paths"] == []


def test_skill_integrity_plus_safe_content_runs_both_narrow_lanes(rules: dict[str, list[str]]) -> None:
    decision = classify_paths([".claude/skills/todo/SKILL.md", "docs/guide.md"], rules)

    assert decision["skill_integrity_only"] is True
    assert decision["content_guard_needed"] is True
    assert decision["needs_code_ci"] is False


def test_skill_integrity_plus_product_code_runs_both_required_lanes(rules: dict[str, list[str]]) -> None:
    decision = classify_paths([".claude/skills/todo/SKILL.md", "benchbox/cli/run.py"], rules)

    assert decision["skill_integrity_needed"] is True
    assert decision["skill_integrity_only"] is False
    assert decision["needs_code_ci"] is True


def test_structural_manifest_decision_forces_product_ci(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(
        ["skill-sync.conf", "tools/skill-sync"],
        rules,
        forced_code_paths=["skill-sync.conf"],
        manifest_decision_reason="manifest_structural_change",
    )

    assert decision["skill_integrity_needed"] is True
    assert decision["skill_integrity_only"] is False
    assert decision["needs_code_ci"] is True
    assert decision["forced_code_paths"] == ["skill-sync.conf"]
    assert decision["unknown_paths"] == []


def test_path_filter_rules_self_edit_is_explicit_product_code(rules: dict[str, list[str]]) -> None:
    decision = classify_paths([".github/path-filters.yml"], rules)

    assert decision["needs_code_ci"] is True
    assert decision["unknown_paths"] == []


FRAMING_TRIGGER_PATHS = [
    "tests/utilities/session_isolation.py",
    "tests/conftest.py",
    "tests/unit/core/conftest.py",
    "tests/plugins/hook.py",
    "tests/unit/plugins/hook.py",
    "tests/fixtures/utility_fixtures.py",
    "_benchbox_pytest_xdist_safety.py",
    "scripts/local_validation.py",
    "tests/duration_policy.py",
    "pytest.ini",
    "tests/unit/core/tpch/test_tpch_dbgen_framing_binaries.py",
    "benchbox/__init__.py",
    "benchbox/_binaries/tpc-h/windows-x86_64/dbgen.exe",
]


@pytest.mark.parametrize("path", FRAMING_TRIGGER_PATHS)
def test_framing_inputs_trigger_the_cross_platform_framing_job(rules: dict[str, list[str]], path: str) -> None:
    decision = classify_paths([path], rules)

    assert decision["framing_needed"] is True
    assert decision["framing_paths"] == [path]
    assert decision["needs_code_ci"] is True


@pytest.mark.parametrize(
    "path",
    [
        "benchbox/cli/run.py",
        "tests/unit/core/test_runner.py",
        "tests/fixtures/golden/q1.sql",
        "docs/development/testing.md",
        "benchbox/_binaries/tpc-ds/linux-x86_64/dsdgen",
    ],
)
def test_unrelated_changes_skip_the_cross_platform_framing_job(rules: dict[str, list[str]], path: str) -> None:
    decision = classify_paths([path], rules)

    assert decision["framing_needed"] is False
    assert decision["framing_paths"] == []


def test_every_registered_pytest_plugin_triggers_the_framing_job(rules: dict[str, list[str]]) -> None:
    repo_root = REPO_RULES.parents[1]
    modules = re.findall(r"^\s*-p\s+(?!no:)(\S+)", (repo_root / "pytest.ini").read_text(encoding="utf-8"), re.M)
    conftest = (repo_root / "tests" / "conftest.py").read_text(encoding="utf-8")
    block = re.search(r"pytest_plugins\s*=\s*\[(.*?)\]", conftest, re.S)
    assert block is not None
    modules += re.findall(r'"([\w.]+)"', block.group(1))

    assert modules
    for module in modules:
        path = f"{module.replace('.', '/')}.py"
        assert (repo_root / path).is_file(), path
        assert classify_paths([path], rules)["framing_needed"] is True, path


def test_first_party_imports_of_pytest_startup_files_trigger_the_framing_job(rules: dict[str, list[str]]) -> None:
    repo_root = REPO_RULES.parents[1]
    entry_points = ["tests/conftest.py", "tests/utilities/session_isolation.py"]
    imported: set[str] = set()
    for entry in entry_points:
        tree = ast.parse((repo_root / entry).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)

    first_party = sorted(
        module for module in imported if module.split(".")[0] in {"scripts", "tests"} and module != "tests"
    )
    assert "scripts.local_validation" in first_party
    for module in first_party:
        path = f"{module.replace('.', '/')}.py"
        assert (repo_root / path).is_file(), path
        assert classify_paths([path], rules)["framing_needed"] is True, path


def test_github_output_exposes_framing_needed(rules: dict[str, list[str]], tmp_path: Path) -> None:
    output = tmp_path / "github-output.txt"

    write_github_output(output, classify_paths(["tests/utilities/paths.py"], rules))
    assert "framing-needed=true\n" in output.read_text(encoding="utf-8")

    skipped = tmp_path / "skipped-output.txt"
    write_github_output(skipped, classify_paths(["benchbox/cli/run.py"], rules))
    assert "framing-needed=false\n" in skipped.read_text(encoding="utf-8")


def test_github_output_exposes_skill_integrity_lane(rules: dict[str, list[str]], tmp_path: Path) -> None:
    decision = classify_paths([".claude/skills/todo/SKILL.md"], rules)
    output = tmp_path / "github-output.txt"

    write_github_output(output, decision)

    text = output.read_text(encoding="utf-8")
    assert "skill-integrity-needed=true\n" in text
    assert "skill-integrity-only=true\n" in text
    assert "needs-code-ci=false\n" in text


def test_docs_markdown_is_safe(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(["docs/development/run-lifecycle-map.md"], rules)
    assert decision["safe_content_only"] is True
    assert decision["needs_code_ci"] is False


def test_dependency_audit_inventory_runs_code_ci(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(["docs/internal/dependency-audit-raw.md"], rules)

    assert decision["needs_code_ci"] is True
    assert decision["safe_content_only"] is False


def test_docs_rst_is_safe(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(["docs/api/index.rst"], rules)
    assert decision["safe_content_only"] is True
    assert decision["needs_code_ci"] is False


@pytest.mark.parametrize(
    "path",
    [
        "docs/publish-allowlist.txt",
        "docs/publish-exclusions.txt",
        "docs/agent/README.md",
        "docs/README.md",
        "docs/internal/release-guide.md",
        "docs/internal/nightly-t3.md",
    ],
)
def test_docs_control_files_run_code_ci(rules: dict[str, list[str]], path: str) -> None:
    assert (REPO_RULES.parents[1] / path).is_file()
    decision = classify_paths([path], rules)
    assert decision["needs_code_ci"] is True
    assert decision["safe_content_only"] is False


@pytest.mark.parametrize("path", ["docs/example.py", "docs/assets/site.css", "docs/assets/page.html"])
def test_docs_code_assets_run_code_ci(rules: dict[str, list[str]], path: str) -> None:
    decision = classify_paths([path], rules)
    assert decision["needs_code_ci"] is True
    assert decision["safe_content_only"] is False


def test_classify_does_not_treat_deleted_code_as_safe(
    rules: dict[str, list[str]],
) -> None:
    decision = classify_paths(["benchbox/_obsolete_module.py", "docs/development/foo.md"], rules)
    assert decision["safe_content_only"] is False
    assert decision["needs_code_ci"] is True


def test_safe_content_only_when_truly_content(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(
        ["_project/blind-spots/foo.md", "docs/development/foo.md", "docs/index.md"],
        rules,
    )
    assert decision["safe_content_only"] is True
    assert decision["needs_code_ci"] is False


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout


def test_event_base_sha_stays_authoritative_when_origin_develop_moves(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=develop", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    manifest = (REPO_RULES.parents[1] / "skill-sync.conf").read_text(encoding="utf-8")
    (repo / "skill-sync.conf").write_text(manifest, encoding="utf-8")
    _git(repo, "add", "skill-sync.conf")
    _git(repo, "commit", "-m", "base", "-q")
    event_base = _git(repo, "rev-parse", "HEAD").strip()

    _git(repo, "checkout", "-b", "feature", "-q")
    first_rev = re.search(r"^rev *= *([0-9a-f]{40})", manifest, re.MULTILINE).group(1)
    feature_manifest = manifest.replace(first_rev, "a" * 40, 1)
    (repo / "skill-sync.conf").write_text(feature_manifest, encoding="utf-8")
    _git(repo, "add", "skill-sync.conf")
    _git(repo, "commit", "-m", "ref only", "-q")

    _git(repo, "checkout", "-b", "moving-base", event_base, "-q")
    (repo / "README.md").write_text("develop moved\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "move develop", "-q")
    moved_base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "update-ref", "refs/remotes/origin/develop", moved_base)
    _git(repo, "checkout", "feature", "-q")

    changed = repo / "changed.txt"
    changed.write_text("skill-sync.conf\n", encoding="utf-8")
    script = REPO_RULES.parents[1] / "scripts" / "path_filter_decision.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--rules",
            str(REPO_RULES),
            "--base-ref",
            event_base,
            "--changed-file",
            str(changed),
        ],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    )
    decision = json.loads(result.stdout)

    assert decision["manifest_base_sha"] == event_base
    assert decision["manifest_base_sha"] != moved_base
    assert decision["manifest_decision_reason"] == "approved_ref_only_change"
    assert decision["skill_integrity_only"] is True


def test_git_changed_paths_includes_deletions(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "benchbox").mkdir()
    (repo / "benchbox" / "old.py").write_text("x = 1\n")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "benchbox/old.py", "README.md")
    _git(repo, "commit", "-m", "init", "-q")
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-b", "feature", "-q")
    (repo / "benchbox" / "old.py").unlink()
    (repo / "README.md").write_text("hello world\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "remove old, edit readme", "-q")

    cwd = Path.cwd()
    try:
        os.chdir(repo)
        changed = git_changed_paths(base)
    finally:
        os.chdir(cwd)

    assert "benchbox/old.py" in changed
    assert "README.md" in changed


BASE_COPY_RULES = "safe-content:\n  - 'docs/**'\ncontent-guard:\n  - 'docs/**'\ncode-ci:\n  - 'benchbox/**'\n"
REROUTED_CHECKOUT_RULES = "safe-content:\n  - 'benchbox/**'\ncontent-guard:\n  - 'docs/**'\ncode-ci:\n  - 'docs/**'\n"


def _rules_repo_with_rerouted_checkout(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "rules-repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    rules_file = repo / ".github" / "path-filters.yml"
    rules_file.parent.mkdir(parents=True, exist_ok=True)
    rules_file.write_text(BASE_COPY_RULES, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base rules", "-q")
    base = _git(repo, "rev-parse", "HEAD").strip()
    rules_file.write_text(REROUTED_CHECKOUT_RULES, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "reroute attempt", "-q")
    return repo, base


def test_rules_base_ref_reads_the_base_copy_not_the_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, base = _rules_repo_with_rerouted_checkout(tmp_path)
    monkeypatch.chdir(repo)
    base_rules = load_rules_from_base_ref(base, Path(".github/path-filters.yml"))
    checkout_rules = load_rules(Path(".github/path-filters.yml"))
    assert classify_paths(["benchbox/core/x.py"], base_rules)["needs_code_ci"] is True
    assert classify_paths(["benchbox/core/x.py"], checkout_rules)["needs_code_ci"] is False


def test_rules_base_ref_cli_evaluates_the_base_copy(tmp_path: Path) -> None:
    repo, base = _rules_repo_with_rerouted_checkout(tmp_path)
    changed = repo / "changed.txt"
    changed.write_text("benchbox/core/x.py\n", encoding="utf-8")
    script = str(REPO_RULES.parents[1] / "scripts" / "path_filter_decision.py")
    pr_only = subprocess.run(
        [sys.executable, script, "--rules", ".github/path-filters.yml", "--changed-file", str(changed)],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    )
    assert json.loads(pr_only.stdout)["needs_code_ci"] is False
    pinned = subprocess.run(
        [
            sys.executable,
            script,
            "--rules",
            ".github/path-filters.yml",
            "--changed-file",
            str(changed),
            "--rules-base-ref",
            base,
        ],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    )
    assert json.loads(pinned.stdout)["needs_code_ci"] is True


def test_rules_base_ref_missing_ref_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _base = _rules_repo_with_rerouted_checkout(tmp_path)
    monkeypatch.chdir(repo)
    with pytest.raises(ValueError, match="cannot read base-ref copy"):
        load_rules_from_base_ref("0" * 40, Path(".github/path-filters.yml"))
