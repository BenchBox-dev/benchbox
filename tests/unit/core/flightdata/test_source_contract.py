from __future__ import annotations

import pytest

from benchbox.core.flightdata.downloader import (
    BTS_BASE_URL,
    PINNED_END_MONTH,
    PINNED_END_YEAR,
    FlightDataDownloader,
    _months_sequence,
    _scale_to_months,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture(autouse=True)
def _fast_synthetic_month(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FlightDataDownloader, "_generate_synthetic_month", lambda *args, **kwargs: 1)


def test_pin_matches_expected_snapshot():
    assert (PINNED_END_YEAR, PINNED_END_MONTH) == (2024, 12)


def test_window_ends_at_the_pin_not_latest_available():
    assert _months_sequence(1) == [(2024, 12)]
    months = _months_sequence(13)
    assert months[0] == (2024, 12)
    assert months[-1] == (2023, 12)


def test_window_skips_unavailable_bts_archives():
    months = _months_sequence(410)
    assert len(months) == 327
    assert months[0] == (2024, 12)
    assert months[-1] == (1987, 10)
    assert not any((1990, 1) <= (year, month) <= (1999, 12) for year, month in months)
    assert not any((1987, 1) <= (year, month) <= (1987, 9) for year, month in months)


def test_downloader_reports_effective_month_count(tmp_path):
    downloader = FlightDataDownloader(scale_factor=10.0, output_dir=tmp_path)
    assert downloader.num_months == len(downloader.months) == 327


def test_explicit_end_still_overridable():
    assert _months_sequence(1, end_year=2020, end_month=6) == [(2020, 6)]


def test_scale_to_months_mapping_is_stable():
    assert _scale_to_months(0.01) == 1
    assert _scale_to_months(1.0) == 41


def test_downloader_window_is_pinned(tmp_path):
    downloader = FlightDataDownloader(scale_factor=0.01, output_dir=tmp_path)
    assert downloader._months == [(2024, 12)]
    assert downloader.num_months == 1


def test_source_contract_lists_exact_remote_files(tmp_path):
    downloader = FlightDataDownloader(scale_factor=0.01, output_dir=tmp_path)
    contract = downloader.source_contract()
    assert contract["source"] == "bts-transtats"
    assert contract["base_url"] == BTS_BASE_URL
    assert contract["csv_encoding"] == "cp1252"
    assert contract["allow_synthetic_fallback"] is False
    assert contract["months"] == [(2024, 12)]
    assert contract["urls"] == [BTS_BASE_URL.format(year=2024, month=12)]


def test_download_stats_record_the_source_window(tmp_path):
    downloader = FlightDataDownloader(scale_factor=0.01, output_dir=tmp_path)
    stats = downloader.get_download_stats()
    assert stats["source"] == "bts-transtats"
    assert stats["months"] == [(2024, 12)]
    assert stats["source_provenance"]["source"] == "unknown"
    assert stats["source_provenance"]["promotion_eligible"] is False


def test_small_scale_records_synthetic_provenance(tmp_path):
    import csv
    import io

    downloader = FlightDataDownloader(scale_factor=0.01, output_dir=tmp_path)
    downloader._process_month(csv.writer(io.StringIO()), 2024, 12, 0)
    assert downloader.source_provenance()["source"] == "synthetic"
    assert downloader.source_provenance()["synthetic_months"] == ["2024-12"]
    assert downloader.source_provenance()["promotion_eligible"] is False


def test_pinned_checksum_mismatch_fails_instead_of_falling_back(tmp_path, monkeypatch):
    import csv
    import io

    from benchbox.core.data_fetch.errors import ChecksumMismatchError
    from benchbox.core.flightdata import downloader as module

    url = BTS_BASE_URL.format(year=2024, month=12)
    monkeypatch.setitem(module.PINNED_SOURCE_SHA256, url, "0" * 64)

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"provider drift"

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())
    downloader = FlightDataDownloader(scale_factor=0.1, output_dir=tmp_path)
    with pytest.raises(ChecksumMismatchError):
        downloader._process_month(csv.writer(io.StringIO()), 2024, 12, 0)
    assert downloader._stats["months_synthetic"] == 0


