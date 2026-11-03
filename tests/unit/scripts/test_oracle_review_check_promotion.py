from __future__ import annotations

import hashlib
import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
try:
    from _project.scripts.oracle_reviewers import promotion, protocol
    from _project.scripts.oracle_reviewers.policy import PromotionRules

    if str(REPO_ROOT / "_project/scripts") not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "_project/scripts"))
    gate = importlib.import_module("_project.scripts.oracle_review_check")
    sys.modules["oracle_review_check"] = gate
    oracle_gate = importlib.import_module("_project.scripts.oracle_gate")
    HAVE_ORACLE = True
except (ImportError, ModuleNotFoundError):
    HAVE_ORACLE = False
    promotion: Any = None
    protocol: Any = None
    PromotionRules: Any = None
    gate: Any = None
    oracle_gate: Any = None

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(not HAVE_ORACLE, reason="_project scripts are not in this checkout (release tree)"),
]
BASE_SHA = "1" * 40
HEAD = "a" * 40
OTHER_HEAD = "b" * 40
BASELINE_COMMIT = "0" * 39 + "1"
OLD_SRC_COMMIT = "2" * 40
NEW_SRC_COMMIT = "3" * 40
OLD_SRC_HASH = "5" * 64
NEW_SRC_HASH = "6" * 64
OLD_ART_HASH = "7" * 64
NEW_ART_HASH = "8" * 64
DELTA_HASH = "9" * 64
RUN = "c" * 32
ORACLE = "benchbox-oracle[bot]"
SUBMITTED_AT = "2026-11-02T12:00:00Z"
STATUS_AT = "2026-11-02T12:01:00Z"
STANDIN_AT = "2026-11-02T12:05:00Z"


def _lock_text(
    *,
    version: str,
    source_commit: str,
    source_sha256: str,
    artifact_sha256: str,
    delta_sha256: str,
    bootstrap: dict[str, Any] | None = None,
    matcher: dict[str, str] | None = None,
    artifact_urls: list[str] | None = None,
) -> str:
    urls = artifact_urls or [
        f"https://github.com/BenchBox-dev/theseus/releases/download/v{version}/theseus_reviewer-{version}-py3-none-any.whl",
        f"https://files.pythonhosted.org/packages/theseus/theseus_reviewer-{version}-py3-none-any.whl",
    ]
    payload: dict[str, Any] = {
        "schema": 1,
        "package": "theseus-reviewer",
        "version": version,
        "repository": "BenchBox-dev/theseus",
        "source_commit": source_commit,
        "source_sha256": source_sha256,
        "delta_sha256": delta_sha256,
        "artifact": {
            "sha256": artifact_sha256,
            "urls": urls,
        },
        "dependencies": [
            {
                "name": "pyyaml",
                "version": "6.0.3",
                "sha256": "f" * 64,
                "urls": ["https://files.pythonhosted.org/packages/pyyaml-6.0.3.whl"],
            }
        ],
        "workflow_sha": source_commit,
    }
    if bootstrap is not None:
        payload["bootstrap"] = bootstrap
    if matcher is not None:
        payload["matcher"] = matcher
    return json.dumps(payload, indent=2) + "\n"


BASE_LOCK = _lock_text(
    version="0.1.0",
    source_commit=OLD_SRC_COMMIT,
    source_sha256=OLD_SRC_HASH,
    artifact_sha256=OLD_ART_HASH,
    delta_sha256="e" * 64,
)
HEAD_LOCK = _lock_text(
    version="0.2.0",
    source_commit=NEW_SRC_COMMIT,
    source_sha256=NEW_SRC_HASH,
    artifact_sha256=NEW_ART_HASH,
    delta_sha256=DELTA_HASH,
)
HEAD_LOCK_DIGEST = promotion.lock_digest(promotion.parse_lock(HEAD_LOCK)) if promotion is not None else ""


