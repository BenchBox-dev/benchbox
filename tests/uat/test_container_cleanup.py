from __future__ import annotations

import json

import pytest

from tests.uat import container_cleanup, docker_assets

pytestmark = pytest.mark.medium


_IMAGES = [
    {
        "id": "img-owned",
        "configuration": {"name": "benchbox/tpc-h-linux-arm64:latest", "creationDate": "2026-07-08T17:47:31Z"},
    },
    {
        "id": "img-agent",
        "configuration": {"name": "local/benchbox-agent:latest", "creationDate": "2026-07-01T00:00:00Z"},
    },
    {"id": "img-shared", "configuration": {"name": "postgres:18", "creationDate": "2026-06-01T00:00:00Z"}},
    {"id": "img-shared2", "configuration": {"name": "ubuntu:24.04", "creationDate": "2026-06-02T00:00:00Z"}},
    {
        "id": "img-builder",
        "configuration": {
            "name": "ghcr.io/apple/container-builder-shim/builder:0.12.0",
            "creationDate": "2026-06-30T00:00:00Z",
        },
    },
]

_CONTAINERS = [
    {
        "id": "buildkit",
        "configuration": {
            "id": "buildkit",
            "image": {"reference": "ghcr.io/apple/container-builder-shim/builder:0.12.0"},
            "labels": {"com.apple.container.resource.role": "builder"},
        },
        "status": {"state": "running"},
    },
    {
        "id": "uat-leftover",
        "configuration": {
            "id": "uat-leftover",
            "image": {"reference": "postgres:18"},
            "labels": {"com.docker.compose.project": "benchbox-uat-smoke-postgresql"},
        },
        "status": {"state": "stopped"},
    },
    {
        "id": "external-ctr",
        "configuration": {"id": "external-ctr", "image": {"reference": "redis:7"}, "labels": {}},
        "status": {"state": "running"},
    },
]

_VOLUMES = [
    {"name": "benchbox-uat-smoke-postgresql_pgdata", "driver": "local"},
    {"name": "developer_scratch", "driver": "local"},
]

_NETWORKS = [
    {
        "id": "default",
        "configuration": {"name": "default", "labels": {"com.apple.container.resource.role": "builtin"}},
    },
    {
        "id": "uat-net",
        "configuration": {
            "name": "benchbox-uat-smoke-postgresql_default",
            "labels": {"com.docker.compose.project": "benchbox-uat-smoke-postgresql"},
        },
    },
]

_SYSTEM_DF = (
    "TYPE           TOTAL  ACTIVE  SIZE      RECLAIMABLE\n"
    "Images         5      1       11.65 GB  11.22 GB (96%)\n"
    "Containers     3      2       14.01 GB  0 B (0%)\n"
    "Local Volumes  2      0       120 MB    120 MB (100%)\n"
)


def _runner(calls: list[tuple[str, ...]], *, df_fails: bool = False):
    def fake(argv, **kwargs):
        argv_tuple = tuple(argv)
        calls.append(argv_tuple)
        if argv_tuple[:4] == ("container", "image", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(_IMAGES), "")
        if argv_tuple[:3] == ("container", "ls", "-a"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(_CONTAINERS), "")
        if argv_tuple[:4] == ("container", "volume", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(_VOLUMES), "")
        if argv_tuple[:4] == ("container", "network", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(_NETWORKS), "")
        if argv_tuple == ("container", "system", "df"):
            if df_fails:
                return docker_assets.DockerCommandResult(argv_tuple, 1, "", "boom", error="unavailable")
            return docker_assets.DockerCommandResult(argv_tuple, 0, _SYSTEM_DF, "")
        return docker_assets.DockerCommandResult(argv_tuple, 0, "", "")

    return fake


_LIST_CALL_PREFIXES = {
    ("container", "image", "ls"),
    ("container", "volume", "ls"),
    ("container", "network", "ls"),
    ("container", "ls", "-a"),
}


def _targets_by_kind(report):
    out: dict[str, set[str]] = {}
    for r in report.targets:
        out.setdefault(r.kind, set()).add(r.display_name)
    return out


