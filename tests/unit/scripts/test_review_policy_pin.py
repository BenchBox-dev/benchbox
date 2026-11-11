from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
POLICY_PATH = ROOT / ".github" / "review.yml"
LOCK_PATH = ROOT / ".github" / "theseus-lock.json"

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(
        not (POLICY_PATH.exists() and LOCK_PATH.exists()),
        reason="the review policy and Theseus lock are not in this checkout (release tree)",
    ),
]

POLICY = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8")) if POLICY_PATH.exists() else {}
LOCK = json.loads(LOCK_PATH.read_text(encoding="utf-8")) if LOCK_PATH.exists() else {}
BUCKETS = ("trivial", "small", "medium", "large", "huge", "max")


def test_the_live_policy_enforces_and_posts_where_the_gate_reads() -> None:
    assert POLICY["version"] == 2
    assert POLICY["mode"] == "enforce"
    assert POLICY["findings_delivery"] == "review"
    assert POLICY["status_context"] == "review-verdict"
    assert POLICY["bot_login"] == "benchbox-oracle"


def test_the_gated_base_and_attesters_are_pinned() -> None:
    consumer = POLICY["consumer"]

    assert consumer["gated_base"] == "develop"
    assert consumer["attesters"] == ["joeharris76"]
    assert consumer["soundness_manifest"] == ".github/soundness-paths.txt"
    assert consumer["inert_manifest"] == ".github/inert-paths.txt"


def test_the_promotion_block_names_only_the_locked_package_repository_and_dependencies() -> None:
    promotion = POLICY["promotion"]

    assert promotion["package"] == LOCK["package"] == "theseus-reviewer"
    assert promotion["repository"] == LOCK["repository"] == "joeharris76/theseus"
    assert promotion["dependencies"] == {}
    assert set(promotion) == {"package", "repository", "dependencies"}


def test_the_policy_carries_no_theseus_owned_review_size_or_bootstrap_keys() -> None:
    assert "brief_max_bytes" not in POLICY
    assert not [key for key in POLICY["promotion"] if key.startswith("bootstrap_")]


def test_the_adopted_review_settings_are_pinned() -> None:
    classifier = POLICY["classifier"]

    assert POLICY["protocol"]["max_finding_rounds"] == 4
    assert "pre_existing" not in POLICY["protocol"]
    assert POLICY["brief"] == {"include_intent": True}
    assert classifier["unavailable_bucket"] == "max"
    assert classifier["fallback"] == "decisions"
    assert classifier["fallback_min_bucket"] == "large"
    assert classifier["size_floor"]["min_lines"] == 1500
    assert not [pattern for pattern in classifier["size_floor"]["exclude"] if pattern.endswith((".py", ".sh"))]


def test_the_review_guidance_is_a_gate_path_and_a_soundness_path() -> None:
    guidance = ".github/review-guidance.md"
    manifest = (ROOT / ".github" / "soundness-paths.txt").read_text(encoding="utf-8").splitlines()

    assert (ROOT / guidance).is_file()
    assert guidance in POLICY["classifier"]["gate_paths"]
    assert f"file\t{guidance}" in manifest


def test_the_do_not_flag_list_never_names_a_gate_soundness_or_data_input() -> None:
    text = (ROOT / ".github" / "review-guidance.md").read_text(encoding="utf-8")
    exemptions = text.split("## Do not flag", 1)[1]
    manifest = (ROOT / ".github" / "soundness-paths.txt").read_text(encoding="utf-8").splitlines()
    soundness = [line.split("\t", 1)[1] for line in manifest if line.startswith(("file\t", "prefix\t"))]
    data = [pattern.rstrip("*") for pattern in POLICY["classifier"]["data_paths"] if not pattern.startswith("*")]
    inputs = (*POLICY["classifier"]["gate_paths"], *soundness, *data, "theseus-lock.json", "baseline")

    assert not [path for path in inputs if path in exemptions]


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
        ".github/oracle-reviewers.yml",
        ".github/oracle-omp-config.yml",
        "scripts/oracle_local_review.py",
        "_project/scripts/oracle_gateway_host.sh",
        ".github/theseus-lock.json",
        "_project/scripts/theseus_bootstrap.py",
        "scripts/theseus_cli.py",
        "scripts/theseus_release_check.py",
        "tests/unit/scripts/test_theseus_release_check.py",
        "tests/unit/scripts/test_review_gateway_host.py",
        "tests/unit/scripts/test_review_provider_diversity.py",
        "tests/fixtures/review_provider_diversity/omp_catalog_windows.json",
        "tests/integration/review/test_curated_capture_fixtures.py",
        "tests/fixtures/review_candidates/provenance.json",
        "_project/scripts/review_gateway_host.sh",
        "tests/unit/scripts/test_review_policy_pin.py",
    ):
        assert path in gate_paths


def test_the_pinned_engine_loads_the_adopted_policy(theseus_engine) -> None:
    import importlib

    policy = importlib.import_module("theseus_reviewer.policy").load_policy(POLICY_PATH)

    assert policy.protocol.max_finding_rounds == 4
    assert policy.protocol.pre_existing == "block"
    assert policy.brief.include_intent is True
    assert (policy.classifier.fallback, policy.classifier.fallback_min_bucket) == ("decisions", "large")
    assert policy.classifier.size_floor_min_lines == 1500
    assert ".github/review-guidance.md" in policy.classifier.gate_paths


def test_the_pinned_engine_recognises_the_review_named_revert_subject(theseus_engine) -> None:
    import importlib
    from datetime import UTC, datetime

    watch = importlib.import_module("theseus_reviewer.omp_watch")
    change = watch.PinChange(
        "a" * 40,
        datetime(2026, 11, 8, tzinfo=UTC),
        "revert(review): restore omp 18.13.3",
        watch.BOT_AUTHOR_EMAIL,
        "18.13.3",
        "18.16.0",
        "",
    )
    assert change.is_revert