def _promo_dict(lock_text: str = HEAD_LOCK, **overrides: Any) -> dict[str, Any]:
    base = {
        "v": 1,
        "head_sha": HEAD,
        "old_source_sha256": OLD_SRC_HASH,
        "new_source_sha256": NEW_SRC_HASH,
        "old_artifact_sha256": OLD_ART_HASH,
        "new_artifact_sha256": NEW_ART_HASH,
        "delta_sha256": DELTA_HASH,
        "lock_sha256": promotion.lock_digest(promotion.parse_lock(lock_text)) if promotion is not None else "",
        "bootstrap": False,
    }
    return {**base, **overrides}


def _review(
    *,
    head_sha: str = HEAD,
    decision: str = protocol.SHIP if protocol is not None else "SHIP",
    open_defects: list[dict[str, Any]] | None = None,
    promo: dict[str, Any] | None = None,
    carried: bool = False,
    kind: str = protocol.FIRST if protocol is not None else "FIRST",
    run_uuid: str = RUN,
    login: str = ORACLE,
    user_type: str = "Bot",
    last_edited_at: str | None = None,
    includes_created_edit: bool = False,
    state_header: str = "success",
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "v": protocol.MARKER_VERSION if protocol is not None else 1,
        "cycle": 1,
        "round": 1,
        "kind": kind,
        "decision": decision,
        "head_sha": head_sha,
        "base_ref": "develop",
        "reviewer": "large",
        "tier": "large",
        "strikes_after": 0,
        "open_defects": open_defects or [],
        "next_id": 1 + len(open_defects or []),
        "patch_digest": "d" * 32,
        "carried": carried,
        "summary": "",
        "run_uuid": run_uuid,
    }
    if promo is not None:
        record["promotion"] = promo
    body = (
        f"### oracle-verdict: {state_header} for `{head_sha}`\n\n"
        f"Decision: **{decision}**.\n\n"
        f"{protocol.encode_marker(record) if protocol is not None else ''}"
    )
    return {
        "id": 10,
        "login": login,
        "user_type": user_type,
        "commit_id": head_sha,
        "state": "COMMENTED",
        "submitted_at": SUBMITTED_AT,
        "body": body,
        "last_edited_at": last_edited_at,
        "includes_created_edit": includes_created_edit,
    }


def _status(state: str = "success", run_uuid: str = RUN, description_prefix: str = "large: SHIP") -> dict[str, Any]:
    suffix = f" [run {run_uuid}]" if run_uuid else ""
    return {
        "id": 20,
        "context": "oracle-verdict",
        "state": state,
        "description": f"{description_prefix}{suffix}",
        "login": ORACLE,
        "user_type": "Bot",
        "created_at": STATUS_AT,
    }


def _standin(head_sha: str = HEAD) -> dict[str, Any]:
    return {
        "login": "joeharris76",
        "user_type": "User",
        "body": f"Stand-in oracle review: APPROVE {head_sha}",
        "created_at": STANDIN_AT,
        "updated_at": STANDIN_AT,
    }


def _pull(
    repo: str = "BenchBox-dev/BenchBox", head_repo: str = "BenchBox-dev/BenchBox", changed: int = 1
) -> dict[str, Any]:
    return {
        "changed_files": changed,
        "base": {"ref": "develop", "sha": BASE_SHA},
        "head": {"sha": HEAD, "repo": {"full_name": head_repo}},
    }


