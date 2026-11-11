# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import numpy as np
import pytest

from benchbox.core.tpch_skew.distributions import (
    ExponentialDistribution,
    NormalDistribution,
    UniformDistribution,
    ZipfianDistribution,
    create_distribution,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestZipfianDistribution:
    def test_initialization(self):

        dist = ZipfianDistribution(s=1.0, num_elements=1000)
        assert dist.s == 1.0
        assert dist.num_elements == 1000

    def test_invalid_s_parameter(self):

        with pytest.raises(ValueError, match="non-negative"):
            ZipfianDistribution(s=-0.5, num_elements=1000)

    def test_invalid_num_elements(self):

        with pytest.raises(ValueError, match="positive"):
            ZipfianDistribution(s=1.0, num_elements=0)

    def test_sample_size(self):

        dist = ZipfianDistribution(s=1.0, num_elements=100)
        rng = np.random.default_rng(42)
        samples = dist.sample(1000, rng)
        assert len(samples) == 1000

    def test_sample_range(self):
        dist = ZipfianDistribution(s=1.0, num_elements=100)
        rng = np.random.default_rng(42)
        samples = dist.sample(1000, rng)
        assert np.all(samples >= 0)
        assert np.all(samples <= 1)

    def test_skew_factor_calculation(self):

        dist_low = ZipfianDistribution(s=0.0, num_elements=100)
        dist_high = ZipfianDistribution(s=2.0, num_elements=100)

        assert dist_low.get_skew_factor() == 0.0
        assert dist_high.get_skew_factor() == 1.0

    def test_description(self):

        dist = ZipfianDistribution(s=1.5, num_elements=500)
        desc = dist.get_description()
        assert "Zipfian" in desc
        assert "1.5" in desc
        assert "500" in desc

    def test_uniform_case(self):
        dist = ZipfianDistribution(s=0.0, num_elements=100)
        rng = np.random.default_rng(42)
        samples = dist.sample(10000, rng)
        assert np.std(samples) > 0.2

    def test_map_to_range(self):

        dist = ZipfianDistribution(s=1.0, num_elements=100)
        samples = np.array([0.0, 0.5, 0.99])
        mapped = dist.map_to_range(samples, 1, 100)
        assert mapped[0] == 1
        assert 1 <= mapped[1] <= 100
        assert mapped[2] == 100


class TestNormalDistribution:
    def test_initialization(self):

        dist = NormalDistribution(mean=0.5, std=0.15)
        assert dist.mean == 0.5
        assert dist.std == 0.15

    def test_invalid_mean(self):

        with pytest.raises(ValueError, match="0, 1"):
            NormalDistribution(mean=1.5, std=0.15)
        with pytest.raises(ValueError, match="0, 1"):
            NormalDistribution(mean=-0.1, std=0.15)

    def test_invalid_std(self):

        with pytest.raises(ValueError, match="positive"):
            NormalDistribution(mean=0.5, std=0)
        with pytest.raises(ValueError, match="positive"):
            NormalDistribution(mean=0.5, std=-0.1)

    def test_sample_range(self):
        dist = NormalDistribution(mean=0.5, std=0.5)
        rng = np.random.default_rng(42)
        samples = dist.sample(10000, rng)
        assert np.all(samples >= 0)
        assert np.all(samples <= 1)

    def test_sample_centered(self):

        dist = NormalDistribution(mean=0.3, std=0.1)
        rng = np.random.default_rng(42)
        samples = dist.sample(10000, rng)
        assert abs(np.mean(samples) - 0.3) < 0.05

    def test_skew_factor(self):

        dist_low = NormalDistribution(mean=0.5, std=0.5)
        dist_high = NormalDistribution(mean=0.5, std=0.05)

        assert dist_low.get_skew_factor() == 0.0
        assert dist_high.get_skew_factor() > 0.8

    def test_description(self):

        dist = NormalDistribution(mean=0.6, std=0.2)
        desc = dist.get_description()
        assert "Normal" in desc
        assert "0.6" in desc
        assert "0.2" in desc


class TestExponentialDistribution:
    def test_initialization(self):

        dist = ExponentialDistribution(rate=3.0)
        assert dist.rate == 3.0

    def test_invalid_rate(self):

        with pytest.raises(ValueError, match="positive"):
            ExponentialDistribution(rate=0)
        with pytest.raises(ValueError, match="positive"):
            ExponentialDistribution(rate=-1.0)

    def test_sample_range(self):
        dist = ExponentialDistribution(rate=3.0)
        rng = np.random.default_rng(42)
        samples = dist.sample(10000, rng)
        assert np.all(samples >= 0)
        assert np.all(samples < 1)

    def test_skew_factor(self):

        dist_low = ExponentialDistribution(rate=0.5)
        dist_high = ExponentialDistribution(rate=5.0)

        assert dist_low.get_skew_factor() == 0.1
        assert dist_high.get_skew_factor() == 1.0

    def test_description(self):

        dist = ExponentialDistribution(rate=2.5)
        desc = dist.get_description()
        assert "Exponential" in desc
        assert "2.5" in desc


class TestUniformDistribution:
    def test_sample_range(self):
        dist = UniformDistribution()
        rng = np.random.default_rng(42)
        samples = dist.sample(10000, rng)
        assert np.all(samples >= 0)
        assert np.all(samples <= 1)

    def test_sample_uniformity(self):

        dist = UniformDistribution()
        rng = np.random.default_rng(42)
        samples = dist.sample(10000, rng)
        assert abs(np.mean(samples) - 0.5) < 0.02

    def test_skew_factor(self):
        dist = UniformDistribution()
        assert dist.get_skew_factor() == 0.0

    def test_description(self):

        dist = UniformDistribution()
        assert dist.get_description() == "Uniform"


class TestCreateDistribution:
    def test_create_zipfian(self):

        dist = create_distribution("zipfian", s=1.5, num_elements=100)
        assert isinstance(dist, ZipfianDistribution)
        assert dist.s == 1.5

    def test_create_normal(self):

        dist = create_distribution("normal", mean=0.4, std=0.2)
        assert isinstance(dist, NormalDistribution)
        assert dist.mean == 0.4

    def test_create_exponential(self):

        dist = create_distribution("exponential", rate=2.0)
        assert isinstance(dist, ExponentialDistribution)
        assert dist.rate == 2.0

    def test_create_uniform(self):

        dist = create_distribution("uniform")
        assert isinstance(dist, UniformDistribution)

    def test_case_insensitive(self):

        dist1 = create_distribution("ZIPFIAN", s=1.0, num_elements=100)
        dist2 = create_distribution("Zipfian", s=1.0, num_elements=100)
        assert isinstance(dist1, ZipfianDistribution)
        assert isinstance(dist2, ZipfianDistribution)

    def test_unknown_distribution(self):

        with pytest.raises(ValueError, match="Unknown distribution"):
            create_distribution("gamma")


class TestDistributionReproducibility:
    def test_zipfian_reproducibility(self):

        dist = ZipfianDistribution(s=1.0, num_elements=100)
        rng1 = np.random.default_rng(42)
        rng2 = np.random.default_rng(42)

        samples1 = dist.sample(100, rng1)
        samples2 = dist.sample(100, rng2)

        np.testing.assert_array_equal(samples1, samples2)

    def test_normal_reproducibility(self):

        dist = NormalDistribution(mean=0.5, std=0.15)
        rng1 = np.random.default_rng(42)
        rng2 = np.random.default_rng(42)

        samples1 = dist.sample(100, rng1)
        samples2 = dist.sample(100, rng2)

        np.testing.assert_array_equal(samples1, samples2)

    def test_different_seeds_different_samples(self):

        dist = ZipfianDistribution(s=1.0, num_elements=100)
        rng1 = np.random.default_rng(42)
        rng2 = np.random.default_rng(123)

        samples1 = dist.sample(100, rng1)
        samples2 = dist.sample(100, rng2)

        assert not np.array_equal(samples1, samples2)


