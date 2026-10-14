from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "scripts/site_inputs_parent.py"

spec = importlib.util.spec_from_file_location("site_inputs_parent_test", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
parent_mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = parent_mod
spec.loader.exec_module(parent_mod)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

CORE = "c" * 40
PARENT = "b" * 40
OTHER = "d" * 40


def _ancestor_ok(parent: str, core: str) -> bool:
    return True


def _ancestor_no(parent: str, core: str) -> bool:
    return False


def test_dispatched_parent_beyond_window_resolves_from_direct_lookup() -> None:
    bundled = [f"{i:040x}" for i in range(10)]
    assert PARENT not in bundled
    parent_sha, source = parent_mod.decide_parent(CORE, PARENT, bundled, _ancestor_ok, lambda sha: "bundle")
    assert parent_sha == PARENT
    assert source == "bundle"


def test_dispatched_parent_with_expired_artifact_is_refused_naming_sha() -> None:
    bundled = [f"{i:040x}" for i in range(3)]
    with pytest.raises(RuntimeError, match=PARENT):
        parent_mod.decide_parent(CORE, PARENT, bundled, _ancestor_ok, lambda sha: "expired")


def test_dispatched_parent_that_is_not_ancestor_is_refused() -> None:
    with pytest.raises(RuntimeError, match="is not an ancestor"):
        parent_mod.decide_parent(CORE, PARENT, [OTHER], _ancestor_no, lambda sha: "bundle")


def test_dispatched_parent_equal_to_core_is_refused() -> None:
    with pytest.raises(RuntimeError, match="is not an ancestor"):
        parent_mod.decide_parent(CORE, CORE, [], _ancestor_ok, lambda sha: "bundle")


def test_empty_pool_accepts_dispatched_parent_as_dispatch() -> None:
    parent_sha, source = parent_mod.decide_parent(CORE, PARENT, [], _ancestor_ok, lambda sha: "missing")
    assert parent_sha == PARENT
    assert source == "dispatch"


def test_nonempty_pool_refuses_parent_without_bundle() -> None:
    with pytest.raises(RuntimeError, match=f"no site-inputs bundle attested on parent {PARENT}"):
        parent_mod.decide_parent(CORE, PARENT, [OTHER], _ancestor_ok, lambda sha: "missing")


def test_automatic_run_takes_newest_bundled_ancestor() -> None:
    first = "e" * 40
    second = "f" * 40
    third = "a" * 40

    def is_ancestor_fn(parent: str, core: str) -> bool:
        return parent in {second, third}

    parent_sha, source = parent_mod.decide_parent(
        CORE, None, [first, second, third], is_ancestor_fn, lambda sha: "missing"
    )
    assert parent_sha == second
    assert source == "bundle"


def test_automatic_run_skips_core_sha_candidate() -> None:
    def is_ancestor_fn(parent: str, core: str) -> bool:
        return True

    parent_sha, source = parent_mod.decide_parent(CORE, None, [CORE, PARENT], is_ancestor_fn, lambda sha: "missing")
    assert parent_sha == PARENT
    assert source == "bundle"


def test_automatic_run_without_ancestor_fails_with_recovery_instruction() -> None:
    with pytest.raises(RuntimeError, match="dispatch site-inputs.yml with parent_core_sha"):
        parent_mod.decide_parent(CORE, None, [OTHER], _ancestor_no, lambda sha: "missing")


def test_automatic_run_with_empty_pool_fails_with_recovery_instruction() -> None:
    with pytest.raises(RuntimeError, match="dispatch site-inputs.yml with parent_core_sha"):
        parent_mod.decide_parent(CORE, None, [], _ancestor_ok, lambda sha: "missing")


def test_resolve_commit_rejects_unknown_ref() -> None:
    with pytest.raises(RuntimeError, match="does not name a commit"):
        parent_mod.resolve_commit("no-such-ref-for-site-inputs-parent")
