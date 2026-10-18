from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.nyctaxi.downloader import (
    PINNED_SOURCE_MONTHS,
    PINNED_SOURCE_YEAR,
    TLC_BASE_URL,
    NYCTaxiDataDownloader,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_pin_matches_expected_snapshot():
    assert PINNED_SOURCE_YEAR == 2019
    assert list(range(1, 13)) == PINNED_SOURCE_MONTHS


def test_defaults_resolve_to_the_pin(tmp_path):
    downloader = NYCTaxiDataDownloader(output_dir=tmp_path)
    assert downloader.year == PINNED_SOURCE_YEAR
    assert downloader.months == PINNED_SOURCE_MONTHS


def test_source_contract_lists_exact_remote_files(tmp_path):
    downloader = NYCTaxiDataDownloader(months=[1], output_dir=tmp_path)
    contract = downloader.source_contract()
    assert contract["source"] == "nyc-tlc"
    assert contract["base_url"] == TLC_BASE_URL
    assert contract["year"] == 2019
    assert contract["months"] == [1]
    assert contract["urls"] == [f"{TLC_BASE_URL}/yellow_tripdata_2019-01.parquet"]
    assert contract["taxi_zones_url"].endswith("taxi_zone_lookup.csv")


def test_full_default_contract_covers_twelve_pinned_files(tmp_path):
    downloader = NYCTaxiDataDownloader(output_dir=tmp_path)
    assert len(downloader.source_contract()["urls"]) == 12


def test_explicit_year_and_months_still_override(tmp_path):
    downloader = NYCTaxiDataDownloader(year=2020, months=[1, 2, 3], output_dir=tmp_path)
    assert downloader.year == 2020
    assert downloader.months == [1, 2, 3]
    assert downloader.source_contract()["year"] == 2020


def test_download_stats_record_the_source(tmp_path):
    downloader = NYCTaxiDataDownloader(months=[6, 7], output_dir=tmp_path)
    stats = downloader.get_download_stats()
    assert stats["source"] == "nyc-tlc"
    assert stats["year"] == 2019
    assert stats["months"] == [6, 7]
    assert stats["source_provenance"]["source"] == "unknown"
    assert stats["source_provenance"]["promotion_eligible"] is False


def test_source_provenance_distinguishes_synthetic_and_remote(tmp_path):
    downloader = NYCTaxiDataDownloader(months=[1, 2], output_dir=tmp_path)
    urls = downloader.source_contract()["urls"]
    downloader._content_hashes[urls[0]] = "a" * 64
    downloader._synthetic_fallback_months.append("2019-02")
    assert downloader.source_provenance()["source"] == "mixed"
    assert downloader.source_provenance()["promotion_eligible"] is False


def test_pinned_checksum_mismatch_is_not_synthetic_fallback(tmp_path, monkeypatch):
    import csv
    import io

    from benchbox.core.data_fetch.errors import ChecksumMismatchError
    from benchbox.core.nyctaxi import downloader as module

    downloader = NYCTaxiDataDownloader(months=[1], output_dir=tmp_path)
    url = downloader.source_contract()["urls"][0]
    monkeypatch.setitem(module.PINNED_SOURCE_SHA256, url, "0" * 64)
    monkeypatch.setattr(module.urllib.request, "urlretrieve", lambda _url, path: Path(path).write_bytes(b"drift"))

    with pytest.raises(ChecksumMismatchError):
        downloader._process_parquet_file(url, csv.writer(io.StringIO()), 0)
    assert downloader._synthetic_fallback_months == []


def test_checksum_abort_invalidates_output_and_sidecar(tmp_path, monkeypatch):
    from benchbox.core.data_fetch.errors import ChecksumMismatchError

    downloader = NYCTaxiDataDownloader(months=[1], output_dir=tmp_path, force_redownload=True)
    output_path = tmp_path / downloader.get_compressed_filename("trips.csv")
    output_path.write_text("old data", encoding="utf-8")
    downloader._write_contract_sidecar(output_path)
    monkeypatch.setattr(
        downloader,
        "_process_parquet_file",
        lambda *_args: (_ for _ in ()).throw(
            ChecksumMismatchError(path="source", expected_sha256="0" * 64, actual_sha256="1" * 64)
        ),
    )

    with pytest.raises(ChecksumMismatchError):
        downloader._download_and_process_trips()

    assert not output_path.exists()
    assert not downloader._contract_sidecar_path(output_path).exists()


def test_unparseable_download_is_not_recorded_as_ingested(tmp_path, monkeypatch):
    import csv
    import io

    import pyarrow.parquet as pq

    from benchbox.core.nyctaxi import downloader as module

    downloader = NYCTaxiDataDownloader(months=[1], output_dir=tmp_path)
    monkeypatch.setattr(downloader, "_generate_synthetic_month", lambda *args, **kwargs: 1)
    url = downloader.source_contract()["urls"][0]
    monkeypatch.setattr(module.urllib.request, "urlretrieve", lambda _url, path: Path(path).write_bytes(b"bad"))
    monkeypatch.setattr(pq, "read_table", lambda _path: (_ for _ in ()).throw(ValueError("invalid parquet")))

    downloader._process_parquet_file(url, csv.writer(io.StringIO()), 0)

    assert downloader._content_hashes == {}
    assert downloader.source_provenance()["source"] == "synthetic"
