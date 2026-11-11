from __future__ import annotations

import pytest

from benchbox.core.results.loader import _extract_cost_summary
from benchbox.core.results.schema import _normalized_cost_allows_direct_total

pytestmark = [pytest.mark.unit, pytest.mark.fast]


class TestExtractCostSummaryPreservesNormalizedCost:
    def test_returns_normalized_cost_when_present(self) -> None:
        normalized = {
            "cost_status": "normalized",
            "normalized_cost_usd": 0.42,
            "cost_model_version": "2026.05.0",
        }
        summary = _extract_cost_summary(
            {"total_usd": 0.42, "model": "estimated"},
            normalized,
        )
        assert summary == {
            "total_cost": 0.42,
            "cost_model": "estimated",
            "normalized_cost": normalized,
        }

    def test_omits_normalized_cost_when_legacy_bundle_has_none(self) -> None:
        summary = _extract_cost_summary({"total_usd": 0.99, "model": "estimated"}, None)
        assert summary == {"total_cost": 0.99, "cost_model": "estimated"}
        assert "normalized_cost" not in summary

    def test_returns_none_for_empty_cost_section(self) -> None:
        assert _extract_cost_summary({}, None) is None
        assert _extract_cost_summary({}, {"cost_status": "normalized"}) is None

    def test_preserves_scan_bytes_without_creating_direct_cost_fields(self) -> None:
        scan_bytes = {"total_bytes_scanned": 512}

        assert _extract_cost_summary({}, None, scan_bytes) == {"scan_bytes": scan_bytes}


class TestNormalizedCostAllowsDirectTotal:
    def test_missing_block_allows_direct_total_for_legacy_bundles(self) -> None:
        assert _normalized_cost_allows_direct_total(None) is True

    def test_normalized_status_allows_direct_total(self) -> None:
        assert _normalized_cost_allows_direct_total({"cost_status": "normalized", "normalized_cost_usd": 1.23}) is True

    def test_not_applicable_local_status_allows_direct_total(self) -> None:
        assert (
            _normalized_cost_allows_direct_total({"cost_status": "not_applicable_local", "normalized_cost_usd": 0.0})
            is True
        )

    def test_unavailable_status_blocks_direct_total(self) -> None:
        assert (
            _normalized_cost_allows_direct_total({"cost_status": "unavailable", "normalized_cost_usd": None}) is False
        )

    def test_normalized_without_amount_blocks_direct_total(self) -> None:
        assert _normalized_cost_allows_direct_total({"cost_status": "normalized", "normalized_cost_usd": None}) is False

    def test_non_dict_non_none_value_blocks_direct_total(self) -> None:

        assert _normalized_cost_allows_direct_total("normalized") is False
        assert _normalized_cost_allows_direct_total(42) is False


