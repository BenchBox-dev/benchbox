from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "scripts/site_inputs_parent.py"

spec = importlib.util.spec_from_file_location("site_inputs_parent_test", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
parent_mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = parent_mod
spec.loader.exec_module(parent_mod)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

CORE = "c" * 40
PARENT = "b" * 40
OTHER = "d" * 40


def _ancestor_ok(parent: str, core: str) -> bool:
    return True


def _ancestor_no(parent: str, core: str) -> bool:
    return False


def test_dispatched_parent_beyond_window_resolves_from_direct_lookup() -> None:
    bundled = [f"{i:040x}" for i in range(10)]
    assert PARENT not in bundled
    parent_sha, source = parent_mod.decide_parent(CORE, PARENT, bundled, _ancestor_ok, lambda sha: "bundle")
    assert parent_sha == PARENT
    assert source == "bundle"


def test_dispatched_parent_with_expired_artifact_is_refused_naming_sha() -> None:
    bundled = [f"{i:040x}" for i in range(3)]
    with pytest.raises(RuntimeError, match=PARENT):
        parent_mod.decide_parent(CORE, PARENT, bundled, _ancestor_ok, lambda sha: "expired")


def test_dispatched_parent_that_is_not_ancestor_is_refused() -> None:
    with pytest.raises(RuntimeError, match="is not an ancestor"):
        parent_mod.decide_parent(CORE, PARENT, [OTHER], _ancestor_no, lambda sha: "bundle")


def test_dispatched_parent_equal_to_core_is_refused() -> None:
    with pytest.raises(RuntimeError, match="is not an ancestor"):
        parent_mod.decide_parent(CORE, CORE, [], _ancestor_ok, lambda sha: "bundle")


def test_empty_pool_accepts_dispatched_parent_as_dispatch() -> None:
    parent_sha, source = parent_mod.decide_parent(CORE, PARENT, [], _ancestor_ok, lambda sha: "missing")
    assert parent_sha == PARENT
    assert source == "dispatch"


def test_nonempty_pool_refuses_parent_without_bundle() -> None:
    with pytest.raises(RuntimeError, match=f"no site-inputs bundle attested on parent {PARENT}"):
        parent_mod.decide_parent(CORE, PARENT, [OTHER], _ancestor_ok, lambda sha: "missing")


def test_automatic_run_takes_newest_bundled_ancestor() -> None:
    first = "e" * 40
    second = "f" * 40
    third = "a" * 40

    def is_ancestor_fn(parent: str, core: str) -> bool:
        return parent in {second, third}

    parent_sha, source = parent_mod.decide_parent(
        CORE, None, [first, second, third], is_ancestor_fn, lambda sha: "missing"
    )
    assert parent_sha == second
    assert source == "bundle"


def test_automatic_run_skips_core_sha_candidate() -> None:
    def is_ancestor_fn(parent: str, core: str) -> bool:
        return True

    parent_sha, source = parent_mod.decide_parent(CORE, None, [CORE, PARENT], is_ancestor_fn, lambda sha: "missing")
    assert parent_sha == PARENT
    assert source == "bundle"


def test_automatic_run_without_ancestor_fails_with_recovery_instruction() -> None:
    with pytest.raises(RuntimeError, match="dispatch site-inputs.yml with parent_core_sha"):
        parent_mod.decide_parent(CORE, None, [OTHER], _ancestor_no, lambda sha: "missing")


def test_automatic_run_with_empty_pool_fails_with_recovery_instruction() -> None:
    with pytest.raises(RuntimeError, match="dispatch site-inputs.yml with parent_core_sha"):
        parent_mod.decide_parent(CORE, None, [], _ancestor_ok, lambda sha: "missing")


def test_resolve_commit_rejects_unknown_ref() -> None:
    with pytest.raises(RuntimeError, match="does not name a commit"):
        parent_mod.resolve_commit("no-such-ref-for-site-inputs-parent")


TRUNK_RUN = {"databaseId": 38216829987, "headSha": CORE, "event": "push"}


def test_dispatch_build_records_numeric_trunk_run_id() -> None:
    import re

    certified = parent_mod.decide_certified_by(CORE, "", [TRUNK_RUN])
    assert re.fullmatch(r"\d+", certified)
    assert certified == str(TRUNK_RUN["databaseId"])


def test_dispatch_build_without_trunk_run_is_refused() -> None:
    with pytest.raises(RuntimeError, match="no successful trunk.yml push run"):
        parent_mod.decide_certified_by(CORE, "", [])


def test_dispatch_build_refuses_unmatched_trunk_runs() -> None:
    other_sha = dict(TRUNK_RUN, headSha=PARENT)
    scheduled = dict(TRUNK_RUN, event="workflow_dispatch")
    with pytest.raises(RuntimeError, match="no successful trunk.yml push run"):
        parent_mod.decide_certified_by(CORE, "", [other_sha, scheduled])


def test_dispatch_build_records_newest_trunk_run_id() -> None:
    older = dict(TRUNK_RUN, databaseId=111)
    newer = dict(TRUNK_RUN, databaseId=222)
    assert parent_mod.decide_certified_by(CORE, "", [newer, older]) == "222"


def test_automatic_build_keeps_triggering_run_id() -> None:
    assert parent_mod.decide_certified_by(CORE, "38344752905", []) == "38344752905"


LIVE_RECEIPT = {
    "artifact": {
        "sha256": "5082ebe408738414e5bf5e649dd87ca2544c47c3cf2a7238a63cb579779ffe33",
        "total_bytes": 626918379,
        "total_files": 6789,
    },
    "bundle_digest": "sha256:d0fdb9eedb9d9329e7516af967959d20e803645566b2cd0b5371dd1ac02a5952",
    "core_merge_to_live_seconds": 3701,
    "core_merged_at": "2026-10-14T16:48:26Z",
    "core_sha": "60bb31f185ae13517a5e4d280aa1fc25fe3929ed",
    "corpus_sha": "3fe314fe38970b71e902843fdcd74fd070a86dc2",
    "deployment_id": 7072918397,
    "explorer_read_model_version": 16,
    "gates": {
        "ok": True,
        "results": {
            "explorer_compat": "pass",
            "links": "pass",
            "origin": "skipped",
            "privacy": "pass",
            "snapshot_digest": "pass",
        },
    },
    "live_at": "2026-10-14T17:50:06.757Z",
    "mode": "deploy",
    "probes": {"attempts": 2, "errors": [], "matched": 33, "mismatched": [], "ok": True},
    "queued_at": "2026-10-14T17:44:50Z",
    "rollback_of": None,
    "run_id": "38609264325",
    "schema": "site-deploy-receipt/v1",
    "site_sha": "4eb622867d03e9543a2832e8146006513871845d",
    "snapshot_canonical_sha256": "2c1a0fbbd749aeb76ec3b03b1e9a1ea3235fba2470503d04003a35d9d9daf1d2",
    "snapshot_read_model_version": 16,
    "snapshot_sha256": "ab2fcb600026da3c6c9365439577adfe191d764002939b137d8da5f79f92b882",
    "target": "production",
}
LIVE_RECEIPT_SHA = "dcde139c3f6d0bf1ac0cf38927a9b262c250a0c98bcb89a5ca556d91fbe3cbe3"


def _site_receipt(run_id: str, target: str, core_sha: str) -> dict:
    return dict(LIVE_RECEIPT, run_id=run_id, target=target, core_sha=core_sha, deployment_id=None)


def _site_description(receipt: dict, legacy: bool = False) -> str:
    sha = parent_mod.receipt_canonical_sha256(receipt)
    if legacy:
        return f"site-deploy receipt sha256:{sha} run:{receipt['run_id']}"
    return f"site-deploy receipt sha256:{sha} run:{receipt['run_id']} target:{receipt['target']}"


def _site_api(monkeypatch: pytest.MonkeyPatch, pages: list[list[int]], statuses: dict[int, str]) -> None:
    import subprocess as subprocess_mod

    real_run = subprocess_mod.run

    def fake_run(argv: list[str], **kwargs: object) -> object:
        command = " ".join(argv[1:])
        if argv[0] == "gh" and "deployments?environment=" in command:
            import re as re_mod

            found = re_mod.search(r"[?&]page=(\d+)", command)
            assert found is not None
            page = int(found.group(1))
            ids = pages[page - 1] if page - 1 < len(pages) else []
            return subprocess_mod.CompletedProcess(argv, 0, "\n".join(str(i) for i in ids), "")
        if argv[0] == "gh" and "/statuses?" in command:
            deployment = int(command.split("deployments/")[1].split("/")[0])
            return subprocess_mod.CompletedProcess(argv, 0, statuses.get(deployment, ""), "")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess_mod, "run", fake_run)


