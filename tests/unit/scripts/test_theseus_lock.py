from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
LOCK = json.loads((ROOT / ".github" / "theseus-lock.json").read_text(encoding="utf-8"))
COMMIT = re.compile(r"[0-9a-f]{40}")


def test_the_vendored_matcher_is_the_copy_the_lock_pins() -> None:
    entry = LOCK["matcher"]
    data = (ROOT / entry["path"]).read_bytes()

    assert entry["path"] == "_project/scripts/soundness_paths.py"
    assert hashlib.sha256(data).hexdigest() == entry["sha256"]


def test_the_lock_names_one_reviewed_commit_for_the_source_and_the_workflows() -> None:
    assert COMMIT.fullmatch(LOCK["source_commit"])
    assert LOCK["workflow_sha"] == LOCK["source_commit"]
    assert "bootstrap" not in LOCK


def test_the_artifact_has_two_distinct_https_sources() -> None:
    urls = LOCK["artifact"]["urls"]

    assert len(set(urls)) == len(urls) >= 2
    assert all(url.startswith("https://") for url in urls)


@pytest.mark.parametrize("name", ["review-verdict.yml", "review-gate.yml"])
def test_each_caller_workflow_is_pinned_to_the_locked_commit_in_both_places(name: str) -> None:
    text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
    pins = re.findall(r"joeharris76/theseus/\.github/workflows/\w+\.yml@([0-9a-f]{40})", text)
    refs = re.findall(r"theseus_ref: ([0-9a-f]{40})", text)

    assert pins == refs == [LOCK["workflow_sha"]]


def test_the_policy_promotion_rules_name_the_locked_repository() -> None:
    policy = (ROOT / ".github" / "review.yml").read_text(encoding="utf-8")

    assert f"repository: {LOCK['repository']}" in policy


def test_the_installed_matcher_is_byte_identical_to_the_vendored_copy_and_the_lock(theseus_engine) -> None:
    import importlib

    installed = Path(importlib.import_module("theseus_reviewer.soundness_paths").__file__).read_bytes()
    vendored = (ROOT / LOCK["matcher"]["path"]).read_bytes()

    assert installed == vendored
    assert hashlib.sha256(installed).hexdigest() == LOCK["matcher"]["sha256"]


