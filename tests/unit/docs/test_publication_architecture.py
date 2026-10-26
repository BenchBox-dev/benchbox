from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

ROOT = Path(__file__).resolve().parents[3]
ADR = ROOT / "docs/internal/adr/adr-independent-publication-authorities.md"
THREAT_MODEL = ROOT / "docs/internal/independent-publication-threat-model.md"
OPERATIONS = ROOT / "docs/internal/independent-publication-contract.md"
HOSTED_PLANNING = ROOT / "docs/internal/hosted-results-planning.md"
PHASE3_THREAT_MODEL = ROOT / "docs/reference/threat-model.md"
PHASE3_RUNBOOK = ROOT / "docs/internal/results-phase-3-runbook.md"
PHASE2_RUNBOOK = ROOT / "docs/internal/results-phase-2-runbook.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(path: Path) -> str:
    return " ".join(_text(path).split())


def test_adr_names_all_authority_surfaces_and_corpus_ownership() -> None:
    text = _normalized(ADR)
    required = {
        "Package releases",
        "Prose and site content",
        "Versioned API documentation",
        "Explorer application artifacts",
        "Accepted corpus archive",
        "Publication desired state",
        "Observed live state",
        "`published-results` owns the accepted corpus archive",
        "`develop` owns Explorer code and admission policy",
        "every validator-clean result",
        "exact `published-results` commit",
    }
    assert not (required - set(filter(lambda phrase: phrase in text, required)))


def test_states_are_orthogonal_and_only_receipt_proves_live() -> None:
    combined = _normalized(ADR) + " " + _normalized(OPERATIONS)
    for state in (
        "`accepted`",
        "`promotion_pending`",
        "`live`",
        "`promotion_failed`",
        "`withdrawn`",
        "Visibility",
        "Trust",
        "Ranking eligibility",
        "Deployment state",
    ):
        assert state in combined
    assert "Only an attested live receipt proves publication" in combined
    assert "A `published-results` merge proves acceptance only" in combined


def test_threat_model_covers_required_control_plane_failures() -> None:
    text = _text(THREAT_MODEL)
    required_headings = {
        "### Pull-request code execution and malicious data",
        "### GitHub token recursion and confused deputy behavior",
        "### Promotion races and stale completion",
        "### Rollback confusion and downgrade",
        "### Takedown abuse, delay, and resurrection",
    }
    assert required_headings <= set(text.splitlines())
    assert "Pull-request validation receives no publication secret" in text
    assert "compare-and-set" in text
    assert "One authorized maintainer may order emergency takedown" in text


def test_manual_review_and_single_maintainer_contract() -> None:
    combined = " ".join((_normalized(ADR), _normalized(THREAT_MODEL), _normalized(OPERATIONS)))
    assert "One authorized maintainer may approve normal promotion" in combined
    assert "One authorized maintainer may also order an emergency takedown" in combined
    assert "require manual maintainer review and MUST NOT auto-merge" in combined
    assert "A branch deletion alone must never be described as erasure" in combined


def test_adr_is_accepted() -> None:
    assert "Accepted (2026-08-31)" in _text(ADR)


