from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

from tests.uat import config, matrix, preflight_budget
from tests.uat.phases import preflight

pytestmark = pytest.mark.medium


def test_local_platforms_check_attempts_automated_platform_then_succeeds(tmp_path: Path, monkeypatch):
    calls: list[str] = []
    reachable_calls = 0

    def fake_reachable(platform: str) -> bool:
        nonlocal reachable_calls
        assert platform == "postgresql"
        reachable_calls += 1
        return reachable_calls > 1

    def fake_bring_up(platform: str) -> int:
        calls.append(platform)
        return 0

    monkeypatch.setattr(preflight, "free_space_gib", lambda path: 100.0)
    monkeypatch.setattr(preflight, "docker_reachable", lambda: True)
    monkeypatch.setattr(preflight, "host_load_1m", lambda: 0.5)

    result = preflight.run_preflight(
        free_space_path=tmp_path,
        local_platforms_check=True,
        requested_platforms=("postgresql",),
        bring_up_runner=fake_bring_up,
        reachability_checker=fake_reachable,
    )

    assert result.aborted is False
    assert calls == ["postgresql"]
    assert result.local_platforms_checked == ("postgresql",)
    assert result.local_platforms_attempted == ("postgresql",)
    assert any("recovered" in warning for warning in result.warnings)


