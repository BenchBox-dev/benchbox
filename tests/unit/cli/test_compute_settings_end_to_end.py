from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from benchbox.cli.commands.run import run
from benchbox.cli.config import ConfigManager
from benchbox.platforms import get_platform_adapter
from benchbox.utils.runtime_env import DriverResolution

pytestmark = [pytest.mark.unit, pytest.mark.medium]

SERVERLESS_HOST = "wg.123456789012.us-east-1.redshift-serverless.amazonaws.com"
PROVISIONED_HOST = "cl.abc123.us-east-1.redshift.amazonaws.com"


@pytest.fixture(autouse=True)
def _drivers_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "benchbox.cli.database.ensure_driver_version",
        lambda package_name, requested_version, auto_install=False, install_hint=None: DriverResolution(
            package=package_name or "",
            requested=requested_version,
            resolved=requested_version or "local-dev",
            actual=requested_version or "local-dev",
            auto_install_used=False,
            runtime_strategy="current-process",
            runtime_path=None,
            runtime_python_executable=sys.executable,
        ),
    )
    for module in ("redshift", "snowflake"):
        monkeypatch.setattr(f"benchbox.platforms.{module}.check_platform_dependencies", lambda *_args: (True, []))


def _save_credentials(**platforms: dict[str, Any]) -> None:
    path = Path.home() / ".benchbox" / "credentials.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(platforms), encoding="utf-8")


def _dry_run(tmp_path: Path, platform: str, *options: str) -> tuple[Any, dict[str, Any]]:
    preview_dir = Path(tempfile.mkdtemp(prefix="preview-", dir=tmp_path))
    args = ["--platform", platform, "--benchmark", "tpch", "--scale", "0.01", "--non-interactive"]
    for option in options:
        args.extend(["--platform-option", option])
    result = CliRunner().invoke(run, [*args, "--dry-run", str(preview_dir)], obj={"config": ConfigManager()})
    summaries = list(preview_dir.glob("tpch_*.json"))
    assert len(summaries) <= 1, summaries
    payload = json.loads(summaries[0].read_text(encoding="utf-8")) if summaries else {}
    return result, payload


def _platform_config(tmp_path: Path, platform: str, *options: str) -> dict[str, Any]:
    result, payload = _dry_run(tmp_path, platform, *options)
    assert result.exit_code == 0, result.output
    assert payload["database_config"]["type"] != "data_only"
    config = dict(payload["platform_config"])
    config.update(config.get("options") or {})
    return config


def _refused(tmp_path: Path, platform: str, *options: str) -> str:
    result, _payload = _dry_run(tmp_path, platform, *options)
    assert result.exit_code != 0, result.output
    return " ".join(result.output.split())


@pytest.mark.parametrize(
    ("host", "option", "expected"),
    [
        (SERVERLESS_HOST, "workgroup_name=wg1", {"workgroup_name": "wg1"}),
        (PROVISIONED_HOST, "cluster_identifier=c1", {"cluster_identifier": "c1"}),
        (SERVERLESS_HOST, "compute_resource=wg2", {"workgroup_name": "wg2"}),
        (PROVISIONED_HOST, "compute_resource=c2", {"cluster_identifier": "c2"}),
        (SERVERLESS_HOST, "cluster_identifier=c3", {"cluster_identifier": "c3"}),
    ],
)
def test_redshift_native_key_or_deployment_selects_the_resource_kind(
    tmp_path: Path, host: str, option: str, expected: dict[str, str]
) -> None:
    _save_credentials(redshift={"host": host, "username": "u", "password": "p"})

    result, payload = _dry_run(tmp_path, "redshift", option)

    assert result.exit_code == 0, result.output
    assert payload["warnings"] == []
    config = {**payload["platform_config"], **(payload["platform_config"].get("options") or {})}
    [(key, value)] = expected.items()
    other = "cluster_identifier" if key == "workgroup_name" else "workgroup_name"
    assert config[key] == value
    assert config["compute_resource"] == value
    assert config.get(other) is None