def _evaluate_gate(
    monkeypatch: pytest.MonkeyPatch,
    *,
    base_lock: str | None = BASE_LOCK,
    head_lock: str | None = HEAD_LOCK,
    reviews: list[dict[str, Any]] | None = None,
    statuses: list[dict[str, Any]] | None = None,
    comments: list[dict[str, Any]] | None = None,
    files: list[str] | None = None,
    head_repo: str = "BenchBox-dev/BenchBox",
    ancestor: bool = True,
    matcher_bytes: bytes | None = None,
    bootstrap_baseline_commit: str | None = None,
    bootstrap_path_map: dict[str, str] | None = None,
    bootstrap_reviewers: tuple[str, ...] = ("joeharris76",),
    dependencies: dict[str, str] | None = None,
    force_exempt_matcher: bool = False,
) -> tuple[int, str]:
    chosen_files = [promotion.LOCK_PATH] if files is None else files

    def fake_optional_bytes(_token: str, _repo: str, path: str, ref: str) -> bytes | None:
        if path == promotion.LOCK_PATH:
            if ref in (BASE_SHA, "develop"):
                return None if base_lock is None else base_lock.encode("utf-8")
            if ref == HEAD:
                return None if head_lock is None else head_lock.encode("utf-8")
        if path == "_project/scripts/soundness_paths.py" and ref == HEAD:
            return matcher_bytes
        return None

    monkeypatch.setattr(gate, "fetch_optional_bytes", fake_optional_bytes)
    monkeypatch.setattr(gate, "_token_ancestor_check", lambda _tok, _repo: lambda _anc, _desc: ancestor)
    pull = _pull(head_repo=head_repo, changed=len(chosen_files))
    used_reviews = [] if reviews is None else reviews
    used_statuses = [_status()] if statuses is None else statuses
    is_promo, promo_refusals = gate.fetch_promotion_refusals(
        "tok",
        "BenchBox-dev/BenchBox",
        pull,
        chosen_files,
        len(chosen_files),
        used_reviews,
        statuses=used_statuses,
        rules=PromotionRules(
            bootstrap_baseline_commit=bootstrap_baseline_commit,
            bootstrap_path_map=bootstrap_path_map or {},
            bootstrap_reviewers=bootstrap_reviewers,
            dependencies={"pyyaml==6.0.3": "f" * 64} if dependencies is None else dependencies,
        ),
        comments=[] if comments is None else comments,
    )
    matcher = (
        (lambda _f: False)
        if force_exempt_matcher
        else gate.path_matcher(len(chosen_files), len(chosen_files), *oracle_gate.load_policy_rules())
    )
    return gate.decide(
        HEAD,
        chosen_files,
        used_statuses,
        matcher,
        None,
        [] if comments is None else comments,
        reviews=used_reviews,
        promotion_refusals=promo_refusals,
        is_promotion=is_promo,
    )


def test_valid_exact_head_promotion_record_with_valid_verdict_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    status, message = _evaluate_gate(monkeypatch, reviews=[_review(promo=_promo_dict())])
    assert status == gate.PASS
    assert "pass (oracle verdict" in message


def test_promotion_refusal_and_is_promotion_still_hold_even_if_exempt_matcher_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refused_status, refused_msg = _evaluate_gate(
        monkeypatch,
        reviews=[],
        statuses=[],
        comments=[_standin()],
        force_exempt_matcher=True,
    )
    assert refused_status == gate.WAITING
    assert "theseus promotion refused" in refused_msg

    no_status_code, _ = _evaluate_gate(
        monkeypatch,
        reviews=[_review(promo=_promo_dict())],
        statuses=[],
        comments=[_standin()],
        force_exempt_matcher=True,
    )
    assert no_status_code == gate.WAITING


def test_missing_promotion_record_is_refused_even_with_ship_verdict_and_standin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status, message = _evaluate_gate(
        monkeypatch,
        reviews=[_review(promo=None)],
        comments=[_standin()],
    )
    assert status == gate.WAITING
    assert "theseus promotion refused" in message
    assert "no oracle promotion evidence record" in message


def test_standin_without_oracle_review_cannot_bypass_promotion(monkeypatch: pytest.MonkeyPatch) -> None:
    status, message = _evaluate_gate(
        monkeypatch,
        reviews=[],
        statuses=[],
        comments=[_standin()],
    )
    assert status == gate.WAITING
    assert "theseus promotion refused" in message


