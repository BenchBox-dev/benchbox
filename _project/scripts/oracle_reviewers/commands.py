from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .policy import Reviewer
from .verdict import VERDICT_SCHEMA

CLAUDE_READ_TOOLS = "Read,Grep,Glob"
FORBIDDEN_FLAGS = ("--dangerously-skip-permissions", "--yolo", "--dangerously-bypass-approvals-and-sandbox")


@dataclass(frozen=True)
class Invocation:
    argv: tuple[str, ...]
    cwd: Path
    output_file: Path | None


def schema_text() -> str:
    return json.dumps(VERDICT_SCHEMA, sort_keys=True, separators=(",", ":"))


def _schema_file(scratch: Path) -> Path:
    schema_file = scratch / "verdict-schema.json"
    schema_file.write_text(schema_text(), encoding="utf-8")
    return schema_file


def build(reviewer: Reviewer, workspace: Path, prompt: str, scratch: Path) -> Invocation:
    if reviewer.harness == "codex":
        schema_file = _schema_file(scratch)
        output_file = scratch / "codex-last-message.json"
        argv = (
            "codex",
            "exec",
            "-C",
            str(workspace),
            "--model",
            reviewer.model,
            "--sandbox",
            "read-only",
            "-c",
            f"model_reasoning_effort={reviewer.effort}",
            "--json",
            "--output-schema",
            str(schema_file),
            "-o",
            str(output_file),
            prompt,
        )
        return Invocation(argv, workspace, output_file)
    if reviewer.harness == "claude":
        argv = (
            "claude",
            "--print",
            "--tools",
            CLAUDE_READ_TOOLS,
            "--model",
            reviewer.model,
            "--effort",
            reviewer.effort,
            *(("--max-turns", str(reviewer.turn_cap)) if reviewer.turn_cap else ()),
            "--setting-sources",
            "user",
            "--strict-mcp-config",
            "--permission-mode",
            "dontAsk",
            "--no-session-persistence",
            "--output-format",
            "stream-json",
            "--verbose",
            "--json-schema",
            schema_text(),
            prompt,
        )
        return Invocation(argv, workspace, None)
    if reviewer.harness == "muse":
        argv = (
            "muse",
            "exec",
            "--workspace",
            str(workspace),
            "--disable-approval",
            "--disable-write",
            "--disable-shell",
            "--model",
            reviewer.model,
            "--reasoning-effort",
            reviewer.effort,
            *(("--max-model-steps", str(reviewer.turn_cap)) if reviewer.turn_cap else ()),
            "--output-schema",
            str(_schema_file(scratch)),
            prompt,
        )
        return Invocation(argv, workspace, None)
    if reviewer.harness == "agy":
        argv = (
            "agy",
            "--model",
            reviewer.model,
            "--mode",
            "plan",
            "--output-format",
            "json",
            "--json-schema",
            schema_text(),
            f"--print={prompt}",
        )
        neutral = scratch / "agy-cwd"
        neutral.mkdir(parents=True, exist_ok=True)
        return Invocation(argv, neutral, None)
    raise ValueError(f"unknown harness {reviewer.harness!r}")