def test_checksum_abort_invalidates_outputs_and_manifest(tmp_path, monkeypatch):
    from benchbox.core.data_fetch.errors import ChecksumMismatchError
    from benchbox.utils.datagen_manifest import MANIFEST_FILENAME

    downloader = FlightDataDownloader(scale_factor=0.1, output_dir=tmp_path, force_redownload=True)
    flights_path = tmp_path / downloader.get_compressed_filename("flights.csv")
    flights_path.write_text("old data", encoding="utf-8")
    manifest_path = tmp_path / MANIFEST_FILENAME
    manifest_path.write_text('{"source_contract_id": "stale"}', encoding="utf-8")
    monkeypatch.setattr(
        downloader,
        "_ensure_flights_data",
        lambda _path: (_ for _ in ()).throw(
            ChecksumMismatchError(path="source", expected_sha256="0" * 64, actual_sha256="1" * 64)
        ),
    )

    with pytest.raises(ChecksumMismatchError):
        downloader.download()

    assert not flights_path.exists()
    assert not manifest_path.exists()


def test_real_only_download_failure_invalidates_partial_output(tmp_path, monkeypatch):
    from benchbox.utils.datagen_manifest import MANIFEST_FILENAME

    downloader = FlightDataDownloader(scale_factor=0.1, output_dir=tmp_path, force_redownload=True)
    flights_path = tmp_path / downloader.get_compressed_filename("flights.csv")
    manifest_path = tmp_path / MANIFEST_FILENAME
    manifest_path.write_text("stale", encoding="utf-8")

    def fail_with_partial_output(path):
        path.write_text("partial", encoding="utf-8")
        raise RuntimeError("BTS download failed for 2024-12; real data is required")

    monkeypatch.setattr(downloader, "_ensure_flights_data", fail_with_partial_output)
    with pytest.raises(RuntimeError, match="real data is required"):
        downloader.download()
    assert not flights_path.exists()
    assert not manifest_path.exists()


def test_unparseable_download_fails_closed_unless_fallback_is_explicit(tmp_path, monkeypatch):
    import csv
    import io

    from benchbox.core.flightdata import downloader as module

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"not a zip"

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())
    downloader = FlightDataDownloader(scale_factor=0.1, output_dir=tmp_path)
    with pytest.raises(RuntimeError, match="2024-12.*real data is required"):
        downloader._process_month(csv.writer(io.StringIO()), 2024, 12, 0)

    assert downloader._content_hashes == {}
    assert downloader.source_provenance()["source"] == "unknown"
    assert downloader.source_provenance()["synthetic_months"] == []

    fallback = FlightDataDownloader(scale_factor=0.1, output_dir=tmp_path, allow_synthetic_fallback=True)
    fallback._process_month(csv.writer(io.StringIO()), 2024, 12, 0)
    assert fallback.source_provenance()["source"] == "synthetic"
    assert fallback.source_provenance()["synthetic_months"] == ["2024-12"]
    assert fallback.source_provenance()["months_synthetic"] == 1


def test_valid_zip_without_csv_routes_through_fallback_cleanup(tmp_path, monkeypatch):

    import csv
    import io
    import zipfile

    from benchbox.core.flightdata import downloader as module

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "no csv here")
    payload = buf.getvalue()

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return payload

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())
    downloader = FlightDataDownloader(scale_factor=0.1, output_dir=tmp_path)
    with pytest.raises(RuntimeError, match="2024-12.*real data is required"):
        downloader._process_month(csv.writer(io.StringIO()), 2024, 12, 0)

    fallback = FlightDataDownloader(scale_factor=0.1, output_dir=tmp_path, allow_synthetic_fallback=True)
    fallback._process_month(csv.writer(io.StringIO()), 2024, 12, 0)
    assert fallback.source_provenance()["source"] == "synthetic"
    assert fallback.source_provenance()["synthetic_months"] == ["2024-12"]


