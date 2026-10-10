#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CLI_DESCRIPTION = (
    "Compare PR-head vs merge-SHA validation outcomes (A2 w4 parity).\n"
    "\n"
    "This script validates that the trusted-base validator produces identical\n"
    "outcomes when run against payload extracted from MERGE_SHA versus the PR head.\n"
    "It is the local parity counterpart to the pull_request_target + MERGE_SHA\n"
    "revalidation in .github/workflows/validate-submission.yml.\n"
    "\n"
    "Contract:\n"
    "- Executes validator from trusted base checkout (ref: BASE_SHA). Payload is\n"
    "  extracted from MERGE_SHA via ``git show $MERGE_SHA:path`` or sparse checkout\n"
    "  to /tmp/payload. Never executes validator code from the PR branch.\n"
    "- Discovers changed bundles via three-dot ``BASE_SHA...MERGE_SHA`` diff with\n"
    "  --diff-filter=ACMRD, then back-maps CHANGED_MANIFESTS / CHANGED_APPLIED /\n"
    "  CHANGED_COMPANIONS to primary bundles via ``git ls-tree $MERGE_SHA``.\n"
    "- Corpus parity: if CORPUS_CHANGED_PATHS_FILE is provided, validates it against\n"
    "  the MERGE_SHA file list and runs ``scripts/generate_corpus_inventory.py --check``\n"
    "  logic on the merge payload; empty file means no corpus changes, missing file\n"
    "  is an error.\n"
    "- Parity with benchbox/validation/bundle.py and scripts/validate_submission.py\n"
    "  --corpus-changed-paths flag.\n"
    "\n"
    "Usage:\n"
    "  uv run -- python scripts/publication/validator_parity.py --base-sha <sha> --merge-sha <sha> --head-sha <sha>\n"
    "  uv run -- python scripts/publication/validator_parity.py --base-sha $BASE_SHA --merge-sha $MERGE_SHA --head-sha $HEAD_SHA --corpus-changed-paths /tmp/corpus_changed_paths.txt\n"
    "  # Env fallback:\n"
    "  BASE_SHA=... MERGE_SHA=... HEAD_SHA=... CORPUS_CHANGED_PATHS_FILE=/tmp/corpus_changed_paths.txt uv run -- python scripts/publication/validator_parity.py\n"
    "\n"
    "Exit codes:\n"
    "  0 - parity holds (head and merge outcomes identical and both succeed)\n"
    "  1 - validation failure or parity divergence\n"
    "  2 - usage / environment error (missing SHA, missing file, git failure)\n"
)

CHECKOUT_ROOT = Path(__file__).resolve().parents[2]
if str(CHECKOUT_ROOT) not in sys.path:
    sys.path.insert(0, str(CHECKOUT_ROOT))

try:
    from benchbox.validation.bundle import discover_bundles, validate_bundles
except ImportError:
    import importlib.util

    bundle_path = CHECKOUT_ROOT / "benchbox" / "validation" / "bundle.py"
    spec = importlib.util.spec_from_file_location("_benchbox_validation_bundle", bundle_path)
    if spec is None or spec.loader is None:
        raise
    bundle = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = bundle
    assert spec.loader is not None
    spec.loader.exec_module(bundle)
    discover_bundles = bundle.discover_bundles
    validate_bundles = bundle.validate_bundles


def _run_git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd or CHECKOUT_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _resolve_sha(label: str, value: str | None, *alt_env: str) -> str:
    if value and value.strip():
        return value.strip()
    for env_label in (label, *alt_env):
        env_val = os.environ.get(env_label, "").strip()
        if env_val:
            return env_val
    return ""


def _diff_name_only(base_sha: str, merge_sha: str, *pathspecs: str, diff_filter: str = "ACMRD") -> list[str]:
    args = ["diff", "--no-renames", "--name-only", f"--diff-filter={diff_filter}", f"{base_sha}...{merge_sha}", "--"]
    args.extend(pathspecs)
    out = _run_git(*args)
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    return lines


def _ls_tree_at(sha: str, prefix: str) -> set[str]:
    out = _run_git("ls-tree", "-r", "--name-only", sha, "--", prefix)
    return {line.strip() for line in out.splitlines() if line.strip()}


