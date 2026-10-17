import json
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).parents[3]


def test_browser_and_nightly_lanes_run_the_high_severity_audit() -> None:
    browser = (ROOT / ".github/workflows/site-docs.yml").read_text(encoding="utf-8")
    nightly = (ROOT / ".github/workflows/nightly.yml").read_text(encoding="utf-8")

    assert browser.count("npm run audit:high") >= 1
    assert nightly.count("npm run audit:high") >= 1


def test_audit_script_is_not_force_fix_or_unbounded() -> None:
    package_text = (ROOT / "results-explorer/package.json").read_text(encoding="utf-8")
    wrapper_text = (ROOT / "results-explorer/scripts/audit-high.mjs").read_text(encoding="utf-8")

    assert '"audit:high": "node scripts/audit-high.mjs"' in package_text
    assert '"npm", ["audit", "--json", "--audit-level=high"]' in wrapper_text
    assert "audit fix --force" not in package_text
    assert "audit fix --force" not in wrapper_text


def test_audit_allowlist_entries_are_documented_and_expire() -> None:
    allowlist = json.loads((ROOT / "results-explorer/scripts/audit-high-allowlist.json").read_text(encoding="utf-8"))

    for entry in allowlist:
        assert set(entry) == {"id", "package", "reason", "review_by", "link"}
        assert re.fullmatch(r"GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}", entry["id"])
        assert entry["id"] in entry["link"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["review_by"])
