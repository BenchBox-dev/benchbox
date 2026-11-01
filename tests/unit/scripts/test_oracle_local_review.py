from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.skipif(
        not (Path(__file__).resolve().parents[3] / "_project" / "scripts" / "oracle_reviewers").is_dir(),
        reason="_project scripts are not in this checkout (release tree)",
    ),
]

ROOT = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location("oracle_local_review", ROOT / "scripts/oracle_local_review.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap = _load()
REVIEWER_FILE = "_project/scripts/oracle_reviewers/cli.py"
CREDENTIALS_FILE = "_project/scripts/oracle_reviewers/credentials.py"


@pytest.fixture(autouse=True)
def _no_ambient_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        from _project.scripts.oracle_reviewers import local
    except ModuleNotFoundError:
        return

    for name in local.SCRUBBED_AT_START:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(bootstrap.HANDOVER_ENV, raising=False)


@pytest.fixture(autouse=True)
def _close_handover_descriptors(monkeypatch: pytest.MonkeyPatch):
    opened: list[int] = []
    original = bootstrap.hand_over

    def recording(environ, names):
        scrubbed = original(environ, names)
        if bootstrap.HANDOVER_ENV in scrubbed:
            opened.append(int(scrubbed[bootstrap.HANDOVER_ENV]))
        return scrubbed

    monkeypatch.setattr(bootstrap, "hand_over", recording)
    yield
    for descriptor in opened:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, files: dict[str, str]) -> str:
    for name, text in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        _git(repo, "add", name)
    _git(repo, "commit", "--quiet", "-m", "change")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def clones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "--quiet", "--initial-branch=develop")
    seed = {path: f"trusted {path}\n" for path in bootstrap.REVIEWER_PATHS if "." in Path(path).name}
    seed[REVIEWER_FILE] = "trusted cli\n"
    seed[CREDENTIALS_FILE] = (ROOT / CREDENTIALS_FILE).read_text(encoding="utf-8")
    seed["unrelated.txt"] = "unrelated\n"
    _commit(origin, seed)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "--quiet", str(origin), str(work)], check=True, capture_output=True)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(work)
    return origin, work


def _args(*extra: str):
    return bootstrap.parser().parse_args(["--head", "HEAD", "--body-file", "body.md", *extra])


def test_the_export_holds_only_the_reviewer_paths_of_the_chosen_commit(clones: tuple[Path, Path]) -> None:
    _, work = clones
    (work / REVIEWER_FILE).write_text("branch edit\n", encoding="utf-8")
    commit = _git(work, "rev-parse", "origin/develop")

    target = bootstrap.export_reviewer(work, commit, Path.home() / bootstrap.EXPORT_PARENT)

    assert (target / REVIEWER_FILE).read_text(encoding="utf-8") == "trusted cli\n"
    assert not (target / "unrelated.txt").exists()
    assert target.name == commit


def test_a_complete_export_is_reused_for_the_same_commit(clones: tuple[Path, Path]) -> None:
    _, work = clones
    commit = _git(work, "rev-parse", "origin/develop")
    parent = Path.home() / bootstrap.EXPORT_PARENT
    first = bootstrap.export_reviewer(work, commit, parent)
    (first / "marker").write_text("kept\n", encoding="utf-8")

    second = bootstrap.export_reviewer(work, commit, parent)

    assert second == first and (second / "marker").is_file()


def test_the_review_launches_from_the_fetched_develop_export_with_the_pinned_runner(clones: tuple[Path, Path]) -> None:
    origin, work = clones
    advanced = _commit(origin, {REVIEWER_FILE: "advanced cli\n"})
    launched: list[tuple[list[str], dict[str, str]]] = []

    code = bootstrap.run(_args(), lambda argv, env: launched.append((argv, env)) or 0)

    assert code == 0
    ((argv, env),) = launched
    export = Path(env["PYTHONPATH"])
    assert export.name == advanced
    assert (export / REVIEWER_FILE).read_text(encoding="utf-8") == "advanced cli\n"
    assert argv[:2] == ["uv", "run"] and "--isolated" in argv and "--no-project" in argv
    assert argv[argv.index("python") + 1] == "-P"
    assert argv[argv.index("local-review") :][:3] == ["local-review", "--policy", ".github/oracle-reviewers.yml"]
    assert ("--base-ref", "origin/develop") in set(zip(argv, argv[1:], strict=False))


