#!/usr/bin/env python3

from __future__ import annotations

import sys

import duckdb

from benchbox.utils.duckdb_extensions import install_datasketches

REQUIRED_FAMILIES: dict[str, str] = {
    "theta": "SELECT datasketch_theta(v) FROM (VALUES (1::INTEGER)) t(v)",
    "frequent_items": "SELECT datasketch_frequent_items(8, v) FROM (VALUES ('x')) t(v)",
    "cpc": "SELECT datasketch_cpc(11, v) FROM (VALUES (1::INTEGER)) t(v)",
    "req": "SELECT datasketch_req(12, v) FROM (VALUES (1.0::FLOAT)) t(v)",
    "kll": "SELECT datasketch_kll(200, v) FROM (VALUES (1.0::DOUBLE)) t(v)",
    "hll": "SELECT datasketch_hll(11, v) FROM (VALUES (1::INTEGER)) t(v)",
}

NETWORK_ERROR_HINTS = (
    "Failed to download extension",
    "Could not connect",
    "HTTP",
    "Network",
)

KNOWN_MISSING_FAMILIES_BY_VERSION: dict[str, frozenset[str]] = {
    "2e38607": frozenset({"theta", "frequent_items"}),
}

MISSING_FUNCTION_HINTS = (
    "Scalar Function with name",
    "does not exist",
)


def _is_network_error(message: str) -> bool:
    return any(hint.lower() in message.lower() for hint in NETWORK_ERROR_HINTS)


def _is_known_upstream_drift(version: str, family: str, message: str) -> bool:
    missing_families = KNOWN_MISSING_FAMILIES_BY_VERSION.get(version, frozenset())
    return family in missing_families and all(hint.lower() in message.lower() for hint in MISSING_FUNCTION_HINTS)


def main() -> int:
    con = duckdb.connect(":memory:")

    try:
        install_datasketches(con)
    except duckdb.Error as exc:
        message = str(exc)
        if _is_network_error(message):
            print(f"network: SKIP ({message.splitlines()[0]})")
            return 0
        print(f"install: FAIL ({message.splitlines()[0]})")
        return 1

    version_row = con.execute(
        "SELECT extension_version FROM duckdb_extensions() WHERE extension_name = 'datasketches'"
    ).fetchone()
    version = version_row[0] if version_row else "unknown"
    print(f"extension_version: {version}")

    failures: list[str] = []
    known_drift: list[str] = []
    for family, probe in REQUIRED_FAMILIES.items():
        try:
            con.execute(probe)
            print(f"{family}: OK")
        except duckdb.Error as exc:
            line = str(exc).splitlines()[0]
            if _is_known_upstream_drift(version, family, line):
                print(f"{family}: KNOWN-DRIFT ({line})")
                known_drift.append(family)
                continue
            print(f"{family}: FAIL ({line})")
            failures.append(family)

    if known_drift:
        print(f"known_drift: {', '.join(known_drift)} missing in reviewed extension version {version}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
