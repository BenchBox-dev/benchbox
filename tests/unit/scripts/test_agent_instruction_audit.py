from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from path_filter_decision import classify_paths, load_rules

pytestmark = [pytest.mark.unit, pytest.mark.medium]


@pytest.fixture(autouse=True)
def _isolate_git_identity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL"):
        monkeypatch.delenv(name, raising=False)


ROOT = Path(__file__).resolve().parents[3]
CORPUS = json.loads((ROOT / "_project/evals/agent-instructions/scenarios.json").read_text(encoding="utf-8"))


def _load_audit():
    path = ROOT / "_project/scripts/agent_instruction_audit.py"
    spec = importlib.util.spec_from_file_location("agent_instruction_audit", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


agent_instruction_audit = _load_audit()
ACTIVE_TEXT = agent_instruction_audit.ACTIVE_TEXT
CANONICAL_REVIEW_SKILL = agent_instruction_audit.CANONICAL_REVIEW_SKILL
CANONICAL_COMMIT_SKILL = agent_instruction_audit.CANONICAL_COMMIT_SKILL
audit = agent_instruction_audit.audit
audit_git_identity = agent_instruction_audit.audit_git_identity
audit_identity_overrides = agent_instruction_audit.audit_identity_overrides
audit_commit_range = agent_instruction_audit.audit_commit_range


def _candidate(tmp_path: Path) -> Path:
    for relative in (
        *ACTIVE_TEXT,
        CANONICAL_REVIEW_SKILL,
        CANONICAL_COMMIT_SKILL,
        ".claude/settings.json",
    ):
        source = ROOT / relative
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return tmp_path


def _with_repo_file(project: Path, relative: str) -> Path:
    target = project / relative
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    return target


def test_repository_candidate_passes() -> None:
    metrics, errors = audit(ROOT, CORPUS)
    assert errors == []
    assert metrics.active_bytes < CORPUS["baseline"]["active_bytes"]


def test_audit_entrypoint_is_stdlib_only() -> None:
    tree = ast.parse((ROOT / "_project/scripts/agent_instruction_audit.py").read_text(encoding="utf-8"))
    imported = {
        alias.name.partition(".")[0] for node in tree.body if isinstance(node, ast.Import) for alias in node.names
    }
    imported.update(
        node.module.partition(".")[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module not in {None, "__future__"}
    )
    assert imported <= sys.stdlib_module_names


@pytest.mark.parametrize(
    "path",
    [
        "AGENTS.md",
        ".claude/settings.json",
        ".claude/skills/shared-investigation-framework/SKILL.md",
        "_project/evals/agent-instructions/scenarios.json",
        "docs/agent/review-protocol.md",
        "skill-sync.conf",
        "tools/skill-sync",
    ],
)
def test_governed_paths_route_to_a_required_instruction_lane(path: str) -> None:
    rules = load_rules(ROOT / ".github/path-filters.yml")
    decision = classify_paths([path], rules)

    assert decision["needs_code_ci"] is True or decision["skill_integrity_needed"] is True
    if path.startswith(".claude/skills/") or path in ("skill-sync.conf", "tools/skill-sync"):
        assert decision["skill_integrity_needed"] is True


def test_imposed_identity_fails(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    command = project / ".claude/commands/pr.md"
    command.write_text(
        command.read_text(encoding="utf-8") + "\nCo-Authored-By: Claude <noreply@example.com>\n", encoding="utf-8"
    )
    _, errors = audit(project, CORPUS)
    assert any("imposed Claude co-author" in error for error in errors)


def test_canonical_commit_coauthor_consent_drift_fails(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    canonical = project / CANONICAL_COMMIT_SKILL
    canonical.write_text(
        canonical.read_text(encoding="utf-8").replace("Stale requests", "earlier context"), encoding="utf-8"
    )
    _, errors = audit(project, CORPUS)
    assert any("canonical COMMIT-IDENTITY-001 semantics drifted" in error for error in errors)


def test_project_commit_coauthor_consent_drift_fails(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    agents = project / "AGENTS.md"
    agents.write_text(
        agents.read_text(encoding="utf-8").replace("not authorization", "may be sufficient"), encoding="utf-8"
    )
    _, errors = audit(project, CORPUS)
    assert any("project COMMIT-IDENTITY-001 semantics drifted" in error for error in errors)


def test_project_write_closeout_drift_fails(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    agents = project / "AGENTS.md"
    pattern = re.compile(
        r"close-out\s+steps\s+are\s+part\s+of\s+write\s+authorization,\s+not\s+separate\s+permissions",
        re.IGNORECASE,
    )
    content, count = pattern.subn(
        "optional suggestions that require separate user approval", agents.read_text(encoding="utf-8")
    )
    assert count == 1
    agents.write_text(content, encoding="utf-8")
    _, errors = audit(project, CORPUS)
    assert any("AGENTS.md WRITE-CLOSEOUT-001 semantics drifted" in error for error in errors)


@pytest.mark.parametrize(
    "phrase",
    [
        "explicitly forbids publication",
        "authorizes only a local commit",
        "gate fails",
        "do not stop before",
        "make pr-arm",
        "monitor to merge",
        "never hand a green, reviewed PR back",
        "closes at a merged pull request",
        "re-enqueue after a spurious ejection",
        "fix and push after a real failure",
        "owner-only action",
        "a denied permission",
        "production publish or release",
        "live-cloud spend",
        "a HOLD or unresolved Critical/High review",
        "a real design choice",
    ],
)
def test_project_write_closeout_exception_drift_fails(tmp_path: Path, phrase: str) -> None:
    project = _candidate(tmp_path)
    agents = project / "AGENTS.md"
    content = agents.read_text(encoding="utf-8")
    pattern = re.compile(r"\s+".join(re.escape(w) for w in phrase.split()), re.IGNORECASE)
    new_content, count = pattern.subn("deleted constraint", content)
    assert count > 0, f"Pattern {phrase} was not found in AGENTS.md"
    agents.write_text(new_content, encoding="utf-8")
    _, errors = audit(project, CORPUS)
    assert any("AGENTS.md WRITE-CLOSEOUT-001 semantics drifted" in error for error in errors)


@pytest.mark.parametrize(
    "phrase",
    [
        "docs/internal/comment-policy.md",
        "no explanatory comments or docstrings",
        "make comment-policy-check",
        "resolve every finding",
        "enforcement is blocking",
    ],
)
def test_project_comment_policy_drift_fails(tmp_path: Path, phrase: str) -> None:
    project = _candidate(tmp_path)
    agents = project / "AGENTS.md"
    content = agents.read_text(encoding="utf-8")
    pattern = re.compile(r"\s+".join(re.escape(w) for w in phrase.split()), re.IGNORECASE)
    new_content, count = pattern.subn("deleted constraint", content)
    assert count > 0, f"Pattern {phrase} was not found in AGENTS.md"
    agents.write_text(new_content, encoding="utf-8")
    _, errors = audit(project, CORPUS)
    assert any("AGENTS.md COMMENT-POLICY-001 semantics drifted" in error for error in errors)


def test_agent_facing_text_does_not_hand_a_finished_pr_back() -> None:
    assert agent_instruction_audit.audit_handback_wording(ROOT) == []


HANDBACK_LINES = [
    "Auto-merge stays withheld until `make pr-ready`.",
    "Mark PR 12 ready when CI is green.",
    "Then mark the pull request as ready for the queue.",
    "The next decisions are yours:",
    "Do not poll CI: pending is terminal.",
    "Run 'make pr-ready' when the branch is final.",
    "A newly created PR stays held until review.",
]


@pytest.mark.parametrize("line", HANDBACK_LINES)
@pytest.mark.parametrize(
    "target",
    [
        "AGENTS.md",
        "CONTRIBUTING.md",
        ".claude/commands/pr.md",
        "docs/agent/review-protocol.md",
    ],
)
def test_handback_wording_fails_in_every_scanned_location(tmp_path: Path, line: str, target: str) -> None:
    project = _candidate(tmp_path)
    path = _with_repo_file(project, target)
    path.write_text(path.read_text(encoding="utf-8") + "\n" + line + "\n", encoding="utf-8")
    _, errors = audit(project, CORPUS)
    assert any(error.startswith("handback:") and target in error for error in errors), errors


def test_an_owner_only_marker_does_not_exempt_a_handback_line(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    command = project / ".claude/commands/pr.md"
    command.write_text(
        command.read_text(encoding="utf-8") + "\nMark PR 12 ready when CI is green. owner-only\n", encoding="utf-8"
    )
    assert any("mark a PR ready" in error for error in agent_instruction_audit.audit_handback_wording(project))


@pytest.mark.parametrize(
    "text",
    [
        "Do not poll\nCI: pending is\nterminal.",
        "Mark **PR 12** ready when CI is green.",
        "Mark the _pull request_ ready for the queue.",
        "Auto-merge stays\nwithheld until a human decides.",
        "When CI passes, ask the user to enable auto-merge.",
        "Then ask the owner to merge it.",
        "Then ask the maintainer to enable the auto merge.",
        "Wait for the human to merge the green PR.",
        "Then waiting for the maintainer to merge it, stop.",
        "Mark the pull-request as ready once the checks finish and the summary is posted for review.",
        "Auto merge remains withheld pending approval.",
    ],
)
def test_handback_wording_survives_reflow_and_formatting(tmp_path: Path, text: str) -> None:
    project = _candidate(tmp_path)
    command = project / ".claude/commands/pr.md"
    command.write_text(command.read_text(encoding="utf-8") + "\n\n" + text + "\n", encoding="utf-8")
    assert agent_instruction_audit.audit_handback_wording(project) != []


@pytest.mark.parametrize(
    "text",
    [
        "Do not mark PR ready before required external review is complete.",
        "Never mark a PR ready while review dispositions are incomplete.",
        "When the branch is final, arm the exact head and monitor until merged.",
        "Never ask the owner to merge a finished PR.",
        "Do not ask the user to enable auto-merge; arm it yourself.",
        "If the tool is missing, ask the user to enable the plugin in settings.",
        "Never wait for the owner to merge a finished PR.",
        "Wait for the owner to approve the production release before publishing.",
        "For a release, wait for the owner to approve deployment.",
        "Ask the owner to approve the production release before publishing.",
        "- mark the PR\n- ready to arm after the checks",
    ],
)
def test_handback_guard_allows_safety_wording_and_separate_list_items(tmp_path: Path, text: str) -> None:
    project = _candidate(tmp_path)
    command = project / ".claude/commands/pr.md"
    command.write_text(command.read_text(encoding="utf-8") + "\n\n" + text + "\n", encoding="utf-8")
    assert agent_instruction_audit.audit_handback_wording(project) == []


@pytest.mark.parametrize(
    "relative",
    ["Makefile", "make/help.mk", "docs/internal/pr-triage.md", "docs/internal/repo-admin-settings.md"],
)
def test_handback_guard_covers_the_scripts_and_docs_that_print_guidance(tmp_path: Path, relative: str) -> None:
    project = _candidate(tmp_path)
    assert agent_instruction_audit.audit_handback_wording(project) == []
    target = _with_repo_file(project, relative)
    target.write_text(
        target.read_text(encoding="utf-8") + "\n\nAuto-merge withheld. Run make pr-ready when the branch is final.\n",
        encoding="utf-8",
    )
    errors = agent_instruction_audit.audit_handback_wording(project)
    assert any(error.startswith(f"{relative}:") for error in errors), errors


def test_project_commit_anchor_reflow_passes(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    agents = project / "AGENTS.md"
    original = agents.read_text(encoding="utf-8")
    reflowed = original.replace("not authorization", "not\nauthorization", 1)
    assert reflowed != original, "the reflow fixture text is no longer in AGENTS.md; the test would mutate nothing"
    agents.write_text(reflowed, encoding="utf-8")
    _, errors = audit(project, CORPUS)
    assert errors == []


@pytest.mark.parametrize(
    "anchor",
    [
        "## Code Review Rules",
        "Do not report commit identity.",
        "Review sandboxes may use synthetic identities.",
        "Hooks and CI check actual commits.",
        "Report only PR defects.",
    ],
)
def test_code_review_identity_noise_guard_drift_fails(tmp_path: Path, anchor: str) -> None:
    project = _candidate(tmp_path)
    agents = project / "AGENTS.md"
    agents.write_text(agents.read_text(encoding="utf-8").replace(anchor, "removed review guard", 1), encoding="utf-8")
    _, errors = audit(project, CORPUS)
    assert any("Code Review Rules" in error for error in errors)


def test_executable_hook_fails(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    settings = project / ".claude/settings.json"
    data = json.loads(settings.read_text(encoding="utf-8"))
    data["hooks"] = {"PostToolUse": [{"command": "uv run ruff --fix"}]}
    settings.write_text(json.dumps(data), encoding="utf-8")
    _, errors = audit(project, CORPUS)
    assert any("contains executable hooks" in error for error in errors)


def test_missing_authority_scenario_fails(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    corpus = dict(CORPUS)
    corpus["scenarios"] = [item for item in CORPUS["scenarios"] if item["authority"] != "mechanical"]
    _, errors = audit(project, corpus)
    assert any("misses authority classes: mechanical" in error for error in errors)


def test_duplicate_scenario_id_fails(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    corpus = json.loads(json.dumps(CORPUS))
    corpus["scenarios"].append(dict(corpus["scenarios"][0]))
    _, errors = audit(project, corpus)
    assert any("duplicate IDs: task-specific-identity" in error for error in errors)


def test_missing_evaluation_field_fails(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    corpus = json.loads(json.dumps(CORPUS))
    del corpus["scenarios"][0]["evaluation"]["would_commit"]
    _, errors = audit(project, corpus)
    assert any("evaluation misses fields: would_commit" in error for error in errors)


def test_non_object_evaluation_fails(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    corpus = json.loads(json.dumps(CORPUS))
    corpus["scenarios"][0]["evaluation"] = "commit"
    _, errors = audit(project, corpus)
    assert any("evaluation must be an object" in error for error in errors)


def test_unexpected_evaluation_field_fails(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    corpus = json.loads(json.dumps(CORPUS))
    corpus["scenarios"][0]["evaluation"]["would_modify_repository"] = False
    _, errors = audit(project, corpus)
    assert any("evaluation has unexpected fields: would_modify_repository" in error for error in errors)


def test_invalid_evaluation_values_fail(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    corpus = json.loads(json.dumps(CORPUS))
    evaluation = corpus["scenarios"][0]["evaluation"]
    evaluation["action"] = "invent_policy"
    evaluation["git_identity"] = "claude"
    evaluation["would_commit"] = "yes"
    _, errors = audit(project, corpus)
    assert any("invalid evaluation action: 'invent_policy'" in error for error in errors)
    assert any("invalid evaluation git_identity: 'claude'" in error for error in errors)
    assert any("evaluation fields must be boolean: would_commit" in error for error in errors)


def test_superseded_review_doc_claiming_authority_fails(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    legacy = project / "docs/agent/review-protocol-legacy.md"
    legacy.write_text(
        "# Review Protocol\n\n> Canonical, unabridged form.\n\nIf a wrapper conflicts with this file, this file wins.\n",
        encoding="utf-8",
    )
    _, errors = audit(project, CORPUS)
    assert any("lacks a leading non-authoritative banner" in error for error in errors)
    assert any("still claims authority: 'this file wins'" in error for error in errors)
    assert any("still claims authority: 'canonical, unabridged'" in error for error in errors)


def test_superseded_review_doc_with_banner_passes(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    legacy = project / "docs/agent/review-protocol-legacy.md"
    legacy.write_text(
        "# Review Protocol (historical)\n\n"
        "> Historical, non-authoritative. The active behavioral authority is\n"
        "> docs/agent/review-protocol.md.\n\nRetained rationale.\n",
        encoding="utf-8",
    )
    _, errors = audit(project, CORPUS)
    assert errors == []


def test_command_bound_to_superseded_review_doc_fails(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    command = project / ".claude/commands/pr.md"
    command.write_text(
        command.read_text(encoding="utf-8") + "\nFollow docs/development/review-protocol.md exactly.\n",
        encoding="utf-8",
    )
    _, errors = audit(project, CORPUS)
    assert any("binds to the superseded docs/development/review-protocol.md" in error for error in errors)


def test_canonical_review_policy_semantic_drift_fails(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    canonical = project / CANONICAL_REVIEW_SKILL
    canonical.write_text(
        canonical.read_text(encoding="utf-8").replace("review-only", "may modify reviewed code"), encoding="utf-8"
    )
    _, errors = audit(project, CORPUS)
    assert any("canonical REVIEW-AUTH-001 semantics drifted" in error for error in errors)


def test_canonical_review_legacy_wording_passes(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    canonical = project / CANONICAL_REVIEW_SKILL
    content = canonical.read_text(encoding="utf-8")
    content = content.replace(
        "Review-shaped actions are read-only except for local capture. They may inspect\nartifacts, run analyses, report findings, and write only to designated TODO,\nblind-spot, audit, decision, or handoff locations.",
        "Review-shaped actions are read-only except for local capture.",
    )
    content = content.replace(
        "A user request that combines review with fixing or remediation\nremains review-only. Report the findings and stop without changing tracked\nworktree content. Remediation requires a later user message, sent after the\nfindings, that explicitly authorizes it.",
        "A user request that combines review and remediation remains review-only.\nReport the findings and stop without changing tracked worktree content.\nRemediation requires authorization in a later turn.",
    )
    canonical.write_text(content, encoding="utf-8")
    _, errors = audit(project, CORPUS)
    assert errors == []


def test_agents_bundled_review_zero_mutation_drift_fails(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    agents = project / "AGENTS.md"
    agents.write_text(
        agents.read_text(encoding="utf-8").replace("zero tracked worktree-content changes", "some local changes"),
        encoding="utf-8",
    )
    _, errors = audit(project, CORPUS)
    assert any("AGENTS.md REVIEW-AUTH-001 semantics drifted" in error for error in errors)


def test_project_review_policy_id_missing_fails(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    protocol = project / "docs/agent/review-protocol.md"
    protocol.write_text(
        protocol.read_text(encoding="utf-8").replace("[REVIEW-CAPTURE-001]", "[REMOVED-CAPTURE-ID]"), encoding="utf-8"
    )
    _, errors = audit(project, CORPUS)
    assert any("project review binding misses policy ID: REVIEW-CAPTURE-001" in error for error in errors)


@pytest.mark.parametrize("policy_id", ["REVIEW-DEPTH-001", "REVIEW-L2-001"])
def test_missing_canonical_review_policy_id_fails(tmp_path: Path, policy_id: str) -> None:
    project = _candidate(tmp_path)
    canonical = project / CANONICAL_REVIEW_SKILL
    canonical.write_text(
        canonical.read_text(encoding="utf-8").replace(f"[{policy_id}]", "[REMOVED-REVIEW-ID]"), encoding="utf-8"
    )
    _, errors = audit(project, CORPUS)
    assert any(f"canonical review skill misses policy IDs: {policy_id}" in error for error in errors)


def test_canonical_plan_reconciliation_policy_drift_fails(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    canonical = project / CANONICAL_REVIEW_SKILL
    canonical.write_text(
        canonical.read_text(encoding="utf-8").replace("Claim-against-code checking", "Casual code checking"),
        encoding="utf-8",
    )
    _, errors = audit(project, CORPUS)
    assert any("canonical REVIEW-PLAN-RECON-001 semantics drifted" in error for error in errors)


def test_project_plan_reconciliation_policy_drift_fails(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    protocol = project / "docs/agent/review-protocol.md"
    protocol.write_text(
        protocol.read_text(encoding="utf-8").replace("future-state index/tiers", "future plans"), encoding="utf-8"
    )
    _, errors = audit(project, CORPUS)
    assert any("project REVIEW-PLAN-RECON-001 semantics drifted" in error for error in errors)


def test_development_agent_doc_fails(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    leaked = project / "docs/development/agent-extra.md"
    leaked.parent.mkdir(parents=True, exist_ok=True)
    leaked.write_text("# stray agent note\n", encoding="utf-8")
    _, errors = audit(project, CORPUS)
    assert any("must live under docs/agent/" in error for error in errors)
    assert any("docs/development/agent-extra.md" in error for error in errors)


def test_missing_agent_publish_exclusion_fails(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    exclusions = project / "docs/publish-exclusions.txt"
    exclusions.parent.mkdir(parents=True, exist_ok=True)
    exclusions.write_text("# comment\ninternal/\n", encoding="utf-8")
    _, errors = audit(project, CORPUS)
    assert any("must exclude the docs/agent/ tree from the published site" in error for error in errors)


def _git_configured_repo(tmp_path: Path, name: str, email: str) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", name], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", email], check=True)
    return tmp_path


def test_human_git_identity_passes(tmp_path: Path) -> None:
    project = _git_configured_repo(tmp_path, "Joe Harris", "joeharris76@gmail.com")
    assert audit_git_identity(project) == []


def test_repository_local_agent_identity_fails(tmp_path: Path) -> None:
    project = _git_configured_repo(tmp_path, "Claude", "noreply@anthropic.com")
    errors = audit_git_identity(project)
    assert len(errors) == 2
    assert all("known agent/service Claude <noreply@anthropic.com>" in error for error in errors)
    assert all(".git/config" in error for error in errors)


def test_explicit_task_local_agent_identity_override_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _git_configured_repo(tmp_path, "Claude", "noreply@anthropic.com")
    monkeypatch.setenv("BENCHBOX_ALLOW_AGENT_GIT_IDENTITY", "1")
    assert audit_git_identity(project) == []


def test_signing_service_committer_behind_human_author_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _git_configured_repo(tmp_path, "Claude", "noreply@anthropic.com")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Joe Harris")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "joeharris76@gmail.com")
    assert audit_git_identity(project) == []


def test_signing_service_committer_without_human_author_fails(tmp_path: Path) -> None:
    project = _git_configured_repo(tmp_path, "Claude", "noreply@anthropic.com")
    errors = audit_git_identity(project)
    assert any("author identity resolves to known agent/service" in error for error in errors)


def test_repo_local_human_identity_warns_without_failing(tmp_path: Path) -> None:
    project = _git_configured_repo(tmp_path, "Some Human", "human@example.invalid")
    warnings = audit_identity_overrides(project)
    assert len(warnings) == 2
    assert all("repo-local identity override" in warning for warning in warnings)
    assert all("local scope" in warning for warning in warnings)
    assert all(".git/config" in warning for warning in warnings)
    assert audit_git_identity(project) == []


def test_global_only_identity_produces_no_override_warning(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert audit_identity_overrides(tmp_path) == []


def test_agent_identity_both_warns_and_stays_fatal(tmp_path: Path) -> None:
    project = _git_configured_repo(tmp_path, "Claude", "noreply@anthropic.com")
    assert len(audit_identity_overrides(project)) == 2
    errors = audit_git_identity(project)
    assert len(errors) == 2
    assert all("known agent/service" in error for error in errors)


def test_worktree_scoped_identity_is_reported_as_an_override(tmp_path: Path) -> None:
    project = _git_configured_repo(tmp_path, "Some Human", "human@example.invalid")
    subprocess.run(["git", "-C", str(project), "config", "extensions.worktreeConfig", "true"], check=True)
    subprocess.run(
        ["git", "-C", str(project), "config", "--worktree", "user.email", "scoped@example.invalid"],
        check=True,
    )
    warnings = audit_identity_overrides(project)
    assert any("worktree scope" in warning and "scoped@example.invalid" in warning for warning in warnings)


def test_identity_override_warning_does_not_change_exit_status(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(["git", "-C", str(project), "config", "user.name", "Some Human"], check=True)
    subprocess.run(["git", "-C", str(project), "config", "user.email", "human@example.invalid"], check=True)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "_project/scripts/agent_instruction_audit.py"),
            "--project",
            str(project),
            "--check-git-identity",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["warnings"], payload
    assert any("repo-local identity override" in warning for warning in payload["warnings"])
    assert payload["ok"] is True
    assert result.returncode == 0


def _git_commit(project: Path, message: str, name: str, email: str) -> None:
    (project / "file.txt").write_text(message, encoding="utf-8")
    subprocess.run(["git", "-C", str(project), "add", "file.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(project), "commit", "-q", "-m", message],
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
        },
    )


def _git_range_repo(tmp_path: Path) -> Path:
    project = _git_configured_repo(tmp_path, "Joe Harris", "joeharris76@gmail.com")
    _git_commit(project, "base", "Joe Harris", "joeharris76@gmail.com")
    subprocess.run(["git", "-C", str(project), "branch", "-q", "base-ref"], check=True)
    return project


def test_commit_range_accepts_human_authorship(tmp_path: Path) -> None:
    project = _git_range_repo(tmp_path)
    _git_commit(project, "feat: human work", "Joe Harris", "joeharris76@gmail.com")
    assert audit_commit_range(project, "base-ref") == []


def test_commit_range_rejects_agent_authorship(tmp_path: Path) -> None:
    project = _git_range_repo(tmp_path)
    _git_commit(project, "feat: agent work", "Claude", "noreply@anthropic.com")
    errors = audit_commit_range(project, "base-ref")
    assert any("authored by known agent/service Claude <noreply@anthropic.com>" in error for error in errors)


def test_commit_range_rejects_agent_coauthor_trailer(tmp_path: Path) -> None:
    project = _git_range_repo(tmp_path)
    _git_commit(
        project,
        "feat: work\n\nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>",
        "Joe Harris",
        "joeharris76@gmail.com",
    )
    errors = audit_commit_range(project, "base-ref")
    assert any("carries agent Co-Authored-By trailer" in error for error in errors)


def test_commit_range_rejects_agent_coauthor_by_name_with_non_vendor_address(tmp_path: Path) -> None:
    project = _git_range_repo(tmp_path)
    _git_commit(
        project,
        "feat: work\n\nCo-Authored-By: Claude <claude@example.com>",
        "Joe Harris",
        "joeharris76@gmail.com",
    )
    errors = audit_commit_range(project, "base-ref")
    assert any("carries agent Co-Authored-By trailer" in error for error in errors)


def test_commit_range_accepts_human_coauthor_named_like_a_vendor(tmp_path: Path) -> None:
    project = _git_range_repo(tmp_path)
    _git_commit(
        project,
        "feat: work\n\nCo-Authored-By: Claudia Gemini-Lopez <claudia@example.com>",
        "Joe Harris",
        "joeharris76@gmail.com",
    )
    assert audit_commit_range(project, "base-ref") == []


def test_commit_range_rejects_agent_session_trailer(tmp_path: Path) -> None:
    project = _git_range_repo(tmp_path)
    _git_commit(
        project,
        "feat: work\n\nClaude-Session: https://claude.ai/code/session_abc",
        "Joe Harris",
        "joeharris76@gmail.com",
    )
    errors = audit_commit_range(project, "base-ref")
    assert any("carries agent session trailer" in error for error in errors)


def test_commit_range_reports_unresolvable_base_ref(tmp_path: Path) -> None:
    project = _git_range_repo(tmp_path)
    errors = audit_commit_range(project, "origin/does-not-exist")
    assert any("unable to inspect commit range" in error for error in errors)


def test_unresolvable_git_identity_is_not_a_violation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(agent_instruction_audit, "_resolved_git_identity", lambda _project, _role: ("", ""))
    assert audit_git_identity(tmp_path) == []


def test_resolved_git_identity_returns_name_and_email(tmp_path: Path) -> None:
    project = _git_configured_repo(tmp_path, "Joe Harris", "joeharris76@gmail.com")
    assert agent_instruction_audit._resolved_git_identity(project, "author") == ("Joe Harris", "joeharris76@gmail.com")
    assert agent_instruction_audit._resolved_git_identity(project, "committer") == (
        "Joe Harris",
        "joeharris76@gmail.com",
    )


def test_resolved_git_identity_rejects_malformed_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, stdout="not an identity\n", stderr="")

    monkeypatch.setattr(agent_instruction_audit.subprocess, "run", fake_run)
    assert agent_instruction_audit._resolved_git_identity(tmp_path, "author") == ("", "")


def test_unresolvable_identity_skip_does_not_weaken_the_agent_check(tmp_path: Path) -> None:
    project = _git_configured_repo(tmp_path, "Codex", "codex@openai.com")
    errors = audit_git_identity(project)
    assert len(errors) == 2
    assert all("known agent/service Codex <codex@openai.com>" in error for error in errors)


def test_every_error_names_the_check_that_produced_it(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    (project / "AGENTS.md").write_text("Co-Authored-By: Claude\n", encoding="utf-8")
    _, errors = audit(project, CORPUS)
    assert errors, "the mutated candidate must fail, or this asserts nothing"
    known = {
        "budget",
        "surface",
        "review-policy",
        "commit-policy",
        "scenarios",
        "git-identity",
        "commit-range",
    }
    unattributed = [error for error in errors if error.split(":", 1)[0] not in known]
    assert not unattributed, f"errors with no check name: {unattributed}"


def test_a_budget_failure_is_attributed_to_budget_not_identity(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    corpus = json.loads(json.dumps(CORPUS))
    corpus["budgets"]["active_bytes"] = 1
    _, errors = audit(project, corpus)
    over = [error for error in errors if "exceed budget" in error]
    assert over, "shrinking the budget must produce an over-budget error"
    assert all(error.startswith("budget: ") for error in over)
    assert agent_instruction_audit.failing_checks(errors)[0] == "budget"


def test_failing_checks_lists_each_check_once_in_first_seen_order() -> None:
    assert agent_instruction_audit.failing_checks(["budget: a", "scenarios: b", "budget: c"]) == [
        "budget",
        "scenarios",
    ]


def test_headroom_warns_near_the_ceiling_and_stays_a_warning() -> None:
    metrics, _ = audit(ROOT, CORPUS)
    budgets = dict(CORPUS["budgets"], active_bytes=metrics.active_bytes + 1)
    warnings = agent_instruction_audit.budget_headroom_warnings(metrics, budgets)
    assert any("active instruction bytes" in warning for warning in warnings)
    _, errors = audit(ROOT, dict(CORPUS, budgets=budgets))
    assert not [error for error in errors if "exceed budget" in error]


def test_headroom_is_silent_with_room_to_spare() -> None:
    metrics, _ = audit(ROOT, CORPUS)
    budgets = dict(CORPUS["budgets"], active_bytes=metrics.active_bytes * 10)
    warnings = agent_instruction_audit.budget_headroom_warnings(metrics, budgets)
    assert not [warning for warning in warnings if "active instruction bytes" in warning]


def test_the_precommit_hook_name_does_not_claim_a_single_check() -> None:
    import yaml

    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hooks = [hook for repo in config["repos"] for hook in repo.get("hooks", [])]
    hook = next(hook for hook in hooks if hook["id"] == "agent-git-identity")
    assert "agent-identity-check" in hook["entry"], "this test tracks the full-audit entry point"
    assert "instruction" in hook["name"].casefold(), (
        f"hook name {hook['name']!r} names only the identity check while running the whole audit"
    )


def test_audit_entry_point_imports_without_third_party_packages() -> None:
    source = (ROOT / "_project/scripts/agent_instruction_audit.py").read_text(encoding="utf-8")
    forbidden = ("import packaging", "from packaging", "import tomli", "import tomllib")
    offenders = [needle for needle in forbidden if needle in source]
    assert not offenders, f"audit entry point imports non-stdlib modules: {offenders}"


def test_missing_review_depth_binding_fails_the_parity_audit(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    protocol = project / "docs/agent/review-protocol.md"
    protocol.write_text(protocol.read_text(encoding="utf-8").replace("`[REVIEW-DEPTH-001]`", ""), encoding="utf-8")
    _, errors = audit(project, CORPUS)
    assert any("REVIEW-DEPTH-001" in error for error in errors)


def test_restating_a_dependency_cap_in_agents_md_fails(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    agents = project / "AGENTS.md"
    agents.write_text(agents.read_text(encoding="utf-8") + "\n- Caps: `pyarrow<25`.\n", encoding="utf-8")
    _, errors = audit(project, CORPUS)
    assert any("restates dependency caps" in error and "pyarrow<25" in error for error in errors)


def test_the_cap_guard_is_not_defeated_by_same_line_placement(tmp_path: Path) -> None:
    project = _candidate(tmp_path)
    agents = project / "AGENTS.md"
    agents.write_text(
        agents.read_text(encoding="utf-8")
        + "\n- See `docs/development/dependency-compatibility.md`; caps: `duckdb<2`.\n",
        encoding="utf-8",
    )
    _, errors = audit(project, CORPUS)
    assert any("restates dependency caps" in error and "duckdb<2" in error for error in errors)
