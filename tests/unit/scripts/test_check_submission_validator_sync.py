from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_submission_validator_sync.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("_check_sync", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_module()

DEVELOP = """      - name: Validate bundles
        run: |
          xargs -d '\\n' uv run -- python scripts/validate_submission.py $REQUIRE_MANIFEST
      - name: Check corpus inventory
        run: uv run -- python scripts/generate_corpus_inventory.py --check
"""

PUBLISHED_IN_SYNC = """      - name: Validate bundles
        run: |
          xargs -d '\\n' uv run --no-project --python 3.11 -- python scripts/validate_submission.py $REQUIRE_MANIFEST
      - name: Check corpus inventory
        run: uv run --no-project --python 3.11 -- python scripts/generate_corpus_inventory.py --check
"""


def test_invocation_only_difference_is_in_sync() -> None:
    assert mod.diff(DEVELOP, PUBLISHED_IN_SYNC) == []


def test_identical_text_is_in_sync() -> None:
    assert mod.diff(DEVELOP, DEVELOP) == []


def test_substantive_drift_is_detected() -> None:
    drifted = PUBLISHED_IN_SYNC.replace("$REQUIRE_MANIFEST", "")
    result = mod.diff(DEVELOP, drifted)
    assert result, "substantive divergence must be reported as drift"
    assert any("REQUIRE_MANIFEST" in line for line in result)


def test_normalize_collapses_both_spellings_identically() -> None:
    slim = "run: uv run --no-project --python 3.11 -- python x.py"
    proj = "run: uv run -- python x.py"
    assert mod.normalize(slim) == mod.normalize(proj)


def test_main_returns_1_on_drift(tmp_path) -> None:
    dev = tmp_path / "develop.yml"
    pub = tmp_path / "published.yml"
    dev.write_text(DEVELOP, encoding="utf-8")
    pub.write_text(PUBLISHED_IN_SYNC.replace("$REQUIRE_MANIFEST", ""), encoding="utf-8")
    assert mod.main(["--develop", str(dev), "--published", str(pub)]) == 1


def test_main_returns_0_when_in_sync(tmp_path) -> None:
    dev = tmp_path / "develop.yml"
    pub = tmp_path / "published.yml"
    dev.write_text(DEVELOP, encoding="utf-8")
    pub.write_text(PUBLISHED_IN_SYNC, encoding="utf-8")
    assert mod.main(["--develop", str(dev), "--published", str(pub)]) == 0


def test_main_returns_2_on_missing_file(tmp_path) -> None:
    dev = tmp_path / "develop.yml"
    dev.write_text(DEVELOP, encoding="utf-8")
    missing = tmp_path / "nope.yml"
    assert mod.main(["--develop", str(dev), "--published", str(missing)]) == 2


