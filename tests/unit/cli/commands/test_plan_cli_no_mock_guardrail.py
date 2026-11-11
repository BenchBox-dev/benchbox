from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

_COMMANDS_DIR = Path(__file__).resolve().parent

_GUARDED_SUITES = {
    "test_show_plan_coverage.py": "load_result_file",
    "test_compare_plans_coverage.py": "load_result_file",
    "test_plan_history_coverage.py": "PlanHistory",
}


def _stubs_symbol(text: str, symbol: str) -> bool:
    return f'"{symbol}"' in text and "monkeypatch.setattr" in text


_REAL_PATH_TEST_RE = re.compile(r"^def (test_\w*_real_\w*)\(", re.MULTILINE)


def _has_real_path_test(text: str) -> bool:
    return bool(_REAL_PATH_TEST_RE.search(text))


@pytest.mark.parametrize(("filename", "stubbed_symbol"), sorted(_GUARDED_SUITES.items()))
def test_plan_cli_suite_that_stubs_loader_has_real_path_test(filename: str, stubbed_symbol: str) -> None:
    suite_path = _COMMANDS_DIR / filename
    assert suite_path.exists(), f"guarded plan CLI suite missing: {filename}"
    text = suite_path.read_text(encoding="utf-8")

    if not _stubs_symbol(text, stubbed_symbol):
        return

    assert _has_real_path_test(text), (
        f"{filename} monkeypatches {stubbed_symbol!r} for fast coverage but has no "
        f"real-path (no-monkeypatch) companion test. Add one that drives the CLI "
        f"through the real loader/store on a tmp-file bundle so fake-shape drift "
        f"(e.g. a fabricated '.phases' attribute) is actually caught. See qpc-11 / F8.1."
    )


