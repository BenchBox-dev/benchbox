from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import zipfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from scripts import sqlglot_delivery as delivery
from scripts.sqlglot_delivery import DeliveryError
from scripts.sync_sqlglot_runtime import adopt_wheel
from tests.unit.scripts.test_adopt_sqlglot_wheel import build_wheel

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REAL_CONFIG = Path(__file__).resolve().parents[3] / ".github" / "sqlglot-delivery.json"
CONSUMER = "BenchBox-dev/BenchBox"
PRODUCER = "BenchBox-dev/benchbox-sqlglot"
WORKFLOW = ".github/workflows/benchbox-delivery.yml"
APP = "benchbox-sqlglot-delivery[bot]"
ENABLED = {"SQLGLOT_DELIVERY_ENABLED": "true"}
FORK_SHA = "a" * 40
UPSTREAM_SHA = "b" * 40
OLD, NEW, NEWER = "30.23.0.2", "30.23.0.3", "30.23.0.4"
BASE_SHA = "e" * 40
FORGED_SHA = "f" * 40


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def receipt_name(version: str, digest: str) -> str:
    return f"delivered benchbox-sqlglot {version} sha256:{digest}"


class FakeGitHub:
    def __init__(self, bare: Path) -> None:
        self.bare = bare
        self.runs: list[dict[str, Any]] = []
        self.jobs: dict[tuple[int, int], list[dict[str, Any]]] = {}
        self.artifacts: dict[int, list[dict[str, Any]]] = {}
        self.blobs: dict[int, bytes] = {}
        self.runs_total: int | None = None
        self.pulls: list[dict[str, Any]] = []
        self.commits: dict[int, list[dict[str, Any]]] = {}
        self.checks: dict[str, list[dict[str, Any]]] = {}
        self.timeline: dict[int, list[dict[str, Any]]] = {}
        self.edits: dict[int, list[str | None]] = {}
        self.edits_unreadable = False
        self.on_read: dict[int, list[Callable[[dict[str, Any]], None] | None]] = {}
        self.calls: list[tuple[str, str]] = []
        self.mutations: list[tuple[str, str, Any]] = []
        self.fail_once: set[tuple[str, str]] = set()
        self.next_artifact = 1000
        self.next_pull = 100

    def page(self, items: Sequence[Any], params: Mapping[str, Any]) -> list[Any]:
        size, number = int(params.get("per_page", 100)), int(params.get("page", 1))
        return list(items[(number - 1) * size : number * size])

    def request(
        self, method: str, path: str, params: Mapping[str, Any] | None = None, body: Mapping[str, Any] | None = None
    ) -> Any:
        params = params or {}
        self.calls.append((method, path))
        if method != "GET" and path != "graphql":
            self.mutations.append((method, path, body))
            if (method, path) in self.fail_once:
                self.fail_once.discard((method, path))
                raise DeliveryError(f"{method} {path} failed: injected")
        if path == f"repos/{PRODUCER}/actions/workflows/benchbox-delivery.yml/runs":
            total = self.runs_total if self.runs_total is not None else len(self.runs)
            return {"total_count": total, "workflow_runs": self.page(self.runs, params)}
        if match := re.fullmatch(rf"repos/{PRODUCER}/actions/runs/(\d+)/attempts/(\d+)/jobs", path):
            jobs = self.jobs.get((int(match[1]), int(match[2])), [])
            return {"total_count": len(jobs), "jobs": self.page(jobs, params)}
        if match := re.fullmatch(rf"repos/{PRODUCER}/actions/runs/(\d+)/artifacts", path):
            artifacts = self.artifacts.get(int(match[1]), [])
            return {"total_count": len(artifacts), "artifacts": self.page(artifacts, params)}
        if path == f"repos/{CONSUMER}/pulls" and method == "GET":
            return self.page(self.filtered(params), params)
        if path == f"repos/{CONSUMER}/pulls" and method == "POST":
            return self.create(body or {})
        if path == "graphql":
            return self.graphql(body or {})
        if match := re.fullmatch(rf"repos/{CONSUMER}/pulls/(\d+)", path):
            pull = self.pull(int(match[1]))
            if method == "PATCH":
                pull["state"] = (body or {})["state"]
            if method == "GET" and self.on_read.get(pull["number"]):
                hook = self.on_read[pull["number"]].pop(0)
                if hook is not None:
                    hook(pull)
            return copy.deepcopy(pull)
        if match := re.fullmatch(rf"repos/{CONSUMER}/pulls/(\d+)/commits", path):
            return self.page(self.commits.get(int(match[1])) or self.recorded_commits(int(match[1])), params)
        if match := re.fullmatch(rf"repos/{CONSUMER}/commits/([0-9a-f]+)/check-runs", path):
            runs = self.checks.get(match[1], [])
            return {"total_count": len(runs), "check_runs": self.page(runs, params)}
        if match := re.fullmatch(rf"repos/{CONSUMER}/issues/(\d+)/timeline", path):
            return self.page(self.timeline.get(int(match[1]), []), params)
        if re.fullmatch(rf"repos/{CONSUMER}/issues/\d+/comments", path):
            return {}
        raise AssertionError(f"unexpected request {method} {path}")

    def download(self, path: str) -> bytes:
        self.calls.append(("DOWNLOAD", path))
        match = re.fullmatch(rf"repos/{PRODUCER}/actions/artifacts/(\d+)/zip", path)
        assert match
        return self.blobs[int(match[1])]

    def recorded_commits(self, number: int) -> list[dict[str, Any]]:
        marker = delivery.parse_marker(self.pull(number)["body"])
        return [commit_of(marker["headSha"], marker["baseSha"])] if marker else []

    def forge_push(self, number: int, *, replace: bool = False, email: str = delivery.BOT_EMAIL) -> None:
        pull = self.pull(number)
        recorded = self.recorded_commits(number)
        pull["head"]["sha"] = FORGED_SHA
        forged = commit_of(FORGED_SHA, recorded[-1]["parents"][0]["sha"] if replace else recorded[-1]["sha"], email)
        self.commits[number] = [forged] if replace else [*recorded, forged]

    def graphql(self, body: Mapping[str, Any]) -> dict[str, Any]:
        if self.edits_unreadable:
            return {"data": None, "errors": [{"message": "forbidden"}]}
        wanted = self.edits.get(body["variables"]["number"], [])
        nodes = [{"editor": {"login": login} if login else None} for login in wanted]
        edits = {"nodes": nodes, "pageInfo": {"hasNextPage": False}}
        return {"data": {"repository": {"pullRequest": {"userContentEdits": edits}}}}

    def filtered(self, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        selected = []
        for pull in self.pulls:
            if params.get("state", "open") != "all" and pull["state"] != params.get("state", "open"):
                continue
            if "base" in params and pull["base"]["ref"] != params["base"]:
                continue
            if "head" in params and f"{CONSUMER.split('/')[0]}:{pull['head']['ref']}" != params["head"]:
                continue
            selected.append(copy.deepcopy(pull))
        return selected

    def pull(self, number: int) -> dict[str, Any]:
        return next(pull for pull in self.pulls if pull["number"] == number)

    def branch_sha(self, branch: str) -> str:
        return subprocess.check_output(
            ["git", "rev-parse", f"refs/heads/{branch}"], cwd=self.bare, encoding="utf-8", errors="strict"
        ).strip()

    def create(self, body: Mapping[str, Any]) -> dict[str, Any]:
        self.next_pull += 1
        return self.add_pull(
            self.next_pull,
            body["head"],
            self.branch_sha(body["head"]),
            title=body["title"],
            body=body["body"],
        )

    def add_pull(
        self,
        number: int,
        branch: str,
        sha: str,
        *,
        title: str | None = None,
        body: str | None = None,
        author: str = APP,
        labels: Sequence[str] = (),
        auto_merge: bool = False,
        state: str = "open",
        merged: bool = False,
        version: str | None = None,
        digest: str | None = None,
        draft: bool = False,
    ) -> dict[str, Any]:
        version = version or branch.removeprefix("chore/sqlglot-").split("-retry")[0]
        if body is None:
            body = delivery.render_marker(
                {
                    "version": version,
                    "forkSha": FORK_SHA,
                    "upstreamSha": UPSTREAM_SHA,
                    "sha256": digest or sha256_of(version.encode()),
                    "runId": 7,
                    "runAttempt": 1,
                    "headSha": sha,
                    "baseSha": BASE_SHA,
                }
            )
        pull = {
            "number": number,
            "state": state,
            "merged_at": "2026-11-01T00:00:00Z" if merged else None,
            "draft": draft,
            "title": title or delivery.title_for(version),
            "body": body,
            "user": {"login": author},
            "labels": [{"name": name} for name in labels],
            "auto_merge": {"merge_method": "squash"} if auto_merge else None,
            "base": {"ref": "develop"},
            "head": {"ref": branch, "sha": sha, "repo": {"full_name": CONSUMER}},
        }
        self.pulls.append(pull)
        return copy.deepcopy(pull)

    def mutated(self, method: str | None = None) -> list[tuple[str, str, Any]]:
        return [item for item in self.mutations if method is None or item[0] == method]


def commit_of(sha: str, parent: str, email: str = delivery.BOT_EMAIL) -> dict[str, Any]:
    return {
        "sha": sha,
        "parents": [{"sha": parent}],
        "commit": {"author": {"email": email}, "committer": {"email": email}},
    }


def sh(cwd: Path, *args: str) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True, stderr=subprocess.STDOUT).strip()


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.files = tmp_path / "wheels"
        self.files.mkdir()
        self.counter = 0
        self.config = delivery.load_config(REAL_CONFIG)
        self.bare = tmp_path / "origin.git"
        sh(tmp_path, "git", "init", "--bare", "-b", "develop", str(self.bare))
        self.clone = tmp_path / "clone"
        runtime = self.clone / "benchbox" / "_vendor"
        (self.clone / "scripts").mkdir(parents=True)
        self.pin_wheel = self.build(OLD)
        manifest = adopt_wheel(self.pin_wheel, runtime)
        baseline = {"schema": 2, "identity": manifest, "cases": []}
        (self.clone / "scripts" / "sqlglot_runtime_baseline.json").write_text(json.dumps(baseline, indent=2) + "\n")
        sh(self.clone, "git", "init", "-b", "develop")
        sh(self.clone, "git", "config", "user.name", "Test")
        sh(self.clone, "git", "config", "user.email", "test@example.com")
        sh(self.clone, "git", "add", ".")
        sh(self.clone, "git", "commit", "-m", "base")
        sh(self.clone, "git", "remote", "add", "origin", str(self.bare))
        sh(self.clone, "git", "push", "-u", "origin", "develop")
        self.api = FakeGitHub(self.bare)
        self.pin = json.loads((runtime / "sqlglot.json").read_text(encoding="utf-8"))
        self.deliver(OLD, 1, wheel=self.pin_wheel)

    def build(self, version: str, dirty: bool = False) -> Path:
        self.counter += 1
        directory = self.files / str(self.counter)
        directory.mkdir()
        return build_wheel(directory, version, dirty=dirty)

    def use_config(self, **changes: Any) -> None:
        raw = json.loads(REAL_CONFIG.read_text(encoding="utf-8"))
        for key, value in changes.items():
            raw[key] = value
        path = self.tmp / "config.json"
        path.write_text(json.dumps(raw))
        self.config = delivery.load_config(path)

    def deliver(
        self,
        version: str,
        run_id: int,
        *,
        wheel: Path | None = None,
        attempts: int = 1,
        receipts: Sequence[int] | None = None,
        expired: Sequence[int] = (),
        event: str = "workflow_dispatch",
        head_branch: str = "main",
        path: str = WORKFLOW,
        repository: str = PRODUCER,
        head_repository: str = PRODUCER,
        tamper: Callable[[dict[str, Any], dict[str, bytes], dict[str, Any]], None] | None = None,
        wheel_for_record: Path | None = None,
        digest_override: str | None = None,
    ) -> dict[str, Any]:
        wheel = wheel or self.build(version)
        wheel_bytes = wheel.read_bytes()
        digest = digest_override or sha256_of(wheel_bytes)
        head_sha = f"{run_id:040x}"
        self.api.runs.append(
            {
                "id": run_id,
                "path": path,
                "head_branch": head_branch,
                "event": event,
                "run_attempt": attempts,
                "head_sha": head_sha,
                "repository": {"full_name": repository},
                "head_repository": {"full_name": head_repository},
            }
        )
        for attempt in range(1, attempts + 1):
            name = receipt_name(version, digest)
            jobs = [{"name": "verify", "status": "completed", "conclusion": "success"}]
            if receipts is None or attempt in receipts:
                jobs.append({"name": name, "status": "completed", "conclusion": "success"})
            self.api.jobs[(run_id, attempt)] = jobs
            artifact_name = f"{self.config.artifact_prefix}{attempt}"
            record: dict[str, Any] = {
                "schema": 1,
                "kind": "benchbox-sqlglot-verified-delivery",
                "version": version,
                "forkSha": FORK_SHA,
                "upstreamSha": UPSTREAM_SHA,
                "wheel": {"filename": wheel.name, "sha256": digest, "size": len(wheel_bytes)},
                "sdist": {"filename": f"benchbox_sqlglot-{version}.tar.gz", "sha256": sha256_of(b"sdist")},
                "source": {"repository": PRODUCER, "event": "push", "ref": "refs/heads/main", "runId": 5},
                "delivery": {
                    "repository": PRODUCER,
                    "workflowPath": WORKFLOW,
                    "ref": "refs/heads/main",
                    "event": event,
                    "commit": head_sha,
                    "runId": run_id,
                    "runAttempt": attempt,
                    "jobName": name,
                    "artifactName": artifact_name,
                },
            }
            files = {wheel.name: wheel_bytes, f"benchbox_sqlglot-{version}.tar.gz": b"sdist"}
            artifact: dict[str, Any] = {"expired": attempt in expired, "workflow_run": {"id": run_id}}
            if tamper is not None:
                tamper(record, files, artifact)
            self.add_artifact(run_id, artifact_name, record, files, artifact)
        return {"wheel": wheel, "sha256": digest, "version": version, "run_id": run_id}

    def add_artifact(
        self, run_id: int, name: str, record: dict[str, Any], files: dict[str, bytes], artifact: dict[str, Any]
    ) -> None:
        sums = artifact.get("sums", "".join(f"{sha256_of(files[member])}  {member}\n" for member in sorted(files)))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("delivery.json", json.dumps(record))
            archive.writestr("SHA256SUMS", sums)
            for member, data in files.items():
                archive.writestr(member, data)
        blob = buffer.getvalue()
        self.api.next_artifact += 1
        self.api.blobs[self.api.next_artifact] = blob
        self.api.artifacts.setdefault(run_id, []).append(
            {
                "id": self.api.next_artifact,
                "name": name,
                "expired": artifact["expired"],
                "workflow_run": artifact["workflow_run"],
                "digest": artifact.get("digest", f"sha256:{sha256_of(blob)}"),
            }
        )

    def plan(self) -> dict[str, Any]:
        (self.tmp / "plan").mkdir(exist_ok=True)
        return delivery.build_plan(self.api, self.config, self.pin, self.tmp / "plan")

    def reset(self) -> None:
        shutil.rmtree(self.tmp / "plan", ignore_errors=True)
        shutil.rmtree(self.tmp / "prepared", ignore_errors=True)
        sh(self.clone, "git", "checkout", "develop")
        for branch in sh(self.clone, "git", "branch", "--list", "chore/*", "--format=%(refname:short)").split():
            sh(self.clone, "git", "branch", "-D", branch)

    def run_plan(self) -> tuple[int, dict[str, Any]]:
        code = delivery.run_plan(self.api, self.config, self.clone, self.tmp / "plan")
        return code, json.loads((self.tmp / "plan" / delivery.PLAN_FILE).read_text(encoding="utf-8"))

    def prepare(self) -> dict[str, Any]:
        code, plan = self.run_plan()
        assert code == 0 and plan["state"] == "adopt"
        return delivery.prepare(self.clone, self.tmp / "plan", self.tmp / "prepared", self.config, checks=None)

    def publish(self, **options: Any) -> dict[str, Any]:
        options.setdefault("environ", ENABLED)
        return delivery.publish(
            self.api,
            self.config,
            self.tmp / "prepared",
            self.clone,
            repo=CONSUMER,
            login=APP,
            **options,
        )

    def remote_branches(self) -> list[str]:
        out = sh(self.bare, "git", "for-each-ref", "--format=%(refname:short)", "refs/heads")
        return sorted(out.split())


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def test_pinned_delivery_with_nothing_newer_is_current(world: World) -> None:
    plan = world.plan()
    assert plan["state"] == "current"
    assert not [call for call in world.api.calls if call[0] == "DOWNLOAD"]


