from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "docker-integration.yml"


def _load_workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    triggers = workflow.get("on", workflow.get(True))
    assert triggers is not None, "workflow has no `on:` block"
    return triggers


def test_workflow_exists_and_scheduled_nightly() -> None:
    assert WORKFLOW.exists()
    triggers = _triggers(_load_workflow())
    assert "schedule" in triggers, "docker-integration.yml must run on a schedule"
    assert "workflow_dispatch" in triggers, "docker-integration.yml must allow manual runs"


def test_all_three_service_jobs_present() -> None:
    jobs = _load_workflow()["jobs"]
    assert {"postgres", "clickhouse", "trino"} <= set(jobs)


def test_dispatch_input_validated_with_exact_tokens() -> None:
    workflow = _load_workflow()
    assert "validate-input" in workflow["jobs"]
    validate = workflow["jobs"]["validate-input"]
    outputs = validate.get("outputs", {})
    assert set(outputs) >= {"run_postgres", "run_clickhouse", "run_trino"}
    validate_text = "\n".join(str(s.get("run", "")) for s in validate["steps"])
    assert "Unknown service" in validate_text
    for job_name, flag in (
        ("postgres", "run_postgres"),
        ("clickhouse", "run_clickhouse"),
        ("trino", "run_trino"),
    ):
        job = workflow["jobs"][job_name]
        assert flag in job.get("if", ""), f"{job_name} must gate on {flag}"


def test_clickhouse_fixture_matches_compose_password() -> None:
    fixture = (REPO_ROOT / "tests" / "integration" / "platforms" / "test_clickhouse_live.py").read_text(
        encoding="utf-8"
    )
    assert 'password="benchbox"' in fixture


def test_each_job_uses_own_compose_stack_and_guards_skips() -> None:
    workflow = _load_workflow()
    expected = {
        "postgres": ("docker/postgresql/docker-compose.yml", "live_postgresql"),
        "clickhouse": ("docker/clickhouse/docker-compose.yml", "live_clickhouse"),
        "trino": ("docker/trino/docker-compose.yml", "live_trino"),
    }
    for job_name, (compose, marker) in expected.items():
        steps = workflow["jobs"][job_name]["steps"]
        text = "\n".join(str(s.get("run", "")) for s in steps)
        assert compose in text, f"{job_name} must bring up {compose}"
        assert marker in text, f"{job_name} must run the {marker} suite"
        assert "no passing tests" in text, f"{job_name} must fail on all-skipped runs"


def test_postgres_job_selects_the_stream_session_isolation_tests() -> None:
    steps = _load_workflow()["jobs"]["postgres"]["steps"]
    text = "\n".join(str(s.get("run", "")) for s in steps)
    node_id = (
        "tests/integration/test_throughput_session_isolation.py::TestPostgreSQLIndependentConnectionIsolatesSessions"
    )
    assert node_id in text
    assert "tests/integration/platforms/test_postgresql_live.py" in text
    assert '-m "live_postgresql"' in text
    assert "--deselect" not in text


@pytest.mark.parametrize(
    ("service", "compose_path", "image_variable"),
    [
        ("clickhouse", "docker/clickhouse/docker-compose.yml", "CLICKHOUSE_IMAGE"),
        ("trino", "docker/trino/docker-compose.yml", "TRINO_IMAGE"),
    ],
)
def test_ci_mirror_refs_match_compose_default_digests(service: str, compose_path: str, image_variable: str) -> None:
    compose = yaml.safe_load((REPO_ROOT / compose_path).read_text(encoding="utf-8"))
    image = compose["services"][service]["image"]
    prefix = "${" + image_variable + ":-"
    assert image.startswith(prefix) and image.endswith("}")
    upstream_image = image[len(prefix) : -1]
    tag_and_digest = upstream_image.rsplit("/", 1)[-1].partition(":")[2]
    expected = f"ghcr.io/benchbox-dev/{service}:{tag_and_digest}"
    nightly = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "nightly-v2.yml").read_text(encoding="utf-8"))
    integration = _load_workflow()
    assert "@sha256:" in tag_and_digest
    assert nightly["jobs"]["docker"]["env"][image_variable] == expected
    assert integration["jobs"][service]["env"][image_variable] == expected


def test_postgres_job_pulls_the_ghcr_mirror_at_the_compose_digest() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker/postgresql/docker-compose.yml").read_text(encoding="utf-8"))
    default_image = compose["services"]["postgresql"]["image"][len("${POSTGRES_IMAGE:-") : -1]
    tag_and_digest = default_image.rsplit("/", 1)[-1].partition(":")[2]
    jobs = _load_workflow()["jobs"]
    postgres = jobs["postgres"]
    assert "@sha256:" in tag_and_digest
    assert postgres["env"]["POSTGRES_IMAGE"] == f"ghcr.io/benchbox-dev/postgres:{tag_and_digest}"
    assert "mirror-postgres" in postgres["needs"]
    assert postgres["permissions"]["packages"] == "read"
    assert jobs["mirror-postgres"]["with"]["images"] == '["postgres"]'
    steps = [step.get("name") for step in postgres["steps"]]
    assert steps.index("Authenticate to GHCR") < steps.index("Bring up PostgreSQL compose stack")


POSTGRES_IMAGE_REFERENCE = re.compile(
    r"^\s*image:\s*(?P<ref>\S*(?:postgres|pgduckdb|pg_mooncake|timescaledb)\S*)\s*$", re.MULTILINE | re.IGNORECASE
)


def _postgres_image_references() -> list[tuple[str, str]]:
    files = [
        *(REPO_ROOT / ".github" / "workflows").glob("*.yml"),
        *(REPO_ROOT / "docker").rglob("docker-compose*.y*ml"),
    ]
    return [
        (str(path.relative_to(REPO_ROOT)), match.group("ref"))
        for path in sorted(files)
        for match in POSTGRES_IMAGE_REFERENCE.finditer(path.read_text(encoding="utf-8"))
    ]


def test_every_postgres_image_reference_is_pinned_by_sha256() -> None:
    references = _postgres_image_references()
    assert {path for path, _ in references} >= {
        ".github/workflows/nightly.yml",
        "docker/postgresql/docker-compose.yml",
        "docker/postgres-extensions/docker-compose.yml",
        "docker/postgres-extensions/docker-compose.timescaledb.yaml",
    }
    unpinned = [(path, ref) for path, ref in references if not re.search(r"@sha256:[0-9a-f]{64}\b", ref)]
    assert not unpinned


def test_compose_workflows_do_not_pull_postgres_from_the_public_registry() -> None:
    for workflow in ("docker-integration.yml", "nightly-v2.yml"):
        text = (REPO_ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        assert "POSTGRES_IMAGE: ghcr.io/benchbox-dev/postgres:" in text, workflow


def test_compose_files_referenced_exist() -> None:
    for compose in (
        "docker/postgresql/docker-compose.yml",
        "docker/clickhouse/docker-compose.yml",
        "docker/trino/docker-compose.yml",
    ):
        assert (REPO_ROOT / compose).exists(), f"missing {compose}"
