from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

RECEIPT_SCHEMA = "prepared_work_v2"
EVIDENCE_SCHEMA = "benchbox_batch_evidence_v2"
REVIEW_SCHEMA = "benchbox_member_review_v1"
BATCH_ID = "b"
REPOSITORY = "o/r"
BRANCH = "feat/x"
SECTION_NAMES = frozenset(
    {
        "members",
        "scope",
        "receipts",
        "integration_log",
        "acceptance_required",
        "recoveries",
        "final_pr_history",
        "final_evidence",
    }
)


REVIEW_STORE: dict[tuple[str, str, str], dict] = {}


def stored_review(_repo: Path, base: str, head: str, trusted: str) -> dict | None:
    return REVIEW_STORE.get((base, head, trusted))


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, encoding="utf-8")
    return proc.stdout.strip()


def head_of(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")


def write_commit(repo: Path, files: dict[str, str | None], message: str) -> str:
    for path, text in files.items():
        target = repo / path
        if text is None:
            git(repo, "rm", "-q", path)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        git(repo, "add", path)
    git(repo, "commit", "-qm", message)
    return head_of(repo)


class BatchFixture:
    def __init__(self, repo: Path, scope: dict[str, list[str]]) -> None:
        self.repo = repo
        self.scope = scope
        self.start = head_of(repo)
        git(repo, "update-ref", "refs/remotes/origin/develop", self.start)
        self.receipts: dict[str, dict] = {}
        self.log: list[dict] = [{"seq": 0, "head": self.start, "kind": "start"}]
        self.required: dict[str, list[dict]] = {}
        self.overlaps: dict[str, list[dict]] = {}
        self._counter = 0

    def _append(self, entry: dict) -> None:
        entry["seq"] = len(self.log)
        entry["previous_head"] = self.log[-1]["head"]
        self.log.append(entry)

    def land(self, member: str, files: dict[str, str | None], base: str | None = None) -> str:
        self._counter += 1
        source_base = base or head_of(self.repo)
        branch = f"member-{member}-{self._counter}"
        git(self.repo, "checkout", "-q", "-b", branch, source_base)
        accepted = write_commit(self.repo, files, f"member {member}")
        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "merge", "--no-ff", "-q", "-m", f"land {member}", branch)
        merged = head_of(self.repo)
        self.receipts[member] = {
            "schema": RECEIPT_SCHEMA,
            "batch_id": BATCH_ID,
            "member_id": member,
            "source_base": source_base,
            "accepted_head": accepted,
            "integration_head": merged,
            "integration_seq": len(self.log),
            "changed_files": sorted(files),
            "overlaps": [],
            "verification": {
                "status": "passed",
                "revision": accepted,
                "clean": True,
                "suite": f"suite-{member}",
                "command": ["pytest", "-q", member],
            },
        }
        REVIEW_STORE[(source_base, accepted, self.start)] = {
            "schema": REVIEW_SCHEMA,
            "outcome": "clear",
            "reason": "",
            "base_sha": source_base,
            "head_sha": accepted,
            "trusted_sha": self.start,
        }
        self._append({"kind": "prepare", "head": merged, "member": member, "changed_files": sorted(files)})
        return merged

    def overlap(self, later: str, earlier: str, paths: list[str]) -> None:
        self.receipts[later]["overlaps"].append({"kind": "member", "member": earlier, "paths": paths})
        self.required.setdefault(earlier, []).append({"kind": "overlap", "by": later, "paths": paths})

    def fix(self, files: dict[str, str | None]) -> str:
        previous = head_of(self.repo)
        merged = write_commit(self.repo, files, "fix")
        touched = sorted(files)
        owners = sorted(m for m, r in self.receipts.items() if set(r["changed_files"]) & set(touched))
        self._append(
            {
                "kind": "fix",
                "head": merged,
                "changed_files": touched,
                "requires_acceptance": owners,
                "previous_head": previous,
            }
        )
        for owner in owners:
            self.required.setdefault(owner, []).append({"kind": "fix", "seq": self.log[-1]["seq"], "paths": touched})
        return merged

    def develop_merge(self, files: dict[str, str | None]) -> str:
        self._counter += 1
        branch = f"develop-{self._counter}"
        git(self.repo, "checkout", "-q", "-b", branch, "refs/remotes/origin/develop")
        develop_tip = write_commit(self.repo, files, "develop change")
        git(self.repo, "update-ref", "refs/remotes/origin/develop", develop_tip)
        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "merge", "--no-ff", "-q", "-m", "refresh from develop", branch)
        merged = head_of(self.repo)
        self._append(
            {"kind": "develop-merge", "head": merged, "changed_files": sorted(files), "requires_acceptance": []}
        )
        return merged

    def changed_files(self) -> list[str]:
        out = subprocess.run(
            ["git", "diff", "--no-renames", "--name-only", "-z", f"{self.start}..{head_of(self.repo)}"],
            cwd=self.repo,
            check=True,
            capture_output=True,
        ).stdout
        paths = {p.decode() for p in out.split(b"\0") if p}
        owned = {p for r in self.receipts.values() for p in r["changed_files"]}
        develop: set[str] = set()
        for entry in self.log:
            if entry["kind"] == "fix":
                owned |= set(entry["changed_files"])
            elif entry["kind"] == "develop-merge":
                develop |= set(entry["changed_files"])
        return sorted(paths - (develop - owned))

    def tracker(self) -> dict:
        head = head_of(self.repo)
        return {
            "batch_id": BATCH_ID,
            "lifecycle": "active",
            "repository": REPOSITORY,
            "integration_branch": BRANCH,
            "start_head": self.start,
            "integration_head": head,
            "integration_worktree": str(self.repo),
            "final_pr": {"repository": REPOSITORY, "number": 7, "node_id": "PR_7", "head": head},
            "members": [
                {"id": m, "status": "open", "prepared": True, "accepted_head": r["accepted_head"]}
                for m, r in self.receipts.items()
            ],
            "scope": copy.deepcopy(self.scope),
            "receipts": copy.deepcopy(self.receipts),
            "integration_log": copy.deepcopy(self.log),
            "acceptance_required": copy.deepcopy(self.required),
            "final_pr_history": [],
        }

    def evidence(self, tracker: dict | None = None) -> dict:
        tracker = tracker or self.tracker()
        head = head_of(self.repo)
        return {
            "schema": EVIDENCE_SCHEMA,
            "batch_id": BATCH_ID,
            "repository": REPOSITORY,
            "integration_branch": BRANCH,
            "integration_worktree": str(self.repo),
            "final_head": head,
            "tracker_rev": "rev-1",
            "tracker": copy.deepcopy(tracker),
            "current_acceptance": {
                member: {
                    "status": "passed",
                    "revision": head,
                    "clean": True,
                    "suite": self.receipts[member]["verification"]["suite"],
                    "command": self.receipts[member]["verification"]["command"],
                    "worktree": str(self.repo),
                }
                for member in self.required
            },
            "changed_files": self.changed_files(),
            "member_reviews": {
                member: {
                    "schema": REVIEW_SCHEMA,
                    "outcome": "clear",
                    "reason": "",
                    "base_sha": receipt["source_base"],
                    "head_sha": receipt["accepted_head"],
                    "trusted_sha": self.start,
                }
                for member, receipt in self.receipts.items()
            },
            "generated_at": "2026-11-01T00:00:00Z",
        }


