from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from _project.scripts.oracle_reviewers import cli

from .conftest import REPO_ROOT

pytestmark = [pytest.mark.unit, pytest.mark.fast]

CONFIG_PATH = REPO_ROOT / ".github" / "oracle-omp-config.yml"
POLICY_PATH = REPO_ROOT / ".github" / "oracle-reviewers.yml"
GATEWAY_URL = "https://example.ts.net:4000"
PROVIDERS = {"anthropic", "openai-codex", "muse-code", "google-antigravity"}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_reviewer_home_writes_the_config_and_the_gateway_providers(tmp_path: Path) -> None:
    assert cli.main(["reviewer-home", "--home", str(tmp_path), "--gateway-url", GATEWAY_URL]) == 0
    agent = tmp_path / ".omp" / "agent"
    assert _load(agent / "config.yml") == _load(CONFIG_PATH)
    providers = _load(agent / "models.yml")["providers"]
    assert set(providers) == PROVIDERS
    for provider in providers.values():
        assert provider == {"transport": "pi-native", "baseUrl": GATEWAY_URL, "apiKey": "OMP_GATEWAY_TOKEN"}


@pytest.mark.parametrize("url", ["", "   ", "http://example.ts.net:4000", "example.ts.net:4000", "https://"])
def test_reviewer_home_refuses_a_missing_or_insecure_gateway_url(tmp_path: Path, url: str) -> None:
    with pytest.raises(SystemExit) as refused:
        cli.main(["reviewer-home", "--home", str(tmp_path), "--gateway-url", url])
    assert "gateway URL" in str(refused.value)
    assert not (tmp_path / ".omp").exists()


def test_committed_config_has_only_roles_and_retry() -> None:
    assert set(_load(CONFIG_PATH)) == {"modelRoles", "retry"}


def test_every_policy_model_alias_has_a_role() -> None:
    roles = _load(CONFIG_PATH)["modelRoles"]
    aliases = {reviewer["model"].removeprefix("@") for reviewer in _load(POLICY_PATH)["reviewers"].values()}
    assert aliases <= set(roles)


def test_each_role_primary_has_exactly_one_model_keyed_chain() -> None:
    config = _load(CONFIG_PATH)
    chains = config["retry"]["fallbackChains"]
    primaries = {target.rsplit(":", 1)[0] for target in config["modelRoles"].values()}
    assert set(chains) == primaries
    assert not set(chains) & set(config["modelRoles"])
    assert all("/" in key and chain for key, chain in chains.items())


def test_fallback_walks_only_on_usage_limits() -> None:
    assert _load(CONFIG_PATH)["retry"]["fallbackOn"] == "usage-limit"
