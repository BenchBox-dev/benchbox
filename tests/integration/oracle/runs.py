from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from _project.scripts.oracle_reviewers import omp_pin

from .fake_github import APP_TOKEN, JOB_TOKEN
from .harness import (
    GATEWAY_TOKEN,
    HELD_SECRET,
    JEV_KEY,
    REPO_ROOT,
    SLUG,
    WORKFLOW,
    ReviewRun,
    Step,
    git,
)

if TYPE_CHECKING:
    from .harness import Harness

STEPS = "steps"
DRIVER = "driver"


@dataclass
class Options:
    classification: tuple[str, str] = ("medium", "standard")
    installable: bool = True
    swap_after_install: bool = False
    tamper_attempts: Callable[[Path], None] | None = None
    plan_env: dict[str, str] | None = None
    before_guard: Callable[[], None] | None = None
    app: bool = True
    shadow: bool = False
    before_compare: Callable[[Path], None] | None = None


def _clause_holds(clause: str, record: ReviewRun) -> bool:
    if clause == "always()":
        return True
    match = re.fullmatch(r"steps\.(\w+)\.(?:outputs\.(\w+)|(outcome)) (==|!=) '(\w*)'", clause)
    if match is None:
        raise AssertionError(f"unsupported workflow condition clause: {clause}")
    step, key, _, operator, expected = match.groups()
    found = record.steps.get(step)
    if key:
        actual = found.outputs.get(key, "") if found is not None else ""
    else:
        actual = "skipped" if found is None else ("success" if found.code == 0 else "failure")
    return (actual == expected) if operator == "==" else (actual != expected)


def _step_runs(name: str, record: ReviewRun) -> bool:
    steps = yaml.safe_load((REPO_ROOT / WORKFLOW).read_text(encoding="utf-8"))["jobs"]["review"]["steps"]
    condition = str(next(step for step in steps if step.get("name") == name).get("if", "success()"))
    clauses = [clause.strip() for clause in condition.split("&&")]
    if "always()" not in clauses and any(step.code != 0 for step in record.steps.values()):
        return False
    return all(_clause_holds(clause, record) for clause in clauses)


class ReviewRunner:
    def __init__(self, harness: Harness, pr: int, record: ReviewRun, env: dict[str, str], options: Options) -> None:
        self.harness = harness
        self.pr = pr
        self.record = record
        self.env = env
        self.options = options
        self.temp = record.temp
        self.plan_dir = self.temp / "oracle-plan"
        self.attempts_dir = self.temp / "oracle-attempts"
        self.final_dir = self.temp / "oracle-final"
        self.classification = self.temp / "oracle-classification" / "classification.json"
        self.workspace = self.temp / "workspace"

    def _drive_env(self) -> dict[str, str]:
        return {
            **self.env,
            "GITHUB_WORKSPACE": str(self.temp),
            "PR_NUMBER": str(self.pr),
            "HAS_APP": "true" if self.options.app else "false",
            "TYPESAFE_BASE_URL": self.harness.jev.url,
        }

    def _phase(self, name: str, args: list[str], extra: dict[str, str] | None = None, key: str | None = None) -> Step:
        step = self.harness.cli(
            name,
            ["drive", "--policy", str(self.harness.policy_file), *args],
            {**self._drive_env(), **(extra or {})},
        )
        self.record.steps[key or name] = step
        return step

    def _cli(self, name: str, args: list[str], extra: dict[str, str]) -> Step:
        step = self.harness.cli(name, args, {**self.env, **extra})
        self.record.steps[name] = step
        return step

    def _checkout(self, head_sha: str) -> None:
        git(self.harness.repo, "clone", "--quiet", "--no-checkout", str(self.harness.repo), str(self.workspace))
        git(self.workspace, "checkout", "--quiet", "--detach", head_sha)
        self.harness._classify(self.record.plan, self.classification, self.options.classification)

    def _loop_env(self, omp_env: dict[str, str]) -> dict[str, str]:
        return {
            **self.env,
            **omp_env,
            "OMP_GATEWAY_TOKEN": GATEWAY_TOKEN,
            "ORACLE_GATEWAY_URL": "https://gateway.example.invalid",
        }

    def _alternate(self, loop: Callable[[str], Step], recover: Callable[[str], Step]) -> None:
        record = self.record
        record.steps["attempts"] = loop("attempts")
        for after in range(1, int(record.plan["max_attempts"])):
            if record.steps["attempts" if after == 1 else f"resume-{after - 1}"].code != 0:
                break
            record.steps[f"recover-{after}"] = recover(f"recover-{after}")
            if record.steps[f"recover-{after}"].code != 0:
                break
            record.steps[f"resume-{after}"] = loop(f"resume-{after}")

    def run(self) -> None:
        decision, post, install_state = self._prepare()
        if self.options.tamper_attempts is not None:
            self.options.tamper_attempts(self.attempts_dir)
        if post:
            self.record.loop_summary = self.record.step_summary()
            self._conclude(install_state)
        else:
            self._idle()
            self.harness.github.complete_run(self.record.run, "success")

    def _idle(self) -> None:
        return None

    def _prepare(self) -> tuple[str | None, bool, str]:
        raise NotImplementedError

    def _conclude(self, install_state: str) -> None:
        raise NotImplementedError

    def _upload_captures(self, redacted: bool) -> None:
        if redacted and (self.attempts_dir / "captures").is_dir():
            files = {path.name: path.read_bytes() for path in sorted((self.attempts_dir / "captures").glob("*.json"))}
            self.harness.github.upload_artifact(self.record.run, "oracle-verdict-capture", files)

    def _upload_state(self, name: str, path: Path) -> None:
        if path.is_file():
            self.harness.github.upload_artifact(self.record.run, name, {"state.json": path.read_bytes()})


