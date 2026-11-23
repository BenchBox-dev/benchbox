from __future__ import annotations

from collections.abc import Callable, Mapping

SEEDED_VERSIONS = ("0.0.1", "99.0.0")


def assert_observation_tracks_engine(observe_forced: Callable[[str], str], expected: Mapping[str, str]) -> None:
    observed = {engine: observe_forced(engine) for engine in expected}
    assert observed == dict(expected)
    assert len(set(observed.values())) == len(observed), f"forcing different engines gave one value: {observed}"


def assert_observation_ignores_version(observe_under_version: Callable[[str], str]) -> None:
    observed = {version: observe_under_version(version) for version in SEEDED_VERSIONS}
    assert len(set(observed.values())) == 1, f"observation changed with the reported library version: {observed}"