def test_owned_mode_targets_only_benchbox_owned():
    report = container_cleanup.reclaim_container_usage(mode="owned", apply=False, runner=_runner([]))
    assert _targets_by_kind(report) == {
        "image": {"benchbox/tpc-h-linux-arm64:latest", "local/benchbox-agent:latest"},
        "container": {"uat-leftover"},
        "volume": {"benchbox-uat-smoke-postgresql_pgdata"},
        "network": {"benchbox-uat-smoke-postgresql_default"},
    }
    retained = {r.display_name for r in report.retained}
    assert {"postgres:18", "ubuntu:24.04", "ghcr.io/apple/container-builder-shim/builder:0.12.0"} <= retained
    assert "buildkit" in retained
    assert "default" in retained


def test_images_mode_adds_shared_images_but_not_builder():
    report = container_cleanup.reclaim_container_usage(mode="images", apply=False, runner=_runner([]))
    assert _targets_by_kind(report)["image"] == {
        "benchbox/tpc-h-linux-arm64:latest",
        "local/benchbox-agent:latest",
        "postgres:18",
        "ubuntu:24.04",
    }
    retained = {r.display_name for r in report.retained}
    assert "ghcr.io/apple/container-builder-shim/builder:0.12.0" in retained
    assert "buildkit" in retained
    assert "external-ctr" not in {r.display_name for r in report.targets}


def test_apply_runs_grouped_removals_in_dependency_order():
    calls: list[tuple[str, ...]] = []
    report = container_cleanup.reclaim_container_usage(mode="owned", apply=True, runner=_runner(calls))
    mutations = [c for c in calls if c[:3] not in _LIST_CALL_PREFIXES and c != ("container", "system", "df")]
    assert mutations == [
        ("container", "rm", "-f", "uat-leftover"),
        ("container", "volume", "rm", "benchbox-uat-smoke-postgresql_pgdata"),
        ("container", "network", "rm", "benchbox-uat-smoke-postgresql_default"),
        ("container", "image", "rm", "benchbox/tpc-h-linux-arm64:latest", "local/benchbox-agent:latest"),
    ]
    assert all(c.status == "ok" for c in report.commands)
    assert report.footprint_after is not None


def test_max_mode_appends_prune_and_builder_reclaim():
    calls: list[tuple[str, ...]] = []
    container_cleanup.reclaim_container_usage(mode="max", apply=True, runner=_runner(calls))
    assert ("container", "prune") in calls
    assert ("container", "volume", "prune") in calls
    assert ("container", "network", "prune") in calls
    assert ("container", "builder", "delete", "--force") in calls


def test_max_mode_reclaims_external_container():
    report = container_cleanup.reclaim_container_usage(mode="max", apply=False, runner=_runner([]))
    assert "external-ctr" in {r.display_name for r in report.targets if r.kind == "container"}


def test_footprint_parses_system_df_rows():
    report = container_cleanup.reclaim_container_usage(mode="owned", apply=False, runner=_runner([]))
    rows = {kind: (size, recl) for kind, size, recl in report.footprint_before.rows}
    assert rows["Images"] == ("11.65 GB", "11.22 GB")
    assert rows["Local Volumes"] == ("120 MB", "120 MB")


def test_footprint_unavailable_is_non_fatal():
    report = container_cleanup.reclaim_container_usage(mode="owned", apply=False, runner=_runner([], df_fails=True))
    assert report.footprint_before.rows == ()
    assert "disk usage unavailable" in container_cleanup.format_container_cleanup_report(report)


def test_unknown_mode_raises():
    with pytest.raises(container_cleanup.ContainerCleanupError):
        container_cleanup.reclaim_container_usage(mode="everything", apply=False, runner=_runner([]))


def test_report_renders_mode_and_store_path():
    report = container_cleanup.reclaim_container_usage(mode="owned", apply=False, runner=_runner([]))
    rendered = container_cleanup.format_container_cleanup_report(report)
    assert "mode: owned (dry-run)" in rendered
    assert "com.apple.container" in rendered
    assert "Retained (widen --mode to reclaim):" in rendered