def test_a_newer_verified_delivery_is_planned_with_its_full_identity(world: World) -> None:
    delivered = world.deliver(NEW, 2)
    code, plan = world.run_plan()
    assert code == 0
    assert plan["state"] == "adopt"
    assert plan["expected"] == {
        "version": NEW,
        "forkSha": FORK_SHA,
        "upstreamSha": UPSTREAM_SHA,
        "sha256": delivered["sha256"],
        "wheel": delivered["wheel"].name,
    }
    assert plan["producer"]["runId"] == 2 and plan["producer"]["artifactName"] == "benchbox-sqlglot-delivery-a1"
    assert (world.tmp / "plan" / delivered["wheel"].name).read_bytes() == delivered["wheel"].read_bytes()


def test_the_newest_of_several_newer_deliveries_wins(world: World) -> None:
    world.deliver(NEW, 2)
    world.deliver(NEWER, 3)
    assert world.plan()["expected"]["version"] == NEWER


def test_tooling_only_producer_movement_is_not_a_new_adoption(world: World) -> None:
    world.deliver(OLD, 9, wheel=world.pin_wheel)
    plan = world.plan()
    assert plan["state"] == "current"


@pytest.mark.parametrize(
    "mutation",
    [
        {"path": ".github/workflows/other.yml"},
        {"head_branch": "feature"},
        {"repository": "attacker/benchbox-sqlglot"},
        {"head_repository": "attacker/benchbox-sqlglot"},
        {"event": "push"},
        {"event": "pull_request"},
    ],
)
def test_runs_that_are_not_trusted_producer_runs_are_never_adopted(world: World, mutation: dict[str, str]) -> None:
    world.deliver(NEW, 2, **mutation)
    plan = world.plan()
    assert plan["state"] == "current"
    assert plan["untrustedRuns"] == 1
    assert not [call for call in world.api.calls if call[0] == "DOWNLOAD"]


