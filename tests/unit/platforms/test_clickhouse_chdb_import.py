from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from benchbox.platforms.clickhouse import _dependencies as dependencies

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_import_chdb_restores_cwd_when_native_load_fails(tmp_path):

    original_cwd = os.getcwd()
    package_dir = tmp_path / "site-packages" / "chdb"
    package_dir.mkdir(parents=True)

    def failing_import(module_name: str):
        assert module_name == "chdb"
        os.chdir(package_dir)
        raise ImportError("mis-aligned LINKEDIT string pool")

    with patch.object(dependencies.importlib, "import_module", side_effect=failing_import):
        with pytest.raises(ImportError, match="mis-aligned LINKEDIT"):
            dependencies.import_chdb()

    assert os.getcwd() == original_cwd


def test_import_chdb_returns_the_module_without_binding_the_adapter_module_global():
    import types

    fake_chdb = types.ModuleType("chdb")
    assert dependencies.chdb is None

    with patch.object(dependencies.importlib, "import_module", return_value=fake_chdb):
        assert dependencies.import_chdb() is fake_chdb

    assert dependencies.chdb is None


