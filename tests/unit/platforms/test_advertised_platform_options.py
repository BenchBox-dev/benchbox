from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

import benchbox.platforms
from benchbox.core.hooks.platform_hooks import PlatformHookRegistry
from benchbox.platforms.manifest import PLATFORM_MANIFEST

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
PLATFORMS_DIR = REPO_ROOT / "benchbox" / "platforms"

ADVERTISED = re.compile(r"--platform-option\s+([A-Za-z_][A-Za-z0-9_]*)\s*=")

PLACEHOLDERS = frozenset({"key", "K", "name", "value", "option"})

KNOWN_MISMATCHES: frozenset[tuple[str, str]] = frozenset()


def _module_to_platform_key() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for entry in PLATFORM_MANIFEST:
        if entry.adapter is None:
            continue
        mapping.setdefault(entry.adapter.module, entry.key)
        try:
            importlib.import_module(entry.adapter.module)
        except Exception:  # pragma: no cover
            pass
    return mapping


def _accepted_option_names(platform: str) -> set[str]:
    names = set(PlatformHookRegistry.list_option_specs(platform))
    for spec in PlatformHookRegistry._option_specs.get(platform, {}).values():
        names.update(getattr(spec, "aliases", ()) or ())
    return names


def _owning_platform(module: str, module_to_key: dict[str, str]) -> str | None:
    parts = module.split(".")
    if parts[-1] == "__init__":
        parts.pop()
    while parts:
        platform = module_to_key.get(".".join(parts))
        if platform is not None:
            return platform
        parts.pop()
    return None


def _advertised_pairs() -> set[tuple[str, str]]:
    module_to_key = _module_to_platform_key()
    pairs: set[tuple[str, str]] = set()
    for path in sorted(PLATFORMS_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        keys = {m.group(1) for m in ADVERTISED.finditer(text)} - PLACEHOLDERS
        if not keys:
            continue
        module = ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts)
        platform = _owning_platform(module, module_to_key)
        if platform is None:
            continue
        allowed = _accepted_option_names(platform)
        pairs |= {(platform, key) for key in keys if key not in allowed}
    return pairs


def test_no_new_adapter_advertises_a_rejected_option() -> None:
    new = _advertised_pairs() - KNOWN_MISMATCHES
    assert not new, (
        "adapter error text or docstring advertises --platform-option keys the CLI rejects:\n  "
        + "\n  ".join(f"{platform}: {key}" for platform, key in sorted(new))
    )


def test_known_mismatch_list_does_not_grow_stale() -> None:
    fixed = KNOWN_MISMATCHES - _advertised_pairs()
    assert not fixed, "these were fixed - delete them from KNOWN_MISMATCHES:\n  " + "\n  ".join(
        f"{platform}: {key}" for platform, key in sorted(fixed)
    )


def test_the_bigquery_case_that_motivated_the_guard_is_fixed() -> None:
    detected = _advertised_pairs()
    assert ("bigquery", "project_id") not in detected
    assert ("bigquery", "biglake_connection") not in detected


def test_the_guard_still_detects_a_rejected_advertised_key(tmp_path: Path) -> None:
    allowed = set(PlatformHookRegistry.list_option_specs("bigquery"))
    rejected_key = "not_a_registered_option"
    assert rejected_key not in allowed, "fixture assumption changed"

    source = tmp_path / "bigquery.py"
    source.write_text(f'"""--platform-option {rejected_key}=<value>"""\n', encoding="utf-8")
    keys = {m.group(1) for m in ADVERTISED.finditer(source.read_text(encoding="utf-8"))}

    assert keys - PLACEHOLDERS - allowed == {rejected_key}


def test_an_accepted_key_is_not_flagged() -> None:
    allowed = set(PlatformHookRegistry.list_option_specs("postgresql"))
    assert "host" in allowed, "fixture assumption changed"
    assert ("postgresql", "host") not in _advertised_pairs()
