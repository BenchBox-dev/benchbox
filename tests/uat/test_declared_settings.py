from __future__ import annotations

import dataclasses
import importlib.util
from pathlib import Path

import pytest

from benchbox.utils import host_lock
from tests.uat import docker_assets, managed_runtime, matrix
from tests.uat.config import validate_config
from tests.uat.conftest import healthy_ps_stdout, platform_reachability
from tests.uat.phases import execute as exec_phase

pytestmark = pytest.mark.medium

GIB = 1024**3

HEALTHY_PROBE = (
    "cgroup_memory=max\n"
    "mem_total_kb={mem_kb}\n"
    "ulimit_l_soft=unlimited\n"
    "ulimit_l_hard=unlimited\n"
    "ulimit_n_soft=1048576\n"
    "ulimit_n_hard=1048576\n"
    "ulimit_u_soft=unlimited\n"
    "ulimit_u_hard=unlimited\n"
    "arch=x86_64\n"
    "entrypoint_marker=present\n"
)


def _spec(tmp_path: Path, body: str) -> docker_assets.DockerPlatformSpec:
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(body, encoding="utf-8")
    return docker_assets.DockerPlatformSpec(platform="fake", compose_files=(compose_file,), services=("db",))


def _probe(mem_gib: float = 16, **overrides: str) -> str:
    observed = managed_runtime.parse_probe_output(HEALTHY_PROBE.format(mem_kb=int(mem_gib * GIB / 1024)))
    observed.update(overrides)
    return "\n".join(f"{key}={value}" for key, value in observed.items())


def _runner(stdout: str, *, returncode: int = 0, stderr: str = ""):
    calls: list[list[str]] = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        return docker_assets.DockerCommandResult(tuple(argv), returncode, stdout, stderr)

    run.calls = calls
    return run


def test_compose_declared_settings_reads_every_supported_declaration(tmp_path: Path):
    spec = _spec(
        tmp_path,
        "services:\n"
        "  db:\n"
        "    platform: ${FAKE_PLATFORM:-linux/amd64}\n"
        "    mem_limit: ${FAKE_MEM:-6g}\n"
        "    ulimits:\n"
        "      memlock:\n"
        "        soft: 8589934592\n"
        "        hard: 8589934592\n"
        "      nofile: 65536\n"
        "    entrypoint:\n"
        "      - sh\n"
        "      - -c\n"
        f"      - touch {managed_runtime.ENTRYPOINT_MARKER_PATH}\n",
    )
    settings = managed_runtime.compose_declared_settings(spec, env={})["db"]
    assert settings.platform == "linux/amd64"
    assert settings.memory_bytes == 6 * GIB
    assert settings.entrypoint is True
    assert {(limit.name, limit.soft, limit.hard) for limit in settings.ulimits} == {
        ("memlock", 8589934592, 8589934592),
        ("nofile", 65536, 65536),
    }
    overridden = managed_runtime.compose_declared_settings(
        spec, env={"FAKE_PLATFORM": "linux/arm64", "FAKE_MEM": "2g"}
    )["db"]
    assert overridden.platform == "linux/arm64" and overridden.memory_bytes == 2 * GIB


def test_compose_declared_settings_ignores_services_that_declare_nothing(tmp_path: Path):
    spec = _spec(tmp_path, "services:\n  db:\n    image: postgres:16\n")
    assert managed_runtime.compose_declared_settings(spec, env={}) == {}


def test_compose_declared_settings_refuses_an_entrypoint_without_the_marker(tmp_path: Path):
    spec = _spec(tmp_path, "services:\n  db:\n    entrypoint: [sh, -c, 'exec true']\n")
    with pytest.raises(docker_assets.DockerAssetError, match="cannot be detected"):
        managed_runtime.compose_declared_settings(spec, env={})


def test_compose_declared_settings_refuses_an_unsupported_ulimit(tmp_path: Path):
    spec = _spec(tmp_path, "services:\n  db:\n    ulimits:\n      core: 0\n")
    with pytest.raises(docker_assets.DockerAssetError, match="core"):
        managed_runtime.compose_declared_settings(spec, env={})


def test_compose_declared_settings_requires_unset_variables_without_a_default(tmp_path: Path):
    spec = _spec(tmp_path, "services:\n  db:\n    mem_limit: ${FAKE_REQUIRED_MEM}\n")
    with pytest.raises(docker_assets.DockerAssetError, match="FAKE_REQUIRED_MEM"):
        managed_runtime.compose_declared_settings(spec, env={})