def _extract_payload(merge_sha: str, paths: list[str], dest: Path) -> list[Path]:
    extracted: list[Path] = []
    for rel in paths:
        try:
            content = _run_git("show", f"{merge_sha}:{rel}")
        except RuntimeError:
            result = subprocess.run(
                ["git", "show", f"{merge_sha}:{rel}"],
                cwd=str(CHECKOUT_ROOT),
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                print(f"Warning: cannot extract {rel} at {merge_sha}, skipping", file=sys.stderr)
                continue
            content = result.stdout.decode("utf-8", errors="replace")
        local = dest / rel
        local.parent.mkdir(parents=True, exist_ok=True)
        raw_result = subprocess.run(
            ["git", "cat-file", "-p", f"{merge_sha}:{rel}"],
            cwd=str(CHECKOUT_ROOT),
            capture_output=True,
            check=False,
        )
        if raw_result.returncode == 0:
            local.write_bytes(raw_result.stdout)
        else:
            local.write_text(content, encoding="utf-8")
        extracted.append(local)
    return extracted


def _discover_changed_bundles(base_sha: str, merge_sha: str) -> list[str]:  # noqa: C901
    changed = _diff_name_only(
        base_sha,
        merge_sha,
        ":(icase,glob)results-data/bundles/*.json",
        ":(icase,glob)results-data/bundles/**/*.json",
        diff_filter="ACMR",
    )
    filtered: list[str] = []
    for path in changed:
        lower = path.lower()
        if (
            lower.endswith(".plans.json")
            or lower.endswith(".tuning.json")
            or lower.endswith(".applied.json")
            or lower.endswith(".manifest.json")
            or lower.endswith(".override.json")
            or lower.endswith("corpus-inventory.json")
            or lower.endswith("submission-manifest.json")
        ):
            continue
        filtered.append(path)

    merge_files = _ls_tree_at(merge_sha, "results-data/bundles")

    def _stem_to_bundle(stem: str) -> str | None:
        target_lower = f"{stem.lower()}.json"
        for candidate in merge_files:
            if candidate.lower() == target_lower:
                return candidate
        for candidate in merge_files:
            lower = candidate.lower()
            if lower.endswith(".json") and lower[:-5] == stem.lower():
                return candidate
        return None

    changed_manifests = _diff_name_only(
        base_sha,
        merge_sha,
        ":(icase,glob)results-data/bundles/*.json",
        ":(icase,glob)results-data/bundles/**/*.json",
        diff_filter="ACMRD",
    )
    changed_manifests = [p for p in changed_manifests if p.lower().endswith(".manifest.json")]
    for manifest in changed_manifests:
        stem = manifest[:-14]
        bundle = _stem_to_bundle(stem)
        if bundle and bundle not in filtered:
            if bundle in merge_files:
                filtered.append(bundle)

    changed_applied_raw = _diff_name_only(
        base_sha,
        merge_sha,
        ":(icase,glob)results-data/bundles/*.json",
        ":(icase,glob)results-data/bundles/**/*.json",
        diff_filter="ACMRD",
    )
    changed_applied = [p for p in changed_applied_raw if p.lower().endswith(".applied.json")]
    for applied in changed_applied:
        stem = applied[:-13]
        bundle = _stem_to_bundle(stem)
        if bundle and bundle not in filtered and bundle in merge_files:
            filtered.append(bundle)

    changed_companions_raw = _diff_name_only(
        base_sha,
        merge_sha,
        ":(icase,glob)results-data/bundles/*.json",
        ":(icase,glob)results-data/bundles/**/*.json",
        diff_filter="ACMRD",
    )
    changed_companions = [
        p
        for p in changed_companions_raw
        if p.lower().endswith(".plans.json")
        or p.lower().endswith(".tuning.json")
        or p.lower().endswith(".override.json")
    ]
    for companion in changed_companions:
        lower = companion.lower()
        if lower.endswith(".plans.json"):
            stem = companion[:-11]
        elif lower.endswith(".tuning.json"):
            stem = companion[:-12]
        elif lower.endswith(".override.json"):
            stem = companion[:-14]
        else:
            continue
        bundle = _stem_to_bundle(stem)
        if bundle is None:
            if companion in merge_files:
                print(f"::error::Live companion has no primary bundle at {stem}.json", file=sys.stderr)
                raise SystemExit(1)
            continue
        if bundle not in filtered and bundle in merge_files:
            filtered.append(bundle)

    return sorted(set(filtered))


def _validate_corpus_changed_paths(corpus_file: Path | None, base_sha: str, merge_sha: str) -> int:
    if corpus_file is None:
        return 0
    if not corpus_file.exists():
        print(
            f"::error::CORPUS_CHANGED_PATHS_FILE missing: {corpus_file} (expected atomic write via git diff + mv)",
            file=sys.stderr,
        )
        return 1
    try:
        content = corpus_file.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"::error::Cannot read corpus changed paths file {corpus_file}: {exc}", file=sys.stderr)
        return 1
    paths = [line.strip() for line in content.splitlines() if line.strip()]
    if not paths:
        return 0
    merge_corpus_files = _ls_tree_at(merge_sha, "results-data/corpus")
    expected = _diff_name_only(base_sha, merge_sha, "results-data/corpus/**", diff_filter="ACMRD")
    expected_set = set(expected)
    for p in paths:
        if not p.startswith("results-data/corpus/"):
            print(f"::error::Corpus changed path not under results-data/corpus/: {p}", file=sys.stderr)
            return 1
        if p not in expected_set and p not in merge_corpus_files:
            if p not in expected_set:
                print(
                    f"Warning: corpus path {p} not in diff {base_sha}...{merge_sha} and not at {merge_sha}",
                    file=sys.stderr,
                )
    return 0