def test_receipt_digest_matches_the_recorded_live_digest() -> None:
    assert parent_mod.receipt_canonical_sha256(LIVE_RECEIPT) == LIVE_RECEIPT_SHA


def test_deployed_parent_prefers_production_over_newer_rehearsal(monkeypatch: pytest.MonkeyPatch) -> None:
    rehearsal = _site_receipt("109", "rehearsal", "b" * 40)
    production = _site_receipt("108", "production", "a" * 40)
    _site_api(monkeypatch, [[9, 8]], {9: _site_description(rehearsal), 8: _site_description(production)})
    loaded: list[str] = []
    sha = parent_mod.site_deployed_core_sha(
        "BenchBox-dev/benchbox-site",
        download_fn=lambda repo, run_id: (loaded.append(run_id), {"109": rehearsal, "108": production}[run_id])[1],
    )
    assert sha == "a" * 40
    assert loaded == ["108"]


def test_deployed_parent_reads_target_from_legacy_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    rehearsal = _site_receipt("109", "rehearsal", "b" * 40)
    production = _site_receipt("108", "production", "a" * 40)
    _site_api(
        monkeypatch,
        [[9, 8]],
        {9: _site_description(rehearsal, legacy=True), 8: _site_description(production, legacy=True)},
    )
    by_run = {"109": rehearsal, "108": production}
    sha = parent_mod.site_deployed_core_sha(
        "BenchBox-dev/benchbox-site", download_fn=lambda repo, run_id: by_run[run_id]
    )
    assert sha == "a" * 40