def test_redshift_command_line_workgroup_overrides_the_saved_one(tmp_path: Path) -> None:
    _save_credentials(redshift={"host": SERVERLESS_HOST, "username": "u", "password": "p", "workgroup_name": "old"})

    config = _platform_config(tmp_path, "redshift", "workgroup_name=new")

    assert config["workgroup_name"] == config["compute_resource"] == "new"


def test_redshift_bare_compute_resource_fails_when_the_host_does_not_name_a_deployment(tmp_path: Path) -> None:
    _save_credentials(redshift={"host": "redshift.internal.example", "username": "u", "password": "p"})

    output = _refused(tmp_path, "redshift", "compute_resource=x")

    assert "'compute_resource=x' could be 'workgroup_name', 'cluster_identifier'" in output


def test_redshift_two_native_keys_on_the_command_line_fail_naming_both(tmp_path: Path) -> None:
    _save_credentials(redshift={"host": SERVERLESS_HOST, "username": "u", "password": "p"})

    output = _refused(tmp_path, "redshift", "workgroup_name=a", "cluster_identifier=b")

    assert "'workgroup_name=a'" in output
    assert "'cluster_identifier=b'" in output


SNOWFLAKE_PROFILE = {
    "account": "acct",
    "username": "u",
    "password": "p",
    "warehouse": "COMPUTE_WH",
    "warehouse_size": "SMALL",
    "database": "BENCHBOX",
}

WIZARD_PROFILE = {key: value for key, value in SNOWFLAKE_PROFILE.items() if key != "warehouse"} | {
    "compute_resource": "COMPUTE_WH"
}


@pytest.mark.parametrize("profile", [SNOWFLAKE_PROFILE, WIZARD_PROFILE], ids=["native-keys", "wizard"])
@pytest.mark.parametrize("option", ["compute_resource=BIG_WH", "warehouse=BIG_WH"])
def test_snowflake_command_line_warehouse_overrides_the_saved_profile(
    tmp_path: Path, option: str, profile: dict
) -> None:
    _save_credentials(snowflake=profile)

    result, payload = _dry_run(tmp_path, "snowflake", option, "compute_size=LARGE")

    assert result.exit_code == 0, result.output
    assert payload["warnings"] == []
    config = {**payload["platform_config"], **(payload["platform_config"].get("options") or {})}
    assert config["compute_resource"] == "BIG_WH"
    assert config["compute_size"] == "LARGE"
    assert config.get("warehouse") in (None, "BIG_WH")
    assert config.get("warehouse_size") in (None, "LARGE")


@pytest.mark.parametrize("profile", [SNOWFLAKE_PROFILE, WIZARD_PROFILE], ids=["native-keys", "wizard"])
def test_snowflake_saved_profile_applies_without_command_line_values(tmp_path: Path, profile: dict) -> None:
    _save_credentials(snowflake=profile)

    config = _platform_config(tmp_path, "snowflake")

    assert config["compute_resource"] == "COMPUTE_WH"
    assert config["compute_size"] == "SMALL"


def test_snowflake_command_line_size_keeps_the_saved_warehouse(tmp_path: Path) -> None:
    _save_credentials(snowflake=SNOWFLAKE_PROFILE)

    config = _platform_config(tmp_path, "snowflake", "compute_size=LARGE")

    assert config["compute_resource"] == "COMPUTE_WH"
    assert config["compute_size"] == "LARGE"


def test_snowflake_conflict_within_the_command_line_names_both_spellings(tmp_path: Path) -> None:
    output = _refused(tmp_path, "snowflake", "warehouse=A_WH", "compute_resource=B_WH")

    assert "'warehouse=A_WH'" in output
    assert "'compute_resource=B_WH'" in output


def test_snowflake_conflict_within_the_saved_profile_names_the_profile(tmp_path: Path) -> None:
    _save_credentials(snowflake={**SNOWFLAKE_PROFILE, "compute_resource": "OTHER_WH"})

    output = _refused(tmp_path, "snowflake")

    assert "saved snowflake credentials" in output
    assert "'compute_resource=OTHER_WH'" in output
    assert "'warehouse=COMPUTE_WH'" in output


