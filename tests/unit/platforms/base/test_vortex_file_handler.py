from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from benchbox.platforms.base.data_loading import VortexFileHandler

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_vortex_file_handler_reads_using_string_path(monkeypatch, tmp_path):

    class _ArrowTable:
        num_rows = 1
        schema = SimpleNamespace(names=["id"])

        def to_pylist(self):
            return [{"id": 1}]

    class _VortexArray:
        def to_arrow(self):
            return _ArrowTable()

    def _read(path):
        assert isinstance(path, str)
        return _VortexArray()

    fake_vortex = SimpleNamespace(io=SimpleNamespace(read=_read))
    monkeypatch.setitem(sys.modules, "vortex", fake_vortex)

    handler = VortexFileHandler()
    connection = Mock()
    benchmark = Mock()
    logger = Mock()
    file_path = tmp_path / "customer.vortex"

    row_count = handler.load_table("customer", file_path, connection, benchmark, logger)

    assert row_count == 1
    connection.executemany.assert_called_once()


def test_vortex_file_handler_falls_back_to_vortex_open(monkeypatch, tmp_path):

    class _ArrowTable:
        num_rows = 1
        schema = SimpleNamespace(names=["id"])

        def to_pylist(self):
            return [{"id": 1}]

    class _VortexFile:
        def to_arrow(self):
            return _ArrowTable()

    def _open(path):
        assert isinstance(path, str)
        return _VortexFile()

    fake_vortex = SimpleNamespace(open=_open)
    monkeypatch.setitem(sys.modules, "vortex", fake_vortex)

    handler = VortexFileHandler()
    connection = Mock()
    benchmark = Mock()
    logger = Mock()
    file_path = tmp_path / "customer.vortex"

    row_count = handler.load_table("customer", file_path, connection, benchmark, logger)

    assert row_count == 1
    connection.executemany.assert_called_once()


