# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import contextlib
import csv
import hashlib
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Union

import numpy as np
import yaml

from benchbox.core.data_fetch.errors import ChecksumMismatchError
from benchbox.core.nyctaxi.schema import (
    get_fhv_trips_columns,
    get_green_trips_columns,
    get_hvfhv_trips_columns,
    get_trips_columns,
)
from benchbox.utils.compression_mixin import CompressionMixin
from benchbox.utils.tls import download_to_file
from benchbox.utils.verbosity import VerbosityMixin, compute_verbosity


def _load_downloader_specs() -> dict[str, Any]:
    with (Path(__file__).with_name("downloader_specs.yaml")).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


_DOWNLOADER_SPECS = _load_downloader_specs()

TLC_BASE_URL = _DOWNLOADER_SPECS["tlc_base_url"]
SCALE_FACTOR_SAMPLE_DIVISOR = float(_DOWNLOADER_SPECS["scale_factor_sample_divisor"])

_PINNED_SOURCE = _DOWNLOADER_SPECS.get("pinned_source") or {}
PINNED_SOURCE_YEAR = int(_PINNED_SOURCE.get("year", 2019))
PINNED_SOURCE_MONTHS = [int(month) for month in _PINNED_SOURCE.get("months", list(range(1, 13)))]
PINNED_SOURCE_SHA256 = {str(url): str(digest) for url, digest in (_PINNED_SOURCE.get("sha256") or {}).items()}

TAXI_ZONES_DATA = [tuple(row) for row in _DOWNLOADER_SPECS["taxi_zones"]]


