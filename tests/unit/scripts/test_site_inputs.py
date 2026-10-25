from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "scripts/site_inputs.py"

spec = importlib.util.spec_from_file_location("site_inputs_test", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
site_inputs = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = site_inputs
spec.loader.exec_module(site_inputs)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_member_digest_is_stable_and_path_sensitive(tmp_path: Path) -> None:
    root = tmp_path / "member"
    _write(root / "a.json", '{"a": 1}\n')
    _write(root / "sub/b.json", '{"b": 2}\n')
    first = site_inputs.member_digest(root)
    assert site_inputs.member_digest(root) == first
    _write(root / "sub/b.json", '{"b": 3}\n')
    assert site_inputs.member_digest(root) != first


def test_member_digest_covers_single_file(tmp_path: Path) -> None:
    target = _write(tmp_path / "symbols.json", '{"symbols": []}\n')
    assert site_inputs.member_digest(target) == site_inputs.file_sha(target)


def _bundle(tmp_path: Path, schema: int = 3, attestations: str = "pass") -> Path:
    out = tmp_path / "bundle"
    members = {
        "docs": "docs tree",
        "repo-files.json": '{"files": []}\n',
        "explorer/results.duckdb": "duckdb-bytes",
        "explorer/contract.json": '{"version": "6"}\n',
        "explorer/fixtures": "fixture tree",
        "explorer/parity": "parity tree",
        "explorer/bundles": "result bundles",
        "landing/prompt-catalog.json": '{"prompts": []}\n',
        "api-public-symbols.json": '{"symbols": []}\n',
        "downloads": "download tree",
        "attestations.json": "attestation tree",
    }
    dirs = {"docs", "explorer/fixtures", "explorer/parity", "explorer/bundles", "downloads"}
    for name, body in members.items():
        if name == "attestations.json":
            continue
        target = out / name
        if name in dirs:
            _write(target / "member.txt", body)
        else:
            _write(target, body)
    snapshot = site_inputs.member_digest(out / "explorer/results.duckdb")
    att = [
        {
            "name": "privacy",
            "result": attestations,
            "inputs": {"bundle": "fixture-bundle"},
            "compared": {"core_sha": "abc"},
        },
        {"name": "explorer_compat", "result": "pass", "inputs": {"snapshot": snapshot}},
        {"name": "snapshot_invariants", "result": "pass", "inputs": {"snapshot": snapshot}},
        {
            "name": "corpus_bijection",
            "result": "pass",
            "inputs": {"snapshot": snapshot},
            "compared": {"accepted_ref": "abc"},
        },
        {"name": "corpus_validation", "result": "pass", "inputs": {"corpus": "tree"}},
        {"name": "validator_parity", "result": "skip", "compared": {"base": "def", "head": "abc"}},
    ]
    _write(out / "attestations.json", json.dumps(att) + "\n")
    digests = {name: site_inputs.member_digest(out / name) for name in members}
    manifest = {
        "schema": schema,
        "core_sha": "abc",
        "parent_core_sha": "def",
        "parent_source": "bundle",
        "members": digests,
    }
    _write(out / "manifest.json", json.dumps(manifest) + "\n")
    return out


def test_verify_accepts_consistent_bundle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert site_inputs.cmd_verify(_bundle(tmp_path)) == 0
    assert "verify OK" in capsys.readouterr().out


def test_verify_rejects_tampered_member(tmp_path: Path) -> None:
    out = _bundle(tmp_path)
    (out / "api-public-symbols.json").write_text('{"symbols": ["x"]}\n', encoding="utf-8")
    assert site_inputs.cmd_verify(out) == 1


def test_verify_rejects_schema_mismatch(tmp_path: Path) -> None:
    assert site_inputs.cmd_verify(_bundle(tmp_path, schema=1)) == 1


def test_verify_rejects_failed_attestation(tmp_path: Path) -> None:
    assert site_inputs.cmd_verify(_bundle(tmp_path, attestations="fail")) == 1


def test_verify_rejects_missing_member(tmp_path: Path) -> None:
    out = _bundle(tmp_path)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["members"].pop("docs")
    (out / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    assert site_inputs.cmd_verify(out) == 1


def test_verify_rejects_missing_attestation(tmp_path: Path) -> None:
    out = _bundle(tmp_path)
    kept = [e for e in json.loads((out / "attestations.json").read_text(encoding="utf-8")) if e["name"] != "privacy"]
    (out / "attestations.json").write_text(json.dumps(kept) + "\n", encoding="utf-8")
    assert site_inputs.cmd_verify(out) == 1


def test_verify_rejects_skipped_required_attestation(tmp_path: Path) -> None:
    out = _bundle(tmp_path)
    changed = [
        dict(e, result="skip") if e["name"] == "privacy" else e
        for e in json.loads((out / "attestations.json").read_text(encoding="utf-8"))
    ]
    (out / "attestations.json").write_text(json.dumps(changed) + "\n", encoding="utf-8")
    assert site_inputs.cmd_verify(out) == 1


def test_verify_rejects_empty_attestations(tmp_path: Path) -> None:
    out = _bundle(tmp_path)
    (out / "attestations.json").write_text("[]\n", encoding="utf-8")
    assert site_inputs.cmd_verify(out) == 1


def test_verify_rejects_pass_entry_without_inputs(tmp_path: Path) -> None:
    out = _bundle(tmp_path)
    changed = [
        {k: v for k, v in e.items() if k != "inputs"} if e["name"] == "explorer_compat" else e
        for e in json.loads((out / "attestations.json").read_text(encoding="utf-8"))
    ]
    (out / "attestations.json").write_text(json.dumps(changed) + "\n", encoding="utf-8")
    assert site_inputs.cmd_verify(out) == 1


def test_verify_rejects_snapshot_digest_mismatch(tmp_path: Path) -> None:
    out = _bundle(tmp_path)
    changed = [
        dict(e, inputs={"snapshot": "0" * 64}) if e["name"] == "snapshot_invariants" else e
        for e in json.loads((out / "attestations.json").read_text(encoding="utf-8"))
    ]
    (out / "attestations.json").write_text(json.dumps(changed) + "\n", encoding="utf-8")
    assert site_inputs.cmd_verify(out) == 1


def test_verify_rejects_empty_member_digest(tmp_path: Path) -> None:
    out = _bundle(tmp_path)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["members"]["docs"] = ""
    (out / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    assert site_inputs.cmd_verify(out) == 1


def test_verify_rejects_compared_head_mismatch(tmp_path: Path) -> None:
    out = _bundle(tmp_path)
    changed = [
        dict(e, compared={"base": "def", "head": "0" * 40}) if e["name"] == "validator_parity" else e
        for e in json.loads((out / "attestations.json").read_text(encoding="utf-8"))
    ]
    (out / "attestations.json").write_text(json.dumps(changed) + "\n", encoding="utf-8")
    assert site_inputs.cmd_verify(out) == 1


def test_verify_rejects_compared_core_sha_mismatch(tmp_path: Path) -> None:
    out = _bundle(tmp_path)
    changed = [
        dict(e, compared={"core_sha": "0" * 40}) if e["name"] == "privacy" else e
        for e in json.loads((out / "attestations.json").read_text(encoding="utf-8"))
    ]
    (out / "attestations.json").write_text(json.dumps(changed) + "\n", encoding="utf-8")
    assert site_inputs.cmd_verify(out) == 1


def test_verify_rejects_compared_accepted_ref_mismatch(tmp_path: Path) -> None:
    out = _bundle(tmp_path)
    changed = [
        dict(e, compared={"accepted_ref": "0" * 40}) if e["name"] == "corpus_bijection" else e
        for e in json.loads((out / "attestations.json").read_text(encoding="utf-8"))
    ]
    (out / "attestations.json").write_text(json.dumps(changed) + "\n", encoding="utf-8")
    assert site_inputs.cmd_verify(out) == 1


def test_verify_rejects_compared_base_mismatch(tmp_path: Path) -> None:
    out = _bundle(tmp_path)
    changed = [
        dict(e, compared={"base": "0" * 40, "head": "abc"}) if e["name"] == "validator_parity" else e
        for e in json.loads((out / "attestations.json").read_text(encoding="utf-8"))
    ]
    (out / "attestations.json").write_text(json.dumps(changed) + "\n", encoding="utf-8")
    assert site_inputs.cmd_verify(out) == 1


def test_verify_rejects_compared_base_without_parent(tmp_path: Path) -> None:
    out = _bundle(tmp_path)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    del manifest["parent_core_sha"]
    (out / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    assert site_inputs.cmd_verify(out) == 1


def test_member_digest_raises_on_missing_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        site_inputs.member_digest(tmp_path / "absent")


def test_build_refuses_foreign_core_sha(tmp_path: Path) -> None:
    for sha in ("d076a974d", "0" * 40):
        args = argparse.Namespace(
            out=str(tmp_path / "out"),
            core_sha=sha,
            parent_core_sha=None,
            parent_source="local",
            certified_by=None,
            profile="exact3",
            cmd="build",
        )
        with pytest.raises(RuntimeError, match="not the bundle core_sha"):
            site_inputs.cmd_build(args)


def _gen_data(tmp_path: Path, payload: dict | None = None) -> Path:
    data = tmp_path / "gen" / "data"
    _write(data / "results.duckdb", "duckdb-bytes")
    _write(data / "bundles" / "r1.json", '{"run": {"id": "x"}}\n')
    if payload is not None:
        _write(data / "fixture-ids.json", json.dumps(payload) + "\n")
    return data


def test_stage_fixtures_copies_db_bundles_and_role_keyed_ids(tmp_path: Path) -> None:
    out = tmp_path / "out"
    site_inputs._stage_fixtures(_gen_data(tmp_path, {"ids": {"duckdb": "r1"}, "shortIds": {"duckdb": "s1"}}), out)

    assert (out / "results.duckdb").is_file()
    assert (out / "r1.json").is_file()
    assert json.loads((out / "fixture-ids.json").read_text(encoding="utf-8"))["ids"]["duckdb"] == "r1"


def test_stage_fixtures_rejects_missing_ids_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="no fixture-ids.json"):
        site_inputs._stage_fixtures(_gen_data(tmp_path), tmp_path / "out")


def test_stage_fixtures_rejects_flat_id_map(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="role-keyed contract"):
        site_inputs._stage_fixtures(_gen_data(tmp_path, {"r1": "s1"}), tmp_path / "out")


def test_repo_files_lists_every_tracked_path_with_kinds(tmp_path: Path) -> None:
    out = tmp_path / "repo-files.json"
    site_inputs.build_repo_files(out, "abc123")
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["core_sha"] == "abc123"
    by_path = {entry["path"]: entry["kind"] for entry in payload["files"]}
    assert by_path["scripts/site_inputs.py"] == "file"
    assert by_path["scripts"] == "tree"
    assert by_path["docs/internal/adr/adr-site-repo-split.md"] == "file"


def _captured_attestations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, corpus_changed: bool
) -> tuple[list[dict], list[list[str]]]:
    commands: list[list[str]] = []

    def fake_run(*cmd: str, cwd: Path = site_inputs.ROOT) -> argparse.Namespace:
        commands.append([str(c) for c in cmd])
        return argparse.Namespace(returncode=0, stdout="ok", stderr="")

    def fake_tree_sha(ref: str, subdir: str) -> str:
        if subdir == "results-data" and corpus_changed:
            return f"tree-{ref}"
        return "tree"

    monkeypatch.setattr(site_inputs, "run", fake_run)
    monkeypatch.setattr(site_inputs, "tree_sha", fake_tree_sha)
    bundle = tmp_path / "bundle"
    _write(bundle / "explorer/results.duckdb", "duckdb-bytes")
    out = tmp_path / "attestations.json"
    site_inputs.build_attestations(out, bundle, "core", "parent")
    return json.loads(out.read_text(encoding="utf-8")), commands


def _command(commands: list[list[str]], script: str) -> list[str]:
    matches = [c for c in commands if script in c]
    assert len(matches) == 1, commands
    return matches[0]


def test_corpus_bijection_matches_the_deploy_gate_form(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entries, commands = _captured_attestations(tmp_path, monkeypatch, corpus_changed=False)
    command = _command(commands, "scripts/publication/check_corpus_bijection.py")
    assert command[command.index("--accepted-ref") + 1] == "core"
    assert command[command.index("--expect-source") + 1] == "core"
    assert command[command.index("--bundles-dir") + 1] == str(site_inputs.ROOT / "results-data" / "bundles")
    assert command[command.index("--ledger-seed") + 1] == str(site_inputs.ROOT / "publication/ledger-seed.json")
    assert "--require-artifact" in command
    bijection = next(e for e in entries if e["name"] == "corpus_bijection")
    assert bijection["compared"] == {"accepted_ref": "core"}


def test_build_refuses_missing_ledger_seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(site_inputs, "ROOT", tmp_path)
    with pytest.raises(RuntimeError, match="ledger seed missing"):
        _captured_attestations(tmp_path, monkeypatch, corpus_changed=False)


def test_verify_rejects_accepted_ref_other_than_core_sha(tmp_path: Path) -> None:
    out = _bundle(tmp_path)
    changed = [
        dict(e, compared={"accepted_ref": "def"}) if e["name"] == "corpus_bijection" else e
        for e in json.loads((out / "attestations.json").read_text(encoding="utf-8"))
    ]
    (out / "attestations.json").write_text(json.dumps(changed) + "\n", encoding="utf-8")
    assert site_inputs.cmd_verify(out) == 1


@pytest.mark.parametrize("corpus_changed", [False, True])
def test_full_corpus_validation_runs_on_every_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corpus_changed: bool
) -> None:
    entries, commands = _captured_attestations(tmp_path, monkeypatch, corpus_changed=corpus_changed)
    assert _command(commands, "results-data/validate_corpus.py") == [
        "uv",
        "run",
        "--",
        "python",
        "results-data/validate_corpus.py",
    ]
    validation = next(e for e in entries if e["name"] == "corpus_validation")
    assert validation["result"] == "pass"
    assert set(validation["inputs"]) == {"corpus", *site_inputs.CORPUS_VALIDATOR_PATHS}


def test_failed_full_corpus_validation_fails_the_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*cmd: str, cwd: Path = site_inputs.ROOT) -> argparse.Namespace:
        failed = "results-data/validate_corpus.py" in cmd
        return argparse.Namespace(returncode=1 if failed else 0, stdout="", stderr="bad bundle" if failed else "")

    monkeypatch.setattr(site_inputs, "run", fake_run)
    monkeypatch.setattr(site_inputs, "tree_sha", lambda ref, subdir: "tree")
    bundle = tmp_path / "bundle"
    _write(bundle / "explorer/results.duckdb", "duckdb-bytes")
    with pytest.raises(RuntimeError, match="corpus_validation"):
        site_inputs.build_attestations(tmp_path / "attestations.json", bundle, "core", "parent")


def test_verify_rejects_bundle_without_full_corpus_validation(tmp_path: Path) -> None:
    out = _bundle(tmp_path)
    kept = [
        e
        for e in json.loads((out / "attestations.json").read_text(encoding="utf-8"))
        if e["name"] != "corpus_validation"
    ]
    (out / "attestations.json").write_text(json.dumps(kept) + "\n", encoding="utf-8")
    assert site_inputs.cmd_verify(out) == 1


def test_validator_parity_skips_when_corpus_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entries, commands = _captured_attestations(tmp_path, monkeypatch, corpus_changed=False)
    parity = next(e for e in entries if e["name"] == "validator_parity")
    assert parity["result"] == "skip"
    assert parity["reason"] == "corpus unchanged"
    assert parity["compared"] == {"base": "parent", "head": "core"}
    assert not [c for c in commands if "scripts/publication/validator_parity.py" in c]


def test_validator_parity_uses_the_mirror_form_when_corpus_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries, commands = _captured_attestations(tmp_path, monkeypatch, corpus_changed=True)
    command = _command(commands, "scripts/publication/validator_parity.py")
    assert command[command.index("--base-sha") + 1] == "parent"
    assert command[command.index("--merge-sha") + 1] == "core"
    assert command[command.index("--head-sha") + 1] == "core"
    assert "--allow-partial-validation" in command
    assert "--require-manifest" not in command
    parity = next(e for e in entries if e["name"] == "validator_parity")
    assert parity["result"] == "pass"
    assert parity["compared"] == {"base": "parent", "merge": "core", "head": "core"}


@pytest.mark.parametrize("source", [None, "trunk"])
def test_verify_rejects_unknown_parent_source(tmp_path: Path, source: str | None) -> None:
    out = _bundle(tmp_path)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    if source is None:
        del manifest["parent_source"]
    else:
        manifest["parent_source"] = source
    (out / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    assert site_inputs.cmd_verify(out) == 1


def _site_inputs_workflow() -> str:
    return (ROOT / ".github/workflows/site-inputs.yml").read_text(encoding="utf-8")


def _site_inputs_parent_script() -> str:
    return (ROOT / "scripts/site_inputs_parent.py").read_text(encoding="utf-8")


def test_parent_candidates_come_only_from_develop_runs() -> None:
    assert "scripts/site_inputs_parent.py" in _site_inputs_workflow()
    assert "gh run list --workflow site-inputs.yml" not in _site_inputs_workflow()
    script = _site_inputs_parent_script()
    assert "--branch" in script and "develop" in script
    assert 'TRUNK_BRANCH = "develop"' in script
    assert "head_branch" in script and "conclusion" in script


def test_parent_selection_fails_closed_without_a_bundled_ancestor() -> None:
    workflow = _site_inputs_workflow()
    assert "~1" not in workflow
    script = _site_inputs_parent_script()
    assert '"dispatch"' in script
    assert script.count('"bundle"') >= 2
    assert "dispatch site-inputs.yml with parent_core_sha" in script


def test_dispatched_parent_is_resolved_to_a_full_commit_sha() -> None:
    script = _site_inputs_parent_script()
    assert "rev-parse" in script
    assert "--verify" in script
    assert "--end-of-options" in script
    assert "^[0-9a-f]{40}$" in script
    assert "does not name a commit" in script


def test_parent_ref_resolves_to_a_full_commit_sha() -> None:
    head = site_inputs.must_run("git", "-C", str(site_inputs.ROOT), "rev-parse", "HEAD").strip()
    assert site_inputs.resolve_parent(head, "HEAD") == head
    assert site_inputs.resolve_parent(head, head[:12]) == head


def test_parent_ref_that_names_no_commit_is_rejected() -> None:
    head = site_inputs.must_run("git", "-C", str(site_inputs.ROOT), "rev-parse", "HEAD").strip()
    with pytest.raises(RuntimeError, match="rev-parse"):
        site_inputs.resolve_parent(head, "no-such-ref-for-site-inputs")


def _site_inputs_jobs() -> dict:
    import yaml

    return yaml.safe_load(_site_inputs_workflow())["jobs"]


def test_failed_bundle_build_reports_to_one_issue() -> None:
    report = _site_inputs_jobs()["report-failure"]
    assert report["needs"] == "build"
    assert report["if"] == "failure()"
    assert report["permissions"] == {"issues": "write"}
    script = report["steps"][0]["run"]
    assert 'gh issue list --repo "$REPO" --label site-bundle-failed --state open' in script
    assert 'gh issue comment "$NUMBER"' in script
    assert "gh issue create" in script


def test_bundle_build_job_keeps_read_only_permissions() -> None:
    import yaml

    workflow = yaml.safe_load(_site_inputs_workflow())
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
    assert "permissions" not in _site_inputs_jobs()["build"]


def test_link_targets_cover_inline_reference_and_autolinks() -> None:
    text = (
        'See [a](../../examples/a.py) and [b](<../x.sql> "title").\n'
        "[ref]: ../../pytest.ini\n"
        "Auto <../../scripts/c.py> and [web](https://example.com) and [top](/docs/x.html) and [frag](#here).\n"
    )
    assert site_inputs.link_targets(text) == [
        "../../examples/a.py",
        "../x.sql",
        "../../pytest.ini",
        "../../scripts/c.py",
    ]


def test_referenced_downloads_keep_tracked_files_outside_docs(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    _write(
        docs / "guide/page.md",
        "[t](../../tools/t.tpl#L3) [m](other.md) [n](../../notes.md) [u](../../untracked.py) [d](../../tools)\n",
    )
    tracked = ["tools/t.tpl", "notes.md", "docs/guide/other.md"]
    assert site_inputs.referenced_downloads(docs, tracked) == ["tools/t.tpl"]


@pytest.mark.parametrize(
    ("template", "eula", "other_eula"),
    [
        (
            "_sources/tpc-ds/query_templates/query1.tpl",
            "_sources/tpc-ds/EULA.txt",
            "_sources/tpc-h/EULA.txt",
        ),
        (
            "benchbox/_binaries/tpc-h/templates/queries/1.sql",
            "_sources/tpc-h/EULA.txt",
            "_sources/tpc-ds/EULA.txt",
        ),
    ],
)
@pytest.mark.parametrize("eula_tracked", [True, False])
def test_referenced_downloads_include_matching_tpc_eula_only_when_tracked(
    tmp_path: Path,
    template: str,
    eula: str,
    other_eula: str,
    eula_tracked: bool,
) -> None:
    docs = tmp_path / "docs"
    _write(docs / "guide/page.md", f"[template](../../{template})\n")
    tracked = [template, other_eula, "examples/other.py"]
    if eula_tracked:
        tracked.append(eula)
    expected = sorted([template, eula]) if eula_tracked else [template]
    assert site_inputs.referenced_downloads(docs, tracked) == expected


@pytest.mark.parametrize(
    ("metadata", "eula"),
    [
        ("_sources/tpc-ds/query_templates/templates.lst", "_sources/tpc-ds/EULA.txt"),
        ("benchbox/_binaries/tpc-h/templates/queries/README", "_sources/tpc-h/EULA.txt"),
    ],
)
def test_referenced_downloads_exclude_tpc_eula_for_non_template_files(
    tmp_path: Path,
    metadata: str,
    eula: str,
) -> None:
    docs = tmp_path / "docs"
    _write(docs / "guide/page.md", f"[metadata](../../{metadata})\n")
    assert site_inputs.referenced_downloads(docs, [metadata, eula]) == [metadata]


def test_build_downloads_copies_each_file_at_its_repo_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    docs = tmp_path / "docs"
    _write(docs / "page.md", "[ini](../pytest.ini)\n")
    monkeypatch.setattr(site_inputs, "git_ls_files", lambda: ["pytest.ini"])
    out = tmp_path / "downloads"
    assert site_inputs.build_downloads(out, docs) == ["pytest.ini"]
    assert (out / "pytest.ini").read_bytes() == (site_inputs.ROOT / "pytest.ini").read_bytes()


def test_bundle_schema_three_requires_downloads_and_result_bundles() -> None:
    assert site_inputs.SCHEMA == 3
    assert "downloads" in site_inputs.REQUIRED_MEMBERS
    assert "explorer/bundles" in site_inputs.REQUIRED_MEMBERS


def _fake_explorer_publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, bundles: bool) -> Path:
    out = tmp_path / "explorer"

    def fake_must_run(*cmd: str, cwd: Path = site_inputs.ROOT) -> str:
        _write(out / "results.duckdb", "duckdb-bytes")
        if bundles:
            _write(out / "bundles" / "r1.json", '{"run": {"id": "r1"}}\n')
        return ""

    monkeypatch.setattr(site_inputs, "must_run", fake_must_run)
    return out


def test_snapshot_build_keeps_the_per_result_bundles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _fake_explorer_publish(tmp_path, monkeypatch, bundles=True)
    site_inputs.build_explorer_snapshot(out)
    assert (out / "bundles" / "r1.json").is_file()


def test_snapshot_build_refuses_a_build_without_per_result_bundles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _fake_explorer_publish(tmp_path, monkeypatch, bundles=False)
    with pytest.raises(RuntimeError, match="no per-result bundles"):
        site_inputs.build_explorer_snapshot(out)


def _candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schema: int, certified_by: str = "candidate") -> Path:
    out = _bundle(tmp_path)
    tiny = _write(tmp_path / "tiny.schema.json", '{"type": "object", "required": ["schema_version"]}\n')
    monkeypatch.setattr(site_inputs, "DOCS_DATA_SCHEMA", tiny)
    monkeypatch.setattr(site_inputs, "DOC_CONTRACTS_SCHEMA", tiny)
    _write(out / "docs-data" / site_inputs.DOCS_DATA_FILE, '{"schema_version": 1}\n')
    _write(out / "doc-contracts.json", '{"schema_version": 1}\n')
    if schema == 5:
        shutil.rmtree(out / "docs")
        shutil.rmtree(out / "downloads")
    names = site_inputs.MEMBERS_BY_SCHEMA[schema]
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["schema"] = schema
    manifest["certified_by"] = certified_by
    manifest["members"] = {name: site_inputs.member_digest(out / name) for name in names}
    _write(out / "manifest.json", json.dumps(manifest) + "\n")
    return out


def test_member_sets_per_schema_are_exact() -> None:
    base = set(site_inputs.REQUIRED_MEMBERS)
    assert set(site_inputs.MEMBERS_BY_SCHEMA[3]) == base
    assert set(site_inputs.MEMBERS_BY_SCHEMA[4]) == base | {"docs-data", "doc-contracts.json"}
    assert set(site_inputs.MEMBERS_BY_SCHEMA[5]) == (base - {"docs", "downloads"}) | {"docs-data", "doc-contracts.json"}
    assert site_inputs.SCHEMA == 3
    assert site_inputs.PROFILE_SCHEMAS["exact3"] == 3


def test_default_profile_is_exact3() -> None:
    parser_args = []

    def capture(args: argparse.Namespace) -> int:
        parser_args.append(args)
        return 0

    original = site_inputs.cmd_build
    site_inputs.cmd_build = capture
    try:
        site_inputs.main(["build", "--out", "x"])
    finally:
        site_inputs.cmd_build = original
    assert parser_args[0].profile == "exact3"


@pytest.mark.parametrize("schema", [4, 5])
def test_verify_accepts_candidate_bundles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], schema: int
) -> None:
    assert site_inputs.cmd_verify(_candidate(tmp_path, monkeypatch, schema)) == 0
    assert f"schema {schema}" in capsys.readouterr().out