@pytest.mark.parametrize(
    ("review_override", "expected_fragment"),
    [
        ({"head_sha": OTHER_HEAD, "promo": _promo_dict(head_sha=OTHER_HEAD)}, "no unedited oracle promotion review"),
        ({"promo": _promo_dict(old_source_sha256="0" * 64)}, "source hashes do not match"),
        ({"promo": _promo_dict(new_source_sha256="0" * 64)}, "source hashes do not match"),
        ({"promo": _promo_dict(old_artifact_sha256="0" * 64)}, "artifact hashes do not match"),
        ({"promo": _promo_dict(new_artifact_sha256="0" * 64)}, "artifact hashes do not match"),
        ({"promo": _promo_dict(delta_sha256="0" * 64)}, "delta digest does not match"),
        ({"promo": _promo_dict(lock_sha256="0" * 64)}, "lock_sha256 does not match"),
        ({"login": "other-app[bot]", "promo": _promo_dict()}, "no unedited oracle promotion review"),
        ({"login": "joeharris76", "user_type": "User", "promo": _promo_dict()}, "no unedited oracle promotion review"),
        ({"last_edited_at": STATUS_AT, "promo": _promo_dict()}, "edited after submission"),
        (
            {"carried": True, "kind": protocol.CARRY if protocol is not None else "carry", "promo": _promo_dict()},
            "cannot be carried",
        ),
    ],
)
def test_wrong_head_hashes_attester_edit_or_carry_is_refused(
    monkeypatch: pytest.MonkeyPatch, review_override: dict[str, Any], expected_fragment: str
) -> None:
    status, message = _evaluate_gate(
        monkeypatch,
        reviews=[_review(**review_override)],
        comments=[_standin()],
    )
    assert status == gate.WAITING
    assert expected_fragment in message


def test_untrusted_artifact_host_in_lock_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    evil_lock = _lock_text(
        version="0.2.0",
        source_commit=NEW_SRC_COMMIT,
        source_sha256=NEW_SRC_HASH,
        artifact_sha256=NEW_ART_HASH,
        delta_sha256=DELTA_HASH,
        artifact_urls=[
            "https://github.com/BenchBox-dev/theseus/releases/download/v0.2.0/theseus_reviewer-0.2.0-py3-none-any.whl",
            "https://attacker.example/evil.whl",
        ],
    )
    status, message = _evaluate_gate(
        monkeypatch,
        head_lock=evil_lock,
        reviews=[_review(promo=_promo_dict(lock_text=evil_lock))],
    )
    assert status == gate.WAITING
    assert "allowed_artifact_hosts" in message


def test_unresolved_finding_blocks_promotion(monkeypatch: pytest.MonkeyPatch) -> None:
    defect = {
        "id": "D1",
        "severity": "Medium",
        "file": ".oracle-promotion/new/src/theseus_reviewer/cli.py",
        "line": 12,
        "end_line": None,
        "title": "Unchecked return code",
    }
    status, message = _evaluate_gate(
        monkeypatch,
        reviews=[
            _review(
                decision=protocol.SHIP_WITH_FIXES,
                open_defects=[defect],
                promo=None,
                state_header="failure",
            )
        ],
        statuses=[_status(state="failure", description_prefix="large: SHIP WITH FIXES, 1 defect(s) to fix")],
        comments=[_standin()],
    )
    assert status == gate.WAITING
    assert "1 open defect(s)" in message


def test_valid_promotion_record_still_requires_valid_verdict_status_and_run_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    review = _review(promo=_promo_dict())
    failing_status, _ = _evaluate_gate(
        monkeypatch,
        reviews=[review],
        statuses=[_status(state="failure", description_prefix="large: DO NOT SHIP")],
        comments=[_standin()],
    )
    assert failing_status == gate.WAITING

    wrong_run_status, _ = _evaluate_gate(
        monkeypatch,
        reviews=[review],
        statuses=[_status(state="success", run_uuid="f" * 32)],
        comments=[_standin()],
    )
    assert wrong_run_status == gate.WAITING