def test_a_run_path_qualified_with_the_trusted_ref_is_trusted(world: World) -> None:
    world.deliver(NEW, 2, path=f"{WORKFLOW}@refs/heads/main")
    assert world.plan()["state"] == "adopt"


def mutate(path: Sequence[str], value: Any) -> Callable[[dict[str, Any], dict[str, bytes], dict[str, Any]], None]:
    def apply(record: dict[str, Any], files: dict[str, bytes], artifact: dict[str, Any]) -> None:
        target = record
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return apply


def tamper_wheel(record: dict[str, Any], files: dict[str, bytes], artifact: dict[str, Any]) -> None:
    name = record["wheel"]["filename"]
    files[name] = files[name] + b"x"
    artifact["sums"] = f"{record['wheel']['sha256']}  {name}\n{sha256_of(b'sdist')}  {record['sdist']['filename']}\n"


def tamper_sums(record: dict[str, Any], files: dict[str, bytes], artifact: dict[str, Any]) -> None:
    artifact["sums"] = (
        f"{'0' * 64}  {record['wheel']['filename']}\n{sha256_of(b'sdist')}  {record['sdist']['filename']}\n"
    )


def tamper_digest(record: dict[str, Any], files: dict[str, bytes], artifact: dict[str, Any]) -> None:
    artifact["digest"] = f"sha256:{'0' * 64}"


def tamper_member(record: dict[str, Any], files: dict[str, bytes], artifact: dict[str, Any]) -> None:
    files["extra.sh"] = b"echo"


def tamper_other_run(record: dict[str, Any], files: dict[str, bytes], artifact: dict[str, Any]) -> None:
    artifact["workflow_run"] = {"id": 999}


