from __future__ import annotations

from fractions import Fraction

import pytest

from benchbox.core.joinorder_sampled.sampler import (
    MOVIE_LINK_ENDPOINTS,
    MOVIE_LINK_TABLE,
    PERSON_CHILD_TABLES,
    TITLE_CHILD_TABLES,
    VERBATIM_TABLES,
    SampledManifest,
    close_title_set_over_episode_parents,
    keep_movie_link_row,
    keep_person_child_row,
    keep_title_child_row,
    keep_title_id,
    manifest_hash,
    merge_episode_parents,
    sample_title_ids,
    sample_title_ids_with_episode_closure,
    scale_to_fraction,
    surviving_person_ids,
    title_hash_rank,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_scale_factor_semantics_accepts_fractions():
    assert scale_to_fraction(1.0) == Fraction(1, 1)
    assert scale_to_fraction(0.5) == Fraction(1, 2)
    assert scale_to_fraction(0.1).denominator == 10


def test_scale_factor_semantics_rejects_out_of_range():
    for bad in (0.0, -0.5, 1.5, 2.0):
        with pytest.raises(ValueError):
            scale_to_fraction(bad)


def test_scale_factor_rejects_bool():
    for bad in (True, False):
        with pytest.raises(TypeError):
            scale_to_fraction(bad)


def test_scale_factor_rejects_values_rounded_into_range():
    with pytest.raises(ValueError):
        scale_to_fraction(1.0001)
    with pytest.raises(ValueError):
        scale_to_fraction(0.0001)
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            scale_to_fraction(bad)


def test_scale_factor_accepts_exact_fraction_input():
    assert scale_to_fraction(Fraction(1, 4)) == Fraction(1, 4)


def test_movie_link_requires_both_endpoints():
    assert MOVIE_LINK_TABLE == "movie_link"
    assert MOVIE_LINK_ENDPOINTS == ("movie_id", "linked_movie_id")
    assert MOVIE_LINK_TABLE not in TITLE_CHILD_TABLES
    kept = {1, 2}
    assert keep_movie_link_row({"movie_id": 1, "linked_movie_id": 2}, kept)
    assert not keep_movie_link_row({"movie_id": 1, "linked_movie_id": 3}, kept)
    assert not keep_movie_link_row({"movie_id": 3, "linked_movie_id": 2}, kept)
    assert not keep_movie_link_row({"movie_id": 1, "linked_movie_id": None}, kept)


def test_single_key_child_row_survives_on_movie_id():
    assert keep_title_child_row({"movie_id": 1}, {1, 2}, TITLE_CHILD_TABLES["cast_info"])
    assert not keep_title_child_row({"movie_id": 3}, {1, 2}, TITLE_CHILD_TABLES["cast_info"])


def test_episode_parent_closure_retains_series_title():
    kept = close_title_set_over_episode_parents({10}, {10: 1, 11: 2})
    assert kept == {10, 1}
    assert close_title_set_over_episode_parents({10}, {10: None}) == {10}


def test_episode_parent_closure_is_transitive():
    parents = {30: 20, 20: 10, 10: None}
    assert close_title_set_over_episode_parents({30}, parents) == {30, 20, 10}


def test_episode_parent_closure_handles_cycles():
    parents = {1: 2, 2: 1}
    assert close_title_set_over_episode_parents({1}, parents) == {1, 2}


def test_merge_episode_parents_covers_both_tables():
    merged = merge_episode_parents({10: None, 11: 5}, {10: 7, 12: 9})
    assert merged == {10: 7, 11: 5, 12: 9}
    merged_conflict = merge_episode_parents({10: 5}, {10: 7})
    assert merged_conflict[10] == 5


def test_episode_parent_closure_closes_aka_only_parent():
    merged = merge_episode_parents({}, {10: 1})
    assert close_title_set_over_episode_parents({10}, merged) == {10, 1}


def test_clustered_selection_tracks_scale_quota():
    fraction = Fraction(1, 2)
    records = [
        {"id": 1},
        {"id": 2},
        {"id": 3},
        {"id": 4},
        {"id": 5},
    ]
    parents = {2: 1, 3: 1, 5: 4}
    kept = sample_title_ids_with_episode_closure(records, parents, fraction, seed=7)
    assert len(kept) / len(records) == pytest.approx(0.5, abs=0.25)
    for title_id, parent in parents.items():
        if title_id in kept:
            assert parent in kept


def test_person_subgraph_closure_filters_to_surviving_cast():
    kept_titles = {1, 2}
    cast_rows = [
        {"movie_id": 1, "person_id": 100},
        {"movie_id": 2, "person_id": 101},
        {"movie_id": 3, "person_id": 102},
        {"movie_id": 1, "person_id": None},
    ]
    surviving = surviving_person_ids(cast_rows, kept_titles)
    assert surviving == {100, 101}
    assert keep_person_child_row({"person_id": 100}, surviving)
    assert not keep_person_child_row({"person_id": 102}, surviving)
    assert not keep_person_child_row({"person_id": None}, surviving)
    assert set(PERSON_CHILD_TABLES) == {"aka_name", "person_info"}
    assert "name" in VERBATIM_TABLES
    assert "cast_info" not in PERSON_CHILD_TABLES


def test_keep_predicate_is_deterministic_and_seeded():
    fraction = scale_to_fraction(0.5)
    first = [title_hash_rank(i, (1, 2000), 11) for i in range(200)]
    second = [title_hash_rank(i, (1, 2000), 11) for i in range(200)]
    assert first == second
    assert any(title_hash_rank(i, (1, 2000), 11) != title_hash_rank(i, (1, 2000), 12) for i in range(50)), (
        "different seeds should decorrelate ranks"
    )
    kept_a = [i for i in range(1000) if keep_title_id(i, fraction, seed=11, kind_id=1, production_year=2000)]
    kept_b = [i for i in range(1000) if keep_title_id(i, fraction, seed=11, kind_id=1, production_year=2000)]
    assert kept_a == kept_b
    assert 0 < len(kept_a) < 1000


def test_stratified_selection_hits_each_stratum_proportionally():
    fraction = Fraction(1, 2)
    records = [{"id": i, "kind_id": 1 if i < 100 else 2, "production_year": 2000} for i in range(200)]
    kept = sample_title_ids(records, fraction, seed=3)
    assert len(kept) == 100
    assert sum(1 for i in kept if i < 100) == 50
    assert sum(1 for i in kept if i >= 100) == 50


def test_stratified_selection_decorrelates_adjacent_ids():
    fraction = Fraction(1, 2)
    records = [{"id": i, "kind_id": 1, "production_year": 2000} for i in range(100)]
    kept = sample_title_ids(records, fraction, seed=5)
    runs = sum(1 for i in sorted(kept) if i + 1 not in kept)
    assert runs > 1, "hash sampling must not keep one contiguous id block"


def test_keep_predicate_fraction_matches_scale():
    for scale in (0.1, 0.25, 0.5):
        fraction = scale_to_fraction(scale)
        records = [{"id": i, "kind_id": (i % 4) + 1, "production_year": 1990 + (i % 20)} for i in range(10000)]
        kept = sample_title_ids(records, fraction, seed=9)
        assert len(kept) == int(round(10000 * float(fraction)))


def test_join_graph_covers_all_canonical_tables():
    import tomllib
    from pathlib import Path

    manifest_path = Path(__file__).resolve().parents[4] / "benchbox" / "core" / "joinorder" / "data_manifest.toml"
    tables = {t["name"] for t in tomllib.load(manifest_path.open("rb"))["tables"]}
    covered = set(TITLE_CHILD_TABLES) | {MOVIE_LINK_TABLE} | set(PERSON_CHILD_TABLES) | set(VERBATIM_TABLES) | {"title"}
    missing = tables - covered
    assert not missing, f"uncovered canonical tables: {missing}"
    assert covered <= tables | {"title"}, f"unknown sampled tables: {covered - tables - {'title'}}"
    assert "person_info" in PERSON_CHILD_TABLES
    assert "name" in VERBATIM_TABLES


def test_derived_identity_never_claims_canonical():
    manifest = SampledManifest(scale_factor=0.5, kept_title_ids=1, total_title_ids=2)
    body = manifest.to_dict()
    assert body["derived_identity"] == "joinorder_sampled"
    assert body["derived_identity"] != "canonical_imdb"
    assert body["source_identity"] == "canonical_imdb"


def test_manifest_binds_exact_source_archive():
    manifest = SampledManifest(
        scale_factor=0.5,
        kept_title_ids=10,
        total_title_ids=20,
        source_dataset_version="joinorder-imdb-2013-v1",
        source_manifest_hash="abc123",
        source_data_archive_hash="def456",
    )
    body = manifest.to_dict()
    assert body["source_dataset_version"] == "joinorder-imdb-2013-v1"
    assert body["source_manifest_hash"] == "abc123"
    assert body["source_data_archive_hash"] == "def456"
    rebound = SampledManifest(scale_factor=0.5, kept_title_ids=10, total_title_ids=20)
    assert manifest_hash(manifest) != manifest_hash(rebound)


def test_manifest_hash_stable():
    first = SampledManifest(scale_factor=0.5, kept_title_ids=10, total_title_ids=20)
    second = SampledManifest(scale_factor=0.5, kept_title_ids=10, total_title_ids=20)
    assert manifest_hash(first) == manifest_hash(second)
    other = SampledManifest(scale_factor=0.25, kept_title_ids=10, total_title_ids=20)
    assert manifest_hash(first) != manifest_hash(other)


