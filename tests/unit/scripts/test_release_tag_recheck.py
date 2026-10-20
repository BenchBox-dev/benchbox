from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SPEC = importlib.util.spec_from_file_location(
    "release_tag_recheck", Path(__file__).resolve().parents[3] / "scripts" / "release_tag_recheck.py"
)
assert SPEC is not None and SPEC.loader is not None
recheck = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recheck)

TAG_OBJECT = "a" * 40
SHA = "b" * 40


def test_accepts_the_admitted_tag_object_and_commit():
    recheck.verify_tag_objects(TAG_OBJECT, SHA, TAG_OBJECT, SHA)


def test_refuses_a_reannotated_tag_on_the_same_commit():
    with pytest.raises(ValueError, match="tag object differs"):
        recheck.verify_tag_objects("c" * 40, SHA, TAG_OBJECT, SHA)


def test_refuses_a_tag_moved_to_another_commit():
    with pytest.raises(ValueError, match="tag commit differs"):
        recheck.verify_tag_objects(TAG_OBJECT, "d" * 40, TAG_OBJECT, SHA)


def test_refuses_malformed_objects():
    with pytest.raises(ValueError, match="not a SHA"):
        recheck.verify_tag_objects("not-a-sha", SHA, TAG_OBJECT, SHA)
