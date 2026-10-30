from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest
import yaml

from tests.uat import config, docker_assets, managed_runtime, matrix
from tests.uat.docker_path_helpers import compose_path_ends_with

pytestmark = pytest.mark.medium


def test_docker_specs_cover_matrix_docker_group_only():
    specs = docker_assets.docker_platform_specs()
    assert set(specs) == set(matrix.PLATFORM_GROUPS["docker"])
    assert "duckdb" not in specs
    assert "firebolt" not in specs


def test_compose_down_commands_are_project_scoped_and_targeted():
    spec = docker_assets.docker_platform_spec("postgresql")
    project = "benchbox-uat-smoke-postgresql"

    containers = docker_assets.compose_down_command(spec, project, "containers")
    volumes = docker_assets.compose_down_command(spec, project, "volumes")
    images = docker_assets.compose_down_command(spec, project, "images")

    assert containers[:4] == ["docker", "compose", "-p", project]
    assert "down" in containers
    assert "-v" not in containers
    assert "--remove-orphans" in containers
    assert volumes[:4] == ["docker", "compose", "-p", project]
    assert "-v" in volumes
    assert images[:4] == ["docker", "compose", "-p", project]
    assert images[-2:] == ["--rmi", "local"]
    for argv in (containers, volumes, images):
        assert not docker_assets.command_has_forbidden_prune(argv)
        assert "-f" in argv
        assert any(compose_path_ends_with(part, "docker", "postgresql", "docker-compose.yml") for part in argv)


@pytest.mark.parametrize(
    ("platform", "service"),
    [
        ("lakesail", "lakesail-connect"),
        ("velox", "velox-connect"),
    ],
)
def test_compose_up_command_includes_wait_timeout_and_scoped_service(platform, service):
    spec = docker_assets.docker_platform_spec(platform)
    project = f"benchbox-uat-smoke-{platform}"
    argv = docker_assets.compose_up_command(spec, project, start_timeout_s=42)
    assert argv[:4] == ["docker", "compose", "-p", project]
    assert argv[-5:] == ["-d", "--wait", "--wait-timeout", "42", service]


def test_compose_pull_command_uses_ignore_buildable_and_is_project_scoped():
    spec = docker_assets.docker_platform_spec("postgresql")
    project = "benchbox-uat-smoke-postgresql"
    argv = docker_assets.compose_pull_command(spec, project)
    assert argv[:4] == ["docker", "compose", "-p", project]
    assert "pull" in argv
    assert "--ignore-buildable" in argv
    assert not docker_assets.command_has_forbidden_prune(argv)


def test_compose_pull_command_scopes_to_declared_service():
    spec = docker_assets.docker_platform_spec("lakesail")
    project = "benchbox-uat-smoke-lakesail"
    argv = docker_assets.compose_pull_command(spec, project)
    assert argv[-1] == "lakesail-connect"


def test_compose_build_command_is_project_scoped():
    spec = docker_assets.docker_platform_spec("lakesail")
    project = "benchbox-uat-smoke-lakesail"
    argv = docker_assets.compose_build_command(spec, project)
    assert argv[:4] == ["docker", "compose", "-p", project]
    assert "build" in argv
    assert argv[-1] == "lakesail-connect"


def test_compose_project_name_sanitizes_and_bounds_length():
    name = docker_assets.compose_project_name(
        "UAT! 2026/05 storage constrained sweep with a very long config name",
        "pg.duckdb",
        "BenchBox_UAT",
    )
    assert len(name) <= 63
    assert re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name)
    assert name == docker_assets.compose_project_name(
        "UAT! 2026/05 storage constrained sweep with a very long config name",
        "pg.duckdb",
        "BenchBox_UAT",
    )


def test_docker_platform_services_populated_and_match_compose_files():
    specs = docker_assets.docker_platform_specs()
    assert not [platform for platform, spec in specs.items() if not spec.services]

    for platform, spec in specs.items():
        declared: list[str] = []
        for compose_file in spec.compose_files:
            data = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
            services = (data.get("services") or {}) if isinstance(data, dict) else {}
            for name, definition in services.items():
                if isinstance(definition, dict) and definition.get("profiles"):
                    continue
                declared.append(name)
        assert set(spec.services) == set(declared), (platform, spec.services, declared)


def test_compose_project_name_container_name_budget_derives_from_service_len():
    long_config = "x" * 80
    short_service_name = docker_assets.compose_project_name(long_config, "cedardb")
    long_service_name = docker_assets.compose_project_name(long_config, "lakesail")

    assert len(long_service_name) < len(short_service_name)
    assert len(short_service_name) <= 63
    assert len(long_service_name) <= 63


def _longest_declared_service_name(spec: docker_assets.DockerPlatformSpec) -> str:
    longest = ""
    for compose_file in spec.compose_files:
        data = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
        services = (data.get("services") or {}) if isinstance(data, dict) else {}
        for name, definition in services.items():
            if isinstance(definition, dict) and definition.get("profiles"):
                continue
            if len(name) > len(longest):
                longest = name
    assert longest, f"no non-profile-gated service found in {spec.compose_files}"
    return longest


