from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "changelog_fragments.py"

spec = importlib.util.spec_from_file_location("changelog_fragments", SCRIPT_PATH)
assert spec is not None
cf = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["changelog_fragments"] = cf
spec.loader.exec_module(cf)

TEXT = "Result readers accept the new optional per-query timing field."


def _entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {"category": "added", "breaking": False, "text": TEXT}
    entry.update(overrides)
    return entry


def _doc(**overrides: object) -> dict[str, object]:
    return {"schema": 1, "entries": [_entry()], **overrides}


def _write(directory: Path, name: str, data: object) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def _errors(tmp_path: Path, data: object, name: str = "fix-thing.json") -> list[str]:
    fragment, errors = cf.parse_fragment(_write(tmp_path, name, data))
    assert (fragment is None) == bool(errors)
    return errors


def test_valid_user_entry_parses(tmp_path: Path) -> None:
    fragment, errors = cf.parse_fragment(
        _write(
            tmp_path,
            "feat-x.json",
            _doc(entries=[_entry(details=["one", "two"], platforms=["duckdb"], benchmarks=["tpch"])]),
        )
    )
    assert errors == []
    assert fragment is not None
    assert fragment.internal is None
    assert fragment.entries[0].details == ("one", "two")
    assert fragment.entries[0].platforms == ("duckdb",)


def test_valid_internal_file_parses(tmp_path: Path) -> None:
    fragment, errors = cf.parse_fragment(_write(tmp_path, "chore-x.json", {"schema": 1, "internal": "Test only."}))
    assert errors == []
    assert fragment is not None
    assert fragment.entries == ()
    assert fragment.internal == "Test only."


@pytest.mark.parametrize(
    ("data", "needle"),
    [
        ("{not json", "invalid JSON"),
        ("[]", "top level must be an object"),
        ({"schema": 2, "entries": [_entry()]}, "schema"),
        ({"schema": True, "entries": [_entry()]}, "schema"),
        ({"entries": [_entry()]}, "schema"),
        (_doc(extra=1), "unknown key 'extra'"),
        (_doc(entries=[_entry(category="removed")]), "entries[0].category"),
        (_doc(entries=[_entry(surprise=True)]), "entries[0]: unknown key 'surprise'"),
        (_doc(entries=[_entry(text="Fixed a thing (#3123)")]), "(#N)"),
        (_doc(entries=[_entry(text="- Starts with a bullet marker")]), "'- '"),
        (_doc(entries=[_entry(text="First line of text\nsecond line of text")]), "single line"),
        (_doc(entries=[_entry(text="too short")]), "length 9"),
        (_doc(entries=[_entry(text="x" * 801)]), "length 801"),
        (_doc(entries=[_entry(category="fixed", breaking=True, lead="Lead")]), "before-you-upgrade"),
        (_doc(entries=[_entry(category="before-you-upgrade", breaking=True)]), "requires a lead"),
        (_doc(entries=[_entry(breaking="yes")]), "breaking"),
        (_doc(entries=[_entry(details=["d"] * 9)]), "exceeds the maximum of 8"),
        (_doc(entries=[_entry(platforms=["DuckDB"])]), "platforms[0]"),
        (_doc(entries=[_entry(benchmarks="tpch")]), "benchmarks"),
        (_doc(entries=[]), "outside 1-5"),
        (_doc(entries=[_entry()] * 6), "outside 1-5"),
        ({"schema": 1}, "exactly one of"),
        ({"schema": 1, "entries": [_entry()], "internal": "Both of them set."}, "exactly one of"),
        ({"schema": 1, "internal": "short"}, "internal: length 5"),
        ({"schema": 1, "internal": "x" * 301}, "internal: length 301"),
        ({"schema": 1, "entries": [{"category": "added"}]}, "entries[0].text: required"),
    ],
)
def test_invalid_fragment_reports_path_specific_message(tmp_path: Path, data: object, needle: str) -> None:
    errors = _errors(tmp_path, data)
    assert errors
    assert any(needle in message for message in errors), errors
    assert all(message.startswith((tmp_path / "fix-thing.json").as_posix()) for message in errors)


