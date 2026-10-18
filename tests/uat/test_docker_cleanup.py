from __future__ import annotations

import json

import pytest

from tests.uat import docker_assets, docker_cleanup

pytestmark = pytest.mark.medium


def _jsonl(*rows: dict[str, object]) -> str:
    return "\n".join(json.dumps(row) for row in rows) + ("\n" if rows else "")


def _fake_inventory_runner(calls: list[tuple[str, ...]], *, duplicate_uat_image: bool = False):
    def fake_runner(argv, **kwargs):
        argv_tuple = tuple(argv)
        calls.append(argv_tuple)
        if argv_tuple[:5] == ("docker", "container", "ls", "-a", "--no-trunc"):
            return docker_assets.DockerCommandResult(
                argv_tuple,
                0,
                _jsonl(
                    {
                        "ID": "uat-container-id",
                        "Names": "benchbox-uat-smoke-clickhouse-1",
                        "CreatedAt": "2026-05-10 12:00:00 -0400 EDT",
                        "Labels": "com.docker.compose.project=benchbox-uat-smoke-clickhouse",
                        "Status": "Exited (0) 2 hours ago",
                    },
                    {
                        "ID": "external-container-id",
                        "Names": "developer-postgres",
                        "CreatedAt": "2026-05-09 08:00:00 -0400 EDT",
                        "Labels": "com.docker.compose.project=developer-postgres",
                        "Status": "Up 1 day",
                    },
                ),
                "",
            )
        if argv_tuple == ("docker", "volume", "ls", "-q"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, "uat_volume\nexternal_volume\n", "")
        if argv_tuple[:3] == ("docker", "volume", "inspect"):
            return docker_assets.DockerCommandResult(
                argv_tuple,
                0,
                json.dumps(
                    [
                        {
                            "Name": "uat_volume",
                            "CreatedAt": "2026-05-10T12:01:00Z",
                            "Driver": "local",
                            "Labels": {"com.docker.compose.project": "benchbox-uat-smoke-clickhouse"},
                        },
                        {
                            "Name": "external_volume",
                            "CreatedAt": "2026-05-08T12:01:00Z",
                            "Driver": "local",
                            "Labels": {"com.docker.compose.project": "developer-postgres"},
                        },
                    ]
                ),
                "",
            )
        if argv_tuple == ("docker", "network", "ls", "-q", "--no-trunc"):
            return docker_assets.DockerCommandResult(
                argv_tuple,
                0,
                "uat-network-id\nexternal-network-id\nambiguous-prefix-network-id\nbridge-network-id\n",
                "",
            )
        if argv_tuple[:3] == ("docker", "network", "inspect"):
            return docker_assets.DockerCommandResult(
                argv_tuple,
                0,
                json.dumps(
                    [
                        {
                            "ID": "uat-network-id",
                            "Name": "benchbox-uat-smoke-clickhouse_default",
                            "Created": "2026-05-10T12:02:00Z",
                            "Labels": {"com.docker.compose.project": "benchbox-uat-smoke-clickhouse"},
                            "Driver": "bridge",
                        },
                        {
                            "ID": "external-network-id",
                            "Name": "developer_default",
                            "Created": "2026-05-08T13:02:00Z",
                            "Labels": {"com.docker.compose.project": "developer-postgres"},
                            "Driver": "bridge",
                        },
                        {
                            "ID": "ambiguous-prefix-network-id",
                            "Name": "benchbox-uatfoo_default",
                            "Created": "2026-05-08T14:02:00Z",
                            "Labels": {"com.docker.compose.project": "benchbox-uatfoo"},
                            "Driver": "bridge",
                        },
                        {
                            "ID": "bridge-network-id",
                            "Name": "bridge",
                            "Created": "",
                            "Labels": {},
                            "Driver": "bridge",
                        },
                    ]
                ),
                "",
            )
        if argv_tuple[:4] == ("docker", "image", "ls", "--no-trunc"):
            rows = [
                {
                    "ID": "sha256:uat-image-id",
                    "Repository": "benchbox-uat-local",
                    "Tag": "latest",
                    "CreatedAt": "2026-05-10 12:03:00 -0400 EDT",
                    "Size": "900MB",
                },
            ]
            if duplicate_uat_image:
                rows.append(
                    {
                        "ID": "sha256:uat-image-id",
                        "Repository": "benchbox-uat-local",
                        "Tag": "dev",
                        "CreatedAt": "2026-05-10 12:04:00 -0400 EDT",
                        "Size": "900MB",
                    }
                )
            rows.append(
                {
                    "ID": "sha256:external-image-id",
                    "Repository": "starrocks/allin1-ubuntu",
                    "Tag": "3.5.16",
                    "CreatedAt": "2026-04-30 09:00:00 -0400 EDT",
                    "Size": "5.14GB",
                }
            )
            return docker_assets.DockerCommandResult(
                argv_tuple,
                0,
                _jsonl(*rows),
                "",
            )
        if argv_tuple[:3] == ("docker", "image", "inspect"):
            return docker_assets.DockerCommandResult(
                argv_tuple,
                0,
                json.dumps(
                    [
                        {
                            "Id": "sha256:uat-image-id",
                            "Created": "2026-05-10T12:03:00Z",
                            "Config": {"Labels": {"com.docker.compose.project": "benchbox-uat-smoke-clickhouse"}},
                        },
                        {
                            "Id": "sha256:external-image-id",
                            "Created": "2026-04-30T09:00:00Z",
                            "Config": {"Labels": {}},
                        },
                    ]
                ),
                "",
            )
        if argv_tuple[0:2] == ("docker", "rm"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, "", "")
        if argv_tuple[0:3] == ("docker", "volume", "rm"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, "", "")
        if argv_tuple[0:3] == ("docker", "network", "rm"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, "", "")
        if argv_tuple[0:3] == ("docker", "image", "rm"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, "", "")
        raise AssertionError(f"unexpected docker command: {argv_tuple}")

    return fake_runner


