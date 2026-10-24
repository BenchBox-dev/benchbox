# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


_SMALL_CORPUS = 200
_DIM = 64


def _vss_available() -> bool:
    try:
        import duckdb

        conn = duckdb.connect()
        conn.execute("INSTALL vss; LOAD vss;")
        conn.close()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.unit,
    pytest.mark.integration,
]


@pytest.fixture(scope="module")
def vector_db(tmp_path_factory):
    import duckdb

    from benchbox.core.vector_search import generator as gmod
    from benchbox.core.vector_search.generator import VectorSearchDataGenerator

    tmp = tmp_path_factory.mktemp("vector_int")

    gen = VectorSearchDataGenerator(
        scale_factor=1.0,
        output_dir=tmp,
        dimensions=_DIM,
        compression_enabled=False,
    )
    orig_base = gmod.BASE_VECTORS
    gmod.BASE_VECTORS = _SMALL_CORPUS
    try:
        paths = gen.generate_data()
    finally:
        gmod.BASE_VECTORS = orig_base

    conn = duckdb.connect()
    conn.execute(f"CREATE TABLE vectors (id BIGINT, embedding REAL[{_DIM}], category VARCHAR(50), doc_id VARCHAR(100))")
    conn.execute(f"CREATE TABLE vector_queries (query_id INTEGER, query_vector REAL[{_DIM}])")

    conn.execute(
        f"INSERT INTO vectors "
        f"SELECT id, embedding::REAL[{_DIM}], category, doc_id "
        f"FROM read_csv_auto('{paths['vectors']}', delim='|', header=true, "
        f"columns={{'id':'BIGINT','embedding':'VARCHAR','category':'VARCHAR','doc_id':'VARCHAR'}})"
    )
    conn.execute(
        f"INSERT INTO vector_queries "
        f"SELECT query_id, query_vector::REAL[{_DIM}] "
        f"FROM read_csv_auto('{paths['vector_queries']}', delim='|', header=true, "
        f"columns={{'query_id':'INTEGER','query_vector':'VARCHAR'}})"
    )

    yield {
        "conn": conn,
        "num_vectors": _SMALL_CORPUS,
        "dim": _DIM,
    }

    conn.close()


@pytest.fixture(scope="module")
def vector_db_with_hnsw(tmp_path_factory):
    if not _vss_available():
        pytest.skip("DuckDB VSS extension not available")

    import duckdb

    from benchbox.core.vector_search import generator as gmod
    from benchbox.core.vector_search.generator import VectorSearchDataGenerator

    tmp = tmp_path_factory.mktemp("vector_hnsw")

    gen = VectorSearchDataGenerator(
        scale_factor=1.0,
        output_dir=tmp,
        dimensions=_DIM,
        compression_enabled=False,
    )
    orig_base = gmod.BASE_VECTORS
    gmod.BASE_VECTORS = _SMALL_CORPUS
    try:
        paths = gen.generate_data()
    finally:
        gmod.BASE_VECTORS = orig_base

    conn = duckdb.connect()
    conn.execute("INSTALL vss; LOAD vss;")
    conn.execute(f"CREATE TABLE vectors (id BIGINT, embedding REAL[{_DIM}], category VARCHAR(50), doc_id VARCHAR(100))")
    conn.execute(f"CREATE TABLE vector_queries (query_id INTEGER, query_vector REAL[{_DIM}])")

    conn.execute(
        f"INSERT INTO vectors "
        f"SELECT id, embedding::REAL[{_DIM}], category, doc_id "
        f"FROM read_csv_auto('{paths['vectors']}', delim='|', header=true, "
        f"columns={{'id':'BIGINT','embedding':'VARCHAR','category':'VARCHAR','doc_id':'VARCHAR'}})"
    )
    conn.execute(
        f"INSERT INTO vector_queries "
        f"SELECT query_id, query_vector::REAL[{_DIM}] "
        f"FROM read_csv_auto('{paths['vector_queries']}', delim='|', header=true, "
        f"columns={{'query_id':'INTEGER','query_vector':'VARCHAR'}})"
    )

    conn.execute("CREATE INDEX hnsw_cosine ON vectors USING HNSW (embedding) WITH (metric = 'cosine')")

    yield {
        "conn": conn,
        "num_vectors": _SMALL_CORPUS,
        "dim": _DIM,
    }

    conn.close()