@pytest.mark.parametrize("name", ["Upper.json", "-lead.json", "spaces here.json", "x.txt", ("a" * 101) + ".json"])
def test_invalid_file_name_is_rejected(tmp_path: Path, name: str) -> None:
    path = tmp_path / "ok.json"
    path.write_text(json.dumps(_doc()), encoding="utf-8")
    _, errors = cf.parse_fragment(Path(name), source=path.read_text(encoding="utf-8"))
    assert any("file name" in message for message in errors)


def test_name_boundary_of_one_hundred_characters_is_accepted() -> None:
    _, errors = cf.parse_fragment(Path(("a" * 100) + ".json"), source=json.dumps(_doc()))
    assert errors == []


def test_text_length_boundaries(tmp_path: Path) -> None:
    assert _errors(tmp_path, _doc(entries=[_entry(text="x" * 20)])) == []
    assert _errors(tmp_path, _doc(entries=[_entry(text="x" * 800)])) == []
    assert _errors(tmp_path, _doc(entries=[_entry(text="x" * 19)]))


def test_breaking_entry_requires_category_and_lead_together(tmp_path: Path) -> None:
    ok = _doc(entries=[_entry(category="before-you-upgrade", breaking=True, lead="Config renamed")])
    assert _errors(tmp_path, ok) == []


def _fragments(*entry_sets: list[dict[str, object]]) -> list[object]:
    out = []
    for index, entries in enumerate(entry_sets):
        fragment, errors = cf.parse_fragment(
            Path(f"f{index}.json"), source=json.dumps({"schema": 1, "entries": entries})
        )
        assert errors == []
        out.append(fragment)
    return out


def test_render_orders_headings_canonically_and_nests_details() -> None:
    fragments = _fragments(
        [
            _entry(category="documentation", text="Documented the fragment workflow for contributors."),
            _entry(category="fixed", text="Fixed a crash when a result bundle is empty."),
            _entry(category="changed", text="Changed the default scale factor handling rules."),
        ],
        [
            _entry(category="added", text="Added a new optional timing field to results.", details=["First", "Second"]),
            _entry(
                category="before-you-upgrade",
                breaking=True,
                lead="Config key renamed",
                text="The old key is no longer read and must be renamed.",
            ),
        ],
    )
    rendered = cf.render(fragments)
    headings = [line for line in rendered.splitlines() if line.startswith("### ")]
    assert headings == [
        "### Before you upgrade",
        "### Added",
        "### Changed",
        "### Fixed",
        "### Documentation",
    ]
    assert "- **BREAKING: Config key renamed.** The old key is no longer read and must be renamed." in rendered
    assert "- Added a new optional timing field to results.\n  - First\n  - Second\n" in rendered
    assert rendered.endswith("\n") and not rendered.endswith("\n\n")


def test_render_sorts_within_heading_by_platform_then_benchmark() -> None:
    fragments = _fragments(
        [
            _entry(text="Zeta entry without any tags at all here.", platforms=["snowflake"]),
            _entry(text="Alpha entry tagged with duckdb platform.", platforms=["duckdb"], benchmarks=["tpch"]),
            _entry(text="Beta entry tagged with duckdb and ssb.", platforms=["duckdb"], benchmarks=["ssb"]),
        ]
    )
    lines = [line for line in cf.render(fragments).splitlines() if line.startswith("- ")]
    assert [line.split()[1] for line in lines] == ["Beta", "Alpha", "Zeta"]


def test_render_is_independent_of_fragment_order() -> None:
    fragments = _fragments(
        [_entry(text="First entry about something that changed.")],
        [_entry(text="Second entry about another thing that changed.")],
    )
    assert cf.render(fragments) == cf.render(list(reversed(fragments)))


