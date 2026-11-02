from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bi = load("batch_integration")
pr_landing = load("pr_landing")

MEMBERS = ["zeta", "alpha", "mid"]
SCOPE = {
    "zeta": ["src/*", "tests/test_zeta.py", "docs/*"],
    "alpha": ["src/*", "tests/test_alpha.py", "docs/*"],
    "mid": ["src/*", "tests/test_mid.py", "docs/*", "config/*"],
}
PROJECT = "proj"
BATCH = "b1"


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, encoding="utf-8")
    return proc.stdout.strip()


def ok(data: dict) -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "data": data}), stderr="")


def done(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class FakeTracker:
    def __init__(self) -> None:
        self.batch: dict | None = None
        self.receipts: dict[str, dict] = {}
        self.log: list[dict] = []
        self.final_pr: dict | None = None
        self.repo: Path | None = None
        self.rev = 1
        self.window_cap = 100000
        self.paged_sections: set[str] = set()
        self.unreadable = False

    def register(self, arguments: dict) -> None:
        self.batch = dict(arguments)
        self.repo = Path(arguments["integration_worktree"])
        self.log = [{"seq": 0, "head": arguments["start_head"], "kind": "start"}]
        self.rev += 1

    @property
    def head(self) -> str:
        return self.log[-1]["head"]

    def prepare(self, arguments: dict) -> None:
        assert self.repo is not None
        base = arguments["source_base"]
        assert base in [entry["head"] for entry in self.log], "source base is not a recorded head"
        changed = bi._changed_files(self.repo, base, arguments["accepted_head"])
        overlaps = []
        for other, receipt in self.receipts.items():
            shared = sorted(set(changed) & set(receipt["changed_files"]))
            if shared:
                overlaps.append({"kind": "member", "member": other, "paths": shared})
        base_seq = min(e["seq"] for e in self.log if e["head"] == base)
        for entry in self.log:
            if entry["kind"] == "fix" and entry["seq"] > base_seq:
                shared = sorted(set(changed) & set(entry["changed_files"]))
                if shared:
                    overlaps.append({"kind": "fix", "seq": entry["seq"], "paths": shared})
        seq = len(self.log)
        self.log.append(
            {
                "seq": seq,
                "head": arguments["integration_head"],
                "previous_head": self.head,
                "kind": "prepare",
                "member": arguments["member_id"],
            }
        )
        self.receipts[arguments["member_id"]] = {
            "schema": "prepared_work_v2",
            "batch_id": arguments["batch_id"],
            "member_id": arguments["member_id"],
            "source_worktree": arguments["source_worktree"],
            "source_revision": arguments["source_revision"],
            "source_base": base,
            "accepted_head": arguments["accepted_head"],
            "integration_head": arguments["integration_head"],
            "integration_seq": seq,
            "scope_hash": arguments["scope_hash"],
            "changed_files": changed,
            "overlaps": overlaps,
            "verification": arguments["verification"],
        }
        self.rev += 1

    def advance(self, arguments: dict) -> None:
        assert arguments["expected_head"] == self.head
        touched = []
        if arguments["reason"] == "fix":
            touched = [m for m, r in self.receipts.items() if set(r["changed_files"]) & set(arguments["changed_files"])]
        self.log.append(
            {
                "seq": len(self.log),
                "head": arguments["head"],
                "previous_head": arguments["expected_head"],
                "kind": arguments["reason"],
                "changed_files": sorted(arguments["changed_files"]),
                "requires_acceptance": touched,
            }
        )
        self.rev += 1

    def bind(self, head: str) -> None:
        self.final_pr = {"repository": "owner/name", "number": 7, "node_id": "PR_node", "head": head}
        self.rev += 1

    def acceptance_required(self) -> dict:
        required: dict[str, list] = {}
        for member, receipt in self.receipts.items():
            for overlap in receipt["overlaps"]:
                if overlap["kind"] == "member":
                    required.setdefault(overlap["member"], []).append({"kind": "overlap", "by": member})
                else:
                    required.setdefault(member, []).append({"kind": "fix-overlap", "seq": overlap["seq"]})
        for entry in self.log:
            if entry["kind"] == "fix":
                for member in entry["requires_acceptance"]:
                    required.setdefault(member, []).append({"kind": "fix", "seq": entry["seq"]})
        return required

    def sections(self) -> dict:
        assert self.batch is not None
        return {
            "members": [{"id": m, "status": "open", "prepared": m in self.receipts} for m in self.batch["members"]],
            "scope": self.batch["scope"],
            "receipts": self.receipts,
            "integration_log": self.log,
            "acceptance_required": self.acceptance_required(),
            "recoveries": [],
            "final_pr_history": [],
            "final_evidence": {},
        }

    def show(self, args: list[str]) -> SimpleNamespace:
        if self.unreadable or self.batch is None:
            return SimpleNamespace(
                returncode=2,
                stdout=json.dumps({"ok": False, "code": "E_STATE", "error": "unknown batch", "recovery": []}),
                stderr="",
            )
        options = dict(zip(args[3::2], args[4::2], strict=False))
        sections = self.sections()
        if "--field" in options:
            assert options.get("--rev") in (None, str(self.rev)), "stale revision"
            blob = json.dumps(sections[options["--field"]], separators=(",", ":"), sort_keys=True)
            offset = int(options.get("--offset", 0))
            budget = min(int(options.get("--budget", 6000)), self.window_cap)
            data = {"batch_id": args[2], "field": options["--field"], "offset": offset, "total": len(blob)}
            data["window"] = blob[offset : offset + budget]
            data["rev"] = str(self.rev)
            if offset + budget < len(blob):
                data["continuation"] = {"field": options["--field"], "offset": offset + budget, "rev": str(self.rev)}
            return ok(data)
        summary = {
            "batch_id": args[2],
            "lifecycle": "active",
            "project_id": self.batch["project_id"],
            "repository": self.batch["repository"],
            "integration_branch": self.batch["integration_branch"],
            "integration_worktree": self.batch["integration_worktree"],
            "start_head": self.batch["start_head"],
            "integration_head": self.head,
            "scope_hash": self.batch["scope_hash"],
            "delivery_boundary": self.batch["delivery_boundary"],
            "terminal_outcome": self.batch["terminal_outcome"],
            "owner": "leader",
            "owner_epoch": 0,
            "develop_ref": self.batch.get("develop_ref"),
            "final_pr": self.final_pr,
            "store": {"schema_version": 4},
            "capability": {"version": 2, "receipt_schema": "prepared_work_v2"},
            "rev": str(self.rev),
        }
        described = {}
        for name, value in sections.items():
            described[name] = {"bytes": len(json.dumps(value)), "items": len(value)}
            if name in self.paged_sections:
                described[name]["continuation"] = {"field": name, "offset": 0, "budget": 6000, "rev": str(self.rev)}
            else:
                summary[name] = value
        summary["sections"] = described
        return ok(summary)


class World:
    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        self.tmp = tmp_path
        self.tracker = FakeTracker()
        self.calls: list[tuple[list[str], Path]] = []
        self.review_cache: dict[tuple[str, str, str], dict] = {}
        self.failing_tests: set[str] = set()
        self.preflight_rc = 0
        self.review_rc: int | None = None
        self.mapped_tests: list[str] = []
        self.origin = tmp_path / "origin.git"
        subprocess.run(["git", "init", "--bare", "-b", "develop", str(self.origin)], check=True, capture_output=True)
        seed = tmp_path / "seed"
        seed.mkdir()
        git(seed, "init", "-b", "develop")
        self.seed = seed
        for name, text in {
            "src/a.py": "".join(f"a{n} = {n}\n" for n in range(8)),
            "src/b.py": "b = 1\n",
            "tests/test_zeta.py": "def test_z():\n    assert True\n",
            "tests/test_alpha.py": "def test_a():\n    assert True\n",
            "tests/test_mid.py": "def test_m():\n    assert True\n",
            "docs/note.md": "note\n",
            ".github/oracle-reviewers.yml": "brief_max_bytes: 100000\n",
            ".gitignore": ".todo-batch/\n",
        }.items():
            (seed / name).parent.mkdir(parents=True, exist_ok=True)
            (seed / name).write_text(text)
        git(seed, "add", ".")
        git(seed, "commit", "-qm", "seed")
        git(seed, "remote", "add", "origin", str(self.origin))
        git(seed, "push", "-q", "origin", "develop")
        self.primary = tmp_path / "primary"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.primary)], check=True, capture_output=True)
        monkeypatch.setattr(bi, "_run", self.run)
        monkeypatch.setattr(bi, "lookup_member_review", self.lookup_review)
        self.integration: Path | None = None

    def lookup_review(self, repo: Path, base: str, head: str, trusted: str):
        return self.review_cache.get((base, head, trusted))

    def run(self, argv, cwd, **_kwargs):
        argv = list(argv)
        self.calls.append((argv, Path(cwd)))
        if argv[:2] == ["make", "worktree-create"]:
            values = dict(item.split("=", 1) for item in argv[2:])
            subprocess.run(
                ["git", "worktree", "add", "-q", "-b", values["BRANCH"], values["WORKTREE_PATH"], values["BASE"]],
                cwd=cwd,
                check=True,
                capture_output=True,
            )
            return done()
        command = bi.TODO_DB_COMMAND
        if argv[: len(command)] == command:
            return self.tracker.show(argv[len(command) :])
        if argv[:2] == ["make", "pr-preflight"]:
            return done(self.preflight_rc, stderr="preflight output")
        if argv[:3] == ["make", "-s", "changelog-check"]:
            return done()
        if "preflight_targets.py" in argv:
            return done(0, "\n".join(self.mapped_tests))
        if argv[:2] == bi.DEFAULT_REVIEW_COMMAND:
            return self.review(argv, Path(cwd))
        if "pytest" in argv or "ruff" in argv:
            failing = any(item in self.failing_tests for item in argv)
            return done(1 if failing else 0, stderr="verification failed" if failing else "")
        raise AssertionError(f"unexpected external command {argv}")

    def review(self, argv: list[str], cwd: Path) -> SimpleNamespace:
        flags = [item for item in argv[2:] if item != "--member"]
        values = dict(zip(flags[0::2], flags[1::2], strict=True))
        base, head, trusted = values["--base-ref"], values["--head"], values["--trusted-ref"]
        files = bi._changed_files(cwd, base, head)
        exempt = all(path.startswith("docs/") for path in files)
        outcome = "not-reviewed" if exempt else "clear"
        if self.review_rc is not None:
            outcome = "findings"
        result = {
            "schema": "benchbox_member_review_v1",
            "outcome": outcome,
            "reason": "fixture",
            "base_sha": base,
            "head_sha": head,
            "head_tree": git(cwd, "rev-parse", f"{head}^{{tree}}"),
            "patch_digest": "digest",
            "trusted_sha": trusted,
        }
        self.review_cache[(base, head, trusted)] = result
        Path(values["--result-json"]).write_text(json.dumps(result), encoding="utf-8")
        return done(self.review_rc or 0)

    def calls_matching(self, word: str) -> list[tuple[list[str], Path]]:
        return [call for call in self.calls if word in call[0]]

    def start(self, members: list[str] | None = None, scope: dict | None = None) -> Path:
        members = members or list(MEMBERS)
        payload = bi.start_batch(
            self.primary, BATCH, members, scope or {m: SCOPE[m] for m in members}, project_id=PROJECT
        )
        self.tracker.register(payload["arguments"])
        self.integration = Path(payload["worktree"])
        self.payload = payload
        return self.integration

    def commit(self, worktree: Path, files: dict[str, str], message: str = "work") -> str:
        for name, text in files.items():
            (worktree / name).parent.mkdir(parents=True, exist_ok=True)
            (worktree / name).write_text(text)
        git(worktree, "add", "--", *files)
        git(worktree, "commit", "-qm", message)
        return git(worktree, "rev-parse", "HEAD")

    def member(self, member: str) -> Path:
        assert self.integration is not None
        return Path(bi.create_member(self.integration, member)["worktree"])

    def sync(self, member_worktree: Path) -> None:
        git(member_worktree, "merge", "--no-edit", "-q", bi.integration_branch_name(BATCH))

    def check(self, member_worktree: Path, tests: list[str] | None = None) -> dict:
        return bi.check_member(member_worktree, tests or [])

    def deliver(self, member: str, files: dict[str, str], tests: list[str] | None = None) -> Path:
        worktree = self.member(member)
        self.commit(worktree, files, f"{member} work")
        self.check(worktree, tests if tests is not None else [f"tests/test_{member}.py"])
        return worktree

    def land(self, member: str, **kwargs) -> dict:
        assert self.integration is not None
        return bi.land_member(self.integration, member, **kwargs)

    def submit(self, result: dict) -> None:
        for step in result["payload"]["steps"]:
            if step["tool"] == "prepare":
                self.tracker.prepare(step["arguments"])

    def land_and_prepare(self, member: str, **kwargs) -> dict:
        result = self.land(member, **kwargs)
        self.submit(result)
        return result

    def head(self) -> str:
        assert self.integration is not None
        return git(self.integration, "rev-parse", "HEAD")
