import sys
import types

import pytest

from .common import (
    install_athena_spark_stub,
    install_athena_stubs,
    install_clickhouse_stub,
    install_databend_stub,
    install_databricks_stub,
    install_dataproc_serverless_stub,
    install_dataproc_stub,
    install_doris_stub,
    install_emr_serverless_stub,
    install_google_cloud_stubs,
    install_influxdb_stub,
    install_lakesail_stub,
    install_postgresql_stub,
    install_presto_stub,
    install_redshift_stubs,
    install_snowflake_stub,
    install_starrocks_stub,
    install_trino_stub,
)
from .conftest import (
    STUB_PATCHED_MODULES,
    find_stub_adapter_attr_leaks,
    import_stub_patched_modules,
    snapshot_stub_adapter_attrs,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]

_INSTALLERS = [
    ("databricks", install_databricks_stub),
    ("bigquery", install_google_cloud_stubs),
    ("redshift", install_redshift_stubs),
    ("snowflake", install_snowflake_stub),
    ("athena", install_athena_stubs),
    ("clickhouse", install_clickhouse_stub),
    ("trino", install_trino_stub),
    ("presto", install_presto_stub),
    ("postgresql", install_postgresql_stub),
    ("influxdb", install_influxdb_stub),
    ("athena_spark", install_athena_spark_stub),
    ("emr_serverless", install_emr_serverless_stub),
    ("dataproc", install_dataproc_stub),
    ("dataproc_serverless", install_dataproc_serverless_stub),
    ("starrocks", install_starrocks_stub),
    ("databend", install_databend_stub),
    ("doris", install_doris_stub),
    ("lakesail", install_lakesail_stub),
]


@pytest.mark.integration
@pytest.mark.platform_smoke
@pytest.mark.parametrize("name,installer", _INSTALLERS, ids=[name for name, _ in _INSTALLERS])
def test_stub_installer_restores_adapter_attrs(monkeypatch, name, installer):
    import_stub_patched_modules()
    before = snapshot_stub_adapter_attrs()
    installer(monkeypatch)
    monkeypatch.undo()
    assert find_stub_adapter_attr_leaks(before) == []


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_stub_installer_ignores_warningregistry(monkeypatch):
    import_stub_patched_modules()
    before = snapshot_stub_adapter_attrs()
    first_module = sys.modules[STUB_PATCHED_MODULES[0]]
    monkeypatch.setattr(first_module, "__warningregistry__", {"test": 1}, raising=False)
    assert find_stub_adapter_attr_leaks(before) == []


@pytest.mark.integration
@pytest.mark.platform_smoke
def test_stub_guard_flags_unrestored_chdb_write():
    from benchbox.platforms.clickhouse import _dependencies

    import_stub_patched_modules()
    before = snapshot_stub_adapter_attrs()
    previous = _dependencies.chdb
    _dependencies.chdb = types.ModuleType("chdb")
    try:
        assert "benchbox.platforms.clickhouse._dependencies.chdb" in find_stub_adapter_attr_leaks(before)
    finally:
        _dependencies.chdb = previous
