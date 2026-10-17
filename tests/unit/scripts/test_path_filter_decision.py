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


def test_explorer_vitest_group_covers_the_full_contract(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(
        [
            "results-explorer/src/pages/Home.tsx",
            "results-explorer/package-lock.json",
            "_project/scripts/explorer_pipeline/contract.py",
            "results-data/corpus-inventory.json",
            "scripts/generate_corpus_inventory.py",
            ".github/workflows/site-docs.yml",
        ],
        rules,
    )

    assert decision["explorer_vitest_needed"] is True
    assert set(decision["explorer_vitest_paths"]) == {
        "results-explorer/src/pages/Home.tsx",
        "results-explorer/package-lock.json",
        "_project/scripts/explorer_pipeline/contract.py",
        "results-data/corpus-inventory.json",
        "scripts/generate_corpus_inventory.py",
        ".github/workflows/site-docs.yml",
    }


def test_unrelated_python_change_skips_explorer_vitest(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(["benchbox/cli/run.py"], rules)
    assert decision["explorer_vitest_needed"] is False
    assert decision["explorer_vitest_paths"] == []


def test_explorer_paths_only_match_results_explorer_source(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(
        [
            "results-explorer/src/pages/Home.tsx",
            "results-explorer/e2e/home.spec.ts",
            "_project/scripts/explorer_pipeline/contract.py",
        ],
        rules,
    )

    assert decision["explorer_paths_needed"] is True
    assert decision["explorer_paths"] == ["results-explorer/src/pages/Home.tsx"]
    assert decision["explorer_tokens_needed"] is True
    assert decision["explorer_tokens_paths"] == ["results-explorer/src/pages/Home.tsx"]


def test_unrelated_python_change_skips_explorer_paths(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(["benchbox/cli/run.py"], rules)

    assert decision["explorer_paths_needed"] is False
    assert decision["explorer_paths"] == []


def test_public_site_theme_inputs_trigger_their_dedicated_gate(rules: dict[str, list[str]]) -> None:
    paths = [
        "landing/shared/header.html",
        "landing/style.css",
        "docs/_static/custom.css",
        "docs/_templates/page.html",
        "results-explorer/index.html",
    ]

    decision = classify_paths(paths, rules)

    assert decision["site_theme_needed"] is True
    assert decision["site_theme_paths"] == paths
    assert decision["explorer_paths_needed"] is False


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


def test_github_output_exposes_explorer_paths_alias(rules: dict[str, list[str]], tmp_path: Path) -> None:
    decision = classify_paths(["results-explorer/src/pages/Home.tsx"], rules)
    output = tmp_path / "github-output.txt"

    write_github_output(output, decision)

    text = output.read_text(encoding="utf-8")
    assert "explorer-paths-needed=true\n" in text


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


def test_docs_conf_py_runs_code_ci(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(["docs/conf.py"], rules)
    assert decision["needs_code_ci"] is True
    assert decision["safe_content_only"] is False


def test_docs_extension_python_runs_code_ci(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(["docs/_extensions/sphinx_tags_fix.py"], rules)
    assert decision["needs_code_ci"] is True


def test_docs_static_python_runs_code_ci(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(["docs/_static/pygments_cobalt2.py"], rules)
    assert decision["needs_code_ci"] is True


def test_docs_javascript_runs_code_ci(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(["docs/_static/collapsible-nav.js"], rules)
    assert decision["needs_code_ci"] is True


def test_docs_css_runs_code_ci(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(["docs/_static/custom.css"], rules)
    assert decision["needs_code_ci"] is True


def test_docs_template_html_runs_code_ci(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(["docs/_templates/page.html"], rules)
    assert decision["needs_code_ci"] is True


def test_docs_makefile_runs_code_ci(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(["docs/Makefile"], rules)
    assert decision["needs_code_ci"] is True


def test_classify_does_not_treat_deleted_code_as_safe(
    rules: dict[str, list[str]],
) -> None:
    decision = classify_paths(["benchbox/_obsolete_module.py", "docs/development/foo.md"], rules)
    assert decision["safe_content_only"] is False
    assert decision["needs_code_ci"] is True


def test_safe_content_only_when_truly_content(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(
        ["_project/blind-spots/foo.md", "docs/development/foo.md", "README.md"],
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


SITE_DOCS_WORKFLOW = REPO_RULES.parent / "workflows" / "site-docs.yml"
SITE_DOCS_UNIT_JOBS = ("explorer", "docs", "landing")
SITE_BUILD_INPUTS = [
    "website/src/pages/index.astro",
    "website/package-lock.json",
    "docs/usage/getting-started.md",
    "landing/hero.png",
    "results-explorer/src/App.tsx",
    "results-data/corpus-inventory.json",
    "scripts/generate_query_docs.py",
    "benchbox/core/query_catalog.py",
    "benchbox/__init__.py",
    "benchbox/utils/dialect_utils.py",
    "_binaries/tpc-h/linux-x86_64/dbgen",
    "benchbox/core/tpch/benchmark.py",
    "_sources/tpc-h/dbgen/queries/1.sql",
    "scripts/publication/check_artifact_privacy.py",
    "Makefile",
    "make/documentation.mk",
    "pyproject.toml",
    "uv.lock",
    ".github/workflows/site-docs.yml",
]


def _site_docs_jobs() -> dict[str, dict]:
    return yaml.safe_load(SITE_DOCS_WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _needs(job: dict) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def test_site_build_inputs_trigger_the_site_build_gate(rules: dict[str, list[str]]) -> None:
    for path in SITE_BUILD_INPUTS:
        decision = classify_paths([path], rules)

        assert decision["site_needed"] is True
        assert decision["site_paths"] == [path]


def test_core_workflow_edit_skips_the_site_gates(rules: dict[str, list[str]]) -> None:
    decision = classify_paths([".github/workflows/ci.yml"], rules)

    assert decision["site_needed"] is False
    assert decision["site_parity_needed"] is False
    assert decision["explorer_vitest_needed"] is False


def test_unrelated_change_skips_the_site_build_gate(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(["tests/unit/test_x.py", "examples/getting_started/run.py"], rules)

    assert decision["site_needed"] is False
    assert decision["site_paths"] == []


@pytest.mark.parametrize(
    "path", ["docs/usage/getting-started.md", "docs/conf.py", "website/src/pages/index.astro", "scripts/site_parity.py"]
)
def test_docs_and_website_changes_trigger_the_parity_gate(rules: dict[str, list[str]], path: str) -> None:
    decision = classify_paths([path], rules)

    assert decision["site_parity_needed"] is True
    assert decision["site_parity_paths"] == [path]


def test_unrelated_change_skips_the_parity_gate(rules: dict[str, list[str]]) -> None:
    assert classify_paths(["tests/unit/test_x.py"], rules)["site_parity_needed"] is False


def test_website_sources_are_explicit_product_code(rules: dict[str, list[str]]) -> None:
    decision = classify_paths(["website/src/pages/index.astro", "website/package.json"], rules)

    assert decision["needs_code_ci"] is True
    assert decision["unknown_paths"] == []
    assert decision["safe_content_only"] is False


def test_github_output_exposes_site_needed(rules: dict[str, list[str]], tmp_path: Path) -> None:
    output = tmp_path / "github-output.txt"
    write_github_output(output, classify_paths(["website/astro.config.ts"], rules))
    assert "site-needed=true\n" in output.read_text(encoding="utf-8")

    skipped = tmp_path / "skipped-output.txt"
    write_github_output(skipped, classify_paths(["tests/unit/test_x.py"], rules))
    assert "site-needed=false\n" in skipped.read_text(encoding="utf-8")


def test_site_build_job_is_gated_on_the_site_filter_and_feeds_the_landing_unit() -> None:
    jobs = _site_docs_jobs()
    landing_run = "\n".join(step.get("run", "") for step in jobs["landing"]["steps"])

    assert jobs["site-build"]["if"] == "${{ needs.site-paths.outputs.site-needed == 'true' }}"
    assert "site-needed" in jobs["site-paths"]["outputs"]
    assert tuple(unit for unit in SITE_DOCS_UNIT_JOBS if unit in jobs) == SITE_DOCS_UNIT_JOBS
    assert [name for name, job in jobs.items() if "site-build" in _needs(job)] == ["landing"]
    assert "--expect site-build=${{ needs.site-paths.outputs.site-needed == 'true' }}" in landing_run


def test_site_jobs_run_the_site_gates_on_node_22_only() -> None:
    jobs = _site_docs_jobs()
    steps = jobs["site-build"]["steps"]
    versions = {
        job_name: [str(step["with"]["node-version"]) for step in job["steps"] if "setup-node" in step.get("uses", "")]
        for job_name, job in jobs.items()
    }
    commands = [step.get("run", "") for step in steps]

    site_jobs = {"site-build", "site-parity"}
    assert versions["site-build"] == ["22"]
    assert all(version == ["20"] for name, version in versions.items() if name not in site_jobs and version)
    assert "make site-check site-build" in commands
    assert "uv run python scripts/publication/check_artifact_privacy.py website/dist" in commands


@pytest.mark.parametrize(
    "changed_path",
    ["website/src/pages/index.astro", "website/package-lock.json", "website/astro.config.ts"],
)
def test_visual_inputs_classify_website_as_a_render_input(tmp_path: Path, changed_path: str) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=develop", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base", "-q")
    base = _git(repo, "rev-parse", "HEAD").strip()
    target = repo / changed_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("changed\n", encoding="utf-8")
    _git(repo, "add", changed_path)
    _git(repo, "commit", "-m", "change", "-q")
    head = _git(repo, "rev-parse", "HEAD").strip()

    classifier = next(
        step["run"] for step in _site_docs_jobs()["site-paths"]["steps"] if step.get("id") == "site-inputs"
    )
    output = tmp_path / "github-output"
    env = {
        **os.environ,
        "EVENT_NAME": "pull_request",
        "PR_BASE_SHA": base,
        "RECOVERY_SOURCE_SHA": "",
        "CURRENT_SHA": head,
        "CURRENT_REF": "refs/pull/1/merge",
        "GITHUB_OUTPUT": output.as_posix(),
    }
    skip_without_posix_shell()
    result = run_posix_shell(classifier, cwd=repo, env=env, capture_output=True, text=True, encoding="utf-8")

    assert result.returncode == 0, result.stderr
    lines = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines() if "=" in line)
    assert lines["changed"] == "true"
    assert lines["render_changed"] == "true"