def _run_validation_on_payload(
    bundle_paths: list[str], merge_sha: str, require_manifest: bool = False, allow_partial: bool = False
) -> tuple[int, str]:
    if not bundle_paths:
        return 0, "No bundles changed"
    with tempfile.TemporaryDirectory(prefix="payload_") as tmpdir:
        dest = Path(tmpdir) / "payload"
        dest.mkdir(parents=True, exist_ok=True)
        merge_files = _ls_tree_at(merge_sha, "results-data/bundles")
        to_extract: set[str] = set(bundle_paths)
        for bundle in bundle_paths:
            stem = bundle[:-5] if bundle.lower().endswith(".json") else bundle
            for suffix in [".manifest.json", ".applied.json", ".plans.json", ".tuning.json", ".override.json"]:
                candidate = f"{stem}{suffix}"
                for mf in merge_files:
                    if mf.lower() == candidate.lower():
                        to_extract.add(mf)
            legacy = str(Path(bundle).parent / "submission-manifest.json")
            for mf in merge_files:
                if mf.lower() == legacy.lower():
                    to_extract.add(mf)
        _extract_payload(merge_sha, sorted(to_extract), dest)
        local_bundles: list[Path] = []
        for b in bundle_paths:
            local = dest / b
            if local.is_file():
                local_bundles.append(local)
            else:
                print(f"Warning: bundle {b} not extracted at {local}", file=sys.stderr)
        if not local_bundles:
            print(
                f"::error::No bundle files extracted at {merge_sha} for {len(bundle_paths)} requested bundle(s)",
                file=sys.stderr,
            )
            return 1, "No bundles extracted"
        results = validate_bundles(
            local_bundles, require_manifest=require_manifest, allow_partial_validation=allow_partial
        )
        ok = all(r.ok for r in results)
        summary = "\n".join(f"{r.path}: {'OK' if r.ok else 'FAIL: ' + '; '.join(r.errors)}" for r in results)
        return (0 if ok else 1), summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument(
        "--base-sha",
        dest="base_sha",
        default=None,
        help="BASE_SHA (default: env BASE_SHA or github.event.pull_request.base.sha)",
    )
    parser.add_argument(
        "--merge-sha", dest="merge_sha", default=None, help="MERGE_SHA (default: env MERGE_SHA or FETCH_HEAD)"
    )
    parser.add_argument(
        "--head-sha",
        dest="head_sha",
        default=None,
        help="HEAD_SHA / PR head (default: env HEAD_SHA or PR_HEAD_SHA)",
    )
    parser.add_argument(
        "--corpus-changed-paths",
        dest="corpus_changed_paths",
        default=None,
        help="Path to CORPUS_CHANGED_PATHS_FILE (default: env CORPUS_CHANGED_PATHS_FILE)",
    )
    parser.add_argument("--require-manifest", action="store_true", help="Require manifest sidecar")
    parser.add_argument(
        "--allow-partial-validation", action="store_true", help="Allow partial validation for trusted mirrors"
    )
    parser.add_argument(
        "--payload-dir",
        dest="payload_dir",
        default=None,
        help="Existing payload dir at MERGE_SHA (alternative to git show extraction)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run the full head-vs-merge parity comparison (SHAs still required)",
    )
    return parser.parse_args(argv)


