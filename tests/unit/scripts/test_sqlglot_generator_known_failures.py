from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest
from check_sqlglot_generator_known_failures import EXPECTED_REPLAY_COMMAND_TEMPLATE, main, validate_policy

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _known_failure(**overrides: object) -> dict[str, object]:
    failure: dict[str, object] = {
        "id": "postgres-to-duckdb-seed-42-case-0",
        "known_fail_since": "2026-08-25",
        "owner": "@joeharris76",
        "issue_url": "https://github.com/tobymao/sqlglot/issues/9999",
        "seed": 42,
        "sqlglot_version": "30.6.0",
        "source_dialect": "postgres",
        "target_dialect": "duckdb",
        "failure_artifact": "_project/sqlglot-upstream/failures/postgres-to-duckdb-seed-42.json",
    }
    failure.update(overrides)
    return failure


def _policy(*known_failures: object, **overrides: object) -> dict[str, object]:
    policy: dict[str, object] = {
        "version": 1,
        "mode": "advisory_with_age",
        "owner": "@joeharris76",
        "max_known_failure_age_days": 7,
        "replay_command_template": EXPECTED_REPLAY_COMMAND_TEMPLATE,
        "known_failures": list(known_failures),
    }
    policy.update(overrides)
    return policy


def _errors(policy: object, today: str = "2026-08-31", repo_root: Path | None = None) -> list[str]:
    root = repo_root or Path(__file__).resolve().parents[3]
    return validate_policy(policy, date.fromisoformat(today), root)


def _write_artifact(repo_root: Path, failure: dict[str, object], **overrides: object) -> Path:
    artifact = {
        field: failure[field] for field in ("id", "seed", "sqlglot_version", "source_dialect", "target_dialect")
    }
    artifact.update(
        {
            "schema": "sqlglot-generator-failure-v1",
            "case_index": 0,
            "case_seed": failure["seed"],
            "input_sql": "SELECT 1",
            "minimized_sql": "SELECT 1",
            "failing_shapes": ["target_to_target"],
            "outcomes": {
                "target_to_target": {
                    "status": "fail",
                    "error_type": "ParseError",
                    "error": "ParseError: synthetic",
                },
                "postgres_to_target": {"status": "pass", "error_type": None, "error": None},
            },
            "replay_command": EXPECTED_REPLAY_COMMAND_TEMPLATE,
        }
    )
    artifact.update(overrides)
    path = repo_root / str(failure["failure_artifact"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact), encoding="utf-8")
    return path


def _write_policy(tmp_path: Path, policy: object) -> Path:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    return path


def test_empty_known_failure_list_passes() -> None:
    assert _errors(_policy()) == []


def test_age_six_passes_and_age_seven_fails(tmp_path: Path) -> None:
    failure = _known_failure()
    _write_artifact(tmp_path, failure)
    policy = _policy(failure)
    assert _errors(policy, today="2026-08-31", repo_root=tmp_path) == []
    assert "known failure is 7 days old" in "\n".join(_errors(policy, today="2026-09-01", repo_root=tmp_path))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"version": 2}, "policy.version"),
        ({"version": 1.0}, "policy.version"),
        ({"mode": "ADVISORY"}, "lowercase value"),
        ({"owner": "@somebody-else"}, "policy.owner"),
        ({"max_known_failure_age_days": 8}, "no greater than 7"),
        ({"max_known_failure_age_days": 6}, "must be exactly 7"),
        ({"max_known_failure_age_days": True}, "positive bounded integer"),
        ({"replay_command_template": "python replay.py --seed {seed}"}, "exact deterministic replay command"),
    ],
)
def test_top_level_policy_values_fail_closed(overrides: dict[str, object], message: str) -> None:
    assert message in "\n".join(_errors(_policy(**overrides)))


