from __future__ import annotations

import contextlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

TRACKED_COPY = (
    "Makefile",
    "make",
    "scripts/batch_integration.py",
    "scripts/pr_landing.py",
    "scripts/pr_arm_omp_bump.py",
    "_project/scripts/check_suites.py",
    "scripts/worktree_lifecycle.sh",
    "scripts/make_timing_shell.sh",
    "scripts/set_worktree_identity.sh",
    "scripts/worktree_lifecycle_metadata.py",
    "scripts/changelog_fragments.py",
    "_project/scripts/pyproject.toml",
    "_project/scripts/uv.lock",
    "_project/scripts/vendor",
    "_project/scripts/oracle_reviewers",
    "_project/scripts/inert_paths.py",
    "_project/scripts/soundness_paths.py",
    "_project/analysis/batch-rollout-evidence.json",
    ".github/oracle-reviewers.yml",
    ".github/oracle-omp-config.yml",
    ".github/soundness-paths.txt",
    ".github/inert-paths.txt",
    ".github/changelog-paths.txt",
    "CHANGELOG.md",
)

SHARED_SOURCE = "ALPHA = None\nGAMMA = None\n\n\ndef unrelated() -> int:\n    return 0\n"

FAKE_REVIEW = """\
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from _project.scripts.oracle_reviewers import local

parser = argparse.ArgumentParser()
parser.add_argument("--policy", required=True)
parser.add_argument("--member", action="store_true")
parser.add_argument("--result-json", required=True)
parser.add_argument("--base-ref", required=True)
parser.add_argument("--trusted-ref", required=True)
parser.add_argument("--head", required=True)
args = parser.parse_args()
outcome_file = Path(__import__("os").environ["FAKE_REVIEW_OUTCOME_FILE"])
outcome = outcome_file.read_text().strip() if outcome_file.exists() else "clear"
root = Path.cwd()
record = local._member_record(root, args.base_ref, args.head, args.trusted_ref, outcome, "fake reviewer verdict")
trusted = local.Trusted.read(root, args.trusted_ref, args.policy)
local._write_json_atomic(
    local._member_path(local.state_dir(), local._member_key(args.base_ref, args.head, args.trusted_ref, trusted)),
    record,
)
Path(args.result_json).write_text(json.dumps(record))
sys.exit(1 if outcome == "findings" else 0)
"""

FAKE_PREFLIGHT = """\
import json
import os
import sys
from pathlib import Path

with Path(os.environ["FAKE_PREFLIGHT_LOG"]).open("a") as log:
    log.write(json.dumps({"cwd": os.getcwd(), "argv": sys.argv[1:]}) + "\\n")
"""

FAKE_GH = """\
#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_GH_LOG"
exit 0
"""

DEVELOP_FILES = {
    "pyproject.toml": (
        '[project]\nname = "disposable"\nversion = "0"\nrequires-python = ">=3.11"\n'
        'dependencies = ["pytest", "pytest-xdist", "pyyaml"]\n'
    ),
    "pytest.ini": "[pytest]\npythonpath = .\n",
    ".gitignore": ".venv/\n__pycache__/\n.pytest_cache/\n.todo-batch/\n*.pyc\n",
    "src/__init__.py": "",
    "src/shared.py": SHARED_SOURCE,
    "tests/__init__.py": "",
    "changelog.d/README.md": "fragments\n",
}


class ToolError(AssertionError):
    pass


def _tracked(paths: tuple[str, ...]) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *paths], cwd=ROOT, check=True, capture_output=True, encoding="utf-8"
    ).stdout
    return [name for name in out.split("\0") if name and (ROOT / name).is_file()]