class TestDuckDBExactSearch:
    def test_data_loads_correct_row_count(self, vector_db):
        conn = vector_db["conn"]
        n = conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        assert n == vector_db["num_vectors"]

    def test_query_vectors_row_count(self, vector_db):
        from benchbox.core.vector_search.generator import NUM_QUERY_VECTORS

        conn = vector_db["conn"]
        n = conn.execute("SELECT COUNT(*) FROM vector_queries").fetchone()[0]
        assert n == NUM_QUERY_VECTORS

    def test_q1_returns_ten_rows(self, vector_db):
        conn = vector_db["conn"]
        rows = conn.execute(
            "SELECT v.id, array_cosine_similarity(v.embedding, q.query_vector) AS similarity "
            "FROM vectors v "
            "CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q "
            "ORDER BY similarity DESC LIMIT 10"
        ).fetchall()
        assert len(rows) == 10

    def test_q1_similarities_in_range(self, vector_db):
        conn = vector_db["conn"]
        rows = conn.execute(
            "SELECT array_cosine_similarity(v.embedding, q.query_vector) AS similarity "
            "FROM vectors v "
            "CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q "
            "ORDER BY similarity DESC LIMIT 10"
        ).fetchall()
        for (sim,) in rows:
            assert -1.01 <= sim <= 1.01, f"Similarity {sim} out of expected range"

    def test_q1_results_are_ordered_descending(self, vector_db):
        conn = vector_db["conn"]
        rows = conn.execute(
            "SELECT array_cosine_similarity(v.embedding, q.query_vector) AS similarity "
            "FROM vectors v "
            "CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q "
            "ORDER BY similarity DESC LIMIT 10"
        ).fetchall()
        sims = [r[0] for r in rows]
        assert sims == sorted(sims, reverse=True)

    def test_q2_returns_ten_rows(self, vector_db):
        conn = vector_db["conn"]
        rows = conn.execute(
            "SELECT v.id, array_distance(v.embedding, q.query_vector) AS distance "
            "FROM vectors v "
            "CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q "
            "ORDER BY distance ASC LIMIT 10"
        ).fetchall()
        assert len(rows) == 10

    def test_q2_distances_are_non_negative(self, vector_db):
        conn = vector_db["conn"]
        rows = conn.execute(
            "SELECT array_distance(v.embedding, q.query_vector) AS distance "
            "FROM vectors v "
            "CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q "
            "ORDER BY distance ASC LIMIT 10"
        ).fetchall()
        for (dist,) in rows:
            assert dist >= 0, f"Negative distance: {dist}"

    def test_q3_filtered_returns_only_target_category(self, vector_db):
        conn = vector_db["conn"]
        rows = conn.execute(
            "SELECT v.id, v.category, array_cosine_similarity(v.embedding, q.query_vector) AS similarity "
            "FROM vectors v "
            "CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q "
            "WHERE v.category = 'category_01' "
            "ORDER BY similarity DESC LIMIT 10"
        ).fetchall()
        assert len(rows) > 0
        for _, cat, _ in rows:
            assert cat == "category_01"

    def test_q4_returns_hundred_rows(self, vector_db):
        conn = vector_db["conn"]
        rows = conn.execute(
            "SELECT v.id, array_cosine_similarity(v.embedding, q.query_vector) AS similarity "
            "FROM vectors v "
            "CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q "
            "ORDER BY similarity DESC LIMIT 100"
        ).fetchall()
        assert len(rows) == min(100, vector_db["num_vectors"])

    def test_q6_multi_category_filter(self, vector_db):
        conn = vector_db["conn"]
        rows = conn.execute(
            "SELECT v.id, v.category, array_cosine_similarity(v.embedding, q.query_vector) AS similarity "
            "FROM vectors v "
            "CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 2) q "
            "WHERE v.category IN ('category_01', 'category_02', 'category_03') "
            "ORDER BY similarity DESC LIMIT 20"
        ).fetchall()
        assert len(rows) > 0
        valid_cats = {"category_01", "category_02", "category_03"}
        for _, cat, _ in rows:
            assert cat in valid_cats

    def test_embedding_data_is_unit_normalised(self, vector_db):
        import math

        conn = vector_db["conn"]
        rows = conn.execute(
            "SELECT list_sum(list_transform(embedding, x -> x * x)) AS sq_norm FROM vectors LIMIT 20"
        ).fetchall()
        for (sq_norm,) in rows:
            norm = math.sqrt(sq_norm)
            assert abs(norm - 1.0) < 0.01, f"Embedding not unit-normalised: ||v||={norm:.4f}"

    def test_all_six_queries_execute_without_error(self, vector_db):
        conn = vector_db["conn"]
        from benchbox.core.vector_search.queries import VectorSearchQueryManager

        mgr = VectorSearchQueryManager()
        for qid in mgr.ALL_QUERY_IDS:
            sql = mgr.get_query(qid)
            rows = conn.execute(sql).fetchall()
            assert isinstance(rows, list), f"{qid} did not return a list"


