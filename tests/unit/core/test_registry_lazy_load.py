from __future__ import annotations

import subprocess
import sys
import threading
import time
from typing import Any

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _run(code: str) -> list[str]:
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return result.stdout.strip().splitlines()


def test_catalogs_load_lazily_not_at_import() -> None:
    lines = _run(
        "import benchbox.core.benchmark_registry as r;"
        "import benchbox.core.results.benchmark_specs as s;"
        "print(r._registry.cache_info().misses, s._specs.cache_info().misses);"
        "r.BENCHMARK_METADATA;"
        "s.BENCHMARK_SPECS;"
        "print(r._registry.cache_info().misses, s._specs.cache_info().misses)"
    )
    assert lines == ["0 0", "1 1"]


def test_dataframe_query_metadata_loads_lazily_not_at_import() -> None:
    lines = _run(
        "import benchbox.core.read_primitives.dataframe_queries as rp;"
        "import benchbox.core.tpcds.dataframe_queries as tpcds;"
        "print(rp.REGISTRY.load_info().misses, tpcds.TPCDS_DATAFRAME_QUERIES.load_info().misses);"
        "len(rp.REGISTRY);"
        "len(tpcds.TPCDS_DATAFRAME_QUERIES);"
        "print(rp.REGISTRY.load_info().misses, tpcds.TPCDS_DATAFRAME_QUERIES.load_info().misses)"
    )
    assert lines == ["0 0", "1 1"]


def test_lazy_registry_first_access_is_single_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    import benchbox.core.benchmark_registry as r

    original_load = r._load_registry_payload
    calls = 0
    calls_lock = threading.Lock()

    def slow_load() -> dict[str, Any]:
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.02)
        return original_load()

    r._registry.cache_clear()
    monkeypatch.setattr(r, "_load_registry_payload", slow_load)

    errors: list[BaseException] = []

    def access_registry() -> None:
        try:
            assert len(r.BENCHMARK_METADATA) > 0
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=access_registry) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert calls == 1
    assert r._registry.cache_info().misses == 1
    r._registry.cache_clear()


def test_lazy_public_names_survive_star_import() -> None:
    lines = _run(
        "ns = {};"
        "exec('from benchbox.core.benchmark_registry import *', ns);"
        "print('BENCHMARK_METADATA' in ns, 'CATEGORY_ORDER' in ns);"
        "ns = {};"
        "exec('from benchbox.core.results.benchmark_specs import *', ns);"
        "print('BENCHMARK_SPECS' in ns, 'LEGACY_ALIASES' in ns)"
    )
    assert lines == ["True True", "True True"]


def test_unknown_attribute_still_raises_attribute_error() -> None:
    import benchbox.core.benchmark_registry as r
    import benchbox.core.results.benchmark_specs as s

    with pytest.raises(AttributeError):
        r.DOES_NOT_EXIST  # noqa: B018
    with pytest.raises(AttributeError):
        s.DOES_NOT_EXIST  # noqa: B018
