from __future__ import annotations

import stat
from pathlib import Path
from types import MappingProxyType
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from benchbox.cli.commands.datagen import datagen
from benchbox.cli.exit_codes import ExitCode
from benchbox.cli.main import cli
from benchbox.utils import datagen_backend as datagen_backend_module
from benchbox.utils.datagen_backend import (
    DEFAULT_DATAGEN_BACKENDS,
    NATIVE_BACKEND_SPECS,
    VALID_DATAGEN_BACKENDS,
    DatagenBackendError,
    NativeBackendSpec,
    get_default_datagen_backend,
    normalize_datagen_backend,
    resolve_backend_output_dir,
    resolve_datagen_backend,
    resolve_env_executable,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_valid_backends_exclude_auto() -> None:
    assert VALID_DATAGEN_BACKENDS == ("legacy", "native")
    assert "auto" not in VALID_DATAGEN_BACKENDS


def test_normalize_rejects_auto_and_unknown() -> None:
    assert normalize_datagen_backend("legacy") == "legacy"
    assert normalize_datagen_backend("NATIVE") == "native"
    with pytest.raises(DatagenBackendError, match="not supported"):
        normalize_datagen_backend("auto")
    with pytest.raises(DatagenBackendError, match="Invalid --datagen-backend"):
        normalize_datagen_backend("rust")


def test_default_backend_is_legacy_and_ignores_installed_extras() -> None:
    assert dict(DEFAULT_DATAGEN_BACKENDS) == {}
    checker = MagicMock(side_effect=AssertionError("default resolution must not probe installed extras"))
    for benchmark in ("tpch", "tpcds", "h2odb", "ssb", "clickbench", "unknown_bench"):
        assert get_default_datagen_backend(benchmark) == "legacy"
        selection = resolve_datagen_backend(benchmark, requested=None, scale_factor=1.0, extra_checker=checker)
        assert selection.backend == "legacy"
        assert selection.source == "default"
    checker.assert_not_called()


def test_explicit_legacy_always_resolves() -> None:
    selection = resolve_datagen_backend("tpch", requested="legacy", scale_factor=0.01)
    assert selection.backend == "legacy"
    assert selection.source == "explicit"


def test_explicit_native_fails_loudly_for_unsupported_benchmark() -> None:
    with pytest.raises(
        DatagenBackendError, match="does not support --datagen-backend native.*--datagen-backend legacy"
    ):
        resolve_datagen_backend("h2odb", requested="native", scale_factor=1.0)


def test_explicit_native_fails_loudly_for_tpcds_subscale_and_non_integer() -> None:
    with pytest.raises(DatagenBackendError, match="scale_factor >= 1.*--datagen-backend legacy"):
        resolve_datagen_backend(
            "tpcds", requested="native", scale_factor=0.1, platform_system="Linux", platform_machine="x86_64"
        )
    with pytest.raises(DatagenBackendError, match="integer scale factor.*--datagen-backend legacy"):
        resolve_datagen_backend(
            "tpcds", requested="native", scale_factor=1.5, platform_system="Linux", platform_machine="x86_64"
        )


def test_explicit_native_fails_loudly_on_windows_arm64() -> None:
    for machine in ("ARM64", "aarch64"):
        with pytest.raises(DatagenBackendError, match="not supported on windows.*--datagen-backend legacy"):
            resolve_datagen_backend(
                "tpch",
                requested="native",
                scale_factor=1.0,
                platform_system="Windows",
                platform_machine=machine,
                extra_checker=lambda _: True,
            )


def test_unimplemented_adapter_errors_before_suggesting_extra() -> None:
    assert NATIVE_BACKEND_SPECS["tpch"].implemented is False
    with pytest.raises(
        DatagenBackendError, match="not enabled in this build yet.*--datagen-backend legacy"
    ) as exc_info:
        resolve_datagen_backend(
            "tpch",
            requested="native",
            scale_factor=1.0,
            platform_system="Linux",
            platform_machine="x86_64",
            extra_checker=lambda _: False,
        )
    assert "uv sync --extra" not in str(exc_info.value)


def test_implemented_spec_fails_loudly_when_extra_missing_and_succeeds_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        datagen_backend_module,
        "NATIVE_BACKEND_SPECS",
        MappingProxyType(
            {
                "tpch": NativeBackendSpec(
                    benchmark="tpch",
                    mechanism="tpcgen-cli",
                    implemented=True,
                    required_extra="native-datagen",
                    required_executable="tpcgen-cli",
                    required_package="tpcgen-cli",
                )
            }
        ),
    )
    with pytest.raises(
        DatagenBackendError, match="native-datagen.*uv sync --extra native-datagen.*--datagen-backend legacy"
    ):
        resolve_datagen_backend(
            "tpch",
            requested="native",
            scale_factor=1.0,
            platform_system="Linux",
            platform_machine="x86_64",
            extra_checker=lambda _: False,
        )

    selection = resolve_datagen_backend(
        "tpch",
        requested="native",
        scale_factor=1.0,
        platform_system="Darwin",
        platform_machine="arm64",
        extra_checker=lambda exe: exe == "tpcgen-cli",
        version_resolver=lambda pkg: "4.0.0" if pkg == "tpcgen-cli" else None,
    )
    assert selection.backend == "native"
    assert selection.source == "explicit"
    assert selection.mechanism == "tpcgen-cli"
    assert selection.backend_version == "4.0.0"


def test_resolve_env_executable_scopes_to_active_python_env(tmp_path: Path) -> None:
    bin_dir = tmp_path / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    fake_py = bin_dir / "python"
    fake_py.write_text("#!/bin/sh\n", encoding="utf-8")

    assert resolve_env_executable("tpcgen-cli", python_executable=fake_py) is None

    fake_cli = bin_dir / "tpcgen-cli"
    fake_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_cli.chmod(fake_cli.stat().st_mode | stat.S_IXUSR)
    assert resolve_env_executable("tpcgen-cli", python_executable=fake_py) == fake_cli