@pytest.mark.parametrize("replica_index", ["1", "10"])
def test_container_name_budget_fits_every_config_and_platform(replica_index):
    config_dir = docker_assets.REPO_ROOT / "tests" / "uat" / "configs"
    config_paths = sorted(config_dir.glob("*.yaml"))
    assert config_paths, "expected at least one checked-in UAT config"

    specs = docker_assets.docker_platform_specs()
    assert len(specs) == 16, "expected all 16 registered Docker platforms"

    configs = [config.load_config(p) for p in config_paths]
    over_long_prefix = "benchbox-uat-" + "x" * 40

    overflows = []
    for cfg in configs:
        for platform, spec in specs.items():
            service = _longest_declared_service_name(spec)
            for prefix in (cfg.cleanup.docker_project_prefix, over_long_prefix):
                project = docker_assets.compose_project_name(cfg.name, platform, prefix)
                container_id = f"{project}-{service}-{replica_index}"
                if len(container_id) > 64:
                    overflows.append((cfg.name, platform, prefix, len(container_id)))

    assert not overflows, f"{len(overflows)} (config, platform, prefix) pairs overflow: {overflows[:5]}"


def test_validate_project_name_budget_rejects_operator_supplied_overflow():
    spec = docker_assets.docker_platform_spec("lakesail")
    budget = docker_assets._project_name_budget("lakesail")
    over_budget_name = "x" * (budget + 1)

    with pytest.raises(docker_assets.DockerAssetError, match=str(budget)) as excinfo:
        docker_assets.validate_project_name_budget(spec, over_budget_name)
    assert str(len(over_budget_name)) in str(excinfo.value)
    assert "lakesail" in str(excinfo.value)

    within_budget_name = "x" * budget
    docker_assets.validate_project_name_budget(spec, within_budget_name)


@pytest.mark.parametrize(
    "command_fn",
    [
        lambda spec, project: docker_assets.compose_up_command(spec, project),
        lambda spec, project: docker_assets.compose_pull_command(spec, project),
        lambda spec, project: docker_assets.compose_build_command(spec, project),
        lambda spec, project: docker_assets.compose_down_command(spec, project, "containers"),
        lambda spec, project: docker_assets.compose_ps_command(spec, project),
    ],
    ids=["up", "pull", "build", "down", "ps"],
)
def test_every_compose_command_rejects_operator_supplied_project_name_overflow(command_fn):
    spec = docker_assets.docker_platform_spec("lakesail")
    budget = docker_assets._project_name_budget("lakesail")
    over_budget_name = "x" * (budget + 1)

    with pytest.raises(docker_assets.DockerAssetError, match=str(budget)):
        command_fn(spec, over_budget_name)


def test_matrix_docker_specs_are_project_scoped_for_managed_start():
    for platform in matrix.PLATFORM_GROUPS["docker"]:
        spec = docker_assets.docker_platform_spec(platform)
        assert spec.managed_start_allowed is True
        assert spec.fixed_container_names == ()
        docker_assets.validate_managed_start_allowed(spec, "fail")


def test_fixed_container_name_specs_are_rejected_for_managed_start():
    spec = docker_assets.DockerPlatformSpec(
        platform="fixed-name",
        compose_files=(Path("docker-compose.yml"),),
        fixed_container_names=("benchbox-fixed",),
    )
    with pytest.raises(docker_assets.DockerAssetError, match="fixed container_name"):
        docker_assets.validate_managed_start_allowed(spec, "fail")


def test_run_docker_command_dry_run_records_without_execution():
    result = docker_assets.run_docker_command(["docker", "compose", "ps"], dry_run=True)
    assert result.succeeded
    assert result.dry_run is True
    assert result.command == "docker compose ps"


def test_run_docker_command_refuses_global_prune():
    result = docker_assets.run_docker_command(["docker", "system", "prune"], dry_run=True)
    assert not result.succeeded
    assert "forbidden" in (result.error or "")


@pytest.mark.parametrize("platform", ["lakesail", "velox"])
def test_path_mirrored_compose_environment_points_at_benchmark_runs_dir(platform, tmp_path):
    spec = docker_assets.docker_platform_spec(platform)
    env = docker_assets.compose_environment(spec, benchmark_runs_dir=tmp_path / "runs")
    assert env == {"BENCHBOX_DATA_DIR": str(tmp_path / "runs")}


@pytest.mark.parametrize("platform", ["lakesail", "velox"])
@pytest.mark.parametrize(
    "relative_dir",
    (
        Path("relative_runs"),
        Path("./relative_runs"),
        Path("../sibling_runs"),
        "relative_runs",
    ),
)
def test_compose_environment_raises_for_relative_benchmark_runs_dir(platform, relative_dir):
    spec = docker_assets.docker_platform_spec(platform)

    with pytest.raises(docker_assets.DockerAssetError, match="absolute"):
        docker_assets.compose_environment(spec, benchmark_runs_dir=relative_dir)


@pytest.mark.parametrize("platform", ["postgresql", "clickhouse-server", "questdb"])
def test_compose_environment_ignores_relative_dir_for_non_path_mirroring_platforms(platform):
    spec = docker_assets.docker_platform_spec(platform)

    expected = {docker_assets.CLICKHOUSE_MEMORY_LIMIT_ENV_VAR: "4g"} if platform == "clickhouse-server" else {}
    assert (
        docker_assets.compose_environment(spec, benchmark_runs_dir=Path("relative_runs"), memory_limit="4g") == expected
    )


