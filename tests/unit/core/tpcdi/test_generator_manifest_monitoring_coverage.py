from __future__ import annotations

import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchbox.core.tpcdi.generator.facts import FactGenerationMixin
from benchbox.core.tpcdi.generator.manifest import ManifestMixin
from benchbox.core.tpcdi.generator.monitoring import ResourceMonitoringMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _ManifestHarness(ManifestMixin):
    def __init__(self, *, compression: bool, ext: str = ".zst") -> None:
        self.scale_factor = 0.01
        self.max_workers = 2
        self.compression_type = "zstd" if compression else "none"
        self.compression_level = 3
        self._compression = compression
        self._ext = ext

    def should_use_compression(self) -> bool:
        return self._compression

    def get_compressor(self) -> SimpleNamespace:
        return SimpleNamespace(get_file_extension=lambda: self._ext)


def test_manifest_consistency_checks_and_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _ManifestHarness(compression=False)
    harness._validate_file_format_consistency(tmp_path)

    raw = tmp_path / "a.tbl"
    raw.write_text("1|x\n", encoding="utf-8")
    harness = _ManifestHarness(compression=True, ext=".zst")
    with pytest.raises(RuntimeError, match="raw .tbl files"):
        harness._validate_file_format_consistency(tmp_path)

    raw.unlink()
    empty = tmp_path / "a.tbl.zst"
    empty.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="empty compressed files"):
        harness._validate_file_format_consistency(tmp_path)

    data = tmp_path / "rows.tbl"
    data.write_text("1|x\n2|y\n", encoding="utf-8")
    harness = _ManifestHarness(compression=False)
    harness._write_manifest(tmp_path, {"T": str(data), "MISSING": str(tmp_path / "none.tbl")})
    manifest = json.loads((tmp_path / "_datagen_manifest.json").read_text(encoding="utf-8"))
    assert manifest["tables"]["T"]["formats"]["tbl"][0]["row_count"] == 2
    assert manifest["tables"]["MISSING"]["formats"]["tbl"][0]["size_bytes"] == 0

    gz = tmp_path / "rows.tbl.gz"
    with gzip.open(gz, "wt") as f:
        f.write("1|x\n2|y\n3|z\n")
    monkeypatch.setattr("benchbox.core.tpcdi.generator.manifest.detect_compression", lambda _p: "gzip")
    harness._write_manifest(tmp_path, {"GZ": str(gz)})
    manifest = json.loads((tmp_path / "_datagen_manifest.json").read_text(encoding="utf-8"))
    assert manifest["tables"]["GZ"]["formats"]["tbl"][0]["row_count"] == 3


class _Logger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, msg: str) -> None:
        self.messages.append(msg)


class _MonitoringHarness(FactGenerationMixin, ResourceMonitoringMixin):
    def __init__(self) -> None:
        self.enable_progress = True
        self.logger = _Logger()
        self.memory_threshold = 0.5
        self.scale_factor = 0.1
        self.max_workers = 2
        self.base_companies = 10
        self.base_securities = 20
        self.base_customers = 30
        self.base_accounts = 40
        self.base_trades = 50
        self.chunk_size = 25
        self.generation_stats = {
            "records_generated": 123,
            "generation_times": {"DimCustomer": 0.2},
            "files_written": 1,
        }


def test_monitoring_mixin_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _MonitoringHarness()
    harness._log_simple_progress(10, 100, "DimCustomer")
    assert any("10.0%" in msg for msg in harness.logger.messages)

    harness.enable_progress = False
    before = list(harness.logger.messages)
    harness._log_simple_progress(20, 100, "DimCustomer")
    assert harness.logger.messages == before

    monkeypatch.setattr(
        "benchbox.core.tpcdi.generator.monitoring.psutil.virtual_memory", lambda: SimpleNamespace(percent=60)
    )
    assert harness._check_memory_usage() is True
    monkeypatch.setattr("benchbox.core.tpcdi.generator.monitoring.gc.collect", lambda: 1)
    harness._cleanup_memory()

    harness._log_generation_summary()
    assert any("Generation Summary:" in msg for msg in harness.logger.messages)
    assert harness._estimate_records_for_table("DimDate") == 5844
    assert harness._estimate_records_for_table("FactTrade") == int(harness.base_trades * harness.scale_factor)
    assert harness._estimate_memory_requirements() > 0
    assert harness._estimate_disk_requirements() > 0
