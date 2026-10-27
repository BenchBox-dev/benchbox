from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
MAKEFILE = REPO_ROOT / "Makefile"
GATE_SCRIPT = REPO_ROOT / "_project" / "scripts" / "ci_lint_environment_gate.py"
GATE_INVOCATION = "_project/scripts/ci_lint_environment_gate.py"

ALWAYS_ON_GUARDS = (
    "ruff-check",
    "ruff-format",
    "ty-check",
    "artifact-hygiene",
    "agent-instructions",
    "audit-deps",
    "spellcheck",
    "agent-commit-range",
    "comment-policy",
)


def _load_gate():
    spec = importlib.util.spec_from_file_location("ci_lint_environment_gate_under_test", GATE_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _ci_lint_recipe_text() -> str:
    text = MAKEFILE.read_text(encoding="utf-8")
    match = re.search(r"^ci-lint:.*?\n((?:\t.*\n|\n)*)", text, re.MULTILINE)
    assert match, "Makefile has no top-level 'ci-lint:' target"
    return match.group(1)


class TestClassificationTable:
    def test_ci_lint_environment_boundary_skips_agent_identity_on_a_runner(self) -> None:
        gate = _load_gate()
        should_run, reason = gate.runs_on_runner("agent-identity", github_actions=True)
        assert should_run is False
        assert reason, "a skipped guard must carry a printable reason, not a silent False"

    def test_ci_lint_environment_boundary_skips_skill_sync_check_on_a_runner(self) -> None:
        gate = _load_gate()
        should_run, reason = gate.runs_on_runner("skill-sync-check", github_actions=True)
        assert should_run is False
        assert reason, "a skipped guard must carry a printable reason, not a silent False"

    def test_skill_sync_skip_reason_names_a_real_required_ci_job(self) -> None:
        gate = _load_gate()
        should_run, reason = gate.runs_on_runner("skill-sync-check", github_actions=True)
        workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))

        assert should_run is False
        assert reason is not None and "skill-integrity" in reason
        assert "skill-integrity" in workflow["jobs"]
        assert "skill-integrity" in workflow["jobs"]["tooling"]["needs"]

    def test_ci_lint_environment_boundary_never_gates_the_commit_range_control(self) -> None:
        gate = _load_gate()
        for github_actions in (True, False):
            should_run, reason = gate.runs_on_runner("agent-commit-range", github_actions=github_actions)
            assert should_run is True
            assert reason is None

    def test_ci_lint_environment_boundary_leaves_real_guards_unconditional(self) -> None:
        gate = _load_gate()
        for slug in ALWAYS_ON_GUARDS:
            should_run, reason = gate.runs_on_runner(slug, github_actions=True)
            assert should_run is True, f"{slug!r} must stay unconditional on a CI runner"
            assert reason is None

    def test_ci_lint_environment_boundary_only_narrows_ci_never_local(self) -> None:
        gate = _load_gate()
        for slug in ("agent-identity", "skill-sync-check", *ALWAYS_ON_GUARDS):
            should_run, reason = gate.runs_on_runner(slug, github_actions=False)
            assert should_run is True, f"{slug!r} must run locally regardless of the CI exception table"
            assert reason is None


class TestGateCli:
    def test_ci_lint_environment_boundary_cli_skips_on_a_runner(self, monkeypatch, capsys) -> None:
        gate = _load_gate()
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        assert gate.main(["agent-identity"]) == 0
        assert capsys.readouterr().out.strip() == gate.DECISION_SKIP

    def test_ci_lint_environment_boundary_cli_runs_off_a_runner(self, monkeypatch, capsys) -> None:
        gate = _load_gate()
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        assert gate.main(["agent-identity"]) == 0
        assert capsys.readouterr().out.strip() == gate.DECISION_RUN

    def test_ci_lint_environment_boundary_cli_ignores_non_true_values(self, monkeypatch, capsys) -> None:
        gate = _load_gate()
        for value in ("false", "", "1", "True"):
            monkeypatch.setenv("GITHUB_ACTIONS", value)
            gate.main(["agent-identity"])
            assert capsys.readouterr().out.strip() == gate.DECISION_RUN, (
                f"GITHUB_ACTIONS={value!r} was treated as a runner, so real guards would be skipped"
            )


