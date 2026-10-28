from __future__ import annotations

import json
from pathlib import Path

import pytest

from _project.scripts.oracle_reviewers import redaction_check

pytestmark = [pytest.mark.unit, pytest.mark.fast]

TOKEN = "tok-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH"


def _blocks() -> list[dict[str, str]]:
    return [{"type": "text", "text": TOKEN[i : i + 16]} for i in range(0, len(TOKEN), 16)]


def test_sanitize_removes_a_token_split_across_text_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMP_GATEWAY_TOKEN", TOKEN)
    path = tmp_path / "capture.json"
    path.write_text(json.dumps({"message": {"content": _blocks()}}), encoding="utf-8")
    assert redaction_check.sanitize(path, ()) is True
    texts = [block["text"] for block in json.loads(path.read_text(encoding="utf-8"))["message"]["content"]]
    assert TOKEN not in "".join(texts)
    assert "[redacted]" in "".join(texts)
    assert redaction_check.sanitize(path, ()) is False


def test_sanitize_keeps_unrelated_split_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMP_GATEWAY_TOKEN", TOKEN)
    path = tmp_path / "capture.json"
    content = [{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}]
    path.write_text(json.dumps({"content": content}), encoding="utf-8")
    assert redaction_check.sanitize(path, ()) is False
    assert json.loads(path.read_text(encoding="utf-8"))["content"] == content
