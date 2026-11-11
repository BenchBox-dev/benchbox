from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_script():
    name = "check_duplicate_code_under_test"
    path = REPO_ROOT / "scripts" / "check_duplicate_code.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dup = _load_script()


class TestDeltaVerdict:
    def test_unchanged_not_increased(self):
        increased, delta = dup.delta_verdict(100, 100)
        assert increased is False
        assert delta == 0

    def test_decrease_not_increased(self):
        increased, delta = dup.delta_verdict(100, 80)
        assert increased is False
        assert delta == -20

    def test_increase_detected(self):
        increased, delta = dup.delta_verdict(100, 150)
        assert increased is True
        assert delta == 50


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=check,
    )


def _init_repo_with_clone(tmp_path: Path) -> Path:

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    package = repo / "benchbox"
    package.mkdir()

    body = textwrap.dedent(
        """\
        def clone_a(x):
            a = x + 1
            b = a * 2
            c = b - 3
            d = c // 4
            e = d + 5
            f = e * 6
            g = f - 7
            h = g // 8
            return h + 9

        def clone_b(y):
            a = y + 1
            b = a * 2
            c = b - 3
            d = c // 4
            e = d + 5
            f = e * 6
            g = f - 7
            h = g // 8
            return h + 9
        """
    )
    (package / "mod.py").write_text(body, encoding="utf-8")
    _git(repo, "add", "benchbox/mod.py")
    _git(repo, "commit", "-m", "base")
    return repo


class TestMaterializeAndScan:
    def test_materialize_source_at_ref(self, tmp_path: Path):
        repo = _init_repo_with_clone(tmp_path)
        sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
        dest = tmp_path / "extract"
        root = dup.materialize_source_at_ref(repo, sha, "benchbox", dest)
        assert root.is_dir()
        assert (root / "mod.py").is_file()

    def test_scan_finds_duplicate_group(self, tmp_path: Path):
        repo = _init_repo_with_clone(tmp_path)
        groups, stats = dup._scan_stats(
            repo / "benchbox",
            min_lines=10,
            exclude_patterns=[],
            ignore_function_names=[],
            ignore_rules=[],
        )
        assert stats["groups"] >= 1
        assert stats["duplicated_lines"] > 0
        assert groups


class TestDeltaCli:
    def test_delta_pass_when_unchanged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
        repo = _init_repo_with_clone(tmp_path)
        monkeypatch.chdir(repo)

        code = dup.main(["--delta-vs", "HEAD", "--source-root", "benchbox", "--min-lines", "10"])
        out = capsys.readouterr().out
        assert code == 0
        assert "PASS" in out
        assert "did not increase" in out

    def test_delta_fail_when_new_clone_added(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
        repo = _init_repo_with_clone(tmp_path)

        extra = textwrap.dedent(
            """\

            def clone_c(z):
                a = z + 1
                b = a * 2
                c = b - 3
                d = c // 4
                e = d + 5
                f = e * 6
                g = f - 7
                h = g // 8
                return h + 9
            """
        )
        mod = repo / "benchbox" / "mod.py"
        mod.write_text(mod.read_text(encoding="utf-8") + extra, encoding="utf-8")
        monkeypatch.chdir(repo)
        code = dup.main(["--delta-vs", "HEAD", "--source-root", "benchbox", "--min-lines", "10"])
        out = capsys.readouterr().out
        assert code == 1
        assert "FAIL" in out
        assert "increased" in out

    def test_delta_report_only_exits_zero_on_increase(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
        repo = _init_repo_with_clone(tmp_path)
        extra = textwrap.dedent(
            """\

            def clone_c(z):
                a = z + 1
                b = a * 2
                c = b - 3
                d = c // 4
                e = d + 5
                f = e * 6
                g = f - 7
                h = g // 8
                return h + 9
            """
        )
        mod = repo / "benchbox" / "mod.py"
        mod.write_text(mod.read_text(encoding="utf-8") + extra, encoding="utf-8")
        monkeypatch.chdir(repo)
        code = dup.main(["--delta-vs", "HEAD", "--source-root", "benchbox", "--min-lines", "10", "--report-only"])
        assert code == 0
        assert "FAIL" in capsys.readouterr().out

    def test_missing_base_ref_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
        repo = _init_repo_with_clone(tmp_path)
        monkeypatch.chdir(repo)
        code = dup.main(["--delta-vs", "refs/does-not-exist", "--source-root", "benchbox"])
        err = capsys.readouterr().err
        assert code == 1
        assert "cannot resolve" in err or "error" in err


