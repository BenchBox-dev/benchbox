from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / ".github" / "review-omp-config.yml"
POLICY_PATH = ROOT / ".github" / "review.yml"
WINDOWS_PATH = ROOT / "tests" / "fixtures" / "review_provider_diversity" / "omp_catalog_windows.json"

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(
        not (CONFIG_PATH.exists() and POLICY_PATH.exists() and WINDOWS_PATH.exists()),
        reason="the review configuration is not in this checkout (release tree)",
    ),
]

MIN_PROVIDERS = 2
INLINE_OVERHEAD = 80000

CONFIG = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
POLICY = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8")) if POLICY_PATH.exists() else {}
CATALOG = json.loads(WINDOWS_PATH.read_text(encoding="utf-8"))["models"] if WINDOWS_PATH.exists() else {}
ROLES = sorted(CONFIG.get("modelRoles", {}))
REVIEWER_ROLES = sorted({reviewer["model"].removeprefix("@") for reviewer in POLICY.get("reviewers", {}).values()})


def _model(target: str) -> str:
    return target.rsplit(":", 1)[0]


def _provider(target: str) -> str:
    return _model(target).split("/", 1)[0]


def _candidates(role: str) -> list[str]:
    primary = CONFIG["modelRoles"][role]
    chains = CONFIG["retry"]["fallbackChains"]
    return [primary, *chains.get(_model(primary), [])]


def _window(target: str) -> int:
    entry = CATALOG[_model(target)]
    return entry["contextWindow"] - entry["maxTokens"]


def test_every_reviewer_role_is_a_configured_role() -> None:
    assert REVIEWER_ROLES
    assert set(REVIEWER_ROLES) <= set(ROLES)


@pytest.mark.parametrize("role", ROLES)
def test_every_role_spans_at_least_two_providers(role: str) -> None:
    providers = {_provider(target) for target in _candidates(role)}
    assert len(providers) >= MIN_PROVIDERS, f"{role} reaches only {sorted(providers)}"


@pytest.mark.parametrize("role", ROLES)
def test_every_candidate_has_a_pinned_catalog_window(role: str) -> None:
    missing = [target for target in _candidates(role) if _model(target) not in CATALOG]
    assert not missing, f"add {missing} to {WINDOWS_PATH.relative_to(ROOT)} from the pinned omp catalog"


@pytest.mark.parametrize("role", REVIEWER_ROLES)
def test_every_reviewer_survives_the_loss_of_any_one_provider(role: str) -> None:
    candidates = _candidates(role)
    for lost in sorted({_provider(target) for target in candidates}):
        survivors = [
            target for target in candidates if _provider(target) != lost and _window(target) >= INLINE_OVERHEAD
        ]
        assert survivors, f"{role} has no candidate with a {INLINE_OVERHEAD} window once {lost} is lost"