def test_resolve_backend_output_dir_isolates_non_identical_backends(tmp_path: Path) -> None:
    from benchbox.utils.datagen_version import current_datagen_stamp, manifest_datagen_is_current

    shared = tmp_path / "datagen" / "tpch_sf1"
    assert resolve_backend_output_dir(shared, "tpch", backend="legacy", scale_factor=1.0) == shared

    cases = [
        ("tpch", 1.0, "4.0.0", None, True),
        ("tpch", 1.0, "4.0.0", "benchbox-streaming", True),
        ("tpch", 1.0, "4.0.0", "tpcgen-cli", False),
        ("tpch", 1.0, "5.0.0", None, False),
        ("tpch", 10.0, "4.0.0", None, False),
        ("h2odb", 1.0, "vec-1.0", None, False),
    ]
    for benchmark, sf, version, parquet_writer, expect_shared in cases:
        base = tmp_path / "datagen" / f"{benchmark}_sf{sf:g}"
        resolved = resolve_backend_output_dir(
            base,
            benchmark,
            backend="native",
            scale_factor=sf,
            backend_version=version,
            parquet_writer=parquet_writer,
        )
        assert (resolved == base) is expect_shared
        if not expect_shared:
            assert resolved == tmp_path / "datagen" / f"{benchmark}_sf{sf:g}__backend_native"

        manifest: dict[str, object] = {
            "benchmark": benchmark,
            "scale_factor": sf,
            "formats": ["tbl", "parquet"] if parquet_writer else ["tbl"],
            **current_datagen_stamp(benchmark, backend="native", backend_version=version),
        }
        if parquet_writer is not None:
            manifest["parquet_writer"] = parquet_writer
        assert manifest_datagen_is_current(manifest, benchmark=benchmark, expected_backend="legacy") is expect_shared


def test_orchestrator_output_root_isolates_non_identical_native_backend(tmp_path: Path) -> None:
    from benchbox.cli.benchmarks import BenchmarkConfig
    from benchbox.cli.orchestrator import BenchmarkOrchestrator

    orchestrator = BenchmarkOrchestrator(base_dir=str(tmp_path))

    class _FakeBenchmark:
        output_dir = None

    native_sf10 = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=10.0,
        options={"datagen_backend": "native", "datagen_backend_version": "4.0.0"},
    )
    local_root = orchestrator._resolve_output_root(native_sf10, _FakeBenchmark(), None)
    assert isinstance(local_root, str)
    assert Path(local_root).name.endswith("__backend_native")

    native_sf1 = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=1.0,
        options={"datagen_backend": "native", "datagen_backend_version": "4.0.0"},
    )
    shared_root = orchestrator._resolve_output_root(native_sf1, _FakeBenchmark(), None)
    assert isinstance(shared_root, str)
    assert not Path(shared_root).name.endswith("__backend_native")

    orchestrator.set_custom_output_dir("s3://example-bucket/benchbox-staging")
    cloud_root = orchestrator._resolve_custom_output_root(native_sf10, _FakeBenchmark(), {})
    assert cloud_root.name.endswith("__backend_native")


def test_cli_datagen_rejects_auto_and_forwards_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    import click

    from benchbox.cli.commands import datagen as datagen_module

    runner = CliRunner()
    bad = runner.invoke(datagen, ["--benchmark", "tpch", "--scale", "0.01", "--datagen-backend", "auto"])
    assert bad.exit_code == ExitCode.USAGE
    assert "auto" in bad.output

    captured: dict[str, object] = {}

    @click.command("run")
    def _stub_run(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(datagen_module, "run", _stub_run)
    good = runner.invoke(datagen, ["--benchmark", "tpch", "--scale", "0.01", "--datagen-backend", "legacy"])
    assert good.exit_code == 0, good.output
    assert captured["datagen_backend"] == "legacy"


def test_dryrun_preview_preserves_explicit_datagen_backend_and_omits_default() -> None:
    from io import StringIO

    from rich.console import Console

    from benchbox.cli.benchmarks import BenchmarkConfig
    from benchbox.cli.dryrun import display_interactive_preview
    from benchbox.core.config import DatabaseConfig

    db_cfg = DatabaseConfig(type="duckdb", name="duckdb")

    explicit_buf = StringIO()
    display_interactive_preview(
        database_config=db_cfg,
        benchmark_config=BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
            options={"datagen_backend": "legacy", "datagen_backend_explicit": True},
        ),
        phases=["generate"],
        tuning="notuning",
        console_obj=Console(file=explicit_buf, force_terminal=False, width=160),
    )
    assert "--datagen-backend legacy" in explicit_buf.getvalue()

    default_buf = StringIO()
    display_interactive_preview(
        database_config=db_cfg,
        benchmark_config=BenchmarkConfig(
            name="tpch",
            display_name="TPC-H",
            scale_factor=0.01,
            options={"datagen_backend": "legacy"},
        ),
        phases=["generate"],
        tuning="notuning",
        console_obj=Console(file=default_buf, force_terminal=False, width=160),
    )
    assert "--datagen-backend" not in default_buf.getvalue()


def test_cli_run_rejects_unsupported_native_backend() -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "run",
            "--benchmark",
            "h2odb",
            "--scale",
            "0.01",
            "--phases",
            "generate",
            "--datagen-backend",
            "native",
            "--non-interactive",
            "--no-progress",
        ],
    )
    assert result.exit_code == ExitCode.USAGE
    assert "does not support --datagen-backend native" in result.output