def test_first_promotion_accepts_approved_bootstrap_baseline_and_forbids_later_bootstrap_bypass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved_map = {"_project/scripts/oracle_reviewers": "src/theseus_reviewer"}
    bootstrap = {
        "baseline_repo": "BenchBox-dev/BenchBox",
        "baseline_commit": BASELINE_COMMIT,
        "baseline_source_sha256": OLD_SRC_HASH,
        "path_map": approved_map,
        "reviewer": "joeharris76",
        "approved_head": NEW_SRC_COMMIT,
    }
    first_lock = _lock_text(
        version="0.1.0",
        source_commit=NEW_SRC_COMMIT,
        source_sha256=NEW_SRC_HASH,
        artifact_sha256=NEW_ART_HASH,
        delta_sha256=DELTA_HASH,
        bootstrap=bootstrap,
    )
    first_promo = _promo_dict(
        lock_text=first_lock,
        old_source_sha256=OLD_SRC_HASH,
        old_artifact_sha256=promotion.BOOTSTRAP_ARTIFACT_SHA256,
        bootstrap=True,
    )
    unconfigured_status, unconfigured_msg = _evaluate_gate(
        monkeypatch,
        base_lock=None,
        head_lock=first_lock,
        reviews=[_review(promo=first_promo)],
        bootstrap_baseline_commit=None,
    )
    assert unconfigured_status == gate.WAITING
    assert "requires bootstrap_baseline_commit and bootstrap_path_map" in unconfigured_msg

    status, _ = _evaluate_gate(
        monkeypatch,
        base_lock=None,
        head_lock=first_lock,
        reviews=[_review(promo=first_promo)],
        comments=[
            {
                "login": "joeharris76",
                "user_type": "User",
                "created_at": SUBMITTED_AT,
                "updated_at": SUBMITTED_AT,
                "body": promotion.bootstrap_approval_line(promotion.parse_lock(first_lock)),
            }
        ],
        ancestor=True,
        bootstrap_baseline_commit=BASELINE_COMMIT,
        bootstrap_path_map=approved_map,
    )
    assert status == gate.PASS

    wrong_baseline_status, wrong_baseline_msg = _evaluate_gate(
        monkeypatch,
        base_lock=None,
        head_lock=first_lock,
        reviews=[_review(promo=first_promo)],
        ancestor=True,
        bootstrap_baseline_commit="9" * 40,
        bootstrap_path_map=approved_map,
    )
    assert wrong_baseline_status == gate.WAITING
    assert "configured baseline commit" in wrong_baseline_msg

    non_ancestor_status, non_ancestor_msg = _evaluate_gate(
        monkeypatch,
        base_lock=None,
        head_lock=first_lock,
        reviews=[_review(promo=first_promo)],
        ancestor=False,
        bootstrap_baseline_commit=BASELINE_COMMIT,
        bootstrap_path_map=approved_map,
    )
    assert non_ancestor_status == gate.WAITING
    assert "not an ancestor" in non_ancestor_msg

    later_malformed = {**_review(promo=_promo_dict()), "id": 99, "body": "<!-- oracle-protocol: v1 broken -->"}
    malformed_status, malformed_msg = _evaluate_gate(
        monkeypatch,
        reviews=[_review(promo=_promo_dict()), later_malformed],
    )
    assert malformed_status == gate.WAITING
    assert "invalid protocol marker" in malformed_msg

    later_bypass_status, later_bypass_msg = _evaluate_gate(
        monkeypatch,
        base_lock=BASE_LOCK,
        head_lock=first_lock,
        reviews=[_review(promo=first_promo)],
    )
    assert later_bypass_status == gate.WAITING
    assert "only allowed on the first theseus promotion" in later_bypass_msg


def test_fork_promotion_and_vendored_matcher_drift_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    fork_status, fork_msg = _evaluate_gate(
        monkeypatch,
        head_repo="attacker/BenchBox",
        reviews=[_review(promo=_promo_dict())],
    )
    assert fork_status == gate.WAITING
    assert "fork pull requests cannot promote theseus" in fork_msg

    good_bytes = b"def match_rules():\n    return True\n"
    matcher_lock = _lock_text(
        version="0.2.0",
        source_commit=NEW_SRC_COMMIT,
        source_sha256=NEW_SRC_HASH,
        artifact_sha256=NEW_ART_HASH,
        delta_sha256=DELTA_HASH,
        matcher={"path": "_project/scripts/soundness_paths.py", "sha256": hashlib.sha256(good_bytes).hexdigest()},
    )
    drift_status, drift_msg = _evaluate_gate(
        monkeypatch,
        head_lock=matcher_lock,
        reviews=[_review(promo=_promo_dict(lock_text=matcher_lock))],
        matcher_bytes=b"def match_rules():\n    return False\n",
    )
    assert drift_status == gate.WAITING
    assert "vendored matcher" in drift_msg


