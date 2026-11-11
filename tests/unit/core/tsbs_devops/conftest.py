# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from datetime import datetime

import pytest


@pytest.fixture
def seed() -> int:

    return 42


@pytest.fixture
def start_time() -> datetime:

    return datetime(2024, 1, 1, 0, 0, 0)


@pytest.fixture
def test_num_hosts() -> int:

    return 5


@pytest.fixture
def test_duration_days() -> int:

    return 1


@pytest.fixture
def test_interval_seconds() -> int:

    return 3600