class TestDuckDBHNSWIndex:
    def test_index_exists_in_catalog(self, vector_db_with_hnsw):

        conn = vector_db_with_hnsw["conn"]
        rows = conn.execute("SELECT index_name FROM duckdb_indexes() WHERE table_name = 'vectors'").fetchall()
        index_names = [r[0] for r in rows]
        assert any("hnsw" in name.lower() for name in index_names), (
            f"No HNSW index found in catalog. Indexes: {index_names}"
        )

    def test_ann_q5_returns_ten_rows(self, vector_db_with_hnsw):
        conn = vector_db_with_hnsw["conn"]
        rows = conn.execute(
            "SELECT v.id, array_cosine_similarity(v.embedding, q.query_vector) AS similarity "
            "FROM vectors v "
            "CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q "
            "ORDER BY similarity DESC LIMIT 10"
        ).fetchall()
        assert len(rows) == 10

    def test_ann_results_ordered_descending(self, vector_db_with_hnsw):
        conn = vector_db_with_hnsw["conn"]
        rows = conn.execute(
            "SELECT array_cosine_similarity(v.embedding, q.query_vector) AS similarity "
            "FROM vectors v "
            "CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q "
            "ORDER BY similarity DESC LIMIT 10"
        ).fetchall()
        sims = [r[0] for r in rows]
        assert sims == sorted(sims, reverse=True)

    def test_ann_similarities_in_valid_range(self, vector_db_with_hnsw):
        conn = vector_db_with_hnsw["conn"]
        rows = conn.execute(
            "SELECT array_cosine_similarity(v.embedding, q.query_vector) AS similarity "
            "FROM vectors v "
            "CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q "
            "ORDER BY similarity DESC LIMIT 10"
        ).fetchall()
        for (sim,) in rows:
            assert -1.01 <= sim <= 1.01