def test_exact3_verify_rejects_added_contract_members(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _candidate(tmp_path, monkeypatch, 4)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["schema"] = 3
    _write(out / "manifest.json", json.dumps(manifest) + "\n")
    assert site_inputs.cmd_verify(out) == 1


def test_schema5_rejects_docs_member(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _candidate(tmp_path, monkeypatch, 5)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    _write(out / "docs" / "member.txt", "docs")
    manifest["members"]["docs"] = site_inputs.member_digest(out / "docs")
    _write(out / "manifest.json", json.dumps(manifest) + "\n")
    assert site_inputs.cmd_verify(out) == 1


def test_schema4_rejects_missing_member(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _candidate(tmp_path, monkeypatch, 4)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    del manifest["members"]["doc-contracts.json"]
    _write(out / "manifest.json", json.dumps(manifest) + "\n")
    assert site_inputs.cmd_verify(out) == 1


def test_candidate_rejects_publication_certifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _candidate(tmp_path, monkeypatch, 4, certified_by="123456")
    assert site_inputs.cmd_verify(out) == 1
    assert "certified_by" in capsys.readouterr().out


def test_candidate_rejects_schema_invalid_docs_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _candidate(tmp_path, monkeypatch, 4)
    _write(out / "docs-data" / site_inputs.DOCS_DATA_FILE, "{}\n")
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["members"]["docs-data"] = site_inputs.member_digest(out / "docs-data")
    _write(out / "manifest.json", json.dumps(manifest) + "\n")
    assert site_inputs.cmd_verify(out) == 1


def test_candidate_rejects_extra_docs_data_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _candidate(tmp_path, monkeypatch, 4)
    _write(out / "docs-data" / "extra.json", "{}\n")
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["members"]["docs-data"] = site_inputs.member_digest(out / "docs-data")
    _write(out / "manifest.json", json.dumps(manifest) + "\n")
    assert site_inputs.cmd_verify(out) == 1


def test_candidate_build_refuses_a_certifier() -> None:
    args = argparse.Namespace(profile="candidate4", certified_by="123", out="unused")
    with pytest.raises(RuntimeError, match="cannot carry a certifier"):
        site_inputs.cmd_build(args)
