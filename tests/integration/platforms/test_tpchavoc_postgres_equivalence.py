# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark(TM) H (TPC-H) - Copyright (C) Transaction Processing Performance Council.
# This implementation is derived from TPC-H.

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.core.tpchavoc.equivalence import (
    EQUIVALENCE_SCALE,
    POSTGRES_KNOWN_DIVERGENCES,
    build_postgres_with_tpch,
    find_postgres_divergences,
    postgres_connection_config,
)
from benchbox.sql_compat.rules.execution_filter.postgres_tpchavoc import POSTGRES_TPCHAVOC_SKIPS

from .conftest import skip_unless_docker_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker_integration,
    pytest.mark.live_integration,
    pytest.mark.live_postgresql,
    pytest.mark.tpchavoc,
    pytest.mark.slow,
]


@pytest.fixture(scope="module")
def postgres_divergences(tmp_path_factory):
    config = postgres_connection_config()
    skip_unless_docker_service(config["host"], config["port"], platform="PostgreSQL")
    output_dir = tmp_path_factory.mktemp("tpchavoc_pg_equivalence")
    connection, tpchavoc, tpch = build_postgres_with_tpch(EQUIVALENCE_SCALE, output_dir, connection_config=config)
    try:
        yield find_postgres_divergences(connection, tpchavoc, tpch)
    finally:
        connection.close()


def test_all_executable_variants_equivalent_on_postgres(postgres_divergences):
    unexpected = {d.key for d in postgres_divergences} - set(POSTGRES_KNOWN_DIVERGENCES)
    assert not unexpected, "Variant(s) diverge from canonical TPC-H on PostgreSQL: " + ", ".join(
        f"{d.key} ({d.detail})" for d in postgres_divergences if d.key in unexpected
    )


def test_skipped_variants_are_excluded_never_marked_equivalent(postgres_divergences):
    reported = {d.key for d in postgres_divergences}
    assert reported.isdisjoint(set(POSTGRES_TPCHAVOC_SKIPS)), (
        "Un-executable (skip-list) variants must be excluded, not evaluated"
    )