def test_local_platforms_check_aborts_non_automated_platform(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(preflight, "free_space_gib", lambda path: 100.0)
    monkeypatch.setattr(preflight, "docker_reachable", lambda: True)
    monkeypatch.setattr(preflight, "host_load_1m", lambda: 0.5)

    result = preflight.run_preflight(
        free_space_path=tmp_path,
        local_platforms_check=True,
        requested_platforms=("spark",),
        reachability_checker=lambda platform: False,
    )

    assert result.aborted is True
    assert "spark" in (result.abort_reason or "")
    assert "no automated UAT bring-up" in (result.abort_reason or "")


def test_local_platforms_check_default_checker_probes_lakesail(tmp_path: Path, monkeypatch):
    probe_calls: list[tuple[str, int]] = []

    def fake_tcp_probe(host: str, port: int, timeout_s: float = 2.0) -> bool:
        probe_calls.append((host, port))
        return False

    monkeypatch.setattr(preflight, "free_space_gib", lambda path: 100.0)
    monkeypatch.setattr(preflight, "docker_reachable", lambda: True)
    monkeypatch.setattr(preflight, "host_load_1m", lambda: 0.5)
    monkeypatch.setattr(matrix, "tcp_probe", fake_tcp_probe)

    result = preflight.run_preflight(
        free_space_path=tmp_path,
        local_platforms_check=True,
        requested_platforms=("lakesail",),
        bring_up_runner=lambda platform: 0,
    )

    assert result.aborted is True
    assert probe_calls == [("localhost", 50051), ("localhost", 50051)]
    assert result.local_platforms_checked == ("lakesail",)
    assert result.local_platforms_attempted == ("lakesail",)
    assert "lakesail" in (result.abort_reason or "")
    assert "remains unreachable" in (result.abort_reason or "")


def test_preflight_config_accepts_local_platforms_check():
    cfg = config.validate_config({"name": "smoke", "preflight": {"local_platforms_check": True}})
    assert cfg.preflight.local_platforms_check is True


def test_preflight_disk_budget_table_error_hard_fails_preflight(tmp_path: Path, monkeypatch):
    table = tmp_path / "disk_budget.tsv"
    table.write_text(
        "platform\tbenchmark\tpeak_datagen_gib\tpeak_database_gib\ttransient_growth_gib\nduckdb\ttpch\t1.0\t2.0\t0.5\n",
        encoding="utf-8",
    )
    cfg = config.validate_config(
        {
            "name": "budget-smoke",
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )

    monkeypatch.setattr(preflight, "free_space_gib", lambda path: 100.0)
    monkeypatch.setattr(preflight, "docker_reachable", lambda: True)
    monkeypatch.setattr(preflight, "host_load_1m", lambda: 0.5)
    monkeypatch.setattr(preflight_budget, "DEFAULT_TABLE_PATH", table)

    result = preflight.run_preflight(free_space_path=tmp_path, disk_budget_config=cfg)

    assert result.aborted is True
    assert result.abort_reason is not None
    assert result.abort_reason.startswith("disk budget estimator failed: ValueError: disk budget table ")
    assert "missing columns" in result.abort_reason
    assert "scale_factor" in result.abort_reason
    assert result.disk_budget_summary is None


def test_preflight_disk_budget_truncated_row_hard_fails_preflight(tmp_path: Path, monkeypatch):
    table = tmp_path / "disk_budget.tsv"
    table.write_text(
        "platform\tbenchmark\tscale_factor\tpeak_datagen_gib\tpeak_database_gib\ttransient_growth_gib\n"
        "duckdb\ttpch\t0.01\t1.0\n",
        encoding="utf-8",
    )
    cfg = config.validate_config(
        {
            "name": "budget-smoke",
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )

    monkeypatch.setattr(preflight, "free_space_gib", lambda path: 100.0)
    monkeypatch.setattr(preflight, "docker_reachable", lambda: True)
    monkeypatch.setattr(preflight, "host_load_1m", lambda: 0.5)
    monkeypatch.setattr(preflight_budget, "DEFAULT_TABLE_PATH", table)

    result = preflight.run_preflight(free_space_path=tmp_path, disk_budget_config=cfg)

    assert result.aborted is True
    assert result.abort_reason is not None
    assert result.abort_reason.startswith("disk budget estimator failed: TypeError: ")
    assert result.disk_budget_summary is None


def test_preflight_disk_budget_unexpected_error_propagates(tmp_path: Path, monkeypatch):
    cfg = config.validate_config(
        {
            "name": "budget-smoke",
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )

    monkeypatch.setattr(preflight, "free_space_gib", lambda path: 100.0)
    monkeypatch.setattr(preflight, "docker_reachable", lambda: True)
    monkeypatch.setattr(preflight, "host_load_1m", lambda: 0.5)

    def _boom(*args, **kwargs):
        raise RuntimeError("unexpected estimator bug")

    monkeypatch.setattr(preflight, "estimate_disk_budget_summary_and_gate", _boom)

    with pytest.raises(RuntimeError, match="unexpected estimator bug"):
        preflight.run_preflight(free_space_path=tmp_path, disk_budget_config=cfg)


def test_preflight_reports_all_required_disk_roots(tmp_path: Path, monkeypatch):
    scratch_tmp = tmp_path / "scratch-tmp"
    docker_root = tmp_path / "docker-root"
    docker_root.mkdir()

    monkeypatch.setattr(preflight, "free_space_gib", lambda path: 100.0)
    monkeypatch.setattr(preflight, "docker_reachable", lambda: True)
    monkeypatch.setattr(preflight, "docker_data_root", lambda: docker_root)
    monkeypatch.setattr(preflight, "host_load_1m", lambda: 0.5)
    monkeypatch.setattr(preflight.tempfile, "gettempdir", lambda: str(scratch_tmp))

    result = preflight.run_preflight(
        free_space_path=tmp_path / "runs",
        benchmark_runs_dir=tmp_path / "runs",
        docker_manage_platforms=True,
    )

    assert result.aborted is False
    assert any("tmp" in line and str(scratch_tmp) in line for line in result.free_space_report)
    assert any("output" in line and str(tmp_path / "runs") in line for line in result.free_space_report)
    assert any(
        "benchmark-data" in line and str(tmp_path / "runs" / "datagen") in line for line in result.free_space_report
    )
    assert any("docker-data" in line and str(docker_root) in line for line in result.free_space_report)
    assert not any("docker-data" in line and str(tmp_path / "runs") in line for line in result.free_space_report)


def test_collect_disk_roots_omits_docker_data_without_engine_root(tmp_path: Path):
    roots = preflight.collect_disk_roots(
        free_space_path=tmp_path / "runs",
        benchmark_runs_dir=tmp_path / "runs",
        docker_manage_platforms=True,
        docker_data_root=None,
    )

    assert ("docker-data", tmp_path / "runs") not in roots
    assert not any(label == "docker-data" for label, _path in roots)


def test_docker_data_root_reads_host_visible_engine_root(tmp_path: Path, monkeypatch):
    docker_root = tmp_path / "docker-root"
    docker_root.mkdir()

    def fake_run(argv, **_kwargs):
        assert argv == ["docker", "info", "--format", "{{.DockerRootDir}}"]
        return subprocess.CompletedProcess(argv, 0, stdout=f"{docker_root}\n", stderr="")

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)

    assert preflight.docker_data_root() == docker_root


def test_docker_data_root_omits_non_host_visible_engine_root(tmp_path: Path, monkeypatch):
    missing_root = tmp_path / "missing-docker-root"

    def fake_run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=f"{missing_root}\n", stderr="")

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)

    assert preflight.docker_data_root() is None


def test_preflight_disk_headroom_gate_aborts_short_root(tmp_path: Path, monkeypatch):
    scratch_tmp = tmp_path / "scratch-tmp"
    table = tmp_path / "disk_budget.tsv"
    table.write_text(
        "platform\tbenchmark\tscale_factor\tpeak_datagen_gib\tpeak_database_gib\ttransient_growth_gib\n"
        "duckdb\ttpch\t0.01\t6.0\t3.0\t1.0\n",
        encoding="utf-8",
    )
    cfg = config.validate_config(
        {
            "name": "budget-smoke",
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
        }
    )

    def fake_free_space(path):
        return 8.0 if Path(path) == scratch_tmp else 20.0

    monkeypatch.setattr(preflight, "free_space_gib", fake_free_space)
    monkeypatch.setattr(preflight, "docker_reachable", lambda: True)
    monkeypatch.setattr(preflight, "host_load_1m", lambda: 0.5)
    monkeypatch.setattr(preflight_budget, "DEFAULT_TABLE_PATH", table)
    monkeypatch.setattr(preflight.tempfile, "gettempdir", lambda: str(scratch_tmp))

    result = preflight.run_preflight(
        free_space_path=tmp_path / "runs",
        benchmark_runs_dir=tmp_path / "runs",
        disk_budget_config=cfg,
    )

    assert result.aborted is True
    assert "disk headroom gate failed" in (result.abort_reason or "")
    assert f"tmp {scratch_tmp}: 8.0 GiB free < 10.0 GiB required" in (result.abort_reason or "")
    assert "computed shortfall 2.0 GiB" in (result.abort_reason or "")
    assert "database concurrent=3.00 GiB" in (result.abort_reason or "")
    assert "chunked_max=3.00 GiB" in (result.abort_reason or "")
    assert "failing now rather than exhausting disk mid-sweep" in (result.abort_reason or "")


def test_preflight_disk_headroom_gate_respects_zero_override(tmp_path: Path, monkeypatch):
    table = tmp_path / "disk_budget.tsv"
    table.write_text(
        "platform\tbenchmark\tscale_factor\tpeak_datagen_gib\tpeak_database_gib\ttransient_growth_gib\n"
        "duckdb\ttpch\t0.01\t6.0\t3.0\t1.0\n",
        encoding="utf-8",
    )
    cfg = config.validate_config(
        {
            "name": "budget-smoke",
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "preflight": {"free_space_min_gib": 0},
        }
    )

    monkeypatch.setattr(preflight, "free_space_gib", lambda path: 1.0)
    monkeypatch.setattr(preflight, "docker_reachable", lambda: True)
    monkeypatch.setattr(preflight, "host_load_1m", lambda: 0.5)
    monkeypatch.setattr(preflight_budget, "DEFAULT_TABLE_PATH", table)

    result = preflight.run_preflight(
        free_space_path=tmp_path / "runs",
        benchmark_runs_dir=tmp_path / "runs",
        free_space_min_gib=0,
        disk_budget_config=cfg,
    )

    assert result.aborted is False
    assert result.abort_reason is None
    assert all("(required 0.00 GiB)" in line for line in result.free_space_report)


def test_preflight_disk_budget_verdict_states_gate_disabled_when_floor_is_zero(tmp_path: Path, monkeypatch):
    table = tmp_path / "disk_budget.tsv"
    table.write_text(
        "platform\tbenchmark\tscale_factor\tpeak_datagen_gib\tpeak_database_gib\ttransient_growth_gib\n"
        "duckdb\ttpch\t0.01\t20.0\t0.0\t2.0\n",
        encoding="utf-8",
    )
    cfg = config.validate_config(
        {
            "name": "zero-floor-smoke",
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "preflight": {"free_space_min_gib": 0},
        }
    )

    monkeypatch.setattr(preflight, "free_space_gib", lambda path: 1.0)
    monkeypatch.setattr(preflight, "docker_reachable", lambda: True)
    monkeypatch.setattr(preflight, "host_load_1m", lambda: 0.5)
    monkeypatch.setattr(preflight_budget, "DEFAULT_TABLE_PATH", table)

    result = preflight.run_preflight(
        free_space_path=tmp_path / "runs",
        benchmark_runs_dir=tmp_path / "runs",
        free_space_min_gib=0,
        disk_budget_config=cfg,
    )

    assert result.aborted is False
    assert result.abort_reason is None
    summary = result.disk_budget_summary or ""
    verdict = next(line for line in summary.splitlines() if line.startswith("Disk budget verdict:"))
    assert "disabled" in verdict
    assert "22.00 GiB" not in verdict
    assert "fits" not in verdict
    assert "no shortfall detected" not in verdict
    assert all("(required 0.00 GiB)" in line for line in result.free_space_report)


def test_requested_platforms_from_config_matches_uat_defaults():
    assert preflight.requested_platforms_from_config(
        config.validate_config({"name": "smoke", "platforms": {"include": ["postgresql"]}})
    ) == ("postgresql",)
    assert "duckdb" in preflight.requested_platforms_from_config(config.validate_config({"name": "smoke"}))


def test_requested_platforms_from_config_preserves_explicit_empty_include():
    cfg = config.validate_config({"name": "smoke", "platforms": {"include": []}})

    assert preflight.requested_platforms_from_config(cfg) == ()


def test_uat_bring_up_unknown_platform_returns_clear_error(capsys):
    module = _load_bring_up_module()
    rc = module.main(["--platform", "does-not-exist"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "unknown platform" in captured.err


def test_make_platform_filter_does_not_trip_bring_up_validation():
    completed = subprocess.run(
        ["make", "-n", "uat-stress", "PLATFORM=duckdb", "CONFIG=tests/uat/configs/stress-default.yaml"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "unknown platform" not in completed.stderr


@pytest.mark.parametrize("platform", ["lakesail", "velox"])
def test_uat_bring_up_path_mirrored_platforms_pass_benchmark_runs_dir_env(
    platform: str,
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    module = _load_bring_up_module()
    captured: dict[str, dict[str, str]] = {}

    def fake_run_docker_command(argv, **kwargs):
        captured["env"] = kwargs.get("env") or {}
        return module.docker_assets.DockerCommandResult(
            argv=tuple(argv), returncode=0, stdout="", stderr="", dry_run=True
        )

    monkeypatch.setattr(module.docker_assets, "run_docker_command", fake_run_docker_command)

    rc = module.main(["--platform", platform, "--benchmark-runs-dir", str(tmp_path), "--dry-run"])

    assert rc == 0
    assert captured["env"] == {"BENCHBOX_DATA_DIR": str(tmp_path)}
    assert "UAT bring-up OK" in capsys.readouterr().out


def test_preflight_automated_set_matches_script_automated_set():
    bring_up = _load_bring_up_module()

    assert bring_up.automated_platforms() == preflight.automated_local_platforms()
    assert bring_up.automated_platforms() == preflight.AUTOMATED_LOCAL_PLATFORMS


def _load_bring_up_module():
    path = Path("scripts/uat-bring-up/uat_bring_up.py").resolve()
    spec = importlib.util.spec_from_file_location("uat_bring_up", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _partial_coverage_table(tmp_path: Path) -> Path:
    table = tmp_path / "disk_budget.tsv"
    table.write_text(
        "platform\tbenchmark\tscale_factor\tpeak_datagen_gib\tpeak_database_gib\t"
        "peak_database_gib_status\ttransient_growth_gib\n"
        "duckdb\ttpch\t0.01\t1.0\t0.0\tunmeasured\t0.5\n",
        encoding="utf-8",
    )
    return table


def _measured_table(tmp_path: Path) -> Path:
    table = tmp_path / "disk_budget.tsv"
    table.write_text(
        "platform\tbenchmark\tscale_factor\tpeak_datagen_gib\tpeak_database_gib\t"
        "peak_database_gib_status\ttransient_growth_gib\n"
        "duckdb\ttpch\t0.01\t1.0\t2.0\tmeasured\t0.5\n",
        encoding="utf-8",
    )
    return table


def _duckdb_tpch_config(**overrides):
    payload = {
        "name": "coverage-smoke",
        "platforms": {"include": ["duckdb"]},
        "benchmarks": {"include": ["tpch"]},
        "scales": {"rungs": [0.01]},
    }
    payload.update(overrides)
    return config.validate_config(payload)


def test_preflight_enforces_configured_floor_when_budget_is_below_it(tmp_path: Path, monkeypatch):
    table = _partial_coverage_table(tmp_path)
    cfg = _duckdb_tpch_config()

    monkeypatch.setattr(preflight, "free_space_gib", lambda path: 0.2)
    monkeypatch.setattr(preflight, "docker_reachable", lambda: True)
    monkeypatch.setattr(preflight, "host_load_1m", lambda: 0.5)
    monkeypatch.setattr(preflight_budget, "DEFAULT_TABLE_PATH", table)

    result = preflight.run_preflight(
        free_space_path=tmp_path / "runs",
        benchmark_runs_dir=tmp_path / "runs",
        free_space_min_gib=5.0,
        disk_budget_config=cfg,
    )

    assert result.aborted is True
    assert result.abort_kind == "disk_floor"
    assert "0.2 GiB free < 5.0 GiB required" in (result.abort_reason or "")


def test_preflight_plain_shortfall_reports_a_plain_message(tmp_path: Path, monkeypatch):
    table = _partial_coverage_table(tmp_path)
    cfg = _duckdb_tpch_config()

    monkeypatch.setattr(preflight, "free_space_gib", lambda path: 0.2)
    monkeypatch.setattr(preflight, "docker_reachable", lambda: True)
    monkeypatch.setattr(preflight, "host_load_1m", lambda: 0.5)
    monkeypatch.setattr(preflight_budget, "DEFAULT_TABLE_PATH", table)

    result = preflight.run_preflight(
        free_space_path=tmp_path / "runs",
        benchmark_runs_dir=tmp_path / "runs",
        free_space_min_gib=5.0,
        disk_budget_config=cfg,
    )

    reason = result.abort_reason or ""
    assert reason.startswith("disk headroom gate failed: ")
    assert "chunking" not in reason
    assert "basis" not in reason


def test_preflight_passing_verdict_discloses_partial_coverage(tmp_path: Path, monkeypatch):
    table = _partial_coverage_table(tmp_path)
    cfg = _duckdb_tpch_config()

    monkeypatch.setattr(preflight, "free_space_gib", lambda path: 500.0)
    monkeypatch.setattr(preflight, "docker_reachable", lambda: True)
    monkeypatch.setattr(preflight, "host_load_1m", lambda: 0.5)
    monkeypatch.setattr(preflight_budget, "DEFAULT_TABLE_PATH", table)

    result = preflight.run_preflight(
        free_space_path=tmp_path / "runs",
        benchmark_runs_dir=tmp_path / "runs",
        disk_budget_config=cfg,
    )

    assert result.aborted is False
    summary = result.disk_budget_summary or ""
    assert "Disk budget coverage: PARTIAL" in summary
    assert "LOWER BOUND" in summary
    verdict = next(line for line in summary.splitlines() if line.startswith("Disk budget verdict:"))
    assert "no shortfall detected" in verdict
    assert "fits" not in verdict
    assert "not a certification that the sweep fits" in summary
    assert any("LOWER BOUND" in warning for warning in result.warnings)
    assert all("(required >= " in line for line in result.free_space_report)


def test_preflight_fully_measured_coverage_reports_a_measured_verdict(tmp_path: Path, monkeypatch):
    table = _measured_table(tmp_path)
    cfg = _duckdb_tpch_config()

    monkeypatch.setattr(preflight, "free_space_gib", lambda path: 500.0)
    monkeypatch.setattr(preflight, "docker_reachable", lambda: True)
    monkeypatch.setattr(preflight, "host_load_1m", lambda: 0.5)
    monkeypatch.setattr(preflight_budget, "DEFAULT_TABLE_PATH", table)

    result = preflight.run_preflight(
        free_space_path=tmp_path / "runs",
        benchmark_runs_dir=tmp_path / "runs",
        disk_budget_config=cfg,
    )

    assert result.aborted is False
    summary = result.disk_budget_summary or ""
    assert "Disk budget coverage: COMPLETE" in summary
    assert "fits every required root" in summary
    assert not any("LOWER BOUND" in warning for warning in result.warnings)
    assert all("(required >= " not in line for line in result.free_space_report)


def test_preflight_warns_when_container_data_root_is_not_host_visible(tmp_path: Path, monkeypatch):
    table = _measured_table(tmp_path)
    cfg = _duckdb_tpch_config()

    monkeypatch.setattr(preflight, "free_space_gib", lambda path: 500.0)
    monkeypatch.setattr(preflight, "docker_reachable", lambda: True)
    monkeypatch.setattr(preflight, "host_load_1m", lambda: 0.5)
    monkeypatch.setattr(preflight, "docker_data_root", lambda: None)
    monkeypatch.setattr(preflight_budget, "DEFAULT_TABLE_PATH", table)

    result = preflight.run_preflight(
        free_space_path=tmp_path / "runs",
        benchmark_runs_dir=tmp_path / "runs",
        disk_budget_config=cfg,
        docker_manage_platforms=True,
    )

    assert any("container data root is not host-visible" in warning for warning in result.warnings)

    monkeypatch.setattr(preflight, "docker_data_root", lambda: tmp_path / "docker-root")
    visible = preflight.run_preflight(
        free_space_path=tmp_path / "runs",
        benchmark_runs_dir=tmp_path / "runs",
        disk_budget_config=cfg,
        docker_manage_platforms=True,
    )
    assert not any("container data root" in warning for warning in visible.warnings)