def test_every_shipped_compose_stack_can_be_verified():
    env = {"CLICKHOUSE_MEMORY_LIMIT": "4g"}
    for platform, spec in docker_assets.docker_platform_specs().items():
        managed_runtime.compose_declared_settings(spec, env=env)


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("doris", {"entrypoint"}),
        ("singlestore", {"platform"}),
        ("presto", {"memory"}),
    ],
)
def test_shipped_stacks_declare_the_settings_the_engines_have_dropped(platform: str, expected: set[str]):
    spec = docker_assets.docker_platform_spec(platform)
    declared = managed_runtime.compose_declared_settings(spec, env={})
    names = {name for settings in declared.values() for name in managed_runtime._declared_names(settings).split(", ")}
    assert expected <= names


def test_the_firebolt_compose_file_declares_its_memlock_ulimit():
    spec = docker_assets.DockerPlatformSpec(
        platform="firebolt", compose_files=(docker_assets.REPO_ROOT / "docker/firebolt/docker-compose.yml",)
    )
    [settings] = managed_runtime.compose_declared_settings(spec, env={}).values()
    assert [(limit.name, limit.soft) for limit in settings.ulimits] == [("memlock", 8589934592)]


def test_doris_entrypoint_marker_is_created_before_the_server_is_exec_ed():
    text = (docker_assets.REPO_ROOT / "docker/doris/docker-compose.yml").read_text(encoding="utf-8")
    assert text.index(managed_runtime.ENTRYPOINT_MARKER_PATH) < text.index("exec bash /usr/local/bin/entry_point.sh")


def test_a_container_that_honors_every_setting_has_no_violations():
    settings = managed_runtime.DeclaredServiceSettings(
        service="db",
        memory_bytes=4 * GIB,
        ulimits=(managed_runtime.DeclaredUlimit("memlock", 8 * GIB, 8 * GIB),),
        platform="linux/amd64",
        entrypoint=True,
    )
    observed = managed_runtime.parse_probe_output(_probe(mem_gib=3.9))
    assert managed_runtime.declared_settings_violations(settings, observed) == []


def test_a_dropped_memory_limit_names_the_declared_and_observed_sizes():
    settings = managed_runtime.DeclaredServiceSettings(service="db", memory_bytes=8 * GIB)
    observed = managed_runtime.parse_probe_output(_probe(mem_gib=1112 / 1024))
    [violation] = managed_runtime.declared_settings_violations(settings, observed)
    assert violation.startswith("memory: declared 8.00 GiB, container sees 1.09 GiB")


def test_a_numeric_cgroup_limit_below_the_vm_memory_wins():
    settings = managed_runtime.DeclaredServiceSettings(service="db", memory_bytes=8 * GIB)
    observed = managed_runtime.parse_probe_output(_probe(mem_gib=16, cgroup_memory=str(2 * GIB)))
    assert managed_runtime.declared_settings_violations(settings, observed)
    observed = managed_runtime.parse_probe_output(_probe(mem_gib=16, cgroup_memory=str(8 * GIB)))
    assert managed_runtime.declared_settings_violations(settings, observed) == []


def test_a_memory_limit_the_container_cannot_report_is_a_violation():
    settings = managed_runtime.DeclaredServiceSettings(service="db", memory_bytes=GIB)
    [violation] = managed_runtime.declared_settings_violations(settings, {})
    assert "did not report a memory limit" in violation


def test_a_dropped_memlock_ulimit_is_a_violation():
    settings = managed_runtime.DeclaredServiceSettings(
        service="db", ulimits=(managed_runtime.DeclaredUlimit("memlock", 8 * GIB, 8 * GIB),)
    )
    observed = managed_runtime.parse_probe_output(_probe(ulimit_l_soft="64", ulimit_l_hard="64"))
    [violation] = managed_runtime.declared_settings_violations(settings, observed)
    assert violation.startswith("ulimit memlock soft: declared 8388608, container has 64")


def test_an_unlimited_declared_ulimit_requires_an_unlimited_container():
    settings = managed_runtime.DeclaredServiceSettings(
        service="db", ulimits=(managed_runtime.DeclaredUlimit("memlock", -1, -1),)
    )
    observed = managed_runtime.parse_probe_output(_probe(ulimit_l_soft="65536", ulimit_l_hard="unlimited"))
    [violation] = managed_runtime.declared_settings_violations(settings, observed)
    assert "declared unlimited" in violation


