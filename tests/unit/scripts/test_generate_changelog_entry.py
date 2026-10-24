from __future__ import annotations

import importlib.util
import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate_changelog_entry.py"

spec = importlib.util.spec_from_file_location("generate_changelog_entry", SCRIPT_PATH)
assert spec is not None
generate_changelog_entry = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(generate_changelog_entry)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _commit(repo: Path, message: str, filename: str, content: str) -> None:
    path = repo / filename
    path.write_text(content, encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-m", message)


def test_section_body_returns_only_requested_release_notes() -> None:
    changelog = """\
# Changelog

## [Unreleased]

## [0.4.0] - 2026-08-27

### Added

- shipped feature

## [0.3.1] - 2026-07-09

### Fixed

- older fix
"""
    assert generate_changelog_entry.section_body(changelog, "0.4.0").strip() == "### Added\n\n- shipped feature"
    assert generate_changelog_entry.section_body(changelog, "9.9.9") is None


def test_curated_section_body_rejects_empty_and_raw_sections(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## [0.4.0] - 2026-08-27\n\n## [0.3.1] - 2026-07-09\n",
        encoding="utf-8",
    )
    body, problems = generate_changelog_entry.curated_section_body(tmp_path, "0.4.0")
    assert body is None
    assert "section contains no bullets" in problems

    changelog.write_text(
        "# Changelog\n\n## [0.4.0] - 2026-08-27\n\n### Added\n\n- raw commit subject (#1933)\n",
        encoding="utf-8",
    )
    body, problems = generate_changelog_entry.curated_section_body(tmp_path, "0.4.0")
    assert body is None
    assert any("verbatim commit subjects" in problem for problem in problems)


CHANGELOG_BEFORE_CUT = """\
# Changelog

## [Unreleased]

## [0.4.0] - 2026-08-27

### Added

- shipped feature
"""

CURATED_BODY = """\
### Before you upgrade

- **Cached databases are rebuilt.** Tuned databases from earlier versions are
  rebuilt on the next run.

### Fixed

- Throughput runs stop at the configured time limit.
"""


def _section_file_cli(tmp_path: Path, monkeypatch, *extra: str) -> int:
    monkeypatch.setattr(
        sys,
        "argv",
        ["generate_changelog_entry.py", "--source", str(tmp_path), "--version", "0.4.1", *extra],
    )
    return generate_changelog_entry.main()


def test_section_file_writes_a_curated_section_that_passes_the_gate(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG_BEFORE_CUT, encoding="utf-8")
    section = tmp_path.parent / "section.md"
    section.write_text(CURATED_BODY, encoding="utf-8")

    assert _section_file_cli(tmp_path, monkeypatch, "--release-date", "2026-10-06", "--section-file", str(section)) == 0

    text = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert text.index("## [Unreleased]") < text.index("## [0.4.1] - 2026-10-06") < text.index("## [0.4.0]")
    assert generate_changelog_entry.section_body(text, "0.4.1").strip() == CURATED_BODY.strip()
    assert generate_changelog_entry.check_changelog_curation(tmp_path, "0.4.1") == (True, [])
    assert _section_file_cli(tmp_path, monkeypatch, "--check-curation") == 0


def test_section_file_with_raw_generated_text_is_still_refused(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG_BEFORE_CUT, encoding="utf-8")
    section = tmp_path.parent / "raw.md"
    section.write_text("### Added\n\n- add tuned template hashing (#2636)\n", encoding="utf-8")
    monkeypatch.delenv("RELEASE_ALLOW_RAW_CHANGELOG", raising=False)

    assert _section_file_cli(tmp_path, monkeypatch, "--section-file", str(section)) == 0
    assert _section_file_cli(tmp_path, monkeypatch, "--check-curation") == 1
    assert "does not look curated" in capsys.readouterr().out


def test_section_file_replaces_a_raw_section_left_by_an_earlier_pass(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".git").mkdir()
    raw = CHANGELOG_BEFORE_CUT.replace(
        "## [0.4.0]", "## [0.4.1] - 2026-10-05\n\n### Added\n\n- raw subject (#2700)\n\n## [0.4.0]"
    )
    (tmp_path / "CHANGELOG.md").write_text(raw, encoding="utf-8")
    section = tmp_path.parent / "section.md"
    section.write_text(CURATED_BODY, encoding="utf-8")

    assert _section_file_cli(tmp_path, monkeypatch, "--release-date", "2026-10-06", "--section-file", str(section)) == 0

    text = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "raw subject" not in text
    assert text.count("## [0.4.1]") == 1
    assert "## [0.4.1] - 2026-10-06" in text
    assert generate_changelog_entry.section_body(text, "0.4.0").strip() == "### Added\n\n- shipped feature"


@pytest.mark.parametrize(
    ("body", "message"),
    [("", "is empty"), ("## [0.4.1] - 2026-10-06\n\n- note\n", "without a '## ' version header")],
)
def test_section_file_rejects_empty_or_headed_bodies(tmp_path: Path, monkeypatch, capsys, body, message) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG_BEFORE_CUT, encoding="utf-8")
    section = tmp_path.parent / "section.md"
    section.write_text(body, encoding="utf-8")

    assert _section_file_cli(tmp_path, monkeypatch, "--section-file", str(section)) == 1
    assert message in capsys.readouterr().out
    assert (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8") == CHANGELOG_BEFORE_CUT


def test_print_section_cli_refuses_uncurated_notes(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [0.4.0] - 2026-08-27\n\n### Added\n\n- raw subject (#1933)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_changelog_entry.py",
            "--source",
            str(tmp_path),
            "--version",
            "0.4.0",
            "--print-section",
        ],
    )
    assert generate_changelog_entry.main() == 1
    assert "is not publishable" in capsys.readouterr().err


