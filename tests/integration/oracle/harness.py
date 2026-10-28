from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from _project.scripts.oracle_reviewers import omp_pin

from .fake_github import APP_TOKEN, GH_HOST, JOB_TOKEN, OWNER_TOKEN, FakeGitHub
from .fake_jev import FakeJev

REPO_ROOT = Path(__file__).resolve().parents[3]
SLUG = "BenchBox-dev/BenchBox"
CHECKER = "benchbox/core/equivalence/checker.py"
RECORDED_STREAM = REPO_ROOT / "tests" / "fixtures" / "oracle_reviewers" / "omp_multi_turn_review.jsonl"
POLICY = ".github/oracle-reviewers.yml"
TRUSTED_FILES = (
    POLICY,
    ".github/workflows/oracle-verdict.yml",
    ".github/soundness-paths.txt",
    ".github/inert-paths.txt",
)
GATEWAY_TOKEN = "gwtok-" + "Q7mZ4kLp" * 5
JEV_KEY = "jevk-" + "T5nB8wXc" * 4
PINNED_OMP = "18.12.1"
SHA256_LINE = re.compile(r"sha256: [0-9a-f]{64}")
URL_LINE = re.compile(r"url: https://\S+")
INSTALL_STEP = "Install omp"
WORKFLOW = ".github/workflows/oracle-verdict.yml"
UNREACHABLE_BUILD = "https://127.0.0.1:9/omp"
STEP_TIMEOUT = 120
COMMIT_AGE = timedelta(hours=2)


def git(path: Path, *args: str) -> str:
    moment = (datetime.now(UTC) - COMMIT_AGE).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        encoding="utf-8",
        check=True,
        env={**os.environ, "GIT_AUTHOR_DATE": moment, "GIT_COMMITTER_DATE": moment},
    )
    return result.stdout.strip()


def ago(minutes: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def outputs(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            values[key] = value
    return values


@dataclass
class Step:
    name: str
    code: int
    stdout: str
    stderr: str
    outputs: dict[str, str]


@dataclass
class ReviewRun:
    run: dict[str, Any]
    temp: Path
    steps: dict[str, Step] = field(default_factory=dict)

    @property
    def plan(self) -> dict[str, Any]:
        path = self.temp / "oracle-plan" / "plan.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def attempts(self) -> list[dict[str, Any]]:
        folder = self.temp / "oracle-attempts"
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("attempt-*.json"))]

    def captures(self) -> list[dict[str, Any]]:
        folder = self.temp / "oracle-attempts" / "captures"
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("capture-*.json"))]