def test_starrocks_compose_memory_defaults_for_manual_use_and_harness_overrides(monkeypatch):
    spec = docker_assets.docker_platform_spec("starrocks")
    compose = yaml.safe_load(spec.compose_files[0].read_text(encoding="utf-8"))
    assert compose["services"]["starrocks"]["mem_limit"] == "${STARROCKS_MEMORY_LIMIT:-4g}"

    monkeypatch.delenv(managed_runtime.STARROCKS_MEMORY_LIMIT_ENV_VAR, raising=False)
    assert docker_assets.compose_environment(spec) == {managed_runtime.STARROCKS_MEMORY_LIMIT_ENV_VAR: "4g"}

    monkeypatch.setenv(managed_runtime.STARROCKS_MEMORY_LIMIT_ENV_VAR, "6g")
    assert docker_assets.compose_environment(spec, starrocks_memory_limit="4g") == {
        managed_runtime.STARROCKS_MEMORY_LIMIT_ENV_VAR: "6g"
    }


@pytest.mark.parametrize("platform", ["lakesail", "velox"])
def test_compose_environment_error_names_platform_and_the_offending_value(platform):
    spec = docker_assets.docker_platform_spec(platform)

    with pytest.raises(docker_assets.DockerAssetError) as excinfo:
        docker_assets.compose_environment(spec, benchmark_runs_dir="relative_runs")

    assert platform in str(excinfo.value)
    assert "relative_runs" in str(excinfo.value)


@pytest.mark.parametrize("platform", ["lakesail", "velox"])
def test_compose_environment_raises_for_missing_benchmark_runs_dir(platform):
    spec = docker_assets.docker_platform_spec(platform)

    with pytest.raises(docker_assets.DockerAssetError, match="benchmark_runs_dir"):
        docker_assets.compose_environment(spec, benchmark_runs_dir=None)

    with pytest.raises(docker_assets.DockerAssetError):
        docker_assets.compose_environment(spec)


@pytest.mark.parametrize("platform", ["postgresql", "clickhouse-server", "questdb"])
def test_compose_environment_returns_empty_for_missing_dir_on_non_path_mirroring_platforms(platform):
    spec = docker_assets.docker_platform_spec(platform)

    expected = {docker_assets.CLICKHOUSE_MEMORY_LIMIT_ENV_VAR: "4g"} if platform == "clickhouse-server" else {}
    assert docker_assets.compose_environment(spec, benchmark_runs_dir=None, memory_limit="4g") == expected
    assert docker_assets.compose_environment(spec, memory_limit="4g") == expected


def test_clickhouse_compose_environment_requires_an_explicit_memory_request(monkeypatch):
    monkeypatch.delenv(docker_assets.CLICKHOUSE_MEMORY_LIMIT_ENV_VAR, raising=False)
    spec = docker_assets.docker_platform_spec("clickhouse-server")
    with pytest.raises(docker_assets.DockerAssetError, match="CLICKHOUSE_MEMORY_LIMIT"):
        docker_assets.compose_environment(spec)


def test_clickhouse_compose_environment_process_override_wins(monkeypatch):
    monkeypatch.setenv(docker_assets.CLICKHOUSE_MEMORY_LIMIT_ENV_VAR, "8g")
    spec = docker_assets.docker_platform_spec("clickhouse-server")
    assert (
        docker_assets.compose_environment(spec, memory_limit="4g")[docker_assets.CLICKHOUSE_MEMORY_LIMIT_ENV_VAR]
        == "8g"
    )


@pytest.mark.parametrize("platform", ["postgresql", "pg-duckdb", "pg-mooncake", "timescaledb"])
def test_local_managed_postgres_compose_password_matches_uat_argv(platform):
    spec = docker_assets.docker_platform_spec(platform)
    compose_text = "\n".join(path.read_text() for path in spec.compose_files)
    argv = matrix.benchbox_run_argv(platform, "tpch", 0.01, local_managed_platform=True)

    assert "POSTGRES_PASSWORD: benchbox" in compose_text
    assert "password=benchbox" in argv


def test_local_managed_clickhouse_compose_password_matches_uat_argv():
    spec = docker_assets.docker_platform_spec("clickhouse-server")
    compose_text = "\n".join(path.read_text() for path in spec.compose_files)
    argv = matrix.benchbox_run_argv("clickhouse-server", "tpch", 0.01, local_managed_platform=True)

    assert "CLICKHOUSE_DB: default" in compose_text
    assert "CLICKHOUSE_PASSWORD: benchbox" in compose_text
    assert 'CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT: "1"' in compose_text
    assert "init-default-database.sql:/docker-entrypoint-initdb.d/init-default-database.sql:ro" in compose_text
    assert "password=benchbox" in argv


def test_local_managed_cedardb_compose_credentials_match_uat_argv():
    spec = docker_assets.docker_platform_spec("cedardb")
    compose_text = "\n".join(path.read_text() for path in spec.compose_files)
    argv = matrix.benchbox_run_argv("cedardb", "tpch", 0.01, local_managed_platform=True)

    assert "CEDAR_USER: benchbox" in compose_text
    assert "CEDAR_PASSWORD: Benchbox1!" in compose_text
    assert "mem_limit: 2g" in compose_text
    assert "CEDAR_DB: benchbox_test" in compose_text
    assert argv[-6:] == [
        "--platform-option",
        "username=benchbox",
        "--platform-option",
        "admin_database=benchbox_test",
        "--platform-option",
        "password=Benchbox1!",
    ]


