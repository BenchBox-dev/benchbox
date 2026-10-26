from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/docs_history_filter.py"
pytestmark = [pytest.mark.unit, pytest.mark.fast]

PRIVACY_TS = """
const PATTERNS: { description: string; pattern: RegExp }[] = [
  { description: "GitHub Personal Access Token", pattern: /ghp_[0-9a-zA-Z]{36}/g },
  { description: "GitHub Fine-grained Personal Access Token", pattern: /github_pat_[0-9a-zA-Z_]{82}/g },
  { description: "Private Key", pattern: /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/g },
  { description: "AWS Access Key ID", pattern: /AKIA[0-9A-Z]{16}/g },
];
const AWS_EXAMPLES = new Set(["AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"]);
const CONNECTION = /(?:postgresql|postgres|mysql):\\/\\/([^\\s/:@]+):([^\\s/@]+)@([^\\s/@]+)/g;
const PLACEHOLDER_PASSWORDS = new Set(["password", "pass", "your-password", "your_password", "changeme", "***", "<password>"]);
"""


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("docs_history_filter", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=env)
    return done.stdout.strip()


def test_strip_message_removes_session_metadata_only(tool):
    message = (
        b"docs: explain\n\nBody line.\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
        b"Claude-Session: https://claude.ai/code/session_01AbC\nhttps://claude.ai/code/session_01AbC\n"
    )
    assert tool.strip_message(message) == (
        b"docs: explain\n\nBody line.\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
    )


def test_strip_message_keeps_prose_around_inline_url(tool):
    assert tool.strip_message(b"see https://claude.ai/code/session_x1 for more\n") == b"see  for more\n"


def test_strip_message_is_identity_without_session_metadata(tool):
    message = b"fix: thing\n\nNo trailing newline"
    assert tool.strip_message(message) == message


@pytest.mark.parametrize(
    "path",
    [
        "docs/a.py",
        "docs/pyproject.toml",
        "docs/uv.lock",
        "docs/key.pem",
        "docs/blog/post.md",
        "docs/CNAME",
    ],
)
def test_prohibited_paths(tool, path):
    assert tool.is_prohibited(path)


def test_generated_query_root_stays_prohibited(tool):
    assert tool.is_prohibited(tool.QUERY_ROOT + "q1.md")


def test_site_owned_markdown_is_not_prohibited(tool):
    assert not tool.is_prohibited("docs/guides/page.md")


def test_privacy_rules_are_read_from_policy_text(tool):
    rules = tool.load_privacy_rules(PRIVACY_TS)
    assert tool.privacy_findings("AKIAIOSFODNN7EXAMPLE", rules) == []
    assert tool.privacy_findings("postgres://user:password@host/db", rules) == []
    assert tool.privacy_findings("AKIA" + "A" * 16, rules) == ["AWS Access Key ID x1"]
    assert tool.privacy_findings("mysql://user:hunter2@host/db", rules) == [
        "Database connection string with credentials x1"
    ]
    header = "-----BEGIN " + "RSA PRIVATE" + " KEY-----"
    assert tool.privacy_findings(header, rules) == ["Private Key x1"]


def test_privacy_rules_reject_unparseable_policy(tool):
    with pytest.raises(ValueError):
        tool.load_privacy_rules("const PATTERNS = [];")


def rule_view(rules):
    return (
        [(description, pattern.pattern) for description, pattern in rules["patterns"]],
        rules["aws_examples"],
        rules["connection"].pattern,
        rules["placeholder_passwords"],
    )


def test_privacy_rules_match_site_policy_file(tool):
    site = Path(os.environ.get("BENCHBOX_SITE_DIR", ROOT.parent / "benchbox-site"))
    policy = site / "deploy/gates/privacy.ts"
    if not policy.is_file():
        pytest.skip("site checkout not available")
    assert rule_view(tool.load_privacy_rules(policy.read_text(encoding="utf-8"))) == rule_view(
        tool.load_privacy_rules(PRIVACY_TS)
    )


def init_repo(path: Path, branch: str) -> None:
    path.mkdir()
    git(path, "init", "-q", "-b", branch)
    git(path, "config", "user.name", "Human")
    git(path, "config", "user.email", "human@example.com")


def test_scan_blobs_flags_blobs_once_and_ignores_trees(tool, tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo, "main")
    (repo / "docs").mkdir()
    (repo / "docs/a.md").write_text("AKIA" + "B" * 16 + "\n", encoding="utf-8")
    (repo / "docs/b.md").write_text("clean\n", encoding="utf-8")
    git(repo, "add", "docs/a.md", "docs/b.md")
    git(repo, "commit", "-q", "-m", "x")
    findings = tool.scan_blobs(repo, tool.load_privacy_rules(PRIVACY_TS))
    assert [(f["paths"], f["findings"]) for f in findings] == [(["docs/a.md"], ["AWS Access Key ID x1"])]


