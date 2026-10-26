from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]

SCAN_GLOBS = (
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    ".github/actions/**/*.yml",
    ".github/actions/**/*.yaml",
    ".pre-commit-config.yaml",
    "Makefile",
    "Dockerfile*",
    "scripts/**/*.py",
    "scripts/**/*.sh",
    "scripts/**/*.ps1",
    "_project/scripts/**/*.py",
    "_project/scripts/**/*.sh",
)

SQLGLOT = r"(?<![\w./-])sqlglot(?![\w-])"
RUNNER = re.compile(r"\b(?:uv\s+run|uvx|uv\s+tool\s+run)\b")
WITH_SQLGLOT = re.compile(rf"--with(?:\s+|=)\s*['\"]?{SQLGLOT}")
INSTALL_SQLGLOT = re.compile(rf"\b(?:uv\s+pip\s+install|pip\s+install|uv\s+add)\b[^\n;&|]*{SQLGLOT}")
NO_PROJECT = re.compile(r"--no-project\b")
QUOTE_NOISE = re.compile(r"[\"',\[\]]")

VACUOUS_REASON = (
    "benchbox/_vendor/__init__.py appends the bundled SQLGlot wheel to benchbox._vendor.__path__, so every BenchBox "
    "import resolves the vendored copy and an overlaid PyPI sqlglot is never imported by code under test. Compare "
    "raw upstream releases with `uv run --no-project --isolated --with sqlglot==VERSION -- python ...` and no "
    "benchbox import."
)


def _logical_lines(text: str) -> Iterator[tuple[int, str]]:
    pending: list[str] = []
    start = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        if not pending:
            start = number
        stripped = raw.rstrip()
        if stripped.endswith("\\"):
            pending.append(stripped[:-1])
            continue
        pending.append(stripped)
        yield start, " ".join(pending)
        pending = []
    if pending:
        yield start, " ".join(pending)


def find_vacuous_overlays(path: Path, text: str) -> list[str]:
    findings: list[str] = []
    for number, line in _logical_lines(text):
        normalized = QUOTE_NOISE.sub(" ", line) if path.suffix == ".py" else line
        location = f"{path}:{number}"
        if RUNNER.search(normalized) and WITH_SQLGLOT.search(normalized) and not NO_PROJECT.search(normalized):
            findings.append(f"{location}: `--with sqlglot` on a project run")
        elif INSTALL_SQLGLOT.search(normalized):
            findings.append(f"{location}: PyPI sqlglot install")
    return findings


def _scan_files() -> list[Path]:
    files: set[Path] = set()
    for pattern in SCAN_GLOBS:
        files.update(path for path in REPO_ROOT.glob(pattern) if path.is_file())
    return sorted(files)


def test_repo_has_no_vacuous_sqlglot_overlays() -> None:
    findings: list[str] = []
    for path in _scan_files():
        findings.extend(find_vacuous_overlays(path.relative_to(REPO_ROOT), path.read_text(encoding="utf-8")))
    assert not findings, VACUOUS_REASON + "\n" + "\n".join(findings)


def test_scan_covers_workflows_makefile_and_scripts() -> None:
    names = {path.relative_to(REPO_ROOT).as_posix() for path in _scan_files()}
    assert "Makefile" in names
    assert any(name.startswith(".github/workflows/") for name in names)
    assert any(name.startswith("scripts/") for name in names)


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("Makefile", "check:\n\tuv run --with sqlglot==26.0.0 -- python -m pytest\n"),
        ("Makefile", "check:\n\tuv run --with 'sqlglot>=25,<27' python x.py\n"),
        ("Makefile", "check:\n\tuv run --with sqlglot python x.py\n"),
        ("Makefile", "check:\n\tuv run --with=sqlglot==25.6.0 python x.py\n"),
        ("Makefile", "check:\n\tuv run --extra dev \\\n\t\t--with sqlglot==26.0.0 python x.py\n"),
        ("ci.yml", "      - run: uv pip install sqlglot==26.0.0\n"),
        ("ci.yml", "      - run: pip install 'sqlglot>=25'\n"),
        ("ci.yml", "      - run: uv add sqlglot\n"),
        ("tool.py", 'subprocess.run(["uv", "run", "--with", "sqlglot==26.0.0", "python", "x.py"])\n'),
        ("tool.sh", "uvx --with sqlglot==26.0.0 pytest\n"),
    ],
)
def test_guard_flags_overlay(name: str, text: str) -> None:
    assert find_vacuous_overlays(Path(name), text)


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("Makefile", "check:\n\tuv run --no-project --isolated --with sqlglot==30.18.0 -- python repro.py\n"),
        ("Makefile", "check:\n\tuv run --with duckdb==1.0 python x.py\n"),
        ("ci.yml", "      - run: uv pip install benchbox_sqlglot-30.18.0.1-py3-none-any.whl\n"),
        ("ci.yml", "      - run: uv run -- python scripts/check_sqlglot_repro_retirement.py\n"),
        ("tool.py", 'run(["uv", "run", "--no-project", "--with", "sqlglot==30.6.0", "python", "r.py"])\n'),
    ],
)
def test_guard_allows_isolated_and_unrelated(name: str, text: str) -> None:
    assert find_vacuous_overlays(Path(name), text) == []
