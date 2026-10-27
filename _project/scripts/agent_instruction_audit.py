#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = ROOT / "_project/evals/agent-instructions/scenarios.json"
ADAPTERS = ("CLAUDE.md", "GEMINI.md", "ANTIGRAVITY.md")
ACTIVE_REVIEW_PROTOCOL = "docs/agent/review-protocol.md"
ACTIVE_TEXT = ("AGENTS.md", *ADAPTERS, ".claude/commands/pr.md", ACTIVE_REVIEW_PROTOCOL)
CANONICAL_REVIEW_SKILL = ".claude/skills/shared-review-protocol/SKILL.md"
CANONICAL_COMMIT_SKILL = ".claude/skills/shared-change-framework/SKILL.md"
REQUIRED_POLICY_IDS = {
    "AUTH-PROVENANCE-001",
    "COMMENT-POLICY-001",
    "COMMIT-IDENTITY-001",
    "DURABLE-ARTIFACTS-001",
    "EVIDENCE-FRESHNESS-001",
    "REVIEW-AUTH-001",
    "REVIEW-DEFECT-001",
    "REVIEW-DEPTH-001",
    "REVIEW-L2-001",
    "REVIEW-CAPTURE-001",
    "REVIEW-PARITY-001",
    "REVIEW-PLAN-RECON-001",
    "WRITE-CLOSEOUT-001",
}
CANONICAL_REVIEW_POLICY_IDS = {
    "REVIEW-AUTH-001",
    "REVIEW-DEFECT-001",
    "REVIEW-DEPTH-001",
    "REVIEW-L2-001",
    "REVIEW-CAPTURE-001",
    "REVIEW-PARITY-001",
    "REVIEW-PLAN-RECON-001",
}
CANONICAL_REVIEW_ANCHORS = {
    "REVIEW-AUTH-001": (
        "read-only",
        "local capture",
        "review-only",
        "tracked worktree content",
    ),
    "REVIEW-DEFECT-001": ("classify it as a defect", "never in blind-spots"),
    "REVIEW-DEPTH-001": ("Obvious answer", "Blind-spot audit", "Problem reframe"),
    "REVIEW-L2-001": ("gaps in the review framework", "not defects already found"),
    "REVIEW-CAPTURE-001": ("protocol governs behavior", "governs storage formats"),
    "REVIEW-PARITY-001": ("Missing IDs or contradictory semantics", "skill governs behavior"),
    "REVIEW-PLAN-RECON-001": (
        "Claim-against-code checking",
        "enumerate the recorded decision surfaces",
        "future-state index and its priority tiers",
        "migration gates in design docs",
        "readiness and evidence documents",
        "open tracker items at the relevant priority",
        "cite each one or explicitly supersede it",
        "dropped open gate, is a plan defect",
    ),
}
CANONICAL_COMMIT_ANCHORS = {
    "COMMIT-IDENTITY-001": (
        "Co-Authored-By",
        "requests that exact trailer",
        "Stale requests",
        "do not grant permission",
        "human author identity",
        "committer behind a human author",
    )
}
PROJECT_COMMIT_ANCHORS = {
    "COMMIT-IDENTITY-001": (
        "Co-Authored-By",
        "exact trailer",
        "not authorization",
        "identities as author",
        "committer slot behind a human author",
    )
}
PROJECT_REVIEW_ANCHORS = {
    "REVIEW-CAPTURE-001": ("~/.todo-db/finding-drafts/", "todo-db-mcp"),
    "REVIEW-PARITY-001": ("canonical skill governs behavior", "only BenchBox-specific bindings"),
    "REVIEW-PLAN-RECON-001": (
        "future-state index/tiers",
        "migration gates",
        "readiness docs",
        "open tracker items",
    ),
}
AGENT_REVIEW_ANCHORS = {"REVIEW-AUTH-001": ("zero tracked worktree-content changes", "do not review and then edit")}
AGENT_WRITE_ANCHORS = {
    "WRITE-CLOSEOUT-001": (
        "authorized write workflow closes at a merged pull request",
        "make pr-open",
        "make pr-arm",
        "monitor to merge",
        "close-out steps are part of write authorization, not separate permissions",
        "never hand a green, reviewed pr back",
        "re-enqueue after a spurious ejection",
        "fix and push after a real failure",
        "owner-only action",
        "a denied permission",
        "production publish or release",
        "live-cloud spend",
        "a hold or unresolved critical/high review",
        "a real design choice",
        "do not stop before",
        "explicitly forbids publication",
        "authorizes only a local commit",
        "gate fails",
    )
}
AGENT_COMMENT_POLICY_ANCHORS = {
    "COMMENT-POLICY-001": (
        "docs/internal/comment-policy.md",
        "no explanatory comments or docstrings",
        "make comment-policy-check",
        "resolve every finding",
        "enforcement is blocking",
    )
}
HANDBACK_TEXT = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    ".claude/commands/*.md",
    "docs/agent/*.md",
    "docs/development/development.md",
    "docs/internal/pr-triage.md",
    "docs/internal/repo-admin-settings.md",
    "Makefile",
    "make/help.mk",
)
HANDBACK_PATTERNS = {
    "auto-merge withheld": r"\bauto[- ]merge\s+(?:is\s+|stays\s+|remains\s+)?withheld\b",
    "withheld until": r"\bwithheld until\b",
    "mark a PR ready": (
        r"(?<!not )(?<!never )\bmark(?:ing|ed)?\s+(?:the\s+|a\s+|an\s+)?(?:pr|pull[- ]request)s?\b[^.]{0,80}\bready\b"
    ),
    "decisions are yours": r"\bdecisions?\s+(?:are|is)\s+yours\b",
    "pending is terminal": r"\bpending is terminal\b",
    "do not poll CI": r"\bdo not poll ci\b",
    "run make pr-ready when": r"\brun\s+['\"]?make pr-ready['\"]?\s+when\b",
    "PR stays held": r"\bpr (?:created and held|stays held|remains held)\b",
    "wait for a human to merge": (
        r"(?<!not )(?<!never )\bwait(?:s|ing)?\s+for\s+(?:the\s+|a\s+)?(?:user|owner|maintainer|human)\s+to\s+"
        r"(?:merge|arm|mark)\b"
    ),
    "ask the user to arm or merge": (
        r"(?<!not )(?<!never )\bask\s+(?:the\s+)?(?:user|owner|maintainer|human)\s+to\s+"
        r"(?:enable\s+(?:the\s+)?auto[- ]?merge|arm|merge|mark)\b"
    ),
}
_MARKDOWN_EMPHASIS = re.compile(r"[*_`]")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")


