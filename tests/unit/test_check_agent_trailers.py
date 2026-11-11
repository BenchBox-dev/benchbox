from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/check_agent_trailers.sh"


def _run(tmp_path: Path, message: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    message_file = tmp_path / "COMMIT_EDITMSG"
    message_file.write_text(message, encoding="utf-8")
    return subprocess.run(
        ["sh", str(SCRIPT), str(message_file)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


REJECTED = pytest.mark.parametrize(
    ("label", "message"),
    [
        ("vendor address", "feat: x\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"),
        ("openai vendor address", "feat: x\n\nCo-Authored-By: Codex <noreply@openai.com>"),
        ("agent name with its own address", "feat: x\n\nCo-Authored-By: Claude <claude@example.com>"),
        ("agent name, unrelated address", "feat: x\n\nCo-Authored-By: Codex <codex@somewhere.org>"),
        ("session trailer", "feat: x\n\nClaude-Session: https://claude.ai/code/session_abc"),
    ],
)

ACCEPTED = pytest.mark.parametrize(
    ("label", "message"),
    [
        ("plain human", "feat: x\n\nCo-Authored-By: Joe Harris <joeharris76@gmail.com>"),
        ("human named like a vendor", "feat: x\n\nCo-Authored-By: Claudia Gemini-Lopez <claudia@example.com>"),
        ("no trailer at all", "feat: x"),
        ("commented-out trailer", "feat: x\n\n# Co-Authored-By: Claude <noreply@anthropic.com>"),
    ],
)


@REJECTED
def test_rejects_agent_attribution(tmp_path: Path, label: str, message: str) -> None:
    result = _run(tmp_path, message)
    assert result.returncode == 1, f"{label} should have been refused"
    assert "agent/service attribution" in result.stderr


@ACCEPTED
def test_accepts_human_attribution(tmp_path: Path, label: str, message: str) -> None:
    result = _run(tmp_path, message)
    assert result.returncode == 0, f"{label} should have been accepted: {result.stderr}"


def test_authorized_trailer_is_declarable(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "feat: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
        env={"PATH": "/usr/bin:/bin", "BENCHBOX_ALLOW_AGENT_COAUTHOR": "1"},
    )
    assert result.returncode == 0, result.stderr


def test_missing_message_file_is_a_usage_error(tmp_path: Path) -> None:
    result = subprocess.run(
        ["sh", str(SCRIPT), str(tmp_path / "absent")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