@pytest.mark.parametrize(
    "tamper",
    [
        mutate(("delivery", "runId"), 1),
        mutate(("delivery", "runAttempt"), 9),
        mutate(("delivery", "repository"), "attacker/x"),
        mutate(("delivery", "workflowPath"), ".github/workflows/other.yml"),
        mutate(("delivery", "commit"), "0" * 40),
        mutate(("delivery", "jobName"), "verify"),
        mutate(("delivery", "ref"), "refs/heads/feature"),
        mutate(("delivery", "event"), "push"),
        mutate(("delivery", "artifactName"), "other"),
        mutate(("source", "repository"), "attacker/x"),
        mutate(("kind",), "something-else"),
        mutate(("schema",), 2),
        mutate(("version",), "30.23.0.9"),
        mutate(("forkSha",), "not-a-commit"),
        mutate(("wheel", "sha256"), "0" * 64),
        mutate(("wheel", "size"), 1),
        mutate(("wheel", "filename"), "other.whl"),
        tamper_wheel,
        tamper_sums,
        tamper_digest,
        tamper_member,
        tamper_other_run,
    ],
)
def test_a_delivery_that_does_not_verify_is_refused_not_skipped(
    world: World, tamper: Callable[[dict[str, Any], dict[str, bytes], dict[str, Any]], None]
) -> None:
    world.deliver(NEW, 2, tamper=tamper)
    with pytest.raises(DeliveryError):
        world.plan()


def test_an_expired_artifact_for_the_newest_delivery_is_refused_loudly(world: World) -> None:
    world.deliver(NEW, 2, expired=(1,))
    with pytest.raises(DeliveryError, match="artifact"):
        world.plan()


def test_a_producer_recovery_attempt_with_the_same_version_and_hash_is_accepted(world: World) -> None:
    world.deliver(NEW, 2, attempts=2, expired=(1,))
    plan = world.plan()
    assert plan["state"] == "adopt"
    assert plan["producer"]["runAttempt"] == 2
    assert plan["producer"]["artifactName"] == "benchbox-sqlglot-delivery-a2"


def test_an_older_attempt_is_used_when_the_newer_attempt_artifact_has_expired(world: World) -> None:
    world.deliver(NEW, 2, attempts=2, expired=(2,))
    assert world.plan()["producer"]["runAttempt"] == 1


def test_a_failed_first_attempt_does_not_hide_a_successful_recovery_attempt(world: World) -> None:
    world.deliver(NEW, 2, attempts=2, receipts=(2,))
    assert world.plan()["producer"]["runAttempt"] == 2


def test_the_newest_run_carrying_the_receipt_is_preferred(world: World) -> None:
    first = world.deliver(NEW, 2)
    world.deliver(NEW, 3, wheel=first["wheel"])
    assert world.plan()["producer"]["runId"] == 3


def test_a_version_with_two_hashes_is_a_producer_exception_that_names_the_fork(world: World) -> None:
    world.deliver(NEW, 2)
    world.deliver(NEW, 3, wheel=world.build(NEW, dirty=True))
    code, plan = world.run_plan()
    assert code == delivery.EXIT_EXCEPTION
    assert plan["state"] == "exception"
    assert PRODUCER in plan["message"] and NEW in plan["message"]


def test_a_collision_on_a_version_core_will_not_adopt_does_not_block_a_newer_one(world: World) -> None:
    world.deliver("30.23.0.1", 2)
    world.deliver("30.23.0.1", 3, wheel=world.build("30.23.0.1", dirty=True))
    world.deliver(NEW, 4)
    world.deliver(NEW, 5, wheel=world.build(NEW, dirty=True))
    world.deliver(NEWER, 6)
    assert world.plan()["expected"]["version"] == NEWER


def test_a_collision_below_the_pin_leaves_core_current(world: World) -> None:
    world.deliver("30.23.0.1", 2)
    world.deliver("30.23.0.1", 3, wheel=world.build("30.23.0.1", dirty=True))
    assert world.plan()["state"] == "current"


def test_the_pinned_version_with_a_different_hash_is_a_producer_exception(world: World) -> None:
    world.api.runs.clear()
    world.deliver(OLD, 2, wheel=world.build(OLD, dirty=True))
    code, plan = world.run_plan()
    assert code == delivery.EXIT_EXCEPTION
    assert plan["state"] == "exception"


def test_a_run_listing_that_disagrees_with_its_total_count_is_refused(world: World) -> None:
    world.deliver(NEW, 2)
    world.api.runs_total = 99
    with pytest.raises(DeliveryError, match="total_count"):
        world.plan()


def test_runs_are_listed_across_every_page(world: World) -> None:
    for number in range(200, 330):
        world.deliver(OLD, number, wheel=world.pin_wheel, event="push")
    world.deliver(NEW, 2)
    assert world.plan()["expected"]["version"] == NEW


def test_a_suppressed_pair_is_skipped_for_the_next_best_delivery(world: World) -> None:
    world.deliver(NEW, 2)
    newer = world.deliver(NEWER, 3)
    entry = {"version": NEWER, "forkSha": FORK_SHA, "sha256": newer["sha256"], "reason": "reverted"}
    world.use_config(suppressed=[entry])
    plan = world.plan()
    assert plan["expected"]["version"] == NEW
    assert plan["skippedSuppressed"] == [{"version": NEWER, "forkSha": FORK_SHA, "sha256": newer["sha256"]}]


def test_a_suppressed_delivery_whose_artifact_expired_is_skipped_and_the_next_older_one_is_planned(
    world: World,
) -> None:
    world.deliver(NEW, 2)
    newer = world.deliver(NEWER, 3, expired=(1,))
    world.use_config(suppressed=[{"version": NEWER, "sha256": newer["sha256"], "reason": "reverted"}])
    plan = world.plan()
    assert plan["state"] == "adopt" and plan["expected"]["version"] == NEW
    assert plan["skippedSuppressed"] == [{"version": NEWER, "sha256": newer["sha256"]}]
    assert not [call for call in world.api.calls if "/runs/3/artifacts" in call[1]]


def test_a_suppressed_delivery_whose_record_no_longer_verifies_is_skipped(world: World) -> None:
    broken = world.deliver(NEWER, 3, tamper=mutate(("kind",), "other"))
    world.use_config(suppressed=[{"version": NEWER, "sha256": broken["sha256"], "reason": "reverted"}])
    assert world.plan()["state"] == "current"


def test_a_suppression_for_another_hash_of_the_version_does_not_hide_the_delivery(world: World) -> None:
    world.deliver(NEWER, 3)
    world.use_config(suppressed=[{"version": NEWER, "sha256": "0" * 64, "reason": "reverted"}])
    assert world.plan()["expected"]["version"] == NEWER


def test_a_suppressed_only_delivery_leaves_core_current(world: World) -> None:
    delivered = world.deliver(NEW, 2)
    world.use_config(suppressed=[{"version": NEW, "sha256": delivered["sha256"], "reason": "reverted"}])
    assert world.plan()["state"] == "current"


@pytest.mark.parametrize(
    "entry",
    [
        {"version": NEW, "sha256": "0" * 64, "reason": " "},
        {"sha256": "0" * 64, "reason": "reverted"},
        {"version": NEW, "sha256": "0" * 64, "forkSha": "not-a-commit", "reason": "reverted"},
    ],
)
def test_a_malformed_suppressed_entry_is_not_a_trust_root(world: World, entry: dict[str, str]) -> None:
    with pytest.raises(DeliveryError, match="trust root"):
        world.use_config(suppressed=[entry])


