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


def _generated(reference: dict[str, Any]) -> list[dict[str, str]]:
    return [dict(record) for record in reference["generated"]]


def _transitions(reference: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(reference["transitions"]))


@pytest.fixture
def ownership_repo(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "--quiet")
    reference = json.loads((ROOT / "quality/docs-ownership.json").read_text(encoding="utf-8"))
    generated = _generated(reference)
    file_outputs = sorted(record["path"] for record in generated if not record["path"].endswith("/"))
    files = {
        "docs/README.md": "core-only",
        "docs/internal/kept.md": "core-only",
        "docs/operations/local-platform-provisioning.tsv": "core-only",
        "docs/publish-allowlist.txt": "core-only",
        "docs/publish-exclusions.txt": "core-only",
        "docs/guides/moved.md": "site-owned",
        file_outputs[0]: "core-generated",
    }
    for source in files:
        write_file(root, source)
    sql_destination = reference["transitions"]["sql_contract"]["core_destination"]
    write_file(root, sql_destination, "CREATE TABLE test;\n")
    write_file(root, "scripts/update_version.py", "DOC_RELEASE_PATTERN = 'fixture'\n")
    tracked = [source for source, owner in files.items() if owner == "core-only"]
    git(root, "add", *sorted(tracked), sql_destination, "scripts/update_version.py")
    manifest = {
        "schema": 1,
        "files": dict(sorted(files.items())),
        "generated": generated,
        "transitions": _transitions(reference),
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


def directory_output(manifest: dict[str, Any]) -> dict[str, str]:
    return next(record for record in manifest["generated"] if record["path"].endswith("/"))


@pytest.mark.parametrize("custom_path", [False, True])
def test_cli_accepts_complete_manifest_and_reports_measured_counts(
    ownership_repo: tuple[Path, dict[str, Any]], custom_path: bool
) -> None:
    root, manifest = ownership_repo
    write_file(root, directory_output(manifest)["path"] + "untracked.md")
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
        ("missing", ("missing, untracked or unsafe source", "docs/internal/kept.md")),
        ("stale", ("coverage mismatch", "docs/internal/kept.md")),
        ("owner", ("unknown owner", "docs/guides/moved.md")),
        ("generated", ("ownership mismatch", "expected 'core-generated'")),
        ("landing-widened", ("is still tracked", "docs/README.md")),
    ],
)
def test_cli_rejects_incomplete_or_misclassified_ownership(
    ownership_repo: tuple[Path, dict[str, Any]], case: str, fragments: tuple[str, ...]
) -> None:
    root, manifest = ownership_repo
    files = manifest["files"]
    if case == "missing":
        (root / "docs/internal/kept.md").unlink()
    elif case == "stale":
        del files["docs/internal/kept.md"]
    elif case == "owner":
        files["docs/guides/moved.md"] = "unknown"
    elif case == "generated":
        generated_path = next(path for path, owner in files.items() if owner == "core-generated")
        files[generated_path] = "site-owned"
    else:
        files["docs/README.md"] = "site-owned"
    assert_failure(run_cli(root, manifest), *fragments)


def test_cli_rejects_site_owned_page_left_in_the_tree(ownership_repo: tuple[Path, dict[str, Any]]) -> None:
    root, manifest = ownership_repo
    git(root, "add", "docs/guides/moved.md")
    assert_failure(run_cli(root, manifest), "site-owned path", "must be absent from the core tree")


def test_cli_rejects_relocated_source_left_in_the_tree(ownership_repo: tuple[Path, dict[str, Any]]) -> None:
    root, manifest = ownership_repo
    source = manifest["transitions"]["sql_contract"]["sources"][0]
    write_file(root, source)
    git(root, "add", source)
    assert_failure(run_cli(root, manifest), "is still tracked", source)


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
    manifest["files"][path] = manifest["files"].pop("docs/guides/moved.md")
    assert_failure(run_cli(root, manifest), fragment, path if "\\" not in path else "docs")


@pytest.mark.parametrize(
    "case,fragments",
    [
        ("duplicate-file", ("duplicate JSON key", "docs/guides/moved.md")),
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
            '"docs/guides/moved.md": "site-owned"',
            '"docs/guides/moved.md": "core-only", "docs/guides/moved.md": "site-owned"',
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


@pytest.mark.parametrize(
    "case,fragments",
    [
        ("tracked-generator", ("must be absent from the core tree",)),
        ("tracked-output", ("must be absent from the core tree", "tracked")),
        ("duplicate", ("duplicate generated output",)),
        ("extra-field", ("exactly path and generator",)),
        ("overlap", ("generated outputs overlap",)),
        ("no-directory", ("must catalogue the relocated generated directory",)),
        ("unsafe-generator", ("unsafe repository path",)),
    ],
)
def test_cli_rejects_invalid_generated_boundaries(
    ownership_repo: tuple[Path, dict[str, Any]], case: str, fragments: tuple[str, ...]
) -> None:
    root, manifest = ownership_repo
    query = directory_output(manifest)
    if case == "tracked-generator":
        write_file(root, query["generator"])
        git(root, "add", query["generator"])
    elif case == "tracked-output":
        output = query["path"] + "authored.md"
        write_file(root, output)
        git(root, "add", "--force", output)
    elif case == "duplicate":
        manifest["generated"].append(dict(query))
    elif case == "extra-field":
        query["sources"] = ["docs/guides/moved.md"]
    elif case == "overlap":
        file_record = next(record for record in manifest["generated"] if not record["path"].endswith("/"))
        file_record["path"] = query["path"] + "nested.md"
    elif case == "no-directory":
        for record in manifest["generated"]:
            if record["path"].endswith("/"):
                record["path"] = record["path"] + "index.md"
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


@pytest.mark.parametrize(
    "case,fragments",
    [
        ("missing-file", ("missing, untracked or unsafe source", "docs/operations/missing.tsv")),
        ("empty-directory", ("holds no tracked files", "docs/operations/empty/")),
    ],
)
def test_cli_rejects_missing_core_destination(
    ownership_repo: tuple[Path, dict[str, Any]], case: str, fragments: tuple[str, ...]
) -> None:
    root, manifest = ownership_repo
    if case == "missing-file":
        manifest["transitions"]["operations_inventory"]["core_destination"] = "docs/operations/missing.tsv"
    else:
        manifest["transitions"]["operations_inventory"]["core_destination"] = "docs/operations/empty/"
    assert_failure(run_cli(root, manifest), *fragments)


def test_cli_handles_completed_contract_relocation(ownership_repo: tuple[Path, dict[str, Any]]) -> None:
    root, manifest = ownership_repo
    transition = manifest["transitions"]["sql_contract"]
    result = run_cli(root, manifest)
    assert result.returncode == 0, result.stderr

    write_file(root, transition["sources"][0], "stale source\n")
    git(root, "add", transition["sources"][0])
    assert_failure(run_cli(root, manifest), "is still tracked", transition["sources"][0])
