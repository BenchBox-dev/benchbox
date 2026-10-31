from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fcntl")

from _project.scripts.oracle_reviewers import credentials, local, protocol, selection
from _project.scripts.oracle_reviewers.commands import gateway_proxy, omp_env
from _project.scripts.oracle_reviewers.verdict import validate

from .conftest import POLICY_PATH, REPO_ROOT
from .test_attempts import _finding, _v

pytestmark = [pytest.mark.unit, pytest.mark.fast]

LOCAL_TOKEN = "localtok-" + "k3Jd9sLp" * 5
CLOUD_TOKEN = "cloudtok-" + "Wq8Zx2Rm" * 5
CLOUD_URL = "https://cloud-gateway.example.ts.net:4000"
TYPESAFE_KEY = "tsk-live-" + "Zy9Xw8Vu" * 4
TS_AUTHKEY = "tskey-auth-" + "Pq7Lm2Nv" * 4
GITHUB_ACCESS = {"GH_TOKEN": "ghp_" + "a1B2c3D4" * 4, "GITHUB_TOKEN": "ghs_" + "e5F6g7H8" * 4}
POLICY_TEXT = POLICY_PATH.read_text(encoding="utf-8")
MANIFEST_TEXT = (REPO_ROOT / local.SOUNDNESS_MANIFEST).read_text(encoding="utf-8")
INERT_TEXT = (REPO_ROOT / local.INERT_MANIFEST).read_text(encoding="utf-8")
HOST_TOKENS = {"ORACLE_HOST_TOKEN_LOCAL": LOCAL_TOKEN, "ORACLE_HOST_TOKEN_CLOUD": CLOUD_TOKEN}
WITHHELD_VALUES = {
    "TYPESAFE_API_KEY": TYPESAFE_KEY,
    "TS_AUTHKEY": TS_AUTHKEY,
    "OMP_GATEWAY_TOKEN": CLOUD_TOKEN,
    "ORACLE_CLOUD_GATEWAY_TOKEN": CLOUD_TOKEN,
    "ORACLE_GATEWAY_URL": CLOUD_URL,
    **GITHUB_ACCESS,
}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "home"
    directory.mkdir()
    monkeypatch.setenv("HOME", str(directory))
    for name in (*WITHHELD_VALUES, "BENCHBOX_EPHEMERAL_CLONE", "GH_ENTERPRISE_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(local, "_handed_over", {})
    return directory


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "--quiet", "--initial-branch=develop")
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "--quiet", "-m", "seed")
    return path


def _judged(findings: list[dict[str, Any]], decision: str | None = None, max_defects: int = 10, prior: Any = ()) -> Any:
    verdict = validate(_v("summary", findings, decision))
    return protocol.judge(verdict, max_defects, prior)


