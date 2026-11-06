from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "oracle_reviewers"
PROVENANCE = json.loads((FIXTURES / "provenance.json").read_text(encoding="utf-8"))["fixtures"]
ORIGINS = {"ci-run", "local-review", "live-jev", "captured", "derived", "hand-built"}
LEAKS = {
    "token": re.compile(
        r"sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|Bearer [A-Za-z0-9._-]{12,}|eyJ[A-Za-z0-9_-]{10,}"
    ),
    "tailnet host": re.compile(r"\.ts\.net|tailscale", re.IGNORECASE),
    "operator home path": re.compile(r"/Users/[A-Za-z0-9_.-]+|/home/(?!runner)[A-Za-z0-9_.-]+"),
    "foreign host": re.compile(
        r"https?://(?!(?:api\.github\.com|github\.com|avatars\.githubusercontent\.com|help\.github\.com|example\.com"
        r"|download\.example\.invalid|localhost|127\.0\.0\.1)(?::\d+)?\.?(?=[/?#\s\"'\\)<>]|$))[^\s/?#\"'\\)<>]+",
        re.IGNORECASE,
    ),
}
PLANTED = {
    "token": "sk-abcdef123456789",
    "tailnet host": "https://gateway.tail1234.ts.net/v1",
    "operator home path": "/Users/someone/work",
    "foreign host": "https://internal.example/x",
}


def _jsonl_events(text: str) -> list[Any]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _streamed_text(events: list[Any]) -> str:
    deltas: list[str] = []
    for event in events:
        inner = event.get("assistantMessageEvent") if isinstance(event, dict) else None
        if isinstance(inner, dict) and isinstance(inner.get("delta"), str):
            deltas.append(inner["delta"])
    return "".join(deltas)


def _leaks(text: str) -> dict[str, list[str]]:
    return {kind: pattern.findall(text)[:2] for kind, pattern in LEAKS.items() if pattern.search(text)}


def _scan(name: str, root: Path) -> dict[str, list[str]]:
    text = (root / name).read_text(encoding="utf-8")
    found = _leaks(text)
    if name.endswith(".jsonl"):
        found.update(_leaks(_streamed_text(_jsonl_events(text))))
    else:
        json.loads(text)
    return found


def _fixture_files(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".json", ".jsonl"} and path.name != "provenance.json"
    )


def test_every_fixture_states_its_source_and_redaction_result_and_none_is_stale() -> None:
    assert sorted(PROVENANCE) == _fixture_files(FIXTURES)
    for name, entry in PROVENANCE.items():
        assert entry["origin"] in ORIGINS, name
        assert entry["source"].strip() and entry["redaction"].strip(), name


@pytest.mark.parametrize(
    "text",
    [
        "https://github.com.evil.example/x",
        "http://localhost.evil.example",
        "https://github.com@internal.example/x",
        "HTTPS://internal.example/x",
        "Https://GITHUB.COM.evil.example/x",
        "https://github.com:secret@internal.example/x",
        "https://example.com-evil.net",
        "http://127.0.0.1.evil.example",
    ],
)
def test_a_host_that_only_starts_with_an_allowed_host_is_a_foreign_host(text: str) -> None:
    assert LEAKS["foreign host"].search(text)


@pytest.mark.parametrize(
    "text",
    ["https://github.com/x", "https://api.github.com/repos", "http://127.0.0.1:4000/v1", "see https://github.com."],
)
def test_an_allowed_host_is_not_a_foreign_host(text: str) -> None:
    assert not LEAKS["foreign host"].search(text)


@pytest.mark.parametrize("name", sorted(PROVENANCE))
def test_no_fixture_carries_a_secret_or_a_private_host_or_path(name: str) -> None:
    assert not _scan(name, FIXTURES)


def test_a_token_split_across_streamed_jsonl_deltas_fails_the_structural_check(tmp_path: Path) -> None:
    split = ("sk-abcdef", "123456789")
    text = "\n".join(
        json.dumps({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": part}})
        for part in split
    )
    (tmp_path / "stream.jsonl").write_text(text, encoding="utf-8")

    assert not _leaks(text)
    assert _scan("stream.jsonl", tmp_path) == {"token": ["sk-abcdef123456789"]}


@pytest.mark.parametrize("kind", sorted(PLANTED))
@pytest.mark.parametrize("name", ["omp_candidate/static_outputs.json", "omp_candidate/chain_review.jsonl"])
def test_a_planted_leak_in_a_copy_of_the_fixtures_is_caught(kind: str, name: str, tmp_path: Path) -> None:
    copy = tmp_path / "oracle_reviewers"
    shutil.copytree(FIXTURES, copy)
    assert not _scan(name, copy)
    path = copy / name
    if name.endswith(".jsonl"):
        path.write_text(path.read_text(encoding="utf-8") + json.dumps({"note": PLANTED[kind]}) + "\n", encoding="utf-8")
    else:
        path.write_text(
            json.dumps({"planted": PLANTED[kind], "original": json.loads(path.read_text(encoding="utf-8"))})
        )

    assert kind in _scan(name, copy)


def test_a_fixture_missing_from_the_provenance_manifest_is_detected(tmp_path: Path) -> None:
    copy = tmp_path / "oracle_reviewers"
    shutil.copytree(FIXTURES, copy)
    (copy / "omp_candidate" / "unlisted.json").write_text("{}", encoding="utf-8")

    assert sorted(PROVENANCE) != _fixture_files(copy)


def test_every_manifest_stream_file_resolves_inside_the_candidate_directory() -> None:
    candidate = (FIXTURES / "omp_candidate").resolve()
    streams = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))["streams"]

    assert streams
    for name, stream in streams.items():
        target = (candidate / stream["file"]).resolve()
        assert target.is_relative_to(candidate), name
        assert target.is_file(), name
