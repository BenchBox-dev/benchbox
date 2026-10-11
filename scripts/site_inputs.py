#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA = 3
PARENT_SOURCES = ("bundle", "dispatch", "local")
GENERATED_QUERIES = Path("docs/benchmarks/queries")


def run(*cmd: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(c) for c in cmd], cwd=cwd, check=False, text=True, capture_output=True)


def must_run(*cmd: str, cwd: Path = ROOT) -> str:
    proc = run(*cmd, cwd=cwd)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed ({proc.returncode}): {proc.stderr[-2000:]}")
    return proc.stdout


def git_ls_files() -> list[str]:
    return must_run("git", "-C", str(ROOT), "ls-files").splitlines()


def tree_sha(ref: str, subdir: str) -> str:
    return must_run("git", "-C", str(ROOT), "rev-parse", f"{ref}:{subdir}").strip()


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


REQUIRED_MEMBERS = (
    "docs",
    "repo-files.json",
    "explorer/results.duckdb",
    "explorer/contract.json",
    "explorer/fixtures",
    "explorer/parity",
    "explorer/bundles",
    "landing/prompt-catalog.json",
    "api-public-symbols.json",
    "downloads",
    "attestations.json",
)
REQUIRED_ATTESTATIONS = (
    "privacy",
    "explorer_compat",
    "snapshot_invariants",
    "corpus_bijection",
)


def member_digest(root: Path) -> str:
    if root.is_file():
        return file_sha(root)
    if not root.is_dir():
        raise FileNotFoundError(f"bundle member missing: {root}")
    lines = [f"{p.relative_to(root)}:{file_sha(p)}" for p in sorted(root.rglob("*")) if p.is_file()]
    return hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest()


def package_version() -> str:
    with open(ROOT / "pyproject.toml", "rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def build_docs(out_docs: Path) -> None:
    must_run("uv", "run", "--", "python", "scripts/generate_query_docs.py")
    must_run("uv", "run", "--", "python", "scripts/generate_compat_docs.py")
    wanted: set[str] = set()
    for path in git_ls_files():
        if not path.startswith("docs/"):
            continue
        if path.startswith("docs/blog/") or path == "docs/CNAME":
            continue
        wanted.add(path)
    queries = ROOT / GENERATED_QUERIES
    if queries.is_dir():
        for path in sorted(queries.rglob("*")):
            if path.is_file():
                wanted.add(str(path.relative_to(ROOT)))
    for rel in sorted(wanted):
        src = ROOT / rel
        dst = out_docs / Path(rel).relative_to("docs")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def build_repo_files(out: Path, core_sha: str) -> None:
    entries = [{"path": p, "kind": "file"} for p in git_ls_files()]
    dirs = sorted({str(Path(p).parent) for p in git_ls_files() if "/" in p})
    entries += [{"path": d, "kind": "tree"} for d in dirs]
    out.write_text(json.dumps({"core_sha": core_sha, "files": entries}, indent=2) + "\n", encoding="utf-8")


def build_explorer_snapshot(out_explorer: Path) -> Path:
    must_run(
        "uv",
        "run",
        "--",
        "python",
        "_project/scripts/explorer_publish.py",
        "build",
        "--data-dir",
        "results-data/",
        "--output",
        str(out_explorer),
    )
    db = out_explorer / "results.duckdb"
    if not db.is_file() or db.stat().st_size == 0:
        raise RuntimeError("explorer snapshot build produced no results.duckdb")
    bundles = out_explorer / "bundles"
    if not bundles.is_dir() or not any(bundles.glob("*.json")):
        raise RuntimeError("explorer snapshot build produced no per-result bundles")
    return db


def build_contract(out: Path) -> None:
    from _project.scripts.explorer_pipeline.contract import (
        EXPLORER_BUILD_CONTRACT,
        EXPLORER_BUILD_CONTRACT_VERSION,
        EXPLORER_READ_MODEL_VERSION,
    )
    from benchbox.core.tuning.modes import MODES

    contract = {
        "build_contract": EXPLORER_BUILD_CONTRACT,
        "snapshot_schema_version": EXPLORER_BUILD_CONTRACT_VERSION,
        "read_model_version": EXPLORER_READ_MODEL_VERSION,
        "tuning_vocabulary": list(MODES),
        "browser_duckdb_schema_sql": (ROOT / "docs/development/browser-duckdb-schema.sql").read_text(encoding="utf-8"),
    }
    out.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")


def _read_fixture_ids(data: Path) -> dict:
    fixture_ids = data / "fixture-ids.json"
    if not fixture_ids.is_file():
        raise RuntimeError("fixture generation produced no fixture-ids.json")
    payload = json.loads(fixture_ids.read_text(encoding="utf-8"))
    if sorted(payload) != ["ids", "shortIds"] or not payload["ids"]:
        raise RuntimeError(f"fixture-ids.json breaks the role-keyed contract: {sorted(payload)}")
    return payload


def _stage_fixtures(data: Path, out_fixtures: Path) -> None:
    db = data / "results.duckdb"
    if not db.is_file() or db.stat().st_size == 0:
        raise RuntimeError("fixture generation produced no results.duckdb")
    _read_fixture_ids(data)
    out_fixtures.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db, out_fixtures / "results.duckdb")
    bundles = data / "bundles"
    if bundles.is_dir():
        for bundle in sorted(bundles.glob("*.json")):
            shutil.copy2(bundle, out_fixtures / bundle.name)
    shutil.copy2(data / "fixture-ids.json", out_fixtures / "fixture-ids.json")


