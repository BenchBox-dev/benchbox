from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from benchbox.utils import host_lock
from tests.uat import docker_assets, managed_runtime, matrix, mocker_launch

pytestmark = pytest.mark.medium

PROJECT = "benchbox-uat-doris"
HEALTHY_PROBE = (
    "cgroup_memory=4294967296\n"
    "mem_total_kb=4194304\n"
    "ulimit_n_soft=1048576\n"
    "ulimit_n_hard=1048576\n"
    "arch=x86_64\n"
    "cpus=4\n"
    "entrypoint_marker=present\n"
)


@pytest.fixture
def mocker_cli(monkeypatch):
    monkeypatch.setenv(docker_assets.CONTAINER_CLI_ENV_VAR, "mocker")
    docker_assets.resolve_container_cli.cache_clear()
    yield
    docker_assets.resolve_container_cli.cache_clear()


def _flag_values(argv: list[str], flag: str) -> list[str]:
    return [argv[index + 1] for index, token in enumerate(argv) if token == flag]


def _runner(script=None):
    calls: list[list[str]] = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        if script:
            result = script(list(argv))
            if result is not None:
                return result
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    run.calls = calls
    return run


def test_applies_only_to_mocker_launch_platforms(mocker_cli, monkeypatch):
    assert mocker_launch.applies(docker_assets.docker_platform_spec("doris"))
    assert mocker_launch.applies(docker_assets.docker_platform_spec("singlestore"))
    assert not mocker_launch.applies(docker_assets.docker_platform_spec("postgresql"))
    monkeypatch.setenv(docker_assets.CONTAINER_CLI_ENV_VAR, "docker")
    docker_assets.resolve_container_cli.cache_clear()
    assert not mocker_launch.applies(docker_assets.docker_platform_spec("doris"))


def test_doris_translation_carries_memory_entrypoint_ports_and_labels(mocker_cli):
    spec = docker_assets.docker_platform_spec("doris")
    (service,) = mocker_launch.build_services(spec, PROJECT, env={})
    argv = mocker_launch.run_command(service, PROJECT)
    assert argv[:5] == ["mocker", "run", "-d", "--name", f"{PROJECT}-doris-1"]
    assert _flag_values(argv, "-m") == ["4g"]
    assert _flag_values(argv, "--entrypoint") == ["bash"]
    assert _flag_values(argv, "-p") == ["19031:9030", "18030:8030", "18040:8040"]
    assert "SKIP_CHECK_ULIMIT=true" in _flag_values(argv, "-e")
    assert f"com.docker.compose.project={PROJECT}" in _flag_values(argv, "--label")
    assert "com.docker.compose.service=doris" in _flag_values(argv, "--label")
    assert f"{PROJECT}-doris-fe-meta:/opt/apache-doris/fe/doris-meta" in _flag_values(argv, "-v")
    assert "--privileged" not in argv
    image_index = argv.index(
        "apache/doris:4.0.3-all-slim@sha256:3237bfb73471da7482c6575cecc1632c96dec09509cd2e60bcb798e2c5342823"
    )
    assert argv[image_index + 1] == "-c"
    script = argv[image_index + 2]
    assert managed_runtime.ENTRYPOINT_MARKER_PATH in script
    assert '"$1"' in script and "$$" not in script


def test_singlestore_translation_carries_platform_memory_cpus_port_and_env(mocker_cli):
    spec = docker_assets.docker_platform_spec("singlestore")
    (service,) = mocker_launch.build_services(spec, "benchbox-uat-singlestore", env={})
    argv = mocker_launch.run_command(service, "benchbox-uat-singlestore")
    assert _flag_values(argv, "--platform") == ["linux/amd64"]
    assert _flag_values(argv, "-m") == ["4g"]
    assert _flag_values(argv, "--cpus") == ["4"]
    assert _flag_values(argv, "-p") == ["13306:3306"]
    assert _flag_values(argv, "-e") == ["ROOT_PASSWORD=benchbox"]
    assert argv[-1] == (
        "ghcr.io/singlestore-labs/singlestoredb-dev:0.2.90"
        "@sha256:2f181587a74b63a83e7dbd46b02e9dce031b8cacf1f5651bff297b2d8c87df37"
    )
    assert "--entrypoint" not in argv


def test_env_overrides_drive_the_translation(mocker_cli):
    spec = docker_assets.docker_platform_spec("singlestore")
    env = {"SINGLESTORE_MEMORY_LIMIT": "6g", "SINGLESTORE_CPUS": "2", "SINGLESTORE_HOST_PORT": "23306"}
    (service,) = mocker_launch.build_services(spec, "p", env=env)
    argv = mocker_launch.run_command(service, "p")
    assert _flag_values(argv, "-m") == ["6g"]
    assert _flag_values(argv, "--cpus") == ["2"]
    assert _flag_values(argv, "-p") == ["23306:3306"]