def test_secondary_ports_follow_compose_mappings(monkeypatch, tmp_path):
    compose = tmp_path / "compose.yml"
    compose.write_text(
        """services:\n  doris:\n    ports:\n      - \"19031:9030\"\n      - \"28030:8030\"\n      - \"28040:8040\"\n"""
    )
    original = docker_assets.docker_platform_spec("doris")
    monkeypatch.setitem(
        docker_assets._DOCKER_PLATFORM_SPECS,
        "doris",
        dataclasses.replace(original, compose_files=(compose,)),
    )

    assert docker_assets.platform_extra_opts("doris") == [
        "--platform-option",
        "port=19031",
        "--platform-option",
        "http_port=28030",
        "--platform-option",
        "be_http_port=28040",
    ]


def test_password_follows_compose_environment_and_override(monkeypatch, tmp_path):
    compose = tmp_path / "compose.yml"
    compose.write_text(
        """services:\n  singlestore:\n    ports:\n      - \"13306:3306\"\n    environment:\n      ROOT_PASSWORD: ${ROOT_PASSWORD:-benchbox}\n"""
    )
    original = docker_assets.docker_platform_spec("singlestore")
    monkeypatch.setitem(
        docker_assets._DOCKER_PLATFORM_SPECS,
        "singlestore",
        dataclasses.replace(original, compose_files=(compose,)),
    )

    assert "password=changed" in docker_assets.platform_extra_opts("singlestore", env={"ROOT_PASSWORD": "changed"})


def test_connection_registries_do_not_duplicate_derived_values():
    assert all(
        not value.startswith(("password=", "http_port=", "be_http_port="))
        for values in docker_assets._PLATFORM_STATIC_OPTS.values()
        for value in values
    )
    assert all(
        not value.startswith("password=")
        for values in docker_assets._PLATFORM_LOCAL_MANAGED_STATIC_OPTS.values()
        for value in values
    )


def test_no_uat_platform_option_forces_shared_database():
    registries = (
        docker_assets._PLATFORM_STATIC_OPTS,
        docker_assets._PLATFORM_LOCAL_MANAGED_STATIC_OPTS,
    )
    for registry in registries:
        for platform, values in registry.items():
            assert not any(value.startswith("database=") for value in values), platform
    for platform in docker_assets._DOCKER_PLATFORM_SPECS:
        argvs = [
            " ".join(matrix.benchbox_run_argv(platform, benchmark, 0.01, local_managed_platform=managed))
            for benchmark in ("tpch", "tpcds")
            for managed in (False, True)
        ]
        assert not any("database=" in argv.replace("admin_database=", "") for argv in argvs), platform


def test_uat_platform_options_are_registered_for_every_docker_platform():
    from benchbox.core.hooks.platform_hooks import PlatformHookRegistry

    for platform in docker_assets._DOCKER_PLATFORM_SPECS:
        argv = matrix.benchbox_run_argv(platform, "tpch", 0.01, local_managed_platform=True)
        provided = [
            tuple(argv[index + 1].split("=", 1)) for index, token in enumerate(argv) if token == "--platform-option"
        ]
        PlatformHookRegistry.parse_options(platform, provided)


def test_resolve_container_cli_env_override_wins_over_platform_default(monkeypatch):
    monkeypatch.setenv(docker_assets.CONTAINER_CLI_ENV_VAR, "podman")
    monkeypatch.setattr(docker_assets, "_current_platform", lambda: "darwin")
    monkeypatch.setattr(docker_assets, "_which_container_cli", lambda cli: f"/usr/bin/{cli}")
    docker_assets.resolve_container_cli.cache_clear()
    try:
        assert docker_assets.resolve_container_cli() == "podman"
    finally:
        docker_assets.resolve_container_cli.cache_clear()


def test_resolve_container_cli_darwin_prefers_mocker_when_present(monkeypatch):
    monkeypatch.delenv(docker_assets.CONTAINER_CLI_ENV_VAR, raising=False)
    monkeypatch.setattr(docker_assets, "_current_platform", lambda: "darwin")
    monkeypatch.setattr(docker_assets, "_which_container_cli", lambda cli: f"/opt/homebrew/bin/{cli}")
    docker_assets.resolve_container_cli.cache_clear()
    try:
        assert docker_assets.resolve_container_cli() == "mocker"
    finally:
        docker_assets.resolve_container_cli.cache_clear()


def test_resolve_container_cli_darwin_falls_back_to_docker_without_mocker(monkeypatch):
    monkeypatch.delenv(docker_assets.CONTAINER_CLI_ENV_VAR, raising=False)
    monkeypatch.setattr(docker_assets, "_current_platform", lambda: "darwin")
    monkeypatch.setattr(
        docker_assets, "_which_container_cli", lambda cli: None if cli == "mocker" else "/usr/bin/docker"
    )
    docker_assets.resolve_container_cli.cache_clear()
    try:
        assert docker_assets.resolve_container_cli() == "docker"
    finally:
        docker_assets.resolve_container_cli.cache_clear()


def test_resolve_container_cli_non_darwin_never_probes_mocker(monkeypatch):
    monkeypatch.delenv(docker_assets.CONTAINER_CLI_ENV_VAR, raising=False)
    monkeypatch.setattr(docker_assets, "_current_platform", lambda: "linux")
    probed: list[str] = []

    def fake_which(cli):
        probed.append(cli)
        return "/usr/bin/docker" if cli == "docker" else None

    monkeypatch.setattr(docker_assets, "_which_container_cli", fake_which)
    docker_assets.resolve_container_cli.cache_clear()
    try:
        assert docker_assets.resolve_container_cli() == "docker"
        assert probed == ["docker"]
    finally:
        docker_assets.resolve_container_cli.cache_clear()