def test_latest_published_version_uses_pypi_metadata(monkeypatch) -> None:
    response = io.BytesIO(json.dumps({"info": {"version": "0.4.0"}}).encode())
    monkeypatch.setattr(generate_changelog_entry.urllib.request, "urlopen", lambda *_args, **_kwargs: response)
    assert generate_changelog_entry.latest_published_version() == "0.4.0"


def test_update_github_release_notes_edits_existing_release_idempotently(tmp_path: Path, monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_run(args, *, cwd, capture_output, text):
        notes_path = Path(args[-1])
        observed.update(args=args, cwd=cwd, notes=notes_path.read_text(encoding="utf-8"), path=notes_path)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(generate_changelog_entry.subprocess, "run", fake_run)
    generate_changelog_entry.update_github_release_notes(tmp_path, "0.4.0", "### Added\n\n- shipped")

    assert observed["args"][:4] == ["gh", "release", "edit", "v0.4.0"]
    assert observed["cwd"] == tmp_path
    assert observed["notes"] == "### Added\n\n- shipped\n"
    assert not observed["path"].exists()


class TestChangelogCurationGuard:
    def _write(self, repo: Path, body: str) -> None:
        (repo / "CHANGELOG.md").write_text(body, encoding="utf-8")

    def test_raw_commit_subjects_are_rejected(self, tmp_path: Path) -> None:
        self._write(
            tmp_path,
            "## [0.3.1] - 2026-07-09\n\n### Fixed\n\n- restore auto_merge_soundness_paths (#1086)\n",
        )
        ok, problems = generate_changelog_entry.check_changelog_curation(tmp_path, "0.3.1")
        assert not ok
        assert any("verbatim commit subjects" in p for p in problems)

    def test_bullet_ceiling_is_enforced(self, tmp_path: Path) -> None:
        bullets = "\n".join(f"- change number {i}" for i in range(generate_changelog_entry.MAX_CURATED_BULLETS + 1))
        self._write(tmp_path, f"## [0.3.1] - 2026-07-09\n\n### Added\n\n{bullets}\n")
        ok, problems = generate_changelog_entry.check_changelog_curation(tmp_path, "0.3.1")
        assert not ok
        assert any("ceiling" in p for p in problems)

    def test_line_ceiling_is_enforced(self, tmp_path: Path) -> None:
        lines = ["## [0.3.1] - 2026-07-09", "", "### Added", ""]
        lines.append("- **One change.** A single curated bullet.")
        lines.extend(f"filler line {i}" for i in range(generate_changelog_entry.MAX_CURATED_LINES))
        self._write(tmp_path, "\n".join(lines) + "\n")
        ok, problems = generate_changelog_entry.check_changelog_curation(tmp_path, "0.3.1")
        assert not ok
        assert any("ceiling" in p for p in problems)

    def test_contributor_level_draft_is_rejected_without_raw_subjects(self, tmp_path: Path) -> None:
        bullets = "\n".join(
            f"- **Change number {i}.** User-visible improvement.\n"
            f"  Further detail on change {i}.\n"
            f"  Even more detail on change {i}.\n"
            f"  Final detail on change {i}."
            for i in range(53)
        )
        self._write(tmp_path, f"## [0.3.1] - 2026-07-09\n\n### Added\n\n{bullets}\n")
        ok, problems = generate_changelog_entry.check_changelog_curation(tmp_path, "0.3.1")
        assert not ok
        assert any("53 bullets exceeds" in p for p in problems)
        assert any("lines exceeds" in p for p in problems)

    def test_missing_section_is_rejected(self, tmp_path: Path) -> None:
        self._write(tmp_path, "## [0.3.0] - 2026-05-16\n\n### Added\n\n- **Thing** - does things.\n")
        ok, problems = generate_changelog_entry.check_changelog_curation(tmp_path, "0.3.1")
        assert not ok
        assert "no '## [0.3.1] - <date>' section found" in problems[0]

    def test_curated_section_passes(self, tmp_path: Path) -> None:
        self._write(
            tmp_path,
            "## [0.3.1] - 2026-07-09\n\n### Fixed\n\n"
            "- **Release bootstrap** - validate-base now restores its helper module.\n\n"
            "## [0.3.0] - 2026-05-16\n\n### Added\n\n- Older entry (#1) still ignored.\n",
        )
        ok, problems = generate_changelog_entry.check_changelog_curation(tmp_path, "0.3.1")
        assert ok, problems

    def test_duplicate_headings_are_rejected(self, tmp_path: Path) -> None:
        self._write(
            tmp_path,
            "## [0.3.1] - 2026-07-09\n\n### Fixed\n\n- First fix for the release.\n\n"
            "### Added\n\n- A feature.\n\n### Fixed\n\n- Second fix for the release.\n",
        )
        ok, problems = generate_changelog_entry.check_changelog_curation(tmp_path, "0.3.1")
        assert not ok
        assert problems == ["duplicate '### Fixed' heading"]

    def test_headings_inside_code_fences_are_not_duplicates(self, tmp_path: Path) -> None:
        self._write(
            tmp_path,
            "## [0.3.1] - 2026-07-09\n\n### Added\n\n- A feature.\n\n```\n### Added\n```\n",
        )
        ok, problems = generate_changelog_entry.check_changelog_curation(tmp_path, "0.3.1")
        assert ok, problems

    def test_non_canonical_headings_remain_allowed(self, tmp_path: Path) -> None:
        self._write(tmp_path, "## [0.4.0] - 2026-08-27\n\n### Removed\n\n- A removal.\n\n### Security\n\n- A fix.\n")
        ok, problems = generate_changelog_entry.check_changelog_curation(tmp_path, "0.4.0")
        assert ok, problems

    def test_every_shipped_changelog_section_passes_the_guard(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        text = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")
        labels = re.findall(r"^## \[(\d[^\]]*)\]", text, re.MULTILINE)
        assert set(generate_changelog_entry.released_versions_in_changelog(text)) <= set(labels)
        for version in labels:
            ok, problems = generate_changelog_entry.check_changelog_curation(repo_root, version)
            assert ok, f"shipped section [{version}] fails the curation guard: {problems}"


class TestCurationOverrideScope:
    def test_missing_changelog_reports_cleanly(self, tmp_path: Path) -> None:
        ok, problems = generate_changelog_entry.check_changelog_curation(tmp_path, "0.3.1")
        assert not ok
        assert "not found" in problems[0]

    def test_override_does_not_apply_without_a_section(self, tmp_path: Path) -> None:
        (tmp_path / "CHANGELOG.md").write_text("## [0.3.0] - 2026-05-16\n\n- **X** - y.\n", encoding="utf-8")
        assert not generate_changelog_entry.has_changelog_section(tmp_path, "0.3.1")
        assert generate_changelog_entry.has_changelog_section(tmp_path, "0.3.0")

    def test_override_applies_to_a_raw_section(self, tmp_path: Path) -> None:
        (tmp_path / "CHANGELOG.md").write_text("## [0.3.1] - 2026-07-09\n\n- raw subject (#1086)\n", encoding="utf-8")
        assert generate_changelog_entry.has_changelog_section(tmp_path, "0.3.1")
        ok, _ = generate_changelog_entry.check_changelog_curation(tmp_path, "0.3.1")
        assert not ok


COMPARE = "https://github.com/BenchBox-dev/BenchBox/compare/"

BASE_CHANGELOG = f"""\
# Changelog

## [Unreleased]

## [0.4.2] - 2026-10-05

### Fixed

- Older fix.

[Unreleased]: {COMPARE}v0.4.2...HEAD
[0.4.2]: {COMPARE}v0.4.1...v0.4.2
"""

RC1_BODY = """\
### Before you upgrade

- **BREAKING: Old thing changed.** Run the migration before you upgrade.

### Added

- The first release candidate feature.
- Another candidate feature.

### Fixed
- A fix written without a blank line after its heading.
"""

RC_CHANGELOG = f"""\
# Changelog

## [Unreleased]

## [0.5.0rc1] - 2026-10-20

{RC1_BODY}
## [0.4.2] - 2026-10-05

### Fixed

- Older fix.

[Unreleased]: {COMPARE}v0.4.2...HEAD
[0.5.0rc1]: {COMPARE}v0.4.2...v0.5.0rc1
[0.4.2]: {COMPARE}v0.4.1...v0.4.2
"""

RELEASE_DATE = "2026-11-01"


def _text(label: str) -> str:
    return f"{label} written for the compile fixture so every entry satisfies the length bounds."


def _entry(category: str = "added", label: str = "entry", **extra: object) -> dict[str, object]:
    return {"category": category, "breaking": False, "text": _text(label), **extra}


def _repo(tmp_path: Path, changelog: str = BASE_CHANGELOG) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    _git(repo, "add", "CHANGELOG.md")
    _git(repo, "commit", "-q", "-m", "chore: initial")
    _git(repo, "tag", "v0.4.2")
    return repo


def _fragment(
    repo: Path,
    name: str,
    entries: list[dict[str, object]] | None = None,
    internal: str | None = None,
    subject: str | None = None,
) -> Path:
    data: dict[str, object] = {"schema": 1, "internal": internal} if internal else {"schema": 1, "entries": entries}
    path = repo / "changelog.d" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    if subject is not None:
        _git(repo, "add", f"changelog.d/{name}")
        _git(repo, "commit", "-q", "-m", subject)
    return path


def _compile(repo: Path, monkeypatch: pytest.MonkeyPatch, version: str, *extra: str) -> int:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_changelog_entry.py",
            "--source",
            str(repo),
            "--compile-fragments",
            "--version",
            version,
            "--release-date",
            RELEASE_DATE,
            *extra,
        ],
    )
    return generate_changelog_entry.main()