def test_internal_only_fragments_render_nothing() -> None:
    fragment, errors = cf.parse_fragment(
        Path("chore-x.json"), source=json.dumps({"schema": 1, "internal": "Test-only."})
    )
    assert errors == []
    assert cf.render([fragment]) == ""
    assert cf.render([]) == ""


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "commit.gpgsign", "false")


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def test_pr_number_comes_from_the_adding_commit_subject(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "seed.txt").write_text("seed", encoding="utf-8")
    _commit(tmp_path, "seed")
    fragment_json = json.dumps(_doc(entries=[_entry(text="Mentions (#999) inside the body text itself here.")]))
    _write(tmp_path / "changelog.d", "fix-a.json", fragment_json)
    _commit(tmp_path, "fix(core): repair the thing (#4242)")
    (tmp_path / "changelog.d" / "fix-a.json").write_text(fragment_json + "\n", encoding="utf-8")
    _commit(tmp_path, "docs: touch the fragment again (#5000)")
    assert cf.pr_number_for(tmp_path, "changelog.d/fix-a.json") == 4242


def test_pr_number_is_unknown_without_squash_suffix_or_history(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write(tmp_path / "changelog.d", "fix-a.json", _doc())
    _commit(tmp_path, "fix: no pull request suffix")
    assert cf.pr_number_for(tmp_path, "changelog.d/fix-a.json") is None
    assert cf.pr_number_for(tmp_path, "changelog.d/never-added.json") is None


@pytest.fixture
def rules() -> tuple[object, ...]:
    return (
        SimpleNamespace(kind="prefix", pattern="benchbox/"),
        SimpleNamespace(kind="prefix", pattern="_sources/"),
        SimpleNamespace(kind="prefix", pattern="_binaries/"),
        SimpleNamespace(kind="file", pattern="pyproject.toml"),
        SimpleNamespace(kind="file", pattern="MANIFEST.in"),
        SimpleNamespace(kind="except", pattern="benchbox/utils/VERSION_MANAGEMENT.md"),
    )


requires_project_parser = pytest.mark.skipif(
    not (REPO_ROOT / "_project" / "scripts" / "soundness_paths.py").is_file(),
    reason="_project is removed from the curated release tree",
)


SHIPPED = ["benchbox/core/runner.py"]
FRAGMENT = "changelog.d/fix-x.json"


@pytest.mark.parametrize(
    ("title", "required"),
    [
        ("feat(core): add a thing", True),
        ("fix: repair a thing", True),
        ("perf(tpcds): speed up staging", True),
        ("docs!: drop an old page", True),
        ("refactor(core)!: rename public api", True),
        ("chore(deps): bump sqlglot", True),
        ("Add a thing without a type", True),
        ("feature: unknown type", True),
        ("Feat: wrong case", True),
        ("fix:no space", True),
        ("fix: line one\nline two", True),
        ("", True),
        ("chore: tidy", False),
        ("chore(ci): tidy", False),
        ("build: adjust packaging", False),
        ("ci: adjust workflow", False),
        ("docs(readme): reword", False),
        ("refactor(core): extract helper", False),
        ("style: format", False),
        ("test(core): cover edge", False),
        ('Revert "feat(core): add a thing"', False),
    ],
)
def test_required_decision_per_title_type(rules: tuple[object, ...], title: str, required: bool) -> None:
    decision = cf.decide_required(title, SHIPPED, rules)
    assert decision.required is required
    assert decision.ok is (not required)


def test_release_identity_change_exempts_otherwise_required_title(rules: tuple[object, ...]) -> None:
    assert not cf.decide_required("feat: x y", SHIPPED, rules, release_identity_changed=True).required
    assert cf.decide_required("feat: x y", SHIPPED, rules, release_identity_changed=False).required


@pytest.mark.parametrize(
    ("paths", "required"),
    [
        (["benchbox/core/runner.py"], True),
        (["_sources/tpch/dbgen.c"], True),
        (["_binaries/tool"], True),
        (["pyproject.toml"], True),
        (["MANIFEST.in"], True),
        (["benchbox\\core\\runner.py"], True),
        (["benchbox/utils/VERSION_MANAGEMENT.md"], False),
        (["benchbox/utils/VERSION_MANAGEMENT.md", "pyproject.toml"], True),
        (["tests/unit/test_x.py", "scripts/x.py", "docs/a.md", "uv.lock"], False),
        (["benchboxx/other.py"], False),
        ([], False),
    ],
)
def test_required_decision_follows_the_path_manifest(
    rules: tuple[object, ...], paths: list[str], required: bool
) -> None:
    assert cf.decide_required("feat: add a thing", paths, rules).required is required


def test_bang_title_without_shipped_paths_is_not_required(rules: tuple[object, ...]) -> None:
    assert not cf.decide_required("feat!: only tests moved", ["tests/a.py"], rules).required


@pytest.mark.parametrize("action_path", [FRAGMENT, "changelog.d/other-branch.json"])
def test_any_fragment_change_satisfies_required_entry(rules: tuple[object, ...], action_path: str) -> None:
    unsatisfied = cf.decide_required("fix: repair", SHIPPED, rules)
    assert unsatisfied.required and not unsatisfied.satisfied and not unsatisfied.ok
    satisfied = cf.decide_required("fix: repair", [*SHIPPED, action_path], rules)
    assert satisfied.required and satisfied.satisfied and satisfied.ok


def test_deleting_a_fragment_satisfies_a_revert_style_requirement(rules: tuple[object, ...]) -> None:
    decision = cf.decide_required("fix: undo the earlier change", [*SHIPPED, FRAGMENT], rules)
    assert decision.ok


@pytest.mark.parametrize(
    "path",
    [
        "changelog.d/nested/x.json",
        "changelog.d/x.txt",
        "changelog.d.bak/x.json",
        "docs/changelog.d/x.json",
        "changelog.d/",
    ],
)
def test_only_top_level_json_under_changelog_dir_counts_as_a_fragment(rules: tuple[object, ...], path: str) -> None:
    assert not cf.decide_required("fix: repair", [*SHIPPED, path], rules).satisfied


def test_internal_title_adding_user_entry_warns_without_blocking(rules: tuple[object, ...]) -> None:
    decision = cf.decide_required("test: add coverage", [*SHIPPED, FRAGMENT], rules, user_entry_fragments=[FRAGMENT])
    assert decision.ok and not decision.required
    assert len(decision.warnings) == 1
    assert not cf.decide_required("test: add coverage", [*SHIPPED, FRAGMENT], rules).warnings
    assert not cf.decide_required(
        "feat: add coverage", [*SHIPPED, FRAGMENT], rules, user_entry_fragments=[FRAGMENT]
    ).warnings


def test_title_text_is_data_not_syntax(rules: tuple[object, ...]) -> None:
    hostile = "feat: $(touch /tmp/pwned) `id` ; rm -rf / \"quoted\" 'single'"
    assert cf.decide_required(hostile, SHIPPED, rules).required


@requires_project_parser
def test_manifest_loader_rejects_unknown_kinds_and_empty_files(tmp_path: Path) -> None:
    bad = tmp_path / "bad.txt"
    bad.write_text("glob\tbenchbox/**\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown rule kind"):
        cf.load_rules(bad)
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing\n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        cf.load_rules(empty)


CHANGELOG = """# Changelog

## [Unreleased]

## [1.1.0] - 2026-01-02

### Fixed

- Fixed a typo in the readme.

## [1.0.0] - 2026-01-01

### Added

- First release.

[Unreleased]: https://example.com/compare/v1.1.0...HEAD
"""


def test_unreleased_guard_passes_for_an_empty_section() -> None:
    assert cf.unreleased_violations(CHANGELOG) == []


def test_unreleased_guard_fails_when_a_line_is_added_under_unreleased() -> None:
    edited = CHANGELOG.replace("## [Unreleased]\n", "## [Unreleased]\n\n### Added\n\n- New thing.\n", 1)
    problems = cf.unreleased_violations(edited)
    assert len(problems) == 1
    assert "line 5" in problems[0] or "line 4" in problems[0]
    assert "[Unreleased]" in problems[0]


def test_unreleased_guard_ignores_edits_inside_released_sections() -> None:
    edited = CHANGELOG.replace("Fixed a typo in the readme.", "Fixed a typo in the README file.")
    assert edited != CHANGELOG
    assert cf.unreleased_violations(edited) == []


def test_unreleased_guard_fails_closed_without_the_heading() -> None:
    problems = cf.unreleased_violations("# Changelog\n\n## [1.0.0] - 2026-01-01\n")
    assert problems and "[Unreleased]" in problems[0]


def test_unreleased_guard_counts_text_when_unreleased_is_the_last_section() -> None:
    assert cf.unreleased_violations("# C\n\n## [Unreleased]\n\n- stray\n")
    assert cf.unreleased_violations("# C\n\n## [Unreleased]\n\n\n") == []


def _merge_tree(repo: Path, left: str, right: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "merge-tree", "--write-tree", left, right], cwd=repo, capture_output=True, text=True, check=False
    )


