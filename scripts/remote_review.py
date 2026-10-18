#!/usr/bin/env python3

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import string
import subprocess
import sys
import tempfile
import tomllib
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pr_landing
import remote_closeout as closeout

CLI_DESCRIPTION = (
    "Run the independent Codex reviewer on a bound cloud-built pull request, then publish and store its record."
)

PROMPT = Path(__file__).resolve().parent / "remote_review_prompt.txt"
CODEX_TIMEOUT = 3600
DRAIN_TIMEOUT = 5
LOCK_WAIT = 60.0
LOCK_ATTEMPTS = 3
DENIED_ROOTS = ("/tmp", "/var/tmp")
RUNS_ENV = "BENCHBOX_REVIEW_RUNS_DIR"
VIEW_FIELDS = f"{closeout.VIEW_FIELDS},title,body"
SEVERITIES = ("Critical", "Required", "Nit")
BLOCKING = frozenset({"Critical", "Required"})
MARKS = ("fixed", "open")
RULE_PATHS = ("AGENTS.md", "docs/agent")
LOCATION_RE = re.compile(r"(?P<path>.+?)(?::(?P<line>[1-9][0-9]*))?")
HUNK_RE = re.compile(r"@@ -[0-9]+(?:,(?P<old>[0-9]+))? \+(?P<start>[0-9]+)(?:,(?P<new>[0-9]+))? @@")
MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")
DIFF = [
    "git",
    "diff",
    "--no-ext-diff",
    "--no-textconv",
    "--no-color",
    "--unified=3",
    "--src-prefix=a/",
    "--dst-prefix=b/",
    "--no-renames",
]
PERMISSION_PROFILE = "benchbox_review"
DISABLED_FEATURES = (
    "apps",
    "plugins",
    "remote_plugin",
    "hooks",
    "memories",
    "computer_use",
    "browser_use",
    "browser_use_external",
    "image_generation",
    "multi_agent",
    "daemon_auto_start",
)
MARKER = "<!-- benchbox-remote-review {} -->"
DEAD_THREAD_ERRORS = ("Could not resolve to a node", "NOT_FOUND")
THREAD_FIELDS = "id isResolved isOutdated comments(first:1) { nodes { body path pullRequestReview { databaseId } } }"
RESOLVE_THREAD = """
  mutation($thread:ID!) {
    resolveReviewThread(input:{threadId:$thread}) { thread { id isResolved } }
  }
"""

Runner = closeout.Runner
CodexRunner = Callable[[list[str], Mapping[str, str]], tuple[int, str]]
Refused = closeout.Refused
LandingError = closeout.LandingError


class DeadStep(LandingError):
    pass


def _object_schema(properties: dict[str, dict]) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


OUTPUT_SCHEMA = _object_schema(
    {
        "findings": {
            "type": "array",
            "items": _object_schema(
                {
                    "location": {"type": "string"},
                    "reasoning": {"type": "string"},
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                }
            ),
        },
        "previous_findings": {
            "type": "array",
            "items": _object_schema({"id": {"type": "string"}, "status": {"type": "string", "enum": list(MARKS)}}),
        },
    }
)


def _kill_group(pid: int) -> None:
    if hasattr(signal, "SIGKILL"):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)


def _warn(message: str) -> None:
    print(f"pr-landing-remote-review: warning: {message}", file=sys.stderr)


def _drain(proc: subprocess.Popen) -> None:
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.communicate(timeout=DRAIN_TIMEOUT)
    if proc.stdout is not None:
        proc.stdout.close()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=DRAIN_TIMEOUT)


def codex_run(cmd: list[str], env: Mapping[str, str]) -> tuple[int, str]:
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=dict(env),
        start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=CODEX_TIMEOUT)
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid)
        _drain(proc)
        raise Refused(f"codex did not finish within {CODEX_TIMEOUT} seconds; its process group was killed") from None
    except BaseException:
        _kill_group(proc.pid)
        _drain(proc)
        raise
    _kill_group(proc.pid)
    return proc.returncode, out.decode("utf-8", "surrogateescape")


def codex_home() -> Path:
    home = os.environ.get("CODEX_HOME")
    return Path(home).expanduser() if home else Path.home() / ".codex"


def _flag(name: str, value: str | None) -> bool:
    if value not in (None, "", "1"):
        raise Refused(f"{name} must be 1 or unset, got {value!r}")
    return value == "1"


