# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from benchbox.core.tpch.generator import TPCHDataGenerator, normalize_tbl_trailing_delimiters

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_strips_exactly_one_trailing_pipe_per_row(tmp_path: Path) -> None:
    path = tmp_path / "customer.tbl"
    path.write_text("1|Customer#1|BUILDING|comment.|\n2|Customer#2|AUTOMOBILE|comment2.|\n", encoding="utf-8")

    rewritten = normalize_tbl_trailing_delimiters(path)

    assert rewritten is True
    assert path.read_text(encoding="utf-8") == "1|Customer#1|BUILDING|comment.\n2|Customer#2|AUTOMOBILE|comment2.\n"


def test_interior_empty_fields_are_preserved(tmp_path: Path) -> None:
    path = tmp_path / "orders.tbl"
    path.write_text("1||BUILDING||comment.|\n", encoding="utf-8")

    normalize_tbl_trailing_delimiters(path)

    assert path.read_text(encoding="utf-8") == "1||BUILDING||comment.\n"


def test_already_clean_file_is_left_untouched(tmp_path: Path) -> None:
    path = tmp_path / "nation.tbl"
    original = "0|ALGERIA|0|comment.\n1|ARGENTINA|1|comment2.\n"
    path.write_text(original, encoding="utf-8")
    mtime_before = path.stat().st_mtime_ns

    rewritten = normalize_tbl_trailing_delimiters(path)

    assert rewritten is False
    assert path.read_text(encoding="utf-8") == original
    assert path.stat().st_mtime_ns == mtime_before


def test_idempotent_on_repeated_calls(tmp_path: Path) -> None:
    path = tmp_path / "region.tbl"
    path.write_text("0|AFRICA|comment.|\n", encoding="utf-8")

    first = normalize_tbl_trailing_delimiters(path)
    second = normalize_tbl_trailing_delimiters(path)

    assert first is True
    assert second is False
    assert path.read_text(encoding="utf-8") == "0|AFRICA|comment.\n"


def test_chunk_boundary_carry_does_not_corrupt_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import benchbox.core.tpch.generator as generator_module

    monkeypatch.setattr(generator_module, "_NORMALIZE_CHUNK_SIZE", 5)
    path = tmp_path / "supplier.tbl.1"
    path.write_text("1|AB|\n2|CD|\n", encoding="utf-8")

    normalize_tbl_trailing_delimiters(path)

    assert path.read_text(encoding="utf-8") == "1|AB\n2|CD\n"


def test_finalize_generation_normalizes_fallback_file_mode_output(tmp_path: Path) -> None:
    dirty_path = tmp_path / "customer.tbl"
    dirty_path.write_text("1|Customer#1|BUILDING|comment.|\n", encoding="utf-8")

    gen = TPCHDataGenerator.__new__(TPCHDataGenerator)
    gen._data_organization_config = None
    gen.verbose_enabled = False
    gen.should_use_compression = MagicMock(return_value=False)
    gen._write_manifest = MagicMock()
    gen._gather_generated_table_paths = MagicMock(return_value=({"customer": dirty_path}, set()))
    gen.log_verbose = MagicMock()

    gen._finalize_generation(tmp_path)

    assert dirty_path.read_text(encoding="utf-8") == "1|Customer#1|BUILDING|comment.\n"


class TestCRLFFraming:
    def test_detects_and_strips_a_trailing_delimiter_under_crlf(self, tmp_path: Path) -> None:
        path = tmp_path / "customer.tbl"
        path.write_bytes(b"1|Customer#1|BUILDING|comment.|\r\n2|Customer#2|AUTOMOBILE|comment2.|\r\n")

        assert normalize_tbl_trailing_delimiters(path) is True
        assert path.read_bytes() == b"1|Customer#1|BUILDING|comment.\r\n2|Customer#2|AUTOMOBILE|comment2.\r\n"

    def test_interior_empty_fields_are_preserved_under_crlf(self, tmp_path: Path) -> None:
        path = tmp_path / "customer.tbl"
        path.write_bytes(b"1||BUILDING||comment.|\r\n")

        assert normalize_tbl_trailing_delimiters(path) is True
        assert path.read_bytes() == b"1||BUILDING||comment.\r\n"

    def test_already_clean_crlf_file_is_byte_stable(self, tmp_path: Path) -> None:
        original = b"1|Customer#1|BUILDING|comment.\r\n2|Customer#2|AUTOMOBILE|comment2.\r\n"
        path = tmp_path / "customer.tbl"
        path.write_bytes(original)

        assert normalize_tbl_trailing_delimiters(path) is False
        assert path.read_bytes() == original

    def test_idempotent_on_repeated_calls_under_crlf(self, tmp_path: Path) -> None:
        path = tmp_path / "region.tbl"
        path.write_bytes(b"0|AFRICA|comment.|\r\n")

        assert normalize_tbl_trailing_delimiters(path) is True
        after_first = path.read_bytes()
        assert normalize_tbl_trailing_delimiters(path) is False
        assert path.read_bytes() == after_first

    def test_chunk_boundary_inside_the_crlf_terminator(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import benchbox.core.tpch.generator as generator_module

        payload = b"1|AB|\r\n2|CD|\r\n"
        for chunk_size in range(1, len(payload) + 1):
            monkeypatch.setattr(generator_module, "_NORMALIZE_CHUNK_SIZE", chunk_size)
            path = tmp_path / f"supplier.tbl.{chunk_size}"
            path.write_bytes(payload)

            normalize_tbl_trailing_delimiters(path)

            assert path.read_bytes() == b"1|AB\r\n2|CD\r\n", f"corrupted at chunk size {chunk_size}"

    def test_unterminated_final_row_under_crlf(self, tmp_path: Path) -> None:
        path = tmp_path / "region.tbl"
        path.write_bytes(b"0|AFRICA|comment.|\r")

        assert normalize_tbl_trailing_delimiters(path) is True
        assert path.read_bytes() == b"0|AFRICA|comment.\r"


