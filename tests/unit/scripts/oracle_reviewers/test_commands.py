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
    reviewer = policy.reviewers["codex-routine"]
    invocation = build(reviewer, WS, PROMPT, tmp_path)
    schema_file = str(tmp_path / "verdict-schema.json")
    assert invocation.argv[:4] == ("codex", "exec", "-C", str(WS))
    assert invocation.argv[invocation.argv.index("--model") + 1] == reviewer.model
    assert invocation.argv[invocation.argv.index("--sandbox") + 1] == "read-only"
    assert f"model_reasoning_effort={reviewer.effort}" in invocation.argv
    assert "--json" in invocation.argv
    assert invocation.argv[invocation.argv.index("--output-schema") + 1] == schema_file
    assert invocation.argv[-1] == PROMPT
    assert json.loads(Path(schema_file).read_text(encoding="utf-8")) == VERDICT_SCHEMA
    deep = build(policy.reviewers["codex-deep"], WS, PROMPT, tmp_path).argv
    assert deep[deep.index("--model") + 1] == policy.reviewers["codex-deep"].model


def test_claude_reviewer_uses_read_tools_and_json_schema(policy: Policy, tmp_path: Path) -> None:
    reviewer = policy.reviewers["claude-deep"]
    invocation = build(reviewer, WS, PROMPT, tmp_path)
    argv = invocation.argv
    assert invocation.cwd == WS
    assert argv[:2] == ("claude", "--print")
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob"
    assert argv[argv.index("--model") + 1] == reviewer.model
    assert argv[argv.index("--effort") + 1] == reviewer.effort
    assert argv[argv.index("--max-turns") + 1] == str(reviewer.turn_cap)
    assert argv[argv.index("--setting-sources") + 1] == "user"
    assert "--strict-mcp-config" in argv
    assert json.loads(argv[argv.index("--json-schema") + 1]) == VERDICT_SCHEMA
    assert argv[argv.index("--output-format") + 1] == "stream-json" and "--verbose" in argv
    assert argv[-1] == PROMPT


def test_muse_reviewer_disables_write_and_shell(policy: Policy, tmp_path: Path) -> None:
    reviewer = policy.reviewers["muse-routine"]
    argv = build(reviewer, WS, PROMPT, tmp_path).argv
    assert argv[:7] == (
        "muse",
        "exec",
        "--workspace",
        str(WS),
        "--disable-approval",
        "--disable-write",
        "--disable-shell",
    )
    assert argv[argv.index("--model") + 1] == reviewer.model
    assert argv[argv.index("--reasoning-effort") + 1] == reviewer.effort
    assert argv[argv.index("--max-model-steps") + 1] == str(reviewer.turn_cap)
    schema_file = argv[argv.index("--output-schema") + 1]
    assert schema_file == str(tmp_path / "verdict-schema.json")
    assert json.loads(Path(schema_file).read_text(encoding="utf-8")) == VERDICT_SCHEMA
    assert argv[-1] == PROMPT


def test_agy_reviewer_uses_plan_mode_and_never_effort(policy: Policy, tmp_path: Path) -> None:
    reviewer = policy.reviewers["gemini-routine"]
    invocation = build(reviewer, WS, PROMPT, tmp_path)
    argv = invocation.argv
    assert invocation.cwd == tmp_path / "agy-cwd"
    assert invocation.cwd.is_dir() and not any(invocation.cwd.iterdir())
    assert str(WS) not in " ".join(argv)
    assert argv[:3] == ("agy", "--model", reviewer.model)
    assert argv[3:5] == ("--mode", "plan")
    assert "--effort" not in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert json.loads(argv[argv.index("--json-schema") + 1]) == VERDICT_SCHEMA
    assert argv[-1] == f"--print={PROMPT}"


def test_no_reviewer_removes_a_boundary(policy: Policy, tmp_path: Path) -> None:
    for reviewer in policy.reviewers.values():
        argv = build(reviewer, WS, PROMPT, tmp_path).argv
        assert not set(argv) & set(FORBIDDEN_FLAGS), reviewer.name