def _headings(text: str, version: str) -> list[str]:
    body = generate_changelog_entry.section_body(text, version) or ""
    return [name for _index, name in generate_changelog_entry._heading_positions(body.splitlines())]


def _links(text: str) -> list[str]:
    return [line for line in text.splitlines() if re.match(r"^\[[^\]]+\]: ", line)]


def _fragment_files(repo: Path) -> list[str]:
    directory = repo / "changelog.d"
    return sorted(path.name for path in directory.glob("*.json")) if directory.is_dir() else []


def _is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    remaining = iter(haystack)
    return all(line in remaining for line in needle)


def test_compile_creates_dated_section_below_empty_unreleased_and_consumes_every_fragment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    _fragment(
        repo,
        "feat-a.json",
        [_entry("fixed", "fixed-a"), _entry("added", "added-a", platforms=["duckdb"], benchmarks=["tpch"])],
        subject="feat: a (#11)",
    )
    _fragment(
        repo,
        "feat-b.json",
        [
            {**_entry("before-you-upgrade", "breaking-b", lead="Cache rebuilt"), "breaking": True},
            _entry("documentation", "docs-b", details=["a nested detail"]),
        ],
        subject="feat: b (#12)",
    )
    _fragment(repo, "chore-c.json", internal="Refactor only, nothing user visible.", subject="chore: c (#13)")

    assert _compile(repo, monkeypatch, "0.4.3") == 0

    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = text.index("## [Unreleased]")
    assert unreleased < text.index(f"## [0.4.3] - {RELEASE_DATE}") < text.index("## [0.4.2]")
    assert text[unreleased:].startswith("## [Unreleased]\n\n## [0.4.3]")
    assert generate_changelog_entry._sibling_module("changelog_fragments").unreleased_violations(text) == []
    assert _headings(text, "0.4.3") == ["Before you upgrade", "Added", "Fixed", "Documentation"]
    body = generate_changelog_entry.section_body(text, "0.4.3") or ""
    assert "- **BREAKING: Cache rebuilt.**" in body
    assert "  - a nested detail" in body
    assert "Refactor only" not in text
    assert _fragment_files(repo) == []
    assert generate_changelog_entry.check_changelog_curation(repo, "0.4.3") == (True, [])

    output = capsys.readouterr().out
    assert "changelog.d/feat-a.json -> PR #11 -> added -> platform:duckdb, benchmark:tpch" in output
    assert "changelog.d/feat-a.json -> PR #11 -> fixed -> -" in output
    assert "changelog.d/feat-b.json -> PR #12 -> before-you-upgrade -> -" in output
    assert "changelog.d/chore-c.json -> PR #13 -> internal -> -" in output
    assert f"/{generate_changelog_entry.MAX_CURATED_BULLETS} bullets" in output
    assert f"/{generate_changelog_entry.MAX_CURATED_LINES} lines" in output


