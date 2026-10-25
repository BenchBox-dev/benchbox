from __future__ import annotations

import copy
import hashlib
import json
import stat
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import release_artifact_consumer as consumer

pytestmark = [pytest.mark.unit, pytest.mark.medium]
ROOT = Path(__file__).resolve().parents[3]
SHA = "a" * 40
WHEEL = "benchbox-0.4.2-py3-none-any.whl"
SDIST = "benchbox-0.4.2.tar.gz"


@pytest.fixture
def metadata():
    repository = {"id": 1135770337, "full_name": consumer.REPOSITORY}
    run = {
        "id": 7,
        "run_attempt": 2,
        "head_sha": SHA,
        "event": "push",
        "head_branch": "develop",
        "path": consumer.WORKFLOW,
        "status": "completed",
        "conclusion": "success",
        "created_at": "2026-09-30T00:00:00Z",
        "repository": repository,
        "head_repository": copy.deepcopy(repository),
    }
    job = {
        "id": 9,
        "name": "dist-artifact",
        "run_id": 7,
        "run_attempt": 2,
        "head_sha": SHA,
        "status": "completed",
        "conclusion": "success",
    }
    artifact = {
        "id": 11,
        "name": f"dist-{SHA}-attempt-2",
        "expired": False,
        "expires_at": "2099-01-01T00:00:00Z",
        "digest": "sha256:" + "b" * 64,
        "size_in_bytes": 1000,
        "workflow_run": {
            "id": 7,
            "head_sha": SHA,
            "repository_id": repository["id"],
            "head_repository_id": repository["id"],
        },
    }
    state = {
        "repository": repository,
        "runs": [run],
        "run": run,
        "jobs": [job],
        "job": job,
        "artifacts": [artifact],
        "artifact": artifact,
        "calls": [],
    }

    def api(path):
        state["calls"].append(path)
        parsed = urlsplit(path)
        query = parse_qs(parsed.query)
        if not path:
            return copy.deepcopy(state["repository"])
        groups = {
            "actions/workflows/trunk.yml/runs": ("runs", "workflow_runs"),
            "actions/runs/7/attempts/2/jobs": ("jobs", "jobs"),
            "actions/runs/7/artifacts": ("artifacts", "artifacts"),
        }
        if parsed.path in groups:
            source, key = groups[parsed.path]
            page = int(query["page"][0])
            rows = state[source]
            return {"total_count": len(rows), key: copy.deepcopy(rows[(page - 1) * 100 : page * 100])}
        if path == "actions/runs/7":
            return copy.deepcopy(state["run"])
        if path == "actions/artifacts/11":
            return copy.deepcopy(state["artifact"])
        raise AssertionError(f"unexpected API path: {path}")

    state["api"] = api
    return state


@pytest.fixture
def distributions(tmp_path, metadata):
    directory = tmp_path / "dist"
    directory.mkdir()
    (directory / WHEEL).write_bytes(b"original wheel")
    (directory / SDIST).write_bytes(b"original source distribution")
    sums = "".join(
        f"{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}\n" for name in (WHEEL, SDIST)
    )
    (directory / "SHA256SUMS").write_text(sums)
    receipt = consumer.producer_receipt(directory, metadata["run"], metadata["job"])
    (directory / consumer.PRODUCER_RECEIPT).write_text(json.dumps(receipt))
    return directory


