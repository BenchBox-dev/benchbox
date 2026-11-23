from __future__ import annotations

import pytest

from tests.unit.platforms.dataframe.observed_engine_contract import (
    assert_observation_ignores_version,
    assert_observation_tracks_engine,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

EXPECTED = {"first": "first", "second": "second"}


def test_contract_accepts_an_observation_that_follows_the_forced_engine():
    assert_observation_tracks_engine(lambda engine: engine, EXPECTED)


def test_contract_fails_on_a_constant_implementation():
    with pytest.raises(AssertionError):
        assert_observation_tracks_engine(lambda engine: "first", EXPECTED)


def test_contract_fails_when_the_value_is_computed_from_the_library_version_alone():
    def from_version_table(version: str) -> str:
        return "second" if int(version.split(".")[0]) >= 2 else "first"

    with pytest.raises(AssertionError):
        assert_observation_ignores_version(from_version_table)


def test_contract_accepts_an_observation_that_ignores_the_version():
    assert_observation_ignores_version(lambda version: "first")