def _bare_repo_with_base(tmp_path: Path) -> tuple[Path, Path]:
    work = tmp_path / "work"
    _init_repo(work)
    (work / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    (work / "README.md").write_text("base\n", encoding="utf-8")
    _commit(work, "base")
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True, capture_output=True)
    return work, bare


def test_distinct_fragments_merge_cleanly_in_a_bare_repository(tmp_path: Path) -> None:
    work, bare = _bare_repo_with_base(tmp_path)
    for branch, name, text in (
        ("feat/one", "feat-one.json", "Added the first independent capability to the runner."),
        ("feat/two", "feat-two.json", "Added the second independent capability to the runner."),
    ):
        _git(work, "checkout", "-q", "-b", branch, "main")
        _write(work / "changelog.d", name, _doc(entries=[_entry(text=text)]))
        _commit(work, f"feat: {branch} (#1)")
        _git(work, "push", "-q", str(bare), branch)
    result = _merge_tree(bare, "feat/one", "feat/two")
    assert result.returncode == 0, result.stdout + result.stderr
    tree = result.stdout.splitlines()[0]
    listing = _git(bare, "ls-tree", "-r", "--name-only", tree).splitlines()
    assert "changelog.d/feat-one.json" in listing
    assert "changelog.d/feat-two.json" in listing


