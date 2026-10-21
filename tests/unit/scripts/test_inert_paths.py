from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from path_filter_decision import classify_paths, load_rules

from _project.scripts import inert_paths as inert
from _project.scripts.oracle_reviewers.classifier import ChangedFile as ReviewFile, ClassifierRules, soundness_scope
from scripts import coverage_test_selection as sel

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / ".github" / "inert-paths.txt"
READERS_FILE = ROOT / "_project" / "inert-doc-readers.json"
ROUTER_RULES = load_rules(ROOT / ".github" / "path-filters.yml")
EMPTY_RULES = ClassifierRules(gate_paths=(), logic_paths=(), data_paths=(), exempt_paths=())
MACHINE_READ_SAMPLE = [
    "AGENTS.md",
    "README.md",
    "docs/README.md",
    "docs/agent/batch-feature-delivery.md",
    "docs/internal/repo-admin-settings.md",
    "docs/internal/dev-loop-worktrees.md",
    "docs/operations/ci-local-parity.md",
    "docs/reference/public-contracts.md",
    "docs/publish-allowlist.txt",
    "docs/publish-exclusions.txt",
    "docs/linkcheck_ignore.txt",
    "tests/uat/CHARTER.md",
    ".claude/settings.json",
    ".claude/commands/blind-spot.md",
    ".claude/skills/code/SKILL.md",
    "_project/scripts/prompts/pr_review_followup.md",
    "benchbox/utils/VERSION_MANAGEMENT.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
]
INERT_SAMPLE = [
    "docs/internal/branch-rename-runbook.md",
    "_project/todo-db-export/index.md",
    "docs/guides/tpc/tpc-di-deployment-guide.md",
]
CODE_ROUTED_SAMPLE = [
    "AGENTS.md",
    "README.md",
    "docs/README.md",
    "docs/internal/repo-admin-settings.md",
    "docs/operations/ci-local-parity.md",
    "docs/reference/public-contracts.md",
    "docs/publish-allowlist.txt",
    "docs/publish-exclusions.txt",
    "docs/linkcheck_ignore.txt",
    "tests/uat/CHARTER.md",
    ".claude/commands/blind-spot.md",
    "benchbox/utils/VERSION_MANAGEMENT.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
]


def _tracked() -> set[str]:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        check=True,
        text=True,
        capture_output=True,
        timeout=120,
    )
    return {entry for entry in proc.stdout.split("\0") if entry}


def _review_file(path: str) -> ReviewFile:
    return ReviewFile(path, None, 5, 0)


def _candidates(source: str, name: str = "probe.py") -> set[str]:
    return inert.file_candidates(source, str(ROOT / name), str(ROOT))


def test_seed_exceptions_are_machine_read_but_not_inert() -> None:
    for path in MACHINE_READ_SAMPLE:
        assert inert.is_machine_read_doc(path), path
        assert not inert.is_inert_path(path), path


def test_plain_prose_stays_inert() -> None:
    assert inert.is_inert_path("docs/internal/branch-rename-runbook.md")
    assert inert.is_inert_path("docs/some-guide.md")
    assert inert.is_inert_path("_project/todo-db-export/events.jsonl")
    assert not inert.is_inert_path("benchbox/core/engine.py")
    assert not inert.is_inert_path("")


def test_inert_predicate_normalises_windows_separators() -> None:
    assert inert.is_inert_path("docs\\internal\\branch-rename-runbook.md")


def test_soundness_paths_still_need_review() -> None:
    from _project.scripts.soundness_paths import is_soundness_path

    assert is_soundness_path(".github/PULL_REQUEST_TEMPLATE.md")


def test_every_exception_matches_a_tracked_path() -> None:
    tracked = _tracked()
    basenames = inert.basename_index(tracked)
    missing = [
        pattern
        for pattern in inert.EXCEPT_PATTERNS
        if not any(inert._glob_matches(path, pattern) for path in tracked)
        and not basenames.get(pattern.rsplit("/", 1)[-1], ())
        and pattern not in tracked
    ]
    assert not missing, f"exception patterns with no tracked file: {missing}"


def test_manifest_uses_only_known_kinds() -> None:
    kinds = {line.split("\t", 1)[0] for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()}
    assert kinds <= {"glob", "file", "prefix", "regex", "except"}
    assert "except" in kinds


