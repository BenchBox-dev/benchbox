from __future__ import annotations

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
    resolve_datagen_backend,
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


def test_explicit_native_fails_loudly_when_extra_missing() -> None:
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


def test_explicit_native_fails_loudly_while_adapter_unimplemented() -> None:
    assert NATIVE_BACKEND_SPECS["tpch"].implemented is False
    with pytest.raises(DatagenBackendError, match="not enabled in this build yet.*--datagen-backend legacy"):
        resolve_datagen_backend(
            "tpch",
            requested="native",
            scale_factor=1.0,
            platform_system="Linux",
            platform_machine="x86_64",
            extra_checker=lambda _: True,
        )


def test_explicit_native_succeeds_when_spec_implemented(monkeypatch: pytest.MonkeyPatch) -> None:
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
                )
            }
        ),
    )
    selection = resolve_datagen_backend(
        "tpch",
        requested="native",
        scale_factor=1.0,
        platform_system="Darwin",
        platform_machine="arm64",
        extra_checker=lambda exe: exe == "tpcgen-cli",
    )
    assert selection.backend == "native"
    assert selection.source == "explicit"
    assert selection.mechanism == "tpcgen-cli"


def test_cli_datagen_rejects_auto_and_forwards_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    bad = runner.invoke(datagen, ["--benchmark", "tpch", "--scale", "0.01", "--datagen-backend", "auto"])
    assert bad.exit_code == ExitCode.USAGE
    assert "auto" in bad.output

    captured: dict[str, object] = {}

    def _fake_invoke(self, command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs

    monkeypatch.setattr("click.Context.invoke", _fake_invoke)
    good = runner.invoke(datagen, ["--benchmark", "tpch", "--scale", "0.01", "--datagen-backend", "legacy"])
    assert good.exit_code == 0, good.output
    assert captured["kwargs"]["datagen_backend"] == "legacy"


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