def test_resolve_container_cli_hard_errors_when_resolved_binary_missing(monkeypatch):
    monkeypatch.setenv(docker_assets.CONTAINER_CLI_ENV_VAR, "ghost-cli")
    monkeypatch.setattr(docker_assets, "_which_container_cli", lambda cli: None)
    docker_assets.resolve_container_cli.cache_clear()
    try:
        with pytest.raises(docker_assets.DockerAssetError, match="ghost-cli"):
            docker_assets.resolve_container_cli()
    finally:
        docker_assets.resolve_container_cli.cache_clear()


def test_resolve_container_cli_is_memoized_across_calls(monkeypatch):
    monkeypatch.setenv(docker_assets.CONTAINER_CLI_ENV_VAR, "docker")
    calls: list[str] = []

    def fake_which(cli):
        calls.append(cli)
        return "/usr/bin/docker"

    monkeypatch.setattr(docker_assets, "_which_container_cli", fake_which)
    docker_assets.resolve_container_cli.cache_clear()
    try:
        assert docker_assets.resolve_container_cli() == "docker"
        assert docker_assets.resolve_container_cli() == "docker"
        assert calls == ["docker"]
    finally:
        docker_assets.resolve_container_cli.cache_clear()


def test_compose_commands_use_the_resolved_engine_binary(monkeypatch):
    monkeypatch.setenv(docker_assets.CONTAINER_CLI_ENV_VAR, "mocker")
    monkeypatch.setattr(docker_assets, "_which_container_cli", lambda cli: f"/opt/homebrew/bin/{cli}")
    docker_assets.resolve_container_cli.cache_clear()
    try:
        spec = docker_assets.docker_platform_spec("postgresql")
        argv = docker_assets.compose_up_command(spec, "benchbox-uat-smoke-postgresql")
        assert argv[0] == "mocker"
    finally:
        docker_assets.resolve_container_cli.cache_clear()


def test_command_has_forbidden_prune_matches_compose_engines_but_exempts_apple_container():
    assert docker_assets.command_has_forbidden_prune(["mocker", "system", "prune"])
    assert docker_assets.command_has_forbidden_prune(["mocker", "volume", "prune"])
    assert docker_assets.command_has_forbidden_prune(["docker", "image", "prune", "-f"])
    assert docker_assets.command_has_forbidden_prune(["podman", "volume", "prune"])
    assert docker_assets.command_has_forbidden_prune(["/usr/local/bin/docker", "system", "prune"])
    assert not docker_assets.command_has_forbidden_prune(["mocker", "compose", "up"])
    assert not docker_assets.command_has_forbidden_prune(["docker", "volume", "ls"])
    assert not docker_assets.command_has_forbidden_prune(["container", "volume", "prune"])
    assert not docker_assets.command_has_forbidden_prune(["container", "network", "prune"])
    assert not docker_assets.command_has_forbidden_prune(["container", "builder", "delete", "--force"])


def test_command_has_forbidden_prune_is_robust_to_repeated_tokens():
    assert docker_assets.command_has_forbidden_prune(["docker", "volume", "volume", "prune"])
    assert not docker_assets.command_has_forbidden_prune(["docker", "prune", "volume", "ls"])


def _spec_with_volume_keys(tmp_path: Path, keys: tuple[str, ...]) -> docker_assets.DockerPlatformSpec:
    compose = tmp_path / "docker-compose.yml"
    volume_lines = "\n".join(f"  {key}:" for key in keys)
    compose.write_text(f"services:\n  s:\n    image: alpine:3.19\nvolumes:\n{volume_lines}\n", encoding="utf-8")
    return docker_assets.DockerPlatformSpec(platform="scratch", compose_files=(compose,))


def test_compose_declared_volume_names_reads_real_platform_spec():
    spec = docker_assets.docker_platform_spec("postgresql")
    assert docker_assets.compose_declared_volume_names(spec) == ("postgresql18-data",)
    assert "benchbox-uat-manual-postgresql-postgresql18-data" in docker_assets.expected_mocker_volume_names(
        "benchbox-uat-manual-postgresql", spec
    )


def test_sweep_leaked_mocker_volumes_removes_exact_declared_names_only(tmp_path, monkeypatch):
    monkeypatch.setenv(docker_assets.CONTAINER_CLI_ENV_VAR, "mocker")
    monkeypatch.setattr(docker_assets, "_which_container_cli", lambda cli: f"/opt/homebrew/bin/{cli}")
    docker_assets.resolve_container_cli.cache_clear()
    spec = _spec_with_volume_keys(tmp_path, ("pgdata", "other"))
    calls: list[tuple[str, ...]] = []

    def fake_runner(argv, **kwargs):
        argv_tuple = tuple(argv)
        calls.append(argv_tuple)
        if argv_tuple == ("mocker", "volume", "ls"):
            return docker_assets.DockerCommandResult(
                argv_tuple,
                0,
                "DRIVER   VOLUME NAME\nlocal    p-pgdata\nlocal    p_other\nlocal    unrelated-project_data\n",
                "",
            )
        return docker_assets.DockerCommandResult(argv_tuple, 0, "", "")

    try:
        removed = docker_assets.sweep_leaked_mocker_volumes("p", spec, runner=fake_runner)
    finally:
        docker_assets.resolve_container_cli.cache_clear()

    assert set(removed) == {"p-pgdata", "p_other"}
    rm_calls = [c for c in calls if c[:3] == ("mocker", "volume", "rm")]
    assert ("mocker", "volume", "rm", "p-pgdata") in rm_calls
    assert ("mocker", "volume", "rm", "p_other") in rm_calls
    assert not any("unrelated-project_data" in c for c in rm_calls)