def test_missing_and_unknown_top_level_fields_are_rejected() -> None:
    policy = _policy(unexpected=True)
    del policy["owner"]
    errors = "\n".join(_errors(policy))
    assert "missing fields: owner" in errors
    assert "unknown fields: unexpected" in errors


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"id": " "}, ".id: must be a non-empty string"),
        ({"known_fail_since": "2026-8-25"}, "ISO date format"),
        ({"owner": "@somebody-else"}, ".owner: must be '@joeharris76'"),
        ({"issue_url": "http://github.com/tobymao/sqlglot/issues/9999"}, "canonical https://github.com"),
        ({"issue_url": "https://bad host/issues/1"}, "canonical https://github.com"),
        ({"issue_url": "https://[bad"}, "canonical https://github.com"),
        ({"seed": -1}, ".seed: must be a non-negative integer"),
        ({"seed": True}, ".seed: must be a non-negative integer"),
        ({"sqlglot_version": ""}, ".sqlglot_version: must be a non-empty string"),
        ({"source_dialect": ""}, ".source_dialect: must be a non-empty string"),
        ({"target_dialect": ""}, ".target_dialect: must be a non-empty string"),
        ({"failure_artifact": "/tmp/failure.json"}, "repository-relative POSIX path"),
        ({"failure_artifact": "C:/tmp/failure.json"}, "repository-relative POSIX path"),
        ({"failure_artifact": "C:failure.json"}, "repository-relative POSIX path"),
        ({"failure_artifact": "../failure.json"}, "repository-relative POSIX path"),
    ],
)
def test_known_failure_field_validation(overrides: dict[str, object], message: str) -> None:
    assert message in "\n".join(_errors(_policy(_known_failure(**overrides))))


def test_missing_and_unknown_known_failure_fields_are_rejected() -> None:
    failure = _known_failure(unexpected=True)
    del failure["issue_url"]
    errors = "\n".join(_errors(_policy(failure)))
    assert "missing fields: issue_url" in errors
    assert "unknown fields: unexpected" in errors


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"sqlglot_version": "30.6.0; echo injected"}, "shell-safe replay token"),
        ({"sqlglot_version": "30.6.0$(id)"}, "shell-safe replay token"),
        ({"sqlglot_version": '"30.6.0"'}, "shell-safe replay token"),
        ({"source_dialect": "postgres;echo"}, "shell-safe replay token"),
        ({"target_dialect": "--help"}, "shell-safe replay token"),
        ({"failure_artifact": "artifacts/failure $(id).json"}, "shell-safe normalized"),
        ({"failure_artifact": "-artifacts/failure.json"}, "shell-safe normalized"),
    ],
)
def test_replay_metadata_rejects_shell_metacharacters_and_option_injection(
    overrides: dict[str, object], message: str
) -> None:
    assert message in "\n".join(_errors(_policy(_known_failure(**overrides))))


@pytest.mark.parametrize(
    "issue_url",
    [
        "https://example.com/not-upstream",
        "https://github.com.evil.example/tobymao/sqlglot/issues/1",
        "https://github.com/tobymao/sqlglot/pull/1",
        "https://github.com/tobymao/sqlglot/issues/0",
        "https://github.com/tobymao/sqlglot/issues/not-a-number",
        "https://github.com/tobymao/sqlglot/issues/1?redirect=1",
    ],
)
def test_issue_url_must_be_a_canonical_sqlglot_issue(issue_url: str) -> None:
    assert "canonical https://github.com" in "\n".join(_errors(_policy(_known_failure(issue_url=issue_url))))


def test_duplicate_ids_are_rejected() -> None:
    duplicate = copy.deepcopy(_known_failure())
    assert "duplicate id 'postgres-to-duckdb-seed-42-case-0'" in "\n".join(
        _errors(_policy(_known_failure(), duplicate))
    )


def test_future_known_failure_date_is_rejected() -> None:
    errors = "\n".join(_errors(_policy(_known_failure(known_fail_since="2026-09-01"))))
    assert "date is in the future" in errors


def test_missing_artifact_and_directory_are_rejected(tmp_path: Path) -> None:
    failure = _known_failure(failure_artifact="artifacts/missing.json")
    errors = "\n".join(_errors(_policy(failure), repo_root=tmp_path))
    assert "must resolve to an existing regular file under the repository" in errors

    directory = tmp_path / "artifacts" / "directory.json"
    directory.mkdir(parents=True)
    failure["failure_artifact"] = "artifacts/directory.json"
    errors = "\n".join(_errors(_policy(failure), repo_root=tmp_path))
    assert "must resolve to an existing regular file under the repository" in errors


