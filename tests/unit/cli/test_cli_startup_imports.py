from __future__ import annotations

import importlib
import subprocess
import sys

import pytest
from click.testing import CliRunner

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


def _clear_modules(monkeypatch: pytest.MonkeyPatch, *prefixes: str) -> None:
    for module_name in list(sys.modules):
        if any(module_name == prefix or module_name.startswith(f"{prefix}.") for prefix in prefixes):
            monkeypatch.delitem(sys.modules, module_name, raising=False)


def test_main_attribute_resolves_to_submodule_before_explicit_import() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import benchbox.cli; import types; "
                "target = getattr(benchbox.cli, 'main'); "
                "assert isinstance(target, types.ModuleType), type(target); "
                "assert callable(target); "
                "assert hasattr(target, 'get_config_manager')"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_calling_main_module_delegates_to_lazy_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    main_module = importlib.import_module("benchbox.cli.main")
    calls: list[str] = []
    monkeypatch.setattr(main_module, "main", lambda: calls.append("main"), raising=False)

    main_module()

    assert calls == ["main"]


def test_importing_conversion_submodule_does_not_import_lifecycle_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_modules(monkeypatch, "benchbox.core.runner", "benchbox.monitoring")

    conversion_module = importlib.import_module("benchbox.core.runner.conversion")

    assert conversion_module.FormatConversionOrchestrator.__name__ == "FormatConversionOrchestrator"
    assert "benchbox.core.runner.runner" not in sys.modules
    assert "benchbox.monitoring" not in sys.modules
    assert "benchbox.monitoring.report" not in sys.modules


def test_setup_help_does_not_require_textcharts(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_modules(
        monkeypatch,
        "benchbox.cli.main",
        "benchbox.cli.app",
        "benchbox.cli.commands",
        "benchbox.core.runner",
        "benchbox.monitoring",
        "benchbox.core.visualization",
    )
    monkeypatch.setitem(sys.modules, "textcharts", None)
    monkeypatch.setitem(sys.modules, "textcharts.base", None)
    monkeypatch.setitem(sys.modules, "textcharts.line_chart", None)

    cli = importlib.import_module("benchbox.cli.main").cli
    result = CliRunner().invoke(cli, ["setup", "--help"])

    assert result.exit_code == 0
    assert "Interactive setup for cloud platform credentials" in result.output


def test_importing_cli_does_not_load_bundled_sqlglot() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import benchbox.cli.main; "
                "loaded = sorted(name for name in sys.modules if name.startswith('benchbox._vendor.sqlglot')); "
                "assert not loaded, loaded"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
