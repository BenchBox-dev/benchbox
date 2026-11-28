from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from _project.scripts.explorer_pipeline.transformer import BundleTransformer, _parse_bundle
from tests.unit.scripts.explorer_pipeline.conftest import MINIMAL_BUNDLE

pytestmark = [pytest.mark.unit, pytest.mark.fast]

OBSERVATIONS = {
    "observed": {"observed": "streaming", "observed_source": "explain"},
    "mixed": {"observed": "mixed", "observed_source": "explain"},
    "not_captured": {
        "observed": "not_captured",
        "observed_source": "none",
        "observed_reason": "no query completed",
    },
}


def _bundle(observation: dict | None) -> dict:
    data = copy.deepcopy(MINIMAL_BUNDLE)
    if observation is not None:
        data["platform"]["execution_engine"] = {
            "requested": "default",
            "applied": "auto",
            "resolution": "version_default",
            **observation,
        }
    return data


@pytest.mark.parametrize("name", sorted(OBSERVATIONS))
def test_pipeline_parses_the_observed_engine_fields(name: str) -> None:
    block = _parse_bundle(_bundle(OBSERVATIONS[name])).platform.execution_engine
    expected = OBSERVATIONS[name]
    assert block.observed == expected["observed"]
    assert block.observed_source == expected["observed_source"]
    assert block.observed_reason == expected.get("observed_reason")


def test_bundle_without_the_field_parses_to_nothing_observed() -> None:
    block = _parse_bundle(_bundle(None)).platform.execution_engine
    assert (block.observed, block.observed_source, block.observed_reason) == (None, None, None)


def test_malformed_observation_does_not_reject_the_bundle() -> None:
    data = _bundle(None)
    data["platform"]["execution_engine"] = {"observed": ["mixed"], "observed_source": 3}
    block = _parse_bundle(data).platform.execution_engine
    assert (block.observed, block.observed_source) == (None, None)


@pytest.mark.parametrize("name", sorted(OBSERVATIONS))
def test_observed_engine_reaches_only_the_observed_engine_column(name: str, tmp_path: Path) -> None:
    emitted = {}
    for label, observation in (("with", OBSERVATIONS[name]), ("without", None)):
        path = tmp_path / label / "tpch_duckdb_sf0.1_20260315.json"
        path.parent.mkdir()
        path.write_text(json.dumps(_bundle(observation)), encoding="utf-8")
        transformer = BundleTransformer()
        emitted[label] = (
            transformer.to_manifest_entry(path, result_id="fixed").model_dump(mode="json"),
            transformer.to_detail_result(path, result_id="fixed").model_dump(mode="json"),
        )
    observed = emitted["with"][0].pop("observed_engine")
    assert emitted["without"][0].pop("observed_engine") is None
    assert emitted["with"] == emitted["without"]
    recorded = OBSERVATIONS[name]["observed"]
    assert observed == (None if recorded == "not_captured" else recorded)
