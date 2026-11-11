# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for
# details.

from __future__ import annotations

import pytest

from benchbox.validation.bundle import (
    APPLIED_RECEIPT_MAX_ENTRIES,
    ValidationResult,
    _validate_platform_section,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _platform(applied: dict) -> dict:
    return {"name": "duckdb", "tuning": {"applied": applied}}


def _validate(platform: dict) -> ValidationResult:
    vr = ValidationResult(path="bundle.json")
    _validate_platform_section(platform, vr)
    return vr


class TestInlineAppliedLedgerBounds:
    def test_oversized_statement_list_is_rejected(self) -> None:
        over = APPLIED_RECEIPT_MAX_ENTRIES + 1
        vr = _validate(_platform({"statements": [{"phase": "ddl"}] * over}))

        assert not vr.ok
        assert any("platform.tuning.applied.statements" in error for error in vr.errors)

    def test_oversized_receipt_entry_list_is_rejected(self) -> None:
        over = APPLIED_RECEIPT_MAX_ENTRIES + 1
        vr = _validate(_platform({"receipt": {"entries": [{"kind": "index"}] * over}}))

        assert not vr.ok
        assert any("platform.tuning.applied.receipt.entries" in error for error in vr.errors)

    def test_ledger_within_the_cap_passes(self) -> None:
        vr = _validate(
            _platform(
                {
                    "status": "applied_unverified",
                    "statements": [{"phase": "ddl", "statement_redacted": True}],
                    "receipt": {"entries": [{"kind": "index", "verdict": "match"}]},
                }
            )
        )

        assert vr.ok

    @pytest.mark.parametrize("tuning", [None, {}, {"applied": None}, {"applied": "not-a-dict"}])
    def test_absent_or_malformed_blocks_are_not_errors_here(self, tuning: object) -> None:
        platform: dict = {"name": "duckdb"}
        if tuning is not None:
            platform["tuning"] = tuning

        assert _validate(platform).ok


class TestBoundsCoverEveryLedgerArray:
    @pytest.mark.parametrize(
        "applied",
        [
            pytest.param({"dropped": [{"redacted": True}] * (APPLIED_RECEIPT_MAX_ENTRIES + 1)}, id="dropped"),
            pytest.param(
                {"receipt": {"observed": [{"kind": "index"}] * (APPLIED_RECEIPT_MAX_ENTRIES + 1)}},
                id="receipt.observed",
            ),
            pytest.param(
                {"receipt": {"dropped": [{"redacted": True}] * (APPLIED_RECEIPT_MAX_ENTRIES + 1)}},
                id="receipt.dropped",
            ),
            pytest.param(
                {"drift_check": {"errors": ["x"] * (APPLIED_RECEIPT_MAX_ENTRIES + 1)}},
                id="drift_check.errors",
            ),
            pytest.param(
                {"drift_check": {"missing_tables": ["t"] * (APPLIED_RECEIPT_MAX_ENTRIES + 1)}},
                id="drift_check.missing_tables",
            ),
        ],
    )
    def test_every_unbounded_array_is_rejected(self, applied: dict) -> None:
        vr = _validate(_platform(applied))

        assert not vr.ok
        assert any("exceeds the" in error for error in vr.errors)

    def test_few_entries_holding_huge_strings_are_rejected(self) -> None:
        vr = _validate(_platform({"statements": [{"phase": "ddl", "note": "x" * (3 * 1024 * 1024)}] * 4}))

        assert not vr.ok
        assert any("byte limit" in error for error in vr.errors)


