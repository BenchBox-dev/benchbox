from __future__ import annotations

import importlib.util
import io
import json
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
    assert "does not look hand-curated" in capsys.readouterr().out


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


def test_since_ref_limits_release_changelog_to_main_delta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\n", encoding="utf-8")
    _git(repo, "add", "CHANGELOG.md")
    _git(repo, "commit", "-m", "chore: initial")
    initial_commit = _git(repo, "rev-parse", "HEAD")

    _git(repo, "branch", "-M", "main")
    _commit(repo, "fix: main-only release hardening", "main.txt", "main\n")

    _git(repo, "checkout", "-b", "develop", initial_commit)
    _commit(repo, "feat: add broad release feature", "feature.txt", "feature\n")
    _commit(repo, "fix: repair clean install", "fix.txt", "fix\n")

    monkeypatch.setattr(generate_changelog_entry, "_summarize_changelog_with_claude", lambda *args: None)

    assert generate_changelog_entry.generate_changelog_entry(
        repo,
        version="0.3.1",
        release_date="2026-05-26",
        since_ref="main",
    )

    changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [0.3.1] - 2026-05-26" in changelog
    assert "- add broad release feature" in changelog
    assert "- repair clean install" in changelog
    assert "main-only release hardening" not in changelog


def test_since_ref_ignores_commits_already_present_via_squash_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\n", encoding="utf-8")
    _git(repo, "add", "CHANGELOG.md")
    _git(repo, "commit", "-m", "chore: initial")
    _git(repo, "branch", "-M", "main")

    _git(repo, "checkout", "-b", "develop")
    _commit(repo, "feat: already released feature", "released_feature.txt", "feature\n")
    _commit(repo, "fix: already released fix", "released_fix.txt", "fix\n")

    _git(repo, "checkout", "main")
    _git(repo, "merge", "--squash", "develop")
    _git(repo, "commit", "-m", "Release v0.3.1")

    _git(repo, "checkout", "develop")
    _commit(repo, "feat: add next release feature", "next_feature.txt", "next\n")

    monkeypatch.setattr(generate_changelog_entry, "_summarize_changelog_with_claude", lambda *args: None)

    assert generate_changelog_entry.generate_changelog_entry(
        repo,
        version="0.3.2",
        release_date="2026-05-31",
        since_ref="main",
    )

    changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "- add next release feature" in changelog
    assert "already released feature" not in changelog
    assert "already released fix" not in changelog


def test_since_ref_ignores_squashed_commit_when_new_patch_touches_same_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\n", encoding="utf-8")
    _git(repo, "add", "CHANGELOG.md")
    _git(repo, "commit", "-m", "chore: initial")
    _git(repo, "branch", "-M", "main")

    _git(repo, "checkout", "-b", "develop")
    _commit(repo, "feat: already released same-file feature", "f.txt", "released\n")

    _git(repo, "checkout", "main")
    _git(repo, "merge", "--squash", "develop")
    _git(repo, "commit", "-m", "Release v0.3.1")

    _git(repo, "checkout", "develop")
    _commit(repo, "fix: add next same-file patch", "f.txt", "released\nnext\n")

    monkeypatch.setattr(generate_changelog_entry, "_summarize_changelog_with_claude", lambda *args: None)

    assert generate_changelog_entry.generate_changelog_entry(
        repo,
        version="0.3.2",
        release_date="2026-05-31",
        since_ref="main",
    )

    changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "- add next same-file patch" in changelog
    assert "already released same-file feature" not in changelog


def test_since_ref_ignores_intermediate_squashed_same_file_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\n", encoding="utf-8")
    _git(repo, "add", "CHANGELOG.md")
    _git(repo, "commit", "-m", "chore: initial")
    _git(repo, "branch", "-M", "main")

    _git(repo, "checkout", "-b", "develop")
    _commit(repo, "feat: already released same-file feature", "f.txt", "A\n")
    _commit(repo, "fix: already released same-file fix", "f.txt", "A\nB\n")

    _git(repo, "checkout", "main")
    _git(repo, "merge", "--squash", "develop")
    _git(repo, "commit", "-m", "Release v0.3.1")

    _git(repo, "checkout", "develop")
    _commit(repo, "fix: add next same-file patch", "f.txt", "A\nB\nC\n")

    monkeypatch.setattr(generate_changelog_entry, "_summarize_changelog_with_claude", lambda *args: None)

    assert generate_changelog_entry.generate_changelog_entry(
        repo,
        version="0.3.2",
        release_date="2026-05-31",
        since_ref="main",
    )

    changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "- add next same-file patch" in changelog
    assert "already released same-file feature" not in changelog
    assert "already released same-file fix" not in changelog