def test_prepare_emits_a_bundle_and_a_marked_pull_request_limited_to_the_adoption_paths(world: World) -> None:
    delivered = world.deliver(NEW, 2)
    prepared = world.prepare()
    assert prepared["branch"] == f"chore/sqlglot-{NEW}"
    assert prepared["title"] == f"chore(deps): adopt benchbox-sqlglot {NEW}"
    assert sorted(prepared["paths"]) == [
        "benchbox/_vendor/SQLGLOT_LICENSE.txt",
        "benchbox/_vendor/benchbox_sqlglot-30.23.0.2-py3-none-any.whl.zip",
        f"benchbox/_vendor/benchbox_sqlglot-{NEW}-py3-none-any.whl.zip",
        "benchbox/_vendor/sqlglot.json",
        delivery.fragment_for(NEW),
        "scripts/sqlglot_runtime_baseline.json",
    ]
    fragment = json.loads(sh(world.clone, "git", "show", f"{prepared['head']}:{delivery.fragment_for(NEW)}"))
    assert fragment["entries"][0]["category"] == "changed" and NEW in fragment["entries"][0]["text"]
    marker = delivery.parse_marker(prepared["body"])
    assert marker == {
        "version": NEW,
        "forkSha": FORK_SHA,
        "upstreamSha": UPSTREAM_SHA,
        "sha256": delivered["sha256"],
        "runId": 2,
        "runAttempt": 1,
        "headSha": prepared["head"],
        "baseSha": prepared["base"],
    }
    pin = json.loads(sh(world.clone, "git", "show", f"{prepared['head']}:benchbox/_vendor/sqlglot.json"))
    assert pin["sha256"] == delivered["sha256"]
    assert sh(world.clone, "git", "bundle", "verify", str(world.tmp / "prepared" / delivery.BUNDLE_FILE))
    assert sh(world.clone, "git", "status", "--porcelain") == ""


def test_prepare_refuses_a_wheel_that_does_not_match_the_plan_and_writes_no_bundle(world: World) -> None:
    world.deliver(NEW, 2)
    code, plan = world.run_plan()
    assert code == 0
    plan["expected"]["sha256"] = "0" * 64
    (world.tmp / "plan" / delivery.PLAN_FILE).write_text(json.dumps(plan))
    with pytest.raises(DeliveryError, match="plan hash"):
        delivery.prepare(world.clone, world.tmp / "plan", world.tmp / "prepared", world.config, checks=None)
    assert not (world.tmp / "prepared" / delivery.BUNDLE_FILE).exists()


def test_prepare_refuses_an_adopter_that_touches_anything_else(world: World) -> None:
    world.deliver(NEW, 2)
    world.run_plan()

    def stray(root: Path) -> None:
        (root / "scripts" / "stray.py").write_text("print('x')\n")

    with pytest.raises(DeliveryError, match="outside the allowed set"):
        delivery.prepare(world.clone, world.tmp / "plan", world.tmp / "prepared", world.config, checks=stray)
    assert not (world.tmp / "prepared" / delivery.BUNDLE_FILE).exists()


def test_prepare_refuses_an_adoption_that_changes_expected_outcomes(world: World) -> None:
    world.deliver(NEW, 2)
    world.run_plan()

    def rewrite_outcomes(root: Path) -> None:
        path = root / delivery.BASELINE
        baseline = json.loads(path.read_text(encoding="utf-8"))
        baseline["cases"] = [{"id": "changed-outcome"}]
        path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(DeliveryError, match="expected outcomes"):
        delivery.prepare(world.clone, world.tmp / "plan", world.tmp / "prepared", world.config, checks=rewrite_outcomes)
    assert not (world.tmp / "prepared" / delivery.BUNDLE_FILE).exists()


def test_a_wheel_built_for_another_version_is_refused(world: World) -> None:
    world.deliver(NEWER, 2, wheel=world.build(NEW))
    with pytest.raises(DeliveryError):
        world.plan()


def test_publish_pushes_the_branch_and_opens_one_marked_pull_request(world: World) -> None:
    world.deliver(NEW, 2)
    prepared = world.prepare()
    result = world.publish()
    assert result["outcome"] == "created"
    assert world.remote_branches() == ["chore/sqlglot-30.23.0.3", "develop"]
    assert world.api.branch_sha(prepared["branch"]) == prepared["head"]
    posts = world.api.mutated("POST")
    assert [path for _, path, _ in posts] == [f"repos/{CONSUMER}/pulls"]
    body = posts[0][2]
    assert body["base"] == "develop" and body["head"] == prepared["branch"] and body["title"] == prepared["title"]
    assert delivery.parse_marker(body["body"])["sha256"] == world.plan()["expected"]["sha256"]


def test_a_duplicate_poll_changes_nothing(world: World) -> None:
    world.deliver(NEW, 2)
    world.prepare()
    world.publish()
    world.api.mutations.clear()
    again = world.publish()
    assert again["outcome"] == "exists"
    assert not world.api.mutations
    assert len(world.api.pulls) == 1


def test_a_tooling_only_producer_run_after_publication_changes_nothing(world: World) -> None:
    first = world.deliver(NEW, 2)
    world.prepare()
    world.publish()
    world.api.mutations.clear()
    world.deliver(NEW, 3, wheel=first["wheel"])
    world.reset()
    world.prepare()
    assert world.publish()["outcome"] == "exists"
    assert not world.api.mutations


def test_an_interrupted_run_resumes_from_the_pushed_branch(world: World) -> None:
    world.deliver(NEW, 2)
    world.prepare()
    world.api.fail_once.add(("POST", f"repos/{CONSUMER}/pulls"))
    with pytest.raises(DeliveryError, match="injected"):
        world.publish()
    assert "chore/sqlglot-30.23.0.3" in world.remote_branches() and not world.api.pulls
    pushed = world.api.branch_sha("chore/sqlglot-30.23.0.3")
    resumed = world.publish()
    assert resumed["actions"][0]["do"] == "resume-branch"
    assert len(world.api.pulls) == 1
    assert world.api.branch_sha("chore/sqlglot-30.23.0.3") == pushed


def test_a_fresh_preparation_on_an_unmoved_develop_resumes_the_same_commit(world: World) -> None:
    world.deliver(NEW, 2)
    prepared = world.prepare()
    world.api.fail_once.add(("POST", f"repos/{CONSUMER}/pulls"))
    with pytest.raises(DeliveryError):
        world.publish()
    world.reset()
    assert world.prepare()["head"] == prepared["head"]
    assert world.publish()["actions"][0]["do"] == "resume-branch"
    assert world.api.branch_sha("chore/sqlglot-30.23.0.3") == prepared["head"]
    assert len(world.api.pulls) == 1


def test_a_branch_left_by_a_preparation_on_an_older_develop_is_refused_not_overwritten(world: World) -> None:
    world.deliver(NEW, 2)
    world.prepare()
    world.api.fail_once.add(("POST", f"repos/{CONSUMER}/pulls"))
    with pytest.raises(DeliveryError):
        world.publish()
    pushed = world.api.branch_sha("chore/sqlglot-30.23.0.3")
    world.reset()
    sh(world.clone, "git", "commit", "--allow-empty", "-m", "unrelated develop movement")
    sh(world.clone, "git", "push", "origin", "develop")
    world.prepare()
    with pytest.raises(DeliveryError, match="not a clean adoption"):
        world.publish()
    assert world.api.branch_sha("chore/sqlglot-30.23.0.3") == pushed
    assert not world.api.pulls