def test_artifact_metadata_must_match_policy_entry(tmp_path: Path) -> None:
    failure = _known_failure(failure_artifact="artifacts/mismatch.json")
    _write_artifact(tmp_path, failure, seed=99)
    errors = "\n".join(_errors(_policy(failure), repo_root=tmp_path))
    assert "artifact metadata 'seed' does not match the policy entry" in errors


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"seed": "42"}, "failure_artifact.seed: must be a non-negative integer"),
        ({"case_index": {"value": 0}}, "failure_artifact.case_index: must be a non-negative integer"),
        ({"case_seed": [42]}, "failure_artifact.case_seed: must be a non-negative integer"),
        ({"failing_shapes": [["target_to_target"]]}, "artifact failing_shapes is invalid"),
        ({"outcomes": ["target_to_target"]}, "artifact outcomes must contain both call shapes"),
        (
            {
                "outcomes": {
                    "target_to_target": {"status": [], "error_type": "ParseError", "error": "ParseError: bad"},
                    "postgres_to_target": {"status": "pass", "error_type": None, "error": None},
                }
            },
            "artifact outcome for target_to_target is invalid",
        ),
    ],
)
def test_malformed_artifact_scalar_and_container_types_are_rejected_deterministically(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    failure = _known_failure(failure_artifact="artifacts/malformed-types.json")
    _write_artifact(tmp_path, failure, **overrides)
    policy = _policy(failure)

    first = _errors(policy, repo_root=tmp_path)
    second = _errors(policy, repo_root=tmp_path)

    assert first == second
    assert message in "\n".join(first)


def test_artifact_replay_command_must_match_portable_policy_template(tmp_path: Path) -> None:
    failure = _known_failure(failure_artifact="artifacts/stale-command.json")
    _write_artifact(tmp_path, failure, replay_command="uv run generator.py --failure-artifact /runner/temp/file.json")

    errors = "\n".join(_errors(_policy(failure), repo_root=tmp_path))
    assert "artifact replay command does not match the policy template" in errors


def test_artifact_must_be_valid_json_object_with_required_metadata(tmp_path: Path) -> None:
    failure = _known_failure(failure_artifact="artifacts/invalid.json")
    artifact_path = tmp_path / "artifacts" / "invalid.json"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("not-json", encoding="utf-8")
    assert "must contain valid UTF-8 JSON" in "\n".join(_errors(_policy(failure), repo_root=tmp_path))

    artifact_path.write_text("[]", encoding="utf-8")
    assert "artifact JSON must be an object" in "\n".join(_errors(_policy(failure), repo_root=tmp_path))

    artifact_path.write_text(json.dumps({"id": failure["id"]}), encoding="utf-8")
    assert "artifact is missing metadata field 'seed'" in "\n".join(_errors(_policy(failure), repo_root=tmp_path))

    artifact_path.write_bytes(b"\xff")
    assert "must contain valid UTF-8 JSON" in "\n".join(_errors(_policy(failure), repo_root=tmp_path))


def test_cli_supports_policy_and_deterministic_today(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    failure = _known_failure(failure_artifact="artifacts/failure.json")
    _write_artifact(tmp_path, failure)
    path = _write_policy(tmp_path, _policy(failure))
    assert main(["--policy", str(path), "--today", "2026-08-31"], repo_root=tmp_path) == 0
    assert "1 unexpired known failure(s)" in capsys.readouterr().out
    assert main(["--policy", str(path), "--today", "2026-09-01"], repo_root=tmp_path) == 1
    assert "known failure is 7 days old" in capsys.readouterr().err


def test_cli_rejects_invalid_today_and_invalid_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    valid_path = _write_policy(tmp_path, _policy())
    assert main(["--policy", str(valid_path), "--today", "2026-02-30"]) == 1
    assert "valid calendar date" in capsys.readouterr().err

    valid_path.write_text("{not-json", encoding="utf-8")
    assert main(["--policy", str(valid_path), "--today", "2026-08-31"]) == 1
    assert "could not load policy" in capsys.readouterr().err
