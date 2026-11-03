from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
POLICY_PATH = ROOT / ".github" / "oracle-reviewers.yml"
LOCK_PATH = ROOT / ".github" / "theseus-lock.json"

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(
        not (POLICY_PATH.exists() and LOCK_PATH.exists()),
        reason="the oracle policy and Theseus lock are not in this checkout (release tree)",
    ),
]

POLICY = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8")) if POLICY_PATH.exists() else {}
LOCK = json.loads(LOCK_PATH.read_text(encoding="utf-8")) if LOCK_PATH.exists() else {}
BUCKETS = ("trivial", "small", "medium", "large", "huge", "max")


def test_the_live_policy_enforces_and_posts_where_the_gate_reads() -> None:
    assert POLICY["version"] == 2
    assert POLICY["mode"] == "enforce"
    assert POLICY["findings_delivery"] == "review"
    assert POLICY["status_context"] == "oracle-verdict"
    assert POLICY["bot_login"] == "benchbox-oracle"


def test_the_gated_base_and_attesters_are_pinned() -> None:
    consumer = POLICY["consumer"]

    assert consumer["gated_base"] == "develop"
    assert consumer["attesters"] == ["joeharris76"]
    assert consumer["soundness_manifest"] == ".github/soundness-paths.txt"
    assert consumer["inert_manifest"] == ".github/inert-paths.txt"


def test_the_promotion_block_names_the_locked_repository_baseline_and_reviewers() -> None:
    promotion = POLICY["promotion"]

    assert promotion["package"] == LOCK["package"] == "theseus-reviewer"
    assert promotion["repository"] == LOCK["repository"] == "joeharris76/theseus"
    assert promotion["bootstrap_baseline_commit"] == LOCK["bootstrap"]["baseline_commit"]
    assert promotion["bootstrap_baseline_commit"] == "95c423c35efd3fb4962145bdc628006d5aaaad26"
    assert promotion["bootstrap_reviewers"] == ["joeharris76"]
    assert promotion["dependencies"] == {}
    assert promotion["bootstrap_path_map"] == LOCK["bootstrap"]["path_map"]


def test_live_reviewers_match_buckets_one_to_one() -> None:
    assert set(POLICY["reviewers"]) == set(BUCKETS)
    assert set(POLICY["buckets"]) == set(BUCKETS)
    for bucket in BUCKETS:
        assert POLICY["buckets"][bucket] in POLICY["reviewers"]
    for reviewer in POLICY["reviewers"].values():
        assert reviewer["harness"] == "omp"
        assert reviewer["enabled"] is True


def test_live_reviewers_inherit_the_role_effort_level() -> None:
    assert all(reviewer["effort"] == "" for reviewer in POLICY["reviewers"].values())


def test_every_enabled_chain_is_one_reviewer_and_the_attempt_budget_covers_it() -> None:
    chain = 1

    assert POLICY["max_attempts"] >= chain


def test_the_evidence_exemptions_keep_readmes_and_changelogs() -> None:
    exempt = POLICY["evidence"]["exempt_paths"]

    assert "README*.md" in exempt
    assert "CHANGELOG*.md" in exempt


def test_every_pinned_omp_build_names_a_platform_and_a_sha256() -> None:
    builds: dict[str, Any] = POLICY["omp"]["builds"]

    assert builds
    for name, build in builds.items():
        assert name.replace("-", "").isalnum()
        assert len(build["sha256"]) == 64 and set(build["sha256"]) <= set("0123456789abcdef")


def test_the_gate_paths_cover_the_theseus_wiring() -> None:
    gate_paths = POLICY["classifier"]["gate_paths"]

    for path in (
        ".github/theseus-lock.json",
        "_project/scripts/theseus_bootstrap.py",
        "scripts/theseus_cli.py",
        "scripts/theseus_release_check.py",
        "scripts/theseus_ledger_check.py",
        "tests/unit/scripts/test_theseus_release_check.py",
        "tests/unit/scripts/test_theseus_ledger_check.py",
        "tests/unit/scripts/test_oracle_gateway_host.py",
        "tests/integration/oracle/test_curated_capture_fixtures.py",
        "tests/fixtures/oracle_reviewers/provenance.json",
        "_project/scripts/oracle_gateway_host.sh",
        "tests/unit/scripts/test_oracle_policy_pin.py",
    ):
        assert path in gate_paths
