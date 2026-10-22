from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .policy import OMP_THINKING_LEVELS, Reviewer
from .verdict import VERDICT_SCHEMA

FORBIDDEN_FLAGS = ("--dangerously-skip-permissions", "--yolo", "--dangerously-bypass-approvals-and-sandbox")
OMP_READ_TOOLS = "read,grep,glob"
OMP_BASE_ENV = ("PATH", "HOME", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "TZ", "USER", "LOGNAME", "SHELL")
OMP_BROKER_ENV = ("OMP_AUTH_BROKER_URL", "OMP_AUTH_BROKER_TOKEN")
OMP_PROVIDER_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_OAUTH_TOKEN",
    "OPENAI_API_KEY",
    "OPENAI_CODEX_OAUTH_TOKEN",
    "GEMINI_API_KEY",
    "META_API_KEY",
)
OMP_OVERLAY_TEXT = """mcp:
  enableProjectConfig: false
advisor:
  enabled: false
contextPromotion:
  enabled: false
prewalk:
  enabled: false
  auto: "off"
fetch:
  enabled: false
disabledProviders:
  - native
  - omp-plugins
  - claude
  - agent-plugins
  - codex
  - agents
  - claude-plugins
  - gemini
  - opencode
  - cursor
  - windsurf
  - cline
  - github
  - vscode
  - agents-md
  - claude-md
  - custom-context
  - mcp-json
  - ssh-json
"""


@dataclass(frozen=True)
class Invocation:
    argv: tuple[str, ...]
    cwd: Path
    output_file: Path | None
    env: Mapping[str, str] | None = None


def schema_text() -> str:
    return json.dumps(VERDICT_SCHEMA, sort_keys=True, separators=(",", ":"))


def omp_env() -> dict[str, str]:
    names = (*OMP_BASE_ENV, *OMP_BROKER_ENV, *OMP_PROVIDER_ENV)
    return {name: value for name in names if (value := os.environ.get(name))}


def write_overlay(scratch: Path) -> Path:
    overlay = scratch / "omp-review-overlay.yml"
    overlay.write_text(OMP_OVERLAY_TEXT, encoding="utf-8")
    return overlay


def build_omp(*, selector: str, effort: str, workspace: Path, scratch: Path, prompt: str) -> Invocation:
    if effort and effort not in OMP_THINKING_LEVELS:
        raise ValueError(f"unknown OMP thinking level {effort!r}")
    review_cwd = scratch / "omp-cwd"
    review_cwd.mkdir(parents=True, exist_ok=True)
    overlay = write_overlay(scratch)
    thinking: tuple[str, ...] = ("--thinking", effort) if effort else ()
    argv = (
        "omp",
        "--cwd",
        str(review_cwd),
        "--add-dir",
        str(workspace),
        "--print",
        "--mode",
        "json",
        "--model",
        selector,
        *thinking,
        "--tools",
        OMP_READ_TOOLS,
        "--no-extensions",
        "--no-lsp",
        "--no-skills",
        "--no-rules",
        "--approval-mode",
        "always-ask",
        "--no-title",
        "--no-session",
        "--config",
        str(overlay),
        prompt,
    )
    return Invocation(argv, review_cwd, None, omp_env())


def build(reviewer: Reviewer, workspace: Path, prompt: str, scratch: Path) -> Invocation:
    if reviewer.harness == "omp":
        return build_omp(
            selector=reviewer.model,
            effort=reviewer.effort,
            workspace=workspace,
            scratch=scratch,
            prompt=prompt,
        )
    raise ValueError(f"unknown harness {reviewer.harness!r}")
