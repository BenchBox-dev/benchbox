from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from _project.scripts.oracle_reviewers.commands import (
    FORBIDDEN_FLAGS,
    build,
    build_omp,
    write_overlay,
)
from _project.scripts.oracle_reviewers.policy import Reviewer
from _project.scripts.oracle_reviewers.runner import Invocation, execute

pytestmark = [pytest.mark.unit, pytest.mark.fast]

WS = Path("/work/space")
PROMPT = "review this"
SELECTOR = "anthropic/claude-opus-5-5"


def _reviewer(**overrides: Any) -> Reviewer:
    base: dict[str, Any] = {
        "name": "omp-probe",
        "harness": "omp",
        "family": "claude",
        "pool": "claude",
        "model": SELECTOR,
        "effort": "medium",
        "role": "review",
        "read_only": "hard",
        "reads_files": True,
        "timeout_minutes": 25,
        "enabled": True,
    }
    base.update(overrides)
    return Reviewer(**base)


def test_omp_argv_is_locked_down_reviewer(tmp_path: Path) -> None:
    invocation = build(_reviewer(), WS, PROMPT, tmp_path)
    argv = invocation.argv
    assert argv[0] == "omp"
    assert "--print" in argv and argv[argv.index("--mode") + 1] == "json"
    assert argv[argv.index("--model") + 1] == SELECTOR
    assert argv[argv.index("--thinking") + 1] == "medium"
    assert argv[argv.index("--tools") + 1] == "read,grep,glob"
    assert "--no-extensions" in argv and "--no-lsp" in argv
    assert "--no-skills" in argv and "--no-rules" in argv
    assert argv[argv.index("--approval-mode") + 1] == "always-ask"
    assert "--no-title" in argv and "--no-session" in argv
    assert argv[argv.index("--add-dir") + 1] == str(WS)
    assert argv[argv.index("--config") + 1] == str(tmp_path / "omp-review-overlay.yml")
    assert argv[-1] == PROMPT
    assert invocation.cwd == tmp_path / "omp-cwd"
    assert invocation.cwd != WS
    assert invocation.output_file is None
    joined = " ".join(argv)
    assert not any(flag in joined for flag in FORBIDDEN_FLAGS)


def test_omp_rejects_unknown_thinking_level(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_omp(selector=SELECTOR, effort="ultra", workspace=WS, scratch=tmp_path, prompt=PROMPT)


def test_overlay_pins_reviewer_safety_and_keeps_omp_fallbacks(tmp_path: Path) -> None:
    overlay = yaml.safe_load(write_overlay(tmp_path).read_text(encoding="utf-8"))
    assert overlay["mcp"]["enableProjectConfig"] is False
    assert overlay["advisor"]["enabled"] is False
    assert overlay["read"]["summarize"]["enabled"] is False
    assert overlay["fetch"]["enabled"] is False
    assert set(overlay["disabledProviders"]) == {
        "native",
        "omp-plugins",
        "claude",
        "agent-plugins",
        "codex",
        "agents",
        "claude-plugins",
        "gemini",
        "opencode",
        "cursor",
        "windsurf",
        "cline",
        "github",
        "vscode",
        "agents-md",
        "claude-md",
        "custom-context",
        "mcp-json",
        "ssh-json",
    }
    assert "retry" not in overlay


def test_env_passes_the_gateway_token_and_drops_provider_and_broker_variables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OMP_GATEWAY_TOKEN", "gateway-test")
    monkeypatch.setenv("ANTHROPIC_OAUTH_TOKEN", "oauth-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OMP_AUTH_BROKER_URL", "https://broker.invalid")
    monkeypatch.setenv("OMP_AUTH_BROKER_TOKEN", "broker-test")
    monkeypatch.setenv("UNRELATED_SECRET", "shh")
    env = build_omp(selector="@smol", effort="", workspace=WS, scratch=tmp_path, prompt=PROMPT).env
    assert env["OMP_GATEWAY_TOKEN"] == "gateway-test"
    for dropped in (
        "ANTHROPIC_OAUTH_TOKEN",
        "OPENAI_API_KEY",
        "OMP_AUTH_BROKER_URL",
        "OMP_AUTH_BROKER_TOKEN",
        "UNRELATED_SECRET",
    ):
        assert dropped not in env


def test_execute_honors_invocation_env(tmp_path: Path) -> None:
    argv = (sys.executable, "-c", "import os,sys; sys.exit(0 if os.environ.get('ORACLE_PROBE')=='1' else 3)")
    base = {"PATH": os.environ["PATH"]}
    ok = execute(Invocation(argv, tmp_path, None, {**base, "ORACLE_PROBE": "1"}), 60)
    assert ok.exit_code == 0
    missing = execute(Invocation(argv, tmp_path, None, dict(base)), 60)
    assert missing.exit_code == 3