def test_recovery_report_identifies_uat_owned_and_alerts_non_uat_resources():
    calls: list[tuple[str, ...]] = []
    report = docker_cleanup.recover_abandoned_uat_docker_usage(
        apply=False,
        runner=_fake_inventory_runner(calls),
    )

    assert {r.kind for r in report.uat_owned} == {"container", "volume", "network", "image"}
    assert {r.display_name for r in report.uat_owned} == {
        "benchbox-uat-smoke-clickhouse-1",
        "uat_volume",
        "benchbox-uat-smoke-clickhouse_default",
        "benchbox-uat-local:latest",
    }
    assert {r.display_name for r in report.non_uat} == {
        "developer-postgres",
        "external_volume",
        "developer_default",
        "benchbox-uatfoo_default",
        "starrocks/allin1-ubuntu:3.5.16",
    }

    rendered = docker_cleanup.format_cleanup_report(report)
    assert "mode: dry-run" in rendered
    assert "created=2026-05-10 12:00:00 -0400 EDT" in rendered
    assert "created=2026-05-10T12:02:00Z" in rendered
    assert "created=2026-04-30T09:00:00Z" in rendered
    assert "Non-UAT Docker resources were NOT cleaned automatically" in rendered
    assert "cleanup: docker rm -f external-container-id" in rendered
    assert "cleanup: docker volume rm external_volume" in rendered
    assert "cleanup: docker network rm external-network-id" in rendered
    assert "cleanup: docker network rm ambiguous-prefix-network-id" in rendered
    assert "cleanup: docker image rm sha256:external-image-id" in rendered


def test_apply_removes_only_uat_owned_resources():
    calls: list[tuple[str, ...]] = []
    report = docker_cleanup.recover_abandoned_uat_docker_usage(
        apply=True,
        runner=_fake_inventory_runner(calls),
    )

    cleanup_calls = calls[7:]
    assert cleanup_calls == [
        ("docker", "rm", "-f", "uat-container-id"),
        ("docker", "volume", "rm", "uat_volume"),
        ("docker", "network", "rm", "uat-network-id"),
        ("docker", "image", "rm", "sha256:uat-image-id"),
    ]
    assert all(command.status == "ok" for command in report.cleanup_commands)
    assert "external-container-id" not in " ".join(" ".join(call) for call in cleanup_calls)


