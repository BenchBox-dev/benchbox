from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from batch_loop_support import Loop, McpServer, World, checkout, submit

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.timeout(1800)]

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import pr_arm
import pr_landing as landing

LEADER_DEAD = "0" * 32


def shared(alpha: str = "None", gamma: str = "None", unrelated: str = "0") -> str:
    return f"ALPHA = {alpha}\nGAMMA = {gamma}\n\n\ndef unrelated() -> int:\n    return {unrelated}\n"


def attribute_test(attribute: str, value: str) -> str:
    return f"from src import shared\n\n\ndef test_{attribute.lower()}():\n    assert shared.{attribute} == {value}\n"


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    built = World(tmp_path_factory.mktemp("batch-loop"))
    built.build()
    try:
        yield built
    finally:
        built.close()


@pytest.fixture(scope="module")
def leader(world: World) -> McpServer:
    return world.server("leader")


@pytest.fixture(scope="module")
def outsider(world: World) -> McpServer:
    return world.server("outsider")


def green_checks(head: str) -> list[dict]:
    return [
        {
            "name": name,
            "head_sha": head,
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-11-01T00:00:00Z",
        }
        for name in landing.REQUIRED_CONTEXTS
    ]


def readiness(world: World, loop: Loop, evidence: dict | None) -> list[str]:
    head = loop.head()
    with checkout(world, loop.integration):
        base = loop.world.git(loop.integration, "rev-parse", "origin/develop")
        identity = landing.GitIdentity(
            repo=str(loop.integration),
            repository="owner/repo",
            branch=loop.branch,
            worktree=str(loop.integration),
            head=head,
            base=base,
            upstream=f"origin/{loop.branch}",
        )
        ready = landing.ReadyEvidence(
            expected_head=head,
            review_decision="APPROVED",
            dispositions_complete=True,
            check_runs=green_checks(head),
            require_batch=True,
            batch_evidence=evidence,
            repository="owner/repo",
            branch=loop.branch,
            worktree=str(loop.integration),
        )
        return landing.ready_failures(identity, head, ready, loop.integration)


def arm_refusals(world: World, loop: Loop) -> list[str]:
    with checkout(world, loop.integration):

        def run(cmd: list[str]) -> tuple[int, str]:
            proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
            return proc.returncode, proc.stdout

        return pr_arm.batch_refusals(run, loop.integration, loop.head(), 42)


def receipts(loop: Loop) -> dict:
    return loop.tracker()["receipts"]


