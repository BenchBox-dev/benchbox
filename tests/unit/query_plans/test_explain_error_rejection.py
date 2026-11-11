import pytest

from benchbox.core.query_plans.parsers.databend import DatabendQueryPlanParser
from benchbox.core.query_plans.parsers.doris import DorisQueryPlanParser
from benchbox.core.query_plans.parsers.questdb import QuestDBQueryPlanParser
from benchbox.core.query_plans.parsers.singlestore import SingleStoreQueryPlanParser
from benchbox.core.query_plans.parsers.spark import SparkQueryPlanParser

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture(
    params=[
        DatabendQueryPlanParser(),
        DorisQueryPlanParser(),
        QuestDBQueryPlanParser(),
        SingleStoreQueryPlanParser(),
        SparkQueryPlanParser(),
    ],
    ids=["databend", "doris", "questdb", "singlestore", "spark"],
)
def parser(request):
    return request.param


class TestExplainFailureHandling:
    def test_failed_prefix_returns_none(self, parser):
        assert parser.parse_explain_output("q", "Failed to get query plan: connection reset") is None

    def test_retired_prefix_returns_none(self, parser):
        assert parser.parse_explain_output("q", "Could not get query plan: boom") is None

    def test_none_returns_none(self, parser):
        assert parser.parse_explain_output("q", None) is None

    def test_empty_returns_none(self, parser):
        assert parser.parse_explain_output("q", "") is None
        assert parser.parse_explain_output("q", "   \n  ") is None


