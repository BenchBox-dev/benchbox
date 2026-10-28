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
            profile="schema5",
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


def test_bundle_schema_three_requires_downloads_and_result_bundles() -> None:
    assert "downloads" in site_inputs.REQUIRED_MEMBERS
    assert "explorer/bundles" in site_inputs.REQUIRED_MEMBERS


@pytest.mark.parametrize("profile", ["exact3", "schema4", "candidate4"])
def test_legacy_profile_builds_explain_the_relocated_sources(profile: str, tmp_path: Path) -> None:
    args = argparse.Namespace(
        out=str(tmp_path / "out"),
        core_sha=None,
        parent_core_sha=None,
        parent_source="local",
        certified_by=None,
        profile=profile,
        cmd="build",
    )
    with pytest.raises(RuntimeError, match="sources moved"):
        site_inputs.cmd_build(args)


def test_default_profile_emits_exactly_eleven_members() -> None:
    assert site_inputs.DEFAULT_PROFILE == "schema5"
    assert site_inputs.PROFILE_SCHEMAS["schema5"] == 5
    assert site_inputs.PROFILE_SCHEMAS["candidate5"] == 5
    assert len(site_inputs.MEMBERS_BY_SCHEMA[5]) == 11
    assert "docs-data" in site_inputs.MEMBERS_BY_SCHEMA[5]
    assert "doc-contracts.json" in site_inputs.MEMBERS_BY_SCHEMA[5]


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
    assert site_inputs.PROFILE_SCHEMAS["exact3"] == 3
    assert site_inputs.PROFILE_SCHEMAS["schema4"] == 4
    assert site_inputs.PROFILE_SCHEMAS["schema5"] == 5
    assert site_inputs.PROFILE_SCHEMAS["candidate4"] == 4
    assert site_inputs.PROFILE_SCHEMAS["candidate5"] == 5


def test_default_profile_is_the_published_schema5() -> None:
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
    assert parser_args[0].profile == "schema5"
    assert site_inputs.PROFILE_SCHEMAS[parser_args[0].profile] == 5
    assert parser_args[0].certified_by is None


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


def test_schema4_verify_accepts_a_run_id_certifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _candidate(tmp_path, monkeypatch, 4, certified_by="38216829987")
    assert site_inputs.cmd_verify(out) == 0
    assert "schema 4" in capsys.readouterr().out


@pytest.mark.parametrize("certified_by", ["local", "", "12a", "-1", "1 ", "\u0663", "latest"])
def test_schema4_verify_rejects_other_certifiers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], certified_by: str
) -> None:
    out = _candidate(tmp_path, monkeypatch, 4, certified_by=certified_by)
    assert site_inputs.cmd_verify(out) == 1
    assert "certified_by" in capsys.readouterr().out


def test_schema4_verify_rejects_a_numeric_certifier_stored_as_a_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _candidate(tmp_path, monkeypatch, 4)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["certified_by"] = 123456
    _write(out / "manifest.json", json.dumps(manifest) + "\n")
    assert site_inputs.cmd_verify(out) == 1


@pytest.mark.parametrize("certified_by", ["local", "", "12a", "-1", "1 ", "\u0663", "latest"])
def test_schema5_verify_rejects_other_certifiers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], certified_by: str
) -> None:
    out = _candidate(tmp_path, monkeypatch, 5, certified_by=certified_by)
    assert site_inputs.cmd_verify(out) == 1
    assert "certified_by" in capsys.readouterr().out


def test_schema5_verify_accepts_a_run_id_certifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _candidate(tmp_path, monkeypatch, 5, certified_by="38216829987")
    assert site_inputs.cmd_verify(out) == 0
    assert "schema 5" in capsys.readouterr().out


def test_schema5_verify_rejects_a_numeric_certifier_stored_as_a_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _candidate(tmp_path, monkeypatch, 5)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["certified_by"] = 123456
    _write(out / "manifest.json", json.dumps(manifest) + "\n")
    assert site_inputs.cmd_verify(out) == 1


def test_schema3_verify_ignores_the_certifier(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = _bundle(tmp_path)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["certified_by"] = "local"
    _write(out / "manifest.json", json.dumps(manifest) + "\n")
    assert site_inputs.cmd_verify(out) == 0
    assert "schema 3" in capsys.readouterr().out


def test_schema4_tampered_contract_fails_its_digest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _candidate(tmp_path, monkeypatch, 4, certified_by="123456")
    _write(out / "doc-contracts.json", '{"schema_version": 2}\n')
    assert site_inputs.cmd_verify(out) == 1


def test_schema4_rejects_an_extra_member(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _candidate(tmp_path, monkeypatch, 4, certified_by="123456")
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    _write(out / "extra.json", "{}\n")
    manifest["members"]["extra.json"] = site_inputs.member_digest(out / "extra.json")
    _write(out / "manifest.json", json.dumps(manifest) + "\n")
    assert site_inputs.cmd_verify(out) == 1


def test_certifier_rules_match_the_site_verifier() -> None:
    assert site_inputs.certifier_errors(3, "local") == []
    assert site_inputs.certifier_errors(4, "candidate") == []
    assert site_inputs.certifier_errors(4, "42") == []
    assert site_inputs.certifier_errors(4, "local")
    assert site_inputs.certifier_errors(5, "candidate") == []
    assert site_inputs.certifier_errors(5, "42") == []
    assert site_inputs.certifier_errors(5, 42)
    assert site_inputs.certifier_errors(5, "local")


def test_default_certifier_is_never_local_for_schema4() -> None:
    assert site_inputs.default_certifier("exact3") == "local"
    for profile in ("schema4", "schema5", "candidate4", "candidate5"):
        assert (
            site_inputs.certifier_errors(site_inputs.PROFILE_SCHEMAS[profile], site_inputs.default_certifier(profile))
            == []
        )


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


@pytest.mark.parametrize("profile", ["candidate4", "candidate5"])
def test_candidate_build_refuses_a_certifier(profile: str) -> None:
    args = argparse.Namespace(profile=profile, certified_by="123", out="unused")
    with pytest.raises(RuntimeError, match="cannot carry a certifier"):
        site_inputs.cmd_build(args)


def test_published_schema4_build_refuses_the_local_certifier() -> None:
    args = argparse.Namespace(profile="schema4", certified_by="local", out="unused")
    with pytest.raises(RuntimeError, match="certified_by candidate or a run id"):
        site_inputs.cmd_build(args)