def test_members_land_bind_rebind_and_finish(world: World, leader: McpServer, outsider: McpServer) -> None:
    loop = Loop(
        world,
        leader,
        "ba",
        {
            "m1": ["src/shared.py", "tests/test_m1.py"],
            "m2": ["src/m2.py", "tests/test_m2.py"],
            "m3": ["src/shared.py", "tests/test_m3.py"],
        },
        dependencies={"m3": ["m1"]},
    )
    loop.register()
    m1, m2, m3 = (loop.mid(name) for name in ("m1", "m2", "m3"))
    assert len(loop.reminders) == 1 and m3 in loop.reminders[0]
    assert m1 in leader.ok("show_item", id=m3)["needs"]["values"]

    refused = outsider.refused("take", "E_BATCH_OWNER", id=m1)
    assert "leader" in refused["error"]

    loop.member("m1")
    loop.member("m2")
    loop.implement("m1", {"src/shared.py": shared(alpha="1"), "tests/test_m1.py": attribute_test("ALPHA", "1")})
    loop.implement(
        "m2",
        {
            "src/m2.py": "VALUE = 2\n",
            "tests/test_m2.py": "from src import m2\n\n\ndef test_value():\n    assert m2.VALUE == 2\n",
        },
    )
    loop.check("m1", "tests/test_m1.py")
    loop.check("m2", "tests/test_m2.py")
    assert world.preflight_log.read_text(encoding="utf-8").count("BASE=") == 2

    loop.land_and_prepare("m1")
    head_after_m1 = loop.head()
    stale = world.refused(loop.integration, "batch-land", MEMBER=m2)
    assert "integration head moved since the member synced" in stale
    assert loop.head() == head_after_m1

    world.git(loop.worktrees["m2"], "merge", "--no-edit", loop.branch)
    loop.check("m2", "tests/test_m2.py")
    killed = loop.land("m2")
    merged_head = loop.head()
    merges = world.git(loop.integration, "rev-list", "--first-parent", "--merges", "--count", "HEAD")
    assert killed["status"] == "merged"

    status = loop.status()
    assert {entry["member"]: entry["state"] for entry in status["members"]}[m2] == "merged-unprepared"
    assert any(m2 in step and "not prepared" in step for step in status["next"])
    resumed = loop.land("m2")
    assert resumed["status"] == "already-merged"
    assert loop.head() == merged_head
    assert world.git(loop.integration, "rev-list", "--first-parent", "--merges", "--count", "HEAD") == merges
    loop.prepare("m2", resumed)

    loop.member("m3")
    loop.implement(
        "m3", {"src/shared.py": shared(alpha="1", gamma="3"), "tests/test_m3.py": attribute_test("GAMMA", "3")}
    )
    loop.check("m3", "tests/test_m3.py")
    claim_first = world.refused(loop.integration, "batch-land", MEMBER=m3, DEPENDENCIES=m1)
    assert "take it now, before landing" in claim_first
    loop.claim("m3")
    landed = loop.land("m3")
    assert landed["reruns"] == [{"member": m1, "paths": ["src/shared.py"], "status": "passed"}]
    assert [step["tool"] for step in landed["payload"]["steps"]] == ["prepare"]
    loop.prepare("m3", landed)

    tracker = loop.tracker()
    assert [overlap["member"] for overlap in tracker["receipts"][m3]["overlaps"]] == [m1]
    assert m1 in tracker["acceptance_required"]
    overlap_runs = [entry for entry in loop.ledger()["acceptance"][m1] if entry["reason"] == f"overlap:{m3}"]
    assert [entry["status"] for entry in overlap_runs] == ["passed"]
    assert loop.status()["acceptance_stale"] == []

    loop.publish()
    bound_head = loop.head()
    assert loop.bind(bound_head)["final_pr"]["head"] == bound_head
    different = leader.refused(
        "bind_batch_pr",
        "E_CONFLICT",
        batch_id=loop.batch,
        owner_generation=loop.generation,
        pr_repository="owner/repo",
        number=43,
        node_id="PR_kwDODisposable43",
        head=bound_head,
    )
    assert "different PR" in different["error"]
    first = loop.evidence()
    assert Path(first["path"]).is_file()
    assert readiness(world, loop, first["evidence"]) == []
    assert arm_refusals(world, loop) == []

    world.commit_files(
        loop.integration, {"src/shared.py": shared(alpha="1", gamma="3", unrelated="1")}, "fix: address review"
    )
    fixed = world.make_json(loop.integration, "batch-advance", REASON="fix")
    assert sorted(fixed["requires_acceptance"]) == [m1, m3]
    assert set(fixed["reruns"].values()) == {"passed"}
    leader.ok("advance_batch_head", **fixed["payload"]["arguments"])
    fix_head = loop.head()
    assert "not bound at the integration head" in world.refused(loop.integration, "batch-evidence")
    leader.refused(
        "bind_batch_pr",
        "E_CONFLICT",
        batch_id=loop.batch,
        owner_generation=loop.generation,
        pr_repository="owner/repo",
        number=42,
        node_id="PR_kwDODisposable42",
        head=fix_head,
    )
    loop.bind(fix_head, previous=bound_head)
    loop.publish()
    stale_ready = readiness(world, loop, first["evidence"])
    assert any(fix_head[:12] in failure or "final_head" in failure for failure in stale_ready), stale_ready
    stale_arm = arm_refusals(world, loop)
    assert stale_arm and any("head" in reason for reason in stale_arm), stale_arm
    second = loop.evidence()
    assert second["evidence"]["final_head"] == fix_head
    assert readiness(world, loop, second["evidence"]) == []

    world.push_develop(
        {
            "src/shared.py": shared(alpha="'develop'", gamma="None", unrelated="0"),
            "src/develop_only.py": "DEV = True\n",
        },
        "feat: change alpha on develop",
    )
    before_merge = loop.head()
    assert "develop merge failed" in world.refused(loop.integration, "batch-advance", REASON="develop-merge")
    assert loop.head() == before_merge
    world.git(loop.integration, "fetch", "-q", "origin", "develop")
    merge = world.run(
        ["git", "merge", "--no-ff", "--no-edit", "refs/remotes/origin/develop"], loop.integration, check=False
    )
    assert merge.returncode != 0
    (loop.integration / "src/shared.py").write_text(shared(alpha="1", gamma="3", unrelated="1"))
    world.git(loop.integration, "add", "src/shared.py")
    world.git(loop.integration, "commit", "-q", "--no-edit")
    develop_merge = loop.advance("develop-merge")
    merge_head = loop.head()
    assert develop_merge["reason"] == "develop-merge" and develop_merge["requires_acceptance"] == []
    assert "src/develop_only.py" in develop_merge["changed_files"]
    loop.bind(merge_head, previous=fix_head)
    loop.publish()
    third = loop.evidence()
    assert readiness(world, loop, third["evidence"]) == []
    assert arm_refusals(world, loop) == []
    assert "src/develop_only.py" not in third["evidence"]["changed_files"]

    for payload in third["finish_payloads"]:
        member = payload["arguments"]["id"]
        claim = leader.ok("take", id=member)["claim"]["generation"]
        arguments = {**payload["arguments"], "generation": claim}
        done = leader.ok(payload["tool"], **arguments)
        assert done["status"] == "done"
    assert loop.tracker()["lifecycle"] == "completed"


