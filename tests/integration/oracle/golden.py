from __future__ import annotations

import difflib
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import protocol

from .harness import PINNED_OMP, REPO_ROOT, Harness, ReviewRun, Step

GOLDEN_DIR = REPO_ROOT / "tests" / "fixtures" / "oracle_golden"
UPDATE_ENV = "ORACLE_GOLDEN_UPDATE"
FINAL_FILES = ("status.json", "comment.json", "review.json", "state/state.json", "attempt/state.json")
SHA = re.compile(r"\b[0-9a-f]{40}\b")
MOMENT = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)")
RUN_URL = re.compile(r"/actions/runs/\d+")
RUN_REF = re.compile(r"\[run \d+\]")
MARKER = re.compile(r"<!-- oracle-protocol: v1 [A-Za-z0-9_-]+ -->")
FENCE = re.compile(r"(?<![0-9a-f])[0-9a-f]{16}-(BEGIN|END) ")
RUN_UUID = re.compile(r'\\"run_uuid\\": \\"\d+\\"')
VOLATILE_KEYS = frozenset({"run_id", "run_uuid", "created_at", "omp_sha256"})


def _attempts(run: ReviewRun) -> list[Any]:
    found: list[Any] = []
    for path in sorted((run.temp / "oracle-attempts").glob("attempt-*.json")):
        try:
            found.append(json.loads(path.read_text(encoding="utf-8")))
        except ValueError:
            found.append({"unreadable": True})
    return found


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _step(step: Step) -> dict[str, Any]:
    return {"code": step.code, "outputs": dict(sorted(step.outputs.items()))}


PLAN_OUTPUTS = ("decision", "post", "pr", "head_sha")
FINALIZE_OUTPUTS = ("state", "comment", "review", "has_state")
SHADOW_PREFIX = "oracle-verdict-shadow"


def _picked(step: Step | None, keys: tuple[str, ...]) -> dict[str, Any] | None:
    if step is None:
        return None
    return {"code": step.code, "outputs": {key: step.outputs[key] for key in keys if key in step.outputs}}


def logical_steps(run: ReviewRun) -> dict[str, Any]:
    steps = run.steps
    if "publish" in steps:
        publish = {"code": steps["publish"].code, "skip": steps["publish"].outputs.get("skip", "")}
        redact = None
    else:
        guard = steps.get("guard")
        posts = [steps[name].code for name in ("status", "comment", "review") if name in steps]
        publish = {"code": max([guard.code, *posts]), "skip": guard.outputs.get("skip", "")} if guard else None
        if guard is None and "finalize" in steps:
            publish = {"code": 0, "skip": ""}
        redact = steps.get("redact")
    finalize = _picked(steps.get("finalize"), FINALIZE_OUTPUTS)
    if finalize is not None and redact is not None:
        finalize["code"] = max(finalize["code"], redact.code)
    return {"plan": _picked(steps.get("plan"), PLAN_OUTPUTS), "finalize": finalize, "publish": publish}


NOT_COMPARED = ("shadow", "classification unavailable")


def _comparable(text: str) -> str:
    return "".join(
        line for line in text.splitlines(keepends=True) if not any(marker in line for marker in NOT_COMPARED)
    )


def posted(harness: Harness, pr: int, head: str) -> dict[str, Any]:
    github = harness.github
    return {
        "statuses": [
            {
                "state": item["state"],
                "context": item["context"],
                "description": item["description"],
                "target_url": item["target_url"],
                "creator": item["creator"]["login"],
            }
            for item in github.statuses.get(head, [])
        ],
        "reviews": [
            {"body": item["body"], "commit_id": item["commit_id"], "creator": item["user"]["login"]}
            for item in github.reviews.get(pr, [])
        ],
        "threads": [
            {"path": item["path"], "comments": [comment["body"] for comment in item["comments"]]}
            for item in github.threads.get(pr, [])
        ],
        "comments": [item["body"] for item in github.comments.get(pr, [])],
        "dispatches": list(github.dispatches),
        "artifacts": sorted(item["name"] for item in github.artifacts if not item["name"].startswith(SHADOW_PREFIX)),
    }


def run_capture(harness: Harness, run: ReviewRun, pr: int, head: str) -> dict[str, Any]:
    final = run.temp / "oracle-final"
    return {
        "steps": logical_steps(run),
        "plan": run.plan,
        "final": {name: _json(final / name) for name in FINAL_FILES},
        "attempts": _attempts(run),
        "prompts": harness.omp_prompts(),
        "summary": _comparable(run.step_summary()),
        "posted": posted(harness, pr, head),
    }


def _marker(match: re.Match[str]) -> str:
    decoded = protocol.decode_marker(match.group(0))
    return f"<!-- oracle-protocol: {json.dumps(decoded, sort_keys=True)} -->"


def _scrub_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("<volatile>" if key in VOLATILE_KEYS and item is not None else _scrub_keys(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub_keys(item) for item in value]
    return value


def normalize(value: Any, harness: Harness) -> Any:
    text = json.dumps(_scrub_keys(value), indent=2, sort_keys=True, ensure_ascii=False)
    text = text.replace(str(harness.root), "<root>")
    text = text.replace(PINNED_OMP, "<omp-version>")
    text = RUN_URL.sub("/actions/runs/<run>", text)
    text = RUN_REF.sub("[run <run>]", text)
    text = MOMENT.sub("<time>", text)
    text = FENCE.sub(r"<fence>-\1 ", text)
    text = _decode_markers(text)
    text = RUN_UUID.sub(lambda _: '\\"run_uuid\\": \\"<volatile>\\"', text)
    shas = list(dict.fromkeys(SHA.findall(text)))
    for index, sha in enumerate(shas):
        text = text.replace(sha, f"<sha{index}>")
    for index, sha in enumerate(shas):
        text = text.replace(sha[:12], f"<sha{index}:12>")
    return json.loads(text)


def _decode_markers(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return json.dumps(_marker(match))[1:-1]

    return MARKER.sub(replace, text)


def _dump(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def assert_golden(name: str, value: Any, harness: Harness) -> None:
    actual = normalize(value, harness)
    path = GOLDEN_DIR / f"{name}.json"
    if os.environ.get(UPDATE_ENV):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_dump(actual), encoding="utf-8")
        return
    assert path.is_file(), f"no golden output recorded for {name}; record with {UPDATE_ENV}=1"
    expected = json.loads(path.read_text(encoding="utf-8"))
    if actual != expected:
        diff = difflib.unified_diff(
            _dump(expected).splitlines(), _dump(actual).splitlines(), "recorded", "actual", lineterm="", n=2
        )
        pytest.fail(f"{name} differs from its recorded golden output:\n" + "\n".join(diff), pytrace=False)