def test_final_compile_folds_prerelease_verbatim_and_moves_compare_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, RC_CHANGELOG)
    _fragment(
        repo,
        "feat-a.json",
        [_entry("added", "added-final"), _entry("documentation", "docs-final")],
        subject="feat: a (#21)",
    )

    assert _compile(repo, monkeypatch, "0.5.0") == 0

    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [0.5.0rc1]" not in text
    assert "[0.5.0rc1]:" not in text
    assert text.index("## [Unreleased]") < text.index(f"## [0.5.0] - {RELEASE_DATE}") < text.index("## [0.4.2]")
    body = generate_changelog_entry.section_body(text, "0.5.0") or ""
    assert _is_subsequence(RC1_BODY.strip().splitlines(), body.splitlines())
    lines = body.splitlines()
    assert lines.index(f"- {_text('added-final')}") > lines.index("- Another candidate feature.")
    assert _headings(text, "0.5.0") == ["Before you upgrade", "Added", "Fixed", "Documentation"]
    assert _links(text) == [
        f"[Unreleased]: {COMPARE}v0.5.0...HEAD",
        f"[0.5.0]: {COMPARE}v0.4.2...v0.5.0",
        f"[0.4.2]: {COMPARE}v0.4.1...v0.4.2",
    ]
    assert (
        generate_changelog_entry.find_release_accounting_errors(
            text, {"v0.5.0"}, published_version="0.5.0", pyproject_version="0.5.0"
        )
        == []
    )


