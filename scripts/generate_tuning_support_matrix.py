from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from benchbox.core.tuning.capability_registry import (
    PLATFORM_ALIASES,
    PLATFORM_TUNING_CAPABILITIES,
    SESSION_CONFIG_CAPABILITIES,
    is_not_rendered_at_execution,
)
from benchbox.core.tuning.packaged_templates import list_packaged_templates

TARGET = REPO_ROOT / "docs" / "reference" / "tuning-platform-support.md"

HEADER = """# Tuning Platform Support

This page lists, for each platform in the tuning capability registry, what a
tuning file can change at execution and whether a tuned run can be verified
against the live catalog. It is generated from
`benchbox/core/tuning/capability_registry.py`, the packaged tuning templates,
and each adapter's catalog introspector by
`scripts/generate_tuning_support_matrix.py` (`make tuning-support-matrix`);
a test fails when the page drifts from those sources, so do not edit it by
hand.

- **Shipped templates**: benchmarks with a packaged `*_tuned.yaml` template,
  used by `--tuning tuned`. Platforms without one use the generic fallback.
- **Applied at execution**: tuning types the platform renders when tables are
  created or loaded. Requesting any other type fails validation.
- **Not applied**: tuning types the registry lists but the platform does not
  apply; requesting one fails validation with the registry's reason.
- **Catalog introspector**: whether the adapter reads the catalog after load
  to corroborate the applied-tuning ledger.
- **Can reach `applied_verified`**: yes only when the platform has a catalog
  introspector and applies at least one tuning type. Other platforms report
  at most `applied_unverified`.

Aliases resolve to another platform's entry: {aliases}.

| Platform | Shipped templates | Applied at execution | Not applied | Spark session map | Catalog introspector | Can reach `applied_verified` |
| --- | --- | --- | --- | --- | --- | --- |
"""


_ADAPTER_NAMES = {"azure-synapse": "synapse", "fabric-warehouse": "fabric_dw"}


def _adapter_has_introspector(platform: str) -> bool | None:
    from benchbox.core.platform_registry import PlatformRegistry
    from benchbox.platforms.base.adapter import PlatformAdapter

    for candidate in (_ADAPTER_NAMES.get(platform, platform), platform.replace("-", "_"), platform.split("-", 1)[-1]):
        try:
            adapter_class = PlatformRegistry.get_adapter_class(candidate)
        except Exception:
            continue
        return adapter_class.get_tuning_introspector is not PlatformAdapter.get_tuning_introspector
    return None


def _templates(platform: str) -> str:
    paths = list_packaged_templates(platform).get(platform, [])
    names = sorted({path.name.removesuffix("_tuned.yaml") for path in paths if path.name.endswith("_tuned.yaml")})
    return ", ".join(names) if names else "none"


def _types(platform: str, *, applied: bool) -> str:
    names = []
    for tuning_type, capability in sorted(
        PLATFORM_TUNING_CAPABILITIES.get(platform, {}).items(), key=lambda item: item[0].value
    ):
        if is_not_rendered_at_execution(capability) == applied:
            continue
        label = tuning_type.value
        if applied and capability.requires_sorted_ingestion:
            label += " (with sorted ingestion)"
        names.append(label)
    return ", ".join(names) if names else "none"


def render() -> str:
    aliases = ", ".join(f"`{alias}` -> `{target}`" for alias, target in sorted(PLATFORM_ALIASES.items()))
    lines = [HEADER.format(aliases=aliases)]
    for platform in sorted(PLATFORM_TUNING_CAPABILITIES.keys() | SESSION_CONFIG_CAPABILITIES.keys()):
        applied = _types(platform, applied=True)
        introspector = _adapter_has_introspector(platform)
        introspector_text = {True: "yes", False: "no", None: "no adapter"}[introspector]
        verifiable = "yes" if introspector and applied != "none" else "no"
        session = "applied" if platform in SESSION_CONFIG_CAPABILITIES else "-"
        lines.append(
            f"| `{platform}` | {_templates(platform)} | {applied} | {_types(platform, applied=False)} | "
            f"{session} | {introspector_text} | {verifiable} |\n"
        )
    return "".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the tuning platform support matrix.")
    parser.add_argument("--check", action="store_true", help="Fail when the committed page is out of date.")
    args = parser.parse_args()
    content = render()
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != content:
            print(f"{TARGET.relative_to(REPO_ROOT)} is out of date; run make tuning-support-matrix", file=sys.stderr)
            return 1
        return 0
    TARGET.write_text(content, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