def test_two_branches_appending_to_one_shared_file_conflict(tmp_path: Path) -> None:
    work, bare = _bare_repo_with_base(tmp_path)
    for branch, line in (("feat/one", "- First change."), ("feat/two", "- Second change.")):
        _git(work, "checkout", "-q", "-b", branch, "main")
        text = CHANGELOG.replace("## [Unreleased]\n", f"## [Unreleased]\n\n{line}\n", 1)
        (work / "CHANGELOG.md").write_text(text, encoding="utf-8")
        _commit(work, f"feat: {branch} (#1)")
        _git(work, "push", "-q", str(bare), branch)
    result = _merge_tree(bare, "feat/one", "feat/two")
    assert result.returncode == 1
    assert "CHANGELOG.md" in result.stdout


def _run_cli(root: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--root", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
    )


def test_check_passes_without_a_fragment_directory_and_flags_invalid_files(tmp_path: Path) -> None:
    assert _run_cli(tmp_path, "check").returncode == 0
    _write(tmp_path / "changelog.d", "good.json", _doc())
    assert _run_cli(tmp_path, "check").returncode == 0
    bad = _write(tmp_path / "changelog.d", "bad.json", {"schema": 3})
    result = _run_cli(tmp_path, "check")
    assert result.returncode == 1
    assert bad.as_posix() in result.stderr


