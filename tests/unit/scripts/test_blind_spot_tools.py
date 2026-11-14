from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_script(name: str):
    path = REPO_ROOT / "_project" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_blind_spot = _load_script("validate_blind_spot")
sweep_blind_spots = _load_script("sweep_blind_spots")


def test_blind_spot_command_uses_active_protocol() -> None:
    command_text = (REPO_ROOT / ".claude" / "commands" / "blind-spot.md").read_text(encoding="utf-8")

    assert "docs/agent/review-protocol.md" in command_text
    assert "docs/development/review-protocol.md" not in command_text
    assert "docs/development/agent-review-protocol.md" not in command_text
    assert "_project/blind-spots/README.md" in command_text
    assert "~/.tractable/finding-drafts/benchbox" in command_text
    assert "~/.benchbox/finding-drafts" not in command_text
    assert ".claude/skills" not in command_text
    assert (REPO_ROOT / "docs" / "agent" / "review-protocol.md").is_file()


def test_superseded_protocol_doc_is_demoted() -> None:
    legacy = REPO_ROOT / "docs" / "agent" / "review-protocol-legacy.md"
    if not legacy.is_file():
        pytest.skip("legacy review-protocol.md fully retired")
    text = legacy.read_text(encoding="utf-8")
    head = "\n".join(text.splitlines()[:10]).casefold()
    assert "non-authoritative" in head
    assert "this file wins" not in text.casefold()
    assert "canonical, unabridged" not in text.casefold()


def test_blind_spot_command_forbids_direct_db_write() -> None:
    command_text = (REPO_ROOT / ".claude" / "commands" / "blind-spot.md").read_text(encoding="utf-8")

    assert "do not commit" in command_text
    assert "todo finding sync" not in command_text
    assert "separate, user-authorized step" in command_text
    assert "local-only" in command_text


def _finding_text(
    stem: str,
    *,
    status: str = "open",
    date: str = "2026-04-29",
    finding_kind: str = "other",
    body: str | None = None,
) -> str:
    body = (
        body
        or """# Test Finding

## Finding
The finding.

## Why this matters
The reason.

## Suggested next steps
- [ ] Do the thing.
"""
    )
    return f"""---
id: {stem}
date: {date}
status: {status}
finding_kind: {finding_kind}
review_context: "unit test"
related_paths: null
suggested_sweep: null
todo_id: null
---

{body}"""


def _write_finding(bs_dir: Path, stem: str, **kwargs) -> Path:
    bs_dir.mkdir(parents=True, exist_ok=True)
    path = bs_dir / f"{stem}.md"
    path.write_text(_finding_text(stem, **kwargs), encoding="utf-8")
    return path


def test_validator_reports_nested_enum_instead_of_traceback(tmp_path: Path) -> None:
    stem = "2026-04-29-120000-nested-status"
    path = tmp_path / f"{stem}.md"
    path.write_text(
        _finding_text(stem).replace("status: open", "status:\n  - open"),
        encoding="utf-8",
    )

    errors = validate_blind_spot.validate_file(path)

    assert "status must be a string, got list" in errors


def test_validator_accepts_crlf_frontmatter(tmp_path: Path) -> None:
    stem = "2026-04-29-120001-crlf-frontmatter"
    path = tmp_path / f"{stem}.md"
    path.write_text(_finding_text(stem).replace("\n", "\r\n"), encoding="utf-8")

    assert validate_blind_spot.validate_file(path) == []


def test_validator_rejects_frontmatter_only_files(tmp_path: Path) -> None:
    stem = "2026-04-29-120002-frontmatter-only"
    path = tmp_path / f"{stem}.md"
    path.write_text(
        f"""---
id: {stem}
date: 2026-04-29
status: open
finding_kind: other
review_context: "unit test"
---
""",
        encoding="utf-8",
    )

    errors = validate_blind_spot.validate_file(path)

    assert "body must include a top-level '# <title>' heading" in errors
    assert "body must include '## Finding' section" in errors


