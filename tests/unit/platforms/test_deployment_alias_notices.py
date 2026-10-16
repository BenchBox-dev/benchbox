from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from benchbox.cli.commands.run import run
from benchbox.cli.config import ConfigManager
from benchbox.core.deprecation_notices import run_notices

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _dry_run(tmp_path: Path, platform: str, *options: str):
    preview_dir = Path(tempfile.mkdtemp(prefix="preview-", dir=tmp_path))
    args = ["--platform", platform, "--benchmark", "tpch", "--scale", "0.01", "--non-interactive"]
    for option in options:
        args.extend(["--platform-option", option])
    return CliRunner().invoke(run, [*args, "--dry-run", str(preview_dir)], obj={"config": ConfigManager()})


@pytest.mark.parametrize(
    ("platform", "option", "selector"),
    [
        ("influxdb", "deployment_mode=core", "influxdb:core"),
        ("influxdb", "mode=core", "influxdb:core"),
        ("lakesail", "deployment_mode=distributed", "lakesail:distributed"),
        ("lakesail", "sail_mode=distributed", "lakesail:distributed"),
    ],
)
def test_supplied_deployment_option_records_one_removal_notice(
    tmp_path: Path, platform: str, option: str, selector: str
) -> None:
    result = _dry_run(tmp_path, platform, option)

    assert result.exit_code == 0, result.output
    notices = run_notices()
    assert len(notices) == 1
    assert notices[0].message == (
        f"Platform option '{option.split('=')[0]}' for '{platform}' is deprecated and will be removed in "
        f"BenchBox 0.6.0; use '--platform {selector}' instead."
    )


@pytest.mark.parametrize("platform", ["influxdb", "lakesail"])
def test_spec_default_deployment_records_no_notice(tmp_path: Path, platform: str) -> None:
    result = _dry_run(tmp_path, platform)

    assert result.exit_code == 0, result.output
    assert run_notices() == ()