def test_retained_publication_docs_keep_merge_and_live_separate() -> None:
    operations = _normalized(OPERATIONS)
    policy_text = " ".join(_normalized(path) for path in (ADR, THREAT_MODEL, OPERATIONS, PHASE3_RUNBOOK))

    assert "Keep desired state at `withdrawal_requested`" in operations
    assert "Record `withdrawn` only in observed live state" in operations
    assert "| withdrawal_requested | Authorized withdrawal event is present in desired state" in _text(OPERATIONS)
    assert "| withdrawn | A matching live receipt confirms" in _text(OPERATIONS)
    assert "affected results withdrawn and republish" not in _text(ADR)
    assert "withdrawn results are excluded from visible and ranking views before republish" not in _text(THREAT_MODEL)
    assert "The result is recorded as `withdrawn` only after a matching live receipt" in _normalized(THREAT_MODEL)
    assert "Sets status to `withdrawn` immediately" not in _text(PHASE3_RUNBOOK)
    assert "status becomes `withdrawn` only when a matching live receipt" in _normalized(PHASE3_RUNBOOK)
    assert "| `withdrawn` | Removed by actor or admin" not in _text(PHASE3_RUNBOOK)
    assert "is withdrawn pending investigation" not in _text(PHASE3_RUNBOOK)
    assert "Withdrawal is an orthogonal presentation state, not a trust tier" in _normalized(PHASE3_RUNBOOK)
    assert "| `published` | Ingest pipeline" not in _text(PHASE3_RUNBOOK)
    assert "`validated` → `published`" not in _text(PHASE3_RUNBOOK)
    assert "| `accepted` | Ingest pipeline commits validator-clean input" in _text(PHASE3_RUNBOOK)
    assert "| `live-receipt-issued` | Attestor confirms" in _text(PHASE3_RUNBOOK)
    for forbidden in (
        "immediate presentation withdrawal",
        "add `withdrawn` to desired state",
        "mark affected results withdrawn",
        "Sets status to `withdrawn` immediately",
        "withdrawn results are excluded from visible and ranking views before republish",
    ):
        assert forbidden not in policy_text
    assert "`readmission_requested` records an authorized desired restoration" in _normalized(ADR)
    assert "previously had a public ID retains its tombstone while readmission is pending" in _normalized(ADR)
    assert "never-public private result continues to have no public surface" in _normalized(ADR)
    assert "previously minted public ID retains its tombstone while pending" in _normalized(ADR)
    assert "never-public private result retains no public surface" in _normalized(ADR)
    assert "presentation remains tombstoned until a matching live receipt" not in _normalized(ADR)
    assert "## Authorized readmission" in _text(OPERATIONS)
    assert "Retain the public tombstone when a public ID previously existed" in operations
    assert "a never-public private result continues to have no public route or tombstone" in operations
    assert "Record presentation `active` only after a matching live receipt confirms" in operations
    assert "Yes, except never-public `private` results" in _normalized(HOSTED_PLANNING)
    assert "Yes for minted public IDs; no for never-public `private` results" in _normalized(HOSTED_PLANNING)


def test_phase3_threat_model_extends_cross_phase_contract() -> None:
    text = _normalized(PHASE3_THREAT_MODEL)

    assert "independent-publication-threat-model.md" in text
    assert "public-vendor-reported" in text
    assert "vendor-supplied" in text
    assert "manual maintainer review with no auto-merge" in text
    assert "one explicit authorized-maintainer approval" in text


def test_freeze_preserves_accepted_archive_bytes_during_rollback_and_withdrawal() -> None:
    phase2 = _normalized(PHASE2_RUNBOOK)
    phase3 = _normalized(PHASE3_RUNBOOK)

    assert "For an accepted bundle, do **not** run `git revert` against `published-results`" in phase2
    assert "Removing those bytes requires a separately approved erasure exception" in phase2
    assert "`published-results` uses squash merges" in phase2
    assert "Do not use `git log --merges` or `git revert -m`" in phase2
    assert "Accepted source bytes remain in the accepted archive indefinitely" in phase3
    assert "Withdrawal suppresses presentation and ranking only" in phase3
    assert "A 180-day purge applies only to unaccepted staging data" in phase3
    assert "Rejection is valid only before archive acceptance" in phase3
    assert "rejection must never purge accepted source bytes" in phase3
    assert "The bundle is retained for 30 days" not in phase3


def test_a0_a1_tracker_completion_evidence_decision_record() -> None:
    decision_path = ROOT / "_project/decisions/independent-publication-a0-a1-tracker-completion-evidence-2026-09-01.md"
    assert decision_path.is_file()
    text = _normalized(decision_path)
    assert "Accepted" in text
    assert "PR #1984" in text
    assert "171a665fb" in text
    assert "PR #1987" in text
    assert "independent-publication-a0-baseline-and-freeze" in text
    assert "independent-publication-a1-authority-and-threat-contract" in text
    assert "Terminal State Immutability" in text
    assert "sha256-chain-v2" in text
    assert "Post-Merge Completion Operational Invariant" in text