def test_an_interruption_before_the_old_pull_request_is_closed_resumes_the_closure(world: World) -> None:
    world.deliver(NEW, 2)
    world.deliver(NEWER, 3)
    sha = sh(world.clone, "git", "rev-parse", "HEAD")
    world.api.add_pull(41, f"chore/sqlglot-{NEW}", sha)
    world.prepare()
    world.api.fail_once.add(("PATCH", f"repos/{CONSUMER}/pulls/41"))
    with pytest.raises(DeliveryError, match="injected"):
        world.publish()
    assert [pull["state"] for pull in world.api.pulls if pull["number"] == 41] == ["open"]
    again = world.publish()
    assert again["outcome"] == "exists"
    assert world.api.pull(41)["state"] == "closed"


def test_a_newer_delivery_supersedes_an_older_unarmed_unheld_automation_pull_request(world: World) -> None:
    world.deliver(NEW, 2)
    world.deliver(NEWER, 3)
    old = world.api.add_pull(41, f"chore/sqlglot-{NEW}", "1" * 40)
    world.prepare()
    result = world.publish()
    assert result["outcome"] == "created"
    assert world.api.pull(41)["state"] == "closed"
    open_numbers = [pull["number"] for pull in world.api.pulls if pull["state"] == "open"]
    assert len(open_numbers) == 1 and open_numbers != [old["number"]]
    assert any(path.endswith("/issues/41/comments") for _, path, _ in world.api.mutations)


def test_an_open_pull_request_for_a_suppressed_pair_is_closed_when_the_next_delivery_publishes(world: World) -> None:
    old = world.deliver(NEW, 2)
    world.prepare()
    world.publish()
    suppressed = world.api.pulls[0]["number"]
    world.deliver(NEWER, 3)
    world.use_config(suppressed=[{"version": NEW, "sha256": old["sha256"], "reason": "reverted"}])
    world.reset()
    world.prepare()
    result = world.publish()
    assert result["outcome"] == "created"
    assert world.api.pull(suppressed)["state"] == "closed"
    assert any(action["do"] == "close-suppressed" for action in result["actions"])


@pytest.mark.parametrize("preserved", ["armed", "green", "held"])
def test_an_older_armed_green_or_held_pull_request_is_preserved_and_the_newer_delivery_waits(
    world: World, preserved: str
) -> None:
    world.deliver(NEW, 2)
    world.deliver(NEWER, 3)
    world.api.add_pull(
        41,
        f"chore/sqlglot-{NEW}",
        "1" * 40,
        auto_merge=preserved == "armed",
        labels=("no-auto-merge",) if preserved == "held" else (),
    )
    if preserved == "green":
        world.api.checks["1" * 40] = [{"status": "completed", "conclusion": "success"}]
    world.prepare()
    result = world.publish()
    assert result["outcome"] == "waiting" and result["preserved"] == [41]
    assert not world.api.mutations
    assert world.remote_branches() == ["develop"]


def test_a_burst_of_deliveries_keeps_one_active_pull_request(world: World) -> None:
    world.deliver(NEW, 2)
    world.prepare()
    world.publish()
    created = [pull["number"] for pull in world.api.pulls]
    for version, run_id in ((NEWER, 3), ("30.23.0.5", 4)):
        world.deliver(version, run_id)
        world.reset()
        world.prepare()
        world.publish()
        opened = [pull for pull in world.api.pulls if pull["state"] == "open"]
        assert len(opened) == 1 and opened[0]["number"] not in created
        created.append(opened[0]["number"])


def test_human_authored_and_human_modified_pull_requests_are_never_touched(world: World) -> None:
    world.deliver(NEW, 2)
    world.deliver(NEWER, 3)
    world.api.add_pull(41, "chore/sqlglot-30.23.0.1", "1" * 40, author="a-human")
    world.api.add_pull(42, f"chore/sqlglot-{NEW}", "2" * 40)
    world.api.forge_push(42)
    world.api.add_pull(44, "chore/sqlglot-30.23.0.1-c", "4" * 40)
    world.api.edits[44] = ["a-human"]
    world.api.add_pull(43, "chore/sqlglot-30.23.0.1-b", "3" * 40, title="chore: my own title")
    world.prepare()
    result = world.publish()
    assert result["outcome"] == "created"
    touched = {path for _, path, _ in world.api.mutations}
    assert not [path for path in touched if re.search(r"/(41|42|43)(/|$)", path)]
    assert all(world.api.pull(number)["state"] == "open" for number in (41, 42, 43))


def test_a_pull_request_that_lost_its_marker_is_not_automation(world: World) -> None:
    world.deliver(NEW, 2)
    world.deliver(NEWER, 3)
    world.api.add_pull(41, f"chore/sqlglot-{NEW}", "1" * 40, body="edited by hand")
    world.prepare()
    world.publish()
    assert world.api.pull(41)["state"] == "open"


def test_a_pair_whose_pull_request_was_closed_unmerged_is_never_reproposed(world: World) -> None:
    delivered = world.deliver(NEW, 2)
    world.api.add_pull(41, f"chore/sqlglot-{NEW}", "1" * 40, state="closed", digest=delivered["sha256"])
    world.prepare()
    result = world.publish()
    assert result["outcome"] == "closed-unmerged"
    assert not world.api.mutations and world.remote_branches() == ["develop"]


def test_an_explicit_retry_proposes_a_closed_pair_again_on_a_fresh_branch(world: World) -> None:
    delivered = world.deliver(NEW, 2)
    world.api.add_pull(41, f"chore/sqlglot-{NEW}", "1" * 40, state="closed", digest=delivered["sha256"])
    world.prepare()
    result = world.publish(retry=True)
    assert result["outcome"] == "created"
    assert f"chore/sqlglot-{NEW}-retry1" in world.remote_branches()
    assert world.api.pull(41)["state"] == "closed"
    opened = [pull for pull in world.api.pulls if pull["state"] == "open"]
    assert [pull["head"]["ref"] for pull in opened] == [f"chore/sqlglot-{NEW}-retry1"]


@pytest.mark.parametrize("retry", [False, True])
def test_a_reverted_adoption_is_never_reproposed_even_on_retry(world: World, retry: bool) -> None:
    delivered = world.deliver(NEW, 2)
    world.api.add_pull(41, f"chore/sqlglot-{NEW}", "1" * 40, state="closed", merged=True, digest=delivered["sha256"])
    world.prepare()
    assert world.publish(retry=retry)["outcome"] == "already-merged"
    assert not world.api.mutations


def test_a_foreign_open_pull_request_on_the_target_branch_blocks_publication(world: World) -> None:
    world.deliver(NEW, 2)
    world.api.add_pull(41, f"chore/sqlglot-{NEW}", "1" * 40, author="a-human")
    world.prepare()
    assert world.publish(retry=True)["outcome"] == "occupied"
    assert not world.api.mutations


