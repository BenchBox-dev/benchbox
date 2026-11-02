from __future__ import annotations

import base64
import hashlib
import io
import json
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import promotion, protocol

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40


def _build_wheel(package: str, version: str, pkg_files: dict[str, bytes]) -> tuple[bytes, str]:
    norm_pkg = package.replace("-", "_")
    dist_info = f"{norm_pkg}-{version}.dist-info"
    entries: dict[str, bytes] = {
        **pkg_files,
        f"{dist_info}/METADATA": f"Metadata-Version: 2.1\nName: {package}\nVersion: {version}\n".encode(),
        f"{dist_info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record_lines: list[str] = []
    for name, content in sorted(entries.items()):
        b64 = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode("ascii").rstrip("=")
        record_lines.append(f"{name},sha256={b64},{len(content)}")
    record_path = f"{dist_info}/RECORD"
    record_lines.append(f"{record_path},,")
    entries[record_path] = ("\n".join(record_lines) + "\n").encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in sorted(entries.items()):
            zf.writestr(name, content)
    raw = buf.getvalue()
    return raw, hashlib.sha256(raw).hexdigest()


def _lock_payload(
    *,
    version: str = "0.2.0",
    source_commit: str = SHA_B,
    source_sha256: str = "1" * 64,
    delta_sha256: str = "2" * 64,
    artifact_sha256: str = "3" * 64,
    workflow_sha: str | None = None,
    bootstrap: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": 1,
        "package": "theseus-reviewer",
        "version": version,
        "repository": "BenchBox-dev/theseus",
        "source_commit": source_commit,
        "source_sha256": source_sha256,
        "delta_sha256": delta_sha256,
        "artifact": {
            "sha256": artifact_sha256,
            "urls": [
                f"https://github.com/BenchBox-dev/theseus/releases/download/v{version}/theseus_reviewer-{version}-py3-none-any.whl",
                f"https://files.pythonhosted.org/packages/theseus/theseus_reviewer-{version}-py3-none-any.whl",
            ],
        },
        "dependencies": [
            {
                "name": "pyyaml",
                "version": "6.0.3",
                "sha256": "4" * 64,
                "urls": ["https://files.pythonhosted.org/packages/pyyaml-6.0.3.whl"],
            }
        ],
        "workflow_sha": source_commit if workflow_sha is None else workflow_sha,
    }
    if bootstrap is not None:
        payload["bootstrap"] = bootstrap
    return payload


def _base_record(head_sha: str = SHA_A, **overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "v": protocol.MARKER_VERSION,
        "cycle": 1,
        "round": 1,
        "kind": protocol.FIRST,
        "decision": protocol.SHIP,
        "head_sha": head_sha,
        "base_ref": "develop",
        "reviewer": "large",
        "tier": "large",
        "strikes_after": 0,
        "open_defects": [],
        "next_id": 1,
        "patch_digest": "d" * 32,
        "carried": False,
        "summary": "",
        "run_uuid": "9" * 32,
    }
    return {**record, **overrides}


def test_lock_requires_two_distinct_https_artifact_sources_and_matching_workflow_sha() -> None:
    single = _lock_payload()
    single["artifact"] = {
        "sha256": "3" * 64,
        "urls": ["https://github.com/BenchBox-dev/theseus/releases/download/v0.2.0/wheel.whl"],
    }
    with pytest.raises(promotion.PromotionError, match="at least 2 https url"):
        promotion.parse_lock(json.dumps(single))

    duplicate = _lock_payload()
    duplicate["artifact"] = {
        "sha256": "3" * 64,
        "urls": [
            "https://github.com/BenchBox-dev/theseus/releases/download/v0.2.0/wheel.whl",
            "https://github.com/BenchBox-dev/theseus/releases/download/v0.2.0/wheel.whl",
        ],
    }
    with pytest.raises(promotion.PromotionError, match="distinct"):
        promotion.parse_lock(json.dumps(duplicate))

    mismatched_wf = _lock_payload(workflow_sha=SHA_C)
    with pytest.raises(promotion.PromotionError, match="workflow_sha must equal"):
        promotion.parse_lock(json.dumps(mismatched_wf))

    untrusted_host = _lock_payload()
    untrusted_host["artifact"]["urls"] = [
        "https://github.com/BenchBox-dev/theseus/releases/download/v0.2.0/wheel.whl",
        "https://attacker.example/evil.whl",
    ]
    parsed = promotion.parse_lock(json.dumps(untrusted_host))
    with pytest.raises(promotion.PromotionError, match="allowed_artifact_hosts"):
        promotion.validate_lock_urls(parsed)


def test_verify_wheel_matches_source_rejects_tampered_bytes_extra_files_and_pth_hooks() -> None:
    source_tree = {
        "src/theseus_reviewer/__init__.py": b"VERSION = '0.2.0'\n",
        "src/theseus_reviewer/cli.py": b"def main():\n    return 0\n",
        "README.md": b"docs\n",
    }
    good_wheel, _ = _build_wheel(
        "theseus-reviewer",
        "0.2.0",
        {
            "theseus_reviewer/__init__.py": b"VERSION = '0.2.0'\n",
            "theseus_reviewer/cli.py": b"def main():\n    return 0\n",
        },
    )
    promotion.verify_wheel_matches_source(
        good_wheel,
        source_tree,
        package="theseus-reviewer",
        version="0.2.0",
    )

    backdoored_wheel, _ = _build_wheel(
        "theseus-reviewer",
        "0.2.0",
        {
            "theseus_reviewer/__init__.py": b"VERSION = '0.2.0'\n",
            "theseus_reviewer/cli.py": b"def main():\n    return 99\n",
        },
    )
    with pytest.raises(promotion.PromotionError, match="bytes do not match"):
        promotion.verify_wheel_matches_source(
            backdoored_wheel,
            source_tree,
            package="theseus-reviewer",
            version="0.2.0",
        )

    pth_wheel, _ = _build_wheel(
        "theseus-reviewer",
        "0.2.0",
        {
            "theseus_reviewer/__init__.py": b"VERSION = '0.2.0'\n",
            "theseus_reviewer/cli.py": b"def main():\n    return 0\n",
            "evil.pth": b"import os\n",
        },
    )
    with pytest.raises(promotion.PromotionError, match="forbidden executable or hook"):
        promotion.verify_wheel_matches_source(
            pth_wheel,
            source_tree,
            package="theseus-reviewer",
            version="0.2.0",
        )


def test_bootstrap_path_map_and_delta_digests_are_deterministic() -> None:
    benchbox_tree = {
        "_project/scripts/oracle_reviewers/cli.py": b"def run():\n    return 1\n",
        "_project/scripts/oracle_gate.py": b"GATE = True\n",
        "benchbox/core/unrelated.py": b"UNRELATED = 1\n",
    }
    path_map = {
        "_project/scripts/oracle_reviewers": "src/theseus_reviewer",
        "_project/scripts/oracle_gate.py": "src/theseus_reviewer/oracle_gate.py",
    }
    mapped_old = promotion.apply_path_map(benchbox_tree, path_map)
    assert set(mapped_old) == {
        "src/theseus_reviewer/cli.py",
        "src/theseus_reviewer/oracle_gate.py",
    }
    new_tree = {
        "src/theseus_reviewer/cli.py": b"def run():\n    return 2\n",
        "src/theseus_reviewer/oracle_gate.py": b"GATE = True\n",
        "src/theseus_reviewer/new_mod.py": b"NEW = True\n",
    }
    changed, diff_text, delta_sha = promotion.compute_upstream_delta(mapped_old, new_tree)
    assert [item.path for item in changed] == [
        ".oracle-promotion/new/src/theseus_reviewer/cli.py",
        ".oracle-promotion/new/src/theseus_reviewer/new_mod.py",
    ]
    assert "+    return 2" in diff_text and "+NEW = True" in diff_text
    assert len(delta_sha) == 64


def test_extract_tar_tree_rejects_symlinks_and_parent_traversal() -> None:
    symlink_buf = io.BytesIO()
    with tarfile.open(fileobj=symlink_buf, mode="w") as archive:
        info = tarfile.TarInfo("repo-root/link.py")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        archive.addfile(info)
    with pytest.raises(promotion.PromotionError, match="non-regular"):
        promotion.extract_tar_tree(symlink_buf.getvalue(), strip_single_root=True)

    traversal_buf = io.BytesIO()
    with tarfile.open(fileobj=traversal_buf, mode="w") as archive:
        info = tarfile.TarInfo("repo-root/../escape.py")
        payload = b"x = 1\n"
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    with pytest.raises(promotion.PromotionError, match="invalid path"):
        promotion.extract_tar_tree(traversal_buf.getvalue(), strip_single_root=True)


def test_protocol_marker_preserves_legacy_v1_compatibility_and_validates_promotion() -> None:
    legacy_encoded = protocol.encode_marker(_base_record())
    legacy_decoded = protocol.decode_marker(legacy_encoded)
    assert legacy_decoded is not None and "promotion" not in legacy_decoded

    promo = promotion.PromotionRecord(
        head_sha=SHA_A,
        old_source_sha256="1" * 64,
        new_source_sha256="2" * 64,
        old_artifact_sha256="3" * 64,
        new_artifact_sha256="4" * 64,
        delta_sha256="5" * 64,
        lock_sha256="6" * 64,
        bootstrap=False,
    ).to_json()
    promo_encoded = protocol.encode_marker(_base_record(promotion=promo))
    promo_decoded = protocol.decode_marker(promo_encoded)
    assert promo_decoded is not None and promo_decoded["promotion"] == promo

    malformed = _base_record(promotion={**promo, "delta_sha256": "short"})
    assert protocol.checked_record(malformed) is None


def test_promotion_rounds_never_carry_across_byte_identical_rebase_while_ordinary_rounds_still_carry() -> None:
    prior = _base_record(head_sha=SHA_A)
    history = protocol.History(records=[prior])
    ordinary = protocol.plan_round(
        history,
        head_sha=SHA_B,
        base_ref="develop",
        digest="d" * 32,
        patches={"abcd1234": "0123456789abcdef"},
        paths=[".github/theseus-lock.json"],
        strike_count=0,
        max_do_not_ship=3,
        is_promotion=False,
    )
    assert ordinary.kind == protocol.CARRY

    promoted = protocol.plan_round(
        history,
        head_sha=SHA_B,
        base_ref="develop",
        digest="d" * 32,
        patches={"abcd1234": "0123456789abcdef"},
        paths=[".github/theseus-lock.json", ".oracle-promotion/new/src/theseus_reviewer/cli.py"],
        strike_count=0,
        max_do_not_ship=3,
        is_promotion=True,
    )
    assert promoted.kind == protocol.FIRST

    prior_with_promo = _base_record(
        head_sha=SHA_A,
        promotion=promotion.PromotionRecord(
            head_sha=SHA_A,
            old_source_sha256="1" * 64,
            new_source_sha256="2" * 64,
            old_artifact_sha256="3" * 64,
            new_artifact_sha256="4" * 64,
            delta_sha256="5" * 64,
            lock_sha256="6" * 64,
        ).to_json(),
    )
    rebased_from_promo = protocol.plan_round(
        protocol.History(records=[prior_with_promo]),
        head_sha=SHA_C,
        base_ref="develop",
        digest="d" * 32,
        patches={"abcd1234": "0123456789abcdef"},
        paths=["README.md"],
        strike_count=0,
        max_do_not_ship=3,
        is_promotion=False,
    )
    assert rebased_from_promo.kind == protocol.FIRST


def test_stage_promotion_workspace_is_git_ignored_and_detects_tampering(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "-C", str(ws), "init", "--quiet"], check=True)
    (ws / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(ws), "-c", "user.name=t", "-c", "user.email=t@e", "add", "tracked.py"], check=True)
    subprocess.run(
        ["git", "-C", str(ws), "-c", "user.name=t", "-c", "user.email=t@e", "commit", "--quiet", "-m", "init"],
        check=True,
    )

    bundle = promotion.PromotionBundle(
        record=promotion.PromotionRecord(
            head_sha=SHA_A,
            old_source_sha256="1" * 64,
            new_source_sha256="2" * 64,
            old_artifact_sha256="3" * 64,
            new_artifact_sha256="4" * 64,
            delta_sha256="5" * 64,
            lock_sha256="6" * 64,
        ),
        changed_files=(),
        diff_text="diff --git a/a.py b/a.py\n",
        old_tree={"a.py": b"old\n"},
        new_tree={"a.py": b"new\n"},
    )
    plan_dir = tmp_path / "plan"
    promotion.write_materialized_bundle(bundle, plan_dir)
    staged = promotion.stage_promotion_workspace(plan_dir, ws)
    assert staged is not None
    status = subprocess.run(
        ["git", "-C", str(ws), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout
    assert status == ""
    before = promotion.staged_tree_digest(ws)
    assert before is not None and before != "invalid"
    (staged / "new" / "a.py").write_text("tampered\n", encoding="utf-8")
    assert promotion.staged_tree_digest(ws) != before


def test_planted_oracle_promotion_directory_is_refused(tmp_path: Path) -> None:
    ws = tmp_path / "ws-planted"
    ws.mkdir()
    subprocess.run(["git", "-C", str(ws), "init", "--quiet"], check=True)
    planted = ws / ".oracle-promotion" / "new"
    planted.mkdir(parents=True)
    (planted / "a.py").write_text("benign\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(ws), "-c", "user.name=t", "-c", "user.email=t@e", "add", ".oracle-promotion/new/a.py"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(ws), "-c", "user.name=t", "-c", "user.email=t@e", "commit", "--quiet", "-m", "plant"],
        check=True,
    )
    bundle = promotion.PromotionBundle(
        record=promotion.PromotionRecord(
            head_sha=SHA_A,
            old_source_sha256="1" * 64,
            new_source_sha256="2" * 64,
            old_artifact_sha256="3" * 64,
            new_artifact_sha256="4" * 64,
            delta_sha256="5" * 64,
            lock_sha256="6" * 64,
        ),
        changed_files=(),
        diff_text="diff --git a/a.py b/a.py\n",
        old_tree={"a.py": b"old\n"},
        new_tree={"a.py": b"malicious\n"},
    )
    plan_dir = tmp_path / "plan-planted"
    promotion.write_materialized_bundle(bundle, plan_dir)
    assert promotion.stage_promotion_workspace(plan_dir, ws) is None


def test_removed_upstream_files_stage_under_old_and_duplicate_path_map_keys_fail() -> None:
    changed, _, _ = promotion.compute_upstream_delta(
        {"src/guard.py": b"def guard():\n    return True\n"},
        {},
    )
    assert len(changed) == 1
    assert changed[0].path == ".oracle-promotion/old/src/guard.py"
    assert changed[0].status == "removed"
    assert changed[0].deletions == 2

    colliding = _lock_payload(
        bootstrap={
            "baseline_repo": "BenchBox-dev/BenchBox",
            "baseline_commit": SHA_A,
            "baseline_source_sha256": "1" * 64,
            "path_map": {"pkg/a": "dst/one", "pkg/a/": "dst/two"},
            "reviewer": "joeharris76",
            "approved_head": SHA_B,
        }
    )
    with pytest.raises(promotion.PromotionError, match="duplicate bootstrap path_map"):
        promotion.parse_lock(json.dumps(colliding))
