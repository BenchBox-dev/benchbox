# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.monitoring.bottleneck import (
    BottleneckAnalysis,
    BottleneckDetector,
    BottleneckIndicator,
    BottleneckSeverity,
    BottleneckType,
    quick_bottleneck_check,
)
from benchbox.monitoring.profiler import ResourceSample, ResourceTimeline, ResourceType

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestBottleneckType:
    def test_all_types(self):
        types = list(BottleneckType)
        assert BottleneckType.CPU_BOUND in types
        assert BottleneckType.MEMORY_BOUND in types
        assert BottleneckType.DISK_READ_BOUND in types
        assert BottleneckType.DISK_WRITE_BOUND in types
        assert BottleneckType.NETWORK_SEND_BOUND in types
        assert BottleneckType.NETWORK_RECV_BOUND in types
        assert BottleneckType.BALANCED in types
        assert BottleneckType.UNKNOWN in types

    def test_string_values(self):
        assert BottleneckType.CPU_BOUND.value == "cpu_bound"
        assert BottleneckType.BALANCED.value == "balanced"


class TestBottleneckSeverity:
    def test_all_severities(self):
        severities = list(BottleneckSeverity)
        assert BottleneckSeverity.NONE in severities
        assert BottleneckSeverity.LOW in severities
        assert BottleneckSeverity.MODERATE in severities
        assert BottleneckSeverity.HIGH in severities
        assert BottleneckSeverity.CRITICAL in severities


class TestBottleneckIndicator:
    def test_basic_creation(self):
        indicator = BottleneckIndicator(
            bottleneck_type=BottleneckType.CPU_BOUND,
            severity=BottleneckSeverity.HIGH,
            score=0.75,
        )
        assert indicator.bottleneck_type == BottleneckType.CPU_BOUND
        assert indicator.severity == BottleneckSeverity.HIGH
        assert indicator.score == 0.75

    def test_with_evidence(self):
        indicator = BottleneckIndicator(
            bottleneck_type=BottleneckType.CPU_BOUND,
            severity=BottleneckSeverity.HIGH,
            score=0.8,
            evidence=["CPU at 95%", "All cores saturated"],
            recommendations=["Scale up CPU", "Optimize queries"],
        )
        assert len(indicator.evidence) == 2
        assert len(indicator.recommendations) == 2

    def test_to_dict(self):
        indicator = BottleneckIndicator(
            bottleneck_type=BottleneckType.CPU_BOUND,
            severity=BottleneckSeverity.HIGH,
            score=0.75,
            evidence=["High CPU usage"],
        )
        d = indicator.to_dict()
        assert d["bottleneck_type"] == "cpu_bound"
        assert d["severity"] == "high"
        assert d["score"] == 0.75
        assert "evidence" in d


class TestBottleneckAnalysis:
    def test_basic_creation(self):
        analysis = BottleneckAnalysis(
            primary_bottleneck=BottleneckType.CPU_BOUND,
            primary_severity=BottleneckSeverity.HIGH,
            summary="CPU is the bottleneck",
        )
        assert analysis.primary_bottleneck == BottleneckType.CPU_BOUND
        assert analysis.summary == "CPU is the bottleneck"

    def test_with_indicators(self):
        indicators = [
            BottleneckIndicator(BottleneckType.CPU_BOUND, BottleneckSeverity.HIGH, 0.8),
            BottleneckIndicator(BottleneckType.MEMORY_BOUND, BottleneckSeverity.LOW, 0.2),
        ]
        analysis = BottleneckAnalysis(
            primary_bottleneck=BottleneckType.CPU_BOUND,
            primary_severity=BottleneckSeverity.HIGH,
            indicators=indicators,
        )
        assert len(analysis.indicators) == 2

    def test_to_dict(self):
        analysis = BottleneckAnalysis(
            primary_bottleneck=BottleneckType.CPU_BOUND,
            primary_severity=BottleneckSeverity.HIGH,
            summary="Test summary",
        )
        d = analysis.to_dict()
        assert d["primary_bottleneck"] == "cpu_bound"
        assert d["primary_severity"] == "high"
        assert d["summary"] == "Test summary"


