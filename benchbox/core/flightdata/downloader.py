# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import calendar
import contextlib
import csv
import hashlib
import io
import json
import logging
import random
import shutil
import urllib.error
import urllib.request
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from benchbox.core.data_fetch.errors import ChecksumMismatchError
from benchbox.utils import tls
from benchbox.utils.compression_mixin import CompressionMixin
from benchbox.utils.datagen_manifest import (
    MANIFEST_FILENAME,
    DataGenerationManifest,
    load_manifest,
    require_manifest_files,
    resolve_compression_metadata,
)
from benchbox.utils.verbosity import VerbosityMixin, compute_verbosity

logger = logging.getLogger(__name__)


def _load_downloader_specs() -> dict[str, Any]:
    with (Path(__file__).with_name("downloader_specs.yaml")).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


_DOWNLOADER_SPECS = _load_downloader_specs()
_DATA_COVERAGE = _DOWNLOADER_SPECS["data_coverage"]
_FLIGHT_SHARDS = _DOWNLOADER_SPECS["flight_shards"]

BTS_BASE_URL = _DOWNLOADER_SPECS["bts_base_url"]
BTS_CSV_ENCODING = str(_DOWNLOADER_SPECS["csv_encoding"])

FIRST_AVAILABLE_YEAR = int(_DATA_COVERAGE["first_available_year"])
LAST_AVAILABLE_YEAR = int(_DATA_COVERAGE["last_available_year"])
APPROXIMATE_MONTHLY_FLIGHTS = int(_DATA_COVERAGE["approximate_monthly_flights"])
MONTHS_PER_SCALE_FACTOR = int(_DATA_COVERAGE["months_per_scale_factor"])


def _unavailable_months() -> set[tuple[int, int]]:
    missing: set[tuple[int, int]] = set()
    for entry in _DATA_COVERAGE.get("unavailable_months") or []:
        start_year, start_month, end_year, end_month = (int(v) for v in entry)
        year, month = start_year, start_month
        while (year, month) <= (end_year, end_month):
            missing.add((year, month))
            month += 1
            if month == 13:
                month = 1
                year += 1
    return missing


UNAVAILABLE_MONTHS = _unavailable_months()
FLIGHTS_SHARD_ROW_TARGET = int(_FLIGHT_SHARDS["row_target"])
FLIGHTS_SHARD_DIRNAME = _FLIGHT_SHARDS["dirname"]
FLIGHTS_SHARD_PREFIX = _FLIGHT_SHARDS["prefix"]

BTS_FIELD_NAMES = _DOWNLOADER_SPECS["bts_field_names"]

_PINNED_SOURCE = _DOWNLOADER_SPECS.get("pinned_source") or {}
PINNED_END_YEAR = int(_PINNED_SOURCE.get("end_year", LAST_AVAILABLE_YEAR))
PINNED_END_MONTH = int(_PINNED_SOURCE.get("end_month", 12))
PINNED_SOURCE_SHA256 = {str(url): str(digest) for url, digest in (_PINNED_SOURCE.get("sha256") or {}).items()}


def _scale_to_months(scale_factor: float) -> int:
    months = max(1, round(scale_factor * MONTHS_PER_SCALE_FACTOR))
    max_months = (LAST_AVAILABLE_YEAR - FIRST_AVAILABLE_YEAR + 1) * 12
    if months >= max_months:
        ceiling_gb = max_months / MONTHS_PER_SCALE_FACTOR
        logger.warning(
            "FlightData: BTS corpus exhausted at SF=%.1f (all %d months used). "
            "Data size is capped at ~%.1f GB regardless of scale factor.",
            scale_factor,
            max_months,
            ceiling_gb,
        )
    return min(months, max_months)


def _months_sequence(
    num_months: int,
    end_year: int = PINNED_END_YEAR,
    end_month: int = PINNED_END_MONTH,
) -> list[tuple[int, int]]:
    result = []
    year, month = end_year, end_month
    for _ in range(num_months):
        if (year, month) in UNAVAILABLE_MONTHS:
            logger.warning(
                "FlightData: BTS archive for %d-%02d is unavailable; substituting an older available month.",
                year,
                month,
            )
        else:
            result.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
        if year < FIRST_AVAILABLE_YEAR:
            break
    while len(result) < num_months and year >= FIRST_AVAILABLE_YEAR:
        if (year, month) not in UNAVAILABLE_MONTHS:
            result.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return result


