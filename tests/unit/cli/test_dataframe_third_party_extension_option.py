from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

import benchbox.platforms.dataframe as dataframe_platforms
from benchbox.cli.commands.run import run
from benchbox.cli.config import ConfigManager
from benchbox.core.hooks.platform_hooks import PlatformHookRegistry
from benchbox.utils.runtime_env import DriverResolution

pytestmark = [pytest.mark.unit, pytest.mark.medium]

OPTION = "allow_third_party_extensions"
DATAFRAME_PLATFORMS = ["polars", "pandas", "cudf", "dask", "datafusion", "pyspark", "lakesail"]


class AdapterReached(Exception):
    pass


@pytest.fixture(autouse=True)
def _drivers_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "benchbox.cli.database.ensure_driver_version",
        lambda package_name, requested_version, auto_install=False, install_hint=None, allow_install=True: (
            DriverResolution(
                package=package_name or "",
                requested=requested_version,
                resolved=requested_version or "local-dev",
                actual=requested_version or "local-dev",
                auto_install_used=False,
                runtime_strategy="current-process",
                runtime_path=None,
                runtime_python_executable=sys.executable,
            )
        ),
    )


def _dry_run(tmp_path: Path, platform: str, *options: str) -> dict[str, Any]:
    preview_dir = Path(tempfile.mkdtemp(prefix="preview-", dir=tmp_path))
    args = ["--platform", platform, "--benchmark", "tpch", "--scale", "0.01", "--non-interactive"]
    for option in options:
        args.extend(["--platform-option", option])
    result = CliRunner().invoke(run, [*args, "--dry-run", str(preview_dir)], obj={"config": ConfigManager()})
    assert result.exit_code == 0, result.output
    (summary,) = preview_dir.glob("tpch_*.json")
    return json.loads(summary.read_text(encoding="utf-8"))


@pytest.mark.parametrize("platform", DATAFRAME_PLATFORMS)
def test_option_is_registered_with_a_false_default(platform: str) -> None:
    spec = PlatformHookRegistry.list_option_specs(platform)[OPTION]

    assert spec.default is False
    assert spec.parser("true") is True


@pytest.mark.parametrize("platform", ["polars", "pandas"])
def test_dry_run_shows_option_when_set(tmp_path: Path, platform: str) -> None:
    payload = _dry_run(tmp_path, platform, f"{OPTION}=true")

    assert f'"{OPTION}": true' in json.dumps(payload)


@pytest.mark.parametrize("platform", ["polars", "pandas"])
def test_dry_run_omits_option_when_unset(tmp_path: Path, platform: str) -> None:
    payload = _dry_run(tmp_path, platform)

    assert f'"{OPTION}": true' not in json.dumps(payload)


@pytest.mark.parametrize(
    ("platform", "adapter_name"), [("polars", "PolarsDataFrameAdapter"), ("pandas", "PandasDataFrameAdapter")]
)
def test_option_reaches_the_dataframe_adapter_constructor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str, adapter_name: str
) -> None:
    adapter_class = getattr(dataframe_platforms, adapter_name)
    seen: list[dict[str, Any]] = []

    def spy(self: Any, **kwargs: Any) -> None:
        seen.append(kwargs)
        raise AdapterReached

    monkeypatch.setattr(adapter_class, "__init__", spy)
    _dry_run(tmp_path, platform, f"{OPTION}=true")

    assert seen[0][OPTION] is True


@pytest.mark.parametrize(("raw", "expected"), [(None, False), (False, False), (True, True)])
def test_polars_adapter_carries_option_and_reports_it(raw: bool | None, expected: bool) -> None:
    kwargs: dict[str, Any] = {} if raw is None else {OPTION: raw}

    adapter = dataframe_platforms.PolarsDataFrameAdapter(**kwargs)

    assert adapter.allow_third_party_extensions is expected
    assert (OPTION in adapter.get_platform_info()) is expected


@pytest.mark.parametrize(("raw", "expected"), [(None, False), (True, True)])
def test_pandas_adapter_carries_option_and_reports_it(raw: bool | None, expected: bool) -> None:
    kwargs: dict[str, Any] = {} if raw is None else {OPTION: raw}

    adapter = dataframe_platforms.PandasDataFrameAdapter(**kwargs)

    assert adapter.allow_third_party_extensions is expected
    assert (OPTION in adapter.get_platform_info()) is expected
