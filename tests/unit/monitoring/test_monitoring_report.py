# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.monitoring.bottleneck import (
    BottleneckDetector,
    BottleneckType,
)
from benchbox.monitoring.profiler import ResourceSample, ResourceTimeline, ResourceType
from benchbox.monitoring.report import (
    ResourceChart,
    ResourceReporter,
    format_bytes,
    format_duration,
    generate_ascii_chart,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestFormatBytes:
    def test_bytes(self):
        assert format_bytes(500) == "500 B"

    def test_kilobytes(self):
        assert format_bytes(2048) == "2.0 KB"

    def test_megabytes(self):
        assert format_bytes(1024 * 1024 * 5) == "5.0 MB"

    def test_gigabytes(self):
        assert format_bytes(1024 * 1024 * 1024 * 2) == "2.0 GB"

    def test_fractional(self):
        assert "1.5" in format_bytes(int(1024 * 1.5))


class TestFormatDuration:
    def test_seconds(self):
        assert format_duration(45.2) == "45.2s"

    def test_minutes(self):
        assert format_duration(125) == "2m 5s"

    def test_hours(self):
        assert format_duration(3750) == "1h 2m"

    def test_zero(self):
        assert format_duration(0) == "0.0s"


class TestResourceChart:
    def test_basic_creation(self):
        chart = ResourceChart(
            resource_type=ResourceType.CPU,
            width=60,
            height=10,
            title="CPU Usage",
            chart_lines=["line1", "line2"],
        )
        assert chart.resource_type == ResourceType.CPU
        assert chart.title == "CPU Usage"

    def test_render(self):
        chart = ResourceChart(
            resource_type=ResourceType.CPU,
            title="CPU Usage",
            chart_lines=["*****", "  ***", "    *"],
        )
        rendered = chart.render()
        assert "CPU Usage" in rendered
        assert "*****" in rendered

    def test_str(self):
        chart = ResourceChart(
            resource_type=ResourceType.CPU,
            chart_lines=["test"],
        )
        assert "test" in str(chart)


class TestGenerateAsciiChart:
    def test_empty_series(self):
        chart = generate_ascii_chart([])
        assert "(no data)" in chart.render()

    def test_single_value(self):
        chart = generate_ascii_chart([50.0])
        rendered = chart.render()
        assert "*" in rendered

    def test_increasing_series(self):
        series = [float(i) for i in range(10)]
        chart = generate_ascii_chart(series, width=20, height=5)
        rendered = chart.render()
        assert "*" in rendered
        assert "→" in rendered or "+" in rendered

    def test_constant_series(self):
        series = [50.0] * 10
        chart = generate_ascii_chart(series, width=20, height=5)
        rendered = chart.render()
        assert "*" in rendered

    def test_with_title_and_unit(self):
        series = [10.0, 20.0, 30.0]
        chart = generate_ascii_chart(series, title="Test Chart", unit="%")
        rendered = chart.render()
        assert "Test Chart" in rendered
        assert "%" in rendered

    def test_chart_dimensions(self):
        series = [float(i) for i in range(100)]
        chart = generate_ascii_chart(series, width=30, height=8)
        assert chart.width == 30
        assert chart.height == 8

    def test_min_max_values(self):
        series = [10.0, 50.0, 90.0]
        chart = generate_ascii_chart(series)
        assert chart.min_value == 10.0
        assert chart.max_value == 90.0

    def test_resampling_long_series(self):
        series = [float(i) for i in range(200)]
        chart = generate_ascii_chart(series, width=50)
        assert "*" in chart.render()

    def test_stretching_short_series(self):
        series = [10.0, 20.0, 30.0]
        chart = generate_ascii_chart(series, width=60)
        assert "*" in chart.render()

    def test_use_unicode_false(self):
        series = [10.0, 50.0, 90.0]
        chart = generate_ascii_chart(series, width=40, use_unicode=False)
        rendered = chart.render()
        assert len(rendered) > 0
        assert "→" not in rendered
        assert "->" in rendered

    def test_use_color_true(self):
        series = [10.0, 50.0, 90.0]
        chart = generate_ascii_chart(series, width=40, use_color=True)
        rendered = chart.render()
        assert "\033[" in rendered


class TestResourceReporter:
    @pytest.fixture
    def sample_timeline(self):
        samples = [
            ResourceSample(
                timestamp=i,
                cpu_percent=50.0 + i * 3,
                memory_mb=1000.0 + i * 50,
                memory_percent=40.0 + i,
                disk_read_iops=100.0 + i * 10,
                disk_write_iops=50.0 + i * 5,
                network_send_rate_mbps=10.0 + i,
                network_recv_rate_mbps=15.0 + i,
            )
            for i in range(10)
        ]
        return ResourceTimeline(samples=samples, start_time=0.0, end_time=10.0)

    @pytest.fixture
    def reporter(self):
        return ResourceReporter()

    def test_basic_creation(self):
        reporter = ResourceReporter()
        assert reporter.chart_width == 60
        assert reporter.chart_height == 10
        assert reporter.detector is not None

    def test_custom_creation(self):
        detector = BottleneckDetector(cpu_high_threshold=70.0)
        reporter = ResourceReporter(
            chart_width=80,
            chart_height=15,
            detector=detector,
        )
        assert reporter.chart_width == 80
        assert reporter.chart_height == 15
        assert reporter.detector is detector

    def test_color_unicode_passthrough(self, sample_timeline):
        reporter = ResourceReporter(use_color=False, use_unicode=False)
        report = reporter.generate_report(sample_timeline, include_charts=True)
        cpu_chart = report.charts[ResourceType.CPU]
        rendered = cpu_chart.render()
        assert "→" not in rendered
        assert "->" in rendered

    def test_generate_report(self, reporter, sample_timeline):
        report = reporter.generate_report(sample_timeline)
        assert report.timeline is sample_timeline
        assert report.analysis is not None
        assert len(report.utilizations) > 0

    def test_generate_report_with_charts(self, reporter, sample_timeline):
        report = reporter.generate_report(sample_timeline, include_charts=True)
        assert len(report.charts) > 0
        assert ResourceType.CPU in report.charts

    def test_generate_report_without_charts(self, reporter, sample_timeline):
        report = reporter.generate_report(sample_timeline, include_charts=False)
        assert len(report.charts) == 0

    def test_generate_summary_line(self, reporter, sample_timeline):
        summary = reporter.generate_summary_line(sample_timeline)
        assert "CPU" in summary
        assert "Mem" in summary
        assert "avg" in summary or "/" in summary

    def test_generate_summary_line_empty(self, reporter):
        timeline = ResourceTimeline()
        summary = reporter.generate_summary_line(timeline)
        assert "No resource data" in summary

    def test_generate_summary_with_disk(self, reporter, sample_timeline):
        summary = reporter.generate_summary_line(sample_timeline)
        assert "Disk" in summary

    def test_generate_summary_with_network(self, reporter, sample_timeline):
        summary = reporter.generate_summary_line(sample_timeline)
        assert "Net" in summary


class TestResourceReport:
    @pytest.fixture
    def sample_report(self):
        samples = [
            ResourceSample(
                timestamp=i,
                cpu_percent=70.0 + i * 2,
                memory_mb=2000.0,
                memory_percent=50.0,
            )
            for i in range(10)
        ]
        timeline = ResourceTimeline(samples=samples, start_time=0.0, end_time=10.0)
        reporter = ResourceReporter()
        return reporter.generate_report(timeline)

    def test_generate_text_report(self, sample_report):
        text = sample_report.generate_text_report()
        assert "RESOURCE UTILIZATION REPORT" in text
        assert "SUMMARY" in text
        assert "BOTTLENECK ANALYSIS" in text

    def test_text_report_contains_duration(self, sample_report):
        text = sample_report.generate_text_report()
        assert "Duration" in text
        assert "10.0" in text

    def test_text_report_contains_utilization_table(self, sample_report):
        text = sample_report.generate_text_report()
        assert "RESOURCE UTILIZATION" in text
        assert "Min" in text
        assert "Avg" in text
        assert "Max" in text
        assert "P95" in text

    def test_text_report_with_charts(self, sample_report):
        text = sample_report.generate_text_report(include_charts=True)
        assert "RESOURCE CHARTS" in text or "CPU Utilization" in text

    def test_text_report_without_charts(self, sample_report):
        text = sample_report.generate_text_report(include_charts=False)
        assert "RESOURCE CHARTS" not in text or "*" not in text.split("RESOURCE CHARTS")[-1]

    def test_generate_json(self, sample_report):
        data = sample_report.generate_json()
        assert "summary" in data
        assert "timeline" in data
        assert "analysis" in data
        assert "utilizations" in data

    def test_json_contains_duration(self, sample_report):
        data = sample_report.generate_json()
        assert data["summary"]["duration_seconds"] == 10.0

    def test_json_serializable(self, sample_report):
        import json

        data = sample_report.generate_json()
        json_str = json.dumps(data)
        assert len(json_str) > 0


class TestResourceReporterEdgeCases:
    def test_empty_timeline(self):
        reporter = ResourceReporter()
        timeline = ResourceTimeline()
        report = reporter.generate_report(timeline)
        assert report.analysis.primary_bottleneck == BottleneckType.UNKNOWN

    def test_single_sample(self):
        reporter = ResourceReporter()
        timeline = ResourceTimeline(samples=[ResourceSample(timestamp=0, cpu_percent=50.0)])
        report = reporter.generate_report(timeline)
        assert report.timeline.sample_count == 1

    def test_zero_values(self):
        reporter = ResourceReporter()
        samples = [ResourceSample(timestamp=i) for i in range(5)]
        timeline = ResourceTimeline(samples=samples)
        report = reporter.generate_report(timeline)
        assert report.analysis is not None

    def test_charts_skip_empty_series(self):
        reporter = ResourceReporter()
        samples = [ResourceSample(timestamp=i, cpu_percent=50.0) for i in range(5)]
        timeline = ResourceTimeline(samples=samples)
        report = reporter.generate_report(timeline, include_charts=True)
        assert ResourceType.CPU in report.charts


class TestReportIntegration:
    def test_full_workflow(self):
        samples = []
        for i in range(60):
            if i < 20:
                cpu = 30 + i * 3
            elif i < 40:
                cpu = 90
            else:
                cpu = 90 - (i - 40) * 3

            samples.append(
                ResourceSample(
                    timestamp=float(i),
                    cpu_percent=float(cpu),
                    memory_mb=2000.0 + i * 10,
                    memory_percent=50.0 + i * 0.3,
                    disk_read_iops=100.0 + i,
                    disk_write_iops=50.0 + i * 0.5,
                    network_send_rate_mbps=5.0 + i * 0.1,
                    network_recv_rate_mbps=10.0 + i * 0.2,
                )
            )

        timeline = ResourceTimeline(samples=samples, start_time=0, end_time=60)

        reporter = ResourceReporter()
        report = reporter.generate_report(timeline)

        assert report.timeline.sample_count == 60
        assert report.analysis is not None
        assert len(report.utilizations) == 6
        assert len(report.charts) > 0

        text = report.generate_text_report()
        assert len(text) > 500

        import json

        data = report.generate_json()
        json_str = json.dumps(data)
        assert len(json_str) > 100

        summary = reporter.generate_summary_line(timeline)
        assert len(summary) < 200
        assert "CPU" in summary