def test_check_can_enforce_an_empty_unreleased_section(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    ok = _run_cli(tmp_path, "check", "--require-empty-unreleased", "--changelog", str(changelog))
    assert ok.returncode == 0
    changelog.write_text(CHANGELOG.replace("## [Unreleased]\n", "## [Unreleased]\n\n- stray\n", 1), encoding="utf-8")
    bad = _run_cli(tmp_path, "check", "--require-empty-unreleased", "--changelog", str(changelog))
    assert bad.returncode == 1
    assert "[Unreleased]" in bad.stderr


def test_add_creates_then_extends_one_fragment_and_never_writes_a_pr_number(tmp_path: Path) -> None:
    env = {
        "CHANGELOG_CATEGORY": "fixed",
        "CHANGELOG_TEXT": 'Fixed `$(rm -rf /)` handling in "quoted" result names.',
    }
    first = _run_cli(tmp_path, "add", "--name", "fix-names.json", env=env)
    assert first.returncode == 0, first.stderr
    second = _run_cli(
        tmp_path, "add", "--name", "fix-names.json", env={**env, "CHANGELOG_PLATFORMS": "duckdb,snowflake"}
    )
    assert second.returncode == 0, second.stderr
    path = tmp_path / "changelog.d" / "fix-names.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema"] == 1
    assert len(data["entries"]) == 2
    assert data["entries"][0]["text"] == env["CHANGELOG_TEXT"]
    assert data["entries"][1]["platforms"] == ["duckdb", "snowflake"]
    assert "#" not in path.read_text(encoding="utf-8")
    assert _run_cli(tmp_path, "check").returncode == 0


def test_add_refuses_invalid_input_and_writes_nothing(tmp_path: Path) -> None:
    result = _run_cli(tmp_path, "add", "--name", "x.json", "--category", "fixed", "--text", "too short")
    assert result.returncode == 1
    assert "text" in result.stderr
    assert not (tmp_path / "changelog.d").exists()
    missing = _run_cli(tmp_path, "add", "--name", "x.json")
    assert missing.returncode == 2
    assert not (tmp_path / "changelog.d").exists()


def test_add_internal_writes_an_internal_fragment_and_refuses_to_overwrite(tmp_path: Path) -> None:
    ok = _run_cli(tmp_path, "add", "--name", "chore-x.json", "--internal", "Refactored internals only.")
    assert ok.returncode == 0, ok.stderr
    assert json.loads((tmp_path / "changelog.d" / "chore-x.json").read_text(encoding="utf-8")) == {
        "schema": 1,
        "internal": "Refactored internals only.",
    }
    again = _run_cli(tmp_path, "add", "--name", "chore-x.json", "--internal", "Refactored internals only.")
    assert again.returncode == 1


def test_add_caps_a_fragment_at_five_entries(tmp_path: Path) -> None:
    args = ("add", "--name", "many.json", "--category", "added", "--text", TEXT)
    for _ in range(5):
        assert _run_cli(tmp_path, *args).returncode == 0
    over = _run_cli(tmp_path, *args)
    assert over.returncode == 1
    assert len(json.loads((tmp_path / "changelog.d" / "many.json").read_text(encoding="utf-8"))["entries"]) == 5


def test_fragment_name_derives_from_branch() -> None:
    assert cf.fragment_name("feat/changelog-fragment-library") == "feat-changelog-fragment-library.json"
    assert cf.fragment_name("Fix/Mixed_Case.x") == "fix-mixed_case.x.json"
    with pytest.raises(ValueError, match="--name"):
        cf.fragment_name("feat/bad name!")