def test_final_compile_without_fragments_folds_the_prerelease_section_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, RC_CHANGELOG)

    assert _compile(repo, monkeypatch, "0.5.0") == 0

    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert (generate_changelog_entry.section_body(text, "0.5.0") or "").strip() == RC1_BODY.strip()


def test_prerelease_compile_adds_only_its_own_compare_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    _fragment(repo, "feat-a.json", [_entry("added", "added-rc")], subject="feat: a (#31)")
    before = _links(BASE_CHANGELOG)

    assert _compile(repo, monkeypatch, "0.5.0rc1") == 0

    after = _links((repo / "CHANGELOG.md").read_text(encoding="utf-8"))
    assert after[0] == before[0] == f"[Unreleased]: {COMPARE}v0.4.2...HEAD"
    assert [link for link in after if link not in before] == [f"[0.5.0rc1]: {COMPARE}v0.4.2...v0.5.0rc1"]
    assert [link for link in before if link not in after] == []


def test_later_prerelease_compile_folds_the_earlier_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path, RC_CHANGELOG)
    _fragment(repo, "fix-a.json", [_entry("fixed", "fixed-rc2")], subject="fix: a (#41)")

    assert _compile(repo, monkeypatch, "0.5.0rc2") == 0

    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [0.5.0rc1]" not in text
    body = generate_changelog_entry.section_body(text, "0.5.0rc2") or ""
    assert "- The first release candidate feature." in body
    assert f"- {_text('fixed-rc2')}" in body
    assert body.count("### Fixed") == 1
    assert _links(text)[:2] == [
        f"[Unreleased]: {COMPARE}v0.4.2...HEAD",
        f"[0.5.0rc2]: {COMPARE}v0.4.2...v0.5.0rc2",
    ]


