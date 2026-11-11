from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from benchbox.core.tuning.applied_ledger import SATISFIED_BY_PREEXISTING_STATE, AppliedTuningLedger
from benchbox.core.tuning.interface import TuningType

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture(autouse=True)
def mock_sf_deps():
    with patch("benchbox.platforms.snowflake.check_platform_dependencies", return_value=(True, [])):
        yield


def _make_adapter():
    from benchbox.platforms.snowflake import SnowflakeAdapter

    with patch("benchbox.platforms.snowflake.snowflake"):
        return SnowflakeAdapter(
            account="test_account",
            username="test_user",
            password="test_pass",
            schema="PUBLIC",
            database="BENCHBOX",
        )


def _clustering_tuning(column="l_orderkey"):
    tuning = Mock()
    tuning.table_name = "lineitem"
    tuning.has_any_tuning.return_value = True
    tuning.get_columns_by_type.side_effect = lambda tuning_type: (
        [SimpleNamespace(name=column, order=0)] if tuning_type == TuningType.CLUSTERING else []
    )
    return tuning


def test_already_present_clustering_key_is_recorded_satisfied_not_dropped():
    adapter = _make_adapter()
    ledger = AppliedTuningLedger()
    adapter._applied_tuning_ledger = ledger
    adapter.resolve_physical_table = Mock(return_value="LINEITEM")

    cursor = Mock()
    cursor.fetchone.return_value = ("(L_ORDERKEY)", True)
    connection = Mock()
    connection.cursor.return_value = cursor

    adapter.apply_table_tunings(_clustering_tuning(), connection)

    assert ledger.dropped == []
    assert len(ledger.satisfied) == 1
    satisfied = ledger.satisfied[0]
    assert "CLUSTER BY" in satisfied.intent
    assert satisfied.satisfied_by == SATISFIED_BY_PREEXISTING_STATE
    assert "already present in Snowflake catalog" in satisfied.reason