def test_preview_renders_markdown_and_prints_pr_numbers_on_stderr(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write(tmp_path / "changelog.d", "fix-a.json", _doc(entries=[_entry(category="fixed")]))
    _commit(tmp_path, "fix: repair (#77)")
    result = _run_cli(tmp_path, "preview")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("### Fixed\n")
    assert "#77" not in result.stdout
    assert "changelog.d/fix-a.json  #77" in result.stderr


@requires_project_parser
def test_required_cli_exit_codes_and_manifest_failure(tmp_path: Path) -> None:
    manifest = tmp_path / "paths.txt"
    manifest.write_text("prefix\tbenchbox/\n", encoding="utf-8")
    base = ("required", "--manifest", str(manifest), "--title", 'feat: $(echo hi) "x"')
    blocked = _run_cli(tmp_path, *base, "--changed-file", "benchbox/a.py")
    assert blocked.returncode == 1
    assert "changelog entry required" in blocked.stdout
    allowed = _run_cli(tmp_path, *base, "--changed-file", "benchbox/a.py", "--changed-file", "changelog.d/x.json")
    assert allowed.returncode == 0
    exempt = _run_cli(tmp_path, *base, "--changed-file", "docs/a.md")
    assert exempt.returncode == 0
    missing = _run_cli(tmp_path, "required", "--manifest", str(tmp_path / "absent.txt"), "--title", "feat: x y")
    assert missing.returncode == 2


def test_required_fails_closed_when_the_manifest_parser_is_unavailable(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(SCRIPT_PATH, scripts / "changelog_fragments.py")
    manifest = tmp_path / "paths.txt"
    manifest.write_text("prefix\tbenchbox/\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(scripts / "changelog_fragments.py"), "required", "--manifest", str(manifest)]
        + ["--title", "feat: add a thing", "--changed-file", "docs/a.md"],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )
    assert result.returncode == 2
    assert "parser unavailable" in result.stderr


@pytest.mark.parametrize(
    "name", ["../../x.json", "Upper.json", "sub/x.json", "/tmp/x.json", ".hidden.json", "x.txt", ""]
)
def test_add_rejects_unsafe_fragment_names_and_writes_nothing(tmp_path: Path, name: str) -> None:
    result = _run_cli(
        tmp_path, "add", "--name", name, "--category", "added", "--text", TEXT, env={"CHANGELOG_FRAGMENT": name}
    )
    assert result.returncode == 2
    assert "fragment name" in result.stderr or "fragment-name" in result.stderr or "branch" in result.stderr
    assert not (tmp_path / "changelog.d").exists()
    assert not (tmp_path.parent / "x.json").exists()


def test_dot_files_never_satisfy_the_gate_and_listing_agrees(tmp_path: Path, rules: tuple[object, ...]) -> None:
    _write(tmp_path / "changelog.d", ".x.json", _doc())
    _write(tmp_path / "changelog.d", "real.json", _doc())
    assert [path.name for path in cf.fragment_files(tmp_path / "changelog.d")] == ["real.json"]
    assert not cf.is_fragment_path("changelog.d/.x.json")
    assert not cf.decide_required("fix: repair", [*SHIPPED, "changelog.d/.x.json"], rules).satisfied
    assert cf.decide_required("fix: repair", [*SHIPPED, "changelog.d/real.json"], rules).satisfied


def test_required_with_missing_manifest_fails_closed_and_names_the_flag(tmp_path: Path) -> None:
    missing = tmp_path / "absent.txt"
    result = _run_cli(tmp_path, "required", "--manifest", str(missing), "--title", "feat: add a thing")
    assert result.returncode == 2
    assert missing.as_posix() in result.stderr
    assert "--manifest" in result.stderr


@requires_project_parser
def test_shipped_manifest_loads_and_classifies_paths_through_the_real_loader() -> None:
    shipped = cf.load_rules()
    assert cf.matches_manifest("benchbox/core/runner.py", shipped)
    assert cf.matches_manifest("_sources/tpch/dbgen.c", shipped)
    assert cf.matches_manifest("pyproject.toml", shipped)
    assert not cf.matches_manifest("benchbox/utils/VERSION_MANAGEMENT.md", shipped)
    assert not cf.matches_manifest("tests/unit/test_x.py", shipped)
    assert not cf.matches_manifest(".github/workflows/ci.yml", shipped)
    assert cf.decide_required("fix: repair", ["benchbox/core/runner.py"], shipped).required
    assert not cf.decide_required("fix: repair", ["docs/a.md"], shipped).required
