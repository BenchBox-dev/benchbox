from __future__ import annotations

import corpus_compliance_snapshot as script
import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


def test_committed_corpus_matches_the_snapshot() -> None:
    recorded = script.load_snapshot(script.CHECKOUT_ROOT / script.SNAPSHOT_PATH)
    computed = script.compute_snapshot(script.CHECKOUT_ROOT / script.BUNDLES_DIR)

    assert script.compare(recorded, computed) == []