class FakeBatchShow:
    def __init__(self, tracker: dict, rev: str = "rev-1", stale: bool = False) -> None:
        self.tracker = tracker
        self.rev = rev
        self.stale = stale
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> tuple[int, str]:
        self.calls.append(cmd)
        args = cmd[cmd.index("show") + 1 :]
        if args[0] != self.tracker["batch_id"]:
            return 2, json.dumps({"ok": False, "code": "E_STATE", "error": "unknown batch"})
        options = dict(zip(args[1::2], args[2::2], strict=False))
        sections = {key: value for key, value in self.tracker.items() if key in SECTION_NAMES}
        scalars = {key: value for key, value in self.tracker.items() if key not in SECTION_NAMES}
        if "--field" not in options:
            stamp = {"cached_rev": self.rev, "stale": True} if self.stale else {"rev": self.rev}
            return 0, json.dumps({"ok": True, "data": {**scalars, **stamp, "sections": self._sections(sections)}})
        if options.get("--rev") != self.rev:
            return 2, json.dumps({"ok": False, "code": "E_CURSOR_STALE", "error": "revision moved"})
        blob = json.dumps(sections[options["--field"]], separators=(",", ":"))
        offset, budget = int(options["--offset"]), int(options["--budget"])
        data: dict = {
            "field": options["--field"],
            "offset": offset,
            "total": len(blob),
            "window": blob[offset : offset + budget],
        }
        if offset + budget < len(blob):
            data["continuation"] = {
                "field": options["--field"],
                "offset": offset + budget,
                "budget": budget,
                "rev": self.rev,
            }
        return 0, json.dumps({"ok": True, "data": data})

    @staticmethod
    def _sections(sections: dict) -> dict:
        return {name: {"bytes": len(json.dumps(value)), "items": len(value)} for name, value in sections.items()}