CLI_DESCRIPTION = "Deterministic audit for BenchBox's active agent instruction surface."


def _paragraphs(text: str) -> list[tuple[int, str]]:
    paragraphs: list[tuple[int, list[str]]] = []
    current: tuple[int, list[str]] | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            current = None
            continue
        if current is None or _LIST_ITEM.match(line):
            current = (number, [])
            paragraphs.append(current)
        current[1].append(line.strip())
    return [(number, _MARKDOWN_EMPHASIS.sub("", " ".join(lines))) for number, lines in paragraphs]


CODE_REVIEW_RULE_ANCHORS = (
    "Do not report commit identity.",
    "Review sandboxes may use synthetic identities.",
    "Hooks and CI check actual commits.",
    "Report only PR defects.",
)
AUTHORITY_CLASSES = {"task", "repository", "mechanical", "recommendation"}
EVALUATION_ACTIONS = {
    "commit_with_human_identity",
    "review_only",
    "stop_publication",
    "continue_locally",
    "capture_local_draft",
    "require_live_read",
}
EVALUATION_IDENTITIES = {"human", "current_task_agent", "not_applicable"}
EVALUATION_BOOLEAN_FIELDS = {
    "would_change_tracked_worktree_content",
    "would_commit",
    "would_add_agent_coauthor",
    "would_push_or_open_pr",
    "would_write_hosted_tracker",
    "would_write_local_draft",
}
EVALUATION_FIELDS = {"action", "git_identity", *EVALUATION_BOOLEAN_FIELDS}
LEGACY_REVIEW_DOC = "docs/agent/review-protocol-legacy.md"
RETIRED_REVIEW_DOCS = (
    LEGACY_REVIEW_DOC,
    "docs/development/review-protocol.md",
    "docs/development/agent-review-protocol.md",
)
AUTHORITY_CONFLICT_MARKERS = ("this file wins", "canonical, unabridged", "conflicts resolve in favor of this")
AGENT_NAMES = {"chatgpt", "claude", "codex", "gemini", "openai"}
AGENT_EMAILS = {"noreply@anthropic.com", "noreply@openai.com"}
GLOBAL_IDENTITY_SCOPES = frozenset({"global", "system"})
SIGNING_SERVICE_EMAILS = {"noreply@anthropic.com"}
AGENT_TRAILER_RE = re.compile(r"^[ \t]*co-authored-by:[ \t]*(.+)$", re.IGNORECASE | re.MULTILINE)
TRAILER_IDENTITY_RE = re.compile(r"^(?P<name>[^<]*?)\s*(?:<(?P<email>[^>]*)>)?\s*$")
AGENT_SESSION_TRAILER_RE = re.compile(
    r"^[ \t]*(claude|codex|gemini|chatgpt)-session:[ \t]*(.+)$", re.IGNORECASE | re.MULTILINE
)


