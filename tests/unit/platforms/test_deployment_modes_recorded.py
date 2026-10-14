from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

import benchbox.platforms.databend.adapter as databend_module
import benchbox.platforms.postgresql as postgresql_module
import benchbox.platforms.timescaledb as timescaledb_module
from benchbox.cli.commands.run import run
from benchbox.cli.config import ConfigManager
from benchbox.core.deployment import deployment_class
from benchbox.core.platform_manifest import PLATFORM_MANIFEST_BY_KEY
from benchbox.core.results.execution_variant import published_variant_id, read_execution_variant
from benchbox.core.results.schema import build_result_payload
from benchbox.platforms.adapter_factory import get_adapter
from benchbox.platforms.base.runtime_metadata import (
    apply_normalized_result_metadata,
    build_default_normalized_result_metadata,
)
from benchbox.utils.runtime_env import DriverResolution
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [pytest.mark.unit, pytest.mark.fast]

CONNECTION = {"host": "db.example.com", "password": "secret", "benchmark": "tpch", "scale_factor": 0.01}
RECORDED_PLATFORMS = {
    "timescaledb": ("TimescaleDB", CONNECTION),
    "databend": ("Databend", CONNECTION),
}
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _declared_modes(platform: str) -> list[str]:
    return list(PLATFORM_MANIFEST_BY_KEY[platform].capabilities["deployment_modes"])


def _default_mode(platform: str) -> str:
    return PLATFORM_MANIFEST_BY_KEY[platform].capabilities["default_deployment"]


MODE_CASES = [(platform, mode) for platform in RECORDED_PLATFORMS for mode in _declared_modes(platform)]


@pytest.fixture(autouse=True)
def _drivers_available(monkeypatch: pytest.MonkeyPatch) -> None:
    psycopg = Mock()
    psycopg.__version__ = "3.1.0"
    monkeypatch.setattr(timescaledb_module, "psycopg", psycopg)
    monkeypatch.setattr(postgresql_module, "psycopg", psycopg)
    monkeypatch.setattr(databend_module, "DATABEND_AVAILABLE", True)
    monkeypatch.setattr(
        "benchbox.platforms.ensure_driver_version",
        lambda package_name, requested_version, auto_install=False, install_hint=None: DriverResolution(
            package=package_name or "",
            requested=requested_version,
            resolved="local-dev",
            actual="local-dev",
            auto_install_used=False,
            runtime_strategy="current-process",
            runtime_path=None,
            runtime_python_executable=sys.executable,
        ),
    )


def _bundle(adapter: Any, display_name: str) -> dict[str, Any]:
    result = make_benchmark_results(platform=display_name)
    apply_normalized_result_metadata(result, build_default_normalized_result_metadata(adapter))
    return build_result_payload(result)


def _dry_run(tmp_path: Path, platform: str, *options: str) -> tuple[Any, dict[str, Any]]:
    preview_dir = Path(tempfile.mkdtemp(prefix="preview-", dir=tmp_path))
    args = ["--platform", platform, "--benchmark", "tpch", "--scale", "0.01", "--non-interactive"]
    for option in options:
        args.extend(["--platform-option", option])
    result = CliRunner().invoke(run, [*args, "--dry-run", str(preview_dir)], obj={"config": ConfigManager()})
    summaries = list(preview_dir.glob("tpch_*.json"))
    payload = json.loads(summaries[0].read_text(encoding="utf-8")) if summaries else {}
    return result, payload


@pytest.mark.parametrize(("platform", "mode"), MODE_CASES)
def test_selected_mode_is_recorded_in_the_bundle_and_in_the_variant_id(platform: str, mode: str) -> None:
    display_name, config = RECORDED_PLATFORMS[platform]

    adapter = get_adapter(f"{platform}:{mode}", **config)
    payload = _bundle(adapter, display_name)

    assert adapter.deployment_selected == mode
    assert adapter.deployment_selected_class == deployment_class(platform, mode)
    assert payload["platform"]["deployment"]["selected"] == mode
    assert payload["platform"]["deployment"]["selected_class"] == deployment_class(platform, mode)
    expected = platform if mode == _default_mode(platform) else f"{platform}~{mode}"
    assert published_variant_id(payload, platform) == expected


@pytest.mark.parametrize("platform", sorted(RECORDED_PLATFORMS))
def test_every_declared_mode_gets_its_own_variant_id(platform: str) -> None:
    display_name, config = RECORDED_PLATFORMS[platform]

    variant_ids = {
        mode: published_variant_id(_bundle(get_adapter(f"{platform}:{mode}", **config), display_name), platform)
        for mode in _declared_modes(platform)
    }

    assert len(set(variant_ids.values())) == len(_declared_modes(platform))
    assert variant_ids[_default_mode(platform)] == platform