def _denied(path: Path) -> bool:
    resolved = path.resolve()
    return any(resolved.is_relative_to(Path(root).resolve()) for root in DENIED_ROOTS)


def runs_root() -> Path:
    override = os.environ.get(RUNS_ENV)
    if override:
        root = Path(override).expanduser()
    else:
        temporary = Path(tempfile.gettempdir()).resolve()
        if not _denied(temporary):
            return temporary
        root = Path.home() / ".benchbox" / "review-runs"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    if _denied(root):
        raise Refused(f"the review directory {root} is under {' or '.join(DENIED_ROOTS)}, which the sandbox denies")
    return root.resolve()


def _codex_model(model: str | None) -> str:
    if not model:
        path = codex_home() / "config.toml"
        try:
            model = tomllib.loads(path.read_text(encoding="utf-8")).get("model")
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise Refused(f"MODEL is required: no default Codex model is readable from {path}: {exc}") from exc
        if model is None:
            raise Refused(f"MODEL is required: {path} sets no default Codex model")
    if not isinstance(model, str) or not MODEL_RE.fullmatch(model):
        raise Refused(f"MODEL must name a Codex model, got {model!r}")
    return model


def _review_view(run: Runner, repo: str, number: int, binding: dict, head: str) -> dict:
    view = closeout._view(run, repo, number, VIEW_FIELDS)
    reasons = closeout._identity_refusals(binding, view, number)
    if view.get("headRefOid") != head:
        reasons.append(
            f"the live PR head {closeout._short(view.get('headRefOid'))} is not HEAD {closeout._short(head)}"
        )
    if reasons:
        raise Refused("\n".join(reasons))
    return view


def _blob_id(data: bytes, length: int) -> str:
    digest = hashlib.sha1 if length == 40 else hashlib.sha256
    return digest(b"blob %d\0" % len(data) + data).hexdigest()


def _tree_entries(run: Runner, revision: str, paths: Sequence[str]) -> list[tuple[str, str, str, str]]:
    code, out = run(["git", "ls-tree", "-r", "-z", "--full-tree", revision, "--", *paths])
    if code != 0:
        raise LandingError(f"git ls-tree {closeout._short(revision)} failed: {out.strip()[:200]}")
    entries: list[tuple[str, str, str, str]] = []
    for record in filter(None, out.split("\0")):
        meta, _, path = record.partition("\t")
        mode, kind, oid = meta.split(" ")
        entries.append((mode, kind, oid, path))
    return entries