REPO_ROOT_BINDING = "REPO_ROOT = Path(__file__).resolve().parent.parent\n"
ROOT_BINDING = "ROOT = Path(__file__).resolve().parent.parent\n"


def test_resolves_joined_path_chains() -> None:
    source = REPO_ROOT_BINDING + 'RUNBOOK = REPO_ROOT / "docs" / "internal" / "repo-admin-settings.md"\n'
    assert "docs/internal/repo-admin-settings.md" in _candidates(source, "scripts/probe.py")


def test_resolves_os_path_join() -> None:
    source = 'import os.path\np = os.path.join("docs", "operations", "ci-local-parity.md")\n'
    assert "docs/operations/ci-local-parity.md" in _candidates(source)


def test_resolves_file_relative_constants() -> None:
    source = 'PROMPT = Path(__file__).resolve().parent / "prompts" / "pr_review_followup.md"\n'
    assert "_project/scripts/prompts/pr_review_followup.md" in _candidates(
        source, "_project/scripts/pr_review_followups.py"
    )


def test_resolves_parents_subscript() -> None:
    source = 'ROOT = Path(__file__).resolve().parents[3]\np = ROOT / "AGENTS.md"\n'
    assert "AGENTS.md" in _candidates(source, "tests/integration/worktree/test_x.py")


def test_resolves_loop_and_comprehension_bindings() -> None:
    looped = (
        ROOT_BINDING
        + 'for relative in ("AGENTS.md", "docs/agent/batch-feature-delivery.md"):\n    p = ROOT / relative\n'
    )
    assert {"AGENTS.md", "docs/agent/batch-feature-delivery.md"} <= _candidates(looped, "scripts/probe.py")
    comp = ROOT_BINDING + 'docs = [ROOT / relative for relative in ("AGENTS.md", "docs/agent/review-protocol.md")]\n'
    assert {"AGENTS.md", "docs/agent/review-protocol.md"} <= _candidates(comp, "scripts/probe.py")


def test_resolves_function_default_paths() -> None:
    source = (
        REPO_ROOT_BINDING
        + 'DOCS_DIR = REPO_ROOT / "docs"\ndef pages(docs_dir=DOCS_DIR):\n    return docs_dir / "publish-exclusions.txt"\n'
    )
    assert "docs/publish-exclusions.txt" in _candidates(source, "scripts/probe.py")


def test_resolves_path_literal_sinks() -> None:
    assert "README.md" in _candidates('version = Path("README.md")\n')


def test_ignores_bare_mentions_and_tmp_paths() -> None:
    source = 'OTHER = ["README.md", "docs/index.md"]\nchangelog = tmp_path / "CHANGELOG.md"\n'
    assert _candidates(source) == set()


def test_text_candidates_skip_comments_and_echo_lines() -> None:
    text = (
        "# see docs/internal/nightly-t3.md\n\t@echo see CONTRIBUTING.md\nPATHS=(\n'results-data/CORPUS_NOTES.md'\n)\n"
    )
    assert inert.text_candidates(text) == {"CONTRIBUTING.md", "results-data/CORPUS_NOTES.md"}
    assert inert.text_candidates(text, skip_echo_lines=True) == {"results-data/CORPUS_NOTES.md"}


def test_guard_flags_reference_missing_its_exception(tmp_path: Path) -> None:
    manifest = tmp_path / "inert-paths.txt"
    manifest.write_text("glob\tdocs/**\n", encoding="utf-8")
    rules = inert._load_rules(manifest, inert._VALID_KINDS)
    assert inert.matches_exemption("docs/secret.md", rules)
    assert not inert.matches_exception("docs/secret.md", ())
    assert inert.matches_exemption("docs/secret.md", rules) and not inert.matches_exception("docs/secret.md", ())


def test_symlink_guard_flags_inert_endpoints() -> None:
    problems = inert.symlink_violations(
        [("docs/x.md", "y.md"), ("ok.py", "docs/guide.md"), ("a/b.py", "../c.py"), ("lib.py", "lib2.py")]
    )
    assert problems == [
        "tracked symlink docs/x.md points from an inert path",
        "tracked symlink docs/x.md points into inert path docs/y.md",
        "tracked symlink ok.py points into inert path docs/guide.md",
    ]
    assert inert.symlink_violations([("lib.py", "lib2.py")]) == []


