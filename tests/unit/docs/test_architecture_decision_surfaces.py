from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

ROOT = Path(__file__).resolve().parents[3]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_ssb_pilot_stop_is_reflected_in_future_state_surfaces() -> None:

    decision = _read("_project/decisions/arch-pilot-evaluation-2026-08-20.md")
    future_state = _read("docs/design/future-state/index.md")
    contract_index = _read("docs/design/future-state/contract-index.md")

    assert "| SSB family plugin (`#1737`) | **Stop** |" in decision
    assert "Pilot complete on SSB; further family" in future_state
    assert "family-migration cost table provides new evidence" in future_state
    assert "Pilot complete on SSB; further migration stopped pending new evidence" in contract_index
    assert "family-migration cost table provides new evidence" in contract_index

    assert "**Tier 2: Act when prerequisites are met:**" not in future_state
    assert (
        "Future-state proposal, Tier 2"
        not in contract_index.split("Define benchmark family plugin seam", maxsplit=1)[1].split("\n", maxsplit=1)[0]
    )