class TestBottleneckDetector:
    @pytest.fixture
    def detector(self):
        return BottleneckDetector()

    @pytest.fixture
    def custom_detector(self):
        return BottleneckDetector(
            cpu_high_threshold=70.0,
            cpu_critical_threshold=90.0,
            memory_high_threshold=70.0,
            memory_critical_threshold=90.0,
        )

    def test_default_thresholds(self, detector):
        assert detector.cpu_high == 80.0
        assert detector.cpu_critical == 95.0
        assert detector.memory_high == 80.0

    def test_custom_thresholds(self, custom_detector):
        assert custom_detector.cpu_high == 70.0
        assert custom_detector.cpu_critical == 90.0

    def test_analyze_empty_timeline(self, detector):
        timeline = ResourceTimeline()
        analysis = detector.analyze(timeline)
        assert analysis.primary_bottleneck == BottleneckType.UNKNOWN
        assert analysis.primary_severity == BottleneckSeverity.NONE
        assert "Insufficient data" in analysis.summary

    def test_analyze_balanced_system(self, detector):
        samples = [
            ResourceSample(
                timestamp=i,
                cpu_percent=30.0,
                memory_mb=1000.0,
                memory_percent=30.0,
                disk_read_iops=50.0,
                disk_write_iops=25.0,
                network_send_rate_mbps=10.0,
                network_recv_rate_mbps=15.0,
            )
            for i in range(10)
        ]
        timeline = ResourceTimeline(samples=samples)
        analysis = detector.analyze(timeline)
        assert analysis.primary_bottleneck == BottleneckType.BALANCED

    def test_analyze_cpu_bound_critical(self, detector):
        samples = [
            ResourceSample(
                timestamp=i,
                cpu_percent=98.0,
                memory_mb=1000.0,
                memory_percent=30.0,
            )
            for i in range(10)
        ]
        timeline = ResourceTimeline(samples=samples)
        analysis = detector.analyze(timeline)
        assert analysis.primary_bottleneck == BottleneckType.CPU_BOUND
        assert analysis.primary_severity == BottleneckSeverity.CRITICAL

    def test_analyze_cpu_bound_high(self, detector):
        samples = [
            ResourceSample(
                timestamp=i,
                cpu_percent=85.0,
                memory_mb=1000.0,
                memory_percent=30.0,
            )
            for i in range(10)
        ]
        timeline = ResourceTimeline(samples=samples)
        analysis = detector.analyze(timeline)
        assert analysis.primary_bottleneck == BottleneckType.CPU_BOUND
        assert analysis.primary_severity == BottleneckSeverity.HIGH

    def test_analyze_memory_bound(self, detector):
        samples = [
            ResourceSample(
                timestamp=i,
                cpu_percent=30.0,
                memory_mb=8000.0,
                memory_percent=96.0,
            )
            for i in range(10)
        ]
        timeline = ResourceTimeline(samples=samples)
        analysis = detector.analyze(timeline)
        assert analysis.primary_bottleneck == BottleneckType.MEMORY_BOUND
        assert analysis.primary_severity == BottleneckSeverity.CRITICAL

    def test_analyze_disk_read_bound(self, detector):
        samples = [
            ResourceSample(
                timestamp=i,
                cpu_percent=30.0,
                memory_percent=30.0,
                disk_read_iops=6000.0,
                disk_write_iops=100.0,
            )
            for i in range(10)
        ]
        timeline = ResourceTimeline(samples=samples)
        analysis = detector.analyze(timeline)
        assert analysis.primary_bottleneck == BottleneckType.DISK_READ_BOUND

    def test_analyze_disk_write_bound(self, detector):
        samples = [
            ResourceSample(
                timestamp=i,
                cpu_percent=30.0,
                memory_percent=30.0,
                disk_read_iops=100.0,
                disk_write_iops=6000.0,
            )
            for i in range(10)
        ]
        timeline = ResourceTimeline(samples=samples)
        analysis = detector.analyze(timeline)
        assert analysis.primary_bottleneck == BottleneckType.DISK_WRITE_BOUND

    def test_analyze_network_send_bound(self, detector):
        samples = [
            ResourceSample(
                timestamp=i,
                cpu_percent=30.0,
                memory_percent=30.0,
                network_send_rate_mbps=1500.0,
                network_recv_rate_mbps=50.0,
            )
            for i in range(10)
        ]
        timeline = ResourceTimeline(samples=samples)
        analysis = detector.analyze(timeline)
        assert analysis.primary_bottleneck == BottleneckType.NETWORK_SEND_BOUND

    def test_analyze_network_recv_bound(self, detector):
        samples = [
            ResourceSample(
                timestamp=i,
                cpu_percent=30.0,
                memory_percent=30.0,
                network_send_rate_mbps=50.0,
                network_recv_rate_mbps=1500.0,
            )
            for i in range(10)
        ]
        timeline = ResourceTimeline(samples=samples)
        analysis = detector.analyze(timeline)
        assert analysis.primary_bottleneck == BottleneckType.NETWORK_RECV_BOUND

    def test_analyze_provides_recommendations(self, detector):
        samples = [ResourceSample(timestamp=i, cpu_percent=98.0, memory_percent=30.0) for i in range(10)]
        timeline = ResourceTimeline(samples=samples)
        analysis = detector.analyze(timeline)

        cpu_indicator = next(
            (i for i in analysis.indicators if i.bottleneck_type == BottleneckType.CPU_BOUND),
            None,
        )
        assert cpu_indicator is not None
        assert len(cpu_indicator.recommendations) > 0

    def test_analyze_provides_evidence(self, detector):
        samples = [ResourceSample(timestamp=i, cpu_percent=98.0, memory_percent=30.0) for i in range(10)]
        timeline = ResourceTimeline(samples=samples)
        analysis = detector.analyze(timeline)

        cpu_indicator = next(
            (i for i in analysis.indicators if i.bottleneck_type == BottleneckType.CPU_BOUND),
            None,
        )
        assert cpu_indicator is not None
        assert len(cpu_indicator.evidence) > 0

    def test_analyze_summary_generation(self, detector):
        samples = [ResourceSample(timestamp=i, cpu_percent=98.0, memory_percent=30.0) for i in range(10)]
        timeline = ResourceTimeline(samples=samples)
        analysis = detector.analyze(timeline)
        assert "CPU" in analysis.summary
        assert "critical" in analysis.summary.lower()

    def test_analyze_utilizations_populated(self, detector):
        samples = [ResourceSample(timestamp=i, cpu_percent=50.0, memory_mb=1000.0) for i in range(10)]
        timeline = ResourceTimeline(samples=samples)
        analysis = detector.analyze(timeline)
        assert ResourceType.CPU in analysis.utilizations
        assert analysis.utilizations[ResourceType.CPU].avg_value == 50.0


