from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).parents[3]
WEBSITE = ROOT / "website"


def _package() -> dict[str, Any]:
    return json.loads((WEBSITE / "package.json").read_text(encoding="utf-8"))


def _jobs(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))["jobs"]


def test_ci_and_nightly_run_the_website_high_severity_audit() -> None:
    ci_steps = _jobs("site-docs.yml")["site-build"]["steps"]
    nightly_steps = _jobs("nightly.yml")["website-audit"]["steps"]
    makefile = (ROOT / "make/documentation.mk").read_text(encoding="utf-8")

    assert any(step.get("run") == "make site-check site-build" for step in ci_steps)
    site_check = makefile.split("site-check:", 1)[1].split("\n\n", 1)[0]
    assert "npm --prefix website run audit:high" in site_check
    audit = [step for step in nightly_steps if step.get("run") == "npm run audit:high"]
    assert [step.get("working-directory") for step in audit] == ["website"]


def test_audit_script_reuses_the_explorer_gate_against_the_website_package() -> None:
    package = _package()
    wrapper = (WEBSITE / "scripts/audit-high.mjs").read_text(encoding="utf-8")

    assert package["scripts"]["audit:high"] == "node scripts/audit-high.mjs"
    assert '"npm", ["audit", "--json", "--audit-level=high"]' in wrapper
    assert 'from "../../results-explorer/scripts/audit-high.mjs"' in wrapper
    assert "audit fix --force" not in wrapper


def test_audit_allowlist_entries_are_documented_and_expire() -> None:
    allowlist = json.loads((WEBSITE / "scripts/audit-high-allowlist.json").read_text(encoding="utf-8"))

    assert allowlist == []
    for entry in allowlist:
        assert set(entry) == {"id", "package", "reason", "review_by", "link"}
        assert re.fullmatch(r"GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}", entry["id"])
        assert entry["id"] in entry["link"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["review_by"])


def test_website_package_is_pinned_exactly_on_node_22() -> None:
    package = _package()
    pinned = {**package["dependencies"], **package["devDependencies"]}

    assert pinned
    assert all(re.fullmatch(r"\d+\.\d+\.\d+", version) for version in pinned.values())
    assert package["engines"]["node"] == ">=22.12.0"
    assert (WEBSITE / "package-lock.json").is_file()
    assert not (ROOT / "package.json").exists()
    assert {"@astrojs/check", "typescript"} <= set(package["devDependencies"])
    assert package["scripts"]["check"] == "BENCHBOX_ALLOW_EMPTY_SIDEBAR=1 astro check"


def test_only_the_website_jobs_use_node_22() -> None:
    website_jobs = {"site-build", "site-parity", "website-audit"}
    for workflow in ("ci.yml", "site-docs.yml", "nightly.yml"):
        for name, job in _jobs(workflow).items():
            versions = {
                str(step["with"]["node-version"])
                for step in job.get("steps", [])
                if "setup-node" in step.get("uses", "")
            }
            if name in website_jobs:
                assert versions == {"22"}, (workflow, name)
            else:
                assert "22" not in versions, (workflow, name)


def test_ci_tests_the_built_website_and_the_not_found_fallback_after_the_build() -> None:
    steps = _jobs("site-docs.yml")["site-build"]["steps"]
    runs = [step.get("run") for step in steps]
    makefile = (ROOT / "make/documentation.mk").read_text(encoding="utf-8")

    assert runs.index("make site-test-built") > runs.index("make site-check site-build")
    assert any(
        run == "npx playwright install --with-deps chromium" for run in runs[: runs.index("make site-test-built")]
    )
    target = makefile.split("site-test-built:")[1].split("\n\n")[0]
    assert "npm --prefix website test" in target
    assert "npm --prefix website run verify:not-found" in target
    assert "npm --prefix website run verify:landing" in target
    assert "BENCHBOX_SITE_UNBUILT=1" in makefile.split("site-check:")[1].split("\n\n")[0]