class _TripDataDownloader(CompressionMixin, VerbosityMixin):
    _COLUMN_ALIASES: dict[str, tuple[str, ...]] = {}
    _COLUMN_DEFAULTS: dict[str, Any] = {}
    _STATS_TAXI_TYPE: str | None = None

    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Union[str, Path] | None = None,
        year: int | None = None,
        months: list[int] | None = None,
        seed: int | None = None,
        verbose: int | bool = 0,
        quiet: bool = False,
        force_redownload: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)

        self.scale_factor = scale_factor
        self.output_dir = Path(output_dir) if output_dir else Path.cwd() / "nyctaxi_data"
        self.year = PINNED_SOURCE_YEAR if year is None else year
        self.months = months or self._default_months(self.year)
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        self.force_redownload = force_redownload

        self.apply_verbosity(compute_verbosity(verbose, quiet))
        self.logger = logging.getLogger(self._LOGGER_NAME)

        self.sample_rate = min(1.0, scale_factor / SCALE_FACTOR_SAMPLE_DIVISOR)
        if self.sample_rate >= 1.0 and scale_factor >= SCALE_FACTOR_SAMPLE_DIVISOR:
            self.logger.warning(
                "NYC Taxi (%s): sample_rate saturated at 1.0 (SF=%.1f). "
                "Full dataset is in use; no additional scaling beyond SF=%.0f.",
                self._TAXI_LABEL,
                scale_factor,
                SCALE_FACTOR_SAMPLE_DIVISOR,
            )
        self._table_row_counts: dict[str, int] = {}
        self._synthetic_fallback_months: list[str] = []
        self._content_hashes: dict[str, str] = {}

    def _default_months(self, year: int) -> list[int]:
        return list(PINNED_SOURCE_MONTHS)

    def source_contract(self) -> dict[str, Any]:
        urls = [f"{TLC_BASE_URL}/{self._URL_PREFIX}_{self.year}-{month:02d}.parquet" for month in self.months]
        return {
            "source": "nyc-tlc",
            "base_url": TLC_BASE_URL,
            "year": self.year,
            "months": list(self.months),
            "urls": urls,
            "expected_sha256": {url: PINNED_SOURCE_SHA256[url] for url in urls if url in PINNED_SOURCE_SHA256},
            "taxi_zones_url": _DOWNLOADER_SPECS["taxi_zones_url"],
        }

    def source_contract_id(self) -> str:
        canonical = json.dumps(self.source_contract(), sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _contract_sidecar_path(self, output_path: Path) -> Path:
        return output_path.with_name(f"_{output_path.stem}_source_contract.json")

    def _read_persisted_contract_id(self, output_path: Path) -> str | None:
        try:
            persisted = json.loads(self._contract_sidecar_path(output_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        contract_id = persisted.get("source_contract_id")
        return contract_id if isinstance(contract_id, str) else None

    def _restore_persisted_provenance(self, output_path: Path) -> None:
        try:
            persisted = json.loads(self._contract_sidecar_path(output_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        hashes = persisted.get("content_hashes")
        if isinstance(hashes, dict):
            self._content_hashes = {str(url): str(digest) for url, digest in hashes.items()}
        synthetic = persisted.get("synthetic_months")
        if isinstance(synthetic, list):
            self._synthetic_fallback_months = [str(month) for month in synthetic]

    def _write_contract_sidecar(self, output_path: Path) -> None:
        sidecar = {
            "source_contract": self.source_contract(),
            "source_contract_id": self.source_contract_id(),
            "content_hashes": dict(self._content_hashes),
            "synthetic_months": list(self._synthetic_fallback_months),
            "source_provenance": self.source_provenance(),
        }
        self._contract_sidecar_path(output_path).write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")

    def _record_synthetic_fallback(self, url: str) -> None:
        match = re.search(r"(\d{4})-(\d{2})", url)
        label = f"{match.group(1)}-{match.group(2)}" if match else url
        if label not in self._synthetic_fallback_months:
            self._synthetic_fallback_months.append(label)

    def download(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self._download_and_process_trips()

    def _download_and_process_trips(self) -> Path:
        output_path = self.output_dir / self.get_compressed_filename(self._OUTPUT_FILENAME)

        if output_path.exists() and not self.force_redownload:
            persisted_id = self._read_persisted_contract_id(output_path)
            if persisted_id is None:
                self.logger.warning("Existing %s has no source-contract evidence; regenerating.", output_path.name)
            elif persisted_id != self.source_contract_id():
                self.logger.warning(
                    "Existing %s was generated under a different source contract "
                    "(%s...); regenerating for the current pin (%s...).",
                    output_path.name,
                    persisted_id[:12],
                    self.source_contract_id()[:12],
                )
            else:
                self._restore_persisted_provenance(output_path)
                self.log_verbose(self._SKIP_EXISTING_MESSAGE)
                return output_path

        self.log_verbose(f"Downloading {self._DOWNLOAD_LOG_LABEL} data for {self.year}")
        self.log_verbose(f"  Months: {self.months}")
        self.log_verbose(f"  Sample rate: {self.sample_rate:.4f}")

        output_columns = type(self)._COLUMN_PROVIDER()
        trip_id = 0
        total_rows = 0

        try:
            with self.open_output_file(output_path, "wt") as outf:
                writer = csv.writer(outf)
                writer.writerow(["trip_id"] + output_columns)

                for month in self.months:
                    url = f"{TLC_BASE_URL}/{self._URL_PREFIX}_{self.year}-{month:02d}.parquet"
                    self.log_verbose(f"  Processing {self.year}-{month:02d}...")

                    try:
                        month_rows = self._process_parquet_file(url, writer, trip_id)
                        trip_id += month_rows
                        total_rows += month_rows
                    except ChecksumMismatchError:
                        raise
                    except Exception as e:
                        self.logger.warning(f"Failed to process {url}: {e}")
                        continue
        except ChecksumMismatchError:
            with contextlib.suppress(OSError):
                output_path.unlink()
            with contextlib.suppress(OSError):
                self._contract_sidecar_path(output_path).unlink()
            raise

        self._table_row_counts[self._TABLE_NAME] = total_rows
        self.log_verbose(f"  {self._TABLE_NAME}: {total_rows} rows total")
        self._write_contract_sidecar(output_path)
        return output_path

    def _process_parquet_file(self, url: str, writer: csv.writer, start_trip_id: int) -> int:
        try:
            import pyarrow.parquet as pq
        except ImportError:
            self.logger.warning("pyarrow not installed, using synthetic data")
            self._record_synthetic_fallback(url)
            return self._generate_synthetic_month(writer, start_trip_id)

        fd, tmp_name = tempfile.mkstemp(suffix=".parquet")
        os.close(fd)
        try:
            download_to_file(url, tmp_name, timeout=120)
            with open(tmp_name, "rb") as handle:
                actual_sha256 = hashlib.sha256(handle.read()).hexdigest()
            expected_sha256 = PINNED_SOURCE_SHA256.get(url)
            if expected_sha256 is not None and actual_sha256 != expected_sha256:
                raise ChecksumMismatchError(path=url, expected_sha256=expected_sha256, actual_sha256=actual_sha256)
            table = pq.read_table(tmp_name)
            df = table.to_pandas()
        except ChecksumMismatchError:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
        except Exception as e:
            self.logger.warning(f"Download failed: {e}, using synthetic data")
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            self._record_synthetic_fallback(url)
            return self._generate_synthetic_month(writer, start_trip_id)
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        self._content_hashes[url] = actual_sha256

        if self.sample_rate < 1.0:
            sample_size = max(1, int(len(df) * self.sample_rate))
            df = df.sample(n=sample_size, random_state=self.seed)

        writer.writerows(self._map_frame_to_rows(df, start_trip_id))
        return len(df)

    @staticmethod
    def _clean_csv_value(value: Any, default: Any) -> Any:
        if value is None:
            return default
        if isinstance(value, float):
            if value != value:
                return default
            if value.is_integer():
                return int(value)
            return value
        value_type = type(value).__name__
        if value_type == "NaTType":
            return default
        if value_type == "Timestamp" or isinstance(value, datetime):
            try:
                return value.strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, AttributeError):
                return default
        if isinstance(value, (np.integer,)):
            return int(value)
        if isinstance(value, (np.floating,)):
            rounded = float(value)
            if rounded != rounded:
                return default
            return int(rounded) if rounded.is_integer() else rounded
        if isinstance(value, (np.bool_,)):
            return int(value)
        return value

    @staticmethod
    def _normalize_sr_flag(value: Any) -> str:
        if value is None:
            return "N"
        if isinstance(value, float) and value != value:
            return "N"
        text = str(value).strip().lower()
        return "Y" if text in ("1", "1.0", "y", "yes", "true") else "N"

    def _source_series(self, df: Any, column: str) -> Any | None:
        present = [name for name in self._COLUMN_ALIASES.get(column, (column,)) if name in df.columns]
        if not present:
            return None
        series = df[present[0]]
        for name in present[1:]:
            if series.dtype == object:
                missing = np.fromiter((value is None for value in series), dtype=bool, count=len(series))
                if missing.any():
                    series = series.where(~missing, df[name])
        return series

    def _clean_series(self, series: Any, default: Any) -> list:
        dtype = series.dtype
        kind = dtype.kind
        if kind == "M":
            text = series.dt.strftime("%Y-%m-%d %H:%M:%S")
            return text.where(series.notna(), default).tolist()
        if kind == "f":
            values = series.to_numpy(dtype="float64", na_value=np.nan)
            missing = np.isnan(values)
            integral = np.isfinite(values) & (values == np.floor(values))
            cleaned = values.astype(object)
            cleaned[integral] = values[integral].astype(np.int64).astype(object)
            cleaned[missing] = default
            return cleaned.tolist()
        if isinstance(dtype, np.dtype) and kind in "iu":
            return series.tolist()
        if isinstance(dtype, np.dtype) and kind == "b":
            return series.astype(np.int64).tolist()
        return [self._clean_csv_value(value, default) for value in series.astype(object).tolist()]

    def _map_frame_to_rows(self, df: Any, start_trip_id: int) -> Any:
        count = len(df)
        columns: list[Any] = [range(start_trip_id, start_trip_id + count)]
        for column in type(self)._COLUMN_PROVIDER():
            default = self._COLUMN_DEFAULTS.get(column, 0)
            series = self._source_series(df, column)
            if series is None:
                values = [self._clean_csv_value(default, default)] * count
            else:
                values = self._clean_series(series, default)
            if column == "sr_flag":
                values = [self._normalize_sr_flag(value) for value in values]
            columns.append(values)
        return zip(*columns)

    def _generate_synthetic_month(self, writer: csv.writer, start_trip_id: int) -> int:
        num_trips = max(self._SYNTHETIC_MIN_TRIPS, int(self._SYNTHETIC_MONTHLY_TRIPS * self.sample_rate * 100))
        for i in range(num_trips):
            trip_id = start_trip_id + i
            hour = int(self.rng.choice(self._SYNTHETIC_HOURS))
            minute = int(self.rng.integers(0, 60))
            day = int(self.rng.integers(1, 29))
            month = int(self.rng.choice(self.months))
            pickup_time = datetime(self.year, month, day, hour, minute)
            duration_min = int(self._SYNTHETIC_DURATION_OFFSET + self.rng.exponential(self._SYNTHETIC_DURATION_SCALE))
            dropoff_time = pickup_time + timedelta(minutes=duration_min)
            distance = max(0.1, self.rng.exponential(self._SYNTHETIC_DISTANCE_SCALE))
            fare = 2.50 + distance * 2.50 + duration_min * 0.35
            self._write_metered_synthetic_row(writer, trip_id, pickup_time, dropoff_time, distance, fare)
        return num_trips

    def _write_metered_synthetic_row(
        self,
        writer: csv.writer,
        trip_id: int,
        pickup_time: datetime,
        dropoff_time: datetime,
        distance: float,
        fare: float,
    ) -> None:
        is_yellow = self._TAXI_LABEL == "Yellow"
        zones = self._POPULAR_ZONES if is_yellow else self._OUTER_BOROUGH_ZONES
        values = {
            "vendor_id": int(self.rng.choice([1, 2])),
            "pickup_datetime": pickup_time.strftime("%Y-%m-%d %H:%M:%S"),
            "dropoff_datetime": dropoff_time.strftime("%Y-%m-%d %H:%M:%S"),
            "trip_distance": f"{distance:.2f}",
            "fare_amount": f"{fare:.2f}",
            "mta_tax": "0.50",
            "improvement_surcharge": "0.30",
        }
        if is_yellow:
            values["passenger_count"] = int(self.rng.choice([1, 1, 1, 2, 2, 3]))
        values["pickup_location_id"] = int(self.rng.choice(zones))
        values["dropoff_location_id"] = int(self.rng.choice(zones))
        if not is_yellow:
            values["passenger_count"] = int(self.rng.choice([1, 1, 1, 2, 2]))
        values["rate_code_id"] = 1
        values["store_and_fwd_flag"] = "N"
        if is_yellow:
            values["payment_type"] = int(self.rng.choice([1, 1, 1, 2]))
        values["extra"] = f"{self.rng.random() * (2 if is_yellow else 1):.2f}"
        values["tip_amount"] = f"{fare * 0.15:.2f}" if self.rng.random() > (0.3 if is_yellow else 0.35) else "0.00"
        values["tolls_amount"] = (
            f"{self.rng.random() * (5 if is_yellow else 4):.2f}" if self.rng.random() > 0.9 else "0.00"
        )
        values["total_amount"] = f"{fare * (1.2 if is_yellow else 1.18):.2f}"
        if not is_yellow:
            values["ehail_fee"] = "0.00"
            values["payment_type"] = int(self.rng.choice([1, 1, 1, 2]))
            values["trip_type"] = int(self.rng.choice([1, 1, 2]))
        values["congestion_surcharge"] = "2.50" if self.rng.random() > 0.5 else "0.00"
        writer.writerow([trip_id, *[values.get(column, "0.00") for column in type(self)._COLUMN_PROVIDER()]])

    def get_download_stats(self) -> dict:
        synthetic = list(self._synthetic_fallback_months)
        if synthetic and len(synthetic) >= len(self.months):
            source = "synthetic"
        elif synthetic:
            source = "nyc-tlc-synthetic-mixed"
        else:
            source = "nyc-tlc"
        stats = {
            "source": source,
            "scale_factor": self.scale_factor,
            "sample_rate": self.sample_rate,
            "year": self.year,
            "months": self.months,
            "seed": self.seed,
            "row_counts": dict(self._table_row_counts),
            "synthetic_months": synthetic,
            "source_provenance": self.source_provenance(),
        }
        return {"taxi_type": self._STATS_TAXI_TYPE, **stats} if self._STATS_TAXI_TYPE is not None else stats

    def source_provenance(self) -> dict[str, Any]:
        urls = set(self.source_contract()["urls"])
        expected = {url: PINNED_SOURCE_SHA256[url] for url in urls if url in PINNED_SOURCE_SHA256}
        observed = {url: self._content_hashes[url] for url in urls if url in self._content_hashes}
        synthetic = set(self._synthetic_fallback_months)
        if synthetic and observed:
            source = "mixed"
        elif synthetic:
            source = "synthetic"
        elif observed:
            source = "remote"
        else:
            source = "unknown"
        eligible = bool(urls) and not synthetic and set(expected) == urls == set(observed) and expected == observed
        return {
            "source": source,
            "expected_sha256": expected,
            "observed_sha256": observed,
            "promotion_eligible": eligible,
        }


class NYCTaxiDataDownloader(_TripDataDownloader):
    _TABLE_NAME = "trips"
    _OUTPUT_FILENAME = "trips.csv"
    _URL_PREFIX = "yellow_tripdata"
    _LOGGER_NAME = "benchbox.core.nyctaxi.downloader"
    _TAXI_LABEL = "Yellow"
    _DOWNLOAD_LOG_LABEL = "NYC Taxi"
    _SKIP_EXISTING_MESSAGE = "Trips data already exists, skipping"
    _COLUMN_PROVIDER = get_trips_columns
    _COLUMN_ALIASES = {
        "vendor_id": ("VendorID", "vendorid"),
        "pickup_datetime": ("tpep_pickup_datetime", "pickup_datetime"),
        "dropoff_datetime": ("tpep_dropoff_datetime", "dropoff_datetime"),
        "pickup_location_id": ("PULocationID", "pulocationid"),
        "dropoff_location_id": ("DOLocationID", "dolocationid"),
        "rate_code_id": ("RatecodeID", "ratecodeid"),
        "airport_fee": ("airport_fee", "Airport_fee"),
    }
    _COLUMN_DEFAULTS = {
        "vendor_id": 1,
        "pickup_datetime": "",
        "dropoff_datetime": "",
        "passenger_count": 1,
        "rate_code_id": 1,
        "store_and_fwd_flag": "N",
        "payment_type": 1,
    }
    _SYNTHETIC_MONTHLY_TRIPS = 10000
    _SYNTHETIC_MIN_TRIPS = 100
    _SYNTHETIC_HOURS = (8, 9, 17, 18, 12, 13, 14, 15, 16, 19, 20, 21, 22)
    _SYNTHETIC_DURATION_OFFSET = 10
    _SYNTHETIC_DURATION_SCALE = 15.0
    _SYNTHETIC_DISTANCE_SCALE = 3.0
    _POPULAR_ZONES = (132, 138, 161, 162, 163, 164, 186, 230, 234, 236, 237, 239)

    def download(self) -> dict[str, Path]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return {"taxi_zones": self._generate_taxi_zones(), "trips": self._download_and_process_trips()}

    def _generate_taxi_zones(self) -> Path:
        output_path = self.output_dir / self.get_compressed_filename("taxi_zones.csv")

        if output_path.exists() and not self.force_redownload:
            self.log_verbose("Taxi zones already exist, skipping")
            return output_path

        self.log_verbose("Generating taxi zones table")

        with self.open_output_file(output_path, "wt") as f:
            writer = csv.writer(f)
            writer.writerow(["location_id", "borough", "zone", "service_zone"])
            writer.writerows(TAXI_ZONES_DATA)

        self._table_row_counts["taxi_zones"] = len(TAXI_ZONES_DATA)
        self.log_verbose(f"  taxi_zones: {len(TAXI_ZONES_DATA)} rows")
        return output_path


class GreenTaxiDataDownloader(_TripDataDownloader):
    _TABLE_NAME = "green_trips"
    _OUTPUT_FILENAME = "green_trips.csv"
    _URL_PREFIX = "green_tripdata"
    _LOGGER_NAME = "benchbox.core.nyctaxi.downloader.green"
    _TAXI_LABEL = "Green"
    _DOWNLOAD_LOG_LABEL = "Green Taxi"
    _SKIP_EXISTING_MESSAGE = "Green trips data already exists, skipping"
    _COLUMN_PROVIDER = get_green_trips_columns
    _STATS_TAXI_TYPE = "green"
    _COLUMN_ALIASES = {
        "vendor_id": ("VendorID", "vendorid"),
        "pickup_datetime": ("lpep_pickup_datetime", "pickup_datetime"),
        "dropoff_datetime": ("lpep_dropoff_datetime", "dropoff_datetime"),
        "rate_code_id": ("RatecodeID", "ratecodeid"),
        "pickup_location_id": ("PULocationID", "pulocationid"),
        "dropoff_location_id": ("DOLocationID", "dolocationid"),
    }
    _COLUMN_DEFAULTS = {
        "vendor_id": 1,
        "pickup_datetime": "",
        "dropoff_datetime": "",
        "store_and_fwd_flag": "N",
        "rate_code_id": 1,
        "passenger_count": 1,
        "payment_type": 1,
        "trip_type": 1,
    }
    _SYNTHETIC_MONTHLY_TRIPS = 2000
    _SYNTHETIC_MIN_TRIPS = 50
    _SYNTHETIC_HOURS = (7, 8, 9, 17, 18, 19, 12, 13, 14, 15, 16)
    _SYNTHETIC_DURATION_OFFSET = 8
    _SYNTHETIC_DURATION_SCALE = 12.0
    _SYNTHETIC_DISTANCE_SCALE = 2.5
    _OUTER_BOROUGH_ZONES = (
        tuple(range(7, 9))
        + (17, 21, 22, 25, 26, 33)
        + tuple(range(35, 38))
        + (39, 40, 49, 61, 62, 65, 69, 74, 76, 85, 103, 109, 128, 129, 133, 143, 181, 189, 197)
    )


class HVFHVDataDownloader(_TripDataDownloader):
    HVFHV_START_YEAR, HVFHV_START_MONTH = 2019, 2
    HVFHV_LICENSE_NUMS = {"HV0002": "Juno", "HV0003": "Uber", "HV0004": "Via", "HV0005": "Lyft"}

    _TABLE_NAME = "hvfhv_trips"
    _OUTPUT_FILENAME = "hvfhv_trips.csv"
    _URL_PREFIX = "fhvhv_tripdata"
    _LOGGER_NAME = "benchbox.core.nyctaxi.downloader.hvfhv"
    _TAXI_LABEL = "HVFHV"
    _DOWNLOAD_LOG_LABEL = "HVFHV"
    _SKIP_EXISTING_MESSAGE = "HVFHV trips data already exists, skipping"
    _COLUMN_PROVIDER = get_hvfhv_trips_columns
    _STATS_TAXI_TYPE = "hvfhv"
    _COLUMN_ALIASES = {
        "pickup_location_id": ("PULocationID", "pulocationid"),
        "dropoff_location_id": ("DOLocationID", "dolocationid"),
    }
    _COLUMN_DEFAULTS = {
        "hvfhs_license_num": "HV0003",
        "dispatching_base_num": "",
        "originating_base_num": "",
        "request_datetime": "",
        "on_scene_datetime": "",
        "pickup_datetime": "",
        "dropoff_datetime": "",
        "shared_request_flag": "N",
        "shared_match_flag": "N",
        "access_a_ride_flag": "N",
        "wav_request_flag": "N",
        "wav_match_flag": "N",
    }

    def _default_months(self, year: int) -> list[int]:
        return list(range(self.HVFHV_START_MONTH if year == self.HVFHV_START_YEAR else 1, 13))

    def _generate_synthetic_month(self, writer: csv.writer, start_trip_id: int) -> int:
        num_trips = max(100, int(25000 * self.sample_rate * 100))

        all_zones = list(range(1, 263))
        license_weights = ["HV0003"] * 55 + ["HV0005"] * 30 + ["HV0002"] * 10 + ["HV0004"] * 5

        for i in range(num_trips):
            trip_id = start_trip_id + i

            hour = int(self.rng.choice([8, 9, 10, 17, 18, 19, 20, 21, 22, 23, 0, 1]))
            minute = int(self.rng.integers(0, 60))
            day = int(self.rng.integers(1, 29))

            month = int(self.rng.choice(self.months))
            pickup_time = datetime(self.year, month, day, hour, minute)
            wait_min = int(2 + self.rng.exponential(5))
            duration_min = int(8 + self.rng.exponential(18))

            request_time = pickup_time - timedelta(minutes=wait_min)
            on_scene_time = pickup_time
            dropoff_time = pickup_time + timedelta(minutes=duration_min)

            distance = max(0.1, self.rng.exponential(4.5))
            base_fare = 2.50 + distance * 1.80 + duration_min * 0.30
            if self.rng.random() > 0.85:
                base_fare *= float(1.5 + self.rng.random())

            license_num = str(self.rng.choice(license_weights))
            base_num = f"B{int(self.rng.integers(100000, 999999))}"
            is_shared = self.rng.random() > 0.75

            writer.writerow(
                [
                    trip_id,
                    license_num,
                    base_num,
                    base_num,
                    request_time.strftime("%Y-%m-%d %H:%M:%S"),
                    on_scene_time.strftime("%Y-%m-%d %H:%M:%S"),
                    pickup_time.strftime("%Y-%m-%d %H:%M:%S"),
                    dropoff_time.strftime("%Y-%m-%d %H:%M:%S"),
                    int(self.rng.choice(all_zones)),
                    int(self.rng.choice(all_zones)),
                    f"{distance:.2f}",
                    int(duration_min * 60),
                    f"{base_fare:.2f}",
                    f"{self.rng.random() * 4:.2f}" if self.rng.random() > 0.9 else "0.00",
                    f"{base_fare * 0.025:.2f}",
                    f"{base_fare * 0.089:.2f}",
                    "2.75" if self.rng.random() > 0.5 else "0.00",
                    "0.00",
                    f"{base_fare * 0.20:.2f}" if self.rng.random() > 0.55 else "0.00",
                    f"{base_fare * 0.60:.2f}",
                    "Y" if is_shared else "N",
                    "Y" if (is_shared and self.rng.random() > 0.5) else "N",
                    "N",
                    "Y" if self.rng.random() > 0.95 else "N",
                    "Y" if self.rng.random() > 0.97 else "N",
                ]
            )

        return num_trips


class FHVDataDownloader(_TripDataDownloader):
    _TABLE_NAME = "fhv_trips"
    _OUTPUT_FILENAME = "fhv_trips.csv"
    _URL_PREFIX = "fhv_tripdata"
    _LOGGER_NAME = "benchbox.core.nyctaxi.downloader.fhv"
    _TAXI_LABEL = "FHV"
    _DOWNLOAD_LOG_LABEL = "FHV"
    _SKIP_EXISTING_MESSAGE = "FHV trips data already exists, skipping"
    _COLUMN_PROVIDER = get_fhv_trips_columns
    _STATS_TAXI_TYPE = "fhv"
    _COLUMN_ALIASES = {
        "dispatching_base_num": ("dispatching_base_num", "dispatching_base_number", "Dispatching_base_num"),
        "affiliated_base_number": ("Affiliated_base_number", "affiliated_base_num"),
        "pickup_datetime": ("pickup_datetime", "Pickup_datetime"),
        "dropoff_datetime": ("dropOff_datetime", "dropoff_datetime", "Dropoff_datetime"),
        "pickup_location_id": ("PUlocationID", "PULocationID", "pulocationid"),
        "dropoff_location_id": ("DOlocationID", "DOLocationID", "dolocationid"),
        "sr_flag": ("SR_Flag", "sr_flag"),
    }
    _COLUMN_DEFAULTS = {
        "dispatching_base_num": "",
        "affiliated_base_number": "",
        "pickup_datetime": "",
        "dropoff_datetime": "",
        "sr_flag": "N",
    }
    _SYNTHETIC_MONTHLY_TRIPS = 1000
    _SYNTHETIC_MIN_TRIPS = 25
    _SYNTHETIC_HOURS = (8, 9, 10, 17, 18, 19, 20, 21, 22)
    _SYNTHETIC_DURATION_OFFSET = 12
    _SYNTHETIC_DURATION_SCALE = 18.0
    _SYNTHETIC_DISTANCE_SCALE = 4.0

    def _generate_synthetic_month(self, writer: csv.writer, start_trip_id: int) -> int:
        num_trips = max(self._SYNTHETIC_MIN_TRIPS, int(self._SYNTHETIC_MONTHLY_TRIPS * self.sample_rate * 100))
        all_zones = list(range(1, 263))
        for i in range(num_trips):
            trip_id = start_trip_id + i
            hour = int(self.rng.choice(self._SYNTHETIC_HOURS))
            minute = int(self.rng.integers(0, 60))
            day = int(self.rng.integers(1, 29))
            month = int(self.rng.choice(self.months))
            pickup_time = datetime(self.year, month, day, hour, minute)
            duration_min = int(self._SYNTHETIC_DURATION_OFFSET + self.rng.exponential(self._SYNTHETIC_DURATION_SCALE))
            dropoff_time = pickup_time + timedelta(minutes=duration_min)
            base_num = f"B{int(self.rng.integers(100000, 999999))}"
            writer.writerow(
                [
                    trip_id,
                    base_num,
                    base_num,
                    pickup_time.strftime("%Y-%m-%d %H:%M:%S"),
                    dropoff_time.strftime("%Y-%m-%d %H:%M:%S"),
                    int(self.rng.choice(all_zones)),
                    int(self.rng.choice(all_zones)),
                    "Y" if self.rng.random() > 0.95 else "N",
                ]
            )
        return num_trips
