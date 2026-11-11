from __future__ import annotations

import io
import threading
from pathlib import Path
from subprocess import CalledProcessError
from types import SimpleNamespace

import pytest

from benchbox.core.tpcds.generator.streaming import StreamingGenerationMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _StreamingHarness(StreamingGenerationMixin):
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.dsdgen_exe = output_dir / "dsdgen"
        self.dsdgen_exe.write_text("#!/bin/sh\nexit 0\n")
        self.scale_factor = 1.0
        self.verbose = False
        self.parallel = 3
        self._manifest_lock = threading.Lock()
        self._manifest_entries = {}

    def _copy_distribution_files(self, _output_dir: Path) -> None:
        return None

    def _is_valid_data_file(self, file_path: Path) -> bool:
        return file_path.exists() and file_path.stat().st_size > 0

    def get_compressed_filename(self, expected_filename: str) -> str:
        return expected_filename + ".zst"

    def open_output_file(self, file_path: Path, mode: str = "wt"):
        return open(file_path, mode)

    def should_use_compression(self) -> bool:
        return True

    def compress_existing_file(self, dat_file: Path, remove_original: bool = False) -> Path:
        compressed = dat_file.with_suffix(dat_file.suffix + ".zst")
        compressed.write_bytes(dat_file.read_bytes())
        if remove_original:
            dat_file.unlink(missing_ok=True)
        return compressed


def test_generate_single_table_streaming_handles_uncompressed_target(monkeypatch, tmp_path):
    harness = _StreamingHarness(tmp_path)
    monkeypatch.setattr(harness, "get_compressed_filename", lambda name: name)
    monkeypatch.setattr("benchbox.core.tpcds.generator.streaming.subprocess.run", lambda *a, **k: None)

    dat_file = tmp_path / "customer.dat"
    dat_file.write_text("1|a\n2|b\n")

    harness._generate_single_table_streaming(tmp_path, "customer")

    assert harness._manifest_entries["customer"][0]["row_count"] == 2
    assert dat_file.exists()


def test_generate_single_table_streaming_wraps_calledprocesserror(monkeypatch, tmp_path):
    harness = _StreamingHarness(tmp_path)

    def _raise(*_args, **_kwargs):
        raise CalledProcessError(returncode=9, cmd=["dsdgen"])

    monkeypatch.setattr("benchbox.core.tpcds.generator.streaming.subprocess.run", _raise)

    with pytest.raises(RuntimeError, match="exit code 9"):
        harness._generate_single_table_streaming(tmp_path, "customer")


def test_generate_single_table_chunk_streaming_counts_last_line_without_newline(monkeypatch, tmp_path):
    harness = _StreamingHarness(tmp_path)

    class _Process:
        def __init__(self):
            self.stdout = io.BytesIO(b"1|only_row")
            self.stderr = io.BytesIO(b"")
            self.returncode = 0

        def wait(self):
            return None

    monkeypatch.setattr("benchbox.core.tpcds.generator.streaming.subprocess.Popen", lambda *a, **k: _Process())

    harness._generate_single_table_chunk_streaming(tmp_path, "call_center", 1)

    entry = harness._manifest_entries["call_center"][0]
    assert entry["row_count"] == 1
    assert entry["path"].endswith(".zst")


def test_generate_single_table_chunk_streaming_raises_on_nonzero_exit_with_no_data(monkeypatch, tmp_path):
    harness = _StreamingHarness(tmp_path)

    class _Process:
        def __init__(self):
            self.stdout = io.BytesIO(b"")
            self.stderr = io.BytesIO(b"failure")
            self.returncode = 2

        def wait(self):
            return None

    monkeypatch.setattr("benchbox.core.tpcds.generator.streaming.subprocess.Popen", lambda *a, **k: _Process())

    with pytest.raises(RuntimeError, match="exit code 2"):
        harness._generate_single_table_chunk_streaming(tmp_path, "call_center", 2)