def test_silent_revert_is_stopped_at_landing_and_only_a_repaired_member_reaches_evidence(
    world: World, leader: McpServer
) -> None:
    loop = Loop(
        world,
        leader,
        "bb",
        {
            "m1": ["src/shared.py", "tests/test_m1.py"],
            "m2": ["src/shared.py", "src/m2.py", "tests/test_m2.py"],
        },
        dependencies={"m2": ["m1"]},
    )
    loop.register()
    m1, m2 = loop.mid("m1"), loop.mid("m2")
    loop.member("m1")
    loop.implement("m1", {"src/shared.py": shared(alpha="1"), "tests/test_m1.py": attribute_test("ALPHA", "1")})
    loop.check("m1", "tests/test_m1.py")
    loop.land_and_prepare("m1")

    loop.member("m2")
    loop.implement(
        "m2",
        {
            "src/shared.py": shared(alpha="None"),
            "src/m2.py": "VALUE = 2\n",
            "tests/test_m2.py": "from src import m2\n\n\ndef test_value():\n    assert m2.VALUE == 2\n",
        },
    )
    loop.check("m2", "tests/test_m2.py")
    loop.claim("m2")
    before = loop.head()
    refused = world.refused(loop.integration, "batch-land", MEMBER=m2, DEPENDENCIES=m1)
    assert f"overlap rerun failed for ['{m1}']" in refused
    assert loop.ledger()["acceptance"][m1][-1]["status"] == "failed"
    assert world.git(world.code_remote, "rev-parse", f"refs/heads/{loop.branch}") == before

    again = world.refused(loop.integration, "batch-land", MEMBER=m2, DEPENDENCIES=m1)
    assert f"overlap rerun failed for ['{m1}']" in again and "reset the integration branch" in again
    assert m2 not in loop.tracker()["receipts"]

    world.git(loop.integration, "reset", "--hard", before)
    loop.implement("m2", {"src/shared.py": shared(alpha="1", gamma="2")})
    loop.check("m2", "tests/test_m2.py")
    repaired = loop.land("m2")
    assert repaired["status"] == "merged" and repaired["reruns"][0]["status"] == "passed"
    loop.prepare("m2", repaired)
    loop.publish()
    loop.bind(loop.head())
    assert readiness(world, loop, loop.evidence()["evidence"]) == []


def test_receipt_survives_context_edit_and_resets_on_acceptance_edit(
    world: World, leader: McpServer, outsider: McpServer
) -> None:
    loop = Loop(
        world,
        leader,
        "bc",
        {"m1": ["src/m1.py", "tests/test_m1.py"], "m2": ["src/m2.py", "tests/test_m2.py"]},
        dependencies={"m2": ["m1"]},
    )
    loop.register()
    m1, m2 = loop.mid("m1"), loop.mid("m2")
    loop.member("m1")
    loop.implement(
        "m1",
        {
            "src/m1.py": "VALUE = 1\n",
            "tests/test_m1.py": "from src import m1\n\n\ndef test_value():\n    assert m1.VALUE == 1\n",
        },
    )
    loop.check("m1", "tests/test_m1.py")
    loop.land_and_prepare("m1")
    loop.member("m2")
    loop.implement(
        "m2",
        {
            "src/m2.py": "VALUE = 2\n",
            "tests/test_m2.py": "from src import m2\n\n\ndef test_value():\n    assert m2.VALUE == 2\n",
        },
    )
    loop.check("m2", "tests/test_m2.py")
    loop.land_and_prepare("m2")
    assert set(receipts(loop)) == {m1, m2}

    leader.ok("update_item", id=m1, context_append="progress note: reviewed by the leader")
    assert set(receipts(loop)) == {m1, m2}

    edited = leader.ok("update_item", id=m1, acceptance=["m1 behaves differently now"])
    assert edited["invalidated"]
    assert receipts(loop) == {}
    status = loop.status()
    assert {entry["member"]: entry["state"] for entry in status["members"]} == {
        m1: "merged-unprepared",
        m2: "merged-unprepared",
    }

    for name in ("m1", "m2"):
        if name == "m2":
            loop.claim(name)
        resumed = loop.land(name)
        assert resumed["status"] == "already-merged"
        loop.prepare(name, resumed)
    assert set(receipts(loop)) == {m1, m2}

    member_worktree = loop.worktrees["m1"]
    before = world.git(member_worktree, "rev-parse", "HEAD")
    remote_before = world.git(world.primary, "ls-remote", "--heads", "origin")
    world.gh_log.unlink(missing_ok=True)
    proc = world.make(member_worktree, "pr-open", check=False)
    assert proc.returncode != 0
    assert "this is a batch member worktree" in proc.stderr
    assert not world.gh_log.exists()
    assert world.git(member_worktree, "rev-parse", "HEAD") == before
    assert world.git(world.primary, "ls-remote", "--heads", "origin") == remote_before
    assert world.git(member_worktree, "status", "--porcelain") == ""

    outsider.refused("take", "E_BATCH_OWNER", id=m2)
    leader.ok("abort_batch", batch_id=loop.batch, owner_generation=loop.generation)
    for member in (m1, m2):
        claimed = outsider.ok("take", id=member)
        assert claimed["status"] == "active"
        outsider.ok("release", id=member, generation=claimed["claim"]["generation"])
    assert loop.tracker()["lifecycle"] == "aborted"