def test_the_review_refuses_without_launching_when_develop_cannot_be_fetched(
    clones: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    origin, work = clones
    _git(work, "remote", "set-url", "origin", str(origin) + "-missing")
    launched: list[object] = []

    code = bootstrap.run(_args(), lambda argv, env: launched.append(argv) or 0)

    assert code == bootstrap.EXIT_UNAVAILABLE
    assert launched == []
    error = capsys.readouterr().err
    assert "origin/develop" in error and "cannot run" in error
    assert not (Path.home() / bootstrap.EXPORT_PARENT).exists()


def test_an_explicit_reviewer_ref_is_exported_without_fetching(clones: tuple[Path, Path]) -> None:
    origin, work = clones
    _git(work, "remote", "set-url", "origin", str(origin) + "-missing")
    _git(work, "switch", "--quiet", "-c", "feature")
    branch_head = _commit(work, {REVIEWER_FILE: "branch cli\n"})
    launched: list[dict[str, str]] = []

    code = bootstrap.run(_args("--reviewer-ref", "HEAD"), lambda argv, env: launched.append(env) or 0)

    assert code == 0
    export = Path(launched[0]["PYTHONPATH"])
    assert export.name == branch_head
    assert (export / REVIEWER_FILE).read_text(encoding="utf-8") == "branch cli\n"


def test_only_the_newest_exports_are_kept(clones: tuple[Path, Path]) -> None:
    _, work = clones
    parent = Path.home() / bootstrap.EXPORT_PARENT
    parent.mkdir(parents=True)
    for index in range(5):
        stale = parent / f"{index:040d}"
        stale.mkdir()
        os.utime(stale, (index, index))
    current = bootstrap.export_reviewer(work, _git(work, "rev-parse", "origin/develop"), parent)

    bootstrap.prune_exports(parent, current)

    kept = sorted(item.name for item in parent.iterdir())
    assert len(kept) == bootstrap.EXPORT_KEEP
    assert current.name in kept and f"{4:040d}" in kept and f"{0:040d}" not in kept


def test_the_withheld_names_and_handover_variable_match_the_reviewer(clones: tuple[Path, Path]) -> None:
    from _project.scripts.oracle_reviewers import local

    _, work = clones
    commit = _git(work, "rev-parse", "origin/develop")
    target = bootstrap.export_reviewer(work, commit, Path.home() / bootstrap.EXPORT_PARENT)

    assert bootstrap.withheld_names(target) == local.SCRUBBED_AT_START
    assert bootstrap.HANDOVER_ENV == local.HANDOVER_ENV


def test_the_launched_environment_holds_no_secret_and_hands_them_over_a_descriptor(
    clones: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    secrets = {
        "TYPESAFE_API_KEY": "k" * 16,
        "TS_AUTHKEY": "t" * 16,
        "GH_TOKEN": "g" * 16,
        "ORACLE_CLOUD_GATEWAY_TOKEN": "c" * 16,
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("UNRELATED_SETTING", "kept")
    launched: list[dict[str, str]] = []

    code = bootstrap.run(_args(), lambda argv, env: launched.append(env) or 0)

    assert code == 0
    (env,) = launched
    assert not set(secrets) & set(env)
    assert not [value for value in env.values() if any(secret in value for secret in secrets.values())]
    assert env["UNRELATED_SETTING"] == "kept"
    with os.fdopen(int(env[bootstrap.HANDOVER_ENV]), "r", encoding="utf-8") as handle:
        assert json.load(handle) == secrets


def test_no_handover_descriptor_is_created_when_nothing_is_withheld(
    clones: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in bootstrap_names():
        monkeypatch.delenv(name, raising=False)
    launched: list[dict[str, str]] = []

    assert bootstrap.run(_args(), lambda argv, env: launched.append(env) or 0) == 0
    assert bootstrap.HANDOVER_ENV not in launched[0]


def bootstrap_names() -> frozenset[str]:
    from _project.scripts.oracle_reviewers import local

    return local.SCRUBBED_AT_START


@pytest.mark.skipif(os.name == "nt", reason="the stub uv is a shebang script")
def test_the_real_launcher_execs_uv_without_any_withheld_secret_in_its_environment(
    clones: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json
    import sys

    _, work = clones
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    dump = tmp_path / "uv-env.json"
    stub = stubs / "uv"
    stub.write_text(
        f"#!{sys.executable}\nimport json, os\n"
        f"open({str(dump)!r}, 'w').write(json.dumps({{'env': dict(os.environ), 'fd': os.environ.get('ORACLE_LOCAL_HANDOVER_FD')}}))\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    secrets = {
        "TYPESAFE_API_KEY": "k" * 16,
        "TS_AUTHKEY": "t" * 16,
        "GH_TOKEN": "g" * 16,
        "ORACLE_CLOUD_GATEWAY_TOKEN": "c" * 16,
    }
    env = {**os.environ, **secrets, "HOME": os.environ["HOME"], "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}"}

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/oracle_local_review.py"), "--head", "HEAD", "--body-file", "body.md"],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    seen = json.loads(dump.read_text(encoding="utf-8"))
    assert not set(secrets) & set(seen["env"])
    assert not [value for value in seen["env"].values() if any(secret in value for secret in secrets.values())]
    assert seen["fd"] is not None