def build_fixtures(out_fixtures: Path) -> None:
    import os
    import tempfile

    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node is required to generate the browser fixture corpus")
    with tempfile.TemporaryDirectory(prefix="benchbox-large-browser-fixture-") as tmp_root:
        env = dict(os.environ, E2E_FIXTURE_OUTPUT_ROOT=tmp_root, E2E_FIXTURE_PROFILE="default")
        proc = subprocess.run(
            [node, "results-explorer/scripts/generate-browser-fixtures.mjs"],
            cwd=str(ROOT),
            env=env,
            check=False,
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"generate-browser-fixtures.mjs failed ({proc.returncode}): {(proc.stdout + proc.stderr)[-2000:]}"
            )
        _stage_fixtures(Path(tmp_root) / "data", out_fixtures)


def build_parity(out_parity: Path) -> None:
    src = ROOT / "tests/parity/fixtures"
    out_parity.mkdir(parents=True, exist_ok=True)
    for fixture in sorted(src.glob("*.json")):
        shutil.copy2(fixture, out_parity / fixture.name)


LINK_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
INLINE_LINK = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
REFERENCE_LINK = re.compile(r"^\s*\[[^\]]+\]:\s*<?([^\s>]+)>?", re.MULTILINE)
AUTOLINK = re.compile(r"<(\.{1,2}/[^>\s]+)>")


def link_targets(text: str) -> list[str]:
    targets = dict.fromkeys(INLINE_LINK.findall(text) + REFERENCE_LINK.findall(text) + AUTOLINK.findall(text))
    return [
        target for target in targets if target and not target.startswith(("#", "/")) and not LINK_SCHEME.match(target)
    ]


def referenced_downloads(docs_dir: Path, tracked: list[str]) -> list[str]:
    files = set(tracked)
    found: set[str] = set()
    for source in sorted(docs_dir.rglob("*.md")):
        page = "docs/" + source.relative_to(docs_dir).as_posix()
        text = source.read_text(encoding="utf-8", errors="replace")
        for target in link_targets(text):
            clean = unquote(target.split("#", 1)[0].split("?", 1)[0])
            if not clean:
                continue
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(page), clean))
            if resolved.startswith(("docs/", "../")) or resolved in ("docs", ".", ".."):
                continue
            if resolved in files and not resolved.endswith((".md", ".rst")):
                found.add(resolved)
                if resolved.startswith("_sources/tpc-ds/query_templates/") and resolved.endswith(".tpl"):
                    found.add("_sources/tpc-ds/EULA.txt")
                elif resolved.startswith("benchbox/_binaries/tpc-h/templates/queries/") and resolved.endswith(".sql"):
                    found.add("_sources/tpc-h/EULA.txt")
    return sorted(found)