class TestSummarizeSkipReason:
    def test_nested_session_skips_summarization(self) -> None:
        for var in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID"):
            reason = generate_changelog_entry.summarize_skip_reason({var: "1"})
            assert reason is not None and var in reason

    def test_outside_session_runs_summarization(self) -> None:
        assert generate_changelog_entry.summarize_skip_reason({}) is None

    def test_explicit_opt_in_overrides_nesting(self) -> None:
        env = {"CLAUDECODE": "1", "BENCHBOX_CHANGELOG_SUMMARIZE": "1"}
        assert generate_changelog_entry.summarize_skip_reason(env) is None

    def test_explicit_opt_out_wins_outside_session(self) -> None:
        env = {"BENCHBOX_CHANGELOG_SUMMARIZE": "0"}
        assert generate_changelog_entry.summarize_skip_reason(env) is not None

    def test_skipped_summarization_never_spawns_a_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.delenv("BENCHBOX_CHANGELOG_SUMMARIZE", raising=False)

        def _explode(*args: object, **kwargs: object) -> None:
            raise AssertionError("nested claude CLI must not be spawned")

        monkeypatch.setattr(generate_changelog_entry.subprocess, "Popen", _explode)
        assert generate_changelog_entry._summarize_changelog_with_claude("0.3.1", "2026-07-09", ["a"], [], []) is None

    def test_claude_cli_runs_in_its_own_process_group(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        class _FakeProc:
            args = ["claude"]
            returncode = 0

            def __enter__(self) -> _FakeProc:
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def communicate(self, timeout: float | None = None) -> tuple[str, str]:
                return ("### Added\n- x\n", "")

        def _fake_popen(cmd: list[str], **kwargs: object) -> _FakeProc:
            captured.update(kwargs)
            return _FakeProc()

        monkeypatch.setattr(generate_changelog_entry.subprocess, "Popen", _fake_popen)
        generate_changelog_entry._run_claude_cli("prompt")
        assert captured["start_new_session"] is True
        assert captured["stdin"] is generate_changelog_entry.subprocess.DEVNULL


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

    def test_manual_edit_placeholder_is_rejected(self, tmp_path: Path) -> None:
        self._write(
            tmp_path,
            f"## [0.3.1] - 2026-07-09\n\n### Added\n\n- {generate_changelog_entry.RAW_PLACEHOLDER}\n",
        )
        ok, problems = generate_changelog_entry.check_changelog_curation(tmp_path, "0.3.1")
        assert not ok
        assert any("placeholder" in p for p in problems)

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

    def test_every_shipped_changelog_section_passes_the_guard(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        text = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")
        for version in generate_changelog_entry.released_versions_in_changelog(text):
            ok, problems = generate_changelog_entry.check_changelog_curation(repo_root, version)
            assert ok, f"shipped section [{version}] fails the curation guard: {problems}"


class TestChangelogGenerationIsIdempotent:
    def test_existing_section_is_left_untouched(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test User")
        _commit(repo, "feat: a new feature", "f.txt", "A\n")

        curated = "## [0.3.1] - 2026-07-09\n\n### Added\n\n- **Curated** - written by hand.\n"
        (repo / "CHANGELOG.md").write_text(curated, encoding="utf-8")

        monkeypatch.setattr(generate_changelog_entry, "_summarize_changelog_with_claude", lambda *args: None)
        assert generate_changelog_entry.generate_changelog_entry(repo, version="0.3.1", release_date="2026-07-09")

        text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
        assert text == curated
        assert text.count("## [0.3.1]") == 1


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