class McpServer:
    def __init__(self, world: World, actor: str) -> None:
        self.world = world
        self.actor = actor
        self._id = 0
        self.proc = subprocess.Popen(
            [
                "uv",
                "run",
                "--project",
                "_project/scripts",
                "--locked",
                "--",
                "todo-db-mcp",
                "--state-remote",
                str(world.tracker_remote),
                "--state-branch",
                "todo-state",
                "--cache-dir",
                str(world.root / f"cache-{actor}"),
                "--actor",
                actor,
            ],
            cwd=world.primary,
            env=world.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        init = self._rpc(
            "initialize",
            {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": actor, "version": "1"}},
        )
        self.server_info = init["result"]["serverInfo"]
        self.capability = init["result"]["capabilities"]["experimental"]["todo_db_batch"]
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.proc.stdin.flush()

    def _rpc(self, method: str, params: dict) -> dict:
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self._id += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise ToolError(f"{self.actor}: no response for {method}")
        return json.loads(line)

    def call(self, tool: str, **arguments: object) -> dict:
        response = self._rpc("tools/call", {"name": tool, "arguments": arguments})
        result = response["result"]
        text = result["content"][0]["text"]
        try:
            payload = json.loads(text)
        except ValueError:
            return {"ok": False, "code": "E_TRANSPORT", "error": text, "recovery": ""}
        return payload

    def ok(self, tool: str, **arguments: object) -> dict:
        payload = self.call(tool, **arguments)
        if payload.get("ok") is not True:
            raise ToolError(f"{self.actor} {tool} refused: {payload}")
        return payload["data"]

    def refused(self, tool: str, code: str, **arguments: object) -> dict:
        payload = self.call(tool, **arguments)
        if payload.get("ok") is not False or payload.get("code") != code:
            raise ToolError(f"{self.actor} {tool} expected {code}, got {payload}")
        return payload

    def kill(self) -> None:
        self.proc.kill()
        self.proc.wait(timeout=10)

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class World:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        root = self.root
        self.tools = root / "tools"
        self.tools.mkdir()
        self.code_remote = root / "hosted" / "owner" / "repo.git"
        self.tracker_remote = root / "tracker.git"
        self.primary = root / "repo"
        self.env = self._environment()
        self.servers: list[McpServer] = []
        self.preflight_log = root / "preflight.log"
        self.gh_log = root / "gh.log"
        self.review_outcome_file = root / "review-outcome"

    def _environment(self) -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("BENCHBOX_", "GIT_", "VIRTUAL_ENV", "UV_PROJECT", "PYTHONPATH", "PYTEST", "TODO_DB"))
        }
        uv_cache = subprocess.run(["uv", "cache", "dir"], check=True, capture_output=True, text=True).stdout.strip()
        uv_python = subprocess.run(["uv", "python", "dir"], check=True, capture_output=True, text=True).stdout.strip()
        home = self.root / "home"
        home.mkdir()
        gitconfig = self.root / "gitconfig"
        gitconfig.write_text(
            "[user]\n\tname = Disposable Dev\n\temail = dev@example.com\n[init]\n\tdefaultBranch = develop\n"
            '[commit]\n\tgpgsign = false\n[protocol "file"]\n\tallow = always\n'
        )
        env.update(
            HOME=str(home),
            GIT_CONFIG_GLOBAL=str(gitconfig),
            GIT_CONFIG_NOSYSTEM="1",
            UV_CACHE_DIR=uv_cache,
            UV_PYTHON_INSTALL_DIR=uv_python,
            BENCHBOX_MAKE_TIMINGS="0",
            FAKE_PREFLIGHT_LOG=str(self.root / "preflight.log"),
            FAKE_GH_LOG=str(self.root / "gh.log"),
            FAKE_REVIEW_OUTCOME_FILE=str(self.root / "review-outcome"),
        )
        return env

    def run(self, argv: list[str], cwd: Path, *, check: bool = True, env: dict[str, str] | None = None):
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=env or self.env,
            check=False,
            capture_output=True,
            text=True,
            timeout=900,
        )
        if check and proc.returncode != 0:
            raise AssertionError(
                f"{argv} failed in {cwd}: {proc.returncode}\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
            )
        return proc

    def git(self, cwd: Path, *args: str) -> str:
        return self.run(["git", *args], cwd).stdout.strip()

    def build(self) -> None:
        self.code_remote.parent.mkdir(parents=True)
        self.run(["git", "init", "--bare", "-q", str(self.code_remote)], self.root)
        self.run(["git", "init", "--bare", "-q", str(self.tracker_remote)], self.root)
        self.primary.mkdir()
        self.run(["git", "init", "-q", "-b", "develop"], self.primary)
        for name in _tracked(TRACKED_COPY):
            target = self.primary / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        for name, content in DEVELOP_FILES.items():
            target = self.primary / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        config = self.primary / ".todo-db" / "config.json"
        config.parent.mkdir()
        config.write_text(
            json.dumps({"state_branch": "todo-state", "state_remote": str(self.tracker_remote)}, indent=2) + "\n"
        )
        self.run(["uv", "lock", "-q"], self.primary)
        self.run(["git", "add", "-A"], self.primary)
        self.run(["git", "commit", "-q", "-m", "chore: seed the disposable repository"], self.primary)
        self.run(["git", "remote", "add", "origin", self.code_remote.as_uri()], self.primary)
        self.run(["git", "push", "-q", "origin", "develop"], self.primary)
        self.run(["git", "fetch", "-q", "origin"], self.primary)
        self.run(["git", "branch", "--set-upstream-to=origin/develop", "develop"], self.primary)
        (self.tools / "fake_review.py").write_text(FAKE_REVIEW)
        (self.tools / "fake_preflight.py").write_text(FAKE_PREFLIGHT)
        gh = self.tools / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        self.env["PATH"] = f"{self.tools}{os.pathsep}{self.env['PATH']}"
        self.run(
            [
                "uv",
                "run",
                "--project",
                "_project/scripts",
                "--locked",
                "--",
                "todo-db",
                "bootstrap",
                "--state-remote",
                str(self.tracker_remote),
                "--state-branch",
                "todo-state",
            ],
            self.primary,
        )

    def server(self, actor: str) -> McpServer:
        server = McpServer(self, actor)
        self.servers.append(server)
        return server

    def close(self) -> None:
        for server in self.servers:
            server.stop()

    def make(self, cwd: Path, target: str, *, check: bool = True, **variables: str):
        argv = ["make", "--no-print-directory", target, *[f"{key}={value}" for key, value in variables.items()]]
        return self.run(argv, cwd, check=check)

    def make_json(self, cwd: Path, target: str, **variables: str) -> dict:
        return json.loads(self.make(cwd, target, **variables).stdout)

    def refused(self, cwd: Path, target: str, **variables: str) -> str:
        proc = self.make(cwd, target, check=False, **variables)
        assert proc.returncode != 0, f"make {target} unexpectedly succeeded: {proc.stdout[-500:]}"
        return proc.stderr + proc.stdout

    @property
    def review_command(self) -> str:
        return f"uv run -- python {self.tools / 'fake_review.py'}"

    @property
    def preflight_command(self) -> str:
        return f"python3 {self.tools / 'fake_preflight.py'}"

    def commit_files(self, worktree: Path, files: dict[str, str], message: str) -> str:
        for name, content in files.items():
            target = worktree / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        self.git(worktree, "add", "--", *files)
        self.git(worktree, "commit", "-q", "-m", message)
        return self.git(worktree, "rev-parse", "HEAD")

    def push_develop(self, files: dict[str, str], message: str) -> str:
        head = self.commit_files(self.primary, files, message)
        self.git(self.primary, "push", "-q", "origin", "develop")
        return head

    def member_check(self, member_worktree: Path, tests: str) -> dict:
        return self.make_json(
            member_worktree,
            "batch-member-check",
            TESTS=tests,
            PREFLIGHT_CMD=self.preflight_command,
            REVIEW_CMD=self.review_command,
        )


