from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from scripts.check_sqlglot_upstream_freshness import check, evaluate, main

pytestmark = [pytest.mark.unit, pytest.mark.fast]

PINNED = "a" * 40
NEWER = "b" * 40
NEWEST = "c" * 40
TODAY = date(2026, 10, 18)
PIN = {"upstream_sha": PINNED, "repository": "https://example.test/fork"}


def waiver(tag: str, approved: str = "2026-10-10", expires: str = "2026-11-05", reason: str = "refresh scheduled"):
    return {"tag": tag, "reason": reason, "approved": approved, "expires": expires}


def test_pin_on_newest_tag_passes() -> None:
    assert evaluate(PIN, {"v30.9.0": "0" * 40, "v30.23.0": PINNED}, [], TODAY) == []


def test_newer_final_tag_fails_naming_both_tags() -> None:
    tags = {"v30.23.0": PINNED, "v30.24.0": NEWER, "v30.25.0": NEWEST}
    (problem,) = evaluate(PIN, tags, [], TODAY)
    assert "v30.23.0" in problem and "v30.25.0" in problem and "waiver" in problem


def test_tags_compare_numerically_not_lexically() -> None:
    tags = {"v30.9.0": PINNED, "v30.10.0": NEWER}
    assert evaluate(PIN, tags, [], TODAY)


@pytest.mark.parametrize(
    ("waivers", "passes"),
    [
        ([waiver("v30.24.0")], True),
        ([waiver("v30.24.0", expires="2026-10-18")], True),
        ([waiver("v30.24.0", approved="2026-10-01", expires="2026-10-17")], False),
        ([waiver("v30.23.5")], False),
        ([waiver("v30.24.0", approved="2026-10-01", expires="2026-12-01")], False),
        ([waiver("v30.24.0", reason="  ")], False),
        ([{"tag": "v30.24.0"}], False),
        ("v30.24.0", False),
    ],
)
def test_waiver_applies_only_to_the_latest_tag_within_its_window(waivers: object, passes: bool) -> None:
    problems = evaluate(PIN, {"v30.23.0": PINNED, "v30.24.0": NEWER}, waivers, TODAY)
    assert (problems == []) is passes


def test_waiver_for_superseded_tag_does_not_cover_a_newer_release() -> None:
    tags = {"v30.23.0": PINNED, "v30.24.0": NEWER, "v30.25.0": NEWEST}
    assert evaluate(PIN, tags, [waiver("v30.24.0")], TODAY)


@pytest.mark.parametrize("pin", [{}, {"upstream_sha": "short"}, {"upstream_sha": "d" * 40}])
def test_pin_that_is_not_an_upstream_release_commit_fails(pin: dict[str, str]) -> None:
    assert evaluate(pin, {"v30.23.0": PINNED}, [], TODAY)


def write_root(root: Path, waivers: list[dict[str, str]]) -> None:
    (root / "benchbox/_vendor").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "benchbox/_vendor/sqlglot.json").write_text(json.dumps(PIN))
    (root / "scripts/sqlglot_refresh_waiver.json").write_text(json.dumps({"schema": 1, "waivers": waivers}))


def test_unreachable_upstream_fails_closed(tmp_path: Path) -> None:
    write_root(tmp_path, [])

    def unreachable() -> dict[str, str]:
        raise RuntimeError("cannot list upstream tags: offline")

    assert check(tmp_path, unreachable, TODAY) == ["cannot list upstream tags: offline"]


def test_check_reads_pin_and_waiver_files(tmp_path: Path) -> None:
    write_root(tmp_path, [waiver("v30.24.0")])
    assert check(tmp_path, lambda: {"v30.23.0": PINNED, "v30.24.0": NEWER}, TODAY) == []


def test_main_exit_status_reflects_the_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_root(tmp_path, [])
    monkeypatch.setattr(
        "scripts.check_sqlglot_upstream_freshness.list_upstream_tags",
        lambda: {"v30.23.0": PINNED, "v30.24.0": NEWER},
    )
    assert main(["--root", str(tmp_path)]) == 1
    monkeypatch.setattr("scripts.check_sqlglot_upstream_freshness.list_upstream_tags", lambda: {"v30.23.0": PINNED})
    assert main(["--root", str(tmp_path)]) == 0