def test_firebolt_command_line_engine_overrides_the_saved_profile(tmp_path: Path) -> None:
    _save_credentials(firebolt={"engine_name": "e1", "engine_size": "S"})

    config = _platform_config(tmp_path, "firebolt", "engine_name=e2", "engine_size=L")

    assert config["compute_resource"] == "e2"
    assert config["compute_size"] == "L"
    assert config.get("engine_name") in (None, "e2")
    assert config.get("engine_size") in (None, "L")


def test_clickhouse_cloud_command_line_service_overrides_the_saved_profile(tmp_path: Path) -> None:
    _save_credentials(
        **{"clickhouse-cloud": {"host": "svc.us-east-2.aws.clickhouse.cloud", "password": "p", "service_id": "s1"}}
    )

    config = _platform_config(tmp_path, "clickhouse-cloud", "service_id=s2")

    assert config["compute_resource"] == "s2"
    assert config.get("service_id") in (None, "s2")


def test_databricks_command_line_warehouse_replaces_the_saved_http_path(tmp_path: Path) -> None:
    _save_credentials(
        databricks={
            "server_hostname": "dbc-1.cloud.databricks.com",
            "http_path": "/sql/1.0/warehouses/aaa111",
            "access_token": "dapi-test",
        }
    )

    config = _platform_config(tmp_path, "databricks", "compute_resource=bbb222")

    assert config["compute_resource"] == "bbb222"
    assert config.get("http_path") is None


def test_quanton_saved_cluster_size_wins_over_the_spec_default(tmp_path: Path) -> None:
    _save_credentials(quanton={"api_key": "k", "s3_staging_dir": "s3://bucket/path", "cluster_size": "large"})

    assert _platform_config(tmp_path, "quanton")["compute_size"] == "large"
    assert _platform_config(tmp_path, "quanton", "compute_size=medium")["compute_size"] == "medium"


def test_influxdb_mode_option_is_accepted(tmp_path: Path) -> None:
    result, payload = _dry_run(tmp_path, "influxdb", "mode=core")

    assert result.exit_code == 0, result.output
    assert "Unknown platform option" not in result.output
    assert payload["database_config"]["type"] == "influxdb"


@pytest.mark.parametrize("entry_module", ["benchbox.platforms", "benchbox.cli.main", "benchbox.mcp.server"])
def test_influxdb_option_specs_register_without_importing_the_adapter_package(entry_module: str) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, importlib\n"
            "importlib.import_module(sys.argv[1])\n"
            "from benchbox.core.hooks.platform_hooks import PlatformHookRegistry as R\n"
            "specs = R.list_option_specs('influxdb')\n"
            "assert R._resolve_option_name('influxdb', 'mode') == 'deployment_mode' in specs, sorted(specs)\n"
            "assert 'benchbox.platforms.influxdb' not in sys.modules\n"
            "print(R.parse_options('influxdb', [('mode', 'core')])['deployment_mode'])\n",
            entry_module,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().splitlines()[-1] == "core"


def test_lakesail_selector_overrides_the_saved_sail_mode(tmp_path: Path) -> None:
    _save_credentials(lakesail={"sail_mode": "local"})

    result, payload = _dry_run(tmp_path, "lakesail:distributed")

    assert result.exit_code == 0, result.output
    assert payload["warnings"] == []
    config = payload["platform_config"]
    assert config["options"]["_deployment_selector"] == "distributed"
    assert config.get("sail_mode") is None
    assert config["options"].get("sail_mode") is None
    adapter = get_platform_adapter("lakesail", **config)
    assert adapter.deployment_selected == "distributed"


def test_lakesail_saved_sail_mode_applies_without_a_selector(tmp_path: Path) -> None:
    _save_credentials(lakesail={"sail_mode": "distributed"})

    config = _platform_config(tmp_path, "lakesail")

    assert config["sail_mode"] == "distributed"
    assert get_platform_adapter("lakesail", **config).deployment_selected == "distributed"


def test_lakesail_selector_and_command_line_alias_conflict_fails(tmp_path: Path) -> None:
    output = _refused(tmp_path, "lakesail:distributed", "sail_mode=local")

    assert "distributed" in output
    assert "local" in output