class TestBottleneckDetectorEdgeCases:
    def test_spike_detection(self):
        detector = BottleneckDetector()
        samples = [ResourceSample(timestamp=i, cpu_percent=30.0, memory_percent=30.0) for i in range(8)]
        samples.append(ResourceSample(timestamp=8, cpu_percent=98.0, memory_percent=30.0))
        samples.append(ResourceSample(timestamp=9, cpu_percent=30.0, memory_percent=30.0))

        timeline = ResourceTimeline(samples=samples)
        analysis = detector.analyze(timeline)
        cpu_indicator = next(
            (i for i in analysis.indicators if i.bottleneck_type == BottleneckType.CPU_BOUND),
            None,
        )
        assert cpu_indicator is not None
        assert cpu_indicator.severity != BottleneckSeverity.NONE

    def test_multiple_bottlenecks(self):
        detector = BottleneckDetector()
        samples = [
            ResourceSample(
                timestamp=i,
                cpu_percent=90.0,
                memory_percent=92.0,
            )
            for i in range(10)
        ]
        timeline = ResourceTimeline(samples=samples)
        analysis = detector.analyze(timeline)

        cpu_indicator = next(
            (i for i in analysis.indicators if i.bottleneck_type == BottleneckType.CPU_BOUND),
            None,
        )
        mem_indicator = next(
            (i for i in analysis.indicators if i.bottleneck_type == BottleneckType.MEMORY_BOUND),
            None,
        )
        assert cpu_indicator is not None
        assert mem_indicator is not None
        assert analysis.indicators[0].score >= analysis.indicators[1].score


class TestQuickBottleneckCheck:
    def test_empty_timeline(self):
        timeline = ResourceTimeline()
        result = quick_bottleneck_check(timeline)
        assert result == BottleneckType.UNKNOWN

    def test_detect_cpu_bound(self):
        samples = [ResourceSample(timestamp=i, cpu_percent=95.0) for i in range(5)]
        timeline = ResourceTimeline(samples=samples)
        result = quick_bottleneck_check(timeline)
        assert result == BottleneckType.CPU_BOUND

    def test_detect_memory_bound(self):
        samples = [ResourceSample(timestamp=i, cpu_percent=30.0, memory_percent=95.0) for i in range(5)]
        timeline = ResourceTimeline(samples=samples)
        result = quick_bottleneck_check(timeline)
        assert result == BottleneckType.MEMORY_BOUND

    def test_detect_disk_read_bound(self):
        samples = [
            ResourceSample(timestamp=i, cpu_percent=30.0, memory_percent=30.0, disk_read_iops=2000.0) for i in range(5)
        ]
        timeline = ResourceTimeline(samples=samples)
        result = quick_bottleneck_check(timeline)
        assert result == BottleneckType.DISK_READ_BOUND

    def test_detect_disk_write_bound(self):
        samples = [
            ResourceSample(
                timestamp=i,
                cpu_percent=30.0,
                memory_percent=30.0,
                disk_read_iops=100.0,
                disk_write_iops=2000.0,
            )
            for i in range(5)
        ]
        timeline = ResourceTimeline(samples=samples)
        result = quick_bottleneck_check(timeline)
        assert result == BottleneckType.DISK_WRITE_BOUND

    def test_detect_network_bound(self):
        samples = [
            ResourceSample(
                timestamp=i,
                cpu_percent=30.0,
                memory_percent=30.0,
                network_send_rate_mbps=150.0,
                network_recv_rate_mbps=50.0,
            )
            for i in range(5)
        ]
        timeline = ResourceTimeline(samples=samples)
        result = quick_bottleneck_check(timeline)
        assert result == BottleneckType.NETWORK_SEND_BOUND

    def test_detect_balanced(self):
        samples = [
            ResourceSample(
                timestamp=i,
                cpu_percent=30.0,
                memory_percent=30.0,
                disk_read_iops=100.0,
                disk_write_iops=50.0,
                network_send_rate_mbps=10.0,
                network_recv_rate_mbps=10.0,
            )
            for i in range(5)
        ]
        timeline = ResourceTimeline(samples=samples)
        result = quick_bottleneck_check(timeline)
        assert result == BottleneckType.BALANCED


