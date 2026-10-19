from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/check_docs_ownership.py"
pytestmark = [pytest.mark.unit, pytest.mark.fast]


def write_file(root: Path, path: str, content: str = "fixture\n") -> Path:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)


@pytest.fixture
def ownership_repo(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "--quiet")
    reference = json.loads((ROOT / "quality/docs-ownership.json").read_text(encoding="utf-8"))
    files = {
        "docs/README.md": "core-only",
        "docs/guides/README.md": "site-owned",
        "docs/guides/public.md": "site-owned",
        "docs/benchmarks/overview.md": "site-owned",
        "docs/internal/private.md": "core-only",
        "docs/agent/private.md": "core-only",
        "docs/development/private.md": "core-only",
    }
    for transition in reference["transitions"].values():
        for source in transition["sources"]:
            files[source] = "core-only"
    for record in reference["generated"]:
        write_file(root, record["generator"])
        if not record["path"].endswith("/"):
            files[record["path"]] = "core-generated"
    for source in files:
        write_file(root, source)
    write_file(root, "docs/development/public.md")
    files["docs/development/public.md"] = "site-owned"
    write_file(root, "docs/publish-allowlist.txt", "development/public.md\n")
    write_file(root, "docs/publish-exclusions.txt", "internal/\nagent/\n")
    write_file(root, ".gitignore", "docs/benchmarks/queries/\n")
    write_file(root, "scripts/update_version.py", "DOC_RELEASE_PATTERN = 'fixture'\n")
    generators = sorted({record["generator"] for record in reference["generated"]})
    git(root, "add", ".gitignore", "scripts/update_version.py", *sorted(files), *generators)
    manifest = {
        "schema": 1,
        "files": dict(sorted(files.items())),
        **{key: reference[key] for key in ("generated", "transitions")},
    }
    return root, manifest


def run_cli(root: Path, manifest: dict[str, Any] | str, custom_path: bool = False) -> subprocess.CompletedProcess[str]:
    manifest_path = "quality/custom.json" if custom_path else "quality/docs-ownership.json"
    write_file(root, manifest_path, manifest if isinstance(manifest, str) else json.dumps(manifest))
    command = [sys.executable, str(SCRIPT), "--root", str(root)]
    if custom_path:
        command.extend(["--manifest", manifest_path])
    return subprocess.run(command, cwd=root.parent, capture_output=True, text=True, check=False)


def assert_failure(result: subprocess.CompletedProcess[str], *fragments: str) -> None:
    assert result.returncode == 1
    assert result.stdout == ""
    error = json.loads(result.stderr)["error"]
    for fragment in fragments:
        assert fragment in error


@pytest.mark.parametrize("custom_path", [False, True])
def test_cli_accepts_complete_manifest_and_reports_measured_counts(
    ownership_repo: tuple[Path, dict[str, Any]], custom_path: bool
) -> None:
    root, manifest = ownership_repo
    write_file(root, "docs/benchmarks/queries/tpch/1.md")
    write_file(root, "docs/guides/untracked.md")
    result = run_cli(root, manifest, custom_path)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["tracked"] == len(manifest["files"])
    assert output["counts"] == {
        owner: list(manifest["files"].values()).count(owner) for owner in ("core-only", "core-generated", "site-owned")
    }


@pytest.mark.parametrize(
    "case,fragments",
    [
        ("missing", ("coverage mismatch", "docs/guides/public.md")),
        ("stale", ("coverage mismatch", "docs/guides/stale.md")),
        ("owner", ("unknown owner", "docs/guides/public.md")),
        ("published", ("ownership mismatch", "docs/guides/public.md")),
        ("private", ("ownership mismatch", "docs/internal/private.md")),
        ("generated", ("ownership mismatch", "docs/compat/capability-matrix.md")),
        ("landing-widened", ("ownership mismatch", "docs/README.md")),
    ],
)
def test_cli_rejects_incomplete_or_misclassified_ownership(
    ownership_repo: tuple[Path, dict[str, Any]], case: str, fragments: tuple[str, ...]
) -> None:
    root, manifest = ownership_repo
    files = manifest["files"]
    if case == "missing":
        del files["docs/guides/public.md"]
    elif case == "stale":
        files["docs/guides/stale.md"] = "site-owned"
    elif case == "owner":
        files["docs/guides/public.md"] = "unknown"
    elif case == "published":
        files["docs/guides/public.md"] = "core-only"
    elif case == "private":
        files["docs/internal/private.md"] = "site-owned"
    elif case == "generated":
        files["docs/compat/capability-matrix.md"] = "site-owned"
    else:
        files["docs/README.md"] = "site-owned"
    assert_failure(run_cli(root, manifest), *fragments)