def test_sweep_leaked_mocker_volumes_never_touches_hyphen_sibling_project(tmp_path, monkeypatch):
    monkeypatch.setenv(docker_assets.CONTAINER_CLI_ENV_VAR, "mocker")
    monkeypatch.setattr(docker_assets, "_which_container_cli", lambda cli: f"/opt/homebrew/bin/{cli}")
    docker_assets.resolve_container_cli.cache_clear()
    spec = _spec_with_volume_keys(tmp_path, ("data",))
    calls: list[tuple[str, ...]] = []

    def fake_runner(argv, **kwargs):
        argv_tuple = tuple(argv)
        calls.append(argv_tuple)
        if argv_tuple == ("mocker", "volume", "ls"):
            return docker_assets.DockerCommandResult(
                argv_tuple,
                0,
                "DRIVER   VOLUME NAME\nlocal    p-data\nlocal    p-ha_data\nlocal    p-ha-data\n",
                "",
            )
        return docker_assets.DockerCommandResult(argv_tuple, 0, "", "")

    try:
        removed = docker_assets.sweep_leaked_mocker_volumes("p", spec, runner=fake_runner)
    finally:
        docker_assets.resolve_container_cli.cache_clear()

    assert removed == ("p-data",)
    rm_calls = [c for c in calls if c[:3] == ("mocker", "volume", "rm")]
    assert rm_calls == [("mocker", "volume", "rm", "p-data")]
    assert not any("p-ha" in " ".join(c) for c in rm_calls)


def test_sweep_leaked_mocker_volumes_is_noop_on_docker_engine(tmp_path, monkeypatch):
    monkeypatch.setenv(docker_assets.CONTAINER_CLI_ENV_VAR, "docker")
    monkeypatch.setattr(docker_assets, "_which_container_cli", lambda cli: f"/usr/bin/{cli}")
    docker_assets.resolve_container_cli.cache_clear()
    spec = _spec_with_volume_keys(tmp_path, ("pgdata",))
    calls: list[tuple[str, ...]] = []

    def fake_runner(argv, **kwargs):
        calls.append(tuple(argv))
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "")

    try:
        removed = docker_assets.sweep_leaked_mocker_volumes("benchbox-uat-smoke-postgresql", spec, runner=fake_runner)
    finally:
        docker_assets.resolve_container_cli.cache_clear()

    assert removed == ()
    assert calls == []


def test_sweep_leaked_mocker_volumes_is_noop_on_dry_run(tmp_path, monkeypatch):
    monkeypatch.setenv(docker_assets.CONTAINER_CLI_ENV_VAR, "mocker")
    monkeypatch.setattr(docker_assets, "_which_container_cli", lambda cli: f"/opt/homebrew/bin/{cli}")
    docker_assets.resolve_container_cli.cache_clear()
    spec = _spec_with_volume_keys(tmp_path, ("pgdata",))
    calls: list[tuple[str, ...]] = []

    def fake_runner(argv, **kwargs):
        calls.append(tuple(argv))
        return docker_assets.DockerCommandResult(tuple(argv), 0, "", "", dry_run=True)

    try:
        removed = docker_assets.sweep_leaked_mocker_volumes(
            "benchbox-uat-smoke-postgresql", spec, runner=fake_runner, dry_run=True
        )
    finally:
        docker_assets.resolve_container_cli.cache_clear()

    assert removed == ()
    assert calls == []


def test_sweep_leaked_mocker_volumes_swallows_individual_removal_failure(tmp_path, monkeypatch):
    monkeypatch.setenv(docker_assets.CONTAINER_CLI_ENV_VAR, "mocker")
    monkeypatch.setattr(docker_assets, "_which_container_cli", lambda cli: f"/opt/homebrew/bin/{cli}")
    docker_assets.resolve_container_cli.cache_clear()
    spec = _spec_with_volume_keys(tmp_path, ("pgdata", "cache"))

    def fake_runner(argv, **kwargs):
        argv_tuple = tuple(argv)
        if argv_tuple == ("mocker", "volume", "ls"):
            return docker_assets.DockerCommandResult(
                argv_tuple, 0, "local    benchbox-uat-demo-pgdata\nlocal    benchbox-uat-demo-cache\n", ""
            )
        if argv_tuple == ("mocker", "volume", "rm", "benchbox-uat-demo-pgdata"):
            return docker_assets.DockerCommandResult(argv_tuple, 1, "", "boom", error="stale reference")
        return docker_assets.DockerCommandResult(argv_tuple, 0, "", "")

    try:
        removed = docker_assets.sweep_leaked_mocker_volumes("benchbox-uat-demo", spec, runner=fake_runner)
    finally:
        docker_assets.resolve_container_cli.cache_clear()

    assert removed == ("benchbox-uat-demo-cache",)


@pytest.mark.parametrize(
    ("platform", "expected_service_names"),
    [
        ("databend", frozenset({"minio", "minio-setup", "databend"})),
        ("doris", frozenset({"doris"})),
        ("starrocks", frozenset({"starrocks"})),
    ],
)
def test_multi_service_platform_service_names_match_documented_guidance(platform, expected_service_names):
    spec = docker_assets.docker_platform_spec(platform)
    service_names: set[str] = set()
    for compose_file in spec.compose_files:
        data = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
        service_names.update(data.get("services", {}).keys())
    assert service_names == expected_service_names