def test_a_raised_ulimit_satisfies_the_declaration():
    settings = managed_runtime.DeclaredServiceSettings(
        service="db", ulimits=(managed_runtime.DeclaredUlimit("nofile", 60000, 60000),)
    )
    observed = managed_runtime.parse_probe_output(_probe(ulimit_n_soft="1048576", ulimit_n_hard="1048576"))
    assert managed_runtime.declared_settings_violations(settings, observed) == []


def test_a_dropped_platform_is_a_violation():
    settings = managed_runtime.DeclaredServiceSettings(service="db", platform="linux/amd64")
    observed = managed_runtime.parse_probe_output(_probe(arch="aarch64"))
    [violation] = managed_runtime.declared_settings_violations(settings, observed)
    assert violation == "platform: declared linux/amd64, container reports machine aarch64"


def test_a_dropped_entrypoint_is_a_violation():
    settings = managed_runtime.DeclaredServiceSettings(service="db", entrypoint=True)
    observed = managed_runtime.parse_probe_output(_probe(entrypoint_marker="absent"))
    [violation] = managed_runtime.declared_settings_violations(settings, observed)
    assert violation.startswith("entrypoint: declared entrypoint did not run")


def test_verification_reports_every_dropped_setting_of_every_service(tmp_path: Path):
    spec = _spec(
        tmp_path,
        "services:\n"
        "  db:\n"
        "    mem_limit: 8g\n"
        "    ulimits:\n"
        "      memlock: -1\n"
        "    entrypoint: [sh, -c, 'touch /tmp/.benchbox-entrypoint-applied']\n",
    )
    runner = _runner(_probe(mem_gib=1, ulimit_l_soft="64", ulimit_l_hard="64", entrypoint_marker="absent"))
    reason = managed_runtime.verify_declared_settings(spec, "proj", env={}, runner=runner)
    assert reason is not None
    assert "did not apply the compose settings declared for fake" in reason
    assert "service db:" in reason
    assert "memory: declared 8.00 GiB" in reason
    assert "ulimit memlock soft" in reason
    assert "entrypoint: declared entrypoint did not run" in reason
    argv = runner.calls[0]
    assert argv[argv.index("exec") :][:3] == ["exec", "-T", "db"]
    assert "-p" in argv and "proj" in argv


def test_verification_passes_for_a_container_with_the_declared_settings(tmp_path: Path):
    spec = _spec(tmp_path, "services:\n  db:\n    mem_limit: 4g\n    platform: linux/amd64\n")
    assert managed_runtime.verify_declared_settings(spec, "proj", env={}, runner=_runner(_probe(mem_gib=3.9))) is None


def test_verification_runs_no_probe_when_nothing_is_declared(tmp_path: Path):
    spec = _spec(tmp_path, "services:\n  db:\n    image: postgres:16\n")
    runner = _runner("")
    assert managed_runtime.verify_declared_settings(spec, "proj", env={}, runner=runner) is None
    assert runner.calls == []


def test_verification_fails_loudly_when_the_probe_cannot_run(tmp_path: Path):
    spec = _spec(tmp_path, "services:\n  db:\n    mem_limit: 4g\n")
    runner = _runner("", returncode=1, stderr="container is not running")
    reason = managed_runtime.verify_declared_settings(spec, "proj", env={}, runner=runner)
    assert reason is not None
    assert "could not read the container's effective settings to verify memory: container is not running" in reason


def test_verification_reports_an_unreadable_declaration(tmp_path: Path):
    spec = _spec(tmp_path, "services:\n  db:\n    mem_limit: ${FAKE_REQUIRED_MEM}\n")
    reason = managed_runtime.verify_declared_settings(spec, "proj", env={}, runner=_runner(""))
    assert reason is not None and "FAKE_REQUIRED_MEM" in reason


