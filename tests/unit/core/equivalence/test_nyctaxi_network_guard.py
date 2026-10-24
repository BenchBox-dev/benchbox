from __future__ import annotations

import urllib.request

import pytest

from benchbox.core.equivalence.builders.nyctaxi import _network_forbidden
from benchbox.utils import tls

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_guard_blocks_the_download_helpers_the_downloaders_use(tmp_path):
    with _network_forbidden():
        with pytest.raises(AssertionError, match="must not touch the network"):
            tls.download_to_file("https://example.invalid/data.parquet", tmp_path / "data.parquet")
        with pytest.raises(AssertionError, match="must not touch the network"):
            tls.urlopen("https://example.invalid/data.parquet", timeout=1)
        with pytest.raises(AssertionError, match="must not touch the network"):
            urllib.request.urlretrieve("https://example.invalid/data.parquet", tmp_path / "data.parquet")


def test_guard_restores_urlopen_after_an_error():
    original = urllib.request.urlopen
    with pytest.raises(RuntimeError), _network_forbidden():
        raise RuntimeError("boom")
    assert urllib.request.urlopen is original