def test_verify_lineage_detects_lost_rename_history(tool, tmp_path):
    body = "".join(f"line {i}\n" for i in range(40))
    source = tmp_path / "source"
    init_repo(source, "develop")
    (source / "docs").mkdir()
    (source / "docs/old.rst").write_text(body, encoding="utf-8")
    git(source, "add", "docs/old.rst")
    git(source, "commit", "-q", "-m", "add")
    git(source, "mv", "docs/old.rst", "docs/new.md")
    git(source, "commit", "-q", "-m", "rename")
    sha = git(source, "rev-parse", "HEAD")

    lossy = tmp_path / "lossy"
    init_repo(lossy, "docs-history-rehearsal")
    (lossy / "docs").mkdir()
    (lossy / "docs/new.md").write_text(body, encoding="utf-8")
    git(lossy, "add", "docs/new.md")
    git(lossy, "commit", "-q", "-m", "rename")

    with pytest.raises(SystemExit) as lost:
        tool.verify_lineage(source, sha, lossy, {"docs/new.md": "unused"}, {"docs/new.md"})
    assert "rename lineage lost" in str(lost.value)
    assert "followed names not kept" in str(lost.value)
    assert "2 followed commits in source, 1 after filtering" in str(lost.value)


def test_split_ownership_excludes_generated_outputs(tool):
    manifest = {
        "files": {"docs/a.md": "site-owned", "docs/b.md": "core-only", "docs/c.md": "core-generated"},
        "generated": [{"path": "docs/generated/queries/"}],
    }
    owned, excluded = tool.split_ownership(manifest)
    assert owned == {"docs/a.md"}
    assert excluded == {"docs/b.md", "docs/c.md", "docs/generated/queries/"}


@pytest.mark.skipif(shutil.which("git-filter-repo") is None, reason="git-filter-repo not installed")
def test_rehearsal_follows_renames_and_strips_forbidden_content(tool, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q", "-b", "develop")
    git(source, "config", "user.name", "Human")
    git(source, "config", "user.email", "human@example.com")

    def commit(message: str, files: dict[str, str], remove: tuple[str, ...] = ()) -> None:
        for name, content in files.items():
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            git(source, "add", name)
        for name in remove:
            git(source, "rm", "-q", name)
        git(source, "commit", "-q", "-m", message)

    body = "".join(f"line {i}\n" for i in range(40))
    commit(
        "add old\n\nClaude-Session: https://claude.ai/code/session_zz\nCo-authored-by: Pat <pat@example.com>",
        {"docs/old.rst": body, "docs/blog/p.md": "post\n", "docs/tool.py": "x = 1\n"},
    )
    git(source, "mv", "docs/old.rst", "docs/new.md")
    git(source, "commit", "-q", "-m", "rename")
    commit("core only", {"docs/internal.md": "core\n", "scripts/build.py": "y = 2\n"})
    manifest = {
        "files": {"docs/new.md": "site-owned", "docs/internal.md": "core-only"},
        "generated": [],
    }
    commit("ownership", {"quality/docs-ownership.json": json.dumps(manifest)})
    sha = git(source, "rev-parse", "HEAD")
    policy = tmp_path / "privacy.ts"
    policy.write_text(PRIVACY_TS, encoding="utf-8")

    work = tmp_path / "work"
    code = tool.main(
        [
            "rehearse",
            "--source",
            str(source),
            "--sha",
            sha,
            "--work",
            str(work),
            "--privacy-ts",
            str(policy),
            "--skip-gitleaks",
        ]
    )
    receipt = json.loads((work / "receipt.json").read_text(encoding="utf-8"))
    clone = work / "repo"

    assert code == 0
    assert receipt["tip_equals_projection"] is True
    assert receipt["input_sha"] == sha
    assert receipt["lineage"] == {"paths_checked": 1, "paths_with_renames": 1}
    assert git(clone, "ls-tree", "-r", "--name-only", "HEAD").splitlines() == ["docs/new.md"]
    names = git(clone, "log", "--all", "--name-only", "--format=").split()
    assert sorted(set(names)) == ["docs/new.md", "docs/old.rst"]
    messages = git(clone, "log", "--all", "--format=%B")
    assert "session_zz" not in messages
    assert "Co-authored-by: Pat <pat@example.com>" in messages
