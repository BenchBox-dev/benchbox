from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path(".github/sqlglot-delivery.json")
PIN = "benchbox/_vendor/sqlglot.json"
BASELINE = "scripts/sqlglot_runtime_baseline.json"
LICENSE_NOTICE = "benchbox/_vendor/SQLGLOT_LICENSE.txt"
KILL_SWITCH = "SQLGLOT_DELIVERY_ENABLED"
HOLD_LABEL = "no-auto-merge"
BOT_NAME = "benchbox-bot"
BOT_EMAIL = "bot@benchbox.dev"
PLAN_FILE = "plan.json"
PULL_REQUEST_FILE = "pull-request.json"
BUNDLE_FILE = "adoption.bundle"
RECORD_FILE = "delivery.json"
SUMS_FILE = "SHA256SUMS"
EXIT_EXCEPTION = 3
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
RECEIPT_RE = re.compile(r"^delivered benchbox-sqlglot (\d+\.\d+\.\d+\.\d+) sha256:([0-9a-f]{64})$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ARCHIVE_RE = re.compile(r"^benchbox/_vendor/benchbox_sqlglot-\d+\.\d+\.\d+\.\d+-py3-none-any\.whl\.zip$")
MARKER_RE = re.compile(r"<!-- sqlglot-delivery: (\{[^\n]*?\}) -->")
MARKER_KEYS = ("forkSha", "runAttempt", "runId", "sha256", "upstreamSha", "version")
GREEN_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
Runner = Callable[[Sequence[str], Path | None, Mapping[str, str] | None], "subprocess.CompletedProcess[str]"]


class DeliveryError(Exception):
    pass


class ProducerException(DeliveryError):
    pass


class Api(Protocol):
    def request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> Any: ...

    def download(self, path: str) -> bytes: ...


class GhApi:
    def request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        command = ["gh", "api", "--method", method, path]
        for key, value in (params or {}).items():
            command += ["-f", f"{key}={value}"]
        payload = None
        if body is not None:
            command += ["--input", "-"]
            payload = json.dumps(body)
        result = subprocess.run(command, input=payload, capture_output=True, text=True, check=False)
        if result.returncode:
            raise DeliveryError(
                f"{method} {path} failed: {result.stderr.strip()[-400:] or result.stdout.strip()[-400:]}"
            )
        return json.loads(result.stdout) if result.stdout.strip() else None

    def download(self, path: str) -> bytes:
        result = subprocess.run(["gh", "api", path], capture_output=True, check=False)
        if result.returncode:
            raise DeliveryError(f"GET {path} failed: {result.stderr.decode(errors='replace').strip()[-400:]}")
        return result.stdout


@dataclass(frozen=True)
class Config:
    producer: str
    workflow_path: str
    branch: str
    events: tuple[str, ...]
    artifact_prefix: str
    record_schema: int
    record_kind: str
    consumer: str
    base: str
    branch_prefix: str
    disarm_actors: tuple[str, ...]
    suppressed: frozenset[tuple[str, str]]

    @property
    def workflow_file(self) -> str:
        return PurePosixPath(self.workflow_path).name


def load_config(path: Path) -> Config:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        producer, consumer, record = raw["producer"], raw["consumer"], raw["producer"]["record"]
        suppressed = set()
        for entry in raw["suppressed"]:
            fork_sha, digest = entry["forkSha"], entry["sha256"]
            if not SHA1_RE.fullmatch(fork_sha) or not SHA256_RE.fullmatch(digest) or not str(entry["reason"]).strip():
                raise ValueError("a suppressed entry needs forkSha, sha256 and a reason")
            suppressed.add((fork_sha, digest))
        config = Config(
            producer=producer["repository"],
            workflow_path=producer["workflowPath"],
            branch=producer["branch"],
            events=tuple(producer["events"]),
            artifact_prefix=producer["artifactPrefix"],
            record_schema=record["schema"],
            record_kind=record["kind"],
            consumer=consumer["repository"],
            base=consumer["base"],
            branch_prefix=consumer["branchPrefix"],
            disarm_actors=tuple(consumer["trunkRedDisarmActors"]),
            suppressed=frozenset(suppressed),
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise DeliveryError(f"unreadable delivery trust root {path}: {error}") from error
    if raw.get("schema") != 1 or not config.events or not config.artifact_prefix:
        raise DeliveryError(f"unsupported delivery trust root {path}")
    return config


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    return (os.environ if environ is None else environ).get(KILL_SWITCH, "") == "true"


def version_key(version: str) -> tuple[int, ...]:
    if not VERSION_RE.fullmatch(version):
        raise DeliveryError(f"not a four-part runtime version: {version!r}")
    return tuple(int(part) for part in version.split("."))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DeliveryError(message)


def paged(api: Api, path: str, key: str | None = None, params: Mapping[str, Any] | None = None) -> list[Any]:
    items: list[Any] = []
    total: int | None = None
    page = 1
    while True:
        data = api.request("GET", path, {**(params or {}), "per_page": 100, "page": page})
        if key is None:
            batch = data
        else:
            batch = data[key]
            total = data.get("total_count", total)
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    if total is not None and total != len(items):
        raise DeliveryError(f"{path} listed {len(items)} items but reports total_count {total}; refusing partial data")
    return items


def same_repository(left: object, right: str) -> bool:
    return isinstance(left, str) and left.casefold() == right.casefold()


def trusted_run(run: Mapping[str, Any], config: Config) -> bool:
    paths = {config.workflow_path, f"{config.workflow_path}@refs/heads/{config.branch}"}
    return (
        run.get("path") in paths
        and run.get("head_branch") == config.branch
        and run.get("event") in config.events
        and same_repository((run.get("repository") or {}).get("full_name"), config.producer)
        and same_repository((run.get("head_repository") or {}).get("full_name"), config.producer)
    )


@dataclass(frozen=True)
class Receipt:
    version: str
    sha256: str
    run: Mapping[str, Any]
    attempt: int
    job_name: str

    @property
    def order(self) -> tuple[int, int]:
        return (int(self.run["id"]), self.attempt)


def collect_receipts(api: Api, config: Config) -> tuple[list[Receipt], int]:
    runs = paged(api, f"repos/{config.producer}/actions/workflows/{config.workflow_file}/runs", "workflow_runs")
    receipts: list[Receipt] = []
    untrusted = 0
    for run in runs:
        if not trusted_run(run, config):
            untrusted += 1
            continue
        for attempt in range(1, int(run["run_attempt"]) + 1):
            jobs = paged(api, f"repos/{config.producer}/actions/runs/{run['id']}/attempts/{attempt}/jobs", "jobs")
            for job in jobs:
                match = RECEIPT_RE.fullmatch(str(job.get("name")))
                if match and job.get("status") == "completed" and job.get("conclusion") == "success":
                    receipts.append(Receipt(match.group(1), match.group(2), run, attempt, job["name"]))
    return receipts, untrusted


def sums_table(text: str) -> dict[str, str]:
    table: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split(None, 1)
        require(len(fields) == 2 and SHA256_RE.fullmatch(fields[0]) is not None, "SHA256SUMS has a malformed line")
        name = fields[1].strip().removeprefix("*")
        require(name not in table, f"SHA256SUMS lists {name} twice")
        table[name] = fields[0]
    return table


def verify_delivery(
    archive: bytes, receipt: Receipt, artifact_name: str, config: Config
) -> tuple[dict[str, Any], bytes, str]:
    run = receipt.run
    try:
        bundle = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile as error:
        raise DeliveryError(f"delivery artifact {artifact_name} is not a zip archive") from error
    with bundle:
        names = bundle.namelist()
        require(len(names) == len(set(names)), "delivery artifact has duplicate members")
        for name in names:
            parts = PurePosixPath(name).parts
            require(len(parts) == 1 and name not in {"", ".", ".."} and "\\" not in name, f"unsafe member {name!r}")
        require(RECORD_FILE in names and SUMS_FILE in names, "delivery artifact lacks its record or SHA256SUMS")
        try:
            record = json.loads(bundle.read(RECORD_FILE))
        except ValueError as error:
            raise DeliveryError("delivery.json is not valid JSON") from error
        require(isinstance(record, dict), "delivery.json is not an object")
        require(record.get("schema") == config.record_schema, "delivery record schema is not trusted")
        require(record.get("kind") == config.record_kind, "delivery record kind is not trusted")
        require(record.get("version") == receipt.version, "delivery record version differs from the receipt")
        wheel = record.get("wheel")
        require(isinstance(wheel, dict), "delivery record has no wheel")
        filename = f"benchbox_sqlglot-{receipt.version}-py3-none-any.whl"
        require(wheel.get("filename") == filename, "delivery record wheel filename is unexpected")
        require(wheel.get("sha256") == receipt.sha256, "delivery record wheel hash differs from the receipt")
        for key in ("forkSha", "upstreamSha"):
            require(
                isinstance(record.get(key), str) and SHA1_RE.fullmatch(record[key]) is not None,
                f"delivery record {key} is not a commit",
            )
        source, delivery = record.get("source"), record.get("delivery")
        require(isinstance(source, dict) and isinstance(delivery, dict), "delivery record lacks source or delivery")
        require(same_repository(source.get("repository"), config.producer), "delivery record source repository")
        require(same_repository(delivery.get("repository"), config.producer), "delivery record repository")
        require(delivery.get("workflowPath") == config.workflow_path, "delivery record workflow path")
        require(delivery.get("runId") == run["id"], "delivery record run id differs from the trusted run")
        require(delivery.get("runAttempt") == receipt.attempt, "delivery record attempt differs from the receipt")
        require(
            delivery.get("ref") in {config.branch, f"refs/heads/{config.branch}"}, "delivery record ref is not trusted"
        )
        require(
            delivery.get("event") == run.get("event") and delivery.get("event") in config.events,
            "delivery record event is not trusted",
        )
        require(delivery.get("commit") == run.get("head_sha"), "delivery record commit differs from the run")
        require(delivery.get("jobName") == receipt.job_name, "delivery record job differs from the receipt")
        require(delivery.get("artifactName") == artifact_name, "delivery record artifact name differs")
        sums = sums_table(bundle.read(SUMS_FILE).decode("utf-8"))
        sdist = record.get("sdist")
        expected_members = {RECORD_FILE, SUMS_FILE, filename}
        if sdist is not None:
            require(isinstance(sdist, dict) and sdist.get("filename") in sums, "delivery sdist is not in SHA256SUMS")
            expected_members.add(sdist["filename"])
            require(sums[sdist["filename"]] == sdist.get("sha256"), "delivery sdist hash differs from SHA256SUMS")
        require(set(names) == expected_members, "delivery artifact members differ from the record")
        require(set(sums) == expected_members - {RECORD_FILE, SUMS_FILE}, "SHA256SUMS differs from the record")
        for name, digest in sums.items():
            require(hashlib.sha256(bundle.read(name)).hexdigest() == digest, f"{name} does not match SHA256SUMS")
        wheel_bytes = bundle.read(filename)
    require(sums[filename] == receipt.sha256, "SHA256SUMS wheel hash differs from the receipt")
    require(hashlib.sha256(wheel_bytes).hexdigest() == receipt.sha256, "wheel bytes do not match the receipt hash")
    require(wheel.get("size") == len(wheel_bytes), "delivery record wheel size differs from the wheel")
    return record, wheel_bytes, filename


def locate_and_verify(api: Api, config: Config, candidates: Sequence[Receipt]) -> dict[str, Any]:
    for receipt in sorted(candidates, key=lambda item: item.order, reverse=True):
        run_id = int(receipt.run["id"])
        name = f"{config.artifact_prefix}{receipt.attempt}"
        artifacts = paged(api, f"repos/{config.producer}/actions/runs/{run_id}/artifacts", "artifacts")
        found = [
            item
            for item in artifacts
            if item.get("name") == name
            and not item.get("expired")
            and (item.get("workflow_run") or {}).get("id") == run_id
        ]
        if not found:
            continue
        artifact = max(found, key=lambda item: int(item["id"]))
        archive = api.download(f"repos/{config.producer}/actions/artifacts/{artifact['id']}/zip")
        require(len(archive) <= MAX_ARTIFACT_BYTES, f"delivery artifact {name} is implausibly large")
        digest = artifact.get("digest")
        if digest is not None:
            require(digest == f"sha256:{hashlib.sha256(archive).hexdigest()}", f"artifact {name} digest mismatch")
        record, wheel, filename = verify_delivery(archive, receipt, name, config)
        return {
            "receipt": receipt,
            "record": record,
            "wheel": wheel,
            "filename": filename,
            "artifact": artifact,
            "archive": archive,
        }
    raise DeliveryError(
        f"no unexpired {config.artifact_prefix}<attempt> artifact exists for benchbox-sqlglot {candidates[0].version} "
        f"sha256:{candidates[0].sha256} in {config.producer}; rerun the producer delivery so core can verify it"
    )


def exception_message(config: Config, subject: str, detail: str) -> str:
    return (
        f"Producer exception: {subject}. {detail}. The producer guarantees one wheel hash per version; core refuses "
        f"to adopt or skip it. Resolve it in https://github.com/{config.producer} (publish a corrected newer "
        "version); core opens no issue for it."
    )


def build_plan(api: Api, config: Config, pin: Mapping[str, Any], out_dir: Path | None = None) -> dict[str, Any]:
    receipts, untrusted = collect_receipts(api, config)
    by_version: dict[str, dict[str, list[Receipt]]] = {}
    for receipt in receipts:
        by_version.setdefault(receipt.version, {}).setdefault(receipt.sha256, []).append(receipt)
    pinned = {"version": pin["version"], "sha256": pin["sha256"]}
    base = {"pin": pinned, "untrustedRuns": untrusted}
    for version, hashes in by_version.items():
        if len(hashes) > 1:
            listing = "; ".join(
                f"sha256:{digest} in run {max(items, key=lambda item: item.order).run['id']}"
                for digest, items in sorted(hashes.items())
            )
            message = exception_message(config, f"benchbox-sqlglot {version} has two wheel hashes", listing)
            return {**base, "state": "exception", "message": message}
    same = by_version.get(pin["version"])
    if same is not None and pin["sha256"] not in same:
        subject = (
            f"core pins benchbox-sqlglot {pin['version']} as sha256:{pin['sha256']} but the producer delivered "
            f"sha256:{next(iter(same))}"
        )
        detail = "The pinned wheel is not the delivered one."
        return {**base, "state": "exception", "message": exception_message(config, subject, detail)}
    skipped: list[dict[str, str]] = []
    for version in sorted(
        (v for v in by_version if version_key(v) > version_key(pin["version"])), key=version_key, reverse=True
    ):
        digest, candidates = next(iter(by_version[version].items()))
        found = locate_and_verify(api, config, candidates)
        record = found["record"]
        if (record["forkSha"], digest) in config.suppressed:
            skipped.append({"version": version, "forkSha": record["forkSha"], "sha256": digest})
            continue
        receipt = found["receipt"]
        result = {
            **base,
            "state": "adopt",
            "skippedSuppressed": skipped,
            "expected": {
                "version": version,
                "forkSha": record["forkSha"],
                "upstreamSha": record["upstreamSha"],
                "sha256": digest,
                "wheel": found["filename"],
            },
            "producer": {
                "repository": config.producer,
                "runId": receipt.run["id"],
                "runAttempt": receipt.attempt,
                "jobName": receipt.job_name,
                "artifactId": found["artifact"]["id"],
                "artifactName": f"{config.artifact_prefix}{receipt.attempt}",
                "runUrl": f"https://github.com/{config.producer}/actions/runs/{receipt.run['id']}",
            },
        }
        if out_dir is not None:
            (out_dir / found["filename"]).write_bytes(found["wheel"])
            (out_dir / RECORD_FILE).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        return result
    return {**base, "state": "current", "skippedSuppressed": skipped}


def write_output(name: str, value: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def run_plan(api: Api, config: Config, root: Path, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    pin = json.loads((root / PIN).read_text(encoding="utf-8"))
    plan = build_plan(api, config, pin, out_dir)
    (out_dir / PLAN_FILE).write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    write_output("state", plan["state"])
    write_output("version", plan.get("expected", {}).get("version", ""))
    print(json.dumps(plan, indent=2))
    if plan["state"] == "exception":
        print(f"::error::{plan['message']}", file=sys.stderr)
        return EXIT_EXCEPTION
    return 0


def run(
    args: Sequence[str], cwd: Path | None = None, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, **env} if env else None
    return subprocess.run(list(args), cwd=cwd, env=environment, capture_output=True, text=True, check=False)


def git(clone: Path, *args: str, env: Mapping[str, str] | None = None) -> str:
    result = run(["git", *args], clone, env)
    if result.returncode:
        raise DeliveryError(f"git {' '.join(args[:2])} failed: {result.stderr.strip()[-400:]}")
    return result.stdout.strip()


def authorization(token: str) -> dict[str, str]:
    if not token:
        return {}
    encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {encoded}",
    }


def branch_for(config: Config, version: str, retry: int = 0) -> str:
    return f"{config.branch_prefix}{version}" + (f"-retry{retry}" if retry else "")


def title_for(version: str) -> str:
    return f"chore(sqlglot): adopt benchbox-sqlglot {version}"


def render_marker(values: Mapping[str, Any]) -> str:
    return (
        "<!-- sqlglot-delivery: "
        + json.dumps({key: values[key] for key in MARKER_KEYS}, separators=(",", ":"))
        + " -->"
    )


def parse_marker(body: str | None) -> dict[str, Any] | None:
    match = MARKER_RE.search(body or "")
    if match is None:
        return None
    try:
        marker = json.loads(match.group(1))
    except ValueError:
        return None
    if not isinstance(marker, dict) or sorted(marker) != list(MARKER_KEYS):
        return None
    patterns = (("version", VERSION_RE), ("forkSha", SHA1_RE), ("upstreamSha", SHA1_RE), ("sha256", SHA256_RE))
    if any(not isinstance(marker[key], str) or pattern.fullmatch(marker[key]) is None for key, pattern in patterns):
        return None
    if any(isinstance(marker[key], bool) or not isinstance(marker[key], int) for key in ("runId", "runAttempt")):
        return None
    return marker


def marker_for(plan: Mapping[str, Any]) -> dict[str, Any]:
    expected, producer = plan["expected"], plan["producer"]
    return {
        "version": expected["version"],
        "forkSha": expected["forkSha"],
        "upstreamSha": expected["upstreamSha"],
        "sha256": expected["sha256"],
        "runId": producer["runId"],
        "runAttempt": producer["runAttempt"],
    }


def pull_request_text(plan: Mapping[str, Any], previous: str) -> tuple[str, str]:
    expected, producer = plan["expected"], plan["producer"]
    body = (
        f"Adopts the verified benchbox-sqlglot {expected['version']} runtime wheel (the pin is {previous}).\n\n"
        f"- Wheel SHA-256: `{expected['sha256']}`\n"
        f"- Fork commit: `{expected['forkSha']}`\n"
        f"- Upstream SQLGlot commit: `{expected['upstreamSha']}`\n"
        f"- Producer delivery: {producer['runUrl']} (attempt {producer['runAttempt']})\n\n"
        "The change touches only the runtime pin, the bundled wheel archive, its license notice and the runtime "
        "baseline identity. The scheduled SQLGlot delivery workflow opened it and keeps at most one such pull "
        "request open. It is armed for merge only through the normal arming checks and never after a human "
        "disables auto-merge or applies the `no-auto-merge` hold. To roll the adoption back, revert it and add "
        "its fork commit and wheel hash to `suppressed` in `.github/sqlglot-delivery.json`.\n\n"
        f"{render_marker(marker_for(plan))}\n"
    )
    return title_for(expected["version"]), body


def porcelain_paths(root: Path) -> list[str]:
    output = run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], root)
    if output.returncode:
        raise DeliveryError(f"git status failed: {output.stderr.strip()[-300:]}")
    paths: list[str] = []
    entries = output.stdout.split("\0")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        paths.append(entry[3:])
        if entry[0] in "RC":
            index += 1
    return sorted(paths)


def check_adoption_paths(paths: Sequence[str], version: str) -> None:
    allowed = {PIN, BASELINE, LICENSE_NOTICE}
    extra = sorted(path for path in paths if path not in allowed and not ARCHIVE_RE.fullmatch(path))
    require(not extra, f"adoption changed paths outside the allowed set: {', '.join(extra)}")
    required = {PIN, BASELINE, f"benchbox/_vendor/benchbox_sqlglot-{version}-py3-none-any.whl.zip"}
    missing = sorted(required - set(paths))
    require(not missing, f"adoption did not change {', '.join(missing)}")


def default_checks(root: Path) -> None:
    from scripts import adopt_sqlglot_wheel

    adopt_sqlglot_wheel.run_checks(root)


def prepare(
    root: Path,
    plan_dir: Path,
    out_dir: Path,
    config: Config,
    checks: Callable[[Path], None] | None = default_checks,
) -> dict[str, Any]:
    plan = json.loads((plan_dir / PLAN_FILE).read_text(encoding="utf-8"))
    require(plan.get("state") == "adopt", f"nothing to prepare: the plan state is {plan.get('state')!r}")
    expected = plan["expected"]
    wheel = plan_dir / expected["wheel"]
    require(wheel.is_file(), f"the verified wheel {expected['wheel']} is missing from {plan_dir}")
    require(hashlib.sha256(wheel.read_bytes()).hexdigest() == expected["sha256"], "wheel does not match the plan hash")
    require(not porcelain_paths(root), "the checkout has uncommitted changes; prepare needs a clean trusted checkout")
    pinned = json.loads((root / PIN).read_text(encoding="utf-8"))
    base = git(root, "rev-parse", "HEAD")
    from scripts import adopt_sqlglot_wheel

    try:
        adopt_sqlglot_wheel.adopt(
            wheel,
            expected["sha256"],
            root=root,
            checks=checks,
            version=expected["version"],
            fork_sha=expected["forkSha"],
            upstream_sha=expected["upstreamSha"],
        )
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        raise DeliveryError(f"adoption refused: {error}") from error
    paths = porcelain_paths(root)
    check_adoption_paths(paths, expected["version"])
    after = json.loads((root / PIN).read_text(encoding="utf-8"))
    require(after.get("sha256") == expected["sha256"], "the adopted pin hash differs from the expected hash")
    require(after.get("version") == expected["version"], "the adopted pin version differs from the expected version")
    branch = branch_for(config, expected["version"])
    title, body = pull_request_text(plan, pinned["version"])
    identity = {
        "GIT_AUTHOR_NAME": BOT_NAME,
        "GIT_AUTHOR_EMAIL": BOT_EMAIL,
        "GIT_COMMITTER_NAME": BOT_NAME,
        "GIT_COMMITTER_EMAIL": BOT_EMAIL,
    }
    git(root, "checkout", "-b", branch)
    for path in paths:
        git(root, "add", "--", path)
    git(root, "-c", "commit.gpgsign=false", "commit", "-m", title, env=identity)
    head = git(root, "rev-parse", "HEAD")
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle = out_dir / BUNDLE_FILE
    bundle.unlink(missing_ok=True)
    git(root, "bundle", "create", str(bundle), branch, f"^{base}")
    prepared = {"title": title, "body": body, "branch": branch, "base": base, "head": head, "paths": paths}
    (out_dir / PULL_REQUEST_FILE).write_text(json.dumps(prepared, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(plan_dir / PLAN_FILE, out_dir / PLAN_FILE)
    print(json.dumps(prepared, indent=2))
    return prepared


def commit_problem(clone: Path, tip: str, plan: Mapping[str, Any], config: Config, remote: str) -> str | None:
    expected = plan["expected"]
    try:
        parents = git(clone, "rev-list", "--parents", "-n", "1", tip).split()[1:]
        if len(parents) != 1:
            return "the adoption commit does not have exactly one parent"
        parent = parents[0]
        if run(
            ["git", "merge-base", "--is-ancestor", parent, f"refs/remotes/{remote}/{config.base}"], clone
        ).returncode:
            return f"the adoption commit is not based on {config.base}"
        if git(clone, "rev-list", "--count", f"{parent}..{tip}") != "1":
            return "the adoption branch carries more than one commit"
        identity = git(clone, "log", "-1", "--format=%ae|%ce", tip)
        if identity != f"{BOT_EMAIL}|{BOT_EMAIL}":
            return "the adoption commit was not authored by the delivery automation"
        names = git(clone, "diff", "--name-only", "--no-renames", "-z", parent, tip).split("\0")
        check_adoption_paths([name for name in names if name], expected["version"])
        pin = json.loads(git(clone, "show", f"{tip}:{PIN}"))
        for key, wanted in (
            ("version", expected["version"]),
            ("fork_sha", expected["forkSha"]),
            ("upstream_sha", expected["upstreamSha"]),
            ("sha256", expected["sha256"]),
        ):
            if pin.get(key) != wanted:
                return f"the adoption pin {key} differs from the verified delivery"
    except (DeliveryError, ValueError) as error:
        return str(error)
    return None


def remote_head(clone: Path, remote: str, branch: str, env: Mapping[str, str]) -> str | None:
    out = git(clone, "ls-remote", "--heads", remote, f"refs/heads/{branch}", env=env)
    return out.split()[0] if out else None


@dataclass(frozen=True)
class OpenAdoption:
    pull: Mapping[str, Any]
    marker: Mapping[str, Any]

    @property
    def number(self) -> int:
        return int(self.pull["number"])

    @property
    def version(self) -> str:
        return str(self.marker["version"])


def pull_problem(api: Api, config: Config, repo: str, login: str, pull: Mapping[str, Any]) -> str | None:
    marker = parse_marker(pull.get("body"))
    if marker is None:
        return "no adoption marker"
    if not same_repository((pull.get("user") or {}).get("login"), login):
        return "not authored by the delivery automation"
    if not same_repository(((pull.get("head") or {}).get("repo") or {}).get("full_name"), repo):
        return "head branch is not in this repository"
    ref = pull["head"]["ref"]
    if not (
        ref == branch_for(config, marker["version"])
        or re.fullmatch(re.escape(branch_for(config, marker["version"])) + r"-retry\d+", ref)
    ):
        return "branch does not match its marker"
    if pull.get("title") != title_for(marker["version"]):
        return "title was edited"
    commits = paged(api, f"repos/{repo}/pulls/{pull['number']}/commits")
    for commit in commits:
        details = commit["commit"]
        if (details["author"]["email"], details["committer"]["email"]) != (BOT_EMAIL, BOT_EMAIL):
            return "carries a commit that is not from the delivery automation"
    return None


def open_adoptions(
    api: Api, config: Config, repo: str, login: str
) -> tuple[list[OpenAdoption], list[Mapping[str, Any]]]:
    owned: list[OpenAdoption] = []
    foreign: list[Mapping[str, Any]] = []
    pulls = paged(api, f"repos/{repo}/pulls", params={"state": "open", "base": config.base})
    for pull in pulls:
        if not str(pull["head"]["ref"]).startswith(config.branch_prefix):
            continue
        if pull_problem(api, config, repo, login, pull) is None:
            owned.append(OpenAdoption(pull, parse_marker(pull["body"]) or {}))
        else:
            foreign.append(pull)
    return owned, foreign


def labels_of(pull: Mapping[str, Any]) -> set[str]:
    return {str(label["name"]) for label in pull.get("labels") or []}


def is_green(api: Api, repo: str, sha: str) -> bool:
    runs = paged(api, f"repos/{repo}/commits/{sha}/check-runs", "check_runs")
    return bool(runs) and all(
        item.get("status") == "completed" and item.get("conclusion") in GREEN_CONCLUSIONS for item in runs
    )


def version_history(api: Api, config: Config, repo: str, version: str) -> tuple[list[Mapping[str, Any]], int]:
    owner = repo.split("/", 1)[0]
    history: list[Mapping[str, Any]] = []
    index = 0
    while True:
        head = f"{owner}:{branch_for(config, version, index)}"
        pulls = paged(api, f"repos/{repo}/pulls", params={"state": "all", "head": head})
        if not pulls:
            return history, index
        history.extend(pulls)
        index += 1


def supersedable(api: Api, repo: str, version: str, item: OpenAdoption) -> bool:
    return (
        version_key(item.version) < version_key(version)
        and not item.pull.get("auto_merge")
        and HOLD_LABEL not in labels_of(item.pull)
        and not is_green(api, repo, item.pull["head"]["sha"])
    )


def publish(
    api: Api,
    config: Config,
    prepared: Path,
    clone: Path,
    *,
    repo: str,
    login: str,
    token: str = "",
    retry: bool = False,
    dry_run: bool = False,
    remote: str = "origin",
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if not enabled(environ):
        return {"outcome": "disabled", "actions": []}
    require(bool(login), "the automation login is empty; pass the App's bot login")
    require(same_repository(repo, config.consumer), f"publishing is restricted to {config.consumer}")
    plan = json.loads((prepared / PLAN_FILE).read_text(encoding="utf-8"))
    pull_request = json.loads((prepared / PULL_REQUEST_FILE).read_text(encoding="utf-8"))
    require(plan.get("state") == "adopt", "the prepared plan is not an adoption")
    expected = plan["expected"]
    version, digest = expected["version"], expected["sha256"]
    require(parse_marker(pull_request["body"]) == marker_for(plan), "the prepared pull request marker differs")
    require(pull_request["title"] == title_for(version), "the prepared pull request title is unexpected")
    auth = authorization(token)
    git(clone, "fetch", remote, f"+refs/heads/{config.base}:refs/remotes/{remote}/{config.base}", env=auth)
    prepared_ref = "refs/sqlglot-delivery/prepared"
    git(clone, "fetch", str(prepared / BUNDLE_FILE), f"+refs/heads/{pull_request['branch']}:{prepared_ref}")
    head = git(clone, "rev-parse", prepared_ref)
    require(head == pull_request["head"], "the prepared bundle tip differs from the prepared pull request")
    problem = commit_problem(clone, head, plan, config, remote)
    require(problem is None, f"the prepared adoption is not publishable: {problem}")

    owned, foreign = open_adoptions(api, config, repo, login)
    history, free_index = version_history(api, config, repo, version)
    result: dict[str, Any] = {"outcome": "", "actions": [], "version": version, "dryRun": dry_run}
    same = [item for item in owned if item.version == version and item.marker["sha256"] == digest]
    others = [item for item in owned if item not in same]
    occupied = [
        pull for pull in history if pull.get("state") == "open" and pull["number"] not in {item.number for item in same}
    ]
    superseded = [item for item in others if supersedable(api, repo, version, item)]
    create = False
    if any(pull.get("merged_at") for pull in history):
        result["outcome"] = "already-merged"
    elif same:
        result.update(outcome="exists", pullRequest=same[0].number)
    elif occupied:
        result["outcome"] = "occupied"
    elif history and not retry:
        result["outcome"] = "closed-unmerged"
    elif len(superseded) != len(others):
        preserved = sorted(item.number for item in others if item not in superseded)
        result.update(outcome="waiting", preserved=preserved)
    else:
        create = True
        result["outcome"] = "created"
    branch = branch_for(config, version, free_index)
    actions: list[dict[str, Any]] = []
    if create:
        existing = remote_head(clone, remote, branch, auth)
        if existing is None:
            actions.append({"do": "push", "branch": branch, "head": head})
        else:
            git(clone, "fetch", remote, f"+refs/heads/{branch}:refs/sqlglot-delivery/resume", env=auth)
            resumed = git(clone, "rev-parse", "refs/sqlglot-delivery/resume")
            problem = commit_problem(clone, resumed, plan, config, remote)
            require(
                problem is None,
                f"branch {branch} exists with no pull request and is not a clean adoption of this delivery: "
                f"{problem}; delete it or resolve it by hand",
            )
            actions.append({"do": "resume-branch", "branch": branch, "head": resumed})
        actions.append({"do": "create-pull-request", "branch": branch})
    if create or same:
        for item in superseded:
            actions.append({"do": "close-pull-request", "number": item.number, "head": item.pull["head"]["sha"]})
    result["actions"] = actions
    if dry_run:
        return result
    for action in actions:
        if action["do"] == "push":
            git(clone, "push", remote, f"{prepared_ref}:refs/heads/{action['branch']}", env=auth)
        elif action["do"] == "create-pull-request":
            created = api.request(
                "POST",
                f"repos/{repo}/pulls",
                body={
                    "title": pull_request["title"],
                    "body": pull_request["body"],
                    "head": action["branch"],
                    "base": config.base,
                },
            )
            result["pullRequest"] = created["number"]
        elif action["do"] == "close-pull-request":
            if not close_superseded(api, repo, action, version, result.get("pullRequest")):
                action["skipped"] = True
    return result


def close_superseded(api: Api, repo: str, action: Mapping[str, Any], version: str, replacement: int | None) -> bool:
    number = action["number"]
    fresh = api.request("GET", f"repos/{repo}/pulls/{number}")
    if (
        fresh.get("state") != "open"
        or fresh["head"]["sha"] != action["head"]
        or fresh.get("auto_merge")
        or HOLD_LABEL in labels_of(fresh)
    ):
        return False
    where = f"#{replacement}, which proposes" if replacement else "a newer pull request that proposes"
    api.request(
        "POST",
        f"repos/{repo}/issues/{number}/comments",
        body={
            "body": f"Superseded by {where} benchbox-sqlglot {version}. "
            "The replacement still passes every merge gate before it lands."
        },
    )
    api.request("PATCH", f"repos/{repo}/pulls/{number}", body={"state": "closed"})
    return True


def arm_refusal(api: Api, config: Config, repo: str, item: OpenAdoption) -> str | None:
    pull = item.pull
    if HOLD_LABEL in labels_of(pull):
        return f"the {HOLD_LABEL} hold is present"
    if pull.get("draft"):
        return "the pull request is a draft"
    if pull.get("auto_merge"):
        return "auto-merge is already armed"
    timeline = paged(api, f"repos/{repo}/issues/{item.number}/timeline")
    for event in timeline:
        kind = event.get("event")
        actor = (event.get("actor") or {}).get("login")
        if kind in {"labeled", "unlabeled"} and (event.get("label") or {}).get("name") == HOLD_LABEL:
            return "a hold was applied by a person; only a person lifts and re-arms it"
        if kind == "auto_merge_disabled" and actor not in config.disarm_actors:
            return f"auto-merge was disabled by {actor or 'an unknown actor'}; only the trunk-red disarm is re-armed"
    return None


def arm(
    api: Api,
    config: Config,
    clone: Path,
    *,
    repo: str,
    login: str,
    token: str = "",
    dry_run: bool = False,
    remote: str = "origin",
    runner: Runner = run,
    environ: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    if not enabled(environ):
        return [{"outcome": "disabled"}]
    require(bool(login), "the automation login is empty; pass the App's bot login")
    require(same_repository(repo, config.consumer), f"arming is restricted to {config.consumer}")
    owned, _ = open_adoptions(api, config, repo, login)
    results: list[dict[str, Any]] = []
    auth = authorization(token)
    for item in owned:
        head = item.pull["head"]["sha"]
        entry: dict[str, Any] = {"pullRequest": item.number, "head": head}
        refusal = arm_refusal(api, config, repo, item)
        if refusal is not None:
            results.append({**entry, "outcome": "not-armed", "reason": refusal})
            continue
        if dry_run:
            results.append({**entry, "outcome": "would-arm"})
            continue
        results.append({**entry, **arm_in_worktree(clone, item, config, repo, token, auth, remote, runner)})
    return results


def arm_in_worktree(
    clone: Path,
    item: OpenAdoption,
    config: Config,
    repo: str,
    token: str,
    auth: Mapping[str, str],
    remote: str,
    runner: Runner,
) -> dict[str, Any]:
    head = item.pull["head"]["sha"]
    branch = item.pull["head"]["ref"]
    tracking = f"refs/remotes/{remote}/{branch}"
    local = f"sqlglot-delivery-arm-{item.number}"
    git(clone, "fetch", remote, f"+refs/heads/{branch}:{tracking}", env=auth)
    if git(clone, "rev-parse", tracking) != head:
        return {"outcome": "not-armed", "reason": "the branch moved after it was read; the next run decides again"}
    scratch = Path(tempfile.mkdtemp(prefix="sqlglot-delivery-arm-"))
    checkout = scratch / "head"
    environment = {"GH_TOKEN": token} if token else None
    try:
        git(clone, "worktree", "add", "--track", "-b", local, str(checkout), tracking)
        gate = runner(
            [
                sys.executable,
                str(ROOT / "scripts/trunk_revert.py"),
                "arm-gate",
                "--pr",
                str(item.number),
                "--repo",
                repo,
            ],
            checkout,
            environment,
        )
        if gate.returncode:
            return {"outcome": "not-armed", "reason": "trunk is red or its state is unreadable", "detail": detail(gate)}
        armed = runner(
            [
                sys.executable,
                str(ROOT / "scripts/pr_arm.py"),
                "--pr",
                str(item.number),
                "--head",
                head,
                "--repo",
                repo,
            ],
            checkout,
            environment,
        )
        if armed.returncode == 0:
            return {"outcome": "armed"}
        return {"outcome": "not-armed", "reason": "the arming checks refused or are waiting", "detail": detail(armed)}
    finally:
        run(["git", "worktree", "remove", "--force", str(checkout)], clone)
        run(["git", "branch", "-D", local], clone)
        shutil.rmtree(scratch, ignore_errors=True)


def detail(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or "").strip()[-600:]


def main(argv: Sequence[str] | None = None, api: Api | None = None, runner: Runner = run) -> int:
    parser = argparse.ArgumentParser(description="Consume verified benchbox-sqlglot deliveries.")
    parser.add_argument("--config", type=Path, default=ROOT / CONFIG)
    commands = parser.add_subparsers(dest="action", required=True)
    plan_parser = commands.add_parser("plan")
    plan_parser.add_argument("--out-dir", type=Path, required=True)
    plan_parser.add_argument("--root", type=Path, default=ROOT)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--plan-dir", type=Path, required=True)
    prepare_parser.add_argument("--out-dir", type=Path, required=True)
    prepare_parser.add_argument("--root", type=Path, default=ROOT)
    for name in ("publish", "arm"):
        sub = commands.add_parser(name)
        sub.add_argument("--clone", type=Path, default=ROOT)
        sub.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
        sub.add_argument("--automation-login", required=True)
        sub.add_argument("--dry-run", action="store_true")
        if name == "publish":
            sub.add_argument("--prepared", type=Path, required=True)
            sub.add_argument("--retry", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        client = api or GhApi()
        if args.action == "plan":
            return run_plan(client, config, args.root, args.out_dir)
        if args.action == "prepare":
            prepare(args.root, args.plan_dir, args.out_dir, config)
            return 0
        token = os.environ.get("GH_TOKEN", "")
        if args.action == "publish":
            outcome: Any = publish(
                client,
                config,
                args.prepared,
                args.clone,
                repo=args.repo,
                login=args.automation_login,
                token=token,
                retry=args.retry,
                dry_run=args.dry_run,
            )
        else:
            outcome = arm(
                client,
                config,
                args.clone,
                repo=args.repo,
                login=args.automation_login,
                token=token,
                dry_run=args.dry_run,
                runner=runner,
            )
        print(json.dumps(outcome, indent=2))
        return 0
    except DeliveryError as error:
        print(f"::error::SQLGlot delivery: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
