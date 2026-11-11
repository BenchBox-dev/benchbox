from __future__ import annotations

import pytest

from benchbox.core.transaction_primitives.catalog.loader import load_transaction_primitives_catalog

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestCatalogLoads:
    def test_catalog_version_and_operations(self):
        catalog = load_transaction_primitives_catalog()
        assert catalog.version >= 1
        assert len(catalog.operations) >= 10
        assert "transaction_commit_small" in catalog.operations
        assert "transaction_rollback_small" in catalog.operations

    def test_known_categories_present(self):
        catalog = load_transaction_primitives_catalog()
        categories = {op.category for op in catalog.operations.values()}
        assert {"overhead", "isolation", "savepoint"} <= categories

    def test_every_operation_has_validation_queries(self):
        catalog = load_transaction_primitives_catalog()
        for op_id, op in catalog.operations.items():
            assert op.write_sql.strip(), f"{op_id} has empty write_sql"
            assert op.validation_queries, f"{op_id} has no validation queries"
            for query in op.validation_queries:
                assert query.sql.strip(), f"{op_id}/{query.id} has empty sql"


