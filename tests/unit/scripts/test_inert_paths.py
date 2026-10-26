from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from ci_units import classify_units, load_unit_rules
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
    source = 'import os.path\np = os.path.join("docs", "internal", "nightly-t3.md")\n'
    assert "docs/internal/nightly-t3.md" in _candidates(source)


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
    source = 'OTHER = ["README.md", "docs/some-guide.md"]\nchangelog = tmp_path / "CHANGELOG.md"\n'
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


def test_guard_reports_no_unexcepted_inert_references(inert_scan: dict[str, list[str]]) -> None:
    refs = inert_scan
    assert refs, "expected the guard to resolve references across the tree"
    violations = sorted(
        f"{source} -> {hit}" for source, hits in refs.items() for hit in hits if inert.is_inert_path(hit)
    )
    assert not violations, f"referenced inert paths missing an exception: {violations[:10]}"


def test_guard_resolves_the_joined_path_consumers(inert_scan: dict[str, list[str]]) -> None:
    refs = inert_scan
    assert "docs/internal/repo-admin-settings.md" in refs["scripts/ruleset_drift_check.py"]
    assert "README.md" in refs["benchbox/utils/version.py"]
    assert "docs/publish-exclusions.txt" in refs["scripts/doc_contracts.py"]
    assert "docs/linkcheck_ignore.txt" in refs["scripts/doc_contracts.py"]
    assert "_project/scripts/prompts/pr_review_followup.md" in refs["_project/scripts/pr_review_followups.py"]


def test_checked_in_readers_match_the_rebuilt_index(inert_scan: dict[str, list[str]]) -> None:
    assert json.loads(READERS_FILE.read_text(encoding="utf-8")) == inert.doc_reader_index(ROOT, inert_scan)


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
    assert "tests/integration/worktree/test_batch_skill_delivery.py" in inert.load_readers(ROOT)["AGENTS.md"]


def test_machine_read_docs_route_as_code() -> None:
    routed = sorted(path for path in _tracked() if inert.matches_exception(path))
    assert routed, "expected tracked machine-read documents"
    for path in routed:
        decision = classify_paths([path], ROUTER_RULES)
        assert decision["needs_code_ci"] or decision["skill_integrity_needed"], path
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


def test_non_markdown_files_read_by_tests_are_not_docs_only(inert_scan: dict[str, list[str]]) -> None:
    rules = load_unit_rules(ROOT / ".github" / "ci-units.yml")
    readers = sorted(
        {
            hit
            for source, hits in inert_scan.items()
            if inert._is_test_file(source)
            for hit in hits
            if not hit.endswith(".md")
        }
    )
    assert "_project/planning/visible_metrics.yaml" in readers
    unrouted = [
        path
        for path in readers
        if {unit for unit, on in classify_units([path], rules)["units"].items() if on} == {"docs"}
    ]
    assert not unrouted, f"non-markdown files read by tests but routed to the docs unit only: {unrouted}"


def _windows_locale_run(real_run: Any) -> Any:
    def run(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("text") and "encoding" not in kwargs:
            kwargs["encoding"] = "cp1252"
        return real_run(*args, **kwargs)

    return run


def _quoted_repo(path: Path, extra: str | None = None) -> str:
    quoted = "notes/right" + chr(0x201D) + "quote.md"
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "core.quotepath", "false"], check=True, capture_output=True)
    target = path / quoted
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("page\n", encoding="utf-8")
    if extra == "symlink":
        (path / "target.md").write_text("page\n", encoding="utf-8")
        (path / ("notes/link" + chr(0x201D) + "name.md")).symlink_to("target.md")
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.com", "add", "."],
        check=True,
        capture_output=True,
    )
    return quoted


def test_tracked_files_decode_smart_quote_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    quoted = _quoted_repo(tmp_path / "repo")
    monkeypatch.setattr(inert, "subprocess", SimpleNamespace(run=_windows_locale_run(subprocess.run)))
    assert quoted in inert.tracked_files(tmp_path / "repo")


def test_tracked_symlink_entries_decode_smart_quote_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _quoted_repo(tmp_path / "repo", extra="symlink")
    monkeypatch.setattr(inert, "subprocess", SimpleNamespace(run=_windows_locale_run(subprocess.run)))
    names = {name for name, _ in inert.tracked_symlink_entries(tmp_path / "repo")}
    assert "notes/link" + chr(0x201D) + "name.md" in names


def test_tracked_files_decode_names_as_strict_utf8(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "notes-ok.md").write_text("page\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", "add", "."],
        check=True,
        capture_output=True,
    )
    seen: dict[str, Any] = {}
    real_run = subprocess.run

    def run(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(inert, "subprocess", SimpleNamespace(run=run))
    assert inert.tracked_files(repo) == {"notes-ok.md"}
    assert seen.get("encoding") == "utf-8"
    assert "errors" not in seen