class Harness:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.repo = root / "repo"
        self.home = root / "home"
        self.bin = root / "bin"
        for folder in (self.home, self.bin, root / "gh-config"):
            folder.mkdir(parents=True)
        self._install_omp()
        self.policy_file = root / "oracle-reviewers.yml"
        self.policy_file.write_text(self._pinned_policy(), encoding="utf-8")
        self._seed()
        self.github = FakeGitHub(SLUG, self.repo).start()
        self.jev = FakeJev().start()

    @property
    def omp_cache(self) -> Path:
        return self.root / "omp-cache"

    def close(self) -> None:
        self.github.stop()
        self.jev.stop()

    def _pinned_policy(self) -> str:
        text = (REPO_ROOT / POLICY).read_text(encoding="utf-8")
        text = SHA256_LINE.sub(f"sha256: {omp_pin.sha256_of(self.bin / 'omp')}", text)
        return URL_LINE.sub(f"url: {UNREACHABLE_BUILD}", text)

    def swap_omp(self) -> None:
        binary = self.bin / "omp"
        binary.write_text(binary.read_text(encoding="utf-8") + "# swapped\n", encoding="utf-8")

    def install_omp_step(self, temp: Path, *, installable: bool) -> Step:
        steps = yaml.safe_load((REPO_ROOT / WORKFLOW).read_text(encoding="utf-8"))["jobs"]["review"]["steps"]
        script = next(step["run"] for step in steps if step.get("name") == INSTALL_STEP)
        trusted = temp / "trusted"
        (trusted / ".github").mkdir(parents=True, exist_ok=True)
        (trusted / POLICY).write_text(self.policy_file.read_text(encoding="utf-8"), encoding="utf-8")
        uv = self.bin / "uv"
        uv.write_text(
            f"#!{sys.executable}\nimport os, sys\n"
            "args = sys.argv[1:]\n"
            "os.execv(sys.executable, [sys.executable, *args[args.index('--') + 2:]])\n",
            encoding="utf-8",
        )
        uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
        env_file = temp / "github-env.txt"
        env_file.write_text("", encoding="utf-8")
        env = {
            **self.base_env(temp),
            "GITHUB_ENV": str(env_file),
            "PYTHONPATH": str(REPO_ROOT),
        }
        if not installable:
            env[omp_pin.CACHE_ENV] = str(self.root / "omp-cache-empty")
        step = self.run(INSTALL_STEP, ["bash", "-e", "-c", script], env, cwd=trusted)
        step.outputs["env"] = env_file.read_text(encoding="utf-8")
        return step

    def _seed(self) -> None:
        self.repo.mkdir()
        git(self.repo, "init", "--quiet", "--initial-branch=develop")
        trusted = {path: (REPO_ROOT / path).read_text(encoding="utf-8") for path in TRUSTED_FILES}
        trusted[POLICY] = self.policy_file.read_text(encoding="utf-8")
        self.write({"README.md": "seed\n", CHECKER: "def checker(a, b):\n    return a == b\n", **trusted}, "seed")

    def write(self, files: dict[str, str | bytes], message: str) -> str:
        for relative, text in files.items():
            target = self.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(text, bytes):
                target.write_bytes(text)
            else:
                target.write_text(text, encoding="utf-8")
            git(self.repo, "add", relative)
        git(self.repo, "commit", "--quiet", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")

    def branch(self, name: str, files: dict[str, str | bytes], message: str, base: str = "develop") -> str:
        git(self.repo, "switch", "--quiet", "-C", name, base)
        sha = self.write(files, message)
        git(self.repo, "switch", "--quiet", "develop")
        return sha

    def rename_branch(self, name: str, old: str, new: str, message: str) -> str:
        git(self.repo, "switch", "--quiet", "-C", name, "develop")
        (self.repo / new).parent.mkdir(parents=True, exist_ok=True)
        git(self.repo, "mv", old, new)
        git(self.repo, "commit", "--quiet", "-m", message)
        sha = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "switch", "--quiet", "develop")
        return sha

    def push(self, name: str, files: dict[str, str | bytes], message: str) -> str:
        git(self.repo, "switch", "--quiet", name)
        sha = self.write(files, message)
        git(self.repo, "switch", "--quiet", "develop")
        return sha

    def _install_omp(self) -> None:
        source = Path(__file__).with_name("fake_omp.py").read_text(encoding="utf-8")
        target = self.bin / "omp"
        target.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
        target.chmod(target.stat().st_mode | stat.S_IXUSR)
        cached = omp_pin.binary_path(omp_pin.Pin(PINNED_OMP, {}), omp_pin.current_platform(), self.omp_cache)
        cached.parent.mkdir(parents=True)
        cached.symlink_to(target)
        security = self.bin / "security"
        security.write_text("#!/bin/sh\nexit 44\n", encoding="utf-8")
        security.chmod(security.stat().st_mode | stat.S_IXUSR)
        self.script_omp([])

    def script_omp(self, replies: list[dict[str, Any]], leak_env: str | None = None) -> None:
        script = {"version": PINNED_OMP, "stream": str(RECORDED_STREAM), "replies": replies, "leak_env": leak_env}
        (self.bin / "script.json").write_text(json.dumps(script), encoding="utf-8")
        (self.bin / "calls.json").unlink(missing_ok=True)
        shutil.rmtree(self.bin / "arrivals", ignore_errors=True)

    def omp_log(self) -> list[dict[str, Any]]:
        path = self.bin / "calls.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []

    def omp_calls(self) -> int:
        return len(self.omp_log())

    def omp_prompts(self) -> list[str]:
        return [call["prompt"] for call in self.omp_log()]

    def base_env(self, temp: Path | None = None) -> dict[str, str]:
        proxy = f"http://127.0.0.1:{self.github.port}"
        env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.home),
            omp_pin.BINARY_ENV: str(self.bin / "omp"),
            omp_pin.CACHE_ENV: str(self.omp_cache),
            "LANG": "C.UTF-8",
            "GH_CONFIG_DIR": str(self.root / "gh-config"),
            "GH_HOST": GH_HOST,
            "GH_NO_UPDATE_NOTIFIER": "1",
            "GH_PROMPT_DISABLED": "1",
            "HTTP_PROXY": proxy,
            "http_proxy": proxy,
            "HTTPS_PROXY": "",
            "https_proxy": "",
            "NO_PROXY": "127.0.0.1",
            "no_proxy": "127.0.0.1",
            "GITHUB_REPOSITORY": SLUG,
            "GITHUB_SERVER_URL": f"https://{GH_HOST}",
            "GITHUB_API_URL": self.github.url,
        }
        if temp is not None:
            env["RUNNER_TEMP"] = str(temp)
            env["GITHUB_OUTPUT"] = str(temp / "output.txt")
            env["GITHUB_STEP_SUMMARY"] = str(temp / "summary.md")
        return env

    def run(
        self, name: str, argv: list[str], env: dict[str, str], cwd: Path = REPO_ROOT, stdin: str | None = None
    ) -> Step:
        output = Path(env["GITHUB_OUTPUT"]) if "GITHUB_OUTPUT" in env else None
        if output is not None:
            output.write_text("", encoding="utf-8")
        result = subprocess.run(
            argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=STEP_TIMEOUT, input=stdin, check=False
        )
        return Step(name, result.returncode, result.stdout, result.stderr, outputs(output) if output else {})

    def cli(self, name: str, args: list[str], env: dict[str, str]) -> Step:
        return self.run(name, [sys.executable, "-m", "_project.scripts.oracle_reviewers.cli", *args], env)

    def gh(self, name: str, args: list[str], env: dict[str, str], token: str) -> Step:
        return self.run(name, ["gh", *args], {**env, "GH_TOKEN": token, "GH_ENTERPRISE_TOKEN": token})

    def _step_runs(self, name: str, record: ReviewRun) -> bool:
        steps = yaml.safe_load((REPO_ROOT / WORKFLOW).read_text(encoding="utf-8"))["jobs"]["review"]["steps"]
        condition = str(next(step for step in steps if step.get("name") == name).get("if", "success()"))

        def out(step: str, key: str) -> str:
            found = record.steps.get(step)
            return found.outputs.get(key, "") if found is not None else ""

        def outcome(step: str) -> str:
            found = record.steps.get(step)
            return "skipped" if found is None else ("success" if found.code == 0 else "failure")

        clauses = [clause.strip() for clause in condition.split("&&")]
        if "always()" not in clauses and any(step.code != 0 for step in record.steps.values()):
            return False
        return all(self._clause_holds(clause, out, outcome) for clause in clauses)

    @staticmethod
    def _clause_holds(clause: str, out: Callable[[str, str], str], outcome: Callable[[str], str]) -> bool:
        if clause == "always()":
            return True
        match = re.fullmatch(r"steps\.(\w+)\.(?:outputs\.(\w+)|(outcome)) (==|!=) '(\w*)'", clause)
        if match is None:
            raise AssertionError(f"unsupported workflow condition clause: {clause}")
        step, key, _, operator, expected = match.groups()
        actual = out(step, key) if key else outcome(step)
        return (actual == expected) if operator == "==" else (actual != expected)

    def _record_attempt(self, record: ReviewRun, run: dict[str, Any], pr: int) -> None:
        if not self._step_runs("Record the retry attempt", record):
            return
        attempt = record.temp / "oracle-final" / "attempt" / "state.json"
        if attempt.is_file():
            self.github.upload_artifact(run, f"oracle-verdict-state-attempt-{pr}", {"state.json": attempt.read_bytes()})

    def review(
        self,
        pr: int,
        *,
        event_name: str = "pull_request_target",
        action: str = "synchronize",
        classification: tuple[str, str] = ("medium", "standard"),
        installable: bool = True,
        swap_after_install: bool = False,
        inputs: dict[str, str] | None = None,
        tamper_attempts: Callable[[Path], None] | None = None,
        plan_env: dict[str, str] | None = None,
        before_guard: Callable[[], None] | None = None,
    ) -> ReviewRun:
        pull = self.github.pulls[pr]
        self.github.refresh(pr)
        head = pull["head"]["sha"]
        trusted = event_name == "pull_request_target"
        run = self.github.add_run(
            "oracle-verdict.yml",
            event=event_name,
            pr=pr,
            head_sha=head if trusted else git(self.repo, "rev-parse", "develop"),
            title_head=head,
            head_branch=pull["head"]["ref"] if trusted else "develop",
            pull_requests=[
                {"number": pr, "base": {"ref": "develop"}, "head": {"ref": pull["head"]["ref"], "sha": head}}
            ]
            if trusted
            else [],
        )
        temp = self.root / f"run-{run['id']}"
        temp.mkdir()
        record = ReviewRun(run, temp)
        event: dict[str, Any] = {"action": action}
        if trusted:
            event["pull_request"] = {"number": pr, "head": {"sha": head}}
        elif event_name == "workflow_dispatch":
            event = {"inputs": {"pr": str(pr), "head": head, **(inputs or {})}}
        (temp / "event.json").write_text(json.dumps(event), encoding="utf-8")
        env = {
            **self.base_env(temp),
            "GITHUB_RUN_ID": str(run["id"]),
            "GITHUB_EVENT_NAME": event_name,
            "GITHUB_EVENT_PATH": str(temp / "event.json"),
            "GITHUB_REF": "refs/heads/develop",
        }
        plan_dir = temp / "oracle-plan"
        attempts_dir = temp / "oracle-attempts"
        final_dir = temp / "oracle-final"
        classification_path = temp / "oracle-classification" / "classification.json"
        plan = self.cli(
            "plan",
            ["plan", "--policy", str(self.policy_file), "--pr", str(pr), "--out", str(plan_dir)],
            {**env, "GH_TOKEN": JOB_TOKEN, **(plan_env or {})},
        )
        record.steps["plan"] = plan
        decision, post = plan.outputs.get("decision"), plan.outputs.get("post") == "true"
        install_state = "installed"
        omp_env: dict[str, str] = {}
        if decision == "review":
            install = self.install_omp_step(temp, installable=installable)
            record.steps["install"] = install
            install_state = install.outputs.get("state", "installed")
            omp_env = dict(line.split("=", 1) for line in install.outputs["env"].splitlines())
            if swap_after_install:
                self.swap_omp()
        if decision == "review" and install_state == "installed":
            workspace = temp / "workspace"
            git(self.repo, "clone", "--quiet", "--no-checkout", str(self.repo), str(workspace))
            git(workspace, "checkout", "--quiet", "--detach", plan.outputs["head_sha"])
            self._classify(record.plan, classification_path, classification)
            loop_args = [
                "attempts",
                "--plan",
                str(plan_dir / "plan.json"),
                "--attempts-dir",
                str(attempts_dir),
                "--classification",
                str(classification_path),
                "--workspace",
                str(workspace),
                "--home-root",
                str(temp),
            ]
            loop_env = {
                **env,
                **omp_env,
                "OMP_GATEWAY_TOKEN": GATEWAY_TOKEN,
                "ORACLE_GATEWAY_URL": "https://gateway.example.invalid",
            }
            recover_args = [
                "recover",
                "--plan",
                str(plan_dir / "plan.json"),
                "--attempts-dir",
                str(attempts_dir),
                "--workspace",
                str(workspace),
            ]
            recover_env = {**env, "TYPESAFE_API_KEY": JEV_KEY, "TYPESAFE_BASE_URL": self.jev.url}
            record.steps["attempts"] = self.cli("attempts", loop_args, loop_env)
            for after in range(1, int(record.plan["max_attempts"])):
                if record.steps["attempts" if after == 1 else f"resume-{after - 1}"].code != 0:
                    break
                record.steps[f"recover-{after}"] = self.cli(f"recover-{after}", recover_args, recover_env)
                if record.steps[f"recover-{after}"].code != 0:
                    break
                record.steps[f"resume-{after}"] = self.cli(f"resume-{after}", loop_args, loop_env)
        if tamper_attempts is not None:
            tamper_attempts(attempts_dir)
        if not post:
            self.github.complete_run(run, "success")
            return record
        finalize_args = ["--omp-install", "failed"] if install_state == "failed" else []
        record.steps["finalize"] = self.cli(
            "finalize",
            [
                "finalize",
                "--plan",
                str(plan_dir / "plan.json"),
                "--attempts-dir",
                str(attempts_dir),
                "--out-dir",
                str(final_dir),
                "--classification",
                str(classification_path),
                *finalize_args,
            ],
            env,
        )
        record.steps["redact"] = self.cli(
            "redact",
            ["redact-check", "--path", str(attempts_dir), "--path", str(final_dir), "--env", "JOB_TOKEN"],
            {**env, "JOB_TOKEN": JOB_TOKEN, "OMP_GATEWAY_TOKEN": GATEWAY_TOKEN},
        )
        if before_guard is not None:
            before_guard()
        guard = self.cli("guard", ["guard", "--plan", str(plan_dir / "plan.json")], {**env, "GH_TOKEN": APP_TOKEN})
        record.steps["guard"] = guard
        finalized = record.steps["finalize"].outputs
        self._record_attempt(record, run, pr)
        if guard.code == 0 and guard.outputs.get("skip") != "true":
            head_sha = plan.outputs["head_sha"]
            status = self.gh(
                "status",
                [
                    "api",
                    "--method",
                    "POST",
                    f"repos/{SLUG}/statuses/{head_sha}",
                    "--input",
                    str(final_dir / "status.json"),
                ],
                env,
                APP_TOKEN,
            )
            record.steps["status"] = status
            if finalized.get("comment") == "true":
                record.steps["comment"] = self.gh(
                    "comment",
                    [
                        "api",
                        "--method",
                        "POST",
                        f"repos/{SLUG}/issues/{pr}/comments",
                        "--input",
                        str(final_dir / "comment.json"),
                    ],
                    env,
                    APP_TOKEN,
                )
            if finalized.get("review") == "true":
                record.steps["review"] = self.gh(
                    "review",
                    [
                        "api",
                        "--method",
                        "POST",
                        f"repos/{SLUG}/pulls/{pr}/reviews",
                        "--input",
                        str(final_dir / "review.json"),
                    ],
                    env,
                    APP_TOKEN,
                )
            if status.code == 0 and finalized.get("has_state") == "true":
                state = (final_dir / "state" / "state.json").read_bytes()
                self.github.upload_artifact(run, f"oracle-verdict-state-{pr}", {"state.json": state})
            base = self.gh("base", ["api", f"repos/{SLUG}/pulls/{pr}", "--jq", ".base.ref"], env, APP_TOKEN)
            if base.stdout.strip() == "develop":
                record.steps["dispatch"] = self.gh(
                    "dispatch",
                    [
                        "workflow",
                        "run",
                        "oracle-gate.yml",
                        "--repo",
                        SLUG,
                        "--ref",
                        "develop",
                        "-f",
                        f"pr={pr}",
                        "-f",
                        f"head_sha={head_sha}",
                    ],
                    env,
                    JOB_TOKEN,
                )
        if record.steps["redact"].code == 0 and (attempts_dir / "captures").is_dir():
            files = {path.name: path.read_bytes() for path in sorted((attempts_dir / "captures").glob("*.json"))}
            self.github.upload_artifact(run, "oracle-verdict-capture", files)
        self.github.complete_run(run, "success")
        return record

    def _classify(self, plan: dict[str, Any], path: Path, classification: tuple[str, str]) -> None:
        from _project.scripts.oracle_reviewers.classify import Classification

        size, template = classification
        path.parent.mkdir(parents=True, exist_ok=True)
        gate_flags = tuple(plan.get("gate_paths", ()))
        value = Classification(size, "routine", template, 0.0, gate_flags, ())
        path.write_text(json.dumps(value.to_json()), encoding="utf-8")

    def gate(self, pr: int, head_sha: str | None = None) -> Step:
        env = {**self.base_env(), "GITHUB_TOKEN": APP_TOKEN, "GH_TOKEN": APP_TOKEN}
        args = [sys.executable, "_project/scripts/oracle_gate.py", "--repo", SLUG, "--pr", str(pr)]
        if head_sha:
            args += ["--head-sha", head_sha]
        return self.run("gate", args, env)

    def sweep(self, *, surface: bool = True) -> Step:
        temp = self.root / f"sweep-{len(list(self.root.glob('sweep-*')))}"
        temp.mkdir()
        env = {**self.base_env(temp), "GH_TOKEN": JOB_TOKEN, "GITHUB_RUN_ID": "1", "GITHUB_EVENT_NAME": "schedule"}
        if surface:
            env["SURFACE_TOKEN"] = APP_TOKEN
        step = self.cli("sweep", ["sweep", "--policy", POLICY], env)
        summary = Path(env["GITHUB_STEP_SUMMARY"])
        step.outputs["summary"] = summary.read_text(encoding="utf-8") if summary.is_file() else ""
        return step

    def arm(self, pr: int, branch: str) -> Step:
        checkout = self.root / f"checkout-{pr}"
        if not checkout.exists():
            git(self.root, "clone", "--quiet", str(self.repo), str(checkout))
            git(checkout, "switch", "--quiet", branch)
        head = git(checkout, "rev-parse", "HEAD")
        env = {**self.base_env(), "GH_TOKEN": OWNER_TOKEN, "GH_ENTERPRISE_TOKEN": OWNER_TOKEN}
        argv = [sys.executable, str(REPO_ROOT / "scripts" / "pr_arm.py"), "--pr", str(pr), "--head", head]
        return self.run("arm", [*argv, "--repo", SLUG], env, cwd=checkout)

    def local_review(self, branch: str, body: str | None = None) -> Step:
        argv, env, checkout = self._local_review_command(branch)
        if body is not None:
            body_file = self.root / "pr-body.md"
            body_file.write_text(body, encoding="utf-8")
            argv = [*argv, "--body-file", str(body_file)]
        return self.run("local-review", argv, env, cwd=checkout)

    def start_local_review(self, branch: str) -> subprocess.Popen[str]:
        argv, env, checkout = self._local_review_command(branch)
        return subprocess.Popen(argv, cwd=checkout, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def finish_local_review(self, process: subprocess.Popen[str]) -> Step:
        stdout, stderr = process.communicate(timeout=STEP_TIMEOUT)
        return Step("local-review", process.returncode, stdout, stderr, {})

    def _local_review_command(self, branch: str) -> tuple[list[str], dict[str, str], Path]:
        checkout = self.root / f"local-{branch.replace('/', '-')}"
        if not checkout.exists():
            git(self.root, "clone", "--quiet", str(self.repo), str(checkout))
            git(checkout, "switch", "--quiet", branch)
            git(checkout, "branch", "--quiet", "-f", "develop", "origin/develop")
        env = {
            **self.base_env(),
            "PYTHONPATH": str(REPO_ROOT),
            "ORACLE_GATEWAY_URL": self.github.url,
            "ORACLE_CLOUD_GATEWAY_TOKEN": GATEWAY_TOKEN,
            "BENCHBOX_LOCAL_ORACLE_DIR": str(self.local_state),
        }
        argv = [sys.executable, "-m", "_project.scripts.oracle_reviewers.cli", "local-review", "--policy", POLICY]
        return [*argv, "--base-ref", "develop"], env, checkout

    @property
    def local_state(self) -> Path:
        return self.root / "local-oracle"

    def local_captures(self) -> list[dict[str, Any]]:
        paths = sorted((self.local_state / "captures").glob("*/capture-*.json"))
        return [json.loads(path.read_text(encoding="utf-8")) for path in paths]