def _load_bring_up_module():
    path = Path("scripts/uat-bring-up/uat_bring_up.py").resolve()
    spec = importlib.util.spec_from_file_location("uat_bring_up_declared_settings", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bring_up(monkeypatch, probe_stdout: str):
    module = _load_bring_up_module()
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        stdout = probe_stdout if "exec" in argv else ""
        return docker_assets.DockerCommandResult(tuple(argv), 0, stdout, "")

    monkeypatch.setenv(host_lock.OWNER_ENV, "1:1.0")
    monkeypatch.setattr(module.docker_assets, "run_docker_command", fake_run)
    monkeypatch.setattr(matrix, "platform_is_reachable", lambda platform: True)
    return module, calls


def test_bring_up_fails_when_the_engine_dropped_the_doris_entrypoint(monkeypatch, capsys):
    module, calls = _bring_up(monkeypatch, _probe(entrypoint_marker="absent"))
    assert module.main(["--platform", "doris"]) == 1
    err = capsys.readouterr().err
    assert "UAT bring-up failed for doris" in err
    assert "entrypoint: declared entrypoint did not run" in err
    assert any("exec" in call for call in calls)


def test_bring_up_reports_healthy_when_the_doris_entrypoint_ran(monkeypatch, capsys):
    module, _ = _bring_up(monkeypatch, _probe())
    assert module.main(["--platform", "doris"]) == 0
    assert "UAT bring-up OK for doris" in capsys.readouterr().out


def test_bring_up_dry_run_does_not_probe(monkeypatch):
    module, calls = _bring_up(monkeypatch, "")
    assert module.main(["--platform", "doris", "--dry-run"]) == 0
    assert not any("exec" in call for call in calls)


def _config(platform: str):
    return validate_config({"name": "declared settings", "platforms": {"include": [platform]}})


def test_managed_check_records_a_failed_event_and_returns_the_reason(tmp_path: Path):
    spec = docker_assets.docker_platform_spec("doris")
    events: list[dict] = []

    def record(events_list, **kwargs):
        events_list.append(kwargs)

    reason = managed_runtime.check_declared_settings(
        _config("doris"),
        spec=spec,
        project_name="proj",
        docker_runner=_runner(_probe(entrypoint_marker="absent")),
        docker_events=events,
        record_event=record,
        log_dir=None,
        benchmark_runs_dir=tmp_path,
    )
    assert reason is not None
    assert reason.startswith("UAT-managed Docker declared-settings check failed for doris project proj:")
    assert events[0]["action"] == "declared-settings" and events[0]["status"] == "failed"


def test_managed_check_is_skipped_in_a_dry_run(tmp_path: Path):
    config = _config("doris")
    config = dataclasses.replace(config, dry_run=True)
    runner = _runner("")
    reason = managed_runtime.check_declared_settings(
        config,
        spec=docker_assets.docker_platform_spec("doris"),
        project_name="proj",
        docker_runner=runner,
        docker_events=[],
        record_event=lambda *args, **kwargs: None,
        log_dir=None,
        benchmark_runs_dir=tmp_path,
    )
    assert reason is None and runner.calls == []


def test_memory_is_left_to_the_runtime_stats_check_for_clickhouse_and_starrocks():
    env = {"CLICKHOUSE_MEMORY_LIMIT": "4g"}
    for platform in ("clickhouse-server", "starrocks"):
        declared = managed_runtime.compose_declared_settings(docker_assets.docker_platform_spec(platform), env=env)
        assert all(settings.memory_bytes is None for settings in declared.values())


def _readiness(tmp_path: Path, probe_stdout: str, *, reachable: bool = True):
    events: list = []

    def runner(argv, **kwargs):
        stdout = probe_stdout if "exec" in argv else healthy_ps_stdout("doris")
        return docker_assets.DockerCommandResult(tuple(argv), 0, stdout, "")

    with platform_reachability(reachable):
        reason = exec_phase._check_docker_platform_readiness(
            _config("doris"),
            spec=docker_assets.docker_platform_spec("doris"),
            project_name="proj",
            platform="doris",
            docker_runner=runner,
            docker_events=events,
            log_dir=None,
            benchmark_runs_dir=tmp_path,
            sleep_fn=lambda seconds: None,
            memory_reader=lambda: None,
        )
    return reason, events


def test_execute_readiness_fails_a_started_stack_whose_engine_dropped_a_declared_setting(tmp_path: Path):
    reason, events = _readiness(tmp_path, _probe(entrypoint_marker="absent"))
    assert reason is not None
    assert "declared-settings check failed for doris" in reason
    assert "entrypoint: declared entrypoint did not run" in reason
    assert [event.status for event in events if event.action == "declared-settings"] == ["failed"]
    assert not any(event.action == "readiness" and event.status == "ok" for event in events)


def test_execute_readiness_names_the_dropped_setting_even_when_the_server_is_unreachable(tmp_path: Path):
    reason, _ = _readiness(tmp_path, _probe(entrypoint_marker="absent"), reachable=False)
    assert reason is not None and "declared-settings check failed" in reason


def test_execute_readiness_passes_when_every_declared_setting_was_applied(tmp_path: Path):
    reason, events = _readiness(tmp_path, _probe())
    assert reason is None
    assert ("declared-settings", "ok") in [(event.action, event.status) for event in events]


