# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import socket
import time

import pytest

from benchbox.experimental.multiregion.config import RegionConfig
from benchbox.experimental.multiregion.latency import (
    LatencyMeasurement,
    LatencyMeasurer,
    LatencyProfile,
    estimate_latency_from_distance,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestLatencyMeasurement:
    def test_basic_creation(self, us_east_region, eu_west_region):
        measurement = LatencyMeasurement(
            source_region=us_east_region,
            target_region=eu_west_region,
            latency_ms=75.5,
            timestamp=time.time(),
            success=True,
        )
        assert measurement.latency_ms == 75.5
        assert measurement.success is True

    def test_failed_measurement(self, us_east_region, eu_west_region):
        measurement = LatencyMeasurement(
            source_region=us_east_region,
            target_region=eu_west_region,
            latency_ms=0,
            timestamp=time.time(),
            success=False,
            error="Connection timeout",
        )
        assert measurement.success is False
        assert measurement.error == "Connection timeout"

    def test_measurement_type(self, us_east_region, eu_west_region):
        measurement = LatencyMeasurement(
            source_region=us_east_region,
            target_region=eu_west_region,
            latency_ms=50,
            timestamp=time.time(),
            success=True,
            measurement_type="query",
        )
        assert measurement.measurement_type == "query"


class TestLatencyProfile:
    @pytest.fixture
    def profile_with_measurements(self, us_east_region, eu_west_region):
        profile = LatencyProfile(
            source_region=us_east_region,
            target_region=eu_west_region,
        )
        latencies = [70, 75, 80, 72, 78, 85, 73, 77, 90, 76]
        for i, lat in enumerate(latencies):
            profile.measurements.append(
                LatencyMeasurement(
                    source_region=us_east_region,
                    target_region=eu_west_region,
                    latency_ms=float(lat),
                    timestamp=time.time() + i,
                    success=True,
                )
            )
        return profile

    def test_sample_count(self, profile_with_measurements):
        assert profile_with_measurements.sample_count == 10

    def test_min_latency(self, profile_with_measurements):
        assert profile_with_measurements.min_latency_ms == 70

    def test_max_latency(self, profile_with_measurements):
        assert profile_with_measurements.max_latency_ms == 90

    def test_avg_latency(self, profile_with_measurements):
        assert 77 < profile_with_measurements.avg_latency_ms < 78

    def test_median_latency(self, profile_with_measurements):
        assert 76 <= profile_with_measurements.median_latency_ms <= 77

    def test_stdev_latency(self, profile_with_measurements):
        assert profile_with_measurements.stdev_latency_ms > 0

    def test_p95_latency(self, profile_with_measurements):
        assert profile_with_measurements.p95_latency_ms >= 85

    def test_success_rate(self, profile_with_measurements):
        assert profile_with_measurements.success_rate == 100.0

    def test_success_rate_with_failures(self, us_east_region, eu_west_region):
        profile = LatencyProfile(
            source_region=us_east_region,
            target_region=eu_west_region,
        )
        profile.measurements.append(LatencyMeasurement(us_east_region, eu_west_region, 50, time.time(), True))
        profile.measurements.append(LatencyMeasurement(us_east_region, eu_west_region, 0, time.time(), False))
        assert profile.success_rate == 50.0

    def test_to_dict(self, profile_with_measurements):
        d = profile_with_measurements.to_dict()
        assert "source_region" in d
        assert "target_region" in d
        assert "avg_latency_ms" in d
        assert "p95_latency_ms" in d

    def test_empty_profile(self, us_east_region, eu_west_region):
        profile = LatencyProfile(
            source_region=us_east_region,
            target_region=eu_west_region,
        )
        assert profile.sample_count == 0
        assert profile.min_latency_ms == 0
        assert profile.avg_latency_ms == 0
        assert profile.success_rate == 0


class TestLatencyMeasurer:
    def test_tcp_measurement_reaches_listening_loopback_server(self, us_east_region, eu_west_region):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            target = RegionConfig(region=eu_west_region, endpoint="127.0.0.1", port=server.getsockname()[1])
            measurement = LatencyMeasurer(source_region=us_east_region, target_config=target).measure_tcp_latency(
                timeout_seconds=2.0
            )
        assert measurement.success is True
        assert measurement.error is None
        assert measurement.latency_ms > 0

    def test_tcp_measurement_reports_unreachable_loopback_port(self, us_east_region, eu_west_region):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            closed_port = probe.getsockname()[1]
        target = RegionConfig(region=eu_west_region, endpoint="127.0.0.1", port=closed_port)
        measurement = LatencyMeasurer(source_region=us_east_region, target_config=target).measure_tcp_latency(
            timeout_seconds=2.0
        )
        assert measurement.success is False
        assert measurement.error
        assert measurement.latency_ms == 0

    def test_query_measurement_no_factory(self, us_east_region, eu_west_config):
        measurer = LatencyMeasurer(
            source_region=us_east_region,
            target_config=eu_west_config,
            connection_factory=None,
        )
        measurement = measurer.measure_query_latency()
        assert measurement.success is False
        assert "No connection factory" in measurement.error

    def test_latency_profile_invalid_method(self, us_east_region, eu_west_config):
        measurer = LatencyMeasurer(
            source_region=us_east_region,
            target_config=eu_west_config,
        )
        with pytest.raises(ValueError, match="Unknown measurement method"):
            measurer.measure_latency_profile(samples=1, method="invalid")


class TestEstimateLatencyFromDistance:
    def test_short_distance(self):
        min_lat, typical_lat = estimate_latency_from_distance(100)
        assert min_lat < 5
        assert typical_lat > min_lat

    def test_transatlantic(self):
        min_lat, typical_lat = estimate_latency_from_distance(5500)
        assert 30 < min_lat < 80
        assert 100 < typical_lat < 200

    def test_transpacific(self):
        min_lat, typical_lat = estimate_latency_from_distance(10000)
        assert min_lat > 50
        assert typical_lat > 150

    def test_zero_distance(self):
        min_lat, typical_lat = estimate_latency_from_distance(0)
        assert min_lat == 0
        assert typical_lat == 0
