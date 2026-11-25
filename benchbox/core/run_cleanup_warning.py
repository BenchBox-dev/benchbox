from __future__ import annotations

from typing import Any

from benchbox.core.platform_manifest import (
    PLATFORM_MANIFEST,
    get_all_platform_aliases,
    get_platform_manifest_entry,
)
from benchbox.utils.run_deadline import remote_cleanup_warning


def cloud_cleanup_warning(platform: str | None, mode: str | None = None) -> str | None:
    if not platform:
        return None
    key = str(platform).lower()
    entry = get_platform_manifest_entry(get_all_platform_aliases().get(key, key))
    if entry is None:
        return None
    if mode == "dataframe":
        dataframe_entry = get_platform_manifest_entry(f"{entry.key}-df")
        if dataframe_entry is not None:
            entry = dataframe_entry
    return _entry_cleanup_warning(entry)


def adapter_cleanup_warning(adapter: Any) -> str | None:
    class_name = type(adapter).__name__
    for entry in PLATFORM_MANIFEST:
        if entry.adapter is not None and entry.adapter.class_name == class_name:
            return _entry_cleanup_warning(entry)
    return None


def _entry_cleanup_warning(entry: Any) -> str | None:
    metadata = entry.metadata_dict()
    if metadata.get("category") != "cloud":
        return None
    return remote_cleanup_warning(
        str(metadata.get("display_name") or entry.key), bool(entry.capabilities.get("run_timeout_cleanup", False))
    )
