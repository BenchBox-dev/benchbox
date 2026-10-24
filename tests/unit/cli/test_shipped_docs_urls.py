from __future__ import annotations

import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from benchbox.cli.main import cli

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCHBOX_ROOT = REPO_ROOT / "benchbox"

_ALLOWED_HOSTS = frozenset(
    {
        "benchbox.dev",
        "api.benchbox.dev",
        "staging.benchbox.dev",
    }
)
_DEAD_HOST = "docs.benchbox.dev"
_HOST_RE = re.compile(r"\b(?:[A-Za-z0-9-]+\.)*benchbox\.dev\b", re.IGNORECASE)
_SCAN_SUFFIXES = {".py", ".md", ".txt", ".rst", ".json", ".toml", ".yaml", ".yml"}


def _shipped_text_files() -> list[Path]:
    return sorted(path for path in BENCHBOX_ROOT.rglob("*") if path.is_file() and path.suffix in _SCAN_SUFFIXES)


def test_shipped_package_does_not_cite_dead_docs_host() -> None:
    hits: list[str] = []
    for path in _shipped_text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        if _DEAD_HOST in text.lower():
            rel = path.relative_to(REPO_ROOT)
            hits.append(str(rel))
    assert hits == [], (
        f"shipped files cite dead hostname {_DEAD_HOST!r} (NXDOMAIN; use https://benchbox.dev/docs/): {hits}"
    )


def test_shipped_package_benchbox_hosts_are_allowlisted() -> None:
    unknown: list[str] = []
    for path in _shipped_text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(REPO_ROOT)
        for match in _HOST_RE.finditer(text):
            host = match.group(0).lower()
            if host not in _ALLOWED_HOSTS:
                unknown.append(f"{rel}: {host}")
    assert unknown == [], f"shipped files cite unallowlisted benchbox.dev hosts: {unknown}"


def test_top_level_help_uses_live_docs_url_and_long_run_flags() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "https://benchbox.dev/docs/" in result.output
    assert _DEAD_HOST not in result.output
    assert "run --platform duckdb --benchmark tpch" in result.output
    assert "run -p duckdb" not in result.output