def test_custom_project_prefix_reaches_classification():
    custom_images = [
        {"id": "img-custom", "configuration": {"name": "foo-tpc-h:latest", "creationDate": "2026-07-08T00:00:00Z"}},
    ]
    custom_containers = [
        {
            "id": "custom-leftover",
            "configuration": {
                "id": "custom-leftover",
                "image": {"reference": "postgres:18"},
                "labels": {"com.docker.compose.project": "foo-smoke-postgresql"},
            },
            "status": {"state": "stopped"},
        },
    ]

    def fake(argv, **kwargs):
        argv_tuple = tuple(argv)
        if argv_tuple[:4] == ("container", "image", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(custom_images), "")
        if argv_tuple[:3] == ("container", "ls", "-a"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(custom_containers), "")
        if argv_tuple[:4] == ("container", "volume", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, "[]", "")
        if argv_tuple == ("container", "system", "df"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, _SYSTEM_DF, "")
        return docker_assets.DockerCommandResult(argv_tuple, 0, "", "")

    report = container_cleanup.reclaim_container_usage(mode="owned", apply=False, project_prefix="foo", runner=fake)
    assert _targets_by_kind(report) == {
        "image": {"foo-tpc-h:latest"},
        "container": {"custom-leftover"},
    }


def test_image_reference_read_from_display_reference_field():
    images = [
        {
            "id": "sha256:abc123",
            "displayReference": "benchbox/tpc-h-linux-arm64:latest",
            "configuration": {"descriptor": {"digest": "sha256:abc123"}},
        },
        {
            "id": "sha256:def456",
            "displayReference": "postgres:18",
            "configuration": {"descriptor": {"digest": "sha256:def456"}},
        },
    ]

    def fake(argv, **kwargs):
        argv_tuple = tuple(argv)
        if argv_tuple[:4] == ("container", "image", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(images), "")
        if argv_tuple[:3] == ("container", "ls", "-a"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, "[]", "")
        if argv_tuple[:4] == ("container", "volume", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, "[]", "")
        if argv_tuple == ("container", "system", "df"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, _SYSTEM_DF, "")
        return docker_assets.DockerCommandResult(argv_tuple, 0, "", "")

    report = container_cleanup.reclaim_container_usage(mode="owned", apply=False, runner=fake)
    assert _targets_by_kind(report) == {"image": {"benchbox/tpc-h-linux-arm64:latest"}}


def test_custom_prefix_changes_classification():
    default_report = container_cleanup.reclaim_container_usage(mode="owned", apply=False, runner=_runner([]))
    default_targets = _targets_by_kind(default_report)
    assert "uat-leftover" in default_targets["container"]
    assert "benchbox-uat-smoke-postgresql_pgdata" in default_targets["volume"]

    custom_report = container_cleanup.reclaim_container_usage(
        mode="owned", apply=False, project_prefix="benchbox-custom", runner=_runner([])
    )
    custom_targets = _targets_by_kind(custom_report)
    assert "uat-leftover" not in custom_targets.get("container", set())
    assert "benchbox-uat-smoke-postgresql_pgdata" not in custom_targets.get("volume", set())
    assert "benchbox-uat-smoke-postgresql_default" not in custom_targets.get("network", set())
    assert custom_report.project_prefix == "benchbox-custom"