@pytest.mark.parametrize("platform", sorted(RECORDED_PLATFORMS))
def test_unselected_run_records_the_manifest_default(platform: str) -> None:
    display_name, config = RECORDED_PLATFORMS[platform]

    payload = _bundle(get_adapter(platform, **config), display_name)

    assert payload["platform"]["deployment"]["selected"] == _default_mode(platform)
    assert published_variant_id(payload, platform) == platform


@pytest.mark.parametrize("platform", sorted(RECORDED_PLATFORMS))
def test_bundle_without_selected_reads_as_the_manifest_default(platform: str) -> None:
    display_name, config = RECORDED_PLATFORMS[platform]
    recorded = _bundle(get_adapter(platform, **config), display_name)
    legacy = json.loads(json.dumps(recorded))
    del legacy["platform"]["deployment"]["selected"]
    del legacy["platform"]["deployment"]["selected_class"]

    assert read_execution_variant(legacy).deployment == _default_mode(platform)
    assert published_variant_id(legacy, platform) == published_variant_id(recorded, platform) == platform


def test_timescaledb_deployment_mode_reaches_the_adapter_through_from_config() -> None:
    adapter = get_adapter("timescaledb", deployment_mode="cloud", host="db.example.com", password="secret")

    assert adapter.deployment_mode == "cloud"
    assert adapter.deployment_selected == "cloud"
    assert adapter.skip_database_management is True


def test_timescaledb_cloud_selector_is_accepted_by_the_run_command(tmp_path: Path) -> None:
    result, payload = _dry_run(tmp_path, "timescaledb:cloud", "host=db.example.com", "password=secret")

    assert result.exit_code == 0, result.output
    assert payload["warnings"] == []
    assert payload["platform_config"]["options"]["_deployment_selector"] == "cloud"


@pytest.mark.parametrize("platform", sorted(RECORDED_PLATFORMS))
def test_run_command_rejects_a_selector_that_disagrees_with_the_legacy_option(tmp_path: Path, platform: str) -> None:
    default, other = _default_mode(platform), next(m for m in _declared_modes(platform) if m != _default_mode(platform))

    result, _payload = _dry_run(tmp_path, f"{platform}:{default}", f"deployment_mode={other}")

    assert result.exit_code != 0
    assert "conflicts with" in " ".join(result.output.split())


@pytest.mark.parametrize("platform", sorted(RECORDED_PLATFORMS))
def test_legacy_deployment_mode_option_selects_the_same_mode(platform: str) -> None:
    display_name, config = RECORDED_PLATFORMS[platform]
    mode = next(m for m in _declared_modes(platform) if m != _default_mode(platform))

    adapter = get_adapter(platform, deployment_mode=mode, **config)

    assert published_variant_id(_bundle(adapter, display_name), platform) == f"{platform}~{mode}"


def test_databend_modes_differ_in_connection_defaults() -> None:
    config = RECORDED_PLATFORMS["databend"][1]

    cloud = get_adapter("databend:cloud", **config)
    self_hosted = get_adapter("databend:self-hosted", **config)
    tls_self_hosted = get_adapter("databend:self-hosted", ssl=True, **config)
    plain_cloud = get_adapter("databend:cloud", ssl=False, **config)

    assert cloud.ssl is True
    assert cloud._build_dsn().startswith("databend+https://")
    assert "@db.example.com:443/" in cloud._build_dsn()
    assert self_hosted.ssl is False
    assert self_hosted._build_dsn().startswith("databend+http://")
    assert "@db.example.com:8000/" in self_hosted._build_dsn()
    assert tls_self_hosted.ssl is True
    assert "@db.example.com:443/" in tls_self_hosted._build_dsn()
    assert plain_cloud.ssl is False


def test_databend_default_connection_is_unchanged_without_a_selector() -> None:
    adapter = get_adapter("databend", **RECORDED_PLATFORMS["databend"][1])

    assert adapter.ssl is True
    assert adapter._build_dsn().startswith("databend+https://")
    assert "@db.example.com:443/" in adapter._build_dsn()


def test_singlestore_declares_only_the_mode_its_adapter_honours() -> None:
    assert _declared_modes("singlestore") == ["self-hosted"]
    assert _default_mode("singlestore") == "self-hosted"

    with pytest.raises(ValueError, match="does not support deployment mode 'cloud'"):
        get_adapter("singlestore:cloud", host="db.example.com", password="secret")


def test_no_published_bundle_belongs_to_a_platform_whose_modes_changed() -> None:
    bundles = REPOSITORY_ROOT / "results-data" / "bundles"
    if not bundles.is_dir():
        pytest.skip("published corpus is not present in this checkout")
    changed = {"timescaledb", "singlestore", "databend"}
    companions = (".manifest.json", ".plans.json", ".tuning.json", ".applied.json", ".override.json")

    present = set()
    for path in bundles.glob("*.json"):
        if path.name.endswith(companions):
            continue
        platform = json.loads(path.read_bytes()).get("platform")
        name = platform.get("name") if isinstance(platform, dict) else platform
        if str(name).strip().lower().replace(" ", "") in changed:
            present.add(path.name)

    assert present == set()