def test_a_branch_that_exists_without_a_pull_request_and_is_not_our_adoption_is_refused(world: World) -> None:
    world.deliver(NEW, 2)
    world.prepare()
    sh(world.clone, "git", "checkout", "-q", "-b", "scratch", "develop")
    (world.clone / "note.txt").write_text("human work\n")
    sh(world.clone, "git", "add", "note.txt")
    sh(world.clone, "git", "commit", "-m", "human work")
    sh(world.clone, "git", "push", "origin", f"scratch:refs/heads/chore/sqlglot-{NEW}")
    with pytest.raises(DeliveryError, match="not a clean adoption"):
        world.publish()
    assert not world.api.mutations


def test_a_prepared_bundle_that_touches_other_paths_is_refused_before_any_mutation(world: World) -> None:
    world.deliver(NEW, 2)
    prepared = world.prepare()
    (world.clone / "scripts" / "evil.py").write_text("print('x')\n")
    sh(world.clone, "git", "add", "scripts/evil.py")
    env = {
        "GIT_AUTHOR_NAME": "b",
        "GIT_AUTHOR_EMAIL": delivery.BOT_EMAIL,
        "GIT_COMMITTER_NAME": "b",
        "GIT_COMMITTER_EMAIL": delivery.BOT_EMAIL,
    }
    subprocess.check_call(["git", "commit", "--amend", "--no-edit"], cwd=world.clone, env={**os.environ, **env})
    head = sh(world.clone, "git", "rev-parse", "HEAD")
    bundle = world.tmp / "prepared" / delivery.BUNDLE_FILE
    bundle.unlink()
    sh(world.clone, "git", "bundle", "create", str(bundle), prepared["branch"], f"^{prepared['base']}")
    pull = json.loads((world.tmp / "prepared" / delivery.PULL_REQUEST_FILE).read_text(encoding="utf-8"))
    pull["body"] = pull["body"].replace(prepared["head"], head)
    pull["head"] = head
    (world.tmp / "prepared" / delivery.PULL_REQUEST_FILE).write_text(json.dumps(pull))
    with pytest.raises(DeliveryError, match="not publishable"):
        world.publish()
    assert not world.api.mutations and world.remote_branches() == ["develop"]


def test_a_marker_that_disagrees_with_the_plan_is_refused(world: World) -> None:
    world.deliver(NEW, 2)
    world.prepare()
    path = world.tmp / "prepared" / delivery.PULL_REQUEST_FILE
    pull = json.loads(path.read_text(encoding="utf-8"))
    pull["body"] = pull["body"].replace(FORK_SHA, "c" * 40)
    path.write_text(json.dumps(pull))
    with pytest.raises(DeliveryError, match="marker"):
        world.publish()
    assert not world.api.mutations


def test_dry_run_prints_the_actions_and_performs_no_hosted_mutation(world: World) -> None:
    world.deliver(NEW, 2)
    world.deliver(NEWER, 3)
    world.api.add_pull(41, f"chore/sqlglot-{NEW}", "1" * 40)
    world.prepare()
    result = world.publish(dry_run=True)
    assert [action["do"] for action in result["actions"]] == ["push", "create-pull-request", "close-pull-request"]
    assert not world.api.mutations
    assert world.remote_branches() == ["develop"]


def test_the_kill_switch_stops_everything_before_any_hosted_read(world: World) -> None:
    world.deliver(NEW, 2)
    world.prepare()
    world.api.calls.clear()
    for environ in ({}, {"SQLGLOT_DELIVERY_ENABLED": "false"}, {"SQLGLOT_DELIVERY_ENABLED": "TRUE"}):
        assert world.publish(environ=environ)["outcome"] == "disabled"
        assert delivery.arm(world.api, world.config, world.clone, repo=CONSUMER, login=APP, environ=environ) == [
            {"outcome": "disabled"}
        ]
    assert not world.api.calls and world.remote_branches() == ["develop"]


def test_publishing_is_restricted_to_the_consumer_repository(world: World) -> None:
    world.deliver(NEW, 2)
    world.prepare()
    with pytest.raises(DeliveryError, match="restricted"):
        delivery.publish(
            world.api,
            world.config,
            world.tmp / "prepared",
            world.clone,
            repo="attacker/BenchBox",
            login=APP,
            environ=ENABLED,
        )


class FakeRunner:
    def __init__(self, codes: Mapping[str, int] | None = None, stdout: str = "") -> None:
        self.codes = dict(codes or {})
        self.stdout = stdout
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, args: Sequence[str], cwd: Path | None = None, env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        assert cwd is not None
        script = Path(args[1]).name
        self.calls.append(
            {
                "script": script,
                "args": list(args[2:]),
                "head": sh(cwd, "git", "rev-parse", "HEAD"),
                "upstream": sh(cwd, "git", "rev-parse", "--abbrev-ref", "@{u}"),
                "dirty": sh(cwd, "git", "status", "--porcelain"),
                "env": dict(env or {}),
            }
        )
        return subprocess.CompletedProcess(list(args), self.codes.get(script, 0), self.stdout, "refused")


@pytest.fixture
def published(world: World) -> World:
    world.deliver(NEW, 2)
    world.prepare()
    world.publish()
    world.api.mutations.clear()
    return world


def arm(world: World, runner: FakeRunner, **options: Any) -> list[dict[str, Any]]:
    options.setdefault("environ", ENABLED)
    return delivery.arm(
        world.api, world.config, world.clone, repo=CONSUMER, login=APP, token="app-token", runner=runner, **options
    )


def test_arm_uses_the_repository_arming_rules_on_the_exact_head(published: World) -> None:
    runner = FakeRunner()
    pull = published.api.pulls[0]
    outcome = arm(published, runner)
    assert outcome == [{"pullRequest": pull["number"], "head": pull["head"]["sha"], "outcome": "armed"}]
    assert [call["script"] for call in runner.calls] == ["trunk_revert.py", "pr_arm.py"]
    gate, arming = runner.calls
    assert gate["args"][:3] == ["arm-gate", "--pr", str(pull["number"])]
    assert arming["args"] == ["--pr", str(pull["number"]), "--head", pull["head"]["sha"], "--repo", CONSUMER]
    assert arming["head"] == pull["head"]["sha"] and arming["dirty"] == ""
    assert arming["upstream"].startswith("origin/chore/sqlglot-")
    assert arming["env"] == {"GH_TOKEN": "app-token"}
    assert not published.api.mutations


def test_an_already_mergeable_pull_request_merged_by_arming_is_reported_as_merged(published: World) -> None:
    runner = FakeRunner(stdout="pr-arm: outcome: merged: PR #7 was already mergeable and merged at head abc\n")
    pull = published.api.pulls[0]
    outcome = arm(published, runner)
    assert outcome == [{"pullRequest": pull["number"], "head": pull["head"]["sha"], "outcome": "merged"}]


