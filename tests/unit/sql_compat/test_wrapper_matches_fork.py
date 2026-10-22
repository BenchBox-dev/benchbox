from __future__ import annotations

import pytest

from tests.unit.sql_compat.golden_corpus import area_cases, outcome
from tests.unit.sql_compat.golden_corpus.area_wrapper import WrapperCase

pytestmark = [pytest.mark.unit, pytest.mark.fast]

CASES = [case for case in area_cases("area_wrapper") if isinstance(case, WrapperCase)]


def test_the_wrapper_corpus_covers_every_case():
    assert CASES
    assert len(CASES) == len(area_cases("area_wrapper"))


@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
def test_wrapper_output_equals_the_fork_output(case: WrapperCase):
    expected = outcome(case.fork)
    if expected.startswith("<") and expected.endswith(">"):
        pytest.skip("the fork reports this construct as unsupported")
    assert outcome(case.current) == expected