def test_untranslatable_compose_setting_fails_loudly(mocker_cli, tmp_path: Path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services:\n  db:\n    image: x\n    ulimits:\n      nofile: 65536\n", encoding="utf-8")
    spec = docker_assets.DockerPlatformSpec(platform="doris", compose_files=(compose,), services=("db",))
    with pytest.raises(docker_assets.DockerAssetError, match="ulimits"):
        mocker_launch.build_services(spec, "p", env={})


def test_launch_runs_volumes_container_then_polls_the_healthcheck(mocker_cli):
    spec = docker_assets.docker_platform_spec("doris")
    health_attempts = []

    def script(argv):
        if argv[:2] == ["mocker", "exec"]:
            health_attempts.append(argv)
            code = 1 if len(health_attempts) < 3 else 0
            return docker_assets.DockerCommandResult(tuple(argv), code, "", "not ready")
        return None

    run = _runner(script)
    result = mocker_launch.launch_stack(spec, PROJECT, env={}, runner=run, timeout_s=300, sleep=lambda s: None)
    assert result.succeeded
    verbs = [call[1] for call in run.calls]
    assert verbs[0] == "rm" and run.calls[0][-1] == f"{PROJECT}-doris-1"
    assert verbs.count("volume") == 2
    assert verbs.count("run") == 1
    assert len(health_attempts) == 3
    assert health_attempts[0][2] == f"{PROJECT}-doris-1"


def test_launch_reports_an_unhealthy_stack_after_the_timeout(mocker_cli):
    spec = docker_assets.docker_platform_spec("singlestore")
    clock = iter(range(0, 1000, 100))

    def script(argv):
        if argv[:2] == ["mocker", "exec"]:
            return docker_assets.DockerCommandResult(tuple(argv), 1, "", "connection refused")
        return None

    result = mocker_launch.launch_stack(
        spec, "p", env={}, runner=_runner(script), timeout_s=300, sleep=lambda s: None, monotonic=lambda: next(clock)
    )
    assert not result.succeeded
    assert "did not become healthy" in result.stderr and "connection refused" in result.stderr


def test_launch_surfaces_a_failed_mocker_run(mocker_cli):
    spec = docker_assets.docker_platform_spec("singlestore")

    def script(argv):
        if argv[:2] == ["mocker", "run"]:
            return docker_assets.DockerCommandResult(tuple(argv), 1, "", "VM aborted")
        return None

    result = mocker_launch.launch_stack(spec, "p", env={}, runner=_runner(script), sleep=lambda s: None)
    assert not result.succeeded
    assert "mocker run for service singlestore failed" in result.stderr and "VM aborted" in result.stderr


def test_probe_and_diagnostic_commands_target_the_launched_container(mocker_cli):
    spec = docker_assets.docker_platform_spec("doris")
    probe = managed_runtime.probe_command(spec, PROJECT, "doris")
    assert probe[:3] == ["mocker", "exec", f"{PROJECT}-doris-1"]
    assert mocker_launch.container_ids_command(PROJECT)[-1] == f"label=com.docker.compose.project={PROJECT}"
    assert mocker_launch.logs_command(spec, PROJECT)[-1] == f"{PROJECT}-doris-1"


def test_probe_falls_back_to_compose_exec_under_docker(monkeypatch):
    monkeypatch.setenv(docker_assets.CONTAINER_CLI_ENV_VAR, "docker")
    docker_assets.resolve_container_cli.cache_clear()
    spec = docker_assets.docker_platform_spec("doris")
    probe = managed_runtime.probe_command(spec, PROJECT, "doris")
    assert probe[1] == "compose" and "exec" in probe


def test_declared_settings_cover_the_mocker_launched_stacks(mocker_cli):
    doris = managed_runtime.compose_declared_settings(docker_assets.docker_platform_spec("doris"), env={})["doris"]
    assert doris.entrypoint and doris.memory_bytes == 4 * 1024**3
    singlestore = managed_runtime.compose_declared_settings(docker_assets.docker_platform_spec("singlestore"), env={})[
        "singlestore"
    ]
    assert singlestore.platform == "linux/amd64" and singlestore.cpus == 4
    assert singlestore.memory_bytes == 4 * 1024**3


def test_cpu_shortfall_is_reported():
    declared = managed_runtime.DeclaredServiceSettings(service="s", cpus=4)
    assert managed_runtime.declared_settings_violations(declared, {"cpus": "4"}) == []
    assert (
        "cpus: declared 4, container sees 1" in managed_runtime.declared_settings_violations(declared, {"cpus": "1"})[0]
    )
    assert "did not report" in managed_runtime.declared_settings_violations(declared, {})[0]


def test_teardown_removes_the_container_and_sweeps_volumes_on_request(mocker_cli):
    spec = docker_assets.docker_platform_spec("doris")
    volume_ls = f"DRIVER VOLUME NAME\nlocal {PROJECT}-doris-fe-meta\nlocal {PROJECT}-doris-be-storage\n"

    def script(argv):
        if argv[:3] == ["mocker", "volume", "ls"]:
            return docker_assets.DockerCommandResult(tuple(argv), 0, volume_ls, "")
        return None

    keep = _runner(script)
    assert mocker_launch.teardown_stack(spec, PROJECT, "containers", runner=keep).succeeded
    assert keep.calls == [["mocker", "rm", "-f", f"{PROJECT}-doris-1"]]

    wipe = _runner(script)
    assert mocker_launch.teardown_stack(spec, PROJECT, "volumes", runner=wipe).succeeded
    removed = [call[-1] for call in wipe.calls if call[:3] == ["mocker", "volume", "rm"]]
    assert sorted(removed) == [f"{PROJECT}-doris-be-storage", f"{PROJECT}-doris-fe-meta"]


def test_teardown_tolerates_an_absent_container_but_not_other_failures(mocker_cli):
    spec = docker_assets.docker_platform_spec("doris")

    def absent(argv):
        return docker_assets.DockerCommandResult(tuple(argv), 1, "", "container not found")

    def broken(argv):
        return docker_assets.DockerCommandResult(tuple(argv), 1, "", "daemon unreachable")

    assert mocker_launch.teardown_stack(spec, PROJECT, "containers", runner=_runner(absent)).succeeded
    assert not mocker_launch.teardown_stack(spec, PROJECT, "containers", runner=_runner(broken)).succeeded
    with pytest.raises(docker_assets.DockerAssetError):
        mocker_launch.teardown_stack(spec, PROJECT, "off", runner=_runner())


def _load_bring_up_module():
    path = Path("scripts/uat-bring-up/uat_bring_up.py").resolve()
    spec = importlib.util.spec_from_file_location("uat_bring_up_mocker_launch", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bring_up(monkeypatch, probe_stdout: str):
    module = _load_bring_up_module()
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        stdout = probe_stdout if argv[:2] == ["mocker", "exec"] and "sh" in argv and len(argv) > 4 else ""
        return docker_assets.DockerCommandResult(tuple(argv), 0, stdout, "")

    monkeypatch.setenv(host_lock.OWNER_ENV, "1:1.0")
    monkeypatch.setattr(module.docker_assets, "run_docker_command", fake_run)
    monkeypatch.setattr(matrix, "platform_is_reachable", lambda platform: True)
    monkeypatch.setattr(module, "_admit_bring_up", lambda *args, **kwargs: 0)
    real_launch_stack = mocker_launch.launch_stack
    monkeypatch.setattr(
        mocker_launch, "launch_stack", lambda *args, **kwargs: real_launch_stack(*args, sleep=lambda s: None, **kwargs)
    )
    return module, calls


def test_bring_up_uses_mocker_run_and_verifies_declared_settings(mocker_cli, monkeypatch, capsys):
    module, calls = _bring_up(monkeypatch, HEALTHY_PROBE)
    assert module.main(["--platform", "doris"]) == 0
    assert any(call[:3] == ["mocker", "run", "-d"] for call in calls)
    assert not any(call[:2] == ["mocker", "compose"] for call in calls)
    assert "UAT bring-up OK for doris" in capsys.readouterr().out


def test_bring_up_still_fails_loudly_when_the_launched_container_lacks_a_setting(mocker_cli, monkeypatch, capsys):
    module, _ = _bring_up(monkeypatch, HEALTHY_PROBE.replace("4294967296", "1112000000").replace("4194304", "1086000"))
    assert module.main(["--platform", "doris"]) == 1
    err = capsys.readouterr().err
    assert "UAT bring-up failed for doris" in err and "memory: declared 4.00 GiB" in err


def test_bring_down_removes_the_launched_container(mocker_cli, monkeypatch, capsys):
    module, calls = _bring_up(monkeypatch, "")
    assert module.main(["--platform", "doris", "--down"]) == 0
    assert any(call[:3] == ["mocker", "rm", "-f"] for call in calls)
    assert "UAT bring-down OK for doris" in capsys.readouterr().out


def test_bring_down_with_volumes_removes_them(mocker_cli, monkeypatch):
    module = _load_bring_up_module()
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        stdout = "local benchbox-manual-doris-doris-fe-meta\n" if argv[:3] == ["mocker", "volume", "ls"] else ""
        return docker_assets.DockerCommandResult(tuple(argv), 0, stdout, "")

    monkeypatch.setattr(module.docker_assets, "run_docker_command", fake_run)
    project = docker_assets.compose_project_name("manual", "doris")
    monkeypatch.setattr(
        mocker_launch.docker_assets,
        "expected_mocker_volume_names",
        lambda project_name, spec: ("benchbox-manual-doris-doris-fe-meta",),
    )
    assert project
    assert module.main(["--platform", "doris", "--down", "--remove-volumes"]) == 0
    assert ["mocker", "volume", "rm", "benchbox-manual-doris-doris-fe-meta"] in calls


def test_launch_reuses_an_existing_named_volume(mocker_cli):
    spec = docker_assets.docker_platform_spec("singlestore")

    def script(argv):
        if argv[:3] == ["mocker", "volume", "create"]:
            return docker_assets.DockerCommandResult(tuple(argv), 1, "", "Error: Volume x already exists")
        return None

    run = _runner(script)
    assert mocker_launch.launch_stack(spec, "p", env={}, runner=run, sleep=lambda s: None).succeeded
    assert any(call[:3] == ["mocker", "run", "-d"] for call in run.calls)