def submit(server: McpServer, payload: dict, generation: str | None = None) -> dict:
    results: dict[str, dict] = {}
    for step in payload["steps"]:
        arguments = dict(step["arguments"])
        if step["tool"] == "prepare":
            arguments["generation"] = generation
        data = server.ok(step["tool"], **arguments)
        if step["tool"] == "take":
            generation = data["claim"]["generation"]
        results[step["tool"]] = data
    return results


@contextlib.contextmanager
def checkout(world: World, path: Path):
    saved_env = dict(os.environ)
    saved_cwd = Path.cwd()
    os.environ.update(world.env)
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(saved_cwd)
        os.environ.clear()
        os.environ.update(saved_env)


class Loop:
    def __init__(
        self,
        world: World,
        leader: McpServer,
        batch: str,
        scope: dict[str, list[str]],
        dependencies: dict[str, list[str]] | None = None,
    ) -> None:
        self.world = world
        self.leader = leader
        self.batch = batch
        self.names = list(scope)
        self.scope = {self.mid(name): patterns for name, patterns in scope.items()}
        self.dependencies = {self.mid(name): [self.mid(d) for d in deps] for name, deps in (dependencies or {}).items()}
        self.generation = ""
        self.integration: Path = world.primary
        self.worktrees: dict[str, Path] = {}
        self.claims: dict[str, str] = {}
        self.reminders: list[str] = []

    def mid(self, name: str) -> str:
        return f"{self.batch}-{name}"

    @property
    def branch(self) -> str:
        return f"feat/{self.batch}"

    def register(self) -> dict:
        for member in self.scope:
            self.leader.ok(
                "create_item",
                id=member,
                title=f"member {member}",
                acceptance=[f"{member} behaves as specified"],
            )
        scope_file = self.world.root / f"{self.batch}-scope.json"
        scope_file.write_text(json.dumps(self.scope))
        variables = {
            "BATCH": self.batch,
            "MEMBERS": ",".join(self.scope),
            "SCOPE_JSON": str(scope_file),
            "PROJECT_ID": "disposable",
            "CONFIRM_CUTOVER": "1",
        }
        if self.dependencies:
            variables["DEPENDENCIES"] = ",".join(
                f"{member}={'+'.join(deps)}" for member, deps in self.dependencies.items()
            )
        start = self.world.make_json(self.world.primary, "batch-start", **variables)
        self.integration = Path(start["worktree"])
        self.generation = start["arguments"]["owner_generation"]
        registered = self.leader.ok("register_batch", **start["arguments"])
        for step in start["after_register"]["steps"]:
            self.leader.ok(step["tool"], **step["arguments"])
        self.reminders = start["after_register"]["reminders"]
        return registered

    def member(self, name: str) -> Path:
        created = self.world.make_json(self.integration, "batch-member-create", MEMBER=self.mid(name))
        self.worktrees[name] = Path(created["worktree"])
        return self.worktrees[name]

    def implement(self, name: str, files: dict[str, str]) -> str:
        return self.world.commit_files(self.worktrees[name], files, f"feat: implement {name}")

    def check(self, name: str, tests: str) -> dict:
        return self.world.member_check(self.worktrees[name], tests)

    def land(self, name: str) -> dict:
        deps = ",".join(self.dependencies.get(self.mid(name), []))
        variables = {"MEMBER": self.mid(name)}
        if deps:
            variables["DEPENDENCIES"] = deps
        return self.world.make_json(self.integration, "batch-land", **variables)

    def claim(self, name: str) -> str:
        self.claims[name] = self.leader.ok("take", id=self.mid(name))["claim"]["generation"]
        return self.claims[name]

    def prepare(self, name: str, landed: dict) -> dict:
        return submit(self.leader, landed["payload"], self.claims.get(name))

    def land_and_prepare(self, name: str) -> dict:
        if self.dependencies.get(self.mid(name)):
            self.claim(name)
        landed = self.land(name)
        assert landed["status"] == "merged", landed
        self.prepare(name, landed)
        return landed

    def head(self) -> str:
        return self.world.git(self.integration, "rev-parse", "HEAD")

    def status(self) -> dict:
        return self.world.make_json(self.integration, "batch-status")

    def tracker(self) -> dict:
        summary = self.leader.ok("show_batch", batch_id=self.batch)
        tracker = {key: value for key, value in summary.items() if key not in ("sections", "rev")}
        for name in summary["sections"]:
            if name in summary:
                continue
            window = ""
            offset = 0
            while True:
                page = self.leader.ok("show_batch", batch_id=self.batch, field=name, offset=offset, rev=summary["rev"])
                window += page["window"]
                if "continuation" not in page:
                    break
                offset = page["continuation"]["offset"]
            tracker[name] = json.loads(window) if window else []
        return tracker

    def ledger(self) -> dict:
        return json.loads((self.integration / ".todo-batch" / self.batch / "ledger.json").read_text(encoding="utf-8"))

    def publish(self) -> None:
        self.world.git(self.integration, "push", "-q", "-u", "origin", self.branch)

    def bind(self, head: str, previous: str | None = None) -> dict:
        arguments: dict[str, object] = {
            "batch_id": self.batch,
            "owner_generation": self.generation,
            "pr_repository": "owner/repo",
            "number": 42,
            "node_id": "PR_kwDODisposable42",
            "head": head,
        }
        if previous is not None:
            arguments["expected_previous_head"] = previous
        return self.leader.ok("bind_batch_pr", **arguments)

    def advance(self, reason: str) -> dict:
        result = self.world.make_json(self.integration, "batch-advance", REASON=reason)
        self.leader.ok("advance_batch_head", **result["payload"]["arguments"])
        return result

    def evidence(self) -> dict:
        return self.world.make_json(self.integration, "batch-evidence")