@pytest.mark.parametrize(
    "path,fragment",
    [
        ("docs/../outside.md", "unsafe repository path"),
        ("/docs/page.md", "unsafe repository path"),
        ("docs//page.md", "unsafe repository path"),
        ("docs/./page.md", "unsafe repository path"),
        ("docs\\page.md", "invalid repository path"),
    ],
)
def test_cli_rejects_unsafe_paths(ownership_repo: tuple[Path, dict[str, Any]], path: str, fragment: str) -> None:
    root, manifest = ownership_repo
    manifest["files"][path] = manifest["files"].pop("docs/guides/public.md")
    assert_failure(run_cli(root, manifest), fragment, path if "\\" not in path else "docs")


@pytest.mark.parametrize(
    "case,fragments",
    [
        ("duplicate-file", ("duplicate JSON key", "docs/guides/public.md")),
        ("duplicate-top-level", ("duplicate JSON key", "schema")),
        ("unknown-field", ("must contain exactly",)),
        ("schema", ("unsupported ownership schema",)),
        ("transition", ("missing=", "readme_landing")),
    ],
)
def test_cli_rejects_open_or_ambiguous_schema(
    ownership_repo: tuple[Path, dict[str, Any]], case: str, fragments: tuple[str, ...]
) -> None:
    root, manifest = ownership_repo
    if case == "duplicate-file":
        text = json.dumps(manifest).replace(
            '"docs/guides/public.md": "site-owned"',
            '"docs/guides/public.md": "core-only", "docs/guides/public.md": "site-owned"',
        )
    elif case == "duplicate-top-level":
        text = json.dumps(manifest).replace('"schema": 1', '"schema": 1, "schema": 1')
    else:
        if case == "unknown-field":
            manifest["extra"] = True
        elif case == "schema":
            manifest["schema"] = True
        else:
            del manifest["transitions"]["readme_landing"]
        text = json.dumps(manifest)
    assert_failure(run_cli(root, text), *fragments)


@pytest.mark.parametrize("case", ["missing-doc", "missing-generator", "untracked-generator", "escaped-source"])
def test_cli_rejects_missing_or_unsafe_sources(ownership_repo: tuple[Path, dict[str, Any]], case: str) -> None:
    root, manifest = ownership_repo
    if case == "missing-doc":
        (root / "docs/guides/public.md").unlink()
    elif case == "missing-generator":
        (root / "scripts/generate_query_docs.py").unlink()
    elif case == "untracked-generator":
        git(root, "rm", "--cached", "scripts/generate_query_docs.py")
    else:
        source = root / "docs/guides/public.md"
        source.unlink()
        outside = write_file(root.parent, "outside.md")
        source.symlink_to(outside)
    assert_failure(run_cli(root, manifest), "missing, untracked or unsafe source")


@pytest.mark.parametrize(
    "case,fragments",
    [
        ("broadened-root", ("contains tracked authored source", "docs/benchmarks/")),
        ("wrong-generator", ("must map", "docs/benchmarks/queries/")),
        ("duplicate", ("duplicate generated output",)),
        ("extra-field", ("exactly path and generator",)),
        ("tracked-query", ("contains tracked authored source", "docs/benchmarks/queries/authored.md")),
        ("unignored-query", ("must be git-ignored",)),
        ("unsafe-generator", ("unsafe repository path",)),
    ],
)
def test_cli_rejects_invalid_generated_boundaries(
    ownership_repo: tuple[Path, dict[str, Any]], case: str, fragments: tuple[str, ...]
) -> None:
    root, manifest = ownership_repo
    query = next(record for record in manifest["generated"] if record["path"].endswith("/"))
    if case == "broadened-root":
        query["path"] = "docs/benchmarks/"
    elif case == "wrong-generator":
        query["generator"] = "scripts/generate_compat_docs.py"
    elif case == "duplicate":
        manifest["generated"].append(dict(query))
    elif case == "extra-field":
        query["sources"] = ["docs/guides/public.md"]
    elif case == "tracked-query":
        path = "docs/benchmarks/queries/authored.md"
        write_file(root, path)
        git(root, "add", "--force", path)
        manifest["files"][path] = "core-generated"
    elif case == "unignored-query":
        write_file(root, ".gitignore", "")
    else:
        query["generator"] = "scripts/../outside.py"
    assert_failure(run_cli(root, manifest), *fragments)


@pytest.mark.parametrize(
    "case,fragments",
    [
        ("symbol", ("is not defined in", "scripts/update_version.py")),
        ("source", ("missing, untracked or unsafe source", "scripts/missing.py")),
    ],
)
def test_cli_rejects_stale_marker_selector(
    ownership_repo: tuple[Path, dict[str, Any]], case: str, fragments: tuple[str, ...]
) -> None:
    root, manifest = ownership_repo
    selector = manifest["transitions"]["readme_landing"]["marker_selector"]
    if case == "symbol":
        selector["symbol"] = "MISSING_PATTERN"
    else:
        selector["source"] = "scripts/missing.py"
    assert_failure(run_cli(root, manifest), *fragments)