def build_downloads(out_downloads: Path, docs_dir: Path) -> list[str]:
    out_downloads.mkdir(parents=True, exist_ok=True)
    paths = referenced_downloads(docs_dir, git_ls_files())
    for rel in paths:
        target = out_downloads / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)
    return paths


def build_prompt_catalog(out: Path) -> None:
    from scripts.generate_landing_quickstarts import build_prompt_catalog, load_catalog

    catalog = load_catalog()
    out.write_text(json.dumps(build_prompt_catalog(catalog), sort_keys=True, indent=2) + "\n", encoding="utf-8")


def attestation(name: str, command: list[str], inputs: dict[str, str], compared: dict[str, str]) -> dict:
    proc = run(*command)
    tail = (proc.stdout + proc.stderr)[-2000:]
    if proc.returncode == 0:
        return {
            "name": name,
            "result": "pass",
            "command": command,
            "inputs": inputs,
            "compared": compared,
            "output_tail": tail,
        }
    return {
        "name": name,
        "result": "fail",
        "reason": f"exit {proc.returncode}",
        "command": command,
        "inputs": inputs,
        "compared": compared,
        "output_tail": tail,
    }


def build_attestations(out: Path, bundle: Path, core_sha: str, parent_sha: str) -> None:
    db = bundle / "explorer/results.duckdb"
    digest = member_digest(bundle)
    entries = []
    entries.append(
        attestation(
            "privacy",
            ["uv", "run", "--", "python", "scripts/publication/check_artifact_privacy.py", str(bundle)],
            {"bundle": digest},
            {"core_sha": core_sha},
        )
    )
    entries.append(
        attestation(
            "explorer_compat",
            [
                "uv",
                "run",
                "--",
                "python",
                "scripts/publication/check_explorer_compat.py",
                "--schema-only",
                "--db-path",
                str(db),
            ],
            {"snapshot": file_sha(db)},
            {"core_sha": core_sha},
        )
    )
    entries.append(
        attestation(
            "snapshot_invariants",
            ["uv", "run", "--", "python", "_project/scripts/results_explorer_snapshot_invariants.py", str(db)],
            {"snapshot": file_sha(db)},
            {"core_sha": core_sha},
        )
    )
    seed = ROOT / "publication/ledger-seed.json"
    if not seed.is_file():
        raise RuntimeError("ledger seed missing: re-home its dispositions before removing it")
    entries.append(
        attestation(
            "corpus_bijection",
            [
                "uv",
                "run",
                "--",
                "python",
                "scripts/publication/check_corpus_bijection.py",
                "--accepted-ref",
                core_sha,
                "--expect-source",
                core_sha,
                "--bundles-dir",
                str(ROOT / "results-data" / "bundles"),
                "--artifact",
                str(db),
                "--require-artifact",
                "--ledger-seed",
                str(seed),
            ],
            {
                "snapshot": file_sha(db),
                "bundles": tree_sha(core_sha, "results-data/bundles"),
                "ledger_seed": file_sha(seed),
            },
            {"accepted_ref": core_sha},
        )
    )
    if tree_sha(core_sha, "results-data") == tree_sha(parent_sha, "results-data"):
        entries.append(
            {
                "name": "validator_parity",
                "result": "skip",
                "reason": "corpus unchanged",
                "compared": {"base": parent_sha, "head": core_sha},
            }
        )
    else:
        entries.append(
            attestation(
                "validator_parity",
                [
                    "uv",
                    "run",
                    "--",
                    "python",
                    "scripts/publication/validator_parity.py",
                    "--base-sha",
                    parent_sha,
                    "--merge-sha",
                    core_sha,
                    "--head-sha",
                    core_sha,
                    "--allow-partial-validation",
                ],
                {
                    "corpus_base": tree_sha(parent_sha, "results-data"),
                    "corpus_head": tree_sha(core_sha, "results-data"),
                },
                {"base": parent_sha, "merge": core_sha, "head": core_sha},
            )
        )
    out.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    failed = [e["name"] for e in entries if e["result"] == "fail"]
    if failed:
        raise RuntimeError(f"attestations failed: {', '.join(failed)}")