class TestMakefileWiring:
    def test_ci_lint_environment_boundary_gates_agent_identity_check_on_a_runner(self) -> None:
        body = _ci_lint_recipe_text()
        gate_call = f"{GATE_INVOCATION} agent-identity"
        assert gate_call in body, "ci-lint does not consult the environment gate before agent-identity-check"
        gate_index = body.index(gate_call)
        guard_index = body.index("$(MAKE) agent-identity-check")
        assert gate_index < guard_index, "the gate must run BEFORE agent-identity-check, not after or not at all"

    def test_ci_lint_environment_boundary_gates_skill_sync_check_on_a_runner(self) -> None:
        body = _ci_lint_recipe_text()
        gate_call = f"{GATE_INVOCATION} skill-sync-check"
        assert gate_call in body, "ci-lint does not consult the environment gate before skill-sync-check"
        gate_index = body.index(gate_call)
        guard_index = body.index("$(MAKE) skill-sync-check")
        assert gate_index < guard_index, "the gate must run BEFORE skill-sync-check, not after or not at all"

    def test_ci_lint_environment_boundary_never_gates_commit_range_in_the_makefile(self) -> None:
        body = _ci_lint_recipe_text()
        assert f"{GATE_INVOCATION} agent-commit-range" not in body, (
            "agent-commit-range-check must never be routed through the environment gate -- "
            "it is the merge-time authorship control and stays unconditional"
        )

    def test_ci_lint_environment_boundary_leaves_real_guard_commands_unconditional(self) -> None:
        body = _ci_lint_recipe_text()
        for command in (
            "uv run ruff check .",
            "uv run ruff format --check .",
            "$(MAKE) typecheck",
            "$(MAKE) artifact-hygiene",
            "$(MAKE) agent-instructions-check",
            "$(MAKE) agent-commit-range-check",
            "$(MAKE) audit-deps",
            "$(MAKE) spellcheck",
        ):
            assert command in body, f"expected guard command {command!r} missing from ci-lint recipe"

    def test_ci_lint_environment_boundary_uses_one_general_mechanism(self) -> None:
        body = _ci_lint_recipe_text()
        assert body.count(GATE_INVOCATION) == 2, (
            f"expected exactly 2 calls into {GATE_INVOCATION} (agent-identity, skill-sync-check); "
            "a differing count means the general mechanism was abandoned for a per-member one-off, "
            "or a guard was gated that should not be"
        )

    def test_ci_lint_runs_one_type_check_through_the_bundled_runtime_wrapper(self) -> None:
        body = _ci_lint_recipe_text()
        assert body.count("$(MAKE) typecheck;") == 1
        assert "uv run ty check;" not in body
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert workflow.count("run: make typecheck") == 1
        assert "run: uv run ty check\n" not in workflow


class TestGateFailsClosed:
    def test_ci_lint_environment_boundary_decision_is_on_stdout_not_the_exit_status(self) -> None:
        gate = _load_gate()
        assert gate.main(["agent-identity"]) == 0, "a RUN decision must not be signalled by a non-zero exit"
        assert gate.main(["skill-sync-check"]) == 0, "a SKIP decision must not be signalled by a non-zero exit"

    def test_ci_lint_environment_boundary_emits_a_single_decision_token(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        gate = _load_gate()
        monkeypatch.setenv("GITHUB_ACTIONS", "true")

        gate.main(["skill-sync-check"])
        captured = capsys.readouterr()
        assert captured.out.strip() == gate.DECISION_SKIP
        assert "hardcoded" in captured.err or "does not exist on a runner" in captured.err, (
            "the skip reason no longer reaches stderr, so an Actions log shows a guard vanishing with no explanation"
        )

        gate.main(["ruff-check"])
        assert capsys.readouterr().out.strip() == gate.DECISION_RUN

    def test_ci_lint_environment_boundary_runs_the_guard_when_the_gate_is_broken(self) -> None:
        lines = _ci_lint_recipe_text().splitlines()
        for slug, guard in (("agent-identity", "agent-identity-check"), ("skill-sync-check", "skill-sync-check")):
            start = next(
                (i for i, line in enumerate(lines) if f"{GATE_INVOCATION} {slug}" in line),
                None,
            )
            assert start is not None, f"ci-lint never consults the gate for {slug!r}"
            end = next(
                (i for i in range(start + 1, len(lines)) if f"$(MAKE) {guard}" in lines[i]),
                None,
            )
            assert end is not None, f"no {guard!r} invocation follows its gate call"

            block = "\n".join(line.rstrip("\\").strip() for line in lines[start:end]).replace("$$", "$")
            broken = re.sub(r"uv run\b.*?" + re.escape(slug), "command-that-does-not-exist", block, count=1)
            assert "command-that-does-not-exist" in broken, f"could not neutralise the gate command for {slug!r}"

            result = subprocess.run(
                ["sh", "-c", f"{broken} echo RAN; else echo SKIPPED; fi"],
                capture_output=True,
                text=True,
            )
            assert result.stdout.strip() == "RAN", (
                f"a broken gate silently skips {guard}: the recipe treats 'could not decide' as 'skip', "
                f"so the guard disappears with nothing recorded in `failed`. Shell said "
                f"{result.stdout.strip()!r} for block:\n{broken}"
            )