def write_zip(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload, mode in members:
            entry = zipfile.ZipInfo(name)
            entry.external_attr = mode << 16
            archive.writestr(entry, payload)
    return {"digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}


def zip_members(directory):
    return [(path.name, path.read_bytes(), stat.S_IFREG | 0o644) for path in directory.iterdir()]


def test_selects_exact_latest_successful_attempt(metadata):
    run, job, artifact = consumer.select_producer(SHA, metadata["api"])
    assert (run["id"], run["run_attempt"], job["id"], artifact["id"]) == (7, 2, 9, 11)
    assert "actions/runs/7/attempts/2/jobs?per_page=100&page=1" in metadata["calls"]


def test_lists_only_trunk_push_runs_on_develop(metadata):
    consumer.select_producer(SHA, metadata["api"])
    listing = [path for path in metadata["calls"] if path.startswith("actions/workflows/")]
    assert listing
    for path in listing:
        parsed = urlsplit(path)
        assert parsed.path == "actions/workflows/trunk.yml/runs"
        query = parse_qs(parsed.query)
        assert query["head_sha"] == [SHA]
        assert query["event"] == ["push"]
        assert query["branch"] == ["develop"]


def test_refuses_commit_without_any_trunk_run(metadata):
    metadata["runs"] = []
    with pytest.raises(ValueError, match="no trunk producer"):
        consumer.select_producer(SHA, metadata["api"])


def test_refuses_run_without_distribution_artifact(metadata):
    metadata["artifacts"] = []
    with pytest.raises(ValueError, match="missing or ambiguous"):
        consumer.select_producer(SHA, metadata["api"])


def test_refuses_run_without_distribution_job(metadata):
    metadata["jobs"] = []
    with pytest.raises(ValueError, match="distribution producer job"):
        consumer.select_producer(SHA, metadata["api"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("event", "pull_request"),
        ("event", "merge_group"),
        ("event", "workflow_dispatch"),
        ("head_branch", "feature"),
        ("head_branch", None),
        ("path", ".github/workflows/ci.yml"),
        ("path", ".github/workflows/release.yml"),
        ("head_sha", "c" * 40),
        ("status", "in_progress"),
        ("conclusion", "failure"),
        ("conclusion", "cancelled"),
        ("run_attempt", True),
    ],
)
def test_refuses_wrong_or_unsuccessful_run(metadata, field, value):
    metadata["run"][field] = value
    with pytest.raises(ValueError):
        consumer.select_producer(SHA, metadata["api"])


@pytest.mark.parametrize("field", ["repository", "head_repository"])
def test_refuses_foreign_run_repository(metadata, field):
    metadata["run"][field] = {"id": 999, "full_name": consumer.REPOSITORY}
    with pytest.raises(ValueError, match="repository"):
        consumer.select_producer(SHA, metadata["api"])


def test_repository_name_matches_without_regard_to_case(metadata):
    for record in (metadata["repository"], metadata["run"]["repository"], metadata["run"]["head_repository"]):
        record["full_name"] = "BenchBox-dev/benchbox"
    run, _job, _artifact = consumer.select_producer(SHA, metadata["api"])
    assert run["id"] == 7


def test_refuses_run_from_another_repository_name(metadata):
    metadata["repository"]["full_name"] = "attacker/benchbox"
    with pytest.raises(ValueError, match="wrong repository"):
        consumer.select_producer(SHA, metadata["api"])


def test_never_uses_old_green_run_after_new_failed_run(metadata):
    older = copy.deepcopy(metadata["run"])
    older.update(id=6, created_at="2026-09-29T00:00:00Z")
    metadata["runs"].insert(0, older)
    metadata["run"]["conclusion"] = "failure"
    with pytest.raises(ValueError, match="latest producer"):
        consumer.select_producer(SHA, metadata["api"])
    assert "actions/runs/6" not in metadata["calls"]


def test_later_failed_producer_on_second_page_cannot_be_hidden(metadata):
    metadata["runs"] = [
        {**copy.deepcopy(metadata["run"]), "id": index + 100, "created_at": "2026-09-29T00:00:00Z"}
        for index in range(100)
    ] + [metadata["run"]]
    metadata["run"]["conclusion"] = "failure"
    with pytest.raises(ValueError, match="latest producer"):
        consumer.select_producer(SHA, metadata["api"])
    assert any("page=2" in path for path in metadata["calls"])


def test_run_attempt_change_between_listing_and_read_fails(metadata):
    metadata["runs"] = [copy.deepcopy(metadata["run"])]
    metadata["run"]["run_attempt"] = 3
    with pytest.raises(ValueError, match="attempt changed"):
        consumer.select_producer(SHA, metadata["api"])


@pytest.mark.parametrize(
    "field,value",
    [("run_id", 8), ("run_attempt", 1), ("head_sha", "d" * 40), ("status", "in_progress"), ("conclusion", "failure")],
)
def test_refuses_wrong_or_unsuccessful_producer_job(metadata, field, value):
    metadata["job"][field] = value
    with pytest.raises(ValueError, match="job"):
        consumer.select_producer(SHA, metadata["api"])


def test_ambiguous_producer_job_fails(metadata):
    metadata["jobs"].append({**metadata["job"], "id": 10})
    with pytest.raises(ValueError, match="ambiguous"):
        consumer.select_producer(SHA, metadata["api"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", f"dist-{SHA}"),
        ("name", f"dist-{SHA}-attempt-1"),
        ("expired", True),
        ("expires_at", "2020-01-01T00:00:00Z"),
        ("digest", None),
        ("size_in_bytes", 0),
        ("size_in_bytes", consumer.MAX_PAYLOAD_BYTES + 1),
    ],
)
def test_refuses_legacy_stale_or_invalid_artifact(metadata, field, value):
    metadata["artifact"][field] = value
    with pytest.raises(ValueError):
        consumer.select_producer(SHA, metadata["api"])


@pytest.mark.parametrize(
    "field,value", [("id", 8), ("head_sha", "d" * 40), ("repository_id", 99), ("head_repository_id", 99)]
)
def test_refuses_foreign_artifact_origin(metadata, field, value):
    metadata["artifact"]["workflow_run"][field] = value
    with pytest.raises(ValueError, match="artifact producer"):
        consumer.select_producer(SHA, metadata["api"])


def test_changed_artifact_metadata_fails(metadata):
    metadata["artifacts"] = [copy.deepcopy(metadata["artifact"])]
    metadata["artifact"]["digest"] = "sha256:" + "c" * 64
    with pytest.raises(ValueError, match="metadata changed"):
        consumer.select_producer(SHA, metadata["api"])


def test_duplicate_artifact_identities_fail(metadata):
    metadata["artifacts"].append({**metadata["artifact"], "id": 12})
    with pytest.raises(ValueError, match="ambiguous"):
        consumer.select_producer(SHA, metadata["api"])


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"total_count": 2, "jobs": [{"id": 1}]},
        {"total_count": True, "jobs": []},
        {"total_count": 2, "jobs": [{"id": 1}, {"id": 1}]},
    ],
)
def test_incomplete_or_malformed_pages_fail(payload):
    with pytest.raises(ValueError):
        consumer._pages(lambda _: payload, "jobs", "jobs")