def _spec_for_compose(tmp_path: Path, body: str) -> docker_assets.DockerPlatformSpec:
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(body, encoding="utf-8")
    return docker_assets.DockerPlatformSpec(platform="fake", compose_files=(compose_file,))


def test_compose_declared_memory_limits_reads_mem_limit(tmp_path: Path):
    spec = _spec_for_compose(tmp_path, "services:\n  db:\n    mem_limit: 4g\n")
    assert docker_assets.compose_declared_memory_limits(spec) == {"db": "4g"}


def test_compose_declared_memory_limits_resolves_required_environment_value(tmp_path: Path):
    spec = _spec_for_compose(tmp_path, "services:\n  db:\n    mem_limit: ${CLICKHOUSE_MEMORY_LIMIT}\n")
    with pytest.raises(docker_assets.DockerAssetError, match="CLICKHOUSE_MEMORY_LIMIT"):
        docker_assets.compose_declared_memory_limits(spec, env={})
    assert docker_assets.compose_declared_memory_limits(spec, env={"CLICKHOUSE_MEMORY_LIMIT": "4g"}) == {"db": "4g"}


def test_compose_declared_memory_limits_reads_deploy_resources_limits(tmp_path: Path):
    spec = _spec_for_compose(
        tmp_path,
        "services:\n  db:\n    deploy:\n      resources:\n        limits:\n          memory: 2G\n",
    )
    assert docker_assets.compose_declared_memory_limits(spec) == {"db": "2G"}


def test_compose_declared_memory_limits_empty_when_nothing_declared(tmp_path: Path):
    spec = _spec_for_compose(tmp_path, "services:\n  db:\n    image: postgres:16\n")
    assert docker_assets.compose_declared_memory_limits(spec) == {}


@pytest.mark.parametrize(
    "body",
    [
        "services:\n  db:\n    deploy:\n      resources:\n        limits:\n",
        "services:\n  db:\n    deploy:\n      resources:\n",
        "services:\n  db:\n    deploy:\n",
        "services:\n  db:\n    deploy:\n      resources:\n        limits: 2G\n",
        "services:\n  db:\n    deploy:\n      resources:\n        limits:\n          - memory\n",
    ],
)
def test_compose_declared_memory_limits_survives_degenerate_limits_blocks(tmp_path: Path, body: str):
    spec = _spec_for_compose(tmp_path, body)
    assert docker_assets.compose_declared_memory_limits(spec) == {}


def test_compose_declared_memory_limits_ignores_unparseable_file(tmp_path: Path):
    spec = _spec_for_compose(tmp_path, "services: [oops\n")
    assert docker_assets.compose_declared_memory_limits(spec) == {}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("4g", 4294967296),
        ("5.25g", 5637144576),
        ("4G", 4294967296),
        ("4gb", 4294967296),
        ("4GiB", 4294967296),
        ("512m", 512 * 1024**2),
        ("64k", 64 * 1024),
        ("100b", 100),
        ("1024", 1024),
    ],
)
def test_parse_memory_bytes_uses_docker_binary_units(value, expected):
    assert docker_assets.parse_memory_bytes(value) == expected


@pytest.mark.parametrize("requested", ["4g", "5.25g"])
def test_admission_accepts_runtime_limit_docker_reports_for_compose_request(requested):
    from tests.uat.clickhouse_memory import runtime_limit_matches_rung

    requested_bytes = docker_assets.parse_memory_bytes(requested)
    runtime = docker_assets.parse_runtime_memory_limit(
        '{{"Name":"c","MemUsage":"1MiB / {}"}}'.format({"4g": "4GiB", "5.25g": "5.25GiB"}[requested])
    )
    assert runtime_limit_matches_rung(runtime, requested_bytes / 1024**3, requested_bytes=requested_bytes)
    assert runtime_limit_matches_rung(5637144576, 5.25, requested_bytes=5637144576)
    assert not runtime_limit_matches_rung(4 * 1024**3 + 1, 4.0, requested_bytes=4 * 1024**3)


@pytest.mark.parametrize("value", ["", "0", "0g", "garbage", "-1g", "1.2.3g"])
def test_parse_memory_bytes_rejects_missing_malformed_or_zero_values(value):
    with pytest.raises(docker_assets.DockerAssetError, match="invalid memory limit"):
        docker_assets.parse_memory_bytes(value)


def test_resolve_clickhouse_memory_limit_has_no_implicit_one_gib_fallback(monkeypatch):
    monkeypatch.delenv(docker_assets.CLICKHOUSE_MEMORY_LIMIT_ENV_VAR, raising=False)
    with pytest.raises(docker_assets.DockerAssetError, match="required"):
        docker_assets.resolve_clickhouse_memory_limit()


def test_resolve_clickhouse_memory_limit_prefers_environment_override(monkeypatch):
    monkeypatch.setenv(docker_assets.CLICKHOUSE_MEMORY_LIMIT_ENV_VAR, "8g")
    value, size = docker_assets.resolve_clickhouse_memory_limit("4g")
    assert value == "8g"
    assert size == 8 * 1024**3


def test_resolve_clickhouse_memory_limit_does_not_replace_empty_environment_with_config(monkeypatch):
    monkeypatch.setenv(docker_assets.CLICKHOUSE_MEMORY_LIMIT_ENV_VAR, "")
    with pytest.raises(docker_assets.DockerAssetError, match="required"):
        docker_assets.resolve_clickhouse_memory_limit("4g")