def test_apply_deduplicates_multitagged_uat_image_cleanup_id():
    calls: list[tuple[str, ...]] = []
    report = docker_cleanup.recover_abandoned_uat_docker_usage(
        apply=True,
        runner=_fake_inventory_runner(calls, duplicate_uat_image=True),
    )

    assert {r.display_name for r in report.uat_owned if r.kind == "image"} == {
        "benchbox-uat-local:dev",
        "benchbox-uat-local:latest",
    }
    image_cleanup_commands = [
        command.argv for command in report.cleanup_commands if command.argv[:3] == ("docker", "image", "rm")
    ]
    assert image_cleanup_commands == [("docker", "image", "rm", "sha256:uat-image-id")]
    assert [call for call in calls if call[:3] == ("docker", "image", "rm")] == image_cleanup_commands


def _mocker_volume_runner(calls: list[tuple[str, ...]]):
    def fake_runner(argv, **kwargs):
        argv_tuple = tuple(argv)
        calls.append(argv_tuple)
        if argv_tuple == ("mocker", "volume", "ls"):
            return docker_assets.DockerCommandResult(
                argv_tuple,
                0,
                "DRIVER   VOLUME NAME\nlocal    benchbox-uat-smoke-postgresql_pgdata\nlocal    developer-scratch\n",
                "",
            )
        if argv_tuple[:2] == ("container", "image") or argv_tuple[:2] == ("container", "ls"):
            raise AssertionError(f"docker-shaped inventory must not run against mocker: {argv_tuple}")
        return docker_assets.DockerCommandResult(argv_tuple, 0, "", "")

    return fake_runner


def test_recover_falls_back_to_mocker_volume_listing_without_crashing(monkeypatch):
    monkeypatch.setenv(docker_assets.CONTAINER_CLI_ENV_VAR, "mocker")
    monkeypatch.setattr(docker_assets, "_which_container_cli", lambda cli: f"/opt/homebrew/bin/{cli}")
    docker_assets.resolve_container_cli.cache_clear()
    calls: list[tuple[str, ...]] = []
    try:
        report = docker_cleanup.recover_abandoned_uat_docker_usage(apply=False, runner=_mocker_volume_runner(calls))
    finally:
        docker_assets.resolve_container_cli.cache_clear()

    assert report.engine == "mocker"
    assert {r.display_name for r in report.uat_owned} == {"benchbox-uat-smoke-postgresql_pgdata"}
    assert report.inventory_note is not None
    assert "ENGINE=container" in report.inventory_note
    rendered = docker_cleanup.format_cleanup_report(report)
    assert "engine: mocker" in rendered
    assert "NOTE:" in rendered
    assert "mocker volume rm benchbox-uat-smoke-postgresql_pgdata" in rendered


def test_recover_apply_removes_mocker_volumes_via_the_resolved_binary(monkeypatch):
    monkeypatch.setenv(docker_assets.CONTAINER_CLI_ENV_VAR, "mocker")
    monkeypatch.setattr(docker_assets, "_which_container_cli", lambda cli: f"/opt/homebrew/bin/{cli}")
    docker_assets.resolve_container_cli.cache_clear()
    calls: list[tuple[str, ...]] = []
    try:
        report = docker_cleanup.recover_abandoned_uat_docker_usage(apply=True, runner=_mocker_volume_runner(calls))
    finally:
        docker_assets.resolve_container_cli.cache_clear()

    assert ("mocker", "volume", "rm", "benchbox-uat-smoke-postgresql_pgdata") in calls
    assert all(command.status == "ok" for command in report.cleanup_commands)
    assert "developer-scratch" not in " ".join(" ".join(c) for c in calls if c[:2] == ("mocker", "volume"))