def test_population_drift_between_pages_fails():
    pages = iter(
        [
            {"total_count": 101, "jobs": [{"id": number} for number in range(1, 101)]},
            {"total_count": 102, "jobs": [{"id": 101}]},
        ]
    )
    with pytest.raises(ValueError, match="population changed"):
        consumer._pages(lambda _: next(pages), "jobs", "jobs")


def test_real_zip_and_producer_receipt_preserve_all_bytes(tmp_path, distributions, metadata):
    archive = tmp_path / "artifact.zip"
    artifact = write_zip(archive, zip_members(distributions))
    payload = consumer.verify_archive(archive, artifact)
    assert payload[WHEEL] == b"original wheel"
    receipt = consumer.verify_producer_receipt(distributions, metadata["run"], metadata["job"])
    assert receipt["run_attempt"] == 2 and receipt["job_id"] == 9
    assert set(receipt["files"]) == {WHEEL, SDIST}


def test_zip_digest_checked_before_archive_parsing(tmp_path):
    archive = tmp_path / "not-a-zip"
    archive.write_bytes(b"tampered archive")
    with pytest.raises(ValueError, match="ZIP digest"):
        consumer.verify_archive(archive, {"digest": "sha256:" + "a" * 64})


@pytest.mark.parametrize(
    "name",
    [
        "../escaped.whl",
        "/absolute.whl",
        "C:drive.whl",
        "folder/nested.whl",
        "folder\\nested.whl",
        "trailing.whl ",
        "trailing.whl.",
    ],
)
def test_unsafe_zip_member_refused(tmp_path, distributions, name):
    members = zip_members(distributions)
    members[0] = (name, members[0][1], members[0][2])
    archive = tmp_path / "artifact.zip"
    artifact = write_zip(archive, members)
    with pytest.raises(ValueError, match="unsafe"):
        consumer.verify_archive(archive, artifact)
    assert not (tmp_path / "escaped.whl").exists()