def test_guard_reports_no_unexcepted_inert_references() -> None:
    refs = inert.scan_tree(ROOT)
    assert refs, "expected the guard to resolve references across the tree"
    violations = sorted(
        f"{source} -> {hit}" for source, hits in refs.items() for hit in hits if inert.is_inert_path(hit)
    )
    assert not violations, f"referenced inert paths missing an exception: {violations[:10]}"


def test_guard_resolves_the_joined_path_consumers() -> None:
    refs = inert.scan_tree(ROOT)
    assert "docs/internal/repo-admin-settings.md" in refs["scripts/ruleset_drift_check.py"]
    assert "docs/operations/ci-local-parity.md" in refs["tests/system/test_ci_lint_parity.py"]
    assert "README.md" in refs["benchbox/utils/version.py"]
    assert "docs/publish-exclusions.txt" in refs["scripts/check_doc_external_links.py"]
    assert "docs/linkcheck_ignore.txt" in refs["scripts/check_doc_external_links.py"]
    assert "_project/scripts/prompts/pr_review_followup.md" in refs["_project/scripts/pr_review_followups.py"]


def test_checked_in_readers_match_the_rebuilt_index() -> None:
    assert json.loads(READERS_FILE.read_text(encoding="utf-8")) == inert.doc_reader_index(ROOT)


def test_no_tracked_symlinks_point_into_inert_paths() -> None:
    assert inert.symlink_violations(inert.tracked_symlink_entries(ROOT)) == []


def test_inert_only_change_is_out_of_scope() -> None:
    scope = soundness_scope([_review_file("docs/internal/branch-rename-runbook.md")], EMPTY_RULES, lambda path: False)
    assert scope.soundness is False


def test_machine_read_change_is_in_scope_without_policy_exemptions() -> None:
    scope = soundness_scope([_review_file("AGENTS.md")], EMPTY_RULES, lambda path: False)
    assert scope.soundness is True
    assert scope.paths == ("AGENTS.md",)


def test_gate_template_still_wins_over_the_manifest_exception() -> None:
    from _project.scripts.soundness_paths import is_soundness_path

    gate = "_project/scripts/oracle_reviewers/templates/gate.md"
    scope = soundness_scope(
        [_review_file(gate)],
        ClassifierRules(
            gate_paths=("_project/scripts/oracle_reviewers/*",), logic_paths=(), data_paths=(), exempt_paths=()
        ),
        is_soundness_path,
    )
    assert scope.soundness is True
    assert scope.gate_paths == (gate,)


def test_machine_read_change_selects_its_readers() -> None:
    files = [sel.ChangedFile(path="AGENTS.md", old_path="AGENTS.md", status="modified")]
    readers = {"AGENTS.md": ["tests/integration/worktree/test_batch_skill_delivery.py"]}
    _, _, _, reader_tests = sel._classify_files(ROOT, None, files, {}, readers)
    assert reader_tests == {"tests/integration/worktree/test_batch_skill_delivery.py"}
    _, uncovered, _, _ = sel._classify_files(ROOT, None, files, {}, {})
    assert uncovered == {"AGENTS.md"}


def test_live_readers_send_agents_md_to_skill_delivery() -> None:
    assert "tests/integration/worktree/test_batch_skill_delivery.py" in sel.load_readers(ROOT)["AGENTS.md"]


def test_machine_read_docs_route_as_code() -> None:
    for path in CODE_ROUTED_SAMPLE:
        decision = classify_paths([path], ROUTER_RULES)
        assert decision["needs_code_ci"] is True, path
        assert decision["safe_content_only"] is False, path


def test_skill_files_route_to_skill_integrity_not_safe_content() -> None:
    decision = classify_paths([".claude/skills/code/SKILL.md"], ROUTER_RULES)
    assert decision["safe_content_only"] is False
    assert decision["needs_code_ci"] or decision["skill_integrity_needed"]


def test_inert_docs_stay_safe_content() -> None:
    for path in INERT_SAMPLE:
        if path not in _tracked():
            continue
        decision = classify_paths([path], ROUTER_RULES)
        assert decision["safe_content_only"] is True, path
        assert decision["needs_code_ci"] is False, path