def _blobs(checkout: Path, oids: Sequence[str]) -> Iterator[bytes]:
    with tempfile.TemporaryFile() as request:
        request.write("".join(f"{oid}\n" for oid in oids).encode("ascii"))
        request.seek(0)
        proc = subprocess.Popen(
            ["git", "cat-file", "--batch"],
            cwd=checkout,
            stdin=request,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert proc.stdout is not None
        try:
            for oid in oids:
                header = proc.stdout.readline().decode("ascii", "replace").split()
                if len(header) != 3 or header[:2] != [oid, "blob"]:
                    raise LandingError(f"git cat-file did not return blob {oid}")
                size = int(header[2])
                data = proc.stdout.read(size)
                proc.stdout.read(1)
                if len(data) != size:
                    raise LandingError(f"git cat-file returned a truncated blob {oid}")
                yield data
        finally:
            proc.kill()
            proc.wait()


def _inside(root: Path, path: str) -> Path:
    parts = PurePosixPath(path).parts
    if not parts or PurePosixPath(path).is_absolute() or any(part in ("", ".", "..") for part in parts):
        raise LandingError(f"the tree has an unsafe path {path!r}")
    return root.joinpath(*parts)


def _escapes(path: str, link: str) -> bool:
    resolved = os.path.normpath(os.path.join(os.path.dirname(path), link))
    return os.path.isabs(link) or resolved == ".." or resolved.startswith("../")


def export_tree(run: Runner, checkout: Path, revision: str, root: Path, paths: Sequence[str] = ()) -> list[str]:
    entries = _tree_entries(run, revision, paths)
    blobs = [(mode, oid, path) for mode, kind, oid, path in entries if kind == "blob"]
    skipped = [f"{path}: submodule at {oid}" for _, kind, oid, path in entries if kind == "commit"]
    files: dict[str, str] = {}
    links: list[tuple[str, str]] = []
    root.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(_blobs(checkout, [oid for _, oid, _ in blobs])) as contents:
        for (mode, oid, path), data in zip(blobs, contents, strict=True):
            target = _inside(root, path)
            if mode == "120000":
                links.append((path, os.fsdecode(data)))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o755 if mode == "100755" else 0o644)
            files[path] = oid
    created: set[str] = set()
    for path, link in links:
        if _escapes(path, link):
            skipped.append(f"{path}: symlink to {link}, which points outside the tree")
            continue
        target = _inside(root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(link)
        created.add(path)
    _verify_export(root, revision, files, created)
    return skipped


def _verify_export(root: Path, revision: str, files: Mapping[str, str], links: set[str]) -> None:
    found_files: dict[str, str] = {}
    found_links: set[str] = set()
    for directory, subdirectories, names in os.walk(root):
        for name in [*subdirectories, *names]:
            path = Path(directory, name)
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                found_links.add(relative)
            elif name in names:
                found_files[relative] = _blob_id(path.read_bytes(), len(files.get(relative, "")))
    if found_files != files or found_links != links:
        raise LandingError(f"the exported tree of {closeout._short(revision)} does not match git ls-tree")


def _json_text(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _prepare_input(
    run: Runner,
    checkout: Path,
    workdir: Path,
    view: dict,
    pinned: tuple[str, str, str],
    item: dict | None,
    previous: Sequence[dict],
) -> str:
    base, head, listing = pinned
    inputs = workdir / "input"
    skipped = export_tree(run, checkout, head, workdir / "head")
    export_tree(run, checkout, base, inputs / "rules", RULE_PATHS)
    code, diff = run([*DIFF, base, head])
    if code != 0:
        raise LandingError(f"git diff {closeout._short(base)} {closeout._short(head)} failed: {diff.strip()[:200]}")
    pull_request = {
        "number": view["number"],
        "title": str(view.get("title") or ""),
        "body": str(view.get("body") or ""),
        "base_sha": base,
        "head_sha": head,
    }
    texts = {
        "diff.patch": diff,
        "manifest.txt": listing,
        "pull_request.json": _json_text(pull_request),
        "previous_findings.json": _json_text(
            [{field: entry[field] for field in ("id", "head_sha", *closeout.FINDING_FIELDS)} for entry in previous]
        ),
        "skipped_entries.txt": "".join(f"{line}\n" for line in skipped),
    }
    if item is not None:
        texts["tracker_item.json"] = _json_text(item)
    for name, text in texts.items():
        (inputs / name).write_text(text, encoding="utf-8", errors="surrogateescape")
    return diff


def review_prompt(workdir: Path, number: int, base: str, head: str) -> str:
    template = string.Template(PROMPT.read_text(encoding="utf-8"))
    return template.substitute(
        pr_number=number, tree=workdir / "head", inputs=workdir / "input", base_sha=base, head_sha=head
    )


def _isolated_env(workdir: Path) -> tuple[dict[str, str], bytes]:
    home = workdir / "home"
    isolated = home / ".codex"
    isolated.mkdir(parents=True)
    source = codex_home() / "auth.json"
    try:
        login = source.read_bytes()
    except OSError as exc:
        raise Refused(f"codex is not logged in: cannot read {source}: {exc}") from exc
    auth = isolated / "auth.json"
    auth.touch(mode=0o600)
    auth.write_bytes(login)
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(home),
        "CODEX_HOME": str(isolated),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    return env, login


def _keep_refreshed_login(env: Mapping[str, str], login: bytes) -> None:
    source = codex_home() / "auth.json"
    staged = source.with_name(f".auth.json.{os.getpid()}.tmp")
    created = False
    try:
        refreshed = Path(env["CODEX_HOME"], "auth.json").read_bytes()
        if refreshed == login:
            return
        json.loads(refreshed)
        if source.is_symlink():
            _warn(f"{source} is a symlink, so the login Codex refreshed during the review was not copied back")
            return
        if source.read_bytes() != login:
            _warn(f"{source} changed during the review, so the login Codex refreshed was not copied back")
            return
        staged.touch(mode=0o600, exist_ok=False)
        created = True
        staged.write_bytes(refreshed)
        os.replace(staged, source)
    except (OSError, ValueError) as exc:
        _warn(f"the login Codex refreshed during the review was not copied back to {source}: {exc}")
    finally:
        if created:
            with contextlib.suppress(OSError):
                staged.unlink(missing_ok=True)


def codex_filesystem(workdir: Path) -> dict[str, str]:
    return {
        ":minimal": "read",
        str(workdir / "input"): "read",
        str(workdir / "head"): "read",
        **dict.fromkeys(DENIED_ROOTS, "none"),
    }


def codex_command(workdir: Path, model: str, prompt: str) -> list[str]:
    readable = codex_filesystem(workdir)
    filesystem = "{" + ",".join(f"{json.dumps(path)}={json.dumps(access)}" for path, access in readable.items()) + "}"
    return [
        "codex",
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--skip-git-repo-check",
        "--json",
        "-C",
        str(workdir / "input"),
        "-c",
        f'default_permissions="{PERMISSION_PROFILE}"',
        "-c",
        f"permissions.{PERMISSION_PROFILE}.filesystem={filesystem}",
        "-c",
        f"permissions.{PERMISSION_PROFILE}.network={{enabled=false}}",
        "-c",
        'web_search="disabled"',
        "-c",
        "mcp_servers={}",
        *(argument for feature in DISABLED_FEATURES for argument in ("--disable", feature)),
        "--output-schema",
        str(workdir / "run" / "schema.json"),
        "-o",
        str(workdir / "run" / "output.json"),
        "-m",
        model,
        prompt,
    ]


def _codex_events(out: str) -> list[dict]:
    events: list[dict] = []
    for line in out.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _codex_failure(events: Sequence[dict], out: str) -> str:
    messages: list[str] = []
    for event in events:
        error = event.get("error")
        if isinstance(error, dict) and closeout._text(error.get("message")):
            messages.append(str(error["message"]))
        elif event.get("type") == "error" and closeout._text(event.get("message")):
            messages.append(str(event["message"]))
    return messages[-1] if messages else out.strip()[-500:]


def _run_reviewer(codex: CodexRunner, workdir: Path, model: str, prompt: str) -> tuple[str, str, str]:
    (workdir / "run").mkdir()
    (workdir / "run" / "schema.json").write_text(_json_text(OUTPUT_SCHEMA), encoding="utf-8")
    env, login = _isolated_env(workdir)
    try:
        code, version = codex(["codex", "--version"], env)
        version = version.strip()
        if code != 0 or not version:
            raise LandingError(f"codex --version failed: {version[:200]}")
        code, out = codex(codex_command(workdir, model, prompt), env)
    finally:
        _keep_refreshed_login(env, login)
    events = _codex_events(out)
    if code != 0:
        raise Refused(f"codex exec failed with exit code {code}: {_codex_failure(events, out)}")
    try:
        text = (workdir / "run" / "output.json").read_text(encoding="utf-8")
    except OSError as exc:
        raise Refused(f"codex exec wrote no final message: {exc}") from exc
    threads = [
        str(event["thread_id"])
        for event in events
        if event.get("type") == "thread.started" and closeout._text(event.get("thread_id"))
    ]
    return text, threads[0] if threads else f"{version} {uuid.uuid4()}", version


def reviewer_output(text: str, previous_ids: Sequence[str]) -> tuple[list[dict], dict[str, str]]:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise Refused(f"the reviewer's final message is not JSON: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {"findings", "previous_findings"}:
        raise Refused("the reviewer's final message is not the object the output schema describes")
    findings, marks = data["findings"], data["previous_findings"]
    if not isinstance(findings, list) or not all(
        closeout._text_object(finding, closeout.FINDING_FIELDS) and finding["severity"] in SEVERITIES
        for finding in findings
    ):
        raise Refused(
            "the reviewer returned a finding without a location, reasoning, and Critical/Required/Nit severity"
        )
    if not isinstance(marks, list) or not all(
        closeout._text_object(mark, ("id", "status")) and mark["status"] in MARKS for mark in marks
    ):
        raise Refused("the reviewer returned a previous finding without an id and a fixed/open status")
    marked = sorted(mark["id"] for mark in marks)
    if marked != sorted(previous_ids):
        raise Refused(f"the reviewer marked previous findings {marked}, but the open ones are {sorted(previous_ids)}")
    return findings, {mark["id"]: mark["status"] for mark in marks}


def changed_lines(diff: str) -> dict[str, set[int]]:
    lines: dict[str, set[int]] = {}
    path: str | None = None
    old = new = 0
    for line in diff.split("\n"):
        if old > 0 or new > 0:
            tag = line[:1]
            old -= tag in (" ", "-")
            new -= tag in (" ", "+")
            continue
        if line.startswith("diff --git "):
            path = None
        elif line.startswith("+++ "):
            target = line.removeprefix("+++ ")
            path = target.removeprefix("b/") if target.startswith("b/") else None
        elif path is not None and (match := HUNK_RE.match(line)):
            start = int(match["start"])
            old = int(match["old"] or 1)
            new = int(match["new"] or 1)
            lines.setdefault(path, set()).update(range(start, start + new))
    return lines


def _placement(location: str, lines: Mapping[str, set[int]]) -> tuple[str, int] | None:
    match = LOCATION_RE.fullmatch(location.strip())
    if match is None or match["line"] is None:
        return None
    line = int(match["line"])
    return (match["path"], line) if line in lines.get(match["path"], set()) else None


def _listed(finding: Mapping[str, str]) -> str:
    return f"- {finding['severity']}, `{finding['location']}`: {finding['reasoning']}"


def _comment_body(finding: Mapping[str, str]) -> str:
    return f"Independent reviewer, {finding['severity']}: {finding['reasoning']}"


def review_payload(
    head: str, findings: Sequence[dict], lines: Mapping[str, set[int]], marker: str
) -> tuple[dict, list[int]]:
    inline: list[tuple[int, str, int]] = []
    listed: list[str] = []
    nits: list[str] = []
    for index, finding in enumerate(findings):
        place = _placement(finding["location"], lines) if finding["severity"] in BLOCKING else None
        if place is not None:
            inline.append((index, *place))
        elif finding["severity"] in BLOCKING:
            listed.append(_listed(finding))
        else:
            nits.append(_listed(finding))
    body = [marker, f"Findings from the independent reviewer at {head}."]
    if listed:
        body.extend(["", "Critical and Required findings outside the changed lines:", *listed])
    if nits:
        body.extend(["", "Nits:", *nits])
    comments = [
        {"path": path, "line": line, "side": "RIGHT", "body": _comment_body(findings[index])}
        for index, path, line in inline
    ]
    payload = {"commit_id": head, "event": "COMMENT", "body": "\n".join(body), "comments": comments}
    return payload, [index for index, _, _ in inline]


def _post(run: Runner, repo: str, number: int, payload: dict) -> tuple[int, str]:
    with tempfile.TemporaryDirectory(prefix="benchbox-remote-review-post-") as directory:
        request = Path(directory, "review.json")
        request.write_text(json.dumps(payload), encoding="utf-8")
        return run(["gh", "api", "--method", "POST", f"repos/{repo}/pulls/{number}/reviews", "--input", str(request)])


def _existing_review(run: Runner, repo: str, number: int, payload: dict) -> int | None:
    marker = payload["body"].split("\n", 1)[0]
    query = (
        f'.[] | select(.commit_id == "{payload["commit_id"]}") | select(.body | contains({json.dumps(marker)})) | .id'
    )
    code, out = run(["gh", "api", "--paginate", f"repos/{repo}/pulls/{number}/reviews", "--jq", query])
    if code != 0:
        raise LandingError(f"cannot list the reviews of PR #{number}: {out.strip()[:200]}")
    ids = [int(line) for line in out.split() if line.isdigit()]
    return ids[0] if ids else None


def _without_inline(payload: dict, findings: Sequence[Mapping[str, str]]) -> dict:
    section = ["", "Critical and Required findings GitHub would not place on the diff:", *map(_listed, findings)]
    return {**payload, "body": "\n".join([payload["body"], *section]), "comments": []}


def _post_review(run: Runner, repo: str, number: int, ledger: Mapping[str, dict], step: dict) -> int:
    code, out = _post(run, repo, number, step["payload"])
    if code != 0 and "HTTP 422" in out and step["payload"]["comments"]:
        step["payload"] = _without_inline(step["payload"], [ledger[finding] for finding in step["findings"]])
        step["findings"] = []
        code, out = _post(run, repo, number, step["payload"])
    if code != 0 and "HTTP 422" in out:
        raise DeadStep(f"GitHub refuses the review for PR #{number} even without inline comments: {out.strip()[:300]}")
    if code != 0:
        raise LandingError(f"posting the review to PR #{number} failed: {out.strip()[:300]}")
    posted = json.loads(out)
    if not isinstance(posted, dict) or not isinstance(posted.get("id"), int):
        raise LandingError(f"GitHub returned no review ID for PR #{number}")
    return posted["id"]


def _normalized(text: object) -> str:
    return str(text or "").replace("\r\n", "\n").strip()


def _thread_ids(run: Runner, repo: str, number: int, review_id: int, comments: Sequence[dict]) -> list[str | None]:
    posted: list[tuple[str, str, str]] = []
    for thread in pr_landing.review_threads(run, repo, number, THREAD_FIELDS):
        nodes = (thread.get("comments") or {}).get("nodes") if isinstance(thread, dict) else None
        first = nodes[0] if isinstance(nodes, list) and nodes and isinstance(nodes[0], dict) else {}
        if (first.get("pullRequestReview") or {}).get("databaseId") == review_id:
            posted.append((str(first.get("path")), _normalized(first.get("body")), str(thread["id"])))
    ids: list[str | None] = []
    for comment in comments:
        match = next((entry for entry in posted if entry[:2] == (comment["path"], _normalized(comment["body"]))), None)
        if match is not None:
            posted.remove(match)
        ids.append(match[2] if match else None)
    return ids


def _resolve_thread(run: Runner, thread_id: str) -> None:
    code, out = run(["gh", "api", "graphql", "-f", f"query={RESOLVE_THREAD}", "-f", f"thread={thread_id}"])
    try:
        resolved = code == 0 and json.loads(out)["data"]["resolveReviewThread"]["thread"]["isResolved"] is True
    except (KeyError, TypeError, ValueError):
        resolved = False
    if not resolved and any(error in out for error in DEAD_THREAD_ERRORS):
        raise DeadStep(f"review thread {thread_id} no longer exists: {out.strip()[:200]}")
    if not resolved:
        raise LandingError(f"resolving review thread {thread_id} failed: {out.strip()[:200]}")


def _publish_review(run: Runner, repo: str, number: int, ledger: Mapping[str, dict], step: dict) -> None:
    if step["review_id"] is None:
        existing = _existing_review(run, repo, number, step["payload"])
        step["review_id"] = existing if existing is not None else _post_review(run, repo, number, ledger, step)
    if step["findings"]:
        threads = _thread_ids(run, repo, number, step["review_id"], step["payload"]["comments"])
        for finding, thread in zip(step["findings"], threads, strict=True):
            ledger[finding]["thread_id"] = thread


def publish_pending(run: Runner, repo: str, number: int, path: Path, state: dict) -> int:
    ledger = {entry["id"]: entry for entry in state.get("review_findings", [])}
    finished = 0
    for review_run in state.get("review_runs", []):
        record = state["records"][review_run["record_index"]]
        if record.get("published", True):
            continue
        for step in review_run["steps"]:
            if step["status"] != "pending":
                continue
            try:
                if step["kind"] == "review":
                    _publish_review(run, repo, number, ledger, step)
                else:
                    _resolve_thread(run, step["thread_id"])
                step["status"] = "done"
            except DeadStep as exc:
                step.update(status="abandoned", reason=str(exc))
                _warn(f"abandoned a publication step of record {review_run['record_index']}: {exc}")
            finished += 1
            pr_landing._write_json_atomic(path, state)
        if any(step["status"] == "abandoned" for step in review_run["steps"]):
            continue
        if review_run["record_index"] == len(state["records"]) - 1:
            view = closeout._view(run, repo, number)
            record["observed_head"] = view.get("headRefOid")
            record["observation"] = closeout.observe(run, repo, view)
        record["published"] = True
        pr_landing._write_json_atomic(path, state)
    return finished


def _require_published(state: dict, number: int) -> None:
    index = len(state["records"]) - 1
    if index < 0 or state["records"][index].get("published", True):
        return
    reasons = [
        str(step.get("reason"))
        for review_run in state.get("review_runs", [])
        if review_run["record_index"] == index
        for step in review_run["steps"]
        if step["status"] == "abandoned"
    ]
    raise Refused(
        f"the latest record for PR #{number} is stored but unpublished, so make pr-arm-remote refuses it: "
        f"{'; '.join(reasons)}; review the PR again for a new record"
    )


def _open_ids(state: Mapping[str, object]) -> list[str]:
    ledger = state.get("review_findings") or []
    return [entry["id"] for entry in ledger if isinstance(entry, dict) and entry["status"] == "open"]


def _store(
    run: Runner,
    repo: str,
    number: int,
    snapshot: dict,
    review: dict,
    builder_head: str,
) -> tuple[Path, int]:
    record, findings, marks, diff = review["record"], review["findings"], review["marks"], review["diff"]
    head = record["head_sha"]
    with closeout._locked_store(repo, number, wait=LOCK_WAIT, attempts=LOCK_ATTEMPTS) as (path, state):
        if state.get("binding") != snapshot["binding"]:
            raise Refused(f"the binding of PR #{number} changed while the reviewer ran")
        if len(state.get("arms") or []) > len(snapshot.get("arms") or []):
            raise Refused(
                f"PR #{number} was armed while the reviewer ran; withdraw it with make pr-landing-remote-withdraw, "
                "then run the review again"
            )
        _review_view(run, repo, number, state["binding"], head)
        publish_pending(run, repo, number, path, state)
        if _open_ids(state) != _open_ids(snapshot):
            raise Refused(f"the open findings of PR #{number} changed while the reviewer ran; run the review again")
        ledger = state.setdefault("review_findings", [])
        fixed = [entry for entry in ledger if entry["status"] == "open" and marks[entry["id"]] == "fixed"]
        for entry in fixed:
            entry.update(status="fixed", fixed_at_head=head)
        closeout._store_record(run, repo, number, state, record, builder_head)
        record_index = len(state["records"]) - 1
        created: dict[int, str] = {}
        for index, finding in enumerate(findings):
            if finding["severity"] in BLOCKING:
                created[index] = f"F{len(ledger) + 1}"
                ledger.append(
                    {
                        "id": created[index],
                        "record_index": record_index,
                        "head_sha": head,
                        **{field: finding[field] for field in closeout.FINDING_FIELDS},
                        "thread_id": None,
                        "status": "open",
                        "fixed_at_head": None,
                    }
                )
        marker = MARKER.format(uuid.uuid4().hex)
        payload, inline = review_payload(head, findings, changed_lines(diff), marker)
        steps: list[dict] = []
        if findings:
            steps.append(
                {
                    "kind": "review",
                    "status": "pending",
                    "payload": payload,
                    "findings": [created[index] for index in inline],
                    "review_id": None,
                }
            )
        steps.extend(
            {"kind": "resolve", "finding": entry["id"], "thread_id": entry["thread_id"], "status": "pending"}
            for entry in fixed
            if entry["thread_id"]
        )
        state["records"][record_index]["published"] = not steps
        state.setdefault("review_runs", []).append(
            {
                "record_index": record_index,
                "head_sha": head,
                "codex_version": review["codex_version"],
                "prompt_sha256": hashlib.sha256(PROMPT.read_bytes()).hexdigest(),
                "steps": steps,
            }
        )
        pr_landing._write_json_atomic(path, state)
        publish_pending(run, repo, number, path, state)
        _require_published(state, number)
    return path, len(steps)


def _resume(pr: str | None, flag: str, repo: str | None, run: Runner) -> int:
    _flag("RESUME", flag)
    repo = closeout._repository(repo)
    number = closeout._pr_number(pr)
    with closeout._locked_store(repo, number, wait=LOCK_WAIT, attempts=LOCK_ATTEMPTS) as (path, state):
        binding = state.get("binding")
        if binding is None:
            raise Refused(f"no binding is recorded for PR #{number}; there is nothing to publish")
        identity = closeout._identity_refusals(binding, closeout._view(run, repo, number), number)
        if identity:
            raise Refused("\n".join(identity))
        finished = publish_pending(run, repo, number, path, state)
        _require_published(state, number)
    print(f"finished {finished} pending publication step(s) for PR #{number} in {path}")
    return 0


def _review(
    pr: str | None,
    head: str | None,
    builder_head: str | None,
    model: str | None,
    dry_run: str | None,
    repo: str | None,
    run: Runner,
    codex: CodexRunner,
    checkout: Path,
) -> int:
    repo = closeout._repository(repo)
    number = closeout._pr_number(pr)
    head = closeout._revision(head, "HEAD")
    dry = _flag("DRY_RUN", dry_run)
    builder_head = builder_head if dry else closeout._revision(builder_head, "BUILDER_HEAD")
    model = _codex_model(model)
    snapshot = closeout._load_store(closeout.store_path(repo, number), repo, number)
    binding = snapshot.get("binding")
    if binding is None:
        raise Refused(f"no binding is recorded for PR #{number}; bind it before reviewing")
    previous = [entry for entry in snapshot.get("review_findings", []) if entry["status"] == "open"]
    view = _review_view(run, repo, number, binding, head)
    fetched = closeout.fetch_pr_head(run, repo, number)
    if fetched != head:
        raise Refused(
            f"GitHub reports head {closeout._short(fetched)} for PR #{number}, not HEAD {closeout._short(head)}"
        )
    base, listing = closeout.pin_manifest(run, head, closeout.fetch_develop(run))
    item = closeout.tracker_item(run, binding["item"]) if binding.get("item") else None
    workdir = Path(tempfile.mkdtemp(prefix="benchbox-remote-review-", dir=runs_root())).resolve()
    try:
        diff = _prepare_input(run, checkout, workdir, view, (base, head, listing), item, previous)
        text, run_id, version = _run_reviewer(codex, workdir, model, review_prompt(workdir, number, base, head))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    findings, marks = reviewer_output(text, [entry["id"] for entry in previous])
    still_open = [entry for entry in previous if marks[entry["id"]] == "open"]
    blocking = [finding for finding in findings if finding["severity"] in BLOCKING]
    record = {
        "pr_number": number,
        "head_branch": binding["head_branch"],
        "pr_node_id": binding["node_id"],
        "base_sha": base,
        "head_sha": head,
        "manifest_digest": closeout.manifest_digest(listing),
        "findings": [*findings, *({field: entry[field] for field in closeout.FINDING_FIELDS} for entry in still_open)],
        "disposition": "changes-requested" if blocking or still_open else "approve",
        "reviewer": {"model": model, "run_id": run_id},
    }
    problems = closeout.record_problems(record)
    if problems:
        raise LandingError("the built record is invalid: " + "; ".join(problems))
    if dry:
        print(json.dumps(record, indent=2))
        return 0
    review = {"record": record, "findings": findings, "marks": marks, "diff": diff, "codex_version": version}
    path, steps = _store(run, repo, number, snapshot, review, str(builder_head))
    print(
        f"stored a {record['disposition']} record for PR #{number} at {closeout._short(head)} in {path} "
        f"and completed {steps} publication step(s)"
    )
    return 0


def review(
    pr: str | None,
    head: str | None,
    builder_head: str | None = None,
    model: str | None = None,
    dry_run: str | None = None,
    repo: str = closeout.REPOSITORY,
    run: Runner = closeout.live_run,
    codex: CodexRunner = codex_run,
    checkout: Path = Path("."),
) -> int:
    return closeout._guarded(
        "pr-landing-remote-review",
        lambda: _review(pr, head, builder_head, model, dry_run, repo, run, codex, checkout),
    )


def resume(pr: str | None, repo: str = closeout.REPOSITORY, run: Runner = closeout.live_run, flag: str = "1") -> int:
    return closeout._guarded("pr-landing-remote-review", lambda: _resume(pr, flag, repo, run))


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    environment = os.environ if env is None else env

    def selected(name: str) -> str | None:
        return environment.get(f"{closeout.ENV_PREFIX}{name}") or None

    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--repo", default=selected("REPO") or closeout.REPOSITORY)
    for name in ("PR", "HEAD", "BUILDER_HEAD", "MODEL", "DRY_RUN", "RESUME"):
        parser.add_argument(f"--{name.lower().replace('_', '-')}", default=selected(name))
    args = parser.parse_args(argv)
    if args.resume is not None:
        return resume(args.pr, args.repo, flag=args.resume)
    return review(args.pr, args.head, args.builder_head, args.model, args.dry_run, args.repo)


if __name__ == "__main__":
    raise SystemExit(main())
