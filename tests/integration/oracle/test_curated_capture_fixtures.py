from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import credentials, redaction_check, verdict

from .harness import REPO_ROOT

pytestmark = [pytest.mark.integration, pytest.mark.medium]

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "oracle_reviewers"
PROVENANCE = json.loads((FIXTURES / "provenance.json").read_text(encoding="utf-8"))["fixtures"]
CI_CAPTURE = "captures/ci_attempt.json"
LOCAL_CAPTURE = "captures/local_review.json"
ORIGINS = {"ci-run", "local-review", "live-jev", "captured", "derived", "hand-built"}
LEAKS = {
    "token": re.compile(
        r"sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|Bearer [A-Za-z0-9._-]{12,}|eyJ[A-Za-z0-9_-]{10,}"
    ),
    "tailnet host": re.compile(r"\.ts\.net|tailscale", re.IGNORECASE),
    "operator home path": re.compile(r"/Users/[A-Za-z0-9_.-]+|/home/(?!runner)[A-Za-z0-9_.-]+"),
    "foreign host": re.compile(
        r"https?://(?!api\.github\.com|github\.com|avatars\.githubusercontent\.com|help\.github\.com|example\.com"
        r"|localhost|127\.0\.0\.1)[A-Za-z0-9._:-]+"
    ),
}


def _capture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _stream(capture: dict[str, Any]) -> str:
    return "\n".join(json.dumps(event) for event in capture["events"])


def _fixture_files() -> list[str]:
    return sorted(
        path.relative_to(FIXTURES).as_posix()
        for path in FIXTURES.rglob("*")
        if path.is_file() and path.suffix in {".json", ".jsonl"} and path.name != "provenance.json"
    )


def test_every_fixture_states_its_source_and_redaction_result_and_none_is_stale() -> None:
    assert sorted(PROVENANCE) == _fixture_files()
    for name, entry in PROVENANCE.items():
        assert entry["origin"] in ORIGINS, name
        assert entry["source"].strip() and entry["redaction"].strip(), name
        if entry["origin"] in {"ci-run", "local-review"} and name.startswith("captures/"):
            assert entry.get("run_id") or entry.get("local_review_at"), name


def test_ci_run_fixtures_name_the_run_that_produced_them() -> None:
    for name, entry in PROVENANCE.items():
        if entry["origin"] == "ci-run":
            assert re.fullmatch(r"\d{9,}", entry["run_id"]), name


@pytest.mark.parametrize("name", sorted(PROVENANCE))
def test_no_fixture_carries_a_secret_or_a_private_host_or_path(name: str, tmp_path: Path) -> None:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    assert not credentials.contains(text)
    found = {kind: pattern.findall(text)[:2] for kind, pattern in LEAKS.items() if pattern.search(text)}
    assert not found, found
    if name.endswith(".json"):
        copy = tmp_path / Path(name).name
        shutil.copyfile(FIXTURES / name, copy)
        redaction_check.sanitize(copy, ())
        assert json.loads(copy.read_text(encoding="utf-8")) == json.loads(text)


def test_the_curated_ci_capture_is_the_recorded_run_and_carries_a_shippable_answer() -> None:
    entry, capture = PROVENANCE[CI_CAPTURE], _capture(CI_CAPTURE)

    assert capture["run_id"] == entry["run_id"] and capture["head_sha"] == entry["head_sha"]
    assert capture["pr"] == entry["pr"] and capture["schema"] == 1 and capture["exit_code"] == 0
    assert capture["absence"] is None and capture["terminal_error"] is None and not capture["timed_out"]
    answer = verdict.parse_output("omp", _stream(capture))
    assert answer["decision"] == "SHIP" and answer["status"] == "complete"
    types = [event["type"] for event in capture["events"]]
    assert "retry_fallback_applied" in types and "retry_fallback_succeeded" in types
    assert not any(kind.endswith("_update") for kind in types)
    results = [event["result"] for event in capture["events"] if event["type"] == "tool_execution_end"]
    assert results and all(item.startswith("[tool output withheld: ") for item in results)


def test_the_curated_local_review_capture_is_the_recorded_review_and_carries_a_shippable_answer() -> None:
    entry, capture = PROVENANCE[LOCAL_CAPTURE], _capture(LOCAL_CAPTURE)

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", entry["local_review_at"])
    assert capture["head_sha"] == entry["head_sha"] and capture["pr"] is None
    assert re.fullmatch(r"[0-9a-f]{32}", capture["run_id"])
    assert capture["schema"] == 1 and capture["exit_code"] == 0 and capture["absence"] is None
    answer = verdict.parse_output("omp", _stream(capture))
    assert answer["decision"] == "SHIP" and answer["status"] == "complete"
    results = [event["result"] for event in capture["events"] if event["type"] == "tool_execution_end"]
    assert results and all(item.startswith("[tool output withheld: ") for item in results)
