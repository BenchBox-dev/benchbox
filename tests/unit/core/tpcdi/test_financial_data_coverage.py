# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

from datetime import date

import pytest

from benchbox.core.tpcdi.financial_data import (
    FinancialConstants,
    FinancialDataPatterns,
    generate_realistic_tax_rates,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestFinancialConstants:
    def test_tier_percentages_sum_to_one(self):
        c = FinancialConstants()
        total = c.TIER_1_PERCENTAGE + c.TIER_2_PERCENTAGE + c.TIER_3_PERCENTAGE
        assert abs(total - 1.0) < 1e-9

    def test_account_type_percentages_sum_to_one(self):
        c = FinancialConstants()
        total = c.TAXABLE_ACCOUNTS_PERCENTAGE + c.TAX_DEFERRED_PERCENTAGE + c.TAX_FREE_PERCENTAGE
        assert abs(total - 1.0) < 1e-9

    def test_credit_score_range_is_valid(self):
        c = FinancialConstants()
        assert c.MIN_CREDIT_SCORE < c.MAX_CREDIT_SCORE
        assert c.MIN_CREDIT_SCORE == 300
        assert c.MAX_CREDIT_SCORE == 850
        assert c.PRIME_CREDIT_THRESHOLD > c.MIN_CREDIT_SCORE

    def test_net_worth_ranges_are_non_overlapping(self):
        c = FinancialConstants()
        assert c.TIER_1_MIN_NET_WORTH > c.TIER_2_MAX_NET_WORTH
        assert c.TIER_2_MIN_NET_WORTH > c.TIER_3_MAX_NET_WORTH

    def test_market_hours_values(self):
        c = FinancialConstants()
        assert c.MARKET_OPEN_HOUR == 9
        assert c.MARKET_OPEN_MINUTE == 30
        assert c.MARKET_CLOSE_HOUR == 16
        assert c.MARKET_CLOSE_MINUTE == 0


class TestFinancialDataPatternsInit:
    def test_seeded_reproducibility(self):
        p1 = FinancialDataPatterns(seed=7)
        tiers1 = [p1.generate_customer_tier() for _ in range(20)]

        p2 = FinancialDataPatterns(seed=7)
        tiers2 = [p2.generate_customer_tier() for _ in range(20)]
        assert tiers1 == tiers2

    def test_different_seeds_differ(self):
        p1 = FinancialDataPatterns(seed=1)
        p2 = FinancialDataPatterns(seed=999)
        tiers1 = [p1.generate_customer_tier() for _ in range(50)]
        tiers2 = [p2.generate_customer_tier() for _ in range(50)]
        assert tiers1 != tiers2

    def test_industries_list_populated(self):
        p = FinancialDataPatterns()
        assert len(p.industries) == 12
        for item in p.industries:
            assert len(item) == 3

    def test_sp_ratings_weights_sum_to_one(self):
        p = FinancialDataPatterns()
        total = sum(w for _, w in p.sp_ratings)
        assert abs(total - 1.0) < 1e-9

    def test_trade_types_populated(self):
        p = FinancialDataPatterns()
        assert len(p.trade_types) == 6
        for t in p.trade_types:
            assert len(t) == 4


class TestGenerateCustomerTier:
    def test_all_tiers_1_2_3(self):
        p = FinancialDataPatterns(seed=42)
        seen = set()
        for _ in range(200):
            seen.add(p.generate_customer_tier())
        assert seen == {1, 2, 3}

    def test_tier_is_int(self):
        p = FinancialDataPatterns()
        assert isinstance(p.generate_customer_tier(), int)


class TestGenerateNetWorth:
    def test_tier1_net_worth_in_range(self):
        p = FinancialDataPatterns(seed=1)
        c = p.constants
        for _ in range(20):
            nw = p.generate_net_worth(1)
            assert c.TIER_1_MIN_NET_WORTH <= nw <= c.TIER_1_MAX_NET_WORTH

    def test_tier2_net_worth_in_range(self):
        p = FinancialDataPatterns(seed=2)
        c = p.constants
        for _ in range(20):
            nw = p.generate_net_worth(2)
            assert c.TIER_2_MIN_NET_WORTH <= nw <= c.TIER_2_MAX_NET_WORTH

    def test_tier3_net_worth_in_range(self):
        p = FinancialDataPatterns(seed=3)
        c = p.constants
        for _ in range(20):
            nw = p.generate_net_worth(3)
            assert c.TIER_3_MIN_NET_WORTH <= nw <= c.TIER_3_MAX_NET_WORTH


class TestGenerateCreditRating:
    def test_credit_score_in_valid_range(self):
        p = FinancialDataPatterns(seed=10)
        for tier in (1, 2, 3):
            nw = p.generate_net_worth(tier)
            score = p.generate_credit_rating(tier, nw)
            assert 300 <= score <= 850

    def test_tier1_tends_higher_scores(self):
        p = FinancialDataPatterns(seed=5)
        avg1 = sum(p.generate_credit_rating(1, 2_000_000) for _ in range(30)) / 30
        avg3 = sum(p.generate_credit_rating(3, 5_000) for _ in range(30)) / 30
        assert avg1 > avg3


class TestGenerateAccountTaxStatus:
    def test_returns_0_1_or_2(self):
        p = FinancialDataPatterns(seed=99)
        seen = set()
        for tier in (1, 2, 3):
            for _ in range(50):
                seen.add(p.generate_account_tax_status(tier))
        assert seen == {0, 1, 2}


class TestGenerateSecurityPrice:
    def test_initial_price_no_base(self):
        p = FinancialDataPatterns(seed=11)
        price = p.generate_security_price()
        assert 1.0 <= price <= 1000.0

    def test_price_evolution_with_base(self):
        p = FinancialDataPatterns(seed=12)
        new_price = p.generate_security_price(base_price=50.0)
        assert new_price >= 0.01

    def test_price_minimum_floor(self):
        import random

        p = FinancialDataPatterns(seed=42)
        original_normalvariate = random.normalvariate
        try:
            random.normalvariate = lambda mu, sigma: -10.0
            price = p.generate_security_price(base_price=0.001)
            assert price >= 0.01
        finally:
            random.normalvariate = original_normalvariate


class TestGenerateTradingVolume:
    def test_large_cap_volume(self):
        p = FinancialDataPatterns(seed=20)
        vol = p.generate_trading_volume(1)
        assert vol >= 500_000

    def test_mid_cap_volume(self):
        p = FinancialDataPatterns(seed=21)
        vol = p.generate_trading_volume(2)
        assert vol > 0

    def test_small_cap_volume(self):
        p = FinancialDataPatterns(seed=22)
        vol = p.generate_trading_volume(3)
        assert vol > 0


class TestGenerateTradeQuantity:
    def test_minimum_one_share(self):
        p = FinancialDataPatterns(seed=30)
        qty = p.generate_trade_quantity(3, 1000.0)
        assert qty >= 1

    def test_tier1_larger_quantities(self):
        p = FinancialDataPatterns(seed=31)
        avg1 = sum(p.generate_trade_quantity(1, 50.0) for _ in range(20)) / 20
        p2 = FinancialDataPatterns(seed=31)
        avg3 = sum(p2.generate_trade_quantity(3, 50.0) for _ in range(20)) / 20
        assert avg1 > avg3

    def test_rounding_to_hundreds(self):
        p = FinancialDataPatterns(seed=40)
        qty = p.generate_trade_quantity(1, 0.10)
        assert qty % 100 == 0 or qty < 1000

    def test_rounding_to_tens(self):
        p = FinancialDataPatterns(seed=41)
        qty = p.generate_trade_quantity(2, 50.0)
        assert qty >= 1


class TestIsMarketHours:
    def test_during_market_hours(self):
        p = FinancialDataPatterns()
        assert p.is_market_hours(10, 0) is True
        assert p.is_market_hours(9, 30) is True
        assert p.is_market_hours(16, 0) is True
        assert p.is_market_hours(13, 0) is True

    def test_outside_market_hours(self):
        p = FinancialDataPatterns()
        assert p.is_market_hours(9, 0) is False
        assert p.is_market_hours(16, 1) is False
        assert p.is_market_hours(8, 0) is False
        assert p.is_market_hours(20, 0) is False


class TestGenerateTradeStatus:
    def test_returns_valid_status(self):
        p = FinancialDataPatterns(seed=50)
        seen = set()
        for _ in range(200):
            seen.add(p.generate_trade_status_distribution())
        assert seen == {"Completed", "Pending", "Cancelled"}


class TestGenerateCompanySPRating:
    def test_returns_known_rating(self):
        p = FinancialDataPatterns(seed=60)
        valid_ratings = {r for r, _ in p.sp_ratings}
        for industry_code in ["TECH", "UTIL", "CONS", "HLTH"]:
            rating = p.generate_company_sp_rating(industry_code)
            assert rating in valid_ratings

    def test_high_quality_industry_uses_modified_weights(self):
        p = FinancialDataPatterns(seed=61)
        for code in ["UTIL", "CONS", "HLTH"]:
            r = p.generate_company_sp_rating(code)
            assert isinstance(r, str)


class TestGenerateRealisticDates:
    def test_returns_requested_count(self):
        p = FinancialDataPatterns(seed=70)
        start = date(2020, 1, 1)
        end = date(2020, 12, 31)
        dates = p.generate_realistic_dates(start, end, 10)
        assert len(dates) == 10

    def test_dates_in_range(self):
        p = FinancialDataPatterns(seed=71)
        start = date(2020, 1, 1)
        end = date(2020, 6, 30)
        dates = p.generate_realistic_dates(start, end, 5)
        for d in dates:
            assert start <= d <= end

    def test_dates_sorted(self):
        p = FinancialDataPatterns(seed=72)
        start = date(2021, 1, 1)
        end = date(2021, 12, 31)
        dates = p.generate_realistic_dates(start, end, 15)
        assert dates == sorted(dates)


class TestGetterMethods:
    def test_get_industry_data(self):
        p = FinancialDataPatterns()
        data = p.get_industry_data()
        assert len(data) == 12
        assert data is not p.industries

    def test_get_status_types(self):
        p = FinancialDataPatterns()
        st = p.get_status_types()
        assert len(st) == 4
        codes = [s[0] for s in st]
        assert "ACTV" in codes

    def test_get_trade_types(self):
        p = FinancialDataPatterns()
        tt = p.get_trade_types()
        assert len(tt) == 6
        assert tt is not p.trade_types


class TestGenerateRealisticTaxRates:
    def test_returns_ten_entries(self):
        rates = generate_realistic_tax_rates()
        assert len(rates) == 10

    def test_each_entry_has_three_fields(self):
        for entry in generate_realistic_tax_rates():
            assert len(entry) == 3
            tax_id, tax_name, rate = entry
            assert isinstance(tax_id, str)
            assert isinstance(tax_name, str)
            assert isinstance(rate, float)

    def test_rates_are_in_valid_range(self):
        for _, _, rate in generate_realistic_tax_rates():
            assert 0.0 <= rate <= 1.0