class StepsRunner(ReviewRunner):
    def _plan_env(self) -> dict[str, str]:
        return {**self.env, "GH_TOKEN": JOB_TOKEN, **(self.options.plan_env or {})}

    def _prepare(self) -> tuple[str | None, bool, str]:
        harness, record, options = self.harness, self.record, self.options
        plan = self._cli(
            "plan",
            ["plan", "--policy", str(harness.policy_file), "--pr", str(self.pr), "--out", str(self.plan_dir)],
            {"GH_TOKEN": JOB_TOKEN, **(options.plan_env or {})},
        )
        if options.shadow and plan.code == 0:
            self._shadow("shadow-plan", ["prepare", "--shadow"], {"GH_TOKEN": JOB_TOKEN, **(options.plan_env or {})})
        decision, post = plan.outputs.get("decision"), plan.outputs.get("post") == "true"
        install_state = "installed"
        if decision == "review":
            install = harness.install_omp_step(self.temp, installable=options.installable)
            record.steps["install"] = install
            install_state = install.outputs.get("state", "installed")
            omp_env = dict(line.split("=", 1) for line in install.outputs["env"].splitlines())
            if options.swap_after_install:
                harness.swap_omp()
            if install_state == "installed":
                self._checkout(plan.outputs["head_sha"])
                self._loop(omp_env)
        return decision, post, install_state

    def _shadow(self, name: str, args: list[str], extra: dict[str, str]) -> Step:
        return self._phase(name, args, extra)

    def _idle(self) -> None:
        if self.options.shadow and "shadow-plan" in self.record.steps:
            self._compare()

    def _loop(self, omp_env: dict[str, str]) -> None:
        loop_args = [
            "attempts",
            "--plan",
            str(self.plan_dir / "plan.json"),
            "--attempts-dir",
            str(self.attempts_dir),
            "--classification",
            str(self.classification),
            "--workspace",
            str(self.workspace),
            "--home-root",
            str(self.temp),
        ]
        recover_args = [
            "recover",
            "--plan",
            str(self.plan_dir / "plan.json"),
            "--attempts-dir",
            str(self.attempts_dir),
            "--workspace",
            str(self.workspace),
        ]
        recover_env = {"TYPESAFE_API_KEY": JEV_KEY, "TYPESAFE_BASE_URL": self.harness.jev.url}
        self._alternate(
            lambda name: self.harness.cli(name, loop_args, self._loop_env(omp_env)),
            lambda name: self.harness.cli(name, recover_args, {**self.env, **recover_env}),
        )

    def _held(self) -> dict[str, str]:
        return {"JOB_TOKEN": JOB_TOKEN, "OMP_GATEWAY_TOKEN": GATEWAY_TOKEN, "TS_OAUTH_CI_SECRET": HELD_SECRET}

    def _conclude(self, install_state: str) -> None:
        record, harness = self.record, self.harness
        finalize_args = ["--omp-install", "failed"] if install_state == "failed" else []
        self._cli(
            "finalize",
            [
                "finalize",
                "--plan",
                str(self.plan_dir / "plan.json"),
                "--attempts-dir",
                str(self.attempts_dir),
                "--out-dir",
                str(self.final_dir),
                "--classification",
                str(self.classification),
                "--env",
                "JOB_TOKEN",
                *finalize_args,
            ],
            self._held(),
        )
        self._cli(
            "redact",
            [
                "redact-check",
                "--path",
                str(self.attempts_dir),
                "--path",
                str(self.final_dir),
                "--env",
                "JOB_TOKEN",
                "--summary-file",
                str(self.attempts_dir / "summary.json"),
                "--summary-file",
                str(self.final_dir / "summary.json"),
            ],
            self._held(),
        )
        if self.options.shadow and record.steps["redact"].code == 0:
            self._shadow(
                "shadow-finalize", ["finalize", "--shadow"], {**self._held(), "OMP_INSTALL_STATE": install_state}
            )
        if self.options.before_guard is not None:
            self.options.before_guard()
        self._post()
        if self.options.shadow:
            self._compare()
        self._upload_captures(record.steps["redact"].code == 0)
        harness.github.complete_run(record.run, "success")

    def _gh(self, name: str, args: list[str], token: str) -> Step:
        step = self.harness.gh(name, args, self.env, token)
        self.record.steps[name] = step
        return step

    def _post_json(self, name: str, target: str, payload: str) -> Step:
        return self._gh(name, ["api", "--method", "POST", target, "--input", str(self.final_dir / payload)], APP_TOKEN)

    def _post(self) -> None:
        record = self.record
        finalized = record.steps["finalize"].outputs
        head_sha = record.steps["plan"].outputs["head_sha"]
        guard = None
        if self.options.app:
            guard = self._cli("guard", ["guard", "--plan", str(self.plan_dir / "plan.json")], {"GH_TOKEN": APP_TOKEN})
        if _step_runs("Record the retry attempt", record):
            self._upload_state(f"oracle-verdict-state-attempt-{self.pr}", self.final_dir / "attempt" / "state.json")
        if guard is None or guard.code != 0 or guard.outputs.get("skip") == "true":
            return
        repo = f"repos/{SLUG}"
        status = self._post_json("status", f"{repo}/statuses/{head_sha}", "status.json")
        if finalized.get("comment") == "true":
            self._post_json("comment", f"{repo}/issues/{self.pr}/comments", "comment.json")
        if finalized.get("review") == "true":
            self._post_json("review", f"{repo}/pulls/{self.pr}/reviews", "review.json")
        if status.code == 0 and finalized.get("has_state") == "true":
            self._upload_state(f"oracle-verdict-state-{self.pr}", self.final_dir / "state" / "state.json")
        base = self._gh("base", ["api", f"{repo}/pulls/{self.pr}", "--jq", ".base.ref"], APP_TOKEN)
        if base.stdout.strip() == "develop":
            self._dispatch(head_sha)

    def _dispatch(self, head_sha: str) -> None:
        self._gh(
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
                f"pr={self.pr}",
                "-f",
                f"head_sha={head_sha}",
            ],
            JOB_TOKEN,
        )

    def _compare(self) -> None:
        steps = self.record.steps
        if self.options.before_compare is not None:
            self.options.before_compare(self.temp / "oracle-shadow")

        def outcome(name: str) -> str:
            found = steps.get(name)
            return "skipped" if found is None else ("success" if found.code == 0 else "failure")

        guard = steps.get("guard")
        extra = {
            "GUARD_SKIP": guard.outputs.get("skip", "") if guard is not None else "",
            "GUARD_OUTCOME": outcome("guard"),
            "POSTED_STATUS": outcome("status"),
            "POSTED_COMMENT": outcome("comment"),
            "POSTED_REVIEW": outcome("review"),
            "POSTED_GATE": outcome("dispatch"),
        }
        self._shadow("shadow-compare", ["compare"], extra)
        result = self.temp / "oracle-shadow" / "result.json"
        if result.is_file():
            self.harness.github.upload_artifact(
                self.record.run, f"oracle-verdict-shadow-{self.pr}", {"result.json": result.read_bytes()}
            )