def test_generate_parent_table_chunk_with_children_tracks_manifest(monkeypatch, tmp_path):
    harness = _StreamingHarness(tmp_path)
    monkeypatch.setattr("benchbox.core.tpcds.generator.streaming.subprocess.run", lambda *a, **k: None)

    parent = tmp_path / "catalog_sales_1_3.dat"
    child = tmp_path / "catalog_returns_1_3.dat"
    parent.write_text("1|a\n2|b\n")
    child.write_text("3|c\n")

    harness._generate_parent_table_chunk_with_children(tmp_path, "catalog_sales", 1, ["catalog_returns"])

    assert harness._manifest_entries["catalog_sales"][0]["row_count"] == 2
    assert harness._manifest_entries["catalog_returns"][0]["row_count"] == 1


def test_generate_table_with_streaming_dispatches_to_parent_for_catalog_sales(monkeypatch, tmp_path):
    harness = _StreamingHarness(tmp_path)
    called_with = {}

    def fake_generate_parent(output_dir, parent_table, child_tables):
        called_with["parent"] = parent_table
        called_with["children"] = child_tables

    monkeypatch.setattr(harness, "_generate_parent_table_with_children", fake_generate_parent)
    monkeypatch.setattr(harness, "_generate_single_table_streaming", lambda *a: None)

    harness._generate_table_with_streaming(tmp_path, "catalog_sales")

    assert called_with["parent"] == "catalog_sales"
    assert "catalog_returns" in called_with["children"]


def test_generate_table_with_streaming_dispatches_to_parent_for_store_sales(monkeypatch, tmp_path):
    harness = _StreamingHarness(tmp_path)
    called_with = {}

    def fake_generate_parent(output_dir, parent_table, child_tables):
        called_with["parent"] = parent_table

    monkeypatch.setattr(harness, "_generate_parent_table_with_children", fake_generate_parent)
    monkeypatch.setattr(harness, "_generate_single_table_streaming", lambda *a: None)

    harness._generate_table_with_streaming(tmp_path, "store_sales")
    assert called_with["parent"] == "store_sales"


def test_generate_table_with_streaming_dispatches_to_single_for_regular_table(monkeypatch, tmp_path):
    harness = _StreamingHarness(tmp_path)
    called = {}

    monkeypatch.setattr(harness, "_generate_single_table_streaming", lambda _d, name: called.__setitem__("name", name))
    monkeypatch.setattr(harness, "_generate_parent_table_with_children", lambda *a: None)

    harness._generate_table_with_streaming(tmp_path, "customer")
    assert called["name"] == "customer"


def test_generate_parent_table_with_children_routes_web_sales(monkeypatch, tmp_path):
    harness = _StreamingHarness(tmp_path)
    monkeypatch.setattr("benchbox.core.tpcds.generator.streaming.subprocess.run", lambda *a, **k: None)

    parent = tmp_path / "web_sales.dat"
    child = tmp_path / "web_returns.dat"
    parent.write_text("row1\nrow2\n")
    child.write_text("ret1\n")

    harness._generate_parent_table_with_children(tmp_path, "web_sales", ["web_returns"])

    assert "web_sales" in harness._manifest_entries
    assert "web_returns" in harness._manifest_entries


def test_generate_parent_table_with_children_cleanup_on_success(monkeypatch, tmp_path):
    harness = _StreamingHarness(tmp_path)
    monkeypatch.setattr("benchbox.core.tpcds.generator.streaming.subprocess.run", lambda *a, **k: None)

    parent = tmp_path / "store_sales.dat"
    parent.write_text("a\nb\n")

    harness._generate_parent_table_with_children(tmp_path, "store_sales", ["store_returns"])

    assert not parent.exists()