def resolve_parent(core_sha: str, parent_ref: str | None) -> str:
    ref = parent_ref or f"{core_sha}~1"
    return must_run("git", "-C", str(ROOT), "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").strip()


def cmd_build(args: argparse.Namespace) -> int:
    out = Path(args.out)
    head = must_run("git", "-C", str(ROOT), "rev-parse", "HEAD").strip()
    core_sha = args.core_sha or head
    if head != core_sha:
        raise RuntimeError(f"worktree HEAD {head} is not the bundle core_sha {core_sha}")
    parent_sha = resolve_parent(core_sha, args.parent_core_sha)
    dirty = must_run("git", "-C", str(ROOT), "status", "--porcelain").splitlines()
    tracked_edits = [line for line in dirty if not line.startswith("??")]
    if tracked_edits:
        raise RuntimeError(f"worktree has tracked modifications: {tracked_edits[:5]}")
    ancestor = run("git", "-C", str(ROOT), "merge-base", "--is-ancestor", parent_sha, core_sha)
    if ancestor.returncode != 0 or parent_sha == core_sha:
        raise RuntimeError(f"parent_core_sha {parent_sha} is not a strict ancestor of {core_sha}")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    build_docs(out / "docs")
    regenerated = [
        line
        for line in must_run("git", "-C", str(ROOT), "status", "--porcelain").splitlines()
        if not line.startswith("??")
    ]
    if regenerated:
        raise RuntimeError(f"docs generation dirtied the tree at {core_sha}: {regenerated[:5]}")
    build_repo_files(out / "repo-files.json", core_sha)
    build_explorer_snapshot(out / "explorer")
    build_contract(out / "explorer/contract.json")
    build_fixtures(out / "explorer/fixtures")
    build_parity(out / "explorer/parity")
    (out / "landing").mkdir(parents=True, exist_ok=True)
    build_prompt_catalog(out / "landing/prompt-catalog.json")
    shutil.copy2(ROOT / "_project/design/site-inventory/api-public-symbols.json", out / "api-public-symbols.json")
    build_downloads(out / "downloads", out / "docs")
    build_attestations(out / "attestations.json", out, core_sha, parent_sha)
    members = {
        "docs": member_digest(out / "docs"),
        "repo-files.json": member_digest(out / "repo-files.json"),
        "explorer/results.duckdb": member_digest(out / "explorer/results.duckdb"),
        "explorer/contract.json": member_digest(out / "explorer/contract.json"),
        "explorer/fixtures": member_digest(out / "explorer/fixtures"),
        "explorer/parity": member_digest(out / "explorer/parity"),
        "explorer/bundles": member_digest(out / "explorer/bundles"),
        "landing/prompt-catalog.json": member_digest(out / "landing/prompt-catalog.json"),
        "api-public-symbols.json": member_digest(out / "api-public-symbols.json"),
        "downloads": member_digest(out / "downloads"),
        "attestations.json": member_digest(out / "attestations.json"),
    }
    manifest = {
        "schema": SCHEMA,
        "core_sha": core_sha,
        "package_version": package_version(),
        "certified_by": args.certified_by or "local",
        "corpus_sha": tree_sha(core_sha, "results-data"),
        "parent_core_sha": parent_sha,
        "parent_source": args.parent_source,
        "members": members,
        "produced_at": datetime.now(timezone.utc).isoformat(),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "core_sha": core_sha, "members": len(members)}))
    return 0


