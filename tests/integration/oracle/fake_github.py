from __future__ import annotations

import io
import itertools
import json
import re
import subprocess
import threading
import zipfile
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

JOB_TOKEN = "ghs_jobtoken" + "J" * 28
APP_TOKEN = "ghs_apptoken" + "A" * 28
OWNER_TOKEN = "gho_ownertoken" + "O" * 26
ACCOUNTS = {
    JOB_TOKEN: {"login": "github-actions[bot]", "type": "Bot"},
    APP_TOKEN: {"login": "benchbox-oracle[bot]", "type": "Bot"},
    OWNER_TOKEN: {"login": "joeharris76", "type": "User"},
}
GH_HOST = "github.localhost"
API_HOST = f"api.{GH_HOST}"
COMMENT_PAGE = 50
COMMENT_PAGE_AFTER = 100
WORKFLOWS = {"oracle-verdict.yml": 7001, "oracle-gate.yml": 7002, "ci.yml": 7003}


def now_text() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


@dataclass
class Failure:
    method: str
    pattern: re.Pattern[str]
    status: int
    remaining: int


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    query: dict[str, str]
    body: bytes
    token: str
    host: str
    accept: str

    def json(self) -> Any:
        return json.loads(self.body or b"{}")


Response = tuple[int, Any, dict[str, str]]
Route = Callable[[Request, re.Match[str]], Response]