class DriverRunner(ReviewRunner):
    def _prepare(self) -> tuple[str | None, bool, str]:
        options = self.options
        plan = self._phase("plan", ["prepare"], {"GH_TOKEN": JOB_TOKEN, **(options.plan_env or {})})
        decision, post = plan.outputs.get("decision"), plan.outputs.get("post") == "true"
        install_state = "installed"
        if decision == "review":
            install_state, omp_env = self._install()
            if options.swap_after_install:
                self.harness.swap_omp()
            if install_state == "installed":
                self._checkout(plan.outputs["head_sha"])
                self._loop(omp_env)
        return decision, post, install_state

    def _install(self) -> tuple[str, dict[str, str]]:
        harness = self.harness
        env_file = self.temp / "github-env.txt"
        env_file.write_text("", encoding="utf-8")
        extra = {"GITHUB_ENV": str(env_file), "PYTHONPATH": str(REPO_ROOT)}
        if not self.options.installable:
            extra[omp_pin.CACHE_ENV] = str(harness.root / "omp-cache-empty")
        step = self._phase("install", ["harness"], extra)
        omp_env = dict(line.split("=", 1) for line in env_file.read_text(encoding="utf-8").splitlines())
        return step.outputs.get("state", "installed"), omp_env

    def _loop(self, omp_env: dict[str, str]) -> None:
        recover_env = {"TYPESAFE_API_KEY": JEV_KEY}
        self._alternate(
            lambda name: self._phase(name, ["attempts"], self._loop_env(omp_env), key=name),
            lambda name: self._phase(name, ["recover"], recover_env, key=name),
        )

    def _conclude(self, install_state: str) -> None:
        record = self.record
        finalized = self._phase(
            "finalize",
            ["finalize"],
            {
                "JOB_TOKEN": JOB_TOKEN,
                "OMP_GATEWAY_TOKEN": GATEWAY_TOKEN,
                "TS_OAUTH_CI_SECRET": HELD_SECRET,
                "OMP_INSTALL_STATE": install_state,
            },
        )
        if finalized.code == 0:
            if self.options.before_guard is not None:
                self.options.before_guard()
            self._publish()
        self._upload_captures(finalized.code == 0)
        self.harness.github.complete_run(record.run, "success")

    def _publish(self) -> None:
        publish = self._phase("publish", ["publish"], {"GH_TOKEN": APP_TOKEN, "JOB_TOKEN": JOB_TOKEN})
        if publish.outputs.get("attempt_state") == "true":
            self._upload_state(f"oracle-verdict-state-attempt-{self.pr}", self.final_dir / "attempt" / "state.json")
        if publish.outputs.get("state_recorded") == "true":
            self._upload_state(f"oracle-verdict-state-{self.pr}", self.final_dir / "state" / "state.json")


def runner_for(mode: str) -> type[ReviewRunner]:
    return {STEPS: StepsRunner, DRIVER: DriverRunner}[mode]
