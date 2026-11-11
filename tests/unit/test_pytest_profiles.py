from configparser import ConfigParser
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_pytest_section(filename: str):
    config = ConfigParser()
    config.read(REPO_ROOT / filename)
    return config["pytest"]


def _tokenize_addopts(raw: str):
    return [part for part in raw.split() if part]


def test_local_profile_disables_coverage_by_default():
    section = _load_pytest_section("pytest.ini")
    addopts = _tokenize_addopts(section["addopts"])

    assert "-p" in addopts
    assert "no:cov" in addopts
    assert "--cov=benchbox" not in addopts
    assert "--cov-report=xml:coverage.xml" not in addopts
    assert "--junit-xml=test-results.xml" not in section["addopts"]

    required_plugins = {line.strip() for line in section["required_plugins"].splitlines() if line.strip()}
    assert all("pytest-cov" not in plugin for plugin in required_plugins)


def test_ci_profile_includes_full_instrumentation():
    section = _load_pytest_section("pytest-ci.ini")
    addopts = _tokenize_addopts(section["addopts"])

    assert "--cov-config=pyproject.toml" in addopts
    assert "--junit-xml=test-results.xml" in section["addopts"]
    assert "no:cov" not in addopts

    required_plugins = {line.strip() for line in section["required_plugins"].splitlines() if line.strip()}
    assert any("pytest-cov" in plugin for plugin in required_plugins)


