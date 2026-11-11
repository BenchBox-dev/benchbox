from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.joinorder_replicated.replicator import (
    DERIVED_IDENTITY,
    INT32_MAX,
    KEY_COLUMNS,
    LOOKUP_FK_COLUMNS,
    LOOKUP_TABLES,
    REPLICATED_FK_TARGETS,
    REPLICATED_TABLES,
    REQUIRES_REAPPROVAL,
    SOURCE_IDENTITY,
    ReplicatedManifest,
    build_replicated_manifest,
    collect_key_maxima,
    derive_key_spaces,
    expected_row_count,
    expected_table_row_count,
    fk_target_table,
    manifest_hash,
    max_supported_scale_factor,
    parquet_column_max,
    promoted_key_type,
    replica_offset,
    requires_bigint_promotion,
    shifted_fk,
    shifted_key,
    shifted_pk,
    validate_key_spaces,
    validate_scale_factor,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_scale_factor_accepts_integers():
    assert validate_scale_factor(1) == 1
    assert validate_scale_factor(3) == 3


def test_scale_factor_rejects_non_integers_and_zero():
    for bad in (0, -1, 1.5, "2", True, None):
        with pytest.raises(ValueError):
            validate_scale_factor(bad)


def test_replica_zero_is_identity():
    assert replica_offset(0, 1_000_000) == 0
    assert shifted_key(42, replica=0, key_space=1_000_000) == 42


def test_replica_offsets_are_disjoint():
    key_space = 40_000_000
    first = shifted_key(36_244_344, replica=1, key_space=key_space)
    assert first == 36_244_344 + key_space
    assert shifted_key(36_244_344, replica=2, key_space=key_space) == 36_244_344 + 2 * key_space


def test_null_keys_pass_through():
    assert shifted_key(None, replica=1, key_space=100) is None


def test_replicated_fk_targets_cover_all_non_pk_key_columns():
    non_pk = {(table, column) for table, column in KEY_COLUMNS if column != "id"}
    targeted = {(table, column) for table, column, _ in REPLICATED_FK_TARGETS}
    assert targeted == non_pk, f"unmapped FKs: {non_pk - targeted}; stale: {targeted - non_pk}"
    for _, _, target in REPLICATED_FK_TARGETS:
        assert target in REPLICATED_TABLES


def test_fk_target_table_rejects_unknown_columns():
    with pytest.raises(ValueError):
        fk_target_table("title", "id")
    with pytest.raises(ValueError):
        fk_target_table("movie_companies", "company_type_id")
    with pytest.raises(ValueError):
        fk_target_table("no_such_table", "movie_id")


def test_shifted_fk_uses_target_key_space_not_referencing_space():
    spaces = {"keyword": 200_000, "movie_keyword": 9_000_000, "title": 3_000_000}
    shifted = shifted_fk(
        7,
        replica=2,
        key_space_per_table=spaces,
        referencing_table="movie_keyword",
        column="keyword_id",
    )
    assert shifted == 7 + 2 * spaces["keyword"]
    assert shifted != 7 + 2 * spaces["movie_keyword"]


def test_referential_integrity_holds_with_distinct_per_table_key_spaces():
    spaces = {"keyword": 200_000, "movie_keyword": 9_000_000, "title": 3_000_000}
    keyword_id, movie_id = 42, 1_000
    for replica in (1, 2):
        assert shifted_fk(
            keyword_id,
            replica=replica,
            key_space_per_table=spaces,
            referencing_table="movie_keyword",
            column="keyword_id",
        ) == shifted_pk(keyword_id, replica=replica, key_space_per_table=spaces, table="keyword")
        assert shifted_fk(
            movie_id,
            replica=replica,
            key_space_per_table=spaces,
            referencing_table="movie_keyword",
            column="movie_id",
        ) == shifted_pk(movie_id, replica=replica, key_space_per_table=spaces, table="title")
    first = shifted_pk(keyword_id, replica=1, key_space_per_table=spaces, table="keyword")
    second = shifted_pk(keyword_id, replica=2, key_space_per_table=spaces, table="keyword")
    assert first >= spaces["keyword"] > keyword_id
    assert second >= 2 * spaces["keyword"] > first


def test_shifted_pk_and_fk_pass_nulls_and_reject_missing_spaces():
    spaces = {"title": 3_000_000}
    assert shifted_pk(None, replica=1, key_space_per_table=spaces, table="title") is None
    assert (
        shifted_fk(None, replica=1, key_space_per_table=spaces, referencing_table="title", column="episode_of_id")
        is None
    )
    with pytest.raises(ValueError):
        shifted_pk(1, replica=1, key_space_per_table=spaces, table="name")
    with pytest.raises(ValueError):
        shifted_fk(1, replica=1, key_space_per_table={}, referencing_table="title", column="episode_of_id")


def test_offset_rejects_bad_inputs():
    with pytest.raises(ValueError):
        replica_offset(-1, 100)
    with pytest.raises(ValueError):
        replica_offset(1, 0)


def _write_parquet(path, column_values):
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table({name: pa.array(values, type=pa.int32()) for name, values in column_values.items()})
    pq.write_table(table, path)


@pytest.fixture()
def _source_parquet_dir(tmp_path):
    maxima = {
        "title": {"id": [1, 2_528_312], "episode_of_id": [None, 10]},
        "keyword": {"id": [1, 134_170]},
        "movie_keyword": {"id": [1, 4_500_000], "movie_id": [1, 2_528_310], "keyword_id": [1, 134_170]},
    }
    for table, columns in maxima.items():
        _write_parquet(tmp_path / f"{table}.parquet", columns)
    keyed: dict[str, set[str]] = {}
    for table, column in KEY_COLUMNS:
        keyed.setdefault(table, set()).add(column)
    for table, columns in keyed.items():
        target = tmp_path / f"{table}.parquet"
        if not target.exists():
            _write_parquet(target, dict.fromkeys(columns, [1]))
    return tmp_path


def test_parquet_column_max_reads_metadata_statistics(_source_parquet_dir):
    assert parquet_column_max(_source_parquet_dir / "title.parquet", "id") == 2_528_312
    assert parquet_column_max(_source_parquet_dir / "movie_keyword.parquet", "keyword_id") == 134_170


def test_parquet_column_max_fails_loudly(tmp_path):
    _write_parquet(tmp_path / "title.parquet", {"id": [1, 5]})
    with pytest.raises(FileNotFoundError):
        parquet_column_max(tmp_path / "missing.parquet", "id")
    with pytest.raises(ValueError):
        parquet_column_max(tmp_path / "title.parquet", "no_such_column")


def test_collect_key_maxima_covers_every_key_column(_source_parquet_dir):
    maxima = collect_key_maxima(_source_parquet_dir)
    assert set(maxima) == set(KEY_COLUMNS)
    assert maxima[("title", "id")] == 2_528_312


def test_derive_key_spaces_exceeds_every_landing_max(_source_parquet_dir):
    spaces = derive_key_spaces(_source_parquet_dir)
    assert set(spaces) == set(REPLICATED_TABLES)
    assert spaces["title"] == 2_528_312 + 1
    assert spaces["keyword"] == 134_170 + 1
    assert spaces["movie_keyword"] == 4_500_000 + 1
    validate_key_spaces(spaces, collect_key_maxima(_source_parquet_dir))


def test_validate_key_spaces_rejects_collision_risk():
    maxima = {("title", "id"): 100, ("title", "episode_of_id"): 50}
    with pytest.raises(ValueError):
        validate_key_spaces({"title": 100}, maxima)
    with pytest.raises(ValueError):
        validate_key_spaces({"title": 0}, maxima)
    with pytest.raises(ValueError):
        validate_key_spaces({}, maxima)


def test_build_replicated_manifest_wires_derivation(_source_parquet_dir):
    manifest = build_replicated_manifest(
        2,
        _source_parquet_dir,
        dataset_version="joinorder-imdb-2013-v1",
        data_archive_hash="abc",
        source_manifest_hash="def",
    )
    assert manifest.scale_factor == 2
    assert manifest.key_space_per_table["title"] == 2_528_312 + 1
    assert manifest.dataset_version == "joinorder-imdb-2013-v1"
    assert manifest.data_archive_hash == "abc"
    assert manifest.source_manifest_hash == "def"
    with pytest.raises(ValueError):
        build_replicated_manifest(0, _source_parquet_dir)


def test_max_supported_scale_factor_and_bigint_promotion():
    spaces = {"title": 2_528_313, "keyword": 134_171, "movie_keyword": 4_500_001}
    maxima = {
        ("title", "id"): 2_528_312,
        ("keyword", "id"): 134_170,
        ("movie_keyword", "id"): 4_500_000,
        ("movie_keyword", "movie_id"): 2_528_310,
        ("movie_keyword", "keyword_id"): 134_170,
    }
    ceiling = max_supported_scale_factor(spaces, maxima)
    assert ceiling == min(
        (INT32_MAX - maximum) // spaces[owner] + 1
        for (table, column), maximum, owner in (
            (("title", "id"), 2_528_312, "title"),
            (("keyword", "id"), 134_170, "keyword"),
            (("movie_keyword", "id"), 4_500_000, "movie_keyword"),
            (("movie_keyword", "movie_id"), 2_528_310, "title"),
            (("movie_keyword", "keyword_id"), 134_170, "keyword"),
        )
    )
    assert ceiling >= 2
    assert promoted_key_type(1, spaces, maxima) == "INTEGER"
    assert promoted_key_type(ceiling, spaces, maxima) == "INTEGER"
    assert requires_bigint_promotion(ceiling + 1, spaces, maxima) is True
    assert promoted_key_type(ceiling + 1, spaces, maxima) == "BIGINT"
    assert requires_bigint_promotion(1, spaces, maxima) is False


def test_expected_table_row_count_rejects_unknown_tables():
    with pytest.raises(ValueError):
        expected_table_row_count("no_such_table", 100, 2)
    with pytest.raises(ValueError):
        expected_table_row_count("TITLE", 100, 2)


def test_row_counts_against_canonical_manifest():
    import tomllib
    from pathlib import Path

    manifest_path = Path(__file__).resolve().parents[4] / "benchbox" / "core" / "joinorder" / "data_manifest.toml"
    tables = {t["name"]: t["row_count"] for t in tomllib.load(manifest_path.open("rb"))["tables"]}
    assert len(tables) == len(REPLICATED_TABLES) + len(LOOKUP_TABLES)
    for table, rows in tables.items():
        assert expected_table_row_count(table, rows, 1) == rows
        if table in LOOKUP_TABLES:
            assert expected_table_row_count(table, rows, 3) == rows
        else:
            scaled = expected_table_row_count(table, rows, 3)
            assert scaled == rows * 3
            assert scaled > rows


def test_key_columns_cover_all_canonical_tables():
    import tomllib
    from pathlib import Path

    manifest_path = Path(__file__).resolve().parents[4] / "benchbox" / "core" / "joinorder" / "data_manifest.toml"
    tables = {t["name"] for t in tomllib.load(manifest_path.open("rb"))["tables"]}
    keyed = {table for table, _ in KEY_COLUMNS} | set(LOOKUP_TABLES)
    assert keyed == tables, f"uncovered: {tables - keyed}"


def test_key_columns_are_integer_columns_per_committed_manifest():
    import tomllib
    from pathlib import Path

    manifest_path = Path(__file__).resolve().parents[4] / "benchbox" / "core" / "joinorder" / "data_manifest.toml"
    schemas = {t["name"]: t.get("schema", {}) for t in tomllib.load(manifest_path.open("rb"))["tables"]}
    missing = [
        f"{table}.{column}"
        for table, column in KEY_COLUMNS
        if schemas.get(table, {}).get(column, "").upper() != "INTEGER"
    ]
    assert not missing, f"non-integer or missing key columns: {missing}"


def test_lookup_tables_stay_single_copy():
    assert set(LOOKUP_TABLES) == {
        "company_type",
        "comp_cast_type",
        "info_type",
        "kind_type",
        "link_type",
        "role_type",
    }
    assert set(REPLICATED_TABLES).isdisjoint(LOOKUP_TABLES)
    for lookup in LOOKUP_TABLES:
        assert expected_table_row_count(lookup, 4, 3) == 4
    assert expected_table_row_count("title", 2_528_312, 3) == 2_528_312 * 3


def test_lookup_fk_values_never_shift():
    lookup_pairs = {(table, column) for table, column, _ in LOOKUP_FK_COLUMNS}
    keyed_pairs = set(KEY_COLUMNS)
    assert lookup_pairs.isdisjoint(keyed_pairs), f"lookup FKs must not shift: {lookup_pairs & keyed_pairs}"
    for table, _, _ in LOOKUP_FK_COLUMNS:
        assert table in REPLICATED_TABLES

    assert ("movie_companies", "company_type_id", "company_type") in LOOKUP_FK_COLUMNS


_PROJECT_DIR = Path(__file__).resolve().parents[4] / "_project"
_DECISION_RECORD = _PROJECT_DIR / "decisions" / "joinorder-track2-scaling-direction-2026-07-04.md"
_REFERENCE_CARDINALITIES = _PROJECT_DIR / "joinorder" / "reference_cardinalities.json"


@pytest.mark.skipif(not _DECISION_RECORD.exists(), reason="_project decision records are not in this checkout")
def test_prototype_stays_deferred_until_reapproval():
    import re
    from pathlib import Path

    assert REQUIRES_REAPPROVAL is True
    repo_root = Path(__file__).resolve().parents[4]
    decision = repo_root / "_project" / "decisions" / "joinorder-track2-scaling-direction-2026-07-04.md"
    assert "must not start unless a future decision re-approves an upward" in decision.read_text(encoding="utf-8")
    registry = repo_root / "benchbox" / "__init__.py"
    assert "joinorder_replicated" not in registry.read_text(encoding="utf-8")
    loader = (repo_root / "benchbox" / "core" / "benchmark_loader.py").read_text(encoding="utf-8")
    assert not re.search(r'"joinorder_replicated"|\'joinorder_replicated\'', loader)


def test_derived_identity_never_claims_canonical():
    manifest = ReplicatedManifest(scale_factor=2)
    body = manifest.to_dict()
    assert body["derived_identity"] == DERIVED_IDENTITY == "replicated_imdb"
    assert body["source_identity"] == SOURCE_IDENTITY == "canonical_imdb"
    assert body["derived_identity"] != "canonical_imdb"


def test_manifest_hash_stable():
    first = ReplicatedManifest(scale_factor=2, data_archive_hash="abc")
    second = ReplicatedManifest(scale_factor=2, data_archive_hash="abc")
    assert manifest_hash(first) == manifest_hash(second)
    other = ReplicatedManifest(scale_factor=3, data_archive_hash="abc")
    assert manifest_hash(first) != manifest_hash(other)


def test_manifest_provenance_matches_canonical_vocabulary():
    manifest = ReplicatedManifest(
        scale_factor=2,
        dataset_version="joinorder-imdb-2013-v1",
        data_archive_hash="abc",
        source_manifest_hash="def",
    )
    body = manifest.to_dict()
    assert body["dataset_version"] == "joinorder-imdb-2013-v1"
    assert body["data_archive_hash"] == "abc"
    assert body["source_manifest_hash"] == "def"
    assert "source_archive_hash" not in body


@pytest.mark.skipif(not _REFERENCE_CARDINALITIES.exists(), reason="_project joinorder oracle is not in this checkout")
def test_expected_cardinality_scaling_against_oracle():
    import json
    from pathlib import Path

    from benchbox.core.joinorder.queries import CANONICAL_JOINORDER_QUERIES

    oracle_path = Path(__file__).resolve().parents[4] / "_project" / "joinorder" / "reference_cardinalities.json"
    oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
    assert set(oracle["queries"]) == set(CANONICAL_JOINORDER_QUERIES)
    assert oracle["queries"]["1a"] == {
        "first_row_sha256": "a575fd5d3c9c9df1ad81c0b99120189032b6faf0ae7b071289908b71e39eba24",
        "row_count": 1,
        "underlying_row_count": 142,
    }
    assert oracle["queries"]["2a"]["underlying_row_count"] == 7834
    assert oracle["queries"]["2a"]["row_count"] == 1
    assert oracle["queries"]["6a"]["underlying_row_count"] == 6
    for query_id, sql in CANONICAL_JOINORDER_QUERIES.items():
        projection = sql.split("FROM", 1)[0]
        items = [item.strip() for item in projection.split("SELECT", 1)[1].split(",")]
        assert items, f"{query_id} has no projection items"
        assert all(item.upper().startswith("MIN(") for item in items), f"{query_id} projects a non-aggregate"
        assert oracle["queries"][query_id]["row_count"] == 1
    for scale in (2, 3):
        for query_id in ("1a", "2a", "6a", "10a", "18a"):
            count = oracle["queries"][query_id]["underlying_row_count"]
            assert expected_row_count(count, scale) == count * scale


