# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from benchbox.core.platform_manifest import PLATFORM_MANIFEST
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.platforms.base.adapter import PlatformAdapter
from benchbox.platforms.base.connection_wrappers import (
    StreamConnectionCapability,
    resolve_stream_connection_capability,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

SNAPSHOT_PATH = Path(__file__).with_name("throughput_session_capability_snapshot.json")
REFRESH_ENV_VAR = "UPDATE_THROUGHPUT_SNAPSHOT"


def _sql_adapter_keys() -> list[str]:
    return sorted(
        entry.key for entry in PLATFORM_MANIFEST if entry.capabilities.get("supports_sql") and entry.adapter is not None
    )


def _load_adapter_classes() -> dict[str, type]:
    classes: dict[str, type] = {}
    unexpected: list[str] = []
    for key in _sql_adapter_keys():
        try:
            classes[key] = PlatformRegistry.get_adapter_class(key)
        except Exception as exc:
            unexpected.append(f"{key}: {type(exc).__name__}: {exc}")
    assert not unexpected, "Manifest adapters failed to load: " + "; ".join(unexpected)
    return classes


def _resolve_entry(key: str) -> dict[str, object]:
    try:
        adapter_cls = PlatformRegistry.get_adapter_class(key)
    except Exception as exc:
        return {"key": key, "status": "unavailable", "detail": f"{type(exc).__name__}: {exc}"}
    try:
        capability, declared = resolve_stream_connection_capability(adapter_cls)
    except RuntimeError as exc:
        return {"key": key, "status": "invalid", "detail": str(exc)}
    record: dict[str, object] = {
        "key": key,
        "status": "resolved",
        "capability": capability.value,
        "declared": declared,
        "declaration_site": "benchbox.core.platform_manifest",
        "adapter": f"{adapter_cls.__module__}.{adapter_cls.__name__}",
    }
    if capability is StreamConnectionCapability.UNSUPPORTED:
        record["gate"] = "reject"
    elif capability is StreamConnectionCapability.INDEPENDENT_CONNECTION and (
        adapter_cls.new_stream_connection is PlatformAdapter.new_stream_connection
    ):
        record["gate"] = "missing-override"
    else:
        record["gate"] = "pass"
    return record


def _check_hook_rule(adapter_cls: type) -> str | None:

    capability, _declared = resolve_stream_connection_capability(adapter_cls)
    if capability is not StreamConnectionCapability.INDEPENDENT_CONNECTION:
        return None
    defines_connect = "create_connection" in adapter_cls.__dict__
    declares_value = "new_stream_connection" in adapter_cls.__dict__
    defines_hook = "_apply_stream_session_state" in adapter_cls.__dict__
    if defines_connect and not declares_value and not defines_hook:
        return (
            f"{adapter_cls.__name__} inherits INDEPENDENT_CONNECTION and defines its own "
            "create_connection() without _apply_stream_session_state(): prove per-stream "
            "session parity with an explicit hook override (a documented no-op when the "
            "subclass adds only one-time setup), or declare the capability directly with "
            "its own new_stream_connection() override."
        )
    return None


class TestThroughputSessionCapabilitySweep:
    def test_snapshot_matches_manifest_resolution(self):
        records = [_resolve_entry(key) for key in _sql_adapter_keys()]
        snapshot = {record["key"]: record for record in records}
        if os.environ.get(REFRESH_ENV_VAR) == "1":
            SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
            pytest.skip(f"Refreshed {SNAPSHOT_PATH.name}; re-run without {REFRESH_ENV_VAR}=1 to verify.")
        assert SNAPSHOT_PATH.exists(), (
            f"Missing capability snapshot {SNAPSHOT_PATH.name}: run with {REFRESH_ENV_VAR}=1 to create it, "
            "review every entry, then commit the snapshot."
        )
        expected = json.loads(SNAPSHOT_PATH.read_text())
        assert snapshot == expected, (
            "Throughput session capability resolution drifted from the pinned snapshot. "
            "New adapters, changed declarations, or newly importable adapters must be reviewed: "
            f"diff against {SNAPSHOT_PATH.name}, confirm each entry is deliberate, then refresh with "
            f"{REFRESH_ENV_VAR}=1 and commit the updated snapshot."
        )

    def test_every_entry_is_declared_and_usable(self):
        records = [_resolve_entry(key) for key in _sql_adapter_keys()]
        bad = [
            r
            for r in records
            if r.get("status") != "resolved" or not r.get("declared") or r.get("gate") == "missing-override"
        ]
        assert not bad, (
            "Adapters without a deliberate, usable capability: "
            + "; ".join(f"{r['key']} ({r.get('status')}/{r.get('gate')}: {r.get('detail', '')})" for r in bad)
            + ". UNSUPPORTED declarations are allowed (they fail closed at runtime); "
            "'invalid' and 'missing-override' entries must be fixed in the adapter."
        )

    def test_inherited_independent_requires_stream_state_hook(self):

        failures = []
        for adapter_cls in _load_adapter_classes().values():
            error = _check_hook_rule(adapter_cls)
            if error is not None:
                failures.append(error)
        assert not failures, "\n".join(failures)

    def test_unsupported_entries_fail_closed_at_runtime(self):

        from benchbox.platforms.base.connection_wrappers import require_throughput_stream_capability

        records = [_resolve_entry(key) for key in _sql_adapter_keys()]
        for record in records:
            if record.get("capability") != StreamConnectionCapability.UNSUPPORTED.value:
                continue
            adapter_cls = PlatformRegistry.get_adapter_class(record["key"])
            with pytest.raises(RuntimeError, match="UNSUPPORTED"):
                require_throughput_stream_capability(adapter_cls, platform_name=record["key"])


class TestCapabilitySingleSource:
    def test_adapter_attribute_matches_the_pinned_manifest_declarations(self):
        pinned = json.loads(SNAPSHOT_PATH.read_text())
        manifest = {entry.key: entry for entry in PLATFORM_MANIFEST if entry.adapter is not None}
        mismatches = []
        for key, adapter_cls in _load_adapter_classes().items():
            from_manifest = StreamConnectionCapability(manifest[key].adapter.stream_connection_capability)
            from_snapshot = StreamConnectionCapability(pinned[key]["capability"])
            actual = adapter_cls.stream_connection_capability
            if not (actual is from_manifest is from_snapshot):
                mismatches.append(f"{key}: class {actual}, manifest {from_manifest}, pinned {from_snapshot}")
        assert not mismatches, "\n".join(mismatches)

    def test_manifest_adapters_do_not_redeclare_the_capability(self):
        redeclared = []
        for key, adapter_cls in _load_adapter_classes().items():
            for klass in adapter_cls.__mro__:
                if klass is PlatformAdapter:
                    break
                if "stream_connection_capability" in vars(klass):
                    redeclared.append(f"{key}: {klass.__module__}.{klass.__qualname__}")
        assert not redeclared, (
            "Declare stream_connection_capability only in the platform manifest; found class attributes on: "
            + "; ".join(redeclared)
        )

    def test_base_stream_dispatch_follows_the_manifest(self):
        for key, adapter_cls in _load_adapter_classes().items():
            expected = StreamConnectionCapability(_resolve_entry(key)["capability"])
            instance = object.__new__(adapter_cls)
            assert instance.stream_connection_capability is expected
            if expected is StreamConnectionCapability.INDEPENDENT_CONNECTION:
                assert adapter_cls.new_stream_connection is not PlatformAdapter.new_stream_connection

    def test_subclass_of_a_manifest_adapter_inherits_its_capability(self):
        from benchbox.platforms.postgresql import PostgreSQLAdapter

        class _MyPostgres(PostgreSQLAdapter):
            pass

        assert resolve_stream_connection_capability(_MyPostgres) == (
            StreamConnectionCapability.INDEPENDENT_CONNECTION,
            True,
        )
        assert _MyPostgres.stream_connection_capability is StreamConnectionCapability.INDEPENDENT_CONNECTION

    def test_subclass_declaration_overrides_the_inherited_manifest_capability(self):
        from benchbox.platforms.postgresql import PostgreSQLAdapter

        class _SharedPostgres(PostgreSQLAdapter):
            stream_connection_capability = StreamConnectionCapability.SHARED_CURSOR

        assert resolve_stream_connection_capability(_SharedPostgres) == (
            StreamConnectionCapability.SHARED_CURSOR,
            True,
        )