def test_generate_parent_table_with_children_subprocess_error(monkeypatch, tmp_path):
    from subprocess import CalledProcessError

    harness = _StreamingHarness(tmp_path)

    def _raise(*a, **k):
        raise CalledProcessError(returncode=5, cmd=["dsdgen"], stderr=b"out of memory")

    monkeypatch.setattr("benchbox.core.tpcds.generator.streaming.subprocess.run", _raise)

    with pytest.raises(RuntimeError, match="exit code 5"):
        harness._generate_parent_table_with_children(tmp_path, "catalog_sales", ["catalog_returns"])


def test_generate_single_table_streaming_compression_removes_dat(monkeypatch, tmp_path):
    harness = _StreamingHarness(tmp_path)
    monkeypatch.setattr("benchbox.core.tpcds.generator.streaming.subprocess.run", lambda *a, **k: None)

    dat_file = tmp_path / "call_center.dat"
    dat_file.write_text("row1\nrow2\nrow3\n")

    harness._generate_single_table_streaming(tmp_path, "call_center")

    assert not dat_file.exists()
    assert harness._manifest_entries["call_center"][0]["row_count"] == 3


def test_generate_single_table_streaming_skips_empty_dat(monkeypatch, tmp_path):
    harness = _StreamingHarness(tmp_path)
    monkeypatch.setattr("benchbox.core.tpcds.generator.streaming.subprocess.run", lambda *a, **k: None)

    dat_file = tmp_path / "empty_table.dat"
    dat_file.write_text("")

    harness._generate_single_table_streaming(tmp_path, "empty_table")

    assert "empty_table" not in harness._manifest_entries


class _NoCompressionHarness(_StreamingHarness):
    def should_use_compression(self) -> bool:
        return False

    def get_compressed_filename(self, expected_filename: str) -> str:
        return expected_filename


def test_generate_parent_chunk_no_compression_tracks_manifest(monkeypatch, tmp_path):
    harness = _NoCompressionHarness(tmp_path)
    monkeypatch.setattr("benchbox.core.tpcds.generator.streaming.subprocess.run", lambda *a, **k: None)

    (tmp_path / "store_sales_1_3.dat").write_text("row1\nrow2\n")
    (tmp_path / "store_returns_1_3.dat").write_text("row_a\n")

    harness._generate_parent_table_chunk_with_children(tmp_path, "store_sales", 1, ["store_returns"])

    assert "store_sales" in harness._manifest_entries
    assert harness._manifest_entries["store_sales"][0]["row_count"] == 2
    assert "store_returns" in harness._manifest_entries
    assert harness._manifest_entries["store_returns"][0]["row_count"] == 1


def test_generate_parent_chunk_no_compression_verbose(monkeypatch, tmp_path):
    harness = _NoCompressionHarness(tmp_path)
    harness.verbose = True
    monkeypatch.setattr("benchbox.core.tpcds.generator.streaming.subprocess.run", lambda *a, **k: None)

    (tmp_path / "store_sales_2_3.dat").write_text("a\n")

    harness._generate_parent_table_chunk_with_children(tmp_path, "store_sales", 2, [])
    assert "store_sales" in harness._manifest_entries


def test_generate_parent_chunk_called_process_error_with_stderr(monkeypatch, tmp_path):
    harness = _StreamingHarness(tmp_path)

    def _raise(*a, **k):
        from subprocess import CalledProcessError

        raise CalledProcessError(returncode=3, cmd=["dsdgen"], stderr=b"memory exhausted")

    monkeypatch.setattr("benchbox.core.tpcds.generator.streaming.subprocess.run", _raise)

    with pytest.raises(RuntimeError, match="memory exhausted"):
        harness._generate_parent_table_chunk_with_children(tmp_path, "catalog_sales", 1, ["catalog_returns"])


def test_generate_parent_chunk_generic_exception(monkeypatch, tmp_path):
    harness = _StreamingHarness(tmp_path)

    monkeypatch.setattr(
        "benchbox.core.tpcds.generator.streaming.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(RuntimeError, match="disk full"):
        harness._generate_parent_table_chunk_with_children(tmp_path, "web_sales", 1, [])