@pytest.mark.parametrize("pins", [{}, {"pyyaml==6.0.3": "0" * 64}, {"pyyaml==6.0.2": "f" * 64}])
def test_ship_receipt_cannot_authorize_untrusted_dependency(
    monkeypatch: pytest.MonkeyPatch, pins: dict[str, str]
) -> None:
    status, message = _evaluate_gate(
        monkeypatch,
        dependencies=pins,
        reviews=[_review(promo=_promo_dict())],
    )
    assert status == gate.WAITING
    assert "not pinned in the base review policy" in message


@pytest.mark.parametrize(
    "override",
    [
        None,
        {"login": "attacker"},
        {"user_type": "Bot"},
        {"updated_at": STATUS_AT},
        {"updated_at": None},
        {"created_at": None},
        {"body": "Theseus bootstrap approval: source=" + OTHER_HEAD + " lock=" + "0" * 64},
        {"wrong_source": True},
        {"wrong_lock": True},
    ],
)
def test_bootstrap_ship_receipt_cannot_replace_authenticated_exact_lock_approval(
    monkeypatch: pytest.MonkeyPatch, override: dict[str, Any] | None
) -> None:
    path_map = {"_project/scripts/oracle_reviewers": "src/theseus_reviewer"}
    payload = json.loads(HEAD_LOCK)
    payload["bootstrap"] = {
        "baseline_repo": "BenchBox-dev/BenchBox",
        "baseline_commit": BASELINE_COMMIT,
        "baseline_source_sha256": OLD_SRC_HASH,
        "path_map": path_map,
        "reviewer": "joeharris76",
        "approved_head": NEW_SRC_COMMIT,
    }
    lock_text = json.dumps(payload)
    approval = {
        "login": "joeharris76",
        "user_type": "User",
        "created_at": SUBMITTED_AT,
        "updated_at": SUBMITTED_AT,
        "body": promotion.bootstrap_approval_line(promotion.parse_lock(lock_text)),
    }
    if override is not None:
        approval.update(override)
        if override.get("wrong_source"):
            approval["body"] = approval["body"].replace(NEW_SRC_COMMIT, OTHER_HEAD)
        if override.get("wrong_lock"):
            approval["body"] = approval["body"].replace(
                promotion.lock_digest(promotion.parse_lock(lock_text)), "0" * 64
            )
    status, message = _evaluate_gate(
        monkeypatch,
        base_lock=None,
        head_lock=lock_text,
        reviews=[
            _review(
                promo=_promo_dict(
                    lock_text=lock_text,
                    old_artifact_sha256=promotion.BOOTSTRAP_ARTIFACT_SHA256,
                    bootstrap=True,
                )
            )
        ],
        comments=[] if override is None else [approval],
        bootstrap_baseline_commit=BASELINE_COMMIT,
        bootstrap_path_map=path_map,
    )
    assert status == gate.WAITING
    assert "unedited pull request comment" in message


def test_hosted_promotion_policy_comes_from_exact_base_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    import yaml

    raw = yaml.safe_load((REPO_ROOT / ".github/oracle-reviewers.yml").read_text(encoding="utf-8"))
    raw["promotion"] = {"dependencies": {"pyyaml==6.0.3": "f" * 64}}
    requests: list[tuple[str, str, str]] = []

    def trusted_policy(_token: str, repo: str, path: str, ref: str) -> str:
        requests.append((repo, path, ref))
        return yaml.safe_dump(raw)

    monkeypatch.setattr(oracle_gate, "fetch_text", trusted_policy)
    rules = oracle_gate.load_promotion_policy("tok", "BenchBox-dev/BenchBox", BASE_SHA)
    assert rules.dependencies == {"pyyaml==6.0.3": "f" * 64}
    assert requests == [("BenchBox-dev/BenchBox", ".github/oracle-reviewers.yml", BASE_SHA)]
