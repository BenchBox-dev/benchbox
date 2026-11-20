from __future__ import annotations

import corpus_compliance_snapshot as script
import pytest

from benchbox.core.benchmark_registry import get_benchmark_metadata
from benchbox.core.results import subscale_policy

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

BUNDLES = script.CHECKOUT_ROOT / script.BUNDLES_DIR
SNAPSHOT = script.CHECKOUT_ROOT / script.SNAPSHOT_PATH


@pytest.fixture(scope="module")
def recorded() -> dict[str, script.Classification]:
    return script.load_snapshot(SNAPSHOT)


def _changed_files(problems: list[str]) -> set[str]:
    return {line.split(":", 1)[0].strip() for line in problems if line.startswith("  ")}


def test_committed_corpus_matches_the_snapshot(recorded) -> None:
    assert script.compare(recorded, script.compute_classes(BUNDLES)) == []


def test_removing_a_standard_scale_fails_and_names_the_reclassified_bundles(recorded, monkeypatch) -> None:
    tpch = get_benchmark_metadata("tpch")
    monkeypatch.setitem(tpch, "scale_options", [scale for scale in tpch["scale_options"] if float(scale) != 1.0])

    problems = script.compare(recorded, script.compute_classes(BUNDLES))

    assert "changed compliance class" in problems[0]
    assert script.REGENERATE_COMMAND in problems[0]
    changed = _changed_files(problems)
    assert changed
    assert all(name.rsplit("/", 1)[-1].startswith("tpch_sf1_") for name in changed)
    assert any("-> {'compliance_class': 'unofficial_nonstandard', 'ranking_excluded': True}" in p for p in problems)


def test_changing_the_subscale_rule_fails_and_names_the_bundles(recorded, monkeypatch) -> None:
    monkeypatch.setattr(subscale_policy, "SUBSCALE_TPC_BENCHMARKS", frozenset({"tpcds"}))

    changed = _changed_files(script.compare(recorded, script.compute_classes(BUNDLES)))

    assert changed
    assert all(name.rsplit("/", 1)[-1].startswith("tpch_sf0") for name in changed)


def test_changing_which_classes_are_ranked_fails(recorded, monkeypatch) -> None:
    monkeypatch.setattr(script, "RANKING_EXCLUDED_COMPLIANCE_CLASSES", frozenset())

    changed = _changed_files(script.compare(recorded, script.compute_classes(BUNDLES)))

    assert changed == {name for name, entry in recorded.items() if entry["ranking_excluded"]}


def test_added_and_removed_bundles_fail_with_the_regenerate_command(recorded) -> None:
    computed = dict(recorded)
    removed = next(iter(computed))
    del computed[removed]
    computed["new/bundle.json"] = {"compliance_class": None, "ranking_excluded": False}

    text = "\n".join(script.compare(recorded, computed))

    assert "new/bundle.json" in text
    assert removed in text
    assert "changed compliance class" not in text
    assert script.REGENERATE_COMMAND in text
