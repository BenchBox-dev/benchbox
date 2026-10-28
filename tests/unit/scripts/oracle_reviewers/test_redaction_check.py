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


def _marked(title: str) -> str:
    from _project.scripts.oracle_reviewers import protocol

    record = {
        "v": protocol.MARKER_VERSION,
        "cycle": 1,
        "round": 1,
        "kind": "first",
        "decision": "SHIP_WITH_FIXES",
        "head_sha": "a" * 40,
        "base_ref": "develop",
        "reviewer": "medium",
        "tier": "medium",
        "strikes_after": 0,
        "open_defects": [{"id": "D1", "severity": "High", "file": "a.py", "line": 1, "end_line": None, "title": title}],
        "next_id": 2,
        "patch_digest": "d",
        "carried": False,
        "summary": "",
    }
    return "Body\n\n" + protocol.encode_marker(record)


def test_sanitize_fails_closed_on_a_secret_inside_an_encoded_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMP_GATEWAY_TOKEN", TOKEN)
    path = tmp_path / "review.json"
    path.write_text(json.dumps({"body": _marked(f"see {TOKEN}")}), encoding="utf-8")
    with pytest.raises(redaction_check.Unredactable):
        redaction_check.sanitize(path, ())


def test_sanitize_accepts_a_marker_without_a_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMP_GATEWAY_TOKEN", TOKEN)
    path = tmp_path / "review.json"
    path.write_text(json.dumps({"body": _marked("plain title")}), encoding="utf-8")
    assert redaction_check.sanitize(path, ()) is False