def test_deployed_parent_refuses_digest_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    production = _site_receipt("108", "production", "a" * 40)
    _site_api(monkeypatch, [[8]], {8: _site_description(production)})
    tampered = dict(production, core_sha="f" * 40)
    with pytest.raises(RuntimeError, match="does not match its recorded digest"):
        parent_mod.site_deployed_core_sha("BenchBox-dev/benchbox-site", download_fn=lambda repo, run_id: tampered)


def test_deployed_parent_fails_closed_when_site_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as subprocess_mod

    def failing_run(argv: list[str], **kwargs: object) -> object:
        if argv[0] == "gh":
            return subprocess_mod.CompletedProcess(argv, 1, "", "connection refused")
        return subprocess_mod.run(argv, **kwargs)

    monkeypatch.setattr(subprocess_mod, "run", failing_run)
    with pytest.raises(RuntimeError, match="cannot read the site deployments"):
        parent_mod.site_deployed_core_sha("BenchBox-dev/benchbox-site")


def test_deployed_parent_fails_closed_without_production(monkeypatch: pytest.MonkeyPatch) -> None:
    _site_api(monkeypatch, [[]], {})
    with pytest.raises(RuntimeError, match="no production deployment is recorded"):
        parent_mod.site_deployed_core_sha("BenchBox-dev/benchbox-site", download_fn=lambda repo, run_id: LIVE_RECEIPT)


def test_deployed_parent_with_expired_bundle_is_refused() -> None:
    with pytest.raises(RuntimeError, match=f"site-inputs bundle for deployed parent {PARENT} is expired"):
        parent_mod.decide_deployed_parent(CORE, PARENT, lambda parent, core: True, lambda sha: "expired")


def test_deployed_parent_with_missing_bundle_is_refused() -> None:
    with pytest.raises(RuntimeError, match=f"site-inputs bundle for deployed parent {PARENT} is missing"):
        parent_mod.decide_deployed_parent(CORE, PARENT, lambda parent, core: True, lambda sha: "missing")


def test_deployed_parent_that_is_not_ancestor_is_refused() -> None:
    with pytest.raises(RuntimeError, match="is not an ancestor"):
        parent_mod.decide_deployed_parent(CORE, PARENT, lambda parent, core: False, lambda sha: "bundle")


def test_manual_dispatch_does_not_read_the_site(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        parent_mod, "site_deployed_core_sha", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError())
    )
    monkeypatch.setattr(parent_mod, "resolve_commit", lambda ref: PARENT)
    monkeypatch.setattr(parent_mod, "is_ancestor", lambda parent, core: True)
    monkeypatch.setattr(parent_mod, "recent_bundled_shas", lambda repo, limit: [])
    monkeypatch.setattr(parent_mod, "parent_bundle_state", lambda sha, repo: "bundle")
    monkeypatch.setattr(parent_mod, "recent_trunk_runs", lambda repo: [])
    parent_sha, parent_source, certified = parent_mod.resolve_and_decide(CORE, PARENT, "o/r", 100, "123")
    assert (parent_sha, parent_source) == (PARENT, "bundle")
    assert certified == "123"
