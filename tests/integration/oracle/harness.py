from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .fake_github import APP_TOKEN, GH_HOST, JOB_TOKEN, FakeGitHub

REPO_ROOT = Path(__file__).resolve().parents[3]
SLUG = "BenchBox-dev/BenchBox"
CHECKER = "benchbox/core/equivalence/checker.py"
RECORDED_STREAM = REPO_ROOT / "tests" / "fixtures" / "oracle_reviewers" / "omp_multi_turn_review.jsonl"
POLICY = ".github/oracle-reviewers.yml"
GATEWAY_TOKEN = "gwtok-" + "Q7mZ4kLp" * 5
PINNED_OMP = "18.12.1"
STEP_TIMEOUT = 120


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout.strip()


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
        self._seed()
        self.github = FakeGitHub(SLUG, self.repo).start()
        self._install_omp()

    def close(self) -> None:
        self.github.stop()

    def _seed(self) -> None:
        self.repo.mkdir()
        git(self.repo, "init", "--quiet", "--initial-branch=develop")
        self.write({"README.md": "seed\n", CHECKER: "def checker(a, b):\n    return a == b\n"}, "seed")

    def write(self, files: dict[str, str], message: str) -> str:
        for relative, text in files.items():
            target = self.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            git(self.repo, "add", relative)
        git(self.repo, "commit", "--quiet", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")

    def branch(self, name: str, files: dict[str, str], message: str, base: str = "develop") -> str:
        git(self.repo, "switch", "--quiet", "-C", name, base)
        sha = self.write(files, message)
        git(self.repo, "switch", "--quiet", "develop")
        return sha

    def push(self, name: str, files: dict[str, str], message: str) -> str:
        git(self.repo, "switch", "--quiet", name)
        sha = self.write(files, message)
        git(self.repo, "switch", "--quiet", "develop")
        return sha

    def _install_omp(self) -> None:
        source = Path(__file__).with_name("fake_omp.py").read_text(encoding="utf-8")
        target = self.bin / "omp"
        target.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
        target.chmod(target.stat().st_mode | stat.S_IXUSR)
        security = self.bin / "security"
        security.write_text("#!/bin/sh\nexit 44\n", encoding="utf-8")
        security.chmod(security.stat().st_mode | stat.S_IXUSR)
        self.script_omp([])

    def script_omp(self, replies: list[dict[str, Any]], leak_env: str | None = None) -> None:
        script = {"version": PINNED_OMP, "stream": str(RECORDED_STREAM), "replies": replies, "leak_env": leak_env}
        (self.bin / "script.json").write_text(json.dumps(script), encoding="utf-8")
        (self.bin / "calls.json").unlink(missing_ok=True)

    def omp_calls(self) -> int:
        path = self.bin / "calls.json"
        return len(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else 0

    def base_env(self, temp: Path | None = None) -> dict[str, str]:
        proxy = f"http://127.0.0.1:{self.github.port}"
        env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.home),
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

    def review(
        self,
        pr: int,
        *,
        event_name: str = "pull_request_target",
        action: str = "synchronize",
        classification: tuple[str, str] = ("medium", "standard"),
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
            event = {"inputs": {"pr": str(pr)}}
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
            ["plan", "--policy", POLICY, "--pr", str(pr), "--out", str(plan_dir)],
            {**env, "GH_TOKEN": JOB_TOKEN},
        )
        record.steps["plan"] = plan
        decision, post = plan.outputs.get("decision"), plan.outputs.get("post") == "true"
        if decision == "review":
            workspace = temp / "workspace"
            git(self.repo, "clone", "--quiet", "--no-checkout", str(self.repo), str(workspace))
            git(workspace, "checkout", "--quiet", "--detach", plan.outputs["head_sha"])
            self._classify(record.plan, classification_path, classification)
            record.steps["attempts"] = self.cli(
                "attempts",
                [
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
                ],
                {**env, "OMP_GATEWAY_TOKEN": GATEWAY_TOKEN, "ORACLE_GATEWAY_URL": "https://gateway.example.invalid"},
            )
        if not post:
            self.github.complete_run(run, "success")
            return record
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
            ],
            env,
        )
        record.steps["redact"] = self.cli(
            "redact",
            ["redact-check", "--path", str(attempts_dir), "--path", str(final_dir), "--env", "JOB_TOKEN"],
            {**env, "JOB_TOKEN": JOB_TOKEN, "OMP_GATEWAY_TOKEN": GATEWAY_TOKEN},
        )
        guard = self.cli("guard", ["guard", "--plan", str(plan_dir / "plan.json")], {**env, "GH_TOKEN": APP_TOKEN})
        record.steps["guard"] = guard
        finalized = record.steps["finalize"].outputs
        if guard.outputs.get("skip") != "true":
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