@dataclass(frozen=True)
class Metrics:
    active_bytes: int
    agents_lines: int
    adapter_bytes: dict[str, int]


def _read(project: Path, relative: str) -> str:
    return (project / relative).read_text(encoding="utf-8")


def _tag(check: str, errors: Iterable[str]) -> list[str]:
    return [f"{check}: {error}" for error in errors]


def failing_checks(errors: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for error in errors:
        check = error.split(":", 1)[0]
        if check not in seen:
            seen.append(check)
    return seen


def collect_metrics(project: Path) -> Metrics:
    texts = {path: _read(project, path) for path in ACTIVE_TEXT}
    return Metrics(
        active_bytes=sum(len(text.encode()) for text in texts.values()),
        agents_lines=len(texts["AGENTS.md"].splitlines()),
        adapter_bytes={name: len(texts[name].encode()) for name in ADAPTERS},
    )


def _policy_section(text: str, policy_id: str) -> str:
    marker = f"[{policy_id}]"
    if marker not in text:
        return ""
    section = text.split(marker, 1)[1]
    parts = re.split(r"\n\s*(?:##\s+|`?\[[A-Z0-9_-]+\])", section, maxsplit=1)
    return parts[0]


def _missing_anchors(text: str, anchors: Iterable[str]) -> list[str]:
    normalized_text = " ".join(text.casefold().split())
    return [anchor for anchor in anchors if " ".join(anchor.casefold().split()) not in normalized_text]


def audit_review_policy(project: Path) -> list[str]:
    errors: list[str] = []
    agents = _read(project, "AGENTS.md")
    protocol = _read(project, ACTIVE_REVIEW_PROTOCOL)
    canonical_review = _read(project, CANONICAL_REVIEW_SKILL)
    policy_text = agents + "\n" + protocol

    missing_ids = sorted(policy_id for policy_id in REQUIRED_POLICY_IDS if policy_id not in policy_text)
    if missing_ids:
        errors.append(f"missing active policy IDs: {', '.join(missing_ids)}")
    if ACTIVE_REVIEW_PROTOCOL not in agents:
        errors.append("AGENTS.md does not select the active project review binding")

    marker = "## Code Review Rules"
    if marker not in agents:
        errors.append("AGENTS.md misses the Code Review Rules section")
    else:
        review_rules = agents.split(marker, 1)[1].split("\n## ", 1)[0]
        missing_anchors = _missing_anchors(review_rules, CODE_REVIEW_RULE_ANCHORS)
        if missing_anchors:
            errors.append(f"AGENTS.md Code Review Rules drifted; missing anchors: {', '.join(missing_anchors)}")

    missing_canonical_ids = sorted(
        policy_id for policy_id in CANONICAL_REVIEW_POLICY_IDS if f"[{policy_id}]" not in canonical_review
    )
    if missing_canonical_ids:
        errors.append(f"canonical review skill misses policy IDs: {', '.join(missing_canonical_ids)}")
    for policy_id, anchors in CANONICAL_REVIEW_ANCHORS.items():
        section = _policy_section(canonical_review, policy_id)
        missing_anchors = _missing_anchors(section, anchors)
        if section and missing_anchors:
            errors.append(f"canonical {policy_id} semantics drifted; missing anchors: {', '.join(missing_anchors)}")
    for policy_id, anchors in PROJECT_REVIEW_ANCHORS.items():
        section = _policy_section(protocol, policy_id)
        if not section:
            errors.append(f"project review binding misses policy ID: {policy_id}")
            continue
        missing_anchors = _missing_anchors(section, anchors)
        if missing_anchors:
            errors.append(f"project {policy_id} semantics drifted; missing anchors: {', '.join(missing_anchors)}")
    for policy_id, anchors in AGENT_REVIEW_ANCHORS.items():
        section = _policy_section(agents, policy_id)
        missing_anchors = _missing_anchors(section, anchors)
        if not section:
            errors.append(f"AGENTS.md review policy misses policy ID: {policy_id}")
        elif missing_anchors:
            errors.append(f"AGENTS.md {policy_id} semantics drifted; missing anchors: {', '.join(missing_anchors)}")

    legacy_path = project / LEGACY_REVIEW_DOC
    if legacy_path.exists():
        legacy = legacy_path.read_text(encoding="utf-8")
        head = "\n".join(legacy.splitlines()[:10]).casefold()
        if "non-authoritative" not in head:
            errors.append(f"superseded {LEGACY_REVIEW_DOC} lacks a leading non-authoritative banner")
        for marker in AUTHORITY_CONFLICT_MARKERS:
            if marker in legacy.casefold():
                errors.append(f"superseded {LEGACY_REVIEW_DOC} still claims authority: {marker!r}")
    return errors


def audit_commit_policy(project: Path) -> list[str]:
    errors: list[str] = []
    agents = _read(project, "AGENTS.md")
    canonical_commit = _read(project, CANONICAL_COMMIT_SKILL)
    for policy_id, anchors in CANONICAL_COMMIT_ANCHORS.items():
        section = _policy_section(canonical_commit, policy_id)
        missing_anchors = _missing_anchors(section, anchors)
        if not section:
            errors.append(f"canonical commit skill misses policy ID: {policy_id}")
        elif missing_anchors:
            errors.append(f"canonical {policy_id} semantics drifted; missing anchors: {', '.join(missing_anchors)}")
    for policy_id, anchors in PROJECT_COMMIT_ANCHORS.items():
        section = _policy_section(agents, policy_id)
        missing_anchors = _missing_anchors(section, anchors)
        if not section:
            errors.append(f"project commit policy misses policy ID: {policy_id}")
        elif missing_anchors:
            errors.append(f"project {policy_id} semantics drifted; missing anchors: {', '.join(missing_anchors)}")
    for policy_id, anchors in AGENT_WRITE_ANCHORS.items():
        section = _policy_section(agents, policy_id)
        missing_anchors = _missing_anchors(section, anchors)
        if not section:
            errors.append(f"AGENTS.md write policy misses policy ID: {policy_id}")
        elif missing_anchors:
            errors.append(f"AGENTS.md {policy_id} semantics drifted; missing anchors: {', '.join(missing_anchors)}")
    for policy_id, anchors in AGENT_COMMENT_POLICY_ANCHORS.items():
        section = _policy_section(agents, policy_id)
        missing_anchors = _missing_anchors(section, anchors)
        if not section:
            errors.append(f"AGENTS.md comment policy misses policy ID: {policy_id}")
        elif missing_anchors:
            errors.append(f"AGENTS.md {policy_id} semantics drifted; missing anchors: {', '.join(missing_anchors)}")
    return errors


def _resolved_git_identity(project: Path, role: str) -> tuple[str, str]:
    result = subprocess.run(
        ["git", "-C", str(project), "var", f"GIT_{role.upper()}_IDENT"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        return "", ""
    match = re.match(r"^(.*?) <([^>]+)>", result.stdout.strip())
    return match.groups() if match else ("", "")


def _is_agent_identity(name: str, email: str) -> bool:
    return name.strip().casefold() in AGENT_NAMES or email.strip().casefold() in AGENT_EMAILS


def _trailer_is_agent(trailer: str) -> bool:
    match = TRAILER_IDENTITY_RE.match(trailer.strip())
    if match is None:
        return False
    return _is_agent_identity(match.group("name") or "", match.group("email") or "")


def _identity_origins(project: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(project), "config", "--show-origin", "--get-regexp", r"^user\.(name|email)$"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def audit_git_identity(project: Path) -> list[str]:
    if os.environ.get("BENCHBOX_ALLOW_AGENT_GIT_IDENTITY") == "1":
        return []

    identities = {role: _resolved_git_identity(project, role) for role in ("author", "committer")}
    author_name, author_email = identities["author"]
    author_is_human = bool(author_name and author_email) and not _is_agent_identity(author_name, author_email)

    if not all(name and email for name, email in identities.values()):
        return []

    errors: list[str] = []
    for role, (name, email) in identities.items():
        if not _is_agent_identity(name, email):
            continue
        if role == "committer" and author_is_human and email.strip().casefold() in SIGNING_SERVICE_EMAILS:
            continue
        errors.append(
            f"Git {role} identity resolves to known agent/service {name} <{email}>; "
            f"inspect config origins and use the human identity. "
            f"Origins: {_identity_origins(project) or '<none>'}"
        )
    return errors


def audit_identity_overrides(project: Path) -> list[str]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(project),
            "config",
            "--show-scope",
            "--show-origin",
            "--get-regexp",
            r"^user\.(name|email)$",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    warnings: list[str] = []
    for line in result.stdout.splitlines():
        scope, _, remainder = line.partition("\t")
        origin, _, entry = remainder.partition("\t")
        key, _, value = entry.partition(" ")
        if not key or scope in GLOBAL_IDENTITY_SCOPES:
            continue
        warnings.append(
            f"repo-local identity override: {key}={value} resolves from the {scope} scope "
            f"({origin or '<unknown origin>'}) and displaces your global identity. "
            f"A local-scope value is shared by the primary clone and every linked worktree. "
            f"Detection only: confirm it is intentional."
        )
    return warnings


def audit_commit_range(project: Path, base_ref: str) -> list[str]:
    if os.environ.get("BENCHBOX_ALLOW_AGENT_GIT_IDENTITY") == "1":
        return []

    unit, record = "\x1f", "\x00"
    result = subprocess.run(
        ["git", "-C", str(project), "log", "--format=%H%x1f%an%x1f%ae%x1f%B%x00", f"{base_ref}..HEAD"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        return [f"unable to inspect commit range {base_ref}..HEAD: {result.stderr.strip() or '<no detail>'}"]

    errors: list[str] = []
    for raw in result.stdout.split(record):
        fields = raw.strip("\n").split(unit)
        if len(fields) < 4:
            continue
        sha, name, email, body = fields[0], fields[1], fields[2], fields[3]
        if _is_agent_identity(name, email):
            errors.append(
                f"commit {sha[:12]} is authored by known agent/service {name} <{email}>; "
                f"reauthor it with the human identity before merging"
            )
        for match in AGENT_TRAILER_RE.finditer(body):
            trailer = match.group(1).strip()
            if _trailer_is_agent(trailer):
                errors.append(
                    f"commit {sha[:12]} carries agent Co-Authored-By trailer '{trailer}'; "
                    f"[COMMIT-IDENTITY-001] forbids it unless the task requested that exact trailer"
                )
        for match in AGENT_SESSION_TRAILER_RE.finditer(body):
            errors.append(
                f"commit {sha[:12]} carries agent session trailer '{match.group(0).strip()}'; "
                f"[COMMIT-IDENTITY-001] treats it as equivalent agent attribution"
            )
    return errors


def audit_dependency_caps(project: Path) -> list[str]:
    agents = _read(project, "AGENTS.md")
    offenders = re.findall(r"`([A-Za-z0-9][A-Za-z0-9._-]*\s*<=?\s*[0-9][0-9A-Za-z_.]*)`", agents)
    if offenders:
        return [
            "AGENTS.md restates dependency caps ("
            + ", ".join(sorted(set(offenders)))
            + "); pyproject.toml owns them and docs/development/dependency-compatibility.md explains them"
        ]
    return []


def audit_scenarios(scenarios: list[dict[str, Any]], policy_text: str) -> list[str]:
    errors: list[str] = []
    scenario_ids = [scenario.get("id") for scenario in scenarios]
    duplicate_ids = sorted(
        {scenario_id for scenario_id in scenario_ids if scenario_ids.count(scenario_id) > 1}, key=str
    )
    if duplicate_ids:
        errors.append(f"scenario corpus has duplicate IDs: {', '.join(str(value) for value in duplicate_ids)}")
    covered_authorities = {scenario.get("authority") for scenario in scenarios}
    missing_authorities = sorted(AUTHORITY_CLASSES - covered_authorities)
    if missing_authorities:
        errors.append(f"scenario corpus misses authority classes: {', '.join(missing_authorities)}")
    for scenario in scenarios:
        missing = sorted({"id", "prompt", "authority", "policy_id", "expected", "evaluation"} - scenario.keys())
        if missing:
            errors.append(f"scenario {scenario.get('id', '<unknown>')} misses fields: {', '.join(missing)}")
            continue
        if scenario["policy_id"] not in policy_text:
            errors.append(f"scenario {scenario['id']} references inactive policy {scenario['policy_id']}")
        evaluation = scenario["evaluation"]
        if not isinstance(evaluation, dict):
            errors.append(f"scenario {scenario['id']} evaluation must be an object")
            continue
        missing_evaluation = sorted(EVALUATION_FIELDS - evaluation.keys())
        if missing_evaluation:
            errors.append(f"scenario {scenario['id']} evaluation misses fields: {', '.join(missing_evaluation)}")
            continue
        unexpected_evaluation = sorted(evaluation.keys() - EVALUATION_FIELDS)
        if unexpected_evaluation:
            errors.append(
                f"scenario {scenario['id']} evaluation has unexpected fields: {', '.join(unexpected_evaluation)}"
            )
            continue
        if evaluation["action"] not in EVALUATION_ACTIONS:
            errors.append(f"scenario {scenario['id']} has invalid evaluation action: {evaluation['action']!r}")
        if evaluation["git_identity"] not in EVALUATION_IDENTITIES:
            errors.append(
                f"scenario {scenario['id']} has invalid evaluation git_identity: {evaluation['git_identity']!r}"
            )
        invalid_boolean_fields = sorted(
            field for field in EVALUATION_BOOLEAN_FIELDS if type(evaluation[field]) is not bool
        )
        if invalid_boolean_fields:
            errors.append(
                f"scenario {scenario['id']} evaluation fields must be boolean: {', '.join(invalid_boolean_fields)}"
            )
    return errors


HEADROOM_WARNING_RATIO = 0.97


def budget_headroom_warnings(metrics: Metrics, budgets: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    measured = [
        ("active instruction bytes", metrics.active_bytes, budgets["active_bytes"]),
        ("AGENTS.md lines", metrics.agents_lines, budgets["agents_lines"]),
        *((f"{name} bytes", size, budgets["adapter_bytes"]) for name, size in metrics.adapter_bytes.items()),
    ]
    for label, value, ceiling in measured:
        if ceiling and value <= ceiling and value >= ceiling * HEADROOM_WARNING_RATIO:
            warnings.append(f"{label} {value} is within {ceiling - value} of the {ceiling} budget")
    return warnings


def audit_docs_placement(project: Path) -> list[str]:
    errors: list[str] = []
    development = project / "docs/development"
    if development.is_dir():
        leaked = sorted(path.relative_to(project).as_posix() for path in development.glob("agent-*.md"))
        if leaked:
            errors.append("agent governance files must live under docs/agent/, not " + ", ".join(leaked))
    exclusions = project / "docs/publish-exclusions.txt"
    if exclusions.exists():
        entries = {line.strip() for line in exclusions.read_text(encoding="utf-8").splitlines()}
        if "agent/" not in entries:
            errors.append("docs/publish-exclusions.txt must exclude the docs/agent/ tree from the published site")
    return errors


def audit_handback_wording(project: Path) -> list[str]:
    errors: list[str] = []
    compiled = {label: re.compile(pattern, re.IGNORECASE) for label, pattern in HANDBACK_PATTERNS.items()}
    paths: list[Path] = []
    for entry in HANDBACK_TEXT:
        paths.extend(sorted(project.glob(entry)))
    for path in paths:
        if not path.is_file():
            continue
        relative = path.relative_to(project).as_posix()
        for number, paragraph in _paragraphs(path.read_text(encoding="utf-8", errors="ignore")):
            for label, pattern in compiled.items():
                if pattern.search(paragraph):
                    errors.append(
                        f"{relative}:{number} tells a reader to hand a finished PR back ({label}); "
                        "arm and merge it, or reword it"
                    )
    return errors


def audit(project: Path, corpus: dict[str, Any]) -> tuple[Metrics, list[str]]:
    errors: list[str] = []
    metrics = collect_metrics(project)
    budgets = corpus["budgets"]
    baseline = corpus["baseline"]

    budget_errors: list[str] = []
    if metrics.active_bytes > budgets["active_bytes"]:
        budget_errors.append(f"active instruction bytes {metrics.active_bytes} exceed budget {budgets['active_bytes']}")
    if metrics.active_bytes >= baseline["active_bytes"]:
        budget_errors.append("active instruction surface did not improve on the recorded baseline")
    if metrics.agents_lines > budgets["agents_lines"]:
        budget_errors.append(f"AGENTS.md lines {metrics.agents_lines} exceed budget {budgets['agents_lines']}")
    for name, size in metrics.adapter_bytes.items():
        if size > budgets["adapter_bytes"]:
            budget_errors.append(f"{name} bytes {size} exceed adapter budget {budgets['adapter_bytes']}")
        adapter = _read(project, name)
        if "AGENTS.md" not in adapter:
            budget_errors.append(f"{name} does not point to AGENTS.md")
    errors.extend(_tag("budget", budget_errors))

    active = "\n".join(_read(project, path) for path in ACTIVE_TEXT)
    command_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((project / ".claude/commands").glob("*.md"))
    )
    surface_errors: list[str] = []
    for retired in RETIRED_REVIEW_DOCS:
        if retired in command_text:
            surface_errors.append(f"a .claude/commands surface binds to the superseded {retired}")
    settings = json.loads(_read(project, ".claude/settings.json"))
    if settings.get("hooks"):
        surface_errors.append(".claude/settings.json contains executable hooks; use explicit gates")
    forbidden_active = {
        "imposed Claude co-author": "Co-Authored-By: Claude",
        "imposed Claude author": "author Claude",
    }
    forbidden_settings = {
        "silent stderr suppression": "2>/dev/null",
        "bare Python hook": "python3 -c",
    }
    for label, needle in forbidden_active.items():
        if needle.casefold() in (active + "\n" + command_text).casefold():
            surface_errors.append(f"{label} pattern remains: {needle}")
    settings_text = _read(project, ".claude/settings.json")
    for label, needle in forbidden_settings.items():
        if needle.casefold() in settings_text.casefold():
            surface_errors.append(f"{label} pattern remains in project settings: {needle}")
    errors.extend(_tag("surface", surface_errors))

    policy_text = _read(project, "AGENTS.md") + "\n" + _read(project, ACTIVE_REVIEW_PROTOCOL)
    errors.extend(_tag("review-policy", audit_review_policy(project)))
    errors.extend(_tag("docs-placement", audit_docs_placement(project)))
    errors.extend(_tag("handback", audit_handback_wording(project)))
    errors.extend(_tag("commit-policy", audit_commit_policy(project)))
    errors.extend(_tag("dependency-caps", audit_dependency_caps(project)))
    errors.extend(_tag("scenarios", audit_scenarios(corpus["scenarios"], policy_text)))

    return metrics, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--project", type=Path, default=ROOT)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--check-git-identity", action="store_true")
    parser.add_argument(
        "--check-commit-range",
        metavar="BASE_REF",
        help="reject agent authorship/attribution on commits in BASE_REF..HEAD (merge-time guard)",
    )
    args = parser.parse_args()
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    metrics, errors = audit(args.project.resolve(), corpus)
    warnings: list[str] = []
    if args.check_git_identity:
        errors.extend(_tag("git-identity", audit_git_identity(args.project.resolve())))
        warnings.extend(_tag("git-identity", audit_identity_overrides(args.project.resolve())))
    if args.check_commit_range:
        errors.extend(_tag("commit-range", audit_commit_range(args.project.resolve(), args.check_commit_range)))
    warnings.extend(_tag("budget", budget_headroom_warnings(metrics, corpus["budgets"])))
    checks = failing_checks(errors)
    result = {
        "ok": not errors,
        "metrics": asdict(metrics),
        "errors": errors,
        "warnings": warnings,
        "failing_checks": checks,
    }
    if args.as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        summary = "PASS" if not errors else f"FAIL ({', '.join(checks)})"
        print(f"agent-instructions: {summary}")
        print(f"active_bytes={metrics.active_bytes} agents_lines={metrics.agents_lines}")
        for name, size in metrics.adapter_bytes.items():
            print(f"{name}={size} bytes")
        for warning in warnings:
            print(f"WARNING: {warning}")
        for error in errors:
            print(f"ERROR: {error}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
