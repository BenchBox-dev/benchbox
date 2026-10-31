from __future__ import annotations

import sys

import pytest

from _project.scripts.oracle_reviewers import omp_pin

from .fake_github import APP_TOKEN, JOB_TOKEN
from .harness import (
    APP_PRIVATE_KEY,
    CHECKER,
    GATEWAY_TOKEN,
    HELD_SECRET,
    JEV_KEY,
    Harness,
    ReviewRun,
)
from .runs import GATEWAY_URL, phase_keys

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
    pytest.mark.skipif(sys.platform != "linux", reason="ancestor environments are read from /proc"),
]

FIXED_CHECKER = "def checker(a, b):\n    if a is None or b is None:\n        return False\n    return a == b\n"
WITHHELD = {
    "TYPESAFE_API_KEY": JEV_KEY,
    "APP_TOKEN": APP_TOKEN,
    "APP_PRIVATE_KEY": APP_PRIVATE_KEY,
    "JOB_TOKEN": JOB_TOKEN,
    "TS_OAUTH_CI_SECRET": HELD_SECRET,
}
HELD_BY_THE_ATTEMPT_STEP = {"OMP_GATEWAY_TOKEN": GATEWAY_TOKEN, "ORACLE_GATEWAY_URL": GATEWAY_URL}
PHASE_SECRETS = {
    "prepare": {"GH_TOKEN", "PR_NUMBER", "TYPESAFE_API_KEY"},
    "scan-before": {"TYPESAFE_API_KEY", "TS_OAUTH_CI_SECRET", "JOB_TOKEN"},
    "harness": set(),
    "scan-after-join": {"TYPESAFE_API_KEY", "TS_OAUTH_CI_SECRET", "JOB_TOKEN"},
    "isolation": set(),
    "attempts": {"OMP_GATEWAY_TOKEN", "ORACLE_GATEWAY_URL"},
    "recover-1": {"TYPESAFE_API_KEY"},
    "resume-1": {"OMP_GATEWAY_TOKEN", "ORACLE_GATEWAY_URL"},
    "scan-after-exit": {"TYPESAFE_API_KEY", "TS_OAUTH_CI_SECRET", "JOB_TOKEN"},
    "finalize": {
        "OMP_INSTALL_STATE",
        "OMP_GATEWAY_TOKEN",
        "TYPESAFE_API_KEY",
        "ORACLE_GATEWAY_URL",
        "TS_OAUTH_CI_CLIENT_ID",
        "TS_OAUTH_CI_SECRET",
        "JOB_TOKEN",
    },
    "publish": {"GH_TOKEN", "JOB_TOKEN"},
    "dispatch": {"GH_TOKEN", "JOB_TOKEN"},
}


def _reviewed(harness: Harness) -> ReviewRun:
    harness.branch("feat/checker", {CHECKER: FIXED_CHECKER}, "guard None operands")
    harness.github.open_pull(41, "feat/checker")
    harness.script_omp(
        [{"decision": "SHIP", "summary": "No defects.", "files_examined": [CHECKER]}],
        ancestor_probe={**WITHHELD, **HELD_BY_THE_ATTEMPT_STEP},
    )
    return harness.review(41)


def _found(harness: Harness) -> set[str]:
    chain = harness.omp_ancestors()
    assert chain, "the reviewer found no ancestor process to inspect"
    return {name for ancestor in chain for name in ancestor["found"]}


def test_no_withheld_secret_is_readable_in_any_ancestor_environment_of_the_reviewer(harness: Harness) -> None:
    _reviewed(harness)

    assert _found(harness) & set(WITHHELD) == set()


def test_the_gateway_token_and_url_are_readable_in_the_attempt_step_environment(harness: Harness) -> None:
    run = _reviewed(harness)

    assert run.steps["attempts"].code == 0
    assert _found(harness) == set(HELD_BY_THE_ATTEMPT_STEP)


def test_the_environment_file_written_by_the_harness_phase_holds_only_the_binary_path(harness: Harness) -> None:
    run = _reviewed(harness)

    lines = run.steps["harness"].outputs["env"].splitlines()
    assert [line.partition("=")[0] for line in lines] == [omp_pin.BINARY_ENV]
    assert not any(value in run.steps["harness"].outputs["env"] for value in (*WITHHELD.values(), GATEWAY_TOKEN))


def test_every_phase_step_receives_exactly_the_secrets_its_workflow_step_names(harness: Harness) -> None:
    run = _reviewed(harness)

    named = {key: set(step.get("env", {})) for key, step in phase_keys().values()}
    for key, passed in run.step_env.items():
        assert set(passed) - {"ORACLE_APP_PRIVATE_KEY"} == named[key], key
    for key, expected in PHASE_SECRETS.items():
        assert named[key] == expected, key
        assert set(run.step_env[key]) - {"ORACLE_APP_PRIVATE_KEY"} == expected, key


def test_nothing_a_phase_exports_to_later_steps_carries_a_withheld_secret(harness: Harness) -> None:
    run = _reviewed(harness)

    exported = " ".join(step.outputs.get("env", "") for step in run.steps.values()) + " ".join(
        run.exported_env.values()
    )
    assert not any(value in exported for value in (*WITHHELD.values(), APP_PRIVATE_KEY))
    for key in ("attempts", "resume-1"):
        passed = run.step_env.get(key, {})
        assert APP_PRIVATE_KEY not in passed.values()