def test_benchbox_experiment_dev_container_survives_owned_mode():
    images = [*_IMAGES, {"id": "img-dev", "configuration": {"name": "benchbox-experiment:latest"}}]
    containers = [
        *_CONTAINERS,
        {
            "id": "dev-ctr",
            "configuration": {"id": "dev-ctr", "image": {"reference": "benchbox-experiment:latest"}, "labels": {}},
            "status": {"state": "running"},
        },
    ]

    def fake(argv, **kwargs):
        argv_tuple = tuple(argv)
        if argv_tuple[:4] == ("container", "image", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(images), "")
        if argv_tuple[:3] == ("container", "ls", "-a"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(containers), "")
        if argv_tuple[:4] == ("container", "volume", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(_VOLUMES), "")
        if argv_tuple[:4] == ("container", "network", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(_NETWORKS), "")
        if argv_tuple == ("container", "system", "df"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, _SYSTEM_DF, "")
        return docker_assets.DockerCommandResult(argv_tuple, 0, "", "")

    owned = container_cleanup.reclaim_container_usage(mode="owned", apply=False, runner=fake)
    owned_names = {r.display_name for r in owned.targets}
    assert "benchbox-experiment:latest" not in owned_names
    assert "dev-ctr" not in owned_names
    retained_names = {r.display_name for r in owned.retained}
    assert "benchbox-experiment:latest" in retained_names
    assert "dev-ctr" in retained_names

    images_mode = container_cleanup.reclaim_container_usage(mode="images", apply=False, runner=fake)
    assert "dev-ctr" not in {r.display_name for r in images_mode.targets}

    max_mode = container_cleanup.reclaim_container_usage(mode="max", apply=False, runner=fake)
    max_names = {r.display_name for r in max_mode.targets}
    assert "benchbox-experiment:latest" in max_names
    assert "dev-ctr" in max_names


def test_network_inventory_owned_vs_shared_vs_system():
    networks = [*_NETWORKS, {"id": "other-net", "configuration": {"name": "developer-bridge", "labels": {}}}]

    def fake(argv, **kwargs):
        argv_tuple = tuple(argv)
        if argv_tuple[:4] == ("container", "image", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(_IMAGES), "")
        if argv_tuple[:3] == ("container", "ls", "-a"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(_CONTAINERS), "")
        if argv_tuple[:4] == ("container", "volume", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(_VOLUMES), "")
        if argv_tuple[:4] == ("container", "network", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(networks), "")
        if argv_tuple == ("container", "system", "df"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, _SYSTEM_DF, "")
        return docker_assets.DockerCommandResult(argv_tuple, 0, "", "")

    owned = container_cleanup.reclaim_container_usage(mode="owned", apply=False, runner=fake)
    owned_networks = {r.display_name for r in owned.targets if r.kind == "network"}
    assert owned_networks == {"benchbox-uat-smoke-postgresql_default"}
    retained_networks = {r.display_name for r in owned.retained if r.kind == "network"}
    assert retained_networks == {"default", "developer-bridge"}

    max_mode = container_cleanup.reclaim_container_usage(mode="max", apply=False, runner=fake)
    max_networks = {r.display_name for r in max_mode.targets if r.kind == "network"}
    assert "developer-bridge" in max_networks
    assert "default" not in max_networks


def test_network_name_extending_prefix_without_separator_is_not_owned():
    networks = [
        *_NETWORKS,
        {"id": "lookalike-net", "configuration": {"name": "benchbox-uatfoo_default", "labels": {}}},
    ]

    def fake(argv, **kwargs):
        argv_tuple = tuple(argv)
        if argv_tuple[:4] == ("container", "image", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(_IMAGES), "")
        if argv_tuple[:3] == ("container", "ls", "-a"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(_CONTAINERS), "")
        if argv_tuple[:4] == ("container", "volume", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(_VOLUMES), "")
        if argv_tuple[:4] == ("container", "network", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(networks), "")
        if argv_tuple == ("container", "system", "df"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, _SYSTEM_DF, "")
        return docker_assets.DockerCommandResult(argv_tuple, 0, "", "")

    owned = container_cleanup.reclaim_container_usage(mode="owned", apply=False, runner=fake)
    owned_networks = {r.display_name for r in owned.targets if r.kind == "network"}
    assert "benchbox-uatfoo_default" not in owned_networks
    retained_networks = {r.display_name for r in owned.retained if r.kind == "network"}
    assert "benchbox-uatfoo_default" in retained_networks