class FakeGitHub:
    def __init__(self, slug: str, git_dir: Path) -> None:
        self.slug = slug
        self.git_dir = git_dir
        self.lock = threading.RLock()
        self.ids = itertools.count(1000)
        self.pulls: dict[int, dict[str, Any]] = {}
        self.reported_changed_files: dict[int, int] = {}
        self.reviews: dict[int, list[dict[str, Any]]] = defaultdict(list)
        self.statuses: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.comments: dict[int, list[dict[str, Any]]] = defaultdict(list)
        self.threads: dict[int, list[dict[str, Any]]] = defaultdict(list)
        self.timeline: dict[int, list[dict[str, Any]]] = defaultdict(list)
        self.runs: list[dict[str, Any]] = []
        self.jobs: dict[int, dict[str, Any]] = {}
        self.artifacts: list[dict[str, Any]] = []
        self.check_runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.rulesets: list[dict[str, Any]] = []
        self.dispatches: list[dict[str, Any]] = []
        self.run_listing_cap: int | None = None
        self.annotations: dict[int, list[dict[str, Any]]] = {}
        self.auto_merges: list[dict[str, Any]] = []
        self.failures: list[Failure] = []
        self.unhandled: list[str] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> FakeGitHub:
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def fail(self, method: str, pattern: str, status: int, times: int) -> None:
        with self.lock:
            self.failures.append(Failure(method, re.compile(pattern), status, times))

    def git(self, *args: str) -> str:
        return self.git_bytes(*args).decode("utf-8")

    def git_bytes(self, *args: str) -> bytes:
        return subprocess.run(["git", "-C", str(self.git_dir), *args], capture_output=True, check=True).stdout

    def open_pull(self, number: int, head_ref: str, base_ref: str = "develop", **extra: Any) -> dict[str, Any]:
        with self.lock:
            pull = {
                "number": number,
                "id": number * 10,
                "node_id": f"PR_{number}",
                "state": "open",
                "draft": False,
                "title": f"change {number}",
                "created_at": "2026-10-01T00:00:00Z",
                "head": {"ref": head_ref, "repo": {"full_name": self.slug}},
                "base": {"ref": base_ref, "repo": {"full_name": self.slug}},
                "labels": [],
                "user": {"login": "joeharris76", "type": "User"},
                **extra,
            }
            self.pulls[number] = pull
            self.refresh(number)
            return pull

    def refresh(self, number: int) -> None:
        pull = self.pulls[number]
        pull["head"]["sha"] = self.git("rev-parse", pull["head"]["ref"]).strip()
        pull["base"]["sha"] = self.git("rev-parse", pull["base"]["ref"]).strip()
        pull["changed_files"] = self.reported_changed_files.get(number, len(self.files(number)))

    def files(self, number: int) -> list[dict[str, Any]]:
        pull = self.pulls[number]
        base, head = pull["base"]["ref"], pull["head"]["ref"]
        merge_base = self.git("merge-base", base, head).strip()
        counts = {}
        for line in self.git("diff", "--numstat", "-M", merge_base, head).splitlines():
            added, deleted, path = line.split("\t", 2)
            counts[path.split(" => ")[-1].rstrip("}")] = (
                int(added) if added != "-" else 0,
                int(deleted) if deleted != "-" else 0,
            )
        items = []
        for line in self.git("diff", "--name-status", "-M", merge_base, head).splitlines():
            parts = line.split("\t")
            code, path, previous = parts[0], parts[-1], parts[1] if len(parts) == 3 else None
            status = {"A": "added", "D": "removed", "M": "modified"}.get(code[0], "renamed")
            added, deleted = next((value for key, value in counts.items() if key.endswith(path)), (0, 0))
            blob = "" if status == "removed" else self.git("rev-parse", f"{head}:{path}").strip()
            item = {"filename": path, "status": status, "additions": added, "deletions": deleted, "sha": blob}
            if previous:
                item["previous_filename"] = previous
            items.append(item)
        return items

    def diff(self, number: int) -> bytes:
        pull = self.pulls[number]
        return self.git_bytes("diff", "-M", f"{pull['base']['ref']}...{pull['head']['ref']}")

    def add_run(
        self,
        workflow: str,
        *,
        event: str,
        pr: int | None,
        head_sha: str,
        title_head: str | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        with self.lock:
            run_id = next(self.ids) * 1000
            recorded = f" head={title_head}" if title_head else ""
            title = f"{workflow.removesuffix('.yml')} ({event} pr={pr if pr is not None else 'sweep'}{recorded})"
            run = {
                "id": run_id,
                "name": workflow.removesuffix(".yml"),
                "workflow_id": WORKFLOWS.get(workflow, 7999),
                "path": f".github/workflows/{workflow}",
                "display_title": title,
                "event": event,
                "status": "in_progress",
                "conclusion": None,
                "head_sha": head_sha,
                "head_branch": "develop",
                "created_at": now_text(),
                "updated_at": now_text(),
                "run_attempt": 1,
                "repository": {"full_name": self.slug},
                "head_repository": {"full_name": self.slug, "owner": {"login": self.slug.split("/")[0]}},
                "pull_requests": [],
                **fields,
            }
            self.runs.append(run)
            return run

    def complete_run(
        self,
        run: dict[str, Any],
        conclusion: str,
        failed_steps: tuple[str, ...] = (),
        job_annotation: str | None = None,
    ) -> None:
        with self.lock:
            run.update({"status": "completed", "conclusion": conclusion, "updated_at": now_text()})
            steps = [{"name": name, "conclusion": "failure"} for name in failed_steps]
            job: dict[str, Any] = {"name": "review", "steps": steps}
            if job_annotation is not None:
                job.update({"id": run["id"] + 1, "conclusion": "cancelled"})
                self.annotations[run["id"] + 1] = [{"annotation_level": "failure", "message": job_annotation}]
            self.jobs[run["id"]] = {"total_count": 1, "jobs": [job]}

    def upload_artifact(self, run: dict[str, Any], name: str, files: dict[str, bytes]) -> None:
        with self.lock:
            artifact_id = next(self.ids)
            data = _zip(files)
            self.artifacts.append(
                {
                    "id": artifact_id,
                    "node_id": f"MDg6QXJ0aWZhY3Q{artifact_id}",
                    "name": name,
                    "size_in_bytes": len(data),
                    "url": f"http://{API_HOST}/repos/{self.slug}/actions/artifacts/{artifact_id}",
                    "archive_download_url": f"http://{API_HOST}/repos/{self.slug}/actions/artifacts/{artifact_id}/zip",
                    "expired": False,
                    "created_at": now_text(),
                    "updated_at": now_text(),
                    "expires_at": "2099-01-01T00:00:00Z",
                    "workflow_run": {"id": run["id"], "head_sha": run["head_sha"]},
                    "data": data,
                }
            )

    def artifact_json(self, name: str, filename: str) -> Any:
        with self.lock:
            item = next(item for item in reversed(self.artifacts) if item["name"] == name)
            with zipfile.ZipFile(io.BytesIO(item["data"])) as archive:
                return json.loads(archive.read(filename))

    def age(self, run: dict[str, Any], minutes: int) -> None:
        with self.lock:
            moment = datetime.strptime(run["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            run["created_at"] = (moment - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
            for item in self.artifacts:
                if item["workflow_run"]["id"] != run["id"]:
                    continue
                with zipfile.ZipFile(io.BytesIO(item["data"])) as archive:
                    files = {name: archive.read(name) for name in archive.namelist()}
                if "state.json" in files:
                    state = json.loads(files["state.json"])
                    updated = datetime.fromisoformat(state["updated_at"]) - timedelta(minutes=minutes)
                    state["updated_at"] = updated.isoformat()
                    files["state.json"] = json.dumps(state).encode("utf-8")
                item["data"] = _zip(files)

    def oracle_statuses(self, sha: str) -> list[dict[str, Any]]:
        return [status for status in self.statuses[sha] if status["context"] == "oracle-verdict"]

    def gate_statuses(self, sha: str) -> list[dict[str, Any]]:
        return [status for status in self.statuses[sha] if status["context"] == "oracle-gate"]

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                return None

            def _body(self) -> bytes:
                if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                    chunks = []
                    while True:
                        size = int(self.rfile.readline().strip() or b"0", 16)
                        if size == 0:
                            self.rfile.readline()
                            return b"".join(chunks)
                        chunks.append(self.rfile.read(size))
                        self.rfile.readline()
                length = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(length) if length else b""

            def _serve(self) -> None:
                body = self._body()
                parts = urlsplit(self.path)
                request = Request(
                    method=self.command,
                    path=unquote(parts.path),
                    query={key: values[-1] for key, values in parse_qs(parts.query).items()},
                    body=body,
                    token=(self.headers.get("Authorization") or "").split(" ")[-1],
                    host=self.headers.get("Host") or API_HOST,
                    accept=self.headers.get("Accept") or "",
                )
                with fake.lock:
                    self._send(*fake.respond(request))

            def _send(self, status: int, payload: Any, headers: dict[str, str] | None = None) -> None:
                if isinstance(payload, bytes):
                    data = payload
                    kind = (headers or {}).pop("Content-Type", "application/octet-stream")
                else:
                    data = json.dumps(payload).encode("utf-8")
                    kind = "application/json; charset=utf-8"
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                self._serve()

            def do_POST(self) -> None:
                self._serve()

            def do_PUT(self) -> None:
                self._serve()

        return Handler

    def _page(self, items: list[Any], request: Request, wrap: str | None = None, total: int | None = None) -> Response:
        per_page = int(request.query.get("per_page", 30))
        page = int(request.query.get("page", 1))
        chunk = items[(page - 1) * per_page : page * per_page]
        headers = {}
        if page * per_page < len(items):
            following = urlencode({**request.query, "page": str(page + 1)})
            headers["Link"] = f'<http://{request.host}{request.path}?{following}>; rel="next"'
        payload: Any = chunk if wrap is None else {"total_count": len(items) if total is None else total, wrap: chunk}
        return 200, payload, headers

    def respond(self, request: Request) -> Response:
        target = f"{request.path}?{urlencode(request.query)}"
        for failure in self.failures:
            if failure.remaining and failure.method == request.method and failure.pattern.search(target):
                failure.remaining -= 1
                return failure.status, {"message": "Server Error"}, {}
        prefix = f"/repos/{self.slug}"
        table = self._top_routes() if not request.path.startswith(prefix) else self._repo_routes()
        rest = request.path.removeprefix(prefix)
        for method, pattern, handler in table:
            match = re.fullmatch(pattern, rest)
            if match is not None and method in (request.method, "*"):
                try:
                    return handler(request, match)
                except (KeyError, StopIteration) as exc:
                    self.unhandled.append(f"{request.method} {request.path}: {exc!r}")
                    return 404, {"message": "Not Found"}, {}
        self.unhandled.append(f"{request.method} {request.path}")
        return 404, {"message": "Not Found"}, {}

    def _top_routes(self) -> list[tuple[str, str, Route]]:
        return [
            ("GET", "/healthz", lambda request, match: (200, {"ok": True}, {})),
            ("POST", "/graphql", lambda request, match: (200, self.graphql(request.json(), request.token), {})),
            ("GET", "/user", lambda request, match: (200, {**ACCOUNTS[request.token], "id": 1}, {})),
        ]

    def _repo_routes(self) -> list[tuple[str, str, Route]]:
        return [
            ("GET", "/?", lambda request, match: (200, {"full_name": self.slug, "default_branch": "develop"}, {})),
            ("POST", "/pulls", self._create_pull),
            ("GET", r"/pulls/(\d+)", self._pull),
            ("GET", "/pulls", self._pull_list),
            ("GET", r"/pulls/(\d+)/files", lambda request, match: self._page(self.files(int(match[1])), request)),
            ("GET", r"/pulls/(\d+)/reviews", lambda request, match: self._page(self.reviews[int(match[1])], request)),
            ("POST", r"/pulls/(\d+)/reviews", self._create_review),
            (
                "GET",
                r"/issues/(\d+)/comments",
                lambda request, match: self._page(self.comments[int(match[1])], request),
            ),
            ("POST", r"/issues/(\d+)/comments", self._create_comment),
            (
                "GET",
                r"/issues/(\d+)/timeline",
                lambda request, match: self._page(self.timeline[int(match[1])], request),
            ),
            ("GET", r"/compare/([^.]+)\.\.\.(.+)", lambda request, match: (200, self.compare(match[1], match[2]), {})),
            ("GET", r"/contents/(.+)", self._contents),
            ("GET", r"/git/trees/([0-9a-f]{40})", lambda request, match: (200, self.tree(match[1]), {})),
            ("GET", r"/commits/([0-9a-f]{40})", self._commit),
            ("GET", r"/commits/([0-9a-f]{40})/statuses", self._status_list),
            ("GET", r"/commits/([0-9a-f]{40})/status", self._combined_status),
            ("GET", r"/commits/([0-9a-f]{40})/check-runs", self._check_runs),
            ("POST", r"/statuses/([0-9a-f]{40})", self._create_status),
            ("GET", r"/actions/workflows/([^/]+)/runs", self._workflow_runs),
            ("GET", r"/actions/workflows/([^/]+)", self._workflow),
            ("POST", r"/actions/workflows/([^/]+)/dispatches", self._dispatch),
            ("GET", r"/actions/runs/(\d+)", lambda request, match: (200, self._run(int(match[1])), {})),
            ("GET", r"/actions/runs/(\d+)/jobs", self._jobs),
            (
                "GET",
                r"/check-runs/(\d+)/annotations",
                lambda request, match: (200, self.annotations.get(int(match[1]), []), {}),
            ),
            ("GET", r"/actions/runs/(\d+)/artifacts", self._run_artifacts),
            ("GET", "/actions/artifacts", self._artifacts),
            ("GET", r"/actions/artifacts/(\d+)/zip", self._artifact_zip),
            ("GET", "/rules/branches/([^/]+)", lambda request, match: (200, self.rulesets, {})),
        ]

    def _contents(self, request: Request, match: re.Match[str]) -> Response:
        data = self.git_bytes("show", f"{request.query['ref']}:{match[1]}")
        return 200, data, {"Content-Type": "text/plain; charset=utf-8"}

    def _pull(self, request: Request, match: re.Match[str]) -> Response:
        number = int(match[1])
        if "diff" in request.accept:
            return 200, self.diff(number), {"Content-Type": "text/plain; charset=utf-8"}
        self.refresh(number)
        return 200, self.pulls[number], {}

    def _create_pull(self, request: Request, match: re.Match[str]) -> Response:
        payload = request.json()
        number = max(self.pulls, default=0) + 1
        pull = self.open_pull(
            number,
            payload["head"],
            payload["base"],
            title=payload["title"],
            body=payload.get("body", ""),
            user={"login": "github-actions[bot]", "type": "Bot"},
        )
        return 201, pull, {}

    def _pull_list(self, request: Request, match: re.Match[str]) -> Response:
        query = request.query
        owner = self.slug.split("/")[0]
        pulls = [
            pull
            for pull in self.pulls.values()
            if query.get("state", "open") in ("all", pull["state"])
            and query.get("base", pull["base"]["ref"]) == pull["base"]["ref"]
            and query.get("head", f"{owner}:{pull['head']['ref']}") == f"{owner}:{pull['head']['ref']}"
        ]
        for pull in pulls:
            self.refresh(pull["number"])
        return self._page(pulls, request)

    def _create_review(self, request: Request, match: re.Match[str]) -> Response:
        return 200, self.post_review(int(match[1]), request.json(), request.token), {}

    def _create_comment(self, request: Request, match: re.Match[str]) -> Response:
        comment = {
            "id": next(self.ids),
            "body": request.json()["body"],
            "user": ACCOUNTS[request.token],
            "author_association": "OWNER" if request.token == OWNER_TOKEN else "NONE",
            "created_at": now_text(),
            "updated_at": now_text(),
        }
        self.comments[int(match[1])].append(comment)
        return 201, comment, {}

    def _commit(self, request: Request, match: re.Match[str]) -> Response:
        date = self.git("show", "-s", "--format=%cI", match[1]).strip()
        return 200, {"sha": match[1], "commit": {"committer": {"date": date}}}, {}

    def _status_list(self, request: Request, match: re.Match[str]) -> Response:
        return self._page(list(reversed(self.statuses[match[1]])), request)

    def _combined_status(self, request: Request, match: re.Match[str]) -> Response:
        latest: dict[str, dict[str, Any]] = {}
        for status in self.statuses[match[1]]:
            latest[status["context"]] = status
        return 200, {"sha": match[1], "statuses": list(reversed(latest.values()))}, {}

    def _check_runs(self, request: Request, match: re.Match[str]) -> Response:
        return self._page(self.check_runs[match[1]], request, "check_runs")

    def _create_status(self, request: Request, match: re.Match[str]) -> Response:
        payload = request.json()
        status = {
            "id": next(self.ids),
            "state": payload["state"],
            "context": payload.get("context", "default"),
            "description": payload.get("description"),
            "target_url": payload.get("target_url"),
            "creator": ACCOUNTS[request.token],
            "created_at": now_text(),
            "updated_at": now_text(),
        }
        self.statuses[match[1]].append(status)
        return 201, status, {}

    def _workflow_name(self, value: str) -> str:
        return value if value in WORKFLOWS else next(key for key, number in WORKFLOWS.items() if str(number) == value)

    def _workflow_runs(self, request: Request, match: re.Match[str]) -> Response:
        name = self._workflow_name(match[1])
        created = request.query.get("created", "").removeprefix(">=")
        wanted = request.query.get("status")
        runs = [
            run
            for run in self.runs
            if run["path"].endswith(f"/{name}")
            and run["created_at"] >= created
            and wanted in (None, run["status"], run["conclusion"])
        ]
        runs.sort(key=lambda run: (run["created_at"], run["id"]), reverse=True)
        total = len(runs)
        if self.run_listing_cap is not None:
            runs = runs[: self.run_listing_cap]
        return self._page(runs, request, "workflow_runs", total)

    def _workflow(self, request: Request, match: re.Match[str]) -> Response:
        name = self._workflow_name(match[1])
        return 200, {"id": WORKFLOWS[name], "name": name, "path": f".github/workflows/{name}", "state": "active"}, {}

    def _dispatch(self, request: Request, match: re.Match[str]) -> Response:
        payload = request.json()
        name = self._workflow_name(match[1])
        self.dispatches.append({"workflow": name, "ref": payload.get("ref"), "inputs": payload.get("inputs") or {}})
        return 204, b"", {}

    def _run(self, run_id: int) -> dict[str, Any]:
        return next(run for run in self.runs if run["id"] == run_id)

    def _jobs(self, request: Request, match: re.Match[str]) -> Response:
        return 200, self.jobs.get(int(match[1]), {"total_count": 0, "jobs": []}), {}

    def _named(self, items: list[dict[str, Any]], request: Request) -> list[dict[str, Any]]:
        found = [self._artifact(item) for item in items]
        return [item for item in found if request.query.get("name", item["name"]) == item["name"]]

    def _run_artifacts(self, request: Request, match: re.Match[str]) -> Response:
        items = [item for item in self.artifacts if item["workflow_run"]["id"] == int(match[1])]
        return self._page(self._named(items, request), request, "artifacts")

    def _artifacts(self, request: Request, match: re.Match[str]) -> Response:
        return self._page(self._named(list(reversed(self.artifacts)), request), request, "artifacts")

    def _artifact_zip(self, request: Request, match: re.Match[str]) -> Response:
        item = next(item for item in self.artifacts if item["id"] == int(match[1]))
        return 200, item["data"], {"Content-Type": "application/zip"}

    def _artifact(self, item: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in item.items() if key != "data"}

    def compare(self, base: str, head: str) -> dict[str, Any]:
        base_sha = self.git("rev-parse", base).strip()
        head_sha = self.git("rev-parse", head).strip()
        merge_base = self.git("merge-base", base_sha, head_sha).strip()
        ahead = int(self.git("rev-list", "--count", f"{base_sha}..{head_sha}").strip())
        behind = int(self.git("rev-list", "--count", f"{head_sha}..{base_sha}").strip())
        status = (
            "identical"
            if not ahead and not behind
            else "ahead"
            if not behind
            else "behind"
            if not ahead
            else "diverged"
        )
        return {
            "status": status,
            "ahead_by": ahead,
            "behind_by": behind,
            "merge_base_commit": {"sha": merge_base},
            "base_commit": {"sha": base_sha},
        }

    def tree(self, sha: str) -> dict[str, Any]:
        entries = []
        for line in self.git("ls-tree", "-r", "--full-tree", sha).splitlines():
            meta, path = line.split("\t", 1)
            mode, kind, blob = meta.split()
            entries.append({"path": path, "mode": mode, "type": kind, "sha": blob})
        return {"sha": sha, "truncated": False, "tree": entries}

    def post_review(self, number: int, payload: dict[str, Any], token: str) -> dict[str, Any]:
        review_id = next(self.ids)
        state = {"APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED"}.get(
            payload.get("event", ""), "COMMENTED"
        )
        review = {
            "id": review_id,
            "node_id": f"PRR_{review_id}",
            "user": ACCOUNTS[token],
            "state": state,
            "body": payload.get("body", ""),
            "submitted_at": now_text(),
            "commit_id": payload.get("commit_id"),
            "edited": False,
            "last_edited_at": None,
        }
        self.reviews[number].append(review)
        for comment in payload.get("comments") or []:
            self.threads[number].append(
                {
                    "id": f"PRRT_{next(self.ids)}",
                    "resolved": False,
                    "path": comment.get("path"),
                    "comments": [{"author": ACCOUNTS[token], "association": "NONE", "body": comment.get("body", "")}],
                }
            )
        return review

    def reply(self, number: int, thread_index: int, body: str, account: dict[str, str], association: str) -> None:
        with self.lock:
            self.threads[number][thread_index]["comments"].append(
                {"author": account, "association": association, "body": body}
            )

    def edit_review(self, number: int, index: int, body: str) -> None:
        with self.lock:
            review = self.reviews[number][index]
            review.update({"body": body, "edited": True, "last_edited_at": now_text()})

    def graphql(self, payload: dict[str, Any], token: str) -> dict[str, Any]:
        query = payload.get("query", "")
        variables = payload.get("variables") or {}
        reviews = {item["node_id"]: item for items in self.reviews.values() for item in items}
        if "PullRequestReviewThread" in query:
            return self._thread_comments(variables)
        if "nodes(ids" in query:
            ids = variables.get("ids") or []
            nodes = [self._edit_state(reviews[node_id], node_id) for node_id in ids if node_id in reviews]
            return {"data": {"nodes": nodes}}
        if "node(id" in query:
            review = reviews.get(variables.get("id"))
            return {"data": {"node": None if review is None else self._edit_state(review, review["node_id"])}}
        if query.lstrip().startswith("mutation"):
            return self.mutation(query, variables)
        number = int(variables.get("number") or variables.get("pr_number") or 0)
        pull = self.pulls.get(number)
        if pull is None:
            return {"data": {"repository": {"pullRequest": None}}, "errors": [{"message": "not found"}]}
        self.refresh(number)
        if "reviewThreads" in query:
            nodes = [
                {
                    "id": thread["id"],
                    "isResolved": thread["resolved"],
                    "isOutdated": False,
                    "path": thread["path"],
                    "comments": {
                        "nodes": [
                            {
                                "author": {
                                    "__typename": item["author"]["type"],
                                    "login": item["author"]["login"].removesuffix("[bot]"),
                                },
                                "authorAssociation": item["association"],
                                "lastEditedAt": None,
                                "includesCreatedEdit": False,
                                "body": item["body"],
                            }
                            for item in thread["comments"][:COMMENT_PAGE]
                        ],
                        "pageInfo": self._comment_page(thread, 0, COMMENT_PAGE),
                    },
                }
                for thread in self.threads[number]
            ]
            connection = {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": nodes,
                "totalCount": len(nodes),
            }
            return {"data": {"repository": {"pullRequest": {"reviewThreads": connection}}}}
        return {"data": {"repository": {"pullRequest": self.graphql_pull(pull)}}}

    def _comment_page(self, thread: dict[str, Any], start: int, size: int) -> dict[str, Any]:
        end = start + size
        more = len(thread["comments"]) > end
        return {"hasNextPage": more, "endCursor": str(end) if more else None}

    def _thread_comments(self, variables: dict[str, Any]) -> dict[str, Any]:
        thread = next(item for items in self.threads.values() for item in items if item["id"] == variables["thread"])
        start = int(variables["after"])
        nodes = [
            {
                "author": {
                    "__typename": item["author"]["type"],
                    "login": item["author"]["login"].removesuffix("[bot]"),
                },
                "authorAssociation": item["association"],
                "body": item["body"],
            }
            for item in thread["comments"][start : start + COMMENT_PAGE_AFTER]
        ]
        page = self._comment_page(thread, start, COMMENT_PAGE_AFTER)
        return {"data": {"node": {"comments": {"pageInfo": page, "nodes": nodes}}}}

    def _edit_state(self, review: dict[str, Any], node_id: str) -> dict[str, Any]:
        return {"id": node_id, "lastEditedAt": review["last_edited_at"], "includesCreatedEdit": review["edited"]}

    def graphql_pull(self, pull: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": pull["node_id"],
            "number": pull["number"],
            "title": pull["title"],
            "url": f"https://{GH_HOST}/{self.slug}/pull/{pull['number']}",
            "state": pull["state"].upper(),
            "isDraft": pull["draft"],
            "headRefOid": pull["head"]["sha"],
            "headRefName": pull["head"]["ref"],
            "baseRefName": pull["base"]["ref"],
            "reviewDecision": pull.get("review_decision"),
            "labels": {"nodes": pull["labels"], "totalCount": len(pull["labels"])},
            "autoMergeRequest": pull.get("auto_merge"),
            "mergeStateStatus": "BLOCKED",
            "isInMergeQueue": False,
            "isMergeQueueEnabled": False,
            "viewerCanEnableAutoMerge": True,
            "headRepositoryOwner": {"login": self.slug.split("/")[0]},
            "headRepository": {"name": self.slug.split("/")[1]},
        }

    def mutation(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        if "enablePullRequestAutoMerge" in query:
            pull = next(item for item in self.pulls.values() if item["node_id"] == variables.get("prID"))
            record = {
                "pr": pull["number"],
                "head": variables.get("expectedHeadOid"),
                "method": variables.get("mergeMethod"),
            }
            self.auto_merges.append(record)
            pull["auto_merge"] = {"enabledAt": now_text(), "mergeMethod": record["method"]}
            return {"data": {"enablePullRequestAutoMerge": {"clientMutationId": None}}}
        return {"errors": [{"message": f"unsupported mutation: {query[:80]}"}]}