def test_sweep_list_handles_mixed_yaml_date_scalars(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bs_dir = tmp_path / "_project" / "blind-spots"
    _write_finding(bs_dir, "2026-04-29-120003-unquoted-date")
    _write_finding(bs_dir, "2026-04-29-120004-quoted-date", date='"2026-04-29"')

    result = sweep_blind_spots.cmd_list(bs_dir, SimpleNamespace(status="open", kind=None))

    assert result == 0
    assert "2 finding(s)." in capsys.readouterr().out


def test_find_one_rejects_path_traversal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bs_dir = tmp_path / "_project" / "blind-spots"
    bs_dir.mkdir(parents=True)

    with pytest.raises(SystemExit) as exc:
        sweep_blind_spots.find_one(bs_dir, "../audits/not-a-finding")

    assert exc.value.code == 2
    assert "invalid finding id" in capsys.readouterr().err


def test_stamp_frontmatter_preserves_unrelated_formatting(tmp_path: Path) -> None:
    stem = "2026-04-29-120005-preserve-format"
    path = tmp_path / f"{stem}.md"
    path.write_text(
        f"""---
id: {stem}
# keep this comment
date: 2026-04-29
status: open
finding_kind: other
review_context: "quoted context"
todo_id: null
---

# Test Finding

## Finding
The finding.

## Why this matters
The reason.

## Suggested next steps
- [ ] Do the thing.
""",
        encoding="utf-8",
    )
    _data, raw, body = sweep_blind_spots.split_frontmatter(path.read_text(encoding="utf-8"))

    sweep_blind_spots.stamp_frontmatter(path, raw, body, "dismissed", None)

    text = path.read_text(encoding="utf-8")
    assert "# keep this comment" in text
    assert 'review_context: "quoted context"' in text
    assert "status: dismissed" in text


def test_stamp_frontmatter_replaces_sequence_shaped_status_node(tmp_path: Path) -> None:
    stem = "2026-04-29-120006-sequence-status"
    path = tmp_path / f"{stem}.md"
    path.write_text(
        f"""---
id: {stem}
date: 2026-04-29
status:
  - open
finding_kind: other
review_context: "unit test"
todo_id: null
---

# Test Finding

## Finding
The finding.

## Why this matters
The reason.

## Suggested next steps
- [ ] Do the thing.
""",
        encoding="utf-8",
    )
    _data, raw, body = sweep_blind_spots.split_frontmatter(path.read_text(encoding="utf-8"))

    sweep_blind_spots.stamp_frontmatter(path, raw, body, "dismissed", None)

    data, raw, _body = sweep_blind_spots.split_frontmatter(path.read_text(encoding="utf-8"))
    assert data["status"] == "dismissed"
    assert raw.splitlines().count("status: dismissed") == 1
    assert "  - open" not in raw


def test_append_triage_log_inserts_before_next_section(tmp_path: Path) -> None:
    path = tmp_path / "finding.md"
    path.write_text(
        """# Test Finding

## Triage log

## Later notes

Keep this content separate.
""",
        encoding="utf-8",
    )

    sweep_blind_spots.append_triage_log(path, "2026-04-29: actioned")

    text = path.read_text(encoding="utf-8")
    assert text.index("- 2026-04-29: actioned") < text.index("## Later notes")


def test_triage_rejects_promote_reason_combo(tmp_path: Path) -> None:
    bs_dir = tmp_path / "_project" / "blind-spots"
    stem = "2026-04-29-120006-promote-reason"
    path = _write_finding(bs_dir, stem)
    args = SimpleNamespace(id=stem, action="promote", reason="because", todo_id="todo-slug")

    result = sweep_blind_spots.cmd_triage(bs_dir, args)

    assert result == 2
    assert "status: open" in path.read_text(encoding="utf-8")


def test_validator_accepts_actionable_status(tmp_path: Path) -> None:
    stem = "2026-04-29-120010-actionable-status"
    path = tmp_path / f"{stem}.md"
    path.write_text(_finding_text(stem, status="actionable"), encoding="utf-8")

    assert validate_blind_spot.validate_file(path) == []


def test_triage_actionable_requires_reason(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bs_dir = tmp_path / "_project" / "blind-spots"
    stem = "2026-04-29-120011-actionable-needs-reason"
    path = _write_finding(bs_dir, stem)
    args = SimpleNamespace(id=stem, action="actionable", reason=None, todo_id=None)

    result = sweep_blind_spots.cmd_triage(bs_dir, args)

    assert result == 2
    assert "--reason is required" in capsys.readouterr().err
    assert "status: open" in path.read_text(encoding="utf-8")


def test_triage_actionable_rejects_blank_reason(tmp_path: Path) -> None:
    bs_dir = tmp_path / "_project" / "blind-spots"
    stem = "2026-04-29-120012-actionable-blank-reason"
    path = _write_finding(bs_dir, stem)
    args = SimpleNamespace(id=stem, action="actionable", reason="   ", todo_id=None)

    result = sweep_blind_spots.cmd_triage(bs_dir, args)

    assert result == 2
    assert "status: open" in path.read_text(encoding="utf-8")


def test_triage_actionable_stamps_status_and_logs_reason(tmp_path: Path) -> None:
    bs_dir = tmp_path / "_project" / "blind-spots"
    stem = "2026-04-29-120013-actionable-happy-path"
    path = _write_finding(bs_dir, stem)
    args = SimpleNamespace(
        id=stem,
        action="actionable",
        reason="2026-05-06 sweep: still load-bearing; blocked on prerequisite",
        todo_id=None,
    )

    result = sweep_blind_spots.cmd_triage(bs_dir, args)

    assert result == 0
    text = path.read_text(encoding="utf-8")
    assert "status: actionable" in text
    assert "## Triage log" in text
    assert "actionable — 2026-05-06 sweep" in text


def test_default_list_filter_excludes_actionable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bs_dir = tmp_path / "_project" / "blind-spots"
    _write_finding(bs_dir, "2026-04-29-120014-still-open")
    _write_finding(bs_dir, "2026-04-29-120015-now-actionable", status="actionable")

    result = sweep_blind_spots.cmd_list(bs_dir, SimpleNamespace(status="open", kind=None))

    assert result == 0
    out = capsys.readouterr().out
    assert "2026-04-29-120014-still-open" in out
    assert "2026-04-29-120015-now-actionable" not in out
    assert "1 finding(s)." in out


def test_actionable_status_filter_surfaces_only_actionable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bs_dir = tmp_path / "_project" / "blind-spots"
    _write_finding(bs_dir, "2026-04-29-120016-still-open")
    _write_finding(bs_dir, "2026-04-29-120017-now-actionable", status="actionable")

    result = sweep_blind_spots.cmd_list(bs_dir, SimpleNamespace(status="actionable", kind=None))

    assert result == 0
    out = capsys.readouterr().out
    assert "2026-04-29-120017-now-actionable" in out
    assert "2026-04-29-120016-still-open" not in out
    assert "1 finding(s)." in out


def test_report_oldest_section_surfaces_actionable_alongside_open(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bs_dir = tmp_path / "_project" / "blind-spots"
    _write_finding(bs_dir, "2026-04-29-120020-stale-open")
    _write_finding(bs_dir, "2026-04-30-120021-confirmed-actionable", date="2026-04-30", status="actionable")
    _write_finding(bs_dir, "2026-04-29-120022-already-dismissed", status="dismissed")

    result = sweep_blind_spots.cmd_report(bs_dir, SimpleNamespace(top=10))

    assert result == 0
    out = capsys.readouterr().out
    assert "Oldest active (2 of 2)" in out
    assert "[open]" in out
    assert "[actionable]" in out
    assert "2026-04-29-120020-stale-open" in out
    assert "2026-04-30-120021-confirmed-actionable" in out
    assert "2026-04-29-120022-already-dismissed" not in out


_DRAFT_BODY = """# Draft class finding

## Finding
A class of issue.

## Why this matters
It matters.

## Suggested next steps
- [ ] Sweep siblings.
"""


def _draft_text(stem: str, extra: str = "") -> str:
    return f"""---
id: {stem}
date: 2026-07-22
status: open
finding_kind: framework-gap
review_context: "phase1 unit"
{extra}---

{_DRAFT_BODY}"""


def test_validator_accepts_new_optional_capture_fields(tmp_path: Path) -> None:
    stem = "2026-07-22-101010-optional-fields"
    extra = (
        "observed_sha: abc1234\n"
        "evidence:\n"
        "  - path: _project/scripts/todo_db.py\n"
        "    pattern: EXPORT_TABLE_ALLOWLIST\n"
        "    note: boundary constant\n"
        "  - path: docs/x.md\n"
        "    line_start: 10\n"
        "    line_end: 20\n"
        "urgency: high\n"
        "breadth: 3\n"
        "confidence: medium\n"
    )
    path = tmp_path / f"{stem}.md"
    path.write_text(_draft_text(stem, extra), encoding="utf-8")

    assert validate_blind_spot.validate_file(path) == []


def test_validator_rejects_evidence_without_path(tmp_path: Path) -> None:
    stem = "2026-07-22-101011-evidence-no-path"
    path = tmp_path / f"{stem}.md"
    path.write_text(_draft_text(stem, "evidence:\n  - pattern: no-path-here\n"), encoding="utf-8")

    errors = validate_blind_spot.validate_file(path)

    assert any("evidence[0].path is required" in e for e in errors)


def test_validator_rejects_unknown_evidence_key(tmp_path: Path) -> None:
    stem = "2026-07-22-101012-evidence-unknown-key"
    path = tmp_path / f"{stem}.md"
    path.write_text(
        _draft_text(stem, "evidence:\n  - path: a.py\n    bogus: nope\n"),
        encoding="utf-8",
    )

    errors = validate_blind_spot.validate_file(path)

    assert any("unknown key(s): ['bogus']" in e for e in errors)


def test_validator_rejects_non_list_evidence(tmp_path: Path) -> None:
    stem = "2026-07-22-101013-evidence-scalar"
    path = tmp_path / f"{stem}.md"
    path.write_text(_draft_text(stem, "evidence: just-a-string\n"), encoding="utf-8")

    errors = validate_blind_spot.validate_file(path)

    assert any("evidence must be a list" in e for e in errors)


def test_validator_rejects_bad_judgment_field(tmp_path: Path) -> None:
    stem = "2026-07-22-101014-bad-urgency"
    path = tmp_path / f"{stem}.md"
    path.write_text(_draft_text(stem, "urgency:\n  - 1\n  - 2\n"), encoding="utf-8")

    errors = validate_blind_spot.validate_file(path)

    assert any("urgency must be a string or integer" in e for e in errors)


def test_drafts_dir_absent_is_valid_zero_credential(tmp_path: Path, monkeypatch, capsys) -> None:
    missing = tmp_path / "no-such-drafts"
    monkeypatch.setattr(sys, "argv", ["validate_blind_spot.py", "--drafts-dir", str(missing)])

    assert validate_blind_spot.main() == 0
    assert "valid state" in capsys.readouterr().out


def test_drafts_dir_validates_captured_draft(tmp_path: Path, monkeypatch, capsys) -> None:
    drafts = tmp_path / "finding-drafts"
    drafts.mkdir()
    stem = "2026-07-22-101015-captured"
    (drafts / f"{stem}.md").write_text(_draft_text(stem, "observed_sha: deadbeef\n"), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["validate_blind_spot.py", "--drafts-dir", str(drafts)])

    assert validate_blind_spot.main() == 0
    assert f"OK   {drafts / f'{stem}.md'}" in capsys.readouterr().out


def test_drafts_dir_reports_invalid_draft(tmp_path: Path, monkeypatch, capsys) -> None:
    drafts = tmp_path / "finding-drafts"
    drafts.mkdir()
    stem = "2026-07-22-101016-bad"
    (drafts / f"{stem}.md").write_text(_draft_text(stem, "bogus_field: nope\n"), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["validate_blind_spot.py", "--drafts-dir", str(drafts)])

    assert validate_blind_spot.main() == 1
    assert "FAIL" in capsys.readouterr().err


def test_validator_rejects_inverted_line_range(tmp_path: Path) -> None:
    stem = "2026-07-22-101017-inverted-range"
    path = tmp_path / f"{stem}.md"
    path.write_text(
        _draft_text(stem, "evidence:\n  - path: a.py\n    line_start: 20\n    line_end: 10\n"),
        encoding="utf-8",
    )

    errors = validate_blind_spot.validate_file(path)

    assert any("line_start must not exceed line_end" in e for e in errors)
