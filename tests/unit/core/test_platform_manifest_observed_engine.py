from __future__ import annotations

import pytest

from benchbox.core.platform_manifest import _validate_observed_engine

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ENGINES = {"a": {}}
EVIDENCE = {"reason": "the library exposes no engine", "evidence": "API reference, no such field"}


@pytest.mark.parametrize(
    "declared",
    [None, {"adapter": "pkg.module:Adapter"}, {"not_captured": EVIDENCE}],
)
def test_valid_declarations_are_accepted(declared):
    _validate_observed_engine("synthetic", declared, ENGINES)


@pytest.mark.parametrize(
    "declared",
    [
        {},
        {"adapter": "pkg.module:Adapter", "not_captured": EVIDENCE},
        {"adapter": "not-a-path"},
        {"adapter": 3},
        {"not_captured": {"reason": "no evidence"}},
        {"not_captured": {"reason": "", "evidence": "doc"}},
        {"not_captured": "reason"},
        {"other": "x"},
    ],
)
def test_malformed_declarations_are_rejected(declared):
    with pytest.raises(ValueError, match="observed_engine"):
        _validate_observed_engine("synthetic", declared, ENGINES)


def test_declaration_without_declared_engines_is_rejected():
    with pytest.raises(ValueError, match="no execution_engines"):
        _validate_observed_engine("synthetic", {"adapter": "pkg.module:Adapter"}, {})