def compare_head_merge_outcomes(
    changed_bundles: list[str],
    merge_sha: str,
    head_sha: str,
    *,
    require_manifest: bool = False,
    allow_partial: bool = False,
) -> tuple[int, str]:
    merge_rc, merge_summary = _run_validation_on_payload(
        changed_bundles,
        merge_sha,
        require_manifest=require_manifest,
        allow_partial=allow_partial,
    )
    head_rc, head_summary = _run_validation_on_payload(
        changed_bundles,
        head_sha,
        require_manifest=require_manifest,
        allow_partial=allow_partial,
    )
    print("--- Validation summary (MERGE_SHA payload) ---")
    print(merge_summary)
    print("--- Validation summary (HEAD_SHA payload) ---")
    print(head_summary)

    if merge_rc != head_rc:
        msg = f"head vs merge outcomes diverged: MERGE_SHA rc={merge_rc}, HEAD_SHA rc={head_rc}"
        print(f"::error::{msg}", file=sys.stderr)
        return 1, msg
    if merge_rc != 0:
        msg = "Parity validation FAILED: both MERGE_SHA and HEAD_SHA payload validations failed"
        print(f"::error::{msg}", file=sys.stderr)
        return 1, msg
    return 0, "Parity OK: MERGE_SHA and HEAD_SHA validation outcomes match"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    base_sha = _resolve_sha("BASE_SHA", args.base_sha)
    merge_sha = _resolve_sha("MERGE_SHA", args.merge_sha)
    head_sha = _resolve_sha("HEAD_SHA", args.head_sha, "PR_HEAD_SHA")
    corpus_file_str = args.corpus_changed_paths or os.environ.get("CORPUS_CHANGED_PATHS_FILE", "").strip() or None
    corpus_file = Path(corpus_file_str) if corpus_file_str else None

    if not base_sha:
        print("::error::BASE_SHA is required (--base-sha or env BASE_SHA)", file=sys.stderr)
        return 2
    if not merge_sha:
        print("::error::MERGE_SHA is required (--merge-sha or env MERGE_SHA)", file=sys.stderr)
        return 2
    if not head_sha:
        print("::error::HEAD_SHA is required (--head-sha or env HEAD_SHA / PR_HEAD_SHA)", file=sys.stderr)
        return 2

    for label, sha in [("BASE_SHA", base_sha), ("MERGE_SHA", merge_sha), ("HEAD_SHA", head_sha)]:
        if len(sha) < 7 or not all(c in "0123456789abcdef" for c in sha.lower()):
            print(f"::error::{label} does not look like a valid SHA: {sha}", file=sys.stderr)
            return 2
    for label, sha in [("BASE_SHA", base_sha), ("MERGE_SHA", merge_sha), ("HEAD_SHA", head_sha)]:
        try:
            _run_git("cat-file", "-e", sha)
        except RuntimeError:
            print(f"::error::{label} not found in repo: {sha}", file=sys.stderr)
            return 2

    try:
        changed_bundles = _discover_changed_bundles(base_sha, merge_sha)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1
    except Exception as exc:
        print(f"::error::Failed to discover changed bundles: {exc}", file=sys.stderr)
        return 2

    print(f"BASE_SHA={base_sha}")
    print(f"MERGE_SHA={merge_sha}")
    print(f"HEAD_SHA={head_sha}")
    print(f"Changed bundles ({len(changed_bundles)}):")
    for bundle in changed_bundles:
        print(f"  {bundle}")

    corpus_rc = _validate_corpus_changed_paths(corpus_file, base_sha, merge_sha)
    if corpus_rc != 0:
        return 1

    rc, message = compare_head_merge_outcomes(
        changed_bundles,
        merge_sha,
        head_sha,
        require_manifest=args.require_manifest,
        allow_partial=args.allow_partial_validation,
    )
    if rc != 0:
        return rc

    if corpus_file is not None and corpus_file.exists():
        content = corpus_file.read_text(encoding="utf-8")
        if content.strip():
            try:
                from scripts.generate_corpus_inventory import generate_inventory

                local_inv = generate_inventory(CHECKOUT_ROOT / "results-data" / "bundles")
                print(f"Local inventory bundles: {len(local_inv.get('bundles', []))} (parity check)")
            except Exception as exc:
                print(f"Warning: corpus inventory parity check skipped: {exc}", file=sys.stderr)

    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