def test_missing_headings_are_created_in_canonical_order_without_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changelog = RC_CHANGELOG.replace(
        RC1_BODY, "### Fixed\n\n- An existing candidate fix.\n\n### Documentation\n\n- An existing doc note.\n"
    )
    repo = _repo(tmp_path, changelog)
    _fragment(
        repo,
        "feat-a.json",
        [
            _entry("documentation", "docs-new"),
            _entry("added", "added-new"),
            _entry("fixed", "fixed-new"),
            {**_entry("before-you-upgrade", "breaking-new", lead="Behavior changed"), "breaking": True},
            _entry("changed", "changed-new"),
        ],
        subject="feat: a (#51)",
    )

    assert _compile(repo, monkeypatch, "0.5.0") == 0

    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert _headings(text, "0.5.0") == ["Before you upgrade", "Added", "Changed", "Fixed", "Documentation"]
    lines = (generate_changelog_entry.section_body(text, "0.5.0") or "").splitlines()
    assert lines.index("- An existing candidate fix.") < lines.index(f"- {_text('fixed-new')}")
    assert lines.index("- An existing doc note.") < lines.index(f"- {_text('docs-new')}")
    assert generate_changelog_entry.check_changelog_curation(repo, "0.5.0") == (True, [])


def test_rerun_appends_new_fragments_to_the_existing_section_and_is_a_noop_without_any(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _fragment(repo, "feat-a.json", [_entry("added", "added-first")], subject="feat: a (#61)")
    assert _compile(repo, monkeypatch, "0.4.3") == 0

    _fragment(repo, "fix-b.json", [_entry("added", "added-second"), _entry("fixed", "fixed-second")])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_changelog_entry.py",
            "--source",
            str(repo),
            "--compile-fragments",
            "--version",
            "0.4.3",
            "--release-date",
            "2027-01-01",
        ],
    )
    assert generate_changelog_entry.main() == 0

    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert text.count("## [0.4.3]") == 1
    assert f"## [0.4.3] - {RELEASE_DATE}" in text
    lines = (generate_changelog_entry.section_body(text, "0.4.3") or "").splitlines()
    assert lines.index(f"- {_text('added-first')}") < lines.index(f"- {_text('added-second')}")
    assert _headings(text, "0.4.3") == ["Added", "Fixed"]
    assert _fragment_files(repo) == []

    settled = (repo / "CHANGELOG.md").read_bytes()
    assert _compile(repo, monkeypatch, "0.4.3") == 0
    assert (repo / "CHANGELOG.md").read_bytes() == settled


def test_invalid_fragment_aborts_before_writing_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    good = _fragment(repo, "feat-good.json", [_entry("added", "added-good")])
    bad = _fragment(repo, "feat-bad.json", [{"category": "added", "breaking": False, "text": "too short"}])
    before = {path: path.read_bytes() for path in (repo / "CHANGELOG.md", good, bad)}

    assert _compile(repo, monkeypatch, "0.4.3") == 1

    assert {path: path.read_bytes() for path in before} == before
    err = capsys.readouterr().err
    assert bad.as_posix() in err
    assert "entries[0].text" in err
    assert good.as_posix() not in err