def test_mocker_compose_project_label_is_recognized(monkeypatch):
    containers = [
        {
            "id": "pg-1",
            "configuration": {
                "id": "pg-1",
                "image": {"reference": "postgres:18"},
                "labels": {
                    "com.mocker.compose.project": "benchbox-uat-manual-postgresql",
                    "com.mocker.compose.service": "postgresql",
                },
            },
            "status": {"state": "running"},
        },
    ]
    networks = [
        {
            "id": "mocker-net",
            "configuration": {
                "name": "benchbox-uat-manual-postgresql_default",
                "labels": {"com.mocker.compose.project": "benchbox-uat-manual-postgresql"},
            },
        },
    ]

    def fake(argv, **kwargs):
        argv_tuple = tuple(argv)
        if argv_tuple[:4] == ("container", "image", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(_IMAGES), "")
        if argv_tuple[:3] == ("container", "ls", "-a"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(containers), "")
        if argv_tuple[:4] == ("container", "volume", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(_VOLUMES), "")
        if argv_tuple[:4] == ("container", "network", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(networks), "")
        if argv_tuple == ("container", "system", "df"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, _SYSTEM_DF, "")
        return docker_assets.DockerCommandResult(argv_tuple, 0, "", "")

    report = container_cleanup.reclaim_container_usage(mode="owned", apply=False, runner=fake)
    owned_names = {r.display_name for r in report.targets}
    assert "pg-1" in owned_names
    assert "benchbox-uat-manual-postgresql_default" in owned_names


def test_max_mode_apply_passes_the_real_forbidden_prune_guard(monkeypatch):
    import subprocess

    executed: list[tuple[str, ...]] = []

    def fake_subprocess_run(argv, **kwargs):
        argv_tuple = tuple(argv)
        executed.append(argv_tuple)
        if argv_tuple[:4] == ("container", "image", "ls", "--format"):
            out = json.dumps(_IMAGES)
        elif argv_tuple[:3] == ("container", "ls", "-a"):
            out = json.dumps(_CONTAINERS)
        elif argv_tuple[:4] == ("container", "volume", "ls", "--format"):
            out = json.dumps(_VOLUMES)
        elif argv_tuple[:4] == ("container", "network", "ls", "--format"):
            out = json.dumps(_NETWORKS)
        elif argv_tuple == ("container", "system", "df"):
            out = _SYSTEM_DF
        else:
            out = ""
        return subprocess.CompletedProcess(argv, 0, out, "")

    monkeypatch.setattr(docker_assets.subprocess, "run", fake_subprocess_run)

    report = container_cleanup.reclaim_container_usage(mode="max", apply=True, runner=None)

    assert ("container", "prune") in executed
    assert ("container", "volume", "prune") in executed
    assert ("container", "network", "prune") in executed
    assert ("container", "builder", "delete", "--force") in executed
    assert all(cmd.status == "ok" for cmd in report.commands)


def test_non_uat_mocker_project_stays_shared_below_max():
    containers = [
        {
            "id": "myapp-web-1",
            "configuration": {
                "id": "myapp-web-1",
                "image": {"reference": "nginx:1.27"},
                "labels": {"com.mocker.compose.project": "myapp", "com.mocker.compose.service": "web"},
            },
            "status": {"state": "running"},
        },
    ]

    def fake(argv, **kwargs):
        argv_tuple = tuple(argv)
        if argv_tuple[:4] == ("container", "image", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(_IMAGES), "")
        if argv_tuple[:3] == ("container", "ls", "-a"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(containers), "")
        if argv_tuple[:4] == ("container", "volume", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(_VOLUMES), "")
        if argv_tuple[:4] == ("container", "network", "ls", "--format"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, json.dumps(_NETWORKS), "")
        if argv_tuple == ("container", "system", "df"):
            return docker_assets.DockerCommandResult(argv_tuple, 0, _SYSTEM_DF, "")
        return docker_assets.DockerCommandResult(argv_tuple, 0, "", "")

    for mode in ("owned", "images"):
        report = container_cleanup.reclaim_container_usage(mode=mode, apply=False, runner=fake)
        assert "myapp-web-1" not in {r.display_name for r in report.targets}, mode
        retained = {r.display_name: r.category for r in report.retained}
        assert retained.get("myapp-web-1") == "shared", mode

    max_report = container_cleanup.reclaim_container_usage(mode="max", apply=False, runner=fake)
    assert "myapp-web-1" in {r.display_name for r in max_report.targets}
