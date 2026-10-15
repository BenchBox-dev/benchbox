from __future__ import annotations

import json
from pathlib import Path

import pytest

from _project.scripts.oracle_reviewers.commands import FORBIDDEN_FLAGS, build
from _project.scripts.oracle_reviewers.policy import Policy
from _project.scripts.oracle_reviewers.verdict import VERDICT_SCHEMA

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WS = Path("/work/space")
PROMPT = "review this"


def test_codex_reviewer_is_hard_read_only_with_output_schema(policy: Policy, tmp_path: Path) -> None:
    invocation = build(policy.reviewers["sol"], WS, PROMPT, tmp_path)
    schema_file = str(tmp_path / "verdict-schema.json")
    assert invocation.argv == (
        "codex",
        "exec",
        "-C",
        str(WS),
        "--model",
        "gpt-6.1-sol",
        "--sandbox",
        "read-only",
        "-c",
        "model_reasoning_effort=medium",
        "--json",
        "--output-schema",
        schema_file,
        "-o",
        str(tmp_path / "codex-last-message.json"),
        PROMPT,
    )
    assert json.loads(Path(schema_file).read_text(encoding="utf-8")) == VERDICT_SCHEMA
    luna = build(policy.reviewers["luna"], WS, PROMPT, tmp_path).argv
    assert luna[luna.index("--model") + 1] == "gpt-6-luna"
    assert "model_reasoning_effort=high" in luna


def test_claude_reviewer_uses_read_tools_and_json_schema(policy: Policy, tmp_path: Path) -> None:
    invocation = build(policy.reviewers["opus"], WS, PROMPT, tmp_path)
    argv = invocation.argv
    assert invocation.cwd == WS
    assert argv[:2] == ("claude", "--print")
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob"
    assert argv[argv.index("--model") + 1] == "claude-opus-5-5"
    assert argv[argv.index("--effort") + 1] == "medium"
    assert argv[argv.index("--max-turns") + 1] == "60"
    assert argv[argv.index("--setting-sources") + 1] == "user"
    assert "--strict-mcp-config" in argv
    assert json.loads(argv[argv.index("--json-schema") + 1]) == VERDICT_SCHEMA
    assert argv[argv.index("--output-format") + 1] == "stream-json" and "--verbose" in argv
    assert argv[-1] == PROMPT


def test_muse_reviewer_disables_write_and_shell(policy: Policy, tmp_path: Path) -> None:
    argv = build(policy.reviewers["muse"], WS, PROMPT, tmp_path).argv
    assert argv[:9] == (
        "muse",
        "exec",
        "--workspace",
        str(WS),
        "--disable-approval",
        "--disable-write",
        "--disable-shell",
        "--model",
        "muse-spark-1.3-contributor",
    )
    assert argv[argv.index("--reasoning-effort") + 1] == "medium"
    assert argv[argv.index("--max-model-steps") + 1] == "80"
    schema_file = argv[argv.index("--output-schema") + 1]
    assert schema_file == str(tmp_path / "verdict-schema.json")
    assert json.loads(Path(schema_file).read_text(encoding="utf-8")) == VERDICT_SCHEMA
    assert argv[-1] == PROMPT


def test_agy_reviewer_uses_plan_mode_and_never_effort(policy: Policy, tmp_path: Path) -> None:
    invocation = build(policy.reviewers["agy"], WS, PROMPT, tmp_path)
    argv = invocation.argv
    assert invocation.cwd == WS
    assert argv[:5] == ("agy", "--model", "gemini-3.8-flash-medium", "--mode", "plan")
    assert "--effort" not in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert json.loads(argv[argv.index("--json-schema") + 1]) == VERDICT_SCHEMA
    assert argv[-1] == f"--print={PROMPT}"


def test_no_reviewer_removes_a_boundary(policy: Policy, tmp_path: Path) -> None:
    for reviewer in policy.reviewers.values():
        argv = build(reviewer, WS, PROMPT, tmp_path).argv
        assert not set(argv) & set(FORBIDDEN_FLAGS), reviewer.name