def test_dead_leader_is_recovered_and_the_batch_continues(world: World, leader: McpServer) -> None:
    first = world.server("leader-dead")
    loop = Loop(
        world,
        first,
        "bd",
        {"m1": ["src/m1.py", "tests/test_m1.py"], "m2": ["src/m2.py", "tests/test_m2.py"]},
    )
    loop.register()
    m1, m2 = loop.mid("m1"), loop.mid("m2")
    loop.member("m1")
    loop.implement(
        "m1",
        {
            "src/m1.py": "VALUE = 1\n",
            "tests/test_m1.py": "from src import m1\n\n\ndef test_value():\n    assert m1.VALUE == 1\n",
        },
    )
    loop.check("m1", "tests/test_m1.py")
    loop.land_and_prepare("m1")
    old_generation = loop.ledger()["owner_generation"]
    assert old_generation == loop.generation
    first.kill()

    successor = world.server("leader-successor")
    loop.leader = successor
    new_generation = "ab" * 16
    recovery = {
        "batch_id": loop.batch,
        "expected_owner": "leader-dead",
        "expected_owner_generation": old_generation,
        "new_owner_generation": new_generation,
        "reason": "the previous leader process was killed",
    }
    successor.refused("recover_batch_owner", "E_CLAIM_STALE", **{**recovery, "expected_owner_generation": LEADER_DEAD})
    successor.refused("recover_batch_owner", "E_CONFLICT", **{**recovery, "expected_owner": "someone-else"})
    successor.refused("take", "E_BATCH_OWNER", id=m2)
    recovered = successor.ok("recover_batch_owner", **recovery)
    assert recovered["owner"] == "leader-successor" and recovered["owner_epoch"] == 1

    ledger_path = loop.integration / ".todo-batch" / loop.batch / "ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["owner_generation"] = new_generation
    ledger_path.write_text(json.dumps(ledger))
    loop.generation = new_generation

    status = loop.status()
    assert {entry["member"]: entry["state"] for entry in status["members"]} == {m1: "prepared", m2: "pending"}
    assert status["owner_generation"] == "present"
    assert set(receipts(loop)) == {m1}

    loop.member("m2")
    loop.implement(
        "m2",
        {
            "src/m2.py": "VALUE = 2\n",
            "tests/test_m2.py": "from src import m2\n\n\ndef test_value():\n    assert m2.VALUE == 2\n",
        },
    )
    loop.check("m2", "tests/test_m2.py")
    loop.land_and_prepare("m2")
    assert set(receipts(loop)) == {m1, m2}

    loop.publish()
    head = loop.head()
    successor.refused(
        "bind_batch_pr",
        "E_CLAIM_STALE",
        batch_id=loop.batch,
        owner_generation=old_generation,
        pr_repository="owner/repo",
        number=42,
        node_id="PR_kwDODisposable42",
        head=head,
    )
    loop.bind(head)
    evidence = loop.evidence()
    assert readiness(world, loop, evidence["evidence"]) == []


def test_ordinary_single_item_delivery_is_unchanged(world: World, leader: McpServer, outsider: McpServer) -> None:
    leader.ok("create_item", id="solo-item", title="An ordinary item", acceptance=["it is done"])
    claimed = outsider.ok("take", id="solo-item")
    assert claimed["status"] == "active"
    finished = outsider.ok("finish", id="solo-item", generation=claimed["claim"]["generation"])
    assert finished["status"] == "done"

    created = world.run(
        [
            "make",
            "--no-print-directory",
            "worktree-create",
            "BRANCH=feat/solo-change",
            f"WORKTREE_PATH={world.root / 'solo'}",
        ],
        world.primary,
    )
    assert created.returncode == 0
    solo = world.root / "solo"
    role = world.run(["git", "config", "--worktree", "--get", "benchbox.batch.role"], solo, check=False)
    assert role.returncode == 1
    refusal = world.refused(solo, "batch-member-check")
    assert "role=none" in refusal
