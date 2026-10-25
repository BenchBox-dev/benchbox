from dataclasses import dataclass, field
from pathlib import Path

import pytest

from benchbox.core.tpc_patterns import generate_official_benchmark_audit_trail

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@dataclass
class _FakeConfig:
    output_dir: Path
    scale_factor: float = 1.0
    num_streams: int = 2


@dataclass
class _FakeResult:
    config: _FakeConfig
    start_time: str = "2026-07-09T00:00:00"
    end_time: str = "2026-07-09T00:01:00"
    total_time: float = 60.0
    power_at_size: float = 100.0
    throughput_at_size: float = 200.0
    success: bool = True
    errors: list = field(default_factory=list)


class TestGenerateOfficialBenchmarkAuditTrail:
    def test_writes_audit_trail_with_default_output_file(self, tmp_path):
        result = _FakeResult(config=_FakeConfig(output_dir=tmp_path))

        output_path = generate_official_benchmark_audit_trail(
            result=result,
            benchmark_title="TPC-H",
            benchmark_slug="tpch",
        )

        assert output_path.exists()
        assert output_path.parent == tmp_path
        assert output_path.name.startswith("tpch_audit_trail_")

        content = output_path.read_text(encoding="utf-8")
        assert "TPC-H Benchmark Audit Trail" in content
        assert "Scale Factor: 1.0" in content
        assert "Number of Streams: 2" in content
        assert "Power@Size: 100.00" in content
        assert "Throughput@Size: 200.00" in content
        assert "QphH" not in content
        assert "QphDS" not in content
        assert "Success: True" in content
        assert "Errors:" not in content

    def test_writes_audit_trail_with_explicit_output_file(self, tmp_path):
        result = _FakeResult(config=_FakeConfig(output_dir=None))
        explicit_path = tmp_path / "nested" / "custom_audit.txt"

        output_path = generate_official_benchmark_audit_trail(
            result=result,
            benchmark_title="TPC-DS",
            benchmark_slug="tpcds",
            output_file=explicit_path,
        )

        assert output_path == explicit_path
        assert output_path.exists()
        assert "TPC-DS Benchmark Audit Trail" in output_path.read_text(encoding="utf-8")

    def test_writes_errors_section_when_present(self, tmp_path):
        result = _FakeResult(config=_FakeConfig(output_dir=tmp_path), success=False, errors=["stream 0 failed"])

        output_path = generate_official_benchmark_audit_trail(
            result=result,
            benchmark_title="TPC-H",
            benchmark_slug="tpch",
        )

        content = output_path.read_text(encoding="utf-8")
        assert "Success: False" in content
        assert "Errors:" in content
        assert "stream 0 failed" in content

    def test_falls_back_to_cwd_benchmark_results_when_no_output_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = _FakeResult(config=_FakeConfig(output_dir=None))

        output_path = generate_official_benchmark_audit_trail(
            result=result,
            benchmark_title="TPC-H",
            benchmark_slug="tpch",
        )

        assert output_path.parent == tmp_path / "benchmark_results"
        assert output_path.exists()