def test_compile_refuses_a_non_empty_unreleased_section_and_an_empty_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stray = BASE_CHANGELOG.replace("## [Unreleased]\n", "## [Unreleased]\n\n### Fixed\n\n- A stray entry.\n")
    repo = _repo(tmp_path, stray)
    _fragment(repo, "feat-a.json", [_entry("added", "added-stray")])
    assert _compile(repo, monkeypatch, "0.4.3") == 1
    assert "[Unreleased] section must stay empty" in capsys.readouterr().err
    assert (repo / "CHANGELOG.md").read_text(encoding="utf-8") == stray

    empty = tmp_path / "other"
    empty.mkdir()
    repo = _repo(empty)
    assert _compile(repo, monkeypatch, "0.4.3") == 1
    assert "no user-facing fragments" in capsys.readouterr().err
    assert (repo / "CHANGELOG.md").read_text(encoding="utf-8") == BASE_CHANGELOG


def test_dry_run_validates_and_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    fragment = _fragment(repo, "feat-a.json", [_entry("added", "added-dry")])

    assert _compile(repo, monkeypatch, "0.4.3", "--dry-run") == 0

    assert (repo / "CHANGELOG.md").read_text(encoding="utf-8") == BASE_CHANGELOG
    assert fragment.exists()


def test_coverage_report_lists_user_facing_commits_without_a_fragment_from_the_previous_final_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "feat: shipped without a fragment (#71)", "a.txt", "a\n")
    _commit(repo, "perf: faster scan without a fragment (#72)", "b.txt", "b\n")
    _commit(repo, "chore: housekeeping (#73)", "c.txt", "c\n")
    _fragment(repo, "fix-d.json", [_entry("fixed", "fixed-covered")], subject="fix: covered by a fragment (#74)")
    seen: list[str] = []
    original = generate_changelog_entry._commits_since_ref

    def spy(source: Path, ref: str):
        seen.append(ref)
        return original(source, ref)

    monkeypatch.setattr(generate_changelog_entry, "_commits_since_ref", spy)

    assert _compile(repo, monkeypatch, "0.4.3") == 0

    assert seen == ["refs/tags/v0.4.2"]
    output = capsys.readouterr().out
    report = output[output.index("Coverage:") :]
    assert "- feat: shipped without a fragment (#71)" in report
    assert "- perf: faster scan without a fragment (#72)" in report
    assert "housekeeping" not in report
    assert "covered by a fragment" not in report


def test_require_prepared_refuses_a_missing_section_and_pending_fragments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    args = ["generate_changelog_entry.py", "--source", str(repo), "--require-prepared", "--version", "0.4.3"]

    monkeypatch.setattr(sys, "argv", args)
    assert generate_changelog_entry.main() == 1
    assert "no '## [0.4.3] - <date>' section" in capsys.readouterr().err

    _fragment(repo, "feat-a.json", [_entry("added", "added-prepared")])
    assert _compile(repo, monkeypatch, "0.4.3", "--dry-run") == 0
    monkeypatch.setattr(sys, "argv", args)
    assert generate_changelog_entry.main() == 1
    assert "still pending" in capsys.readouterr().err

    assert _compile(repo, monkeypatch, "0.4.3") == 0
    monkeypatch.setattr(sys, "argv", args)
    assert generate_changelog_entry.main() == 0

    _fragment(repo, "feat-late.json", [_entry("added", "added-late")])
    monkeypatch.setattr(sys, "argv", args)
    assert generate_changelog_entry.main() == 1
    assert "feat-late.json" in capsys.readouterr().err


def test_commit_subject_drafting_and_summarizer_are_gone() -> None:
    removed_names = (
        "RAW_PLACEHOLDER",
        "_build_raw_changelog",
        "_summarize_changelog_with_claude",
        "summarize_skip_reason",
        "_run_claude_cli",
        "_patch_delta_commit_subjects",
        "_resolve_log_range",
        "generate_changelog_entry",
    )
    for name in removed_names:
        assert not hasattr(generate_changelog_entry, name), name
    assert hasattr(generate_changelog_entry, "_commits_since_ref")

    forbidden = ("RAW_PLACEHOLDER", "CHANGELOG_SUMMARIZE", "claude --print", "--since-ref", "--since-tag", "SINCE_REF")
    sources = [
        REPO_ROOT / "scripts" / "generate_changelog_entry.py",
        REPO_ROOT / "scripts" / "release_flow.py",
        REPO_ROOT / "Makefile",
        REPO_ROOT / "docs" / "internal" / "release-guide.md",
    ]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{token} still referenced in {path.name}"