def test_the_state_directory_is_fixed_per_user_with_no_environment_override(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BENCHBOX_LOCAL_ORACLE_DIR", str(tmp_path / "elsewhere"))
    assert local.state_dir() == home / ".cache" / "benchbox" / "local-oracle"
    assert not hasattr(local, "STATE_DIR_ENV")


def test_a_medium_only_ship_with_fixes_proceeds(capsys: pytest.CaptureFixture[str]) -> None:
    outcome = local.judged_outcome(_judged([_finding("Medium"), _finding("Low", 3)]), "medium")

    assert outcome.label == "SHIP WITH FIXES" and not outcome.blocked
    reporter = local.Reporter()
    assert local._decide(outcome, "", reporter) == local.EXIT_PROCEED


@pytest.mark.parametrize("severity", ["Critical", "High"])
def test_a_blocking_finding_blocks(severity: str, capsys: pytest.CaptureFixture[str]) -> None:
    outcome = local.judged_outcome(_judged([_finding(severity)]), "medium")

    assert outcome.blocked
    assert local._decide(outcome, "", local.Reporter()) == local.EXIT_REFUSED
    assert "Refusing to open the PR" in capsys.readouterr().err


def test_a_do_not_ship_from_the_max_defects_overflow_blocks() -> None:
    overflow = [_finding("Low", line) for line in range(1, 12)]

    outcome = local.judged_outcome(_judged(overflow, max_defects=10), "medium")

    assert outcome.label == "DO NOT SHIP" and outcome.blocked
    assert "more than the 10 allowed" in outcome.summary


def test_a_carried_critical_blocks_even_when_the_new_diff_is_clean() -> None:
    carried = {"id": "D1", "severity": "Critical", "file": "a.py", "line": 3, "end_line": None, "title": "carried"}

    outcome = local.judged_outcome(_judged([], prior=[carried]), "medium")

    assert outcome.blocked and outcome.label == "SHIP WITH FIXES"
    assert [item["severity"] for item in outcome.findings] == ["Critical"]


def test_a_carried_medium_does_not_block() -> None:
    carried = {"id": "D1", "severity": "Medium", "file": "a.py", "line": 3, "end_line": None, "title": "carried"}

    assert not local.judged_outcome(_judged([], prior=[carried]), "medium").blocked


def _record(decision: str, *severities: str) -> dict[str, Any]:
    defects = [
        {"id": f"D{index}", "severity": severity, "file": "a.py", "line": index, "end_line": None, "title": "t"}
        for index, severity in enumerate(severities, 1)
    ]
    return {"decision": decision, "reviewer": "medium", "summary": "recorded", "open_defects": defects}


@pytest.mark.parametrize(
    ("decision", "severities", "blocked"),
    [
        ("SHIP", (), False),
        ("SHIP_WITH_FIXES", ("Medium",), False),
        ("SHIP_WITH_FIXES", ("Medium", "High"), True),
        ("SHIP_WITH_FIXES", ("Critical",), True),
        ("DO_NOT_SHIP", (), True),
        ("REFUSED", (), True),
    ],
)
def test_a_recorded_decision_blocks_on_the_same_rule(decision: str, severities: tuple[str, ...], blocked: bool) -> None:
    assert local.recorded_outcome(_record(decision, *severities)).blocked is blocked


def test_override_line_semantics_are_unchanged() -> None:
    blocked = local.Outcome(True, "SHIP WITH FIXES", "medium", "", ())
    reporter = local.Reporter()

    assert local._decide(blocked, "Notes\n\nLocal oracle override: intended\n", reporter) == local.EXIT_PROCEED
    assert local._decide(blocked, "Local oracle override:\n", reporter) == local.EXIT_REFUSED
    assert local._decide(blocked, "<!--\nLocal oracle override: hidden\n", reporter) == local.EXIT_REFUSED
    assert local._decide(blocked, "    Local oracle override: code sample\n", reporter) == local.EXIT_REFUSED
    assert local._decide(blocked, "Local oracle override: <reason>\n", reporter) == local.EXIT_REFUSED


def _plan() -> dict[str, Any]:
    return {
        "run_id": "r1",
        "run_uuid": "r1",
        "created_at": "t1",
        "head_sha": "h1",
        "base_sha": "b1",
        "decision": "review",
        "protocol": {"patch_digest": "d1", "kind": "first", "prior": []},
        "pool_blocked_until": {},
    }


def test_the_cache_key_follows_the_shared_digest_and_every_trusted_input() -> None:
    trusted = local.Trusted(POLICY_TEXT, MANIFEST_TEXT, INERT_TEXT)
    base = local.cache_key(_plan(), "tree", trusted)

    same = {
        **_plan(),
        "run_id": "r2",
        "run_uuid": "r2",
        "created_at": "t2",
        "head_sha": "h2",
        "pool_blocked_until": {"x": 1},
    }
    assert local.cache_key(same, "tree", trusted) == base

    digest = {**_plan(), "protocol": {"patch_digest": "d2", "kind": "first", "prior": []}}
    assert local.cache_key(digest, "tree", trusted) != base
    assert local.cache_key({**_plan(), "base_sha": "b2"}, "tree", trusted) != base
    assert local.cache_key(_plan(), "other-tree", trusted) != base
    for changed in (
        local.Trusted(POLICY_TEXT + "\n# changed\n", MANIFEST_TEXT, INERT_TEXT),
        local.Trusted(POLICY_TEXT, MANIFEST_TEXT + "\nfile\textra.txt\n", INERT_TEXT),
        local.Trusted(POLICY_TEXT, MANIFEST_TEXT, INERT_TEXT + "\nfile\textra.txt\n"),
    ):
        assert local.cache_key(_plan(), "tree", changed) != base


def test_the_trusted_digest_covers_the_model_mapping_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    trusted = local.Trusted(POLICY_TEXT, MANIFEST_TEXT, INERT_TEXT)
    before = trusted.digest()
    config = tmp_path / "oracle-omp-config.yml"
    config.write_text(local.cli.OMP_CONFIG_SOURCE.read_text(encoding="utf-8") + "# remapped\n", encoding="utf-8")
    monkeypatch.setattr(local.cli, "OMP_CONFIG_SOURCE", config)

    assert trusted.digest() != before


def test_an_outcome_round_trips_through_the_cache_and_a_bad_record_is_ignored(tmp_path: Path) -> None:
    outcome = local.Outcome(
        True,
        "SHIP WITH FIXES",
        "medium",
        "s",
        ({"severity": "High", "file": "a", "line": 1, "title": "t", "detail": "d"},),
    )
    path = local.cache_path(tmp_path, "k")

    local._store(path, outcome, ())

    assert local._load_cached(path) == outcome
    path.write_text(json.dumps({"blocked": "yes"}), encoding="utf-8")
    assert local._load_cached(path) is None


def test_a_cached_outcome_never_holds_a_secret_value(tmp_path: Path) -> None:
    leaking = local.Outcome(True, "SHIP WITH FIXES", "medium", f"see {TYPESAFE_KEY}", ())
    path = local.cache_path(tmp_path, "k")

    local._store(path, leaking, (TYPESAFE_KEY,))

    assert TYPESAFE_KEY not in path.read_text(encoding="utf-8")


def _run(tmp_path: Path, key: str = TYPESAFE_KEY) -> local.Run:
    run = local.Run(
        root=tmp_path / "repo",
        work=tmp_path / "work",
        policy=tmp_path / "policy.yml",
        soundness=tmp_path / "soundness.txt",
        inert=tmp_path / "inert.txt",
        repository="o/r",
        head="h" * 40,
        base_ref="origin/develop",
        pr=None,
        run_id="run-1",
        reporter=local.Reporter(),
        key=key,
    )
    run.access = local.Credentials(local.CLOUD_CLIENT, CLOUD_URL, CLOUD_TOKEN)
    return run


def _grants(run: local.Run, phase: str) -> set[str]:
    environ = run._environ(phase, run.grants(phase))
    return {name for name in environ if name in local.WITHHELD_FROM_PHASES or name in HOST_TOKENS} | {
        name for name in ("ORACLE_GATEWAY_URL",) if name in environ
    }


def test_every_phase_receives_exactly_the_secrets_it_needs(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name, value in {**WITHHELD_VALUES, "GH_ENTERPRISE_TOKEN": "ghe_" + "z9Y8x7W6" * 4}.items():
        monkeypatch.setenv(name, value)
    gateway = home / local.GATEWAY_DIRECTORY
    gateway.mkdir(parents=True)
    (gateway / "local-client.token").write_text(LOCAL_TOKEN + "\n", encoding="utf-8")
    (gateway / "cloud-client.token").write_text(CLOUD_TOKEN + "\n", encoding="utf-8")
    run = _run(tmp_path)
    key = {"TYPESAFE_API_KEY"}
    hosts = set(HOST_TOKENS)
    gateway_pair = {"OMP_GATEWAY_TOKEN", "ORACLE_GATEWAY_URL"}
    monkeypatch.setattr(local, "_handed_over", {"TS_AUTHKEY": TS_AUTHKEY})

    assert _grants(run, "prepare") == key | set(GITHUB_ACCESS) | {"GH_ENTERPRISE_TOKEN"}
    assert _grants(run, "scan") == key | {"TS_AUTHKEY"} | hosts
    assert _grants(run, "harness") == set()
    assert _grants(run, "isolation") == set()
    assert _grants(run, "attempts") == gateway_pair
    assert _grants(run, "recover") == key
    assert _grants(run, "finalize") == key | {"TS_AUTHKEY"} | hosts | gateway_pair


def test_the_attempt_phase_environment_holds_no_withheld_value(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name, value in WITHHELD_VALUES.items():
        monkeypatch.setenv(name, value)
    run = _run(tmp_path)

    environ = run._environ("attempts", run.grants("attempts"))

    held = {name for name, value in environ.items() if value in (TYPESAFE_KEY, TS_AUTHKEY, *GITHUB_ACCESS.values())}
    assert held == set()
    assert environ["OMP_GATEWAY_TOKEN"] == CLOUD_TOKEN


def test_the_scan_roots_name_the_gateway_directory_without_its_canonical_files(home: Path, tmp_path: Path) -> None:
    gateway = home / local.GATEWAY_DIRECTORY
    gateway.mkdir(parents=True)
    run = _run(tmp_path)

    environ = run._environ("scan", run.grants("scan"))

    assert str(gateway) in environ[local.SCAN_ENV].split(os.pathsep)
    assert str(gateway / "local-client.token") in environ[local.EXCLUDE_ENV].split(os.pathsep)
    assert str(run.work) in environ[local.SCAN_ENV].split(os.pathsep)


def test_withheld_secrets_leave_the_process_environment_but_stay_available(tmp_path: Path) -> None:
    script = (
        "import json, os\n"
        "from _project.scripts.oracle_reviewers import local\n"
        "local.withhold_secrets()\n"
        "print(json.dumps({'environ': sorted(set(os.environ) & set(local.SCRUBBED_AT_START)), "
        "'held': local._held('TYPESAFE_API_KEY'), 'url': local.resolve_credentials().url}))\n"
    )
    environ = {
        **{name: value for name, value in os.environ.items() if name not in local.SCRUBBED_AT_START},
        "PYTHONPATH": str(REPO_ROOT),
        "HOME": str(tmp_path),
        **WITHHELD_VALUES,
    }

    result = subprocess.run([sys.executable, "-c", script], env=environ, capture_output=True, text=True, check=True)

    seen = json.loads(result.stdout.splitlines()[-1])
    assert seen == {"environ": [], "held": TYPESAFE_KEY, "url": CLOUD_URL}


def test_pool_blocks_persist_for_the_next_review(tmp_path: Path) -> None:
    moment = datetime.now(UTC)
    blocks = {
        "codex": selection.PoolBlock(moment + timedelta(hours=1), "openai", moment).to_json(),
        "claude": selection.PoolBlock(moment - timedelta(hours=1), "anthropic", moment - timedelta(hours=2)).to_json(),
    }
    path = tmp_path / "state" / local.POOL_BLOCKS_FILE

    local._store_pool_blocks(path, blocks)
    loaded = local._load_pool_blocks(path)

    assert set(loaded) == {"codex", "claude"}
    assert set(selection.active_blocks(loaded, moment, same_head=False)) <= {"codex"}
    assert local._load_pool_blocks(tmp_path / "missing.json") == {}


def test_a_second_review_of_the_same_patch_names_the_lock_holder_then_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import fcntl

    monkeypatch.setattr(local, "LOCK_WAIT_SECONDS", 0.3)
    monkeypatch.setattr(local, "LOCK_POLL_SECONDS", 0.05)
    lock = tmp_path / "cache" / "k.lock"
    lock.parent.mkdir()
    with open(lock, "a+", encoding="utf-8") as held:
        held.write("branch feat/other, started 2026-10-30 10:00:00Z")
        held.flush()
        fcntl.flock(held, fcntl.LOCK_EX)
        with pytest.raises(local.Unavailable, match="another local oracle review of this patch"):
            with local.exclusive(lock, local.Reporter(), "branch feat/me"):
                raise AssertionError("the lock must not be granted")
    assert "branch feat/other, started 2026-10-30 10:00:00Z" in capsys.readouterr().out


def test_a_lock_held_for_another_patch_does_not_delay_this_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fcntl

    monkeypatch.setattr(local, "LOCK_WAIT_SECONDS", 0.3)
    directory = tmp_path / "cache"
    directory.mkdir()
    with open(directory / ("0" * 64 + ".lock"), "a+", encoding="utf-8") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with local.exclusive(directory / ("1" * 64 + ".lock"), local.Reporter(), "branch feat/me"):
            assert (directory / ("1" * 64 + ".lock")).read_text(encoding="utf-8") == "branch feat/me"


def test_the_local_environment_reads_its_adapter_values_and_posts_nothing(tmp_path: Path) -> None:
    environ = {
        local.WORK_ENV: str(tmp_path / "work"),
        local.REPOSITORY_ENV: "o/r",
        local.ROOT_ENV: str(tmp_path / "repo"),
        local.BASE_ENV: "origin/develop",
        local.HEAD_ENV: "h" * 40,
        local.STATE_ENV: str(tmp_path / "state"),
        local.SCAN_ENV: os.pathsep.join(["/a", "/b"]),
        local.EXCLUDE_ENV: "/a/x",
        local.PROXY_ENV: "http://127.0.0.1:1055",
    }

    env = local.LocalEnvironment.from_environ(environ, tmp_path / "policy.yml")

    assert env.layout.attempts == tmp_path / "work" / "attempts"
    assert env.scan_roots == (Path("/a"), Path("/b")) and env.scan_excludes == (Path("/a/x"),)
    assert env.pr == "" and env.has_app is False and env.loopback_http is True
    assert {"TYPESAFE_API_KEY", "TS_AUTHKEY", *HOST_TOKENS} <= set(env.redacted_secrets)
    with pytest.raises(local.driver.CredentialError, match="posts nothing"):
        env.sink()
    with env.session():
        assert omp_env().get("HTTPS_PROXY") == "http://127.0.0.1:1055"


class _ProxyServer:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        requests = self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                requests.append((self.path, self.headers.get("Host", "")))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args: Any) -> None:
                return None

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> _ProxyServer:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()


def _write_status(repo: Path, proxy: str) -> None:
    git_dir = Path(_git(repo, "rev-parse", "--absolute-git-dir"))
    (git_dir / local.CLOUD_STATUS_FILE).write_text(
        f"gateway=reachable\ngateway_detail=ok\ngateway_proxy={proxy}\nomp=installed\n", encoding="utf-8"
    )


def test_reachable_goes_through_the_gateway_proxy_and_fails_without_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local, "REACHABILITY_SECONDS", 3)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    unreachable = "http://127.0.0.1:1"
    with _ProxyServer() as proxy:
        assert local.reachable(unreachable, proxy.url) is True
        assert proxy.requests == [(f"{unreachable}/healthz", "127.0.0.1:1")]
    assert local.reachable(unreachable) is False


def test_reachable_ignores_the_ambient_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local, "REACHABILITY_SECONDS", 3)
    with _ProxyServer() as ambient:
        monkeypatch.setenv("HTTP_PROXY", ambient.url)
        monkeypatch.setenv("http_proxy", ambient.url)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        assert local.reachable("http://127.0.0.1:1") is False
        assert ambient.requests == []


