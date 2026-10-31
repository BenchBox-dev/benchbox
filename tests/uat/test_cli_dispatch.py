from __future__ import annotations

import json
import subprocess
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest
import yaml

from tests.uat import _cli
from tests.uat.config import validate_config
from tests.utilities.paths import REPO_ROOT

pytestmark = pytest.mark.medium


def _write_uat_config(path: Path, payload: dict) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_write_uat_config_round_trips_windows_paths(tmp_path: Path) -> None:
    config_path = tmp_path / "uat.yaml"
    windows_path = str(PureWindowsPath("C:/Users/runneradmin/uat/{name}-{date}"))

    _write_uat_config(config_path, {"name": "windows-path", "output": {"logs_dir_template": windows_path}})

    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["output"]["logs_dir_template"] == windows_path


def test_main_routes_subcommand(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_execute(args):
        calls.append((args.cmd, args.config))
        return 0

    monkeypatch.setattr(_cli, "_handle_execute", fake_execute)
    rc = _cli.main(["execute", "--config", "x.yaml"])
    assert rc == 0
    assert calls == [("execute", "x.yaml")]


def test_main_no_args_uses_cell_parser(capsys):
    rc = _cli.main([])
    assert rc == 2
    err = capsys.readouterr().err
    assert "cell" in err
    assert "--platform" in err


def test_main_bare_flags_route_to_cell(monkeypatch):
    calls: list[tuple[str, str, float]] = []

    def fake_cell(args):
        calls.append((args.platform, args.benchmark, args.scale))
        return 0

    monkeypatch.setattr(_cli, "_handle_cell", fake_cell)
    rc = _cli.main(["--platform", "duckdb", "--benchmark", "tpch", "--scale", "0.01"])
    assert rc == 0
    assert calls == [("duckdb", "tpch", 0.01)]


@pytest.mark.parametrize(("platform", "expected_local_managed"), [("pg-duckdb", True), ("duckdb", False)])
def test_cell_main_scopes_local_managed_platform_to_uat_docker_platforms(
    platform,
    expected_local_managed,
    monkeypatch,
    capsys,
):
    captured = {}

    def fake_run_cell(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            platform=kwargs["platform"],
            benchmark=kwargs["benchmark"],
            scale=kwargs["scale"],
            status="passed",
            exit_code=0,
            elapsed_s=0.1,
            log_path=Path("cell.log"),
            result_path=None,
            submit_terminal_state=None,
        )

    monkeypatch.setattr("tests.uat.runner.run_cell", fake_run_cell)

    rc = _cli.main(["cell", "--platform", platform, "--benchmark", "tpch", "--scale", "0.01"])

    assert rc == 0
    assert captured["local_managed_platform"] is expected_local_managed
    assert json.loads(capsys.readouterr().out)["platform"] == platform


def test_main_unknown_subcommand_returns_2(capsys):
    rc = _cli.main(["nonsense"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "invalid choice" in err


def test_main_help_returns_0(capsys):
    rc = _cli.main(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "cell" in out
    assert "preflight" in out
    assert "replay-classify" in out


def test_main_preflight_returns_2_when_preflight_aborts(tmp_path, monkeypatch, capsys):
    config = validate_config({"name": "preflight-smoke", "preflight": {"free_space_min_gib": 1000.0}})

    def fake_run_preflight(**kwargs):
        return SimpleNamespace(
            disk_budget_summary="budget: high water mark 1 GiB",
            free_space_report=("Free space: tmp 10.00 GiB (required 5.00 GiB) /tmp",),
            warnings=("disk nearly full",),
            aborted=True,
            abort_reason="free space 0.1 GiB < cutoff 1000.0 GiB",
        )

    monkeypatch.setattr("tests.uat.config.load_config", lambda path: config)
    monkeypatch.setattr("tests.uat.phases.execute.default_benchmark_runs_dir", lambda cfg: tmp_path / "runs")
    monkeypatch.setattr("tests.uat.phases.preflight.run_preflight", fake_run_preflight)

    rc = _cli.main(["preflight", "--config", "uat.yaml"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "budget: high water mark 1 GiB" in captured.out
    assert "Free space: tmp 10.00 GiB (required 5.00 GiB) /tmp" in captured.out
    assert "[preflight warn] disk nearly full" in captured.err
    assert "[preflight] ABORT: free space 0.1 GiB < cutoff 1000.0 GiB" in captured.err


def test_execute_and_sweep_use_same_preflight_kwargs(tmp_path, monkeypatch):
    config_path = tmp_path / "uat.yaml"
    _write_uat_config(
        config_path,
        {
            "name": "preflight-kwargs",
            "phases": ["preflight", "execute"],
            "platforms": {"include": ["postgresql"]},
            "preflight": {
                "free_space_min_gib": 12.5,
                "free_space_path": str(tmp_path / "free-space"),
                "docker_required": True,
                "noisy_neighbor_warn_load": 3.5,
                "local_platforms_check": True,
            },
            "output": {
                "benchmark_runs_dir_template": str(tmp_path / "runs"),
                "logs_dir_template": str(tmp_path / "logs" / "{name}-{date}"),
            },
        },
    )
    calls: list[dict[str, object]] = []

    def fake_run_preflight(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            disk_budget_summary=None,
            free_space_report=(),
            warnings=(),
            aborted=True,
            abort_reason="stop after capturing preflight kwargs",
            exit_code=lambda: 2,
        )

    monkeypatch.setattr("tests.uat.phases.preflight.run_preflight", fake_run_preflight)

    execute_rc = _cli.main(["execute", "--config", str(config_path)])
    sweep_rc = _cli.main(["sweep", "--config", str(config_path)])

    assert execute_rc == 2
    assert sweep_rc == 2
    assert len(calls) == 2
    execute_kwargs = {key: value for key, value in calls[0].items() if key != "disk_budget_config"}
    sweep_kwargs = {key: value for key, value in calls[1].items() if key != "disk_budget_config"}
    assert execute_kwargs == sweep_kwargs
    assert calls[0]["disk_budget_config"] == calls[1]["disk_budget_config"]


def test_subcommands_table_covers_all_make_targets():
    expected = {
        "cell",
        "docker-cleanup",
        "execute",
        "gate-check",
        "validate",
        "package",
        "report",
        "sweep",
        "stress",
        "verify-tuning-matrix",
    }
    assert set(_cli.MAKE_TARGET_SUBCOMMANDS) == expected


def test_sweep_main_forwards_dry_run_override(monkeypatch, capsys):
    calls: list[tuple[Path, bool | None, list[str] | None]] = []

    class StubResult:
        name = "stub"
        log_dir = Path("logs")
        aborted_phase = None
        abort_reason = None
        phase_exit_codes = {"execute": 0}
        plan = None

        def exit_code(self):
            return 0

    def fake_run_sweep_from_path(config_path, *, dry_run_override=None, stress_overrides=None, platform_shard=None):
        assert stress_overrides is None
        calls.append((config_path, dry_run_override, platform_shard))
        return StubResult()

    monkeypatch.setattr("tests.uat.orchestrator.run_sweep_from_path", fake_run_sweep_from_path)
    rc = _cli.main(
        ["sweep", "--config", "tests/uat/configs/uat-2026-05-02.yaml", "--dry-run", "--platforms", "duckdb,sqlite"]
    )

    assert rc == 0
    assert calls == [(Path("tests/uat/configs/uat-2026-05-02.yaml"), True, ["duckdb", "sqlite"])]
    assert '"phase_exit_codes"' in capsys.readouterr().out


def test_stress_main_forwards_platform_benchmark_scale_as_stress_overrides(monkeypatch, capsys):
    calls: list[tuple[Path, dict]] = []

    class StubResult:
        name = "stub"
        log_dir = Path("logs")
        aborted_phase = None
        abort_reason = None
        phase_exit_codes = {"execute": 0}

        def exit_code(self):
            return 0

    def fake_run_sweep_from_path(config_path, *, dry_run_override=None, stress_overrides=None):
        assert dry_run_override is None
        calls.append((config_path, stress_overrides))
        return StubResult()

    monkeypatch.setattr("tests.uat.orchestrator.run_sweep_from_path", fake_run_sweep_from_path)
    rc = _cli.main(["stress", "--platform", "duckdb", "--benchmark", "tpch", "--scale", "0.5"])

    assert rc == 0
    default_config_path = REPO_ROOT / "tests" / "uat" / "configs" / "stress-default.yaml"
    assert calls == [(default_config_path, {"platform": "duckdb", "benchmark": "tpch", "scale": 0.5})]
    out = capsys.readouterr().out
    assert '"phase_exit_codes"' in out
    assert "abort_reason" not in out


def test_make_uat_sweep_forwards_dry_run_variable():
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("uat-sweep:", maxsplit=1)[1].split("# make uat-stress", maxsplit=1)[0]

    assert "[DRY_RUN=1]" in target
    assert "$(if $(DRY_RUN),--dry-run,)" in target


def test_make_uat_docker_cleanup_defaults_to_dry_run_and_supports_apply():
    dry_run = subprocess.run(
        ["make", "--no-print-directory", "-n", "uat-docker-cleanup"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    apply = subprocess.run(
        ["make", "--no-print-directory", "-n", "uat-docker-cleanup", "APPLY=1", "PREFIX=benchbox-uat-test"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert dry_run.returncode == 0, dry_run.stderr
    assert "docker-cleanup" in dry_run.stdout
    assert "--apply" not in dry_run.stdout
    assert apply.returncode == 0, apply.stderr
    assert '--prefix "benchbox-uat-test"' in apply.stdout
    assert "--apply" in apply.stdout


@pytest.mark.parametrize(
    ("target", "variables", "expected_fragment"),
    [
        (
            "uat-cell",
            ["PLATFORM=duckdb", "BENCHMARK=tpch", "SCALE=0.01"],
            '--platform "duckdb"',
        ),
        (
            "uat-stress",
            ["PLATFORM=duckdb", "BENCHMARK=tpch"],
            '--platform "duckdb"',
        ),
    ],
)
def test_make_uat_targets_accept_non_bring_up_platforms(target, variables, expected_fragment):
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", target, *variables],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert expected_fragment in result.stdout
    assert "unknown platform" not in result.stderr


def test_make_uat_bring_up_unknown_platform_still_fails_clearly():
    result = subprocess.run(
        ["make", "--no-print-directory", "uat-bring-up", "PLATFORM=does-not-exist"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "unknown platform" in result.stderr.strip().splitlines()[-1]


def test_execute_main_reads_cleanup_config_for_standalone_path(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "uat.yaml"
    _write_uat_config(
        config_path,
        {
            "name": "managed-cli",
            "phases": ["execute"],
            "platforms": {"include": ["clickhouse-server"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "cleanup": {"docker_manage_platforms": True, "docker_platform_switch": "volumes"},
        },
    )
    captured: dict[str, object] = {}

    def fake_run_execute(config, **kwargs):
        captured["docker_manage_platforms"] = config.cleanup.docker_manage_platforms
        captured["docker_platform_switch"] = config.cleanup.docker_platform_switch
        captured["cleanup_enabled"] = kwargs["cleanup_enabled"]
        captured["free_space_checks_enabled"] = kwargs["free_space_checks_enabled"]
        return type(
            "ExecuteOutcome",
            (),
            {
                "results": (),
                "pruned": (),
                "skipped_unreachable": (),
                "docker_events": (),
                "aborted": False,
                "abort_reason": None,
                "exit_code": lambda self: 0,
            },
        )()

    monkeypatch.setattr("tests.uat.phases.execute.run_execute", fake_run_execute)
    rc = _cli.main(["execute", "--config", str(config_path)])

    assert rc == 0
    assert captured == {
        "docker_manage_platforms": True,
        "docker_platform_switch": "volumes",
        "cleanup_enabled": True,
        "free_space_checks_enabled": True,
    }
    assert '"name": "managed-cli"' in capsys.readouterr().out


def test_uat_execute_and_execute_only_sweep_produce_identical_cells_jsonl(tmp_path, monkeypatch, capsys):
    from tests.uat.config import load_config
    from tests.uat.orchestrator import run_sweep
    from tests.uat.runner import CellResult

    def fake_run_cell(platform, benchmark, scale, **kwargs):
        return CellResult(
            platform=platform,
            benchmark=benchmark,
            scale=scale,
            status="passed",
            exit_code=0,
            elapsed_s=1.23,
            log_path=Path("/nonexistent/cell.log"),
            result_path=Path("/nonexistent/result.json"),
        )

    monkeypatch.setattr("tests.uat.phases.execute.run_cell", fake_run_cell)

    config_path = tmp_path / "uat.yaml"
    _write_uat_config(
        config_path,
        {
            "name": "parity-smoke",
            "phases": ["execute"],
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "preflight": {"free_space_min_gib": 0},
            "output": {"logs_dir_template": str(tmp_path / "execute-run")},
        },
    )

    rc = _cli.main(["execute", "--config", str(config_path)])
    assert rc == 0
    execute_summary = json.loads(capsys.readouterr().out)
    execute_log_dir = Path(execute_summary["log_dir"])

    sweep_config = load_config(config_path)
    sweep_log_dir = tmp_path / "sweep-run"
    run_sweep(sweep_config, log_dir_override=sweep_log_dir)

    execute_cells = (execute_log_dir / "cells.jsonl").read_text(encoding="utf-8")
    sweep_cells = (sweep_log_dir / "cells.jsonl").read_text(encoding="utf-8")
    assert execute_cells == sweep_cells

    execute_sidecar = (execute_log_dir / "cells.jsonl.accounting.json").read_text(encoding="utf-8")
    sweep_sidecar = (sweep_log_dir / "cells.jsonl.accounting.json").read_text(encoding="utf-8")
    assert execute_sidecar == sweep_sidecar


def test_execute_main_reports_success_for_dry_run_config(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "uat.yaml"
    _write_uat_config(
        config_path,
        {
            "name": "dry-run-smoke",
            "dry_run": True,
            "phases": ["execute"],
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "scales": {"rungs": [0.01]},
            "output": {"logs_dir_template": str(tmp_path / "dry-run-execute")},
        },
    )

    def fail_run_execute(config, **kwargs):
        raise AssertionError("dry_run: true must not invoke run_execute")

    monkeypatch.setattr("tests.uat.phases.execute.run_execute", fail_run_execute)

    rc = _cli.main(["execute", "--config", str(config_path)])

    summary = json.loads(capsys.readouterr().out)
    assert summary["aborted"] is False
    assert summary["abort_reason"] is None
    assert rc == 0


def test_report_json_includes_unvalidated_count(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.uat import cells_io, orchestrator
    from tests.uat.runner import CellResult

    source_info = orchestrator.RunSourceInfo(commit_sha="deadbeef", commit_short_sha="deadbee", dirty=False)
    clean_cell = CellResult(
        platform="duckdb",
        benchmark="tpch",
        scale=0.01,
        status="passed",
        exit_code=0,
        elapsed_s=1.0,
        log_path=tmp_path / "duckdb.log",
        result_path=tmp_path / "duckdb_result.json",
        submit_terminal_state="submittable",
    )
    unvalidated_cell = CellResult(
        platform="polars-df",
        benchmark="tpch",
        scale=0.01,
        status="passed",
        exit_code=0,
        elapsed_s=1.0,
        log_path=tmp_path / "polars_df.log",
        result_path=tmp_path / "polars_df_result.json",
        submit_terminal_state="unvalidated",
    )
    cells_jsonl = tmp_path / "cells.jsonl"
    cells_io.write_cells_jsonl(cells_jsonl, [clean_cell, unvalidated_cell], source_info=source_info)

    exit_code = _cli.main(
        ["report", "--cells-jsonl", str(cells_jsonl), "--output-tsv", str(tmp_path / "matrix_summary.tsv")]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] == 2
    assert payload["attempted"] == 2
    assert payload["unvalidated"] == 1


def _write_stage(tmp_path: Path, index: int, name: str, completed_at: str, **overrides) -> Path:
    from tests.uat import gate_summary

    kwargs = {
        "config_name": name,
        "source_commit_sha": "abc123",
        "source_dirty": False,
        "container_engine": "docker",
        "completed_at": completed_at,
        "dry_run": False,
        "aborted": False,
        "abort_phase": None,
        "abort_reason": None,
        "phase_exit_codes": {"execute": 0, "validate": 0, "report": 0},
        "accounting": gate_summary.PhaseAccounting(attempted=5, passed=5, total_defined=5),
        "unreachable_is_estimated": False,
        "validator_clean_rate": 1.0,
        "validator_clean_rate_floor": 1.0,
        "validator_floor_breached": False,
        "cross_scale_clean_pairs": 10,
        "cross_scale_floor": 8,
        "cross_scale_floor_breached": False,
        "verdict": "green",
    }
    if "artifact_digests" not in overrides:
        import hashlib

        seeds = {
            "cells_jsonl": f"cells-stage{index}\n".encode(),
            "accounting_sidecar": f"sidecar-stage{index}\n".encode(),
            "lifecycle_log": f"lifecycle-stage{index}\n".encode(),
        }
        kwargs["artifact_digests"] = {k: hashlib.sha256(v).hexdigest() for k, v in seeds.items()}
    kwargs.update(overrides)
    stage_dir = tmp_path / f"stage{index}"
    stage_dir.mkdir(exist_ok=True)
    if kwargs.get("artifact_digests") is not None:
        for key, file_name in (
            ("cells_jsonl", "cells.jsonl"),
            ("accounting_sidecar", "cells.jsonl.accounting.json"),
            ("lifecycle_log", "uat_lifecycle.log"),
        ):
            hex_digest = kwargs["artifact_digests"].get(key)
            target = stage_dir / file_name
            if hex_digest is None:
                target.unlink(missing_ok=True)
            else:
                if key == "cells_jsonl":
                    target.write_bytes(f"cells-stage{index}\n".encode())
                elif key == "accounting_sidecar":
                    target.write_bytes(f"sidecar-stage{index}\n".encode())
                else:
                    target.write_bytes(f"lifecycle-stage{index}\n".encode())
    gate_summary.write_gate_summary(stage_dir, gate_summary.GateSummary(**kwargs))
    return stage_dir


def _patch_lifecycle_digest_to_absent(stage_dir: Path) -> None:
    from tests.uat import gate_summary

    summary_path = stage_dir / gate_summary.GATE_SUMMARY_FILENAME
    summary = gate_summary.read_gate_summary(summary_path)
    digests = dict(summary.artifact_digests or {})
    digests["lifecycle_log"] = None
    patched = gate_summary.GateSummary(
        **{**summary.to_json_dict(), "artifact_digests": digests, "accounting": summary.accounting}
    )
    gate_summary.write_gate_summary(stage_dir, patched)


def _update_digests(stage_dir: Path) -> None:
    import hashlib

    from tests.uat import gate_summary

    summary_path = stage_dir / gate_summary.GATE_SUMMARY_FILENAME
    summary = gate_summary.read_gate_summary(summary_path)
    digests = dict(summary.artifact_digests or {})
    digests["lifecycle_log"] = hashlib.sha256((stage_dir / "uat_lifecycle.log").read_bytes()).hexdigest()
    patched = gate_summary.GateSummary(
        **{**summary.to_json_dict(), "artifact_digests": digests, "accounting": summary.accounting}
    )
    gate_summary.write_gate_summary(stage_dir, patched)


def test_gate_check_green_writes_combined_evidence(tmp_path: Path, capsys):
    s1 = _write_stage(tmp_path, 1, "release-gate-01-native-dataframe", "2026-07-10T10:00:00")
    s2 = _write_stage(tmp_path, 2, "release-gate-02-docker-nonoltp", "2026-07-10T12:00:00")
    s3 = _write_stage(tmp_path, 3, "release-gate-03-docker-oltp", "2026-07-10T14:00:00")
    (s2 / "uat_lifecycle.log").write_text(
        "2026-07-10T11:00:00 [docker] platform=starrocks action=up status=ok\n", encoding="utf-8"
    )
    (s3 / "uat_lifecycle.log").write_text(
        "2026-07-10T13:00:00 [docker] platform=postgresql action=up status=ok\n", encoding="utf-8"
    )
    _update_digests(s2)
    _update_digests(s3)
    output = tmp_path / "evidence" / "uat-gate-summary.json"

    rc = _cli.main(
        ["gate-check", "--stage1", str(s1), "--stage2", str(s2), "--stage3", str(s3), "--output", str(output)]
    )

    assert rc == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["verdict"] == "green"
    assert payload["source_commit_sha"] == "abc123"
    assert payload["ordering_violations"] == []
    stdout = capsys.readouterr().out
    assert "APPROVE" in stdout


def test_gate_check_flags_docker_up_before_stage1_completion(tmp_path: Path, capsys):
    s1 = _write_stage(tmp_path, 1, "release-gate-01-native-dataframe", "2026-07-10T10:00:00")
    s2 = _write_stage(tmp_path, 2, "release-gate-02-docker-nonoltp", "2026-07-10T12:00:00")
    s3 = _write_stage(tmp_path, 3, "release-gate-03-docker-oltp", "2026-07-10T14:00:00")
    (s2 / "uat_lifecycle.log").write_text(
        "2026-07-10T09:00:00 [docker] platform=starrocks action=up status=ok\n", encoding="utf-8"
    )
    output = tmp_path / "evidence.json"

    rc = _cli.main(
        ["gate-check", "--stage1", str(s1), "--stage2", str(s2), "--stage3", str(s3), "--output", str(output)]
    )

    assert rc == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["verdict"] == "red"
    assert any("stage1 boundary" in violation for violation in payload["ordering_violations"])


def test_gate_check_flags_stage3_docker_up_before_stage2_completion(tmp_path: Path):
    s1 = _write_stage(tmp_path, 1, "release-gate-01-native-dataframe", "2026-07-10T10:00:00")
    s2 = _write_stage(tmp_path, 2, "release-gate-02-docker-nonoltp", "2026-07-10T12:00:00")
    s3 = _write_stage(tmp_path, 3, "release-gate-03-docker-oltp", "2026-07-10T14:00:00")
    (s3 / "uat_lifecycle.log").write_text(
        "2026-07-10T11:00:00 [docker] platform=postgresql action=up status=ok\n", encoding="utf-8"
    )
    output = tmp_path / "evidence.json"

    rc = _cli.main(
        ["gate-check", "--stage1", str(s1), "--stage2", str(s2), "--stage3", str(s3), "--output", str(output)]
    )

    assert rc == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert any("stage2 boundary" in violation for violation in payload["ordering_violations"])


def test_gate_check_red_stage_blocks_and_reports_reason(tmp_path: Path, capsys):
    s1 = _write_stage(tmp_path, 1, "release-gate-01-native-dataframe", "2026-07-10T10:00:00")
    s2 = _write_stage(
        tmp_path,
        2,
        "release-gate-02-docker-nonoltp",
        "2026-07-10T12:00:00",
        verdict="red",
        phase_exit_codes={"execute": 1, "report": 1},
    )
    s3 = _write_stage(tmp_path, 3, "release-gate-03-docker-oltp", "2026-07-10T14:00:00")
    output = tmp_path / "evidence.json"

    rc = _cli.main(
        ["gate-check", "--stage1", str(s1), "--stage2", str(s2), "--stage3", str(s3), "--output", str(output)]
    )

    assert rc == 1
    assert "HOLD" in capsys.readouterr().err
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["verdict"] == "red"


def test_gate_check_missing_stage_summary_is_a_hard_error(tmp_path: Path, capsys):
    s1 = _write_stage(tmp_path, 1, "release-gate-01-native-dataframe", "2026-07-10T10:00:00")
    s2 = _write_stage(tmp_path, 2, "release-gate-02-docker-nonoltp", "2026-07-10T12:00:00")
    missing = tmp_path / "stage3-not-run"
    output = tmp_path / "evidence.json"

    rc = _cli.main(
        ["gate-check", "--stage1", str(s1), "--stage2", str(s2), "--stage3", str(missing), "--output", str(output)]
    )

    assert rc == 2
    assert not output.exists()
    assert "not found" in capsys.readouterr().err


def test_gate_check_same_run_dir_passed_thrice_is_red(tmp_path: Path):
    s1 = _write_stage(tmp_path, 1, "release-gate-01-native-dataframe", "2026-07-10T10:00:00")

    output = tmp_path / "evidence.json"
    rc = _cli.main(
        ["gate-check", "--stage1", str(s1), "--stage2", str(s1), "--stage3", str(s1), "--output", str(output)]
    )

    assert rc == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["verdict"] == "red"
    assert any("do not match the expected" in reason for reason in payload["reasons"])


def test_gate_check_docker_stage_without_lifecycle_log_is_red(tmp_path: Path):
    s1 = _write_stage(tmp_path, 1, "release-gate-01-native-dataframe", "2026-07-10T10:00:00")
    s2 = _write_stage(tmp_path, 2, "release-gate-02-docker-nonoltp", "2026-07-10T12:00:00")
    (s2 / "uat_lifecycle.log").unlink(missing_ok=True)
    _patch_lifecycle_digest_to_absent(s2)
    s3 = _write_stage(tmp_path, 3, "release-gate-03-docker-oltp", "2026-07-10T14:00:00")
    (s3 / "uat_lifecycle.log").write_text(
        "2026-07-10T13:00:00 [docker] platform=postgresql action=up status=ok\n", encoding="utf-8"
    )
    output = tmp_path / "evidence.json"

    rc = _cli.main(
        ["gate-check", "--stage1", str(s1), "--stage2", str(s2), "--stage3", str(s3), "--output", str(output)]
    )

    assert rc == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["verdict"] == "red"
    assert any(
        "missing lifecycle log" in violation and "stage2" in violation for violation in payload["ordering_violations"]
    )
    assert not any(
        "stage3" in violation and "missing lifecycle log" in violation for violation in payload["ordering_violations"]
    )


def test_gate_check_rejects_unknown_sha(tmp_path: Path, capsys):
    s1 = _write_stage(
        tmp_path, 1, "release-gate-01-native-dataframe", "2026-07-10T10:00:00", source_commit_sha="unknown"
    )
    s2 = _write_stage(tmp_path, 2, "release-gate-02-docker-nonoltp", "2026-07-10T12:00:00", source_commit_sha="unknown")
    s3 = _write_stage(tmp_path, 3, "release-gate-03-docker-oltp", "2026-07-10T14:00:00", source_commit_sha="unknown")
    output = tmp_path / "evidence.json"
    rc = _cli.main(
        ["gate-check", "--stage1", str(s1), "--stage2", str(s2), "--stage3", str(s3), "--output", str(output)]
    )
    assert rc == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["verdict"] == "red"
    assert any("unknown" in reason.lower() for reason in payload["reasons"])


def test_gate_check_rejects_digest_mismatch(tmp_path: Path, capsys):
    s1 = _write_stage(tmp_path, 1, "release-gate-01-native-dataframe", "2026-07-10T10:00:00")
    s2 = _write_stage(tmp_path, 2, "release-gate-02-docker-nonoltp", "2026-07-10T12:00:00")
    s3 = _write_stage(tmp_path, 3, "release-gate-03-docker-oltp", "2026-07-10T14:00:00")
    (s1 / "cells.jsonl").write_text('{"tampered": true}\n', encoding="utf-8")
    output = tmp_path / "evidence.json"
    rc = _cli.main(
        ["gate-check", "--stage1", str(s1), "--stage2", str(s2), "--stage3", str(s3), "--output", str(output)]
    )
    assert rc == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["verdict"] == "red"
    assert any("digest mismatch" in reason.lower() for reason in payload["reasons"])
    assert any("cells_jsonl" in reason for reason in payload["reasons"])


def test_gate_check_rejects_old_evidence_without_digests(tmp_path: Path, capsys):
    s1 = _write_stage(tmp_path, 1, "release-gate-01-native-dataframe", "2026-07-10T10:00:00", artifact_digests=None)
    s2 = _write_stage(tmp_path, 2, "release-gate-02-docker-nonoltp", "2026-07-10T12:00:00", artifact_digests=None)
    s3 = _write_stage(tmp_path, 3, "release-gate-03-docker-oltp", "2026-07-10T14:00:00", artifact_digests=None)
    output = tmp_path / "evidence.json"
    rc = _cli.main(
        ["gate-check", "--stage1", str(s1), "--stage2", str(s2), "--stage3", str(s3), "--output", str(output)]
    )
    assert rc == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert any("regenerate evidence" in reason.lower() for reason in payload["reasons"])


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_completeness_gate_digest_provenance(tmp_path: Path):
    from tests.uat import orchestrator
    from tests.uat.config import validate_config

    cfg = validate_config(
        {
            "name": "digest-provenance-e2e",
            "phases": ["execute", "report"],
            "platforms": {"include": ["duckdb"]},
            "benchmarks": {"include": ["tpch"]},
            "execute": {"extra_args": ["--client-region", "local", "--client-cloud", "unknown"]},
        }
    )
    log_dir = tmp_path / "logs"
    orchestrator.run_sweep(cfg, log_dir_override=log_dir)
    from tests.uat.gate_summary import read_gate_summary

    summary = read_gate_summary(log_dir / "uat_gate_summary.json")
    assert summary.artifact_digests is not None
    assert set(summary.artifact_digests.keys()) == {"cells_jsonl", "accounting_sidecar", "lifecycle_log"}
    import hashlib

    for key, expected in summary.artifact_digests.items():
        path = {
            "cells_jsonl": log_dir / "cells.jsonl",
            "accounting_sidecar": log_dir / "cells.jsonl.accounting.json",
            "lifecycle_log": log_dir / "uat_lifecycle.log",
        }[key]
        if expected is None:
            assert not path.exists(), f"expected absent {key} -> no file"
        else:
            assert path.is_file()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


def test_every_light_subcommand_is_a_registered_subparser():
    parser = _cli._build_parser()
    registered = next(action for action in parser._actions if hasattr(action, "choices") and action.choices).choices
    assert set(registered) >= _cli.LIGHT_SUBCOMMANDS


def _guard_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BENCHBOX_TEST_LOCK_DIR", str(tmp_path / "lock"))
    monkeypatch.setenv("BENCHBOX_HEAVY_ADMIT", "force")
    monkeypatch.setenv("BENCHBOX_HEAVY_WATCHDOG", "off")
    monkeypatch.delenv("BENCHBOX_TEST_SESSION_OWNER", raising=False)


def _spy_acquire(monkeypatch):
    calls = []
    real = _cli.host_lock.acquire

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(_cli.host_lock, "acquire", spy)
    return calls


def _with_fake_subcommand(monkeypatch, name, seen):
    real_build = _cli._build_parser

    def build():
        parser = real_build()
        sub = next(action for action in parser._actions if hasattr(action, "choices") and action.choices)
        fake = sub.add_parser(name)
        fake.set_defaults(handler=lambda args: seen.append(_cli.os.environ.get("BENCHBOX_TEST_SESSION_OWNER")) or 0)
        return parser

    monkeypatch.setattr(_cli, "_build_parser", build)


def test_an_unlisted_subcommand_acquires_the_lock_with_admission_and_guard(monkeypatch, tmp_path):
    _guard_env(monkeypatch, tmp_path)
    monkeypatch.setattr(_cli.session_isolation, "active", lambda: False)
    seen: list[str | None] = []
    _with_fake_subcommand(monkeypatch, "future-heavy", seen)
    calls = _spy_acquire(monkeypatch)
    assert _cli.main(["future-heavy"]) == 0
    assert len(calls) == 1
    assert calls[0]["admit"] is True and calls[0]["guard"] is True
    assert seen and seen[0]
    assert "BENCHBOX_TEST_SESSION_OWNER" not in _cli.os.environ


def test_dispatch_inside_an_isolated_session_does_not_acquire(monkeypatch, tmp_path):
    _guard_env(monkeypatch, tmp_path)
    monkeypatch.setattr(_cli.session_isolation, "active", lambda: True)
    seen: list[str | None] = []
    _with_fake_subcommand(monkeypatch, "future-heavy", seen)
    calls = _spy_acquire(monkeypatch)
    assert _cli.main(["future-heavy"]) == 0
    assert calls == []


def test_a_light_subcommand_does_not_acquire(monkeypatch, tmp_path):
    _guard_env(monkeypatch, tmp_path)
    monkeypatch.setattr(_cli.session_isolation, "active", lambda: False)
    calls = _spy_acquire(monkeypatch)
    monkeypatch.setattr(_cli, "_handle_report", lambda args: 0)
    assert _cli.main(["report", "--cells-jsonl", "x", "--output-tsv", "y"]) == 0
    assert calls == []


def _uat_subprocess(tmp_path, *argv):
    import os
    import sys

    from tests.utilities.lock_holder import held_lock

    lock_dir = tmp_path / "lock"
    env = dict(
        os.environ,
        BENCHBOX_TEST_LOCK_DIR=str(lock_dir),
        BENCHBOX_TEST_LOCK_WAIT_SECONDS="0",
        PYTHONPATH=str(REPO_ROOT),
    )
    env.pop("BENCHBOX_TEST_SESSION_OWNER", None)
    env.pop("BENCHBOX_SKIP_TEST_LOCK", None)
    with held_lock(lock_dir):
        return subprocess.run(
            [sys.executable, "-m", "tests.uat._cli", *argv],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )


def test_a_dry_run_sweep_and_report_do_not_acquire_but_a_real_sweep_is_refused(tmp_path):
    missing = str(tmp_path / "missing.yaml")
    dry = _uat_subprocess(tmp_path / "a", "sweep", "--config", missing, "--dry-run")
    report = _uat_subprocess(
        tmp_path / "b", "report", "--cells-jsonl", missing, "--output-tsv", str(tmp_path / "out.tsv")
    )
    heavy = _uat_subprocess(tmp_path / "c", "sweep", "--config", missing)
    assert "[host-lock]" not in dry.stderr and dry.returncode != 75
    assert "[host-lock]" not in report.stderr and report.returncode != 75
    assert heavy.returncode == 75, heavy.stdout + heavy.stderr
