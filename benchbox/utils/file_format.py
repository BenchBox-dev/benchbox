# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)

COMPRESSION_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".zst",
        ".gz",
        ".bz2",
        ".xz",
        ".lz4",
        ".snappy",
    }
)

DATA_FORMAT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".parquet",
        ".vortex",
        ".tbl",
        ".csv",
        ".dat",
    }
)

_COMPRESSION_NAMES: dict[str, str] = {
    ".zst": "zstd",
    ".gz": "gzip",
    ".bz2": "bzip2",
    ".xz": "xz",
    ".lz4": "lz4",
    ".snappy": "snappy",
}

_FORMAT_NAMES: dict[str, str] = {
    ".parquet": "parquet",
    ".vortex": "vortex",
    ".tbl": "tbl",
    ".csv": "csv",
    ".dat": "tbl",
}


def is_compression_extension(suffix: str) -> bool:
    if not suffix.startswith("."):
        suffix = f".{suffix}"
    return suffix.lower() in COMPRESSION_EXTENSIONS


def is_data_format_extension(suffix: str) -> bool:
    if not suffix.startswith("."):
        suffix = f".{suffix}"
    return suffix.lower() in DATA_FORMAT_EXTENSIONS


def detect_compression(path: Union[str, Path]) -> str | None:
    path = Path(path)
    suffix = path.suffix.lower()
    return _COMPRESSION_NAMES.get(suffix)


def detect_data_format(path: Union[str, Path]) -> str:
    path = Path(path)
    suffixes = [s.lower() for s in path.suffixes]

    for suffix in reversed(suffixes):
        if suffix in COMPRESSION_EXTENSIONS:
            continue
        if suffix in _FORMAT_NAMES:
            return _FORMAT_NAMES[suffix]

    if suffixes:
        non_compression_suffixes = [s for s in suffixes if s not in COMPRESSION_EXTENSIONS]
        if non_compression_suffixes:
            logger.debug(f"Unknown format extension(s) {non_compression_suffixes} in '{path}', defaulting to csv")
    return "csv"


def strip_compression_suffix(path: Union[str, Path]) -> Path:
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix in COMPRESSION_EXTENSIONS:
        return path.with_suffix("")

    return path


def get_data_extension(path: Union[str, Path]) -> str | None:
    path = Path(path)
    for suffix in reversed(path.suffixes):
        suffix_lower = suffix.lower()
        if suffix_lower in COMPRESSION_EXTENSIONS:
            continue
        if suffix_lower in DATA_FORMAT_EXTENSIONS:
            return suffix_lower
    return None


def get_base_name_without_compression(path: Union[str, Path]) -> str:
    path = Path(path)
    stripped = strip_compression_suffix(path)
    return stripped.name


def normalize_format_extension(suffix: str) -> str:
    if not suffix.startswith("."):
        suffix = f".{suffix}"
    suffix = suffix.lower()
    return _FORMAT_NAMES.get(suffix, "csv")


TPC_FORMAT_EXTENSIONS: frozenset[str] = frozenset({".tbl", ".dat"})


def is_tpc_format(path: Union[str, Path]) -> bool:
    path = Path(path)
    for suffix in path.suffixes:
        suffix_lower = suffix.lower()
        if suffix_lower in COMPRESSION_EXTENSIONS:
            continue
        return suffix_lower in TPC_FORMAT_EXTENSIONS
    return False


def get_delimiter_for_file(path: Union[str, Path]) -> str:
    return "|" if is_tpc_format(path) else ","


TRAILING_DUMMY_COLUMN: str = "_trailing_delimiter_"


def count_delimited_fields(line: str, delimiter: str) -> int:
    stripped = line.rstrip("\r\n")
    try:
        return len(next(csv.reader([stripped], delimiter=delimiter, quotechar='"', doublequote=True)))
    except (csv.Error, StopIteration):
        return len(stripped.split(delimiter))


def has_trailing_delimiter(
    path: Union[str, Path],
    delimiter: str,
    column_names: list[str] | None = None,
) -> bool:
    from benchbox.utils.compression import CompressionError, CompressionManager

    path = Path(path)

    if column_names is not None:
        expected = len(column_names)
        checker = lambda line: count_delimited_fields(line, delimiter) > expected
    else:
        checker = lambda line: line.rstrip("\n").endswith(delimiter)

    return _check_first_nonempty_line(path, checker, CompressionError, CompressionManager)


def _check_first_nonempty_line(path: Path, checker, CompressionError, CompressionManager) -> bool:
    compression_type = detect_compression(path)
    if compression_type:
        manager = CompressionManager()
        compressor = manager.get_compressor(compression_type)
        with compressor.open_for_read(path, mode="rt") as handle:
            for line in handle:
                if line.strip():
                    return checker(line)
        return False
    with path.open("rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.strip():
                return checker(line)
    return False


def get_column_names_with_trailing(column_names: list[str], has_trailing: bool) -> list[str]:
    if has_trailing:
        return column_names + [TRAILING_DUMMY_COLUMN]
    return column_names


def is_parquet_format(path: Union[str, Path]) -> bool:
    path = Path(path)
    for suffix in path.suffixes:
        suffix_lower = suffix.lower()
        if suffix_lower in COMPRESSION_EXTENSIONS:
            continue
        return suffix_lower == ".parquet"
    return False


def is_csv_format(path: Union[str, Path]) -> bool:
    path = Path(path)
    for suffix in path.suffixes:
        suffix_lower = suffix.lower()
        if suffix_lower in COMPRESSION_EXTENSIONS:
            continue
        return suffix_lower == ".csv"
    return False


def validate_tbl_compression_consistency(target_dir: Path, file_extension: str) -> None:
    raw_tbl = list(target_dir.glob("*.tbl"))
    if raw_tbl:
        names = ", ".join(f.name for f in raw_tbl[:5])
        more = "..." if len(raw_tbl) > 5 else ""
        raise RuntimeError(
            f"File format consistency violation: Found raw .tbl files with compression enabled: {names}{more}"
        )
    compressed = list(target_dir.glob(f"*.tbl{file_extension}"))
    empties = [f for f in compressed if f.stat().st_size <= (9 if file_extension == ".zst" else 20)]
    if empties:
        names = ", ".join(f.name for f in empties[:5])
        more = "..." if len(empties) > 5 else ""
        raise RuntimeError(f"File format consistency violation: Found empty compressed files: {names}{more}")