@pytest.mark.parametrize("mode", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFDIR])
def test_nonregular_zip_member_refused(tmp_path, distributions, mode):
    members = zip_members(distributions)
    members[0] = (members[0][0], members[0][1], mode | 0o644)
    archive = tmp_path / "artifact.zip"
    artifact = write_zip(archive, members)
    with pytest.raises(ValueError, match="nonregular"):
        consumer.verify_archive(archive, artifact)


def test_case_alias_duplicate_zip_member_refused(tmp_path, distributions):
    members = zip_members(distributions)
    members[1] = (members[0][0].upper(), members[1][1], members[1][2])
    archive = tmp_path / "artifact.zip"
    artifact = write_zip(archive, members)
    with pytest.raises(ValueError, match="duplicate"):
        consumer.verify_archive(archive, artifact)


def test_legacy_three_file_zip_refused(tmp_path, distributions):
    archive = tmp_path / "artifact.zip"
    artifact = write_zip(archive, [row for row in zip_members(distributions) if row[0] != consumer.PRODUCER_RECEIPT])
    with pytest.raises(ValueError, match="legacy"):
        consumer.verify_archive(archive, artifact)


def test_wheel_tampering_refused_even_with_new_zip_digest(tmp_path, distributions):
    (distributions / WHEEL).write_bytes(b"altered wheel")
    archive = tmp_path / "artifact.zip"
    artifact = write_zip(archive, zip_members(distributions))
    with pytest.raises(ValueError, match="distribution hash"):
        consumer.verify_archive(archive, artifact)


@pytest.mark.parametrize("sums", ["", "invalid\n", "a" * 64 + "  ../escaped.whl\n", "a" * 64 + "  unexpected.whl\n"])
def test_missing_or_malformed_distribution_hash_records_refused(tmp_path, distributions, sums):
    (distributions / "SHA256SUMS").write_text(sums)
    archive = tmp_path / "artifact.zip"
    artifact = write_zip(archive, zip_members(distributions))
    with pytest.raises(ValueError, match="SHA256SUMS"):
        consumer.verify_archive(archive, artifact)


def test_duplicate_hash_record_refused(tmp_path, distributions):
    sums = distributions / "SHA256SUMS"
    sums.write_text(sums.read_text() + sums.read_text().splitlines()[0] + "\n")
    archive = tmp_path / "artifact.zip"
    artifact = write_zip(archive, zip_members(distributions))
    with pytest.raises(ValueError, match="duplicate SHA256SUMS"):
        consumer.verify_archive(archive, artifact)


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_attempt", 1),
        ("job_id", 8),
        ("run_id", 8),
        ("schema", True),
        ("repository", "attacker/fork"),
        ("workflow_path", "other.yml"),
    ],
)
def test_producer_receipt_identity_substitution_refused(distributions, metadata, field, value):
    path = distributions / consumer.PRODUCER_RECEIPT
    receipt = json.loads(path.read_text())
    receipt[field] = value
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="producer receipt"):
        consumer.verify_producer_receipt(distributions, metadata["run"], metadata["job"])