def test_contract_id_stable_and_pin_sensitive(tmp_path):
    first = FlightDataDownloader(scale_factor=0.01, output_dir=tmp_path)
    second = FlightDataDownloader(scale_factor=0.01, output_dir=tmp_path)
    assert first.source_contract_id() == second.source_contract_id()
    other = FlightDataDownloader(scale_factor=1.0, output_dir=tmp_path)
    assert first.source_contract_id() != other.source_contract_id()
    fallback = FlightDataDownloader(scale_factor=0.01, output_dir=tmp_path, allow_synthetic_fallback=True)
    assert first.source_contract_id() != fallback.source_contract_id()


def test_missing_manifest_has_no_persisted_contract(tmp_path):
    downloader = FlightDataDownloader(scale_factor=0.01, output_dir=tmp_path)
    assert downloader._persisted_source_contract_id() is None


def test_manifest_persists_contract_and_hashes(tmp_path):
    from benchbox.utils.datagen_manifest import MANIFEST_FILENAME, load_manifest

    downloader = FlightDataDownloader(scale_factor=0.01, output_dir=tmp_path)
    flights = tmp_path / "flights.csv"
    flights.write_text("x", encoding="utf-8")
    downloader._table_file_row_counts = {flights: 0}
    downloader._content_hashes = {"https://example/x.zip": "abc123"}
    downloader._write_manifest({"flights": flights})
    manifest = load_manifest(tmp_path / MANIFEST_FILENAME)
    assert manifest["source_contract_id"] == downloader.source_contract_id()
    assert manifest["source_contract"]["source"] == "bts-transtats"
    assert manifest["content_hashes"] == {"https://example/x.zip": "abc123"}
    assert manifest["source_provenance"]["promotion_eligible"] is False
    assert downloader._persisted_source_contract_id() == downloader.source_contract_id()


def test_manifest_reuse_requires_complete_month_evidence_and_real_months(tmp_path):
    from benchbox.core.flightdata.benchmark import FlightDataBenchmark

    benchmark = FlightDataBenchmark(scale_factor=0.1, output_dir=tmp_path)
    months = [f"{year}-{month:02d}" for year, month in benchmark.downloader.months]
    manifest = {
        "source_contract_id": benchmark.downloader.source_contract_id(),
        "source_provenance": {
            "downloaded_months": months,
            "synthetic_months": [],
            "months_downloaded": len(months),
            "months_synthetic": 0,
        },
    }
    assert benchmark.manifest_matches_datagen_identity(manifest)
    manifest["source_provenance"]["synthetic_months"] = [months[-1]]
    manifest["source_provenance"]["downloaded_months"] = months[:-1]
    manifest["source_provenance"]["months_downloaded"] -= 1
    manifest["source_provenance"]["months_synthetic"] = 1
    assert not benchmark.manifest_matches_datagen_identity(manifest)

    fallback = FlightDataBenchmark(scale_factor=0.1, output_dir=tmp_path, allow_synthetic_fallback=True)
    manifest["source_contract_id"] = fallback.downloader.source_contract_id()
    assert fallback.manifest_matches_datagen_identity(manifest)
    manifest["source_provenance"]["synthetic_months"] = []
    assert not fallback.manifest_matches_datagen_identity(manifest)


