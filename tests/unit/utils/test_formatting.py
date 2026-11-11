import pytest

from benchbox.utils.formatting import (
    format_bytes,
    format_duration,
    format_memory_usage,
    format_number,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestFormatDuration:
    def test_milliseconds(self):
        assert format_duration(0.001) == "1.0ms"
        assert format_duration(0.123) == "123.0ms"
        assert format_duration(0.9999) == "999.9ms"

    def test_seconds(self):
        assert format_duration(1.0) == "1.000s"
        assert format_duration(1.234) == "1.234s"
        assert format_duration(59.999) == "59.999s"

    def test_minutes(self):
        assert format_duration(60.0) == "1.0m"
        assert format_duration(75.0) == "1.2m"
        assert format_duration(3600.0) == "60.0m"

    def test_edge_cases(self):
        assert format_duration(0.0) == "0.0ms"
        assert format_duration(0.9999) == "999.9ms"
        assert format_duration(1.0) == "1.000s"
        assert format_duration(59.999) == "59.999s"
        assert format_duration(60.0) == "1.0m"


class TestFormatBytes:
    def test_bytes(self):
        assert format_bytes(0) == "0.00 B"
        assert format_bytes(512) == "512.00 B"
        assert format_bytes(1023) == "1023.00 B"

    def test_kilobytes(self):
        assert format_bytes(1024) == "1.00 KB"
        assert format_bytes(1536) == "1.50 KB"
        assert format_bytes(1048575) == "1024.00 KB"

    def test_megabytes(self):
        assert format_bytes(1048576) == "1.00 MB"
        assert format_bytes(1572864) == "1.50 MB"
        assert format_bytes(1073741823) == "1024.00 MB"

    def test_gigabytes(self):
        assert format_bytes(1073741824) == "1.00 GB"
        assert format_bytes(1610612736) == "1.50 GB"
        assert format_bytes(1099511627775) == "1024.00 GB"

    def test_terabytes(self):
        assert format_bytes(1099511627776) == "1.00 TB"
        assert format_bytes(1649267441664) == "1.50 TB"

    def test_petabytes(self):
        assert format_bytes(1125899906842624) == "1.00 PB"
        assert format_bytes(1688849860263936) == "1.50 PB"

    def test_float_input(self):
        assert format_bytes(1024.0) == "1.00 KB"
        assert format_bytes(1572864.5) == "1.50 MB"

    def test_edge_cases(self):
        assert format_bytes(0) == "0.00 B"
        assert format_bytes(1) == "1.00 B"


class TestFormatMemoryUsage:
    def test_megabytes_input(self):
        assert format_memory_usage(1.0) == "1.00 MB"
        assert format_memory_usage(512.5) == "512.50 MB"
        assert format_memory_usage(1024.0) == "1.00 GB"

    def test_conversion_to_bytes(self):
        result = format_memory_usage(1.0)
        assert result == "1.00 MB"


class TestFormatNumber:
    def test_integers(self):
        assert format_number(1000) == "1,000"
        assert format_number(1234567) == "1,234,567"
        assert format_number(999) == "999"

    def test_floats(self):
        assert format_number(1234.567, 2) == "1,234.57"
        assert format_number(1234.567, 1) == "1,234.6"
        assert format_number(1234.567, 0) == "1,235"

    def test_default_precision(self):
        assert format_number(1234.567) == "1,234.57"

    def test_edge_cases(self):
        assert format_number(0) == "0"
        assert format_number(0.0) == "0.00"
        assert format_number(-1234) == "-1,234"
        assert format_number(-1234.567) == "-1,234.57"


class TestImportIntegration:
    def test_utils_import(self):
        from benchbox.utils import (
            format_bytes,
            format_duration,
            format_memory_usage,
            format_number,
        )

        assert format_duration(1.5) == "1.500s"
        assert format_bytes(2048) == "2.00 KB"
        assert format_memory_usage(100.0) == "100.00 MB"
        assert format_number(12345) == "12,345"

    def test_direct_import(self):
        from benchbox.utils.formatting import format_bytes, format_duration

        assert format_duration(0.5) == "500.0ms"
        assert format_bytes(1024) == "1.00 KB"


