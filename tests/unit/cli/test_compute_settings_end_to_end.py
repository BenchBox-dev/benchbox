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
        (SERVERLESS_HOST, "compute_resource=wg2", "wg2"),
        (PROVISIONED_HOST, "compute_resource=c2", "c2"),
    ],
)
def test_redshift_compute_resource_applies_for_either_deployment(
    tmp_path: Path, host: str, option: str, expected: str
) -> None:
    _save_credentials(redshift={"host": host, "username": "u", "password": "p"})

    result, payload = _dry_run(tmp_path, "redshift", option)

    assert result.exit_code == 0, result.output
    assert payload["warnings"] == []
    config = {**payload["platform_config"], **(payload["platform_config"].get("options") or {})}
    assert config["compute_resource"] == expected
    assert config.get("workgroup_name") is None
    assert config.get("cluster_identifier") is None


def test_redshift_saved_native_key_loads_as_compute_resource(tmp_path: Path) -> None:
    _save_credentials(redshift={"host": SERVERLESS_HOST, "username": "u", "password": "p", "workgroup_name": "old"})

    config = _platform_config(tmp_path, "redshift")

    assert config["compute_resource"] == "old"
    assert config.get("workgroup_name") is None


def test_redshift_saved_cluster_identifier_on_a_custom_host_still_names_a_cluster(tmp_path: Path) -> None:
    from benchbox.platforms.redshift import RedshiftAdapter

    _save_credentials(
        redshift={"host": "redshift.internal.example.com", "username": "u", "password": "p", "cluster_identifier": "c1"}
    )

    config = _platform_config(tmp_path, "redshift")
    try:
        adapter = RedshiftAdapter.from_config({**config, "benchmark": "tpch", "scale_factor": 0.01})
    except ImportError:
        pytest.skip("Redshift drivers not installed")

    assert adapter.cluster_identifier == "c1"
    assert adapter.workgroup_name is None


def test_redshift_command_line_compute_resource_overrides_the_saved_one(tmp_path: Path) -> None:
    _save_credentials(redshift={"host": SERVERLESS_HOST, "username": "u", "password": "p", "workgroup_name": "old"})

    config = _platform_config(tmp_path, "redshift", "compute_resource=new")

    assert config["compute_resource"] == "new"


def test_redshift_command_line_compute_resource_ignores_the_saved_key_kind(tmp_path: Path) -> None:
    from benchbox.platforms.redshift import RedshiftAdapter

    _save_credentials(redshift={"host": SERVERLESS_HOST, "username": "u", "password": "p", "cluster_identifier": "c1"})

    config = _platform_config(tmp_path, "redshift", "compute_resource=wg2")
    try:
        adapter = RedshiftAdapter.from_config({**config, "benchmark": "tpch", "scale_factor": 0.01})
    except ImportError:
        pytest.skip("Redshift drivers not installed")

    assert adapter.workgroup_name == "wg2"
    assert adapter.cluster_identifier != "wg2"


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
def test_snowflake_command_line_compute_resource_overrides_the_saved_profile(tmp_path: Path, profile: dict) -> None:
    _save_credentials(snowflake=profile)

    result, payload = _dry_run(tmp_path, "snowflake", "compute_resource=BIG_WH", "compute_size=LARGE")

    assert result.exit_code == 0, result.output
    assert payload["warnings"] == []
    config = {**payload["platform_config"], **(payload["platform_config"].get("options") or {})}
    assert config["compute_resource"] == "BIG_WH"
    assert config["compute_size"] == "LARGE"
    assert config.get("warehouse") is None
    assert config.get("warehouse_size") is None


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


def test_snowflake_saved_profile_with_both_spellings_keeps_the_new_key(tmp_path: Path) -> None:
    _save_credentials(snowflake={**SNOWFLAKE_PROFILE, "compute_resource": "OTHER_WH"})

    config = _platform_config(tmp_path, "snowflake")

    assert config["compute_resource"] == "OTHER_WH"
    assert config.get("warehouse") is None


def test_firebolt_saved_native_keys_load_as_the_unified_keys(tmp_path: Path) -> None:
    _save_credentials(firebolt={"engine_name": "e1", "engine_size": "S"})

    config = _platform_config(tmp_path, "firebolt")

    assert config["compute_resource"] == "e1"
    assert config["compute_size"] == "S"


def test_firebolt_command_line_unified_keys_override_the_saved_profile(tmp_path: Path) -> None:
    _save_credentials(firebolt={"engine_name": "e1", "engine_size": "S"})

    config = _platform_config(tmp_path, "firebolt", "compute_resource=e2", "compute_size=L")

    assert config["compute_resource"] == "e2"
    assert config["compute_size"] == "L"


@pytest.mark.parametrize(
    ("platform", "option", "replacement"),
    [
        ("firebolt", "engine_name=e2", "'compute_resource'"),
        ("firebolt", "engine_size=L", "'compute_size'"),
        ("clickhouse-cloud", "service_id=s2", "'compute_resource'"),
        ("snowflake", "warehouse=A_WH", "'compute_resource'"),
        ("snowflake", "warehouse_size=LARGE", "'compute_size'"),
        ("redshift", "workgroup_name=a", "'compute_resource'"),
        ("redshift", "cluster_identifier=b", "'compute_resource'"),
    ],
)
def test_removed_compute_option_on_the_command_line_is_refused(
    tmp_path: Path, platform: str, option: str, replacement: str
) -> None:
    output = _refused(tmp_path, platform, option)

    assert "was removed in BenchBox 0.6.0" in output
    assert replacement in output


def test_clickhouse_cloud_saved_service_id_loads_as_compute_resource(tmp_path: Path) -> None:
    _save_credentials(
        **{"clickhouse-cloud": {"host": "svc.us-east-2.aws.clickhouse.cloud", "password": "p", "service_id": "s1"}}
    )

    config = _platform_config(tmp_path, "clickhouse-cloud", "compute_resource=s2")

    assert config["compute_resource"] == "s2"


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


@pytest.mark.parametrize(
    ("platform", "option", "replacement"),
    [
        ("influxdb", "mode=core", "--platform influxdb:core|cloud"),
        ("lakesail", "sail_mode=local", "--platform lakesail:local|distributed"),
        ("lakesail:distributed", "sail_mode=local", "--platform lakesail:local|distributed"),
    ],
)
def test_removed_deployment_option_on_the_command_line_is_refused(
    tmp_path: Path, platform: str, option: str, replacement: str
) -> None:
    output = _refused(tmp_path, platform, option)

    assert "was removed in BenchBox 0.6.0" in output
    assert replacement in output


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
            "assert 'mode' not in specs and 'deployment_mode' not in specs, sorted(specs)\n"
            "assert 'benchbox.platforms.influxdb' not in sys.modules\n"
            "try:\n"
            "    R.parse_options('influxdb', [('mode', 'core')])\n"
            "except Exception as error:\n"
            "    print(type(error).__name__)\n",
            entry_module,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().splitlines()[-1] == "PlatformOptionError"


def test_lakesail_saved_sail_mode_is_refused_naming_the_selector(tmp_path: Path) -> None:
    _save_credentials(lakesail={"sail_mode": "distributed"})

    result, _payload = _dry_run(tmp_path, "lakesail")

    assert result.exit_code != 0
    message = " ".join((result.output + str(result.exception)).split())
    assert "'sail_mode' for 'lakesail' was removed in BenchBox 0.6.0" in message
    assert "--platform lakesail:local|distributed" in message
