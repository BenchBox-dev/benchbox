from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "validate-submission.yml"


def _load():
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_trigger_is_pull_request_target_not_pull_request():
    data = _load()
    on = data[True]
    assert "pull_request_target" in on, "must use pull_request_target for trusted execution"
    assert "pull_request" not in on, "must replace pull_request to avoid double-run"
    trg = on["pull_request_target"]
    assert trg["branches"] == ["published-results"]
    assert "results-data/bundles/**" in trg["paths"]
    assert "results-data/corpus/**" in trg["paths"]
    assert trg["types"] == ["opened", "synchronize", "reopened"]


def test_permissions_are_read_and_pr_write():
    data = _load()
    perms = data["permissions"]
    assert perms["contents"] == "read"
    assert perms["pull-requests"] == "write"


def test_checkout_uses_trusted_base_sha():
    data = _load()
    steps = data["jobs"]["validate"]["steps"]
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout"))
    assert checkout["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"
    assert str(checkout["with"]["fetch-depth"]) in ("0", "1")


def test_shas_resolved_via_event_and_fetch_head_fallback():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "github.event.pull_request.base.sha" in text
    assert "git merge-base" not in text
    assert '"$EFFECTIVE_BASE...$EFFECTIVE_MERGE"' in text
    assert "pull/${PR_NUMBER}/merge" in text or "pull/${{ github.event.pull_request.number }}/merge" in text
    assert "--depth=1" in text
    assert "FETCH_HEAD" in text
    assert 'EFFECTIVE_MERGE="${MERGE_SHA:-HEAD}"' in text
    assert "BASE_SHA" in text and "MERGE_SHA" in text and "CORPUS_CHANGED_PATHS_FILE" in text
    assert "GITHUB_ENV" in text


def test_head_ref_fetched_fail_closed_for_parity():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "pull/${PR_NUMBER}/head" in text
    for line in text.splitlines():
        if "pull/${PR_NUMBER}/head" in line:
            assert "|| true" not in line
    assert 'git cat-file -e "$HEAD_SHA"' in text


def test_corpus_paths_producer_is_atomic_and_fails_closed():
    data = _load()
    steps = data["jobs"]["validate"]["steps"]
    corpus = next(s for s in steps if s.get("id") == "corpus-paths")
    run = corpus["run"]
    assert "CORPUS_CHANGED_PATHS_FILE.tmp" in run
    assert (
        'mv "$CORPUS_CHANGED_PATHS_FILE.tmp" "$CORPUS_CHANGED_PATHS_FILE"' in run
        or 'mv "$TMP" "$CORPUS_CHANGED_PATHS_FILE"' in run
    )
    assert ': > "' in run
    assert '"$EFFECTIVE_BASE...$EFFECTIVE_MERGE"' in run
    assert 'EFFECTIVE_BASE="$BASE_SHA"' in run and 'EFFECTIVE_MERGE="${MERGE_SHA:-HEAD}"' in run
    assert "git diff" in run and ("results-data/corpus" in run or "results-data/bundles" in run)
    assert 'rm -f "$CORPUS_CHANGED_PATHS_FILE' in run or 'rm -f "$TMP"' in run
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "--corpus-changed-paths" in text
    assert "CORPUS_CHANGED_PATHS_FILE missing" in text


def test_changed_bundles_uses_merge_sha_for_payload():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert 'git ls-tree -r --name-only "$MERGE_SHA"' in text or 'git ls-tree -r --name-only "$EFFECTIVE_MERGE"' in text
    assert 'git cat-file -e "$MERGE_SHA:' in text or 'git cat-file -e "$EFFECTIVE_MERGE:' in text
    assert "ls-tree -r --name-only HEAD" not in text
    assert 'cat-file -e "HEAD:' not in text
    assert 'git show "$MERGE_SHA:' in text or 'git show "$EFFECTIVE_MERGE:' in text
    assert "/tmp/payload" in text
    assert "CHANGED_MANIFESTS" in text
    assert "CHANGED_APPLIED" in text
    assert "CHANGED_COMPANIONS" in text
    assert "--diff-filter=ACMR" in text
    assert "--diff-filter=ACMRD" in text


def test_validator_dispatch_is_trusted_base_with_merge_payload():
    data = _load()
    steps = data["jobs"]["validate"]["steps"]
    validate = next(s for s in steps if s.get("id") == "validate")
    run = validate["run"]
    assert "validate_submission.py" in run
    assert "--corpus-changed-paths" in run
    assert "pipefail" in run
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "Reject non-maintainer vendor/ additions" in text
    assert "Reject validator or workflow changes" in text
    assert "head.repo.fork" in text
    assert "author_association" in text
    for f in [
        "scripts/validate_submission.py",
        "benchbox/validation/bundle.py",
        "scripts/generate_corpus_inventory.py",
        ".github/workflows/validate-submission.yml",
    ]:
        assert f in text