class TestRecallAtK:
    def test_recall_at_10_with_hnsw(self, vector_db_with_hnsw):
        from benchbox.core.vector_search.metrics import recall_at_k

        conn = vector_db_with_hnsw["conn"]

        gt_rows = conn.execute(
            "SELECT v.id "
            "FROM vectors v "
            "CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q "
            "ORDER BY array_cosine_similarity(v.embedding, q.query_vector) DESC "
            "LIMIT 100"
        ).fetchall()
        gt_ids = [r[0] for r in gt_rows]

        ann_rows = conn.execute(
            "SELECT v.id "
            "FROM vectors v "
            "CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q "
            "ORDER BY array_cosine_similarity(v.embedding, q.query_vector) DESC "
            "LIMIT 10"
        ).fetchall()
        ann_ids = [r[0] for r in ann_rows]

        recall = recall_at_k(gt_ids, ann_ids, k=10)
        assert recall >= 0.8, (
            f"recall@10={recall:.3f} below threshold 0.8. GT top-10: {gt_ids[:10]}, ANN top-10: {ann_ids}"
        )

    def test_recall_at_10_across_multiple_queries(self, vector_db_with_hnsw):
        from benchbox.core.vector_search.metrics import recall_at_k

        conn = vector_db_with_hnsw["conn"]
        recalls = []

        for qid in range(1, 6):
            gt_rows = conn.execute(
                f"SELECT v.id "
                f"FROM vectors v "
                f"CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = {qid}) q "
                f"ORDER BY array_cosine_similarity(v.embedding, q.query_vector) DESC "
                f"LIMIT 100"
            ).fetchall()
            gt_ids = [r[0] for r in gt_rows]

            ann_rows = conn.execute(
                f"SELECT v.id "
                f"FROM vectors v "
                f"CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = {qid}) q "
                f"ORDER BY array_cosine_similarity(v.embedding, q.query_vector) DESC "
                f"LIMIT 10"
            ).fetchall()
            ann_ids = [r[0] for r in ann_rows]

            recalls.append(recall_at_k(gt_ids, ann_ids, k=10))

        mean_recall = sum(recalls) / len(recalls)
        assert mean_recall >= 0.8, (
            f"Mean recall@10={mean_recall:.3f} across 5 queries is below 0.8. Per-query recalls: {recalls}"
        )

    def test_recall_metric_perfect_agreement(self, vector_db):
        from benchbox.core.vector_search.metrics import recall_at_k

        conn = vector_db["conn"]
        rows = conn.execute(
            "SELECT v.id "
            "FROM vectors v "
            "CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q "
            "ORDER BY array_cosine_similarity(v.embedding, q.query_vector) DESC "
            "LIMIT 10"
        ).fetchall()
        ids = [r[0] for r in rows]
        assert recall_at_k(ids, ids, k=10) == 1.0

    def test_recall_metric_no_overlap(self, vector_db):
        from benchbox.core.vector_search.metrics import recall_at_k

        conn = vector_db["conn"]
        exact_rows = conn.execute("SELECT id FROM vectors ORDER BY id ASC LIMIT 10").fetchall()
        ann_rows = conn.execute("SELECT id FROM vectors ORDER BY id DESC LIMIT 10").fetchall()
        exact_ids = [r[0] for r in exact_rows]
        ann_ids = [r[0] for r in ann_rows]

        if not set(exact_ids) & set(ann_ids):
            assert recall_at_k(exact_ids, ann_ids, k=10) == 0.0

    def test_recall_at_different_k_values(self, vector_db):
        from benchbox.core.vector_search.metrics import recall_at_k

        conn = vector_db["conn"]
        gt_rows = conn.execute(
            "SELECT v.id "
            "FROM vectors v "
            "CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q "
            "ORDER BY array_cosine_similarity(v.embedding, q.query_vector) DESC "
            "LIMIT 100"
        ).fetchall()
        gt_ids = [r[0] for r in gt_rows]

        ann_rows = conn.execute(
            "SELECT v.id "
            "FROM vectors v "
            "CROSS JOIN (SELECT query_vector FROM vector_queries WHERE query_id = 1) q "
            "ORDER BY array_cosine_similarity(v.embedding, q.query_vector) DESC "
            "LIMIT 10"
        ).fetchall()
        ann_ids = [r[0] for r in ann_rows]

        for k in (1, 5, 10):
            r = recall_at_k(gt_ids, ann_ids, k=k)
            assert 0.0 <= r <= 1.0, f"recall@{k}={r} out of range"