def test_cloud_credentials_read_the_proxy_from_the_gateway_status_file(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ORACLE_GATEWAY_URL", CLOUD_URL)
    monkeypatch.setenv("ORACLE_CLOUD_GATEWAY_TOKEN", CLOUD_TOKEN)
    assert local.resolve_credentials().proxy == ""
    _write_status(repo, "http://127.0.0.1:1055")
    assert local.resolve_credentials().proxy == "http://127.0.0.1:1055"
    _write_status(repo, "http://evil.example:1055")
    assert local.resolve_credentials().proxy == ""


def test_local_credentials_use_the_loopback_client_and_ignore_the_gateway_status_file(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    token = home / local.LOCAL_TOKEN_PATH
    token.parent.mkdir(parents=True)
    token.write_text(LOCAL_TOKEN + "\n", encoding="utf-8")
    _write_status(repo, "http://127.0.0.1:1055")

    access = local.resolve_credentials()

    assert (access.client, access.url, access.token, access.proxy) == (
        local.LOCAL_CLIENT,
        local.LOCAL_GATEWAY_URL,
        LOCAL_TOKEN,
        "",
    )


def test_a_missing_local_client_token_refuses(home: Path) -> None:
    with pytest.raises(local.Unavailable, match="client token is missing"):
        local.resolve_credentials()


def test_a_cloud_session_without_the_cloud_client_refuses_instead_of_using_the_local_one(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = home / local.LOCAL_TOKEN_PATH
    token.parent.mkdir(parents=True)
    token.write_text(LOCAL_TOKEN + "\n", encoding="utf-8")
    monkeypatch.setenv(local.CLOUD_SESSION_ENV, "1")

    with pytest.raises(local.Unavailable, match=local.CLOUD_CLIENT):
        local.resolve_credentials()


def test_a_session_cannot_inject_a_reviewer_proxy_through_its_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORACLE_GATEWAY_PROXY", "http://evil.example:8080")
    assert not {"HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"} & set(omp_env())
    with gateway_proxy("http://evil.example:8080"):
        assert not {"HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"} & set(omp_env())
    with gateway_proxy("http://127.0.0.1:1055"):
        assert omp_env()["HTTPS_PROXY"] == "http://127.0.0.1:1055"
    assert not {"HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"} & set(omp_env())


def test_a_renamed_gate_file_still_flags_the_gate_through_its_old_path() -> None:
    plan = {
        "gate_path_list": [".github/workflows/oracle-verdict.yml"],
        "reviewed_files": [
            {
                "path": ".github/workflows/renamed-review.yml",
                "previous_path": ".github/workflows/oracle-verdict.yml",
                "additions": 0,
                "deletions": 0,
                "status": "renamed",
            }
        ],
    }

    assert local.cli.plan_gate_flags(plan) == [".github/workflows/oracle-verdict.yml"]


def test_the_known_credential_names_cover_the_values_the_phases_withhold() -> None:
    assert {"TYPESAFE_API_KEY", "OMP_GATEWAY_TOKEN", "ORACLE_GATEWAY_URL"} <= set(credentials.NAMES)
    assert set(local.HOST_TOKEN_FILES) | {"TS_AUTHKEY", "GH_TOKEN"} <= local.WITHHELD_FROM_PHASES


@pytest.mark.parametrize("phase", ["prepare", "harness", "scan", "isolation", "attempts", "recover", "finalize"])
def test_no_phase_environment_carries_the_gateway_proxy_as_a_proxy_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    run = _run(tmp_path)
    run.access = local.Credentials(local.CLOUD_CLIENT, CLOUD_URL, CLOUD_TOKEN, "http://127.0.0.1:1055")

    environ = run._environ(phase, run.grants(phase))

    assert not {"HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy"} & set(environ)
    assert environ[local.PROXY_ENV] == "http://127.0.0.1:1055"