class FlightDataDownloader(CompressionMixin, VerbosityMixin):
    def __init__(
        self,
        scale_factor: float,
        output_dir: Path,
        seed: int | None = None,
        verbose: int | bool = 0,
        quiet: bool = False,
        force_redownload: bool = False,
        allow_synthetic_fallback: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)

        self.scale_factor = scale_factor
        self.output_dir = Path(output_dir)
        self.seed = seed if seed is not None else 42
        self.force_redownload = force_redownload
        self.allow_synthetic_fallback = allow_synthetic_fallback
        self.logger = logger
        verbosity_settings = compute_verbosity(verbose, quiet)
        self.apply_verbosity(verbosity_settings)
        self._rng = random.Random(self.seed)
        self._num_months = _scale_to_months(scale_factor)
        self._months = _months_sequence(self._num_months)
        if len(self._months) < self._num_months:
            logger.warning(
                "FlightData: only %d of %d requested months are available; "
                "corpus is capped at the downloadable window.",
                len(self._months),
                self._num_months,
            )
            self._num_months = len(self._months)
        self._stats: dict[str, Any] = {
            "source": "bts-transtats",
            "scale_factor": scale_factor,
            "num_months": self._num_months,
            "months": list(self._months),
            "months_downloaded": 0,
            "months_synthetic": 0,
            "downloaded_months": [],
            "synthetic_months": [],
            "total_flights": 0,
        }
        self._table_row_counts: dict[str, int] = {}
        self._table_file_row_counts: dict[Path, int] = {}
        self._content_hashes: dict[str, str] = {}

    def source_provenance(self) -> dict[str, Any]:
        urls = set(self.source_contract()["urls"])
        expected = {url: PINNED_SOURCE_SHA256[url] for url in urls if url in PINNED_SOURCE_SHA256}
        observed = {url: self._content_hashes[url] for url in urls if url in self._content_hashes}
        synthetic_count = int(self._stats["months_synthetic"])
        downloaded_count = int(self._stats["months_downloaded"])
        if synthetic_count and downloaded_count:
            source = "mixed"
        elif synthetic_count:
            source = "synthetic"
        elif downloaded_count:
            source = "remote"
        else:
            source = "unknown"
        eligible = (
            bool(urls) and not synthetic_count and set(expected) == urls == set(observed) and expected == observed
        )
        return {
            "source": source,
            "months_downloaded": downloaded_count,
            "months_synthetic": synthetic_count,
            "downloaded_months": list(self._stats["downloaded_months"]),
            "synthetic_months": list(self._stats["synthetic_months"]),
            "expected_sha256": expected,
            "observed_sha256": observed,
            "promotion_eligible": eligible,
        }

    def download(self) -> dict[str, Path | list[Path]]:
        self.output_dir.mkdir(parents=True, exist_ok=True)

        if not self.force_redownload:
            persisted_id = self._persisted_source_contract_id()
            if persisted_id is None:
                self.force_redownload = True
            elif persisted_id != self.source_contract_id():
                logger.warning(
                    "Existing flightdata corpus was generated under a different source contract "
                    "(%s...); regenerating for the current pin (%s...).",
                    persisted_id[:12],
                    self.source_contract_id()[:12],
                )
                self.force_redownload = True
            else:
                try:
                    persisted_manifest = load_manifest(self.output_dir / MANIFEST_FILENAME)
                except (OSError, ValueError):
                    persisted_manifest = {}
                if self.manifest_matches_source_identity(persisted_manifest):
                    self._restore_persisted_provenance()
                else:
                    logger.warning("FlightData cache lacks complete month provenance; regenerating")
                    self.force_redownload = True

        flights_path = self.output_dir / self.get_compressed_filename("flights.csv")
        airlines_path = self.output_dir / self.get_compressed_filename("airlines.csv")
        airports_path = self.output_dir / self.get_compressed_filename("airports.csv")

        if not airlines_path.exists() or self.force_redownload:
            self._copy_reference_file("airlines.csv", airlines_path)

        if not airports_path.exists() or self.force_redownload:
            self._copy_reference_file("airports.csv", airports_path)

        if airlines_path.exists() and airlines_path not in self._table_file_row_counts:
            self._record_existing_csv_file("airlines", airlines_path)
        if airports_path.exists() and airports_path not in self._table_file_row_counts:
            self._record_existing_csv_file("airports", airports_path)

        try:
            flight_files = self._ensure_flights_data(flights_path)
        except (ChecksumMismatchError, RuntimeError):
            self.force_redownload = True
            self._remove_flights_outputs(flights_path)
            with contextlib.suppress(OSError):
                (self.output_dir / MANIFEST_FILENAME).unlink()
            raise

        table_files = {
            "flights": flight_files,
            "airlines": airlines_path,
            "airports": airports_path,
        }
        if self._table_file_row_counts:
            self._write_manifest(table_files)
        return table_files

    def backfill_csv_dialect_metadata(self) -> bool:
        manifest_path = Path(self.output_dir) / MANIFEST_FILENAME
        try:
            manifest = load_manifest(manifest_path)
        except (OSError, ValueError):
            return False
        if str(manifest.get("benchmark", "")).lower() != "flightdata":
            return False
        changed = False
        tables = manifest.get("tables", {}) or {}
        for formats in tables.values():
            if not isinstance(formats, dict):
                continue
            inner = formats.get("formats")
            if not isinstance(inner, dict):
                continue
            for format_name, entries in inner.items():
                if format_name != "csv" or not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    metadata = entry.get("metadata")
                    if not isinstance(metadata, dict):
                        continue
                    if metadata.get("csv_null_marker") != "":
                        metadata["csv_null_marker"] = ""
                        changed = True
        if not changed:
            return False
        try:
            tmp_path = manifest_path.with_name(manifest_path.name + ".tmp")
            tmp_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            tmp_path.replace(manifest_path)
        except OSError:
            logger.warning("Could not heal CSV dialect metadata in %s; continuing", manifest_path)
            return False
        return True

    def repair_reusable_layout(self) -> dict[str, Path | list[Path]] | None:
        if not self._should_use_sharded_flights():
            return None

        if self._existing_flight_shards():
            return None

        legacy_path = self._find_legacy_single_flights_path()
        if legacy_path is None:
            return None

        self.log_verbose(f"Repairing FlightData cache layout by sharding {legacy_path.name}")
        shard_paths = self._split_existing_flights_file(legacy_path)
        if not shard_paths:
            return None

        airlines_path = self._existing_reference_path("airlines")
        airports_path = self._existing_reference_path("airports")
        if airlines_path is None or airports_path is None:
            return None

        self._record_existing_csv_file("airlines", airlines_path)
        self._record_existing_csv_file("airports", airports_path)
        table_files: dict[str, Path | list[Path]] = {
            "flights": shard_paths,
            "airlines": airlines_path,
            "airports": airports_path,
        }
        self._write_manifest(table_files)

        with contextlib.suppress(OSError):
            legacy_path.unlink()

        return table_files

    def _copy_reference_file(self, filename: str, dest: Path) -> None:
        ref_dir = Path(__file__).parent / "reference_data"
        src = ref_dir / filename
        if src.exists():
            row_count = 0
            with src.open(encoding="utf-8") as source, self.open_output_file(dest, "wt") as target:
                for line in source:
                    target.write(line)
                    row_count += 1
            table_name = dest.name.split(".")[0]
            self._table_row_counts[table_name] = max(0, row_count - 1)
            self._table_file_row_counts[dest] = max(0, row_count - 1)
            self.log_verbose(f"Copied reference data: {filename}")
        else:
            logger.warning(f"Reference file not found: {src}")

    def _ensure_flights_data(self, flights_path: Path) -> Path | list[Path]:
        if self.force_redownload:
            self._remove_flights_outputs(flights_path)

        if self._should_use_sharded_flights():
            shard_paths = self._existing_flight_shards()
            if shard_paths and not self.force_redownload:
                for shard_path in shard_paths:
                    self._record_existing_csv_file("flights", shard_path)
                return shard_paths

            repaired = self.repair_reusable_layout()
            if repaired and isinstance(repaired.get("flights"), list):
                return repaired["flights"]

            return self._generate_flights_shards()

        if not flights_path.exists():
            self._generate_flights_csv(flights_path)
        elif flights_path not in self._table_file_row_counts:
            self._record_existing_csv_file("flights", flights_path)
        return flights_path

    def _should_use_sharded_flights(self) -> bool:
        return self._num_months >= MONTHS_PER_SCALE_FACTOR

    def _remove_flights_outputs(self, flights_path: Path) -> None:
        with contextlib.suppress(OSError):
            flights_path.unlink()
        for candidate in self.output_dir.glob("flights.csv.*"):
            with contextlib.suppress(OSError):
                candidate.unlink()
        shutil.rmtree(self._flights_shard_dir(), ignore_errors=True)

    def _flights_shard_dir(self) -> Path:
        return self.output_dir / FLIGHTS_SHARD_DIRNAME

    def _existing_flight_shards(self) -> list[Path]:
        shard_dir = self._flights_shard_dir()
        if not shard_dir.is_dir():
            return []
        return sorted(path for path in shard_dir.glob(f"{FLIGHTS_SHARD_PREFIX}*.csv*") if path.is_file())

    def _existing_reference_path(self, table_name: str) -> Path | None:
        for candidate in sorted(self.output_dir.glob(f"{table_name}.csv*")):
            if candidate.is_file():
                return candidate
        return None

    def _find_legacy_single_flights_path(self) -> Path | None:
        manifest_path = self.output_dir / "_datagen_manifest.json"
        if manifest_path.exists():
            with contextlib.suppress(Exception):
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                flights_data = manifest.get("tables", {}).get("flights")
                formats = flights_data.get("formats", {}) if isinstance(flights_data, dict) else {}
                entries = [entry for fmt_entries in formats.values() for entry in (fmt_entries or [])]
                if len(entries) == 1:
                    rel = entries[0].get("path")
                    if rel:
                        path = self.output_dir / rel
                        if path.is_file() and path.name.startswith("flights.csv"):
                            return path

        for candidate in sorted(self.output_dir.glob("flights.csv*")):
            if candidate.is_file():
                return candidate
        return None

    def _generate_flights_csv(self, output_path: Path) -> int:
        self.log_verbose(f"Generating flight data for {self._num_months} months (SF={self.scale_factor})")

        with self.open_output_file(output_path, "wt") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "flight_id",
                    "flight_date",
                    "year",
                    "month",
                    "day_of_month",
                    "day_of_week",
                    "reporting_airline",
                    "flight_number",
                    "origin",
                    "dest",
                    "crs_dep_time",
                    "dep_time",
                    "dep_delay",
                    "crs_arr_time",
                    "arr_time",
                    "arr_delay",
                    "cancelled",
                    "cancellation_code",
                    "diverted",
                    "crs_elapsed_time",
                    "actual_elapsed_time",
                    "air_time",
                    "distance",
                    "carrier_delay",
                    "weather_delay",
                    "nas_delay",
                    "security_delay",
                    "late_aircraft_delay",
                ]
            )

            flight_id = 1
            for year, month in self._months:
                rows_written = self._process_month(writer, year, month, flight_id)
                flight_id += rows_written
                self._stats["total_flights"] += rows_written

        self._table_row_counts["flights"] = self._stats["total_flights"]
        self._table_file_row_counts[output_path] = self._stats["total_flights"]
        self.log_verbose(f"Wrote {self._stats['total_flights']:,} flights to {output_path}")
        return self._stats["total_flights"]

    def _generate_flights_shards(self) -> list[Path]:
        self.log_verbose(f"Generating sharded flight data for {self._num_months} months (SF={self.scale_factor})")
        shard_dir = self._flights_shard_dir()
        shutil.rmtree(shard_dir, ignore_errors=True)
        shard_dir.mkdir(parents=True, exist_ok=True)

        shard_paths: list[Path] = []
        flight_id = 1
        for shard_index, (year, month) in enumerate(self._months, start=1):
            shard_name = self.get_compressed_filename(f"{FLIGHTS_SHARD_PREFIX}{shard_index:04d}_{year}_{month:02d}.csv")
            shard_path = shard_dir / shard_name
            rows_written = self._write_flights_shard(shard_path, year, month, flight_id)
            flight_id += rows_written
            self._stats["total_flights"] += rows_written
            self._table_file_row_counts[shard_path] = rows_written
            shard_paths.append(shard_path)

        self._table_row_counts["flights"] = self._stats["total_flights"]
        self.log_verbose(f"Wrote {self._stats['total_flights']:,} flights across {len(shard_paths)} shards")
        return shard_paths

    def _write_flights_shard(self, shard_path: Path, year: int, month: int, flight_id: int) -> int:
        with self.open_output_file(shard_path, "wt") as f:
            writer = csv.writer(f)
            writer.writerow(self._flight_header())
            return self._process_month(writer, year, month, flight_id)

    @staticmethod
    def _flight_header() -> list[str]:
        return [
            "flight_id",
            "flight_date",
            "year",
            "month",
            "day_of_month",
            "day_of_week",
            "reporting_airline",
            "flight_number",
            "origin",
            "dest",
            "crs_dep_time",
            "dep_time",
            "dep_delay",
            "crs_arr_time",
            "arr_time",
            "arr_delay",
            "cancelled",
            "cancellation_code",
            "diverted",
            "crs_elapsed_time",
            "actual_elapsed_time",
            "air_time",
            "distance",
            "carrier_delay",
            "weather_delay",
            "nas_delay",
            "security_delay",
            "late_aircraft_delay",
        ]

    def _split_existing_flights_file(self, legacy_path: Path) -> list[Path]:
        staging_dir = self.output_dir / ".flights-shards.tmp"
        final_dir = self._flights_shard_dir()
        shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)

        shard_paths: list[Path] = []
        shard_counts: dict[Path, int] = {}
        shard_index = 0
        rows_in_shard = 0
        target_cm: Any = None
        target_f: Any = None
        writer: csv.writer | None = None

        def close_target() -> None:
            nonlocal target_cm, target_f, writer, rows_in_shard
            if target_cm is not None:
                target_cm.__exit__(None, None, None)
            target_cm = None
            target_f = None
            writer = None
            rows_in_shard = 0

        def open_target(header: list[str]) -> tuple[csv.writer, Path]:
            nonlocal target_cm, target_f, shard_index, rows_in_shard
            shard_index += 1
            shard_name = self.get_compressed_filename(f"{FLIGHTS_SHARD_PREFIX}{shard_index:04d}.csv")
            shard_path = staging_dir / shard_name
            target_cm = self.open_output_file(shard_path, "wt")
            target_f = target_cm.__enter__()
            shard_writer = csv.writer(target_f)
            shard_writer.writerow(header)
            rows_in_shard = 0
            shard_paths.append(shard_path)
            shard_counts[shard_path] = 0
            return shard_writer, shard_path

        try:
            with self._open_existing_csv_for_read(legacy_path) as source:
                reader = csv.reader(source)
                header = next(reader, None)
                if header is None:
                    raise ValueError(f"FlightData source file {legacy_path} is empty")
                self._validate_flights_header(header, legacy_path)
                current_path: Path | None = None
                for line_number, row in enumerate(reader, start=2):
                    self._validate_flight_row_width(row, line_number, legacy_path, len(header))
                    if writer is None or rows_in_shard >= FLIGHTS_SHARD_ROW_TARGET:
                        close_target()
                        writer, current_path = open_target(header)
                    writer.writerow(row)
                    rows_in_shard += 1
                    assert current_path is not None
                    shard_counts[current_path] += 1
                    self._stats["total_flights"] += 1
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise
        finally:
            close_target()

        shutil.rmtree(final_dir, ignore_errors=True)
        staging_dir.replace(final_dir)

        final_paths: list[Path] = []
        final_counts: dict[Path, int] = {}
        for shard_path in shard_paths:
            final_path = final_dir / shard_path.name
            final_paths.append(final_path)
            final_counts[final_path] = shard_counts[shard_path]
            self._table_file_row_counts[final_path] = shard_counts[shard_path]

        self._table_row_counts["flights"] = sum(final_counts.values())
        return final_paths

    def _open_existing_csv_for_read(self, path: Path) -> Any:
        compression_type = self.compression_manager.detect_compression(path)
        compressor = self.compression_manager.get_compressor(compression_type)
        return compressor.open_for_read(path, "rt")

    def _record_existing_csv_file(self, table_name: str, path: Path) -> None:
        if path in self._table_file_row_counts:
            return
        row_count = self._count_flight_csv_rows(path) if table_name == "flights" else self._count_csv_rows(path)
        self._table_row_counts[table_name] = self._table_row_counts.get(table_name, 0) + row_count
        self._table_file_row_counts[path] = row_count

    def _count_csv_rows(self, path: Path) -> int:
        with self._open_existing_csv_for_read(path) as f:
            return max(0, sum(1 for _ in f) - 1)

    def _count_flight_csv_rows(self, path: Path) -> int:
        with self._open_existing_csv_for_read(path) as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header is None:
                raise ValueError(f"FlightData source file {path} is empty")
            self._validate_flights_header(header, path)
            row_count = 0
            for line_number, row in enumerate(reader, start=2):
                self._validate_flight_row_width(row, line_number, path, len(header))
                row_count += 1
            return row_count

    def _validate_flights_header(self, header: list[str], path: Path) -> None:
        expected = self._flight_header()
        if header != expected:
            raise ValueError(
                f"FlightData source file {path} has invalid header: expected {len(expected)} columns "
                f"{expected!r}, found {len(header)} columns {header!r}"
            )

    @staticmethod
    def _validate_flight_row_width(row: list[str], line_number: int, path: Path, expected_columns: int) -> None:
        if len(row) != expected_columns:
            raise ValueError(
                f"FlightData source file {path} row {line_number} has {len(row)} columns; expected {expected_columns}"
            )

    def _write_manifest(self, table_files: dict[str, Path | list[Path]]) -> None:
        manifest = DataGenerationManifest(
            output_dir=self.output_dir,
            benchmark="flightdata",
            scale_factor=self.scale_factor,
            compression=resolve_compression_metadata(self),
            parallel=1,
            seed=self.seed,
            formats=["csv"],
            extra_metadata={
                "source_contract": self.source_contract(),
                "source_contract_id": self.source_contract_id(),
                "content_hashes": dict(self._content_hashes),
                "source_provenance": self.source_provenance(),
            },
        )
        metadata = {
            "csv_delimiter": ",",
            "csv_has_header": True,
            "csv_null_marker": "",
        }
        for table_name, paths_or_path in table_files.items():
            paths = paths_or_path if isinstance(paths_or_path, list) else [paths_or_path]
            for path in paths:
                manifest.add_entry(
                    table_name,
                    path,
                    row_count=self._table_file_row_counts.get(path, 0),
                    format="csv",
                    metadata=metadata,
                )
        require_manifest_files(manifest.file_counts()[1], label="FlightData", output_dir=self.output_dir)
        manifest.write()

    def _process_month(self, writer: csv.writer, year: int, month: int, start_id: int) -> int:
        if self.scale_factor < 0.1:
            self._stats["months_synthetic"] += 1
            self._stats["synthetic_months"].append(f"{year}-{month:02d}")
            return self._generate_synthetic_month(writer, year, month, start_id)

        url = BTS_BASE_URL.format(year=year, month=month)
        try:
            rows = self._download_bts_month(writer, url, year, month, start_id)
            self._stats["months_downloaded"] += 1
            self._stats["downloaded_months"].append(f"{year}-{month:02d}")
            return rows
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, zipfile.BadZipFile, ValueError) as e:
            if not self.allow_synthetic_fallback:
                raise RuntimeError(f"BTS download failed for {year}-{month:02d}; real data is required") from e
            logger.warning(f"BTS download failed for {year}-{month:02d}: {e}. Using synthetic data.")
            rows = self._generate_synthetic_month(writer, year, month, start_id)
            self._stats["months_synthetic"] += 1
            self._stats["synthetic_months"].append(f"{year}-{month:02d}")
            return rows

    def _download_bts_month(self, writer: csv.writer, url: str, year: int, month: int, start_id: int) -> int:
        self.log_verbose(f"  Downloading BTS data: {year}-{month:02d}")

        req = urllib.request.Request(
            url, headers={"User-Agent": "BenchBox/1.0 (https://github.com/BenchBox-dev/BenchBox)"}
        )
        with tls.urlopen(req, timeout=120) as response:
            zip_bytes = response.read()
        actual_sha256 = hashlib.sha256(zip_bytes).hexdigest()
        expected_sha256 = PINNED_SOURCE_SHA256.get(url)
        if expected_sha256 is not None and actual_sha256 != expected_sha256:
            raise ChecksumMismatchError(path=url, expected_sha256=expected_sha256, actual_sha256=actual_sha256)
        rows_written = 0
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not csv_names:
                raise ValueError(f"No CSV found in ZIP from {url}")

            with zf.open(csv_names[0]) as csv_file:
                reader = csv.DictReader(io.TextIOWrapper(csv_file, encoding=BTS_CSV_ENCODING))

                for bts_row in reader:
                    row = self._transform_bts_row(bts_row, start_id + rows_written)
                    if row is not None:
                        writer.writerow(row)
                        rows_written += 1

        self._content_hashes[url] = actual_sha256
        return rows_written

    def _transform_bts_row(self, bts: dict[str, str], flight_id: int) -> list[Any] | None:

        def _float_or_null(val: str) -> str:
            v = val.strip()
            if v in ("", ".", "NA", "N/A"):
                return ""
            try:
                return str(float(v))
            except ValueError:
                return ""

        def _int_or_null(val: str) -> str:
            v = val.strip()
            if v in ("", ".", "NA", "N/A"):
                return ""
            try:
                return str(int(float(v)))
            except ValueError:
                return ""

        flight_date = bts.get("FlightDate", "").strip()
        if not flight_date:
            return None

        cancelled = _int_or_null(bts.get("Cancelled", "0")) or "0"

        return [
            flight_id,
            flight_date,
            _int_or_null(bts.get("Year", "")),
            _int_or_null(bts.get("Month", "")),
            _int_or_null(bts.get("DayofMonth", "")),
            _int_or_null(bts.get("DayOfWeek", "")),
            bts.get("Reporting_Airline", "").strip(),
            _int_or_null(bts.get("Flight_Number_Reporting_Airline", "")),
            bts.get("Origin", "").strip(),
            bts.get("Dest", "").strip(),
            _int_or_null(bts.get("CRSDepTime", "")),
            _float_or_null(bts.get("DepTime", "")),
            _float_or_null(bts.get("DepDelay", "")),
            _int_or_null(bts.get("CRSArrTime", "")),
            _float_or_null(bts.get("ArrTime", "")),
            _float_or_null(bts.get("ArrDelay", "")),
            cancelled,
            bts.get("CancellationCode", "").strip(),
            _int_or_null(bts.get("Diverted", "0")) or "0",
            _float_or_null(bts.get("CRSElapsedTime", "")),
            _float_or_null(bts.get("ActualElapsedTime", "")),
            _float_or_null(bts.get("AirTime", "")),
            _float_or_null(bts.get("Distance", "")),
            _float_or_null(bts.get("CarrierDelay", "")),
            _float_or_null(bts.get("WeatherDelay", "")),
            _float_or_null(bts.get("NASDelay", "")),
            _float_or_null(bts.get("SecurityDelay", "")),
            _float_or_null(bts.get("LateAircraftDelay", "")),
        ]

    def _generate_synthetic_month(self, writer: csv.writer, year: int, month: int, start_id: int) -> int:
        rng = self._rng

        carriers = ["AA", "DL", "UA", "WN", "B6", "AS", "NK", "F9", "HA", "OO"]
        airports = [
            "ATL",
            "LAX",
            "ORD",
            "DFW",
            "DEN",
            "JFK",
            "SFO",
            "SEA",
            "LAS",
            "MCO",
            "EWR",
            "PHX",
            "IAH",
            "MIA",
            "BOS",
            "MSP",
            "DTW",
            "FLL",
            "PHL",
            "LGA",
            "BWI",
            "SLC",
            "CLT",
            "IAD",
            "DCA",
            "MDW",
            "TPA",
            "PDX",
            "SAN",
            "AUS",
        ]

        route_distances = {
            ("ATL", "LAX"): 1946,
            ("ATL", "ORD"): 587,
            ("ATL", "DFW"): 731,
            ("LAX", "SFO"): 337,
            ("LAX", "SEA"): 954,
            ("LAX", "LAS"): 236,
            ("ORD", "JFK"): 740,
            ("ORD", "DFW"): 802,
            ("DFW", "LAX"): 1235,
            ("JFK", "LAX"): 2475,
            ("JFK", "MIA"): 1090,
            ("SFO", "SEA"): 679,
            ("DEN", "LAX"): 862,
            ("DEN", "ORD"): 888,
            ("PHX", "LAX"): 370,
        }

        _, days_in_month = calendar.monthrange(year, month)

        if year == 2020:
            daily_flights = rng.randint(5000, 12000)
        elif year == 2021:
            daily_flights = rng.randint(12000, 18000)
        else:
            daily_flights = rng.randint(18000, 25000)

        dest_by_origin = {a: [b for b in airports if b != a] for a in airports}

        rows_written = 0

        for day in range(1, days_in_month + 1):
            flight_date = date(year, month, day)
            day_of_week = flight_date.weekday() + 1
            day_count = int(daily_flights * (0.85 if day_of_week >= 6 else 1.0))

            for _ in range(day_count):
                carrier = rng.choice(carriers)
                origin = rng.choice(airports)
                dest = rng.choice(dest_by_origin[origin])

                dist = route_distances.get((origin, dest), route_distances.get((dest, origin), rng.randint(200, 2500)))

                dep_hour = rng.randint(5, 22)
                dep_min = rng.choice([0, 15, 30, 45])
                crs_dep = dep_hour * 100 + dep_min

                crs_elapsed = max(60, int(60 + (dist / 500.0) * 45))
                arr_total_min = dep_hour * 60 + dep_min + crs_elapsed
                crs_arr = (arr_total_min // 60 % 24) * 100 + (arr_total_min % 60)

                cancel_prob = 0.03 if month in (1, 2, 12) else 0.015
                cancelled = 1 if rng.random() < cancel_prob else 0

                if cancelled:
                    cancel_codes = ["A", "A", "B", "B", "C", "D"]
                    cancellation_code = rng.choice(cancel_codes)
                    dep_time = arr_time = dep_delay = arr_delay = ""
                    actual_elapsed = air_time = ""
                    carrier_delay = weather_delay = nas_delay = sec_delay = late_delay = ""
                    diverted = 0
                else:
                    cancellation_code = ""
                    delay_prob = 0.22
                    if rng.random() < delay_prob:
                        dep_delay_min = rng.expovariate(1 / 25)
                        dep_delay_min = min(dep_delay_min, 300)
                    else:
                        dep_delay_min = rng.uniform(-10, 15)

                    dep_delay_min = round(dep_delay_min, 1)

                    actual_dep_min = dep_hour * 60 + dep_min + dep_delay_min
                    dep_time = (int(actual_dep_min // 60) % 24) * 100 + round(actual_dep_min % 60)

                    recovery = rng.uniform(0, min(abs(dep_delay_min) * 0.3, 15))
                    arr_delay_min = round(dep_delay_min - recovery, 1)

                    actual_arr_min = arr_total_min + arr_delay_min
                    arr_time = (int(actual_arr_min // 60) % 24) * 100 + round(actual_arr_min % 60)

                    actual_elapsed = crs_elapsed + arr_delay_min - dep_delay_min + recovery
                    air_time = actual_elapsed * 0.85

                    dep_delay = dep_delay_min
                    arr_delay = arr_delay_min
                    diverted = 1 if rng.random() < 0.003 else 0

                    if arr_delay_min > 15:
                        remaining = arr_delay_min
                        carrier_delay = round(remaining * rng.uniform(0, 0.5), 1)
                        remaining -= carrier_delay
                        weather_delay = round(remaining * rng.uniform(0, 0.4), 1)
                        remaining -= weather_delay
                        nas_delay = round(remaining * rng.uniform(0, 0.4), 1)
                        remaining -= nas_delay
                        sec_delay = round(remaining * rng.uniform(0, 0.05), 1)
                        late_delay = round(arr_delay_min - carrier_delay - weather_delay - nas_delay - sec_delay, 1)
                    else:
                        carrier_delay = weather_delay = nas_delay = sec_delay = late_delay = ""

                writer.writerow(
                    [
                        start_id + rows_written,
                        flight_date.isoformat(),
                        year,
                        month,
                        day,
                        day_of_week,
                        carrier,
                        rng.randint(1, 9999),
                        origin,
                        dest,
                        crs_dep,
                        dep_time if dep_time != "" else "",
                        dep_delay if dep_delay != "" else "",
                        crs_arr,
                        arr_time if arr_time != "" else "",
                        arr_delay if arr_delay != "" else "",
                        cancelled,
                        cancellation_code,
                        diverted,
                        crs_elapsed,
                        round(actual_elapsed, 1) if actual_elapsed != "" else "",
                        round(air_time, 1) if air_time != "" else "",
                        dist,
                        carrier_delay,
                        weather_delay,
                        nas_delay,
                        sec_delay,
                        late_delay,
                    ]
                )
                rows_written += 1

        return rows_written

    @property
    def months(self) -> list[tuple[int, int]]:
        return self._months

    @property
    def num_months(self) -> int:
        return self._num_months

    def source_contract(self) -> dict[str, Any]:
        return {
            "source": "bts-transtats",
            "base_url": BTS_BASE_URL,
            "csv_encoding": BTS_CSV_ENCODING,
            "allow_synthetic_fallback": self.allow_synthetic_fallback,
            "months": list(self._months),
            "urls": [BTS_BASE_URL.format(year=year, month=month) for year, month in self._months],
            "expected_sha256": {
                url: PINNED_SOURCE_SHA256[url]
                for url in [BTS_BASE_URL.format(year=year, month=month) for year, month in self._months]
                if url in PINNED_SOURCE_SHA256
            },
        }

    def source_contract_id(self) -> str:
        canonical = json.dumps(self.source_contract(), sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def manifest_matches_source_identity(self, manifest: dict[str, Any]) -> bool:
        if manifest.get("source_contract_id") != self.source_contract_id():
            return False
        provenance = manifest.get("source_provenance")
        if not isinstance(provenance, dict):
            return False
        expected = {f"{year}-{month:02d}" for year, month in self._months}
        downloaded = provenance.get("downloaded_months")
        synthetic = provenance.get("synthetic_months")
        if not isinstance(downloaded, list) or not isinstance(synthetic, list):
            return False
        if not all(isinstance(month, str) for month in downloaded + synthetic):
            return False
        if len(downloaded) + len(synthetic) != len(expected) or set(downloaded) & set(synthetic):
            return False
        if set(downloaded + synthetic) != expected:
            return False
        if provenance.get("months_downloaded") != len(downloaded):
            return False
        if provenance.get("months_synthetic") != len(synthetic):
            return False
        return self.scale_factor < 0.1 or self.allow_synthetic_fallback or not synthetic

    def _persisted_source_contract_id(self) -> str | None:
        manifest_path = Path(self.output_dir) / MANIFEST_FILENAME
        try:
            manifest = load_manifest(manifest_path)
        except (OSError, ValueError):
            return None
        contract_id = manifest.get("source_contract_id")
        return contract_id if isinstance(contract_id, str) else None

    def _restore_persisted_provenance(self) -> None:
        try:
            manifest = load_manifest(Path(self.output_dir) / MANIFEST_FILENAME)
        except (OSError, ValueError):
            return
        hashes = manifest.get("content_hashes")
        if isinstance(hashes, dict):
            self._content_hashes = {str(url): str(digest) for url, digest in hashes.items()}
        provenance = manifest.get("source_provenance")
        if isinstance(provenance, dict):
            for count_key, list_key in (
                ("months_synthetic", "synthetic_months"),
                ("months_downloaded", "downloaded_months"),
            ):
                months = provenance.get(list_key)
                if isinstance(months, list) and all(isinstance(value, str) for value in months):
                    self._stats[list_key] = months
                    self._stats[count_key] = len(months)

    def get_download_stats(self) -> dict[str, Any]:
        return {**self._stats, "source_provenance": self.source_provenance()}