def test_payload_and_hashes_cannot_be_replaced_without_producer_receipt(distributions, metadata):
    (distributions / WHEEL).write_bytes(b"replacement wheel")
    sums = "".join(
        f"{hashlib.sha256((distributions / name).read_bytes()).hexdigest()}  {name}\n" for name in (WHEEL, SDIST)
    )
    (distributions / "SHA256SUMS").write_text(sums)
    with pytest.raises(ValueError, match="producer receipt"):
        consumer.verify_producer_receipt(distributions, metadata["run"], metadata["job"])


def test_duplicate_json_key_refused(distributions, metadata):
    path = distributions / consumer.PRODUCER_RECEIPT
    path.write_text('{"schema":1,"schema":1}')
    with pytest.raises(ValueError, match="duplicate JSON key"):
        consumer.verify_producer_receipt(distributions, metadata["run"], metadata["job"])


def test_admission_has_no_build_publish_or_attestation_command():
    import ast

    tree = ast.parse((ROOT / "scripts/release_artifact_consumer.py").read_text())
    literals = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert not set(literals) & {"uv build", "build", "publish", "pypi", "test-pypi", "attestation"}


def test_encrypted_zip_member_refused(tmp_path, distributions):
    archive = tmp_path / "artifact.zip"
    write_zip(archive, zip_members(distributions))
    data = bytearray(archive.read_bytes())
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        position = data.index(signature)
        data[position + offset] |= 1
    archive.write_bytes(data)
    artifact = {"digest": "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()}
    with pytest.raises(ValueError, match="nonregular"):
        consumer.verify_archive(archive, artifact)


def test_declared_payload_size_limit_refused(tmp_path, distributions, monkeypatch):
    archive = tmp_path / "artifact.zip"
    artifact = write_zip(archive, zip_members(distributions))
    monkeypatch.setattr(consumer, "MAX_PAYLOAD_BYTES", 10)
    with pytest.raises(ValueError, match="size limit"):
        consumer.verify_archive(archive, artifact)


def test_pending_other_run_for_same_sha_fails(metadata):
    older = {**copy.deepcopy(metadata["run"]), "id": 6, "created_at": "2026-09-29T00:00:00Z", "status": "queued"}
    metadata["runs"].insert(0, older)
    with pytest.raises(ValueError, match="still pending"):
        consumer.select_producer(SHA, metadata["api"])


def test_rerun_of_older_run_cannot_be_hidden_behind_newer_creation_time(metadata):
    older = {
        **copy.deepcopy(metadata["run"]),
        "id": 6,
        "created_at": "2026-09-29T00:00:00Z",
        "run_started_at": "2026-10-01T00:00:00Z",
    }
    metadata["runs"].insert(0, older)
    with pytest.raises(ValueError, match="newer attempt"):
        consumer.select_producer(SHA, metadata["api"])


def test_extra_zip_member_refused(tmp_path, distributions):
    members = zip_members(distributions)
    members[-1] = ("extra.txt", b"extra", stat.S_IFREG | 0o644)
    archive = tmp_path / "artifact.zip"
    artifact = write_zip(archive, members)
    with pytest.raises(ValueError, match="membership"):
        consumer.verify_archive(archive, artifact)


@pytest.mark.parametrize("value", ["", "not-a-date", "2026-09-30T00:00:00", None])
def test_malformed_producer_timestamp_refused(metadata, value):
    metadata["run"]["created_at"] = value
    with pytest.raises(ValueError):
        consumer.select_producer(SHA, metadata["api"])


def test_timestamp_offsets_cannot_hide_newer_failed_producer(metadata):
    older = {**copy.deepcopy(metadata["run"]), "id": 6, "created_at": "2026-09-30T01:00:00+02:00"}
    metadata["runs"].insert(0, older)
    metadata["run"]["conclusion"] = "failure"
    with pytest.raises(ValueError, match="latest producer"):
        consumer.select_producer(SHA, metadata["api"])


@pytest.mark.parametrize("population,key", [("jobs", "jobs"), ("artifacts", "artifacts")])
def test_producer_and_artifact_on_later_pages_are_not_lost(metadata, population, key):
    target = metadata[population][0]
    metadata[population] = [
        {**copy.deepcopy(target), "id": index + 100, "name": "irrelevant"} for index in range(100)
    ] + [target]
    assert consumer.select_producer(SHA, metadata["api"])[2]["id"] == 11
    assert any(key in path and "page=2" in path for path in metadata["calls"])


def test_dos_directory_attribute_refused(tmp_path, distributions):
    archive = tmp_path / "artifact.zip"
    write_zip(archive, zip_members(distributions))
    data = bytearray(archive.read_bytes())
    position = data.index(b"PK\x01\x02")
    data[position + 38] |= 0x10
    archive.write_bytes(data)
    artifact = {"digest": "sha256:" + hashlib.sha256(data).hexdigest()}
    with pytest.raises(ValueError, match="nonregular"):
        consumer.verify_archive(archive, artifact)


def _producer_wait(metadata, listings, **options):
    pending = list(listings)
    sleeps = []
    now = [0.0]

    def api(path):
        metadata["jobs"] = pending.pop(0) if len(pending) > 1 else pending[0]
        return metadata["api"](path)

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    def job():
        return consumer.running_producer_job(7, 2, api, sleep, lambda: now[0], **options)

    return job, sleeps


def _running(metadata, **changes):
    return {**metadata["job"], "status": "in_progress", "conclusion": None, **changes}


def test_producer_job_running_is_returned_without_waiting(metadata, capsys):
    job, sleeps = _producer_wait(metadata, [[_running(metadata)]])
    assert job()["id"] == 9 and sleeps == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("lagging", [[], "queued"])
def test_producer_job_listing_lag_is_waited_out(metadata, lagging, capsys):
    first = [] if lagging == [] else [_running(metadata, status="queued")]
    job, sleeps = _producer_wait(metadata, [first, first, [_running(metadata)]])
    assert job()["id"] == 9 and len(sleeps) == 2
    out = capsys.readouterr().out
    assert out.startswith("::notice::") and out.count("\n") == 1
    assert "waited 6.0s" in out and "run 7 attempt 2" in out
    assert ("0 job(s) named dist-artifact: none" if lagging == [] else "id=9 run_attempt=2 status=queued") in out


def test_producer_job_that_never_runs_fails_with_diagnostics(metadata):
    job, sleeps = _producer_wait(metadata, [[_running(metadata, status="queued")]], wait_seconds=10, poll_seconds=3)
    with pytest.raises(ValueError, match=r"did not become running within 10s.*id=9 run_attempt=2 status=queued"):
        job()
    assert sleeps == [3, 3, 3, 3]


def test_missing_producer_job_fails_after_bounded_wait(metadata):
    job, _sleeps = _producer_wait(metadata, [[]], wait_seconds=5, poll_seconds=2)
    with pytest.raises(ValueError, match=r"0 job\(s\) named dist-artifact: none"):
        job()


def test_duplicate_producer_jobs_fail_immediately_with_diagnostics(metadata):
    jobs = [_running(metadata), _running(metadata, id=10)]
    job, sleeps = _producer_wait(metadata, [jobs])
    with pytest.raises(ValueError, match=r"not unique: 2 job\(s\).*id=9 .*id=10 "):
        job()
    assert sleeps == []


def test_completed_producer_job_fails_immediately(metadata):
    job, sleeps = _producer_wait(metadata, [[metadata["job"]]])
    with pytest.raises(ValueError, match=r"not running: 1 job\(s\).*status=completed"):
        job()
    assert sleeps == []