def cmd_verify(out: Path) -> int:
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA:
        print(f"schema mismatch: {manifest.get('schema')!r}")
        return 1
    expected = manifest.get("members", {})
    if not expected:
        print("empty manifest")
        return 1
    if sorted(expected) != sorted(REQUIRED_MEMBERS):
        print(f"member set mismatch: {sorted(expected)}")
        return 1
    if any(not sha for sha in expected.values()):
        print("empty member digest in manifest")
        return 1
    bad = [name for name, sha in expected.items() if member_digest(out / name) != sha]
    if bad:
        print(f"digest mismatch: {', '.join(bad)}")
        return 1
    attestations = json.loads((out / "attestations.json").read_text(encoding="utf-8"))
    if not attestations:
        print("empty attestations")
        return 1
    by_name = {e["name"]: e["result"] for e in attestations}
    missing = [name for name in list(REQUIRED_ATTESTATIONS) + ["validator_parity"] if name not in by_name]
    if missing:
        print(f"missing attestations: {', '.join(missing)}")
        return 1
    failed = [name for name, result in by_name.items() if result == "fail"]
    if failed:
        print(f"failed attestations: {', '.join(failed)}")
        return 1
    required_skipped = [name for name in REQUIRED_ATTESTATIONS if by_name[name] == "skip"]
    if required_skipped:
        print(f"required attestations skipped: {', '.join(required_skipped)}")
        return 1
    unknown = [name for name, result in by_name.items() if result not in ("pass", "skip")]
    if unknown:
        print(f"unknown attestation results: {', '.join(unknown)}")
        return 1
    snapshot = expected["explorer/results.duckdb"]
    inputless = [e["name"] for e in attestations if e["result"] == "pass" and not e.get("inputs")]
    if inputless:
        print(f"passing attestations without inputs: {', '.join(inputless)}")
        return 1
    drifted = [
        e["name"]
        for e in attestations
        if isinstance(e.get("inputs"), dict) and "snapshot" in e["inputs"] and e["inputs"]["snapshot"] != snapshot
    ]
    if drifted:
        print(f"attestation inputs drifted from manifest: {', '.join(drifted)}")
        return 1
    if manifest.get("parent_source") not in PARENT_SOURCES:
        print(f"unknown parent_source: {manifest.get('parent_source')!r}")
        return 1
    core_sha = manifest.get("core_sha")
    parent_sha = manifest.get("parent_core_sha")
    rebound = [
        e["name"]
        for e in attestations
        if isinstance(e.get("compared"), dict)
        and (
            ("head" in e["compared"] and e["compared"]["head"] != core_sha)
            or ("base" in e["compared"] and e["compared"]["base"] != parent_sha)
            or ("core_sha" in e["compared"] and e["compared"]["core_sha"] != core_sha)
            or ("accepted_ref" in e["compared"] and e["compared"]["accepted_ref"] != core_sha)
        )
    ]
    if rebound:
        print(f"attestation range drifted from manifest: {', '.join(rebound)}")
        return 1
    print(
        f"verify OK: schema {SCHEMA}, {len(expected)} members, "
        f"{sum(1 for e in attestations if e['result'] == 'pass')} pass, "
        f"{sum(1 for e in attestations if e['result'] == 'skip')} skip"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and verify the versioned site-inputs bundle")
    sub = parser.add_subparsers(dest="cmd", required=True)
    build = sub.add_parser("build")
    build.add_argument("--out", required=True)
    build.add_argument("--core-sha", default=None)
    build.add_argument("--parent-core-sha", default=None)
    build.add_argument("--parent-source", choices=PARENT_SOURCES, default="local")
    build.add_argument("--certified-by", default=None)
    verify = sub.add_parser("verify")
    verify.add_argument("dir", type=Path)
    args = parser.parse_args(argv)
    if args.cmd == "build":
        return cmd_build(args)
    return cmd_verify(args.dir)


if __name__ == "__main__":
    raise SystemExit(main())
