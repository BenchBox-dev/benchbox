from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from _project.scripts.oracle_reviewers.policy import Policy, load_policy

REPO_ROOT = Path(__file__).resolve().parents[4]
POLICY_PATH = REPO_ROOT / ".github" / "oracle-reviewers.yml"


@pytest.fixture(scope="session")
def policy() -> Policy:
    return load_policy(POLICY_PATH)


@pytest.fixture(scope="session")
def codex_enabled_policy() -> Policy:
    policy = load_policy(POLICY_PATH)
    reviewers = {
        name: replace(reviewer, enabled=True, disabled_reason="") if reviewer.family == "codex" else reviewer
        for name, reviewer in policy.reviewers.items()
    }
    return replace(policy, reviewers=reviewers)


collect_ignore = ["test_local_review.py"] if sys.platform == "win32" else []

WINDOWS_SKIP = pytest.mark.skip(
    reason="the oracle reviewer tooling runs on Linux Actions runners: it uses POSIX paths, process groups and shebang CLIs"
)


def pytest_collection_modifyitems(config, items):
    if sys.platform != "win32":
        return
    for item in items:
        if Path(__file__).parent in Path(str(item.path)).parents:
            item.add_marker(WINDOWS_SKIP)