@pytest.mark.parametrize("failing", ["trunk_revert.py", "pr_arm.py"])
def test_a_refused_arming_is_reported_not_forced(published: World, failing: str) -> None:
    runner = FakeRunner({failing: 2})
    assert arm(published, runner)[0]["outcome"] == "not-armed"
    assert [call["script"] for call in runner.calls] == (
        ["trunk_revert.py"] if failing == "trunk_revert.py" else ["trunk_revert.py", "pr_arm.py"]
    )


@pytest.mark.parametrize(
    "history",
    [
        [{"event": "labeled", "label": {"name": "no-auto-merge"}, "actor": {"login": "a-human"}}],
        [
            {"event": "labeled", "label": {"name": "no-auto-merge"}, "actor": {"login": "a-human"}},
            {"event": "unlabeled", "label": {"name": "no-auto-merge"}, "actor": {"login": "a-human"}},
        ],
        [{"event": "auto_merge_disabled", "actor": {"login": "a-human"}}],
        [
            {"event": "auto_merge_disabled", "actor": {"login": "github-actions[bot]"}},
            {"event": "auto_merge_enabled", "actor": {"login": APP}},
            {"event": "auto_merge_disabled", "actor": {"login": "a-human"}},
        ],
        [{"event": "auto_merge_disabled", "actor": None}],
    ],
)
def test_a_human_disarm_or_hold_is_never_rearmed(published: World, history: list[dict[str, Any]]) -> None:
    published.api.timeline[published.api.pulls[0]["number"]] = history
    runner = FakeRunner()
    assert arm(published, runner)[0]["outcome"] == "not-armed"
    assert not runner.calls


def test_the_hold_label_blocks_arming(published: World) -> None:
    published.api.pulls[0]["labels"] = [{"name": "no-auto-merge"}]
    runner = FakeRunner()
    assert arm(published, runner)[0]["outcome"] == "not-armed" and not runner.calls


def test_only_the_documented_trunk_red_disarm_is_rearmed(published: World) -> None:
    number = published.api.pulls[0]["number"]
    published.api.timeline[number] = [
        {"event": "auto_merge_enabled", "actor": {"login": APP}},
        {"event": "auto_merge_disabled", "actor": {"login": "github-actions[bot]"}},
    ]
    runner = FakeRunner()
    assert arm(published, runner)[0]["outcome"] == "armed"
    assert [call["script"] for call in runner.calls] == ["trunk_revert.py", "pr_arm.py"]


def test_an_already_armed_pull_request_is_left_alone(published: World) -> None:
    published.api.pulls[0]["auto_merge"] = {"merge_method": "squash"}
    runner = FakeRunner()
    assert arm(published, runner)[0]["outcome"] == "not-armed" and not runner.calls


@pytest.mark.parametrize("replace", [False, True])
def test_a_commit_pushed_by_a_human_carrying_the_bot_email_is_never_armed(published: World, replace: bool) -> None:
    published.api.forge_push(published.api.pulls[0]["number"], replace=replace)
    runner = FakeRunner()
    assert arm(published, runner) == []
    assert not runner.calls


def test_a_pull_request_body_edited_by_a_person_is_never_armed(published: World) -> None:
    published.api.edits[published.api.pulls[0]["number"]] = ["a-human"]
    runner = FakeRunner()
    assert arm(published, runner) == []
    assert not runner.calls


def test_a_body_edit_by_the_app_alone_does_not_block_arming(published: World) -> None:
    published.api.edits[published.api.pulls[0]["number"]] = ["benchbox-sqlglot-delivery"]
    assert arm(published, FakeRunner())[0]["outcome"] == "armed"


def test_a_suppressed_pair_is_never_armed(published: World) -> None:
    marker = delivery.parse_marker(published.api.pulls[0]["body"])
    published.use_config(suppressed=[{"version": marker["version"], "sha256": marker["sha256"], "reason": "reverted"}])
    runner = FakeRunner()
    assert arm(published, runner)[0]["outcome"] == "not-armed" and not runner.calls


@pytest.mark.parametrize("unreadable", ["error", "anonymous-editor"])
def test_an_unreadable_or_anonymous_edit_history_refuses(published: World, unreadable: str) -> None:
    number = published.api.pulls[0]["number"]
    if unreadable == "error":
        published.api.edits_unreadable = True
        runner = FakeRunner()
        with pytest.raises(DeliveryError, match="cannot be read"):
            arm(published, runner)
    else:
        published.api.edits[number] = [None]
        runner = FakeRunner()
        assert arm(published, runner) == []
    assert not runner.calls


def test_arm_dry_run_runs_no_arming_command(published: World) -> None:
    runner = FakeRunner()
    assert arm(published, runner, dry_run=True)[0]["outcome"] == "would-arm"
    assert not runner.calls


def test_a_head_that_moved_after_publication_is_not_armed(published: World) -> None:
    published.api.pulls[0]["head"]["sha"] = "9" * 40
    runner = FakeRunner()
    assert arm(published, runner) == []
    assert not runner.calls


def test_a_head_forged_between_listing_and_arming_is_not_armed(published: World) -> None:
    number = published.api.pulls[0]["number"]
    published.api.on_read[number] = [lambda pull: published.api.forge_push(number)]
    runner = FakeRunner()
    outcome = arm(published, runner)
    assert outcome[0]["outcome"] == "not-armed" and "no longer points" in outcome[0]["reason"]
    assert not runner.calls


def test_a_head_forged_after_the_trunk_gate_is_not_armed(published: World) -> None:
    number = published.api.pulls[0]["number"]
    published.api.on_read[number] = [None, lambda pull: published.api.forge_push(number)]
    runner = FakeRunner()
    assert arm(published, runner)[0]["outcome"] == "not-armed"
    assert [call["script"] for call in runner.calls] == ["trunk_revert.py"]


def test_arming_leaves_no_worktree_behind(published: World) -> None:
    arm(published, FakeRunner())
    assert sh(published.clone, "git", "worktree", "list").count("\n") == 0
    assert "sqlglot-delivery-arm" not in sh(published.clone, "git", "branch", "--list")


def test_the_cli_plan_exits_nonzero_for_a_producer_exception(world: World, capsys: pytest.CaptureFixture[str]) -> None:
    world.deliver(NEW, 2)
    world.deliver(NEW, 3, wheel=world.build(NEW, dirty=True))
    code = delivery.main(
        ["--config", str(REAL_CONFIG), "plan", "--root", str(world.clone), "--out-dir", str(world.tmp / "plan")],
        api=world.api,
    )
    assert code == delivery.EXIT_EXCEPTION
    assert "Producer exception" in capsys.readouterr().err


def test_the_cli_reports_a_refusal_as_a_failure(world: World, capsys: pytest.CaptureFixture[str]) -> None:
    world.deliver(NEW, 2, tamper=mutate(("kind",), "other"))
    code = delivery.main(
        ["--config", str(REAL_CONFIG), "plan", "--root", str(world.clone), "--out-dir", str(world.tmp / "plan")],
        api=world.api,
    )
    assert code == 1
    assert "kind" in capsys.readouterr().err