def test_direct_downloader_does_not_reuse_cache_without_month_evidence(tmp_path, monkeypatch):
    import json

    from benchbox.utils.datagen_manifest import MANIFEST_FILENAME

    downloader = FlightDataDownloader(scale_factor=0.1, output_dir=tmp_path)
    (tmp_path / MANIFEST_FILENAME).write_text(
        json.dumps({"source_contract_id": downloader.source_contract_id()}), encoding="utf-8"
    )
    monkeypatch.setattr(downloader, "_copy_reference_file", lambda _name, path: path.write_text("x\n"))
    monkeypatch.setattr(downloader, "_record_existing_csv_file", lambda *_args: None)
    monkeypatch.setattr(downloader, "_write_manifest", lambda _files: None)

    def _flights(path):
        assert downloader.force_redownload
        path.write_text("x\n", encoding="utf-8")
        return path

    monkeypatch.setattr(downloader, "_ensure_flights_data", _flights)
    downloader.download()


def test_verified_month_evidence_round_trips_through_result_bundle(tmp_path):
    from benchbox.core.flightdata.benchmark import FlightDataBenchmark
    from benchbox.core.results.loader import reconstruct_benchmark_results
    from benchbox.core.results.schema import build_result_payload
    from benchbox.core.runner.runner import _attach_datagen_version
    from tests.fixtures.result_dict_fixtures import make_benchmark_results

    benchmark = FlightDataBenchmark(scale_factor=0.01, output_dir=tmp_path)
    downloader = benchmark.downloader
    downloader._stats["months_synthetic"] = 1
    downloader._stats["synthetic_months"] = ["2024-12"]
    flights = tmp_path / "flights.csv"
    flights.write_text("x", encoding="utf-8")
    downloader._table_file_row_counts = {flights: 0}
    downloader._write_manifest({"flights": flights})

    result = _attach_datagen_version(make_benchmark_results(benchmark_name="flightdata", scale_factor=0.01), benchmark)
    provenance = result.flightdata_source_provenance
    assert provenance is not None
    assert provenance["synthetic_months"] == ["2024-12"]
    assert provenance["months_synthetic"] == 1
    bundle = build_result_payload(result)
    assert bundle["benchmark"]["source_provenance"] == provenance
    assert reconstruct_benchmark_results(bundle).flightdata_source_provenance == provenance

    unlinked = _attach_datagen_version(
        make_benchmark_results(benchmark_name="flightdata", scale_factor=0.01),
        benchmark,
        dataset_identity_established=False,
    )
    assert unlinked.flightdata_source_provenance is None


def test_prefetcher_overlaps_downloads_and_bounds_window():
    import threading

    from benchbox.core.flightdata.downloader import _ZipPrefetcher

    urls = [f"u{i}" for i in range(10)]
    started: list[str] = []
    gate = threading.Event()
    lock = threading.Lock()

    def fetch(url: str) -> bytes:
        with lock:
            started.append(url)
        gate.wait(timeout=5)
        return url.encode()

    prefetcher = _ZipPrefetcher(fetch, urls, workers=3)
    try:
        deadline = 0
        while len(started) < 3 and deadline < 500:
            threading.Event().wait(0.01)
            deadline += 1
        assert sorted(started) == ["u0", "u1", "u2"]
        gate.set()
        assert prefetcher.take("u0") == b"u0"
        assert prefetcher.take("missing") is None
        for i in range(1, 10):
            assert prefetcher.take(f"u{i}") == f"u{i}".encode()
    finally:
        prefetcher.close()
    assert sorted(started) == sorted(urls)


def test_generation_consumes_months_in_order_with_prefetch(monkeypatch, tmp_path):
    fetched: list[str] = []

    def fake_fetch(self, url):
        fetched.append(url)
        raise OSError("offline")

    monkeypatch.setattr(FlightDataDownloader, "_fetch_bts_zip", fake_fetch)
    downloader = FlightDataDownloader(1.0, tmp_path, allow_synthetic_fallback=True)
    paths = downloader._generate_flights_shards()

    assert len(paths) == len(downloader.months)
    assert sorted(fetched) == sorted(BTS_BASE_URL.format(year=y, month=m) for y, m in downloader.months)
    assert downloader._prefetcher is None