def test_resolve_starrocks_memory_limit_validates_config_and_environment():
    assert managed_runtime.resolve_starrocks_memory_limit("4g", env={}) == ("4g", 4 * 1024**3)
    assert managed_runtime.resolve_starrocks_memory_limit("4g", env={"STARROCKS_MEMORY_LIMIT": "6g"}) == (
        "6g",
        6 * 1024**3,
    )
    with pytest.raises(docker_assets.DockerAssetError, match="non-empty"):
        managed_runtime.resolve_starrocks_memory_limit("4g", env={"STARROCKS_MEMORY_LIMIT": ""})


def test_starrocks_readiness_command_requires_alive_frontend_and_backend():
    spec = docker_assets.docker_platform_spec("starrocks")
    argv = managed_runtime._readiness_command(spec, "benchbox-uat-starrocks")
    assert argv is not None
    assert argv[-4] == "starrocks"
    assert "SHOW FRONTENDS\\G" in argv[-1]
    assert "SHOW BACKENDS\\G" in argv[-1]
    assert "Alive: true" in argv[-1]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"Name":"clickhouse","MemUsage":"512MB / 4GB"}', 4_000_000_000),
        ("clickhouse 512MB / 4GB 12%", 4_000_000_000),
        ("CONTAINER ID NAME CPU % MEM USAGE / LIMIT MEM %\nabc clickhouse 0% 512MiB / 4GiB 1%", 4 * 1024**3),
    ],
)
def test_parse_runtime_memory_limit_supports_docker_and_mocker_shapes(text, expected):
    assert docker_assets.parse_runtime_memory_limit(text) == expected


def test_compose_stats_command_targets_first_service():
    spec = docker_assets.docker_platform_spec("clickhouse-server")
    argv = docker_assets.compose_stats_command(spec, "benchbox-uat-clickhouse-server")
    assert argv[-1] == "benchbox-uat-clickhouse-server-clickhouse-1"
    assert argv[-3:] == ["--format", "{{json .}}", "benchbox-uat-clickhouse-server-clickhouse-1"]


def test_compose_ps_command_passes_all_so_stopped_containers_are_listed():
    spec = docker_assets.docker_platform_spec("postgresql")
    argv = docker_assets.compose_ps_command(spec, "benchbox-uat-smoke-postgresql")
    assert argv[-2:] == ["ps", "-a"]
    assert "-p" in argv and "benchbox-uat-smoke-postgresql" in argv


@pytest.mark.parametrize(
    ("status", "why"),
    [
        ("Exited (137) 5 seconds ago", "compose v2 dead container -- the 2026-08-04 CedarDB state"),
        ("Exit 1", "compose v1 spelling of the same state"),
        ("Restarting (1) 3 seconds ago", "crash-looping"),
        ("Created", "never started"),
        ("Dead", "engine could not clean it up"),
        ("Paused", "SIGSTOPped; the port accepts nothing"),
        ("Up 30 seconds (unhealthy)", "the engine's own healthcheck says no"),
    ],
)
def test_compose_ps_unhealthy_services_flags_every_not_ready_state(status: str, why: str):
    stdout = f"NAME      IMAGE     COMMAND   STATUS\nsvc       img       cmd       {status}\n"
    assert docker_assets.compose_ps_unhealthy_services(stdout) == ("svc",), why


@pytest.mark.parametrize("status", ["Up 5 seconds", "Up 2 hours (healthy)", "Up About a minute"])
def test_compose_ps_unhealthy_services_passes_a_serving_container(status: str):
    stdout = f"NAME      IMAGE     COMMAND   STATUS\nsvc       img       cmd       {status}\n"
    assert docker_assets.compose_ps_unhealthy_services(stdout) == ()


def test_compose_ps_unhealthy_services_ignores_state_words_in_the_name_column():
    stdout = "NAME               IMAGE     COMMAND   STATUS\nbenchbox-Created   img       cmd       Up 5 seconds\n"
    assert docker_assets.compose_ps_unhealthy_services(stdout) == ()


def test_compose_ps_unhealthy_services_reports_every_bad_row():
    stdout = (
        "NAME      IMAGE     COMMAND   STATUS\n"
        "alpha     img       cmd       Up 5 seconds\n"
        "beta      img       cmd       Exited (1) 2 seconds ago\n"
        "gamma     img       cmd       Restarting (1) 1 second ago\n"
    )
    assert docker_assets.compose_ps_unhealthy_services(stdout) == ("beta", "gamma")


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "NAME      IMAGE     COMMAND   STATUS\n",
        "Name   Command   State   Ports\n--------------------------------\n",
        "\n\n",
    ],
)
def test_compose_ps_service_rows_is_empty_for_no_service_rows(stdout: str):
    assert docker_assets.compose_ps_service_rows(stdout) == ()


def test_compose_ps_service_rows_keeps_real_rows():
    stdout = "NAME      IMAGE     COMMAND   STATUS\nsvc       img       cmd       Up 5 seconds\n"
    assert len(docker_assets.compose_ps_service_rows(stdout)) == 1


def test_velox_connect_tmpfs_permits_native_library_loading():
    compose = yaml.safe_load((docker_assets.REPO_ROOT / "docker/velox/docker-compose.yml").read_text(encoding="utf-8"))
    tmpfs_mounts = compose["services"]["velox-connect"]["tmpfs"]

    assert any(mount.startswith("/tmp:") and "exec" in mount.split(":", 1)[1].split(",") for mount in tmpfs_mounts)
