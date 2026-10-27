from __future__ import annotations

import ast
import hashlib
import re
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = REPO_ROOT / "benchbox"
SCAN_ROOTS = (PACKAGE_ROOT, REPO_ROOT / "scripts", REPO_ROOT / "_project")

VENDOR_PACKAGE = "benchbox._vendor"
VENDORED_SQLGLOT = VENDOR_PACKAGE + ".sqlglot"
FORK_REWRITE_PACKAGE = VENDORED_SQLGLOT + ".benchbox"
ALLOWED_PATHS = ("benchbox/_vendor/", "benchbox/utils/dialect_utils.py")
FORK_REWRITE_ALLOWED_PATHS = ALLOWED_PATHS

KIND_IMPORT = "import"
KIND_CALL = "call"
KIND_GENERATOR = "generator"
KIND_OPTIMIZER = "optimizer"
KIND_DYNAMIC = "dynamic"
ALL_KINDS = frozenset({KIND_IMPORT, KIND_CALL, KIND_GENERATOR, KIND_OPTIMIZER, KIND_DYNAMIC})

CALL_ATTRS_ANY_RECEIVER = frozenset({"transpile", "parse_one"})
CALL_ATTRS_WHEN_CALLED = frozenset({"sql"})
CALL_ATTRS_ON_FORK_MODULE = frozenset({"parse", "tokenize"})
CALL_NAMES_IMPORTED = frozenset({"transpile", "parse_one", "parse", "tokenize"})
GENERATOR_ATTRS_ANY_RECEIVER = frozenset({"generator_class"})
GENERATOR_ATTRS_WHEN_CALLED = frozenset({"generate", "generator"})
GENERATOR_NAMES = frozenset({"Generator", "Dialect", "Dialects", "generator", "generators", "dialects", "transforms"})
OPTIMIZER_NAMES = frozenset({"optimizer", "optimize", "qualify"})
GENERATOR_SEGMENTS = frozenset({"generator", "generators", "dialects", "transforms"})
OPTIMIZER_SEGMENTS = frozenset({"optimizer"})
MODULE_ALIAS_NAMES = frozenset({"exp", "expressions"})
LOADER_NAMES = frozenset({"import_module", "__import__", "spec_from_file_location", "find_spec", "load_module"})
DYNAMIC_MODULE_STRING = re.compile(r"^(benchbox\._vendor(\.[\w]+)*|sqlglot\.benchbox(\.[\w]+)*)$")
DYNAMIC_FRAGMENTS = ("benchbox._vendor", "_vendor.sqlglot", "_vendor/sqlglot", "sqlglot.benchbox")

PARSE_ONLY = "parse_only"
AST_REWRITE = "ast_rewrite"
PIN_IDENTITY = "pin_identity"
AVAILABILITY_GUARD = "availability_guard"
DISPOSITIONS = frozenset({PARSE_ONLY, AST_REWRITE, PIN_IDENTITY, AVAILABILITY_GUARD})


@dataclass(frozen=True)
class Finding:
    kind: str
    fingerprint: str


@dataclass(frozen=True)
class Summary:
    count: int
    digest: str


@dataclass(frozen=True)
class AllowlistEntry:
    count: int
    digest: str
    disposition: str
    reason: str


def _is_vendored_module(module: str) -> bool:
    return module == VENDORED_SQLGLOT or module.startswith(VENDORED_SQLGLOT + ".")


def _is_vendor_path(module: str) -> bool:
    return module == VENDOR_PACKAGE or module.startswith(VENDOR_PACKAGE + ".")


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


def _shape(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_shape(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return f"{_shape(node.func)}()"
    if isinstance(node, ast.Subscript):
        return f"{_shape(node.value)}[]"
    return "?"


def _segment_kinds(module: str) -> set[str]:
    segments = set(module.removeprefix(VENDORED_SQLGLOT).split("."))
    kinds: set[str] = set()
    if segments & GENERATOR_SEGMENTS:
        kinds.add(KIND_GENERATOR)
    if segments & OPTIMIZER_SEGMENTS:
        kinds.add(KIND_OPTIMIZER)
    return kinds


def _name_kinds(name: str) -> set[str]:
    kinds: set[str] = set()
    if name in GENERATOR_NAMES:
        kinds.add(KIND_GENERATOR)
    if name in OPTIMIZER_NAMES:
        kinds.add(KIND_OPTIMIZER)
    return kinds


def _string_loads_fork(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return bool(DYNAMIC_MODULE_STRING.match(node.value)) or node.value.startswith(DYNAMIC_FRAGMENTS)
    if isinstance(node, ast.JoinedStr):
        return any(
            isinstance(part, ast.Constant)
            and isinstance(part.value, str)
            and any(fragment in part.value for fragment in DYNAMIC_FRAGMENTS)
            for part in node.values
        )
    return False


def _call_only_string_loads_fork(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and DYNAMIC_MODULE_STRING.match(node.value):
        return False
    return _string_loads_fork(node)


class _Scanner(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scope: list[str] = []
        self.import_findings: list[Finding] = []
        self.pending: list[tuple[str, str]] = []
        self.module_names: set[str] = set()
        self.imported_call_names: set[str] = set()
        self.uses_fork = False

    def _scope(self) -> str:
        return ".".join(self.scope) or "<module>"

    def _add(self, kind: str, fingerprint: str) -> None:
        self.import_findings.append(Finding(kind, fingerprint))

    def _enter(self, node: ast.AST, name: str) -> None:
        self.scope.append(name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter(node, node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._enter(node, node.name)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if _is_vendor_path(alias.name):
                self.uses_fork = True
                self.module_names.add(alias.asname or alias.name.split(".")[0])
                self._add(KIND_IMPORT, ast.unparse(ast.Import(names=[alias])))
                for kind in sorted(_segment_kinds(alias.name)):
                    self._add(kind, ast.unparse(ast.Import(names=[alias])))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if node.level == 0 and module == "benchbox":
            for alias in node.names:
                if alias.name == "_vendor":
                    self._record_from_import(node, alias, VENDOR_PACKAGE, self.module_names)
        elif node.level == 0 and _is_vendor_path(module):
            for alias in node.names:
                self._record_from_import(node, alias, module, self.module_names)
        self.generic_visit(node)

    def _record_from_import(self, node: ast.ImportFrom, alias: ast.alias, module: str, module_names: set[str]) -> None:
        self.uses_fork = True
        bound = alias.asname or alias.name
        text = ast.unparse(ast.ImportFrom(module=node.module, names=[alias], level=0))
        self._add(KIND_IMPORT, text)
        if alias.name == "*":
            self._add(KIND_GENERATOR, text)
            self._add(KIND_OPTIMIZER, text)
            return
        kinds = _segment_kinds(module)
        if _is_vendored_module(module):
            kinds |= _name_kinds(alias.name)
            kinds |= _segment_kinds(f"{module}.{alias.name}")
            if alias.name in CALL_NAMES_IMPORTED:
                self.imported_call_names.add(bound)
            if alias.name in MODULE_ALIAS_NAMES:
                module_names.add(bound)
        elif module == VENDOR_PACKAGE and alias.name == "sqlglot" or alias.name == "_vendor":
            module_names.add(bound)
        for kind in sorted(kinds):
            self._add(kind, text)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        dotted = _dotted(node)
        if dotted == VENDOR_PACKAGE:
            self.uses_fork = True
            self._add(KIND_IMPORT, f"{self._scope()}:{dotted}")
        if node.attr in CALL_ATTRS_ANY_RECEIVER:
            self.pending.append((KIND_CALL, f"{self._scope()}:{_shape(node)}"))
        elif node.attr in GENERATOR_ATTRS_ANY_RECEIVER:
            self.pending.append((KIND_GENERATOR, f"{self._scope()}:{_shape(node)}"))
        elif isinstance(node.value, ast.Name):
            self.pending.append(("module-attr", f"{self._scope()}:{node.value.id}.{node.attr}"))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.pending.append(("name", f"{self._scope()}:{node.id}"))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        function_name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
        if isinstance(node.func, ast.Attribute):
            fingerprint = f"{self._scope()}:{_shape(node.func)}"
            if function_name in CALL_ATTRS_WHEN_CALLED:
                self.pending.append((KIND_CALL, fingerprint))
            elif function_name in GENERATOR_ATTRS_WHEN_CALLED:
                self.pending.append((KIND_GENERATOR, fingerprint))
        if function_name in LOADER_NAMES and any(_call_only_string_loads_fork(arg) for arg in node.args):
            self.uses_fork = True
            self._add(KIND_DYNAMIC, f"{self._scope()}:{_shape(node.func)}")
        elif (
            function_name == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            attr = node.args[1].value
            fingerprint = f"{self._scope()}:getattr:{attr}"
            if attr in CALL_ATTRS_ANY_RECEIVER | CALL_ATTRS_WHEN_CALLED | CALL_ATTRS_ON_FORK_MODULE:
                self.pending.append((KIND_CALL, fingerprint))
            elif attr in GENERATOR_ATTRS_ANY_RECEIVER | GENERATOR_ATTRS_WHEN_CALLED or attr in GENERATOR_NAMES:
                self.pending.append((KIND_GENERATOR, fingerprint))
            elif attr in OPTIMIZER_NAMES:
                self.pending.append((KIND_OPTIMIZER, fingerprint))
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and DYNAMIC_MODULE_STRING.match(node.value):
            self.uses_fork = True
            self._add(KIND_DYNAMIC, f"{self._scope()}:{node.value}")


def scan_source(source: str) -> list[Finding]:
    scanner = _Scanner()
    scanner.visit(ast.parse(source))
    if not scanner.uses_fork:
        return []
    findings = list(scanner.import_findings)
    for tag, fingerprint in scanner.pending:
        name = fingerprint.split(":", 1)[1]
        if tag == "name":
            if name in scanner.imported_call_names:
                findings.append(Finding(KIND_CALL, fingerprint))
        elif tag == "module-attr":
            base, attr = name.split(".", 1)
            if base in scanner.module_names:
                if attr in CALL_ATTRS_ON_FORK_MODULE:
                    findings.append(Finding(KIND_CALL, fingerprint))
                for kind in sorted(_name_kinds(attr)):
                    findings.append(Finding(kind, fingerprint))
        else:
            findings.append(Finding(tag, fingerprint))
    return findings


def _fingerprint(finding: Finding) -> str:
    return hashlib.sha256(f"{finding.kind}|{finding.fingerprint}".encode()).hexdigest()


def summarize(findings: list[Finding]) -> dict[str, Summary]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for finding in findings:
        grouped[finding.kind].append(_fingerprint(finding))
    return {
        kind: Summary(len(prints), hashlib.sha256("\n".join(sorted(prints)).encode()).hexdigest()[:16])
        for kind, prints in grouped.items()
    }


def count_kind(source: str, kind: str) -> int:
    return sum(finding.kind == kind for finding in scan_source(source))


def _python_files(roots: Sequence[Path], repo_root: Path) -> Iterator[tuple[str, Path]]:
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            yield path.relative_to(repo_root).as_posix(), path


def scan_package(roots: Sequence[Path] = SCAN_ROOTS, repo_root: Path = REPO_ROOT) -> dict[tuple[str, str], Summary]:
    offenders: dict[tuple[str, str], Summary] = {}
    for relative, path in _python_files(roots, repo_root):
        if relative.startswith(ALLOWED_PATHS):
            continue
        for kind, summary in summarize(scan_source(path.read_text(encoding="utf-8"))).items():
            offenders[(relative, kind)] = summary
    return offenders


@dataclass(frozen=True)
class AllowlistDiff:
    new_or_grown: dict[tuple[str, str], tuple[int, int]]
    stale: dict[tuple[str, str], tuple[int, int]]
    swapped: dict[tuple[str, str], tuple[str, str]]

    def __bool__(self) -> bool:
        return bool(self.new_or_grown or self.stale or self.swapped)


def diff_allowlist(
    actual: dict[tuple[str, str], Summary], allowlist: dict[tuple[str, str], AllowlistEntry]
) -> AllowlistDiff:
    new_or_grown: dict[tuple[str, str], tuple[int, int]] = {}
    stale: dict[tuple[str, str], tuple[int, int]] = {}
    swapped: dict[tuple[str, str], tuple[str, str]] = {}
    for key in sorted(set(actual) | set(allowlist)):
        listed = allowlist[key].count if key in allowlist else 0
        found = actual[key].count if key in actual else 0
        if found > listed:
            new_or_grown[key] = (listed, found)
        elif found < listed:
            stale[key] = (listed, found)
        elif allowlist[key].digest != actual[key].digest:
            swapped[key] = (allowlist[key].digest, actual[key].digest)
    return AllowlistDiff(new_or_grown, stale, swapped)


DATAFRAME_PARSE_ONLY = "Parses SQL into an AST to build DataFrame operations; emits no dialect SQL"
EQUIVALENCE_CROSS_SURFACE = "Normalizes SQL and DataFrame surfaces to one AST form to compare them"
EQUIVALENCE_TPCDS = "Splits a script into statements for result equivalence; emits no dialect SQL"
VARIANT_CONTRACTS = "Reads projection names from variant SQL to check result contracts; emits no SQL"
QUALIFICATION_PIN = "Records the bundled fork version and pin identity in qualification output; translates nothing"
RETIREMENT_CHECK = (
    "Parses the SQL that translate_sql_query already produced to classify repro outcomes; emits no dialect SQL"
)
RUNTIME_SYNC = "Names the vendored namespace when syncing the bundled runtime; translates nothing"
SELF_BINDING_TOOL = (
    "Developer analysis that parses DDL and queries into scopes to find self-binding columns; "
    "emits no dialect SQL and ships in no wheel"
)
GENERATOR_FUZZ_PILOT = (
    "Fuzzing pilot that translates through translate_sql_query and only parses the result to check it is "
    "valid SQL; ships in no wheel"
)
UPSTREAM_ML_EVALUATION = (
    "Evaluates upstream PyPI sqlglot as a baseline against machine-learning translators; a deliberately "
    "different translator from the product, never imported by benchbox, ships in no wheel"
)
UPSTREAM_REPRO = (
    "Reproduces behaviour of upstream PyPI sqlglot for bug reports; the upstream translator is the subject, "
    "never imported by benchbox, ships in no wheel"
)
UPSTREAM_JOINORDER_PARSE = (
    "Parses Join Order Benchmark SQL with upstream PyPI sqlglot to validate it at build time; "
    "emits no dialect SQL and ships in no wheel"
)

_UPSTREAM_SQLGLOT_TOOLS: dict[str, str] = {
    "_project/scripts/build_joinorder_data.py": UPSTREAM_JOINORDER_PARSE,
    "_project/sql-translation-ml/corpus.py": UPSTREAM_ML_EVALUATION,
    "_project/sql-translation-ml/evaluate.py": UPSTREAM_ML_EVALUATION,
    "_project/sql-translation-ml/experiment.py": UPSTREAM_ML_EVALUATION,
    "_project/sql-translation-ml/oracle.py": UPSTREAM_ML_EVALUATION,
    "_project/sql-translation-ml/schema_evaluate.py": UPSTREAM_ML_EVALUATION,
    "_project/sql-translation-ml/schema_generator.py": UPSTREAM_ML_EVALUATION,
    "_project/sqlglot-upstream/repros/repro_all.py": UPSTREAM_REPRO,
    "_project/sqlglot-upstream/repros/sqlite_extract.py": UPSTREAM_REPRO,
}

_BYPASS_ENTRIES: dict[tuple[str, str], AllowlistEntry] = {
    ("benchbox/core/equivalence/builders/tpcds.py", KIND_IMPORT): AllowlistEntry(
        2, "3cbc8c0042278b22", PARSE_ONLY, EQUIVALENCE_TPCDS
    ),
    ("benchbox/core/equivalence/builders/tpcds.py", KIND_CALL): AllowlistEntry(
        1, "2d9876fb8833ebff", PARSE_ONLY, EQUIVALENCE_TPCDS
    ),
    ("benchbox/core/equivalence/cross_surface.py", KIND_IMPORT): AllowlistEntry(
        9, "d6af488543b98c2d", PARSE_ONLY, EQUIVALENCE_CROSS_SURFACE
    ),
    ("benchbox/core/equivalence/cross_surface.py", KIND_CALL): AllowlistEntry(
        7, "86388b9449d919b6", PARSE_ONLY, EQUIVALENCE_CROSS_SURFACE
    ),
    ("benchbox/core/joinorder/dataframe_queries.py", KIND_IMPORT): AllowlistEntry(
        2, "fbdc7af8cca0373f", PARSE_ONLY, DATAFRAME_PARSE_ONLY
    ),
    ("benchbox/core/joinorder/dataframe_queries.py", KIND_CALL): AllowlistEntry(
        2, "fb01d63f6c269d12", PARSE_ONLY, DATAFRAME_PARSE_ONLY
    ),
    ("benchbox/core/joinorder_synthetic/dataframe_queries.py", KIND_IMPORT): AllowlistEntry(
        2, "fbdc7af8cca0373f", PARSE_ONLY, DATAFRAME_PARSE_ONLY
    ),
    ("benchbox/core/joinorder_synthetic/dataframe_queries.py", KIND_CALL): AllowlistEntry(
        3, "897827f1a3e91fb4", PARSE_ONLY, DATAFRAME_PARSE_ONLY
    ),
    ("benchbox/core/read_primitives/variant_contracts.py", KIND_IMPORT): AllowlistEntry(
        2, "3cbc8c0042278b22", PARSE_ONLY, VARIANT_CONTRACTS
    ),
    ("benchbox/core/read_primitives/variant_contracts.py", KIND_CALL): AllowlistEntry(
        1, "cc4c21f607084cb0", PARSE_ONLY, VARIANT_CONTRACTS
    ),
    ("benchbox/core/tpcds/qualification/runner.py", KIND_IMPORT): AllowlistEntry(
        2, "67a211d4931f4a34", PIN_IDENTITY, QUALIFICATION_PIN
    ),
    ("scripts/check_sqlglot_repro_retirement.py", KIND_IMPORT): AllowlistEntry(
        1, "e13fca925c9c0f9f", PARSE_ONLY, RETIREMENT_CHECK
    ),
    ("scripts/check_sqlglot_repro_retirement.py", KIND_CALL): AllowlistEntry(
        2, "49551fc000acd53e", PARSE_ONLY, RETIREMENT_CHECK
    ),
    ("scripts/sync_sqlglot_runtime.py", KIND_DYNAMIC): AllowlistEntry(
        1, "528690f76dcd63ce", PIN_IDENTITY, RUNTIME_SYNC
    ),
    ("_project/scripts/detect_self_binding.py", KIND_IMPORT): AllowlistEntry(
        4, "4648306936a3d0a5", PARSE_ONLY, SELF_BINDING_TOOL
    ),
    ("_project/scripts/detect_self_binding.py", KIND_CALL): AllowlistEntry(
        3, "d341cc2ca4370ad1", PARSE_ONLY, SELF_BINDING_TOOL
    ),
    ("_project/scripts/detect_self_binding.py", KIND_OPTIMIZER): AllowlistEntry(
        2, "0c3913e9381dddc0", PARSE_ONLY, SELF_BINDING_TOOL
    ),
    ("_project/sqlglot-upstream/repros/generator.py", KIND_IMPORT): AllowlistEntry(
        2, "8a47d3886b82d5d4", PARSE_ONLY, GENERATOR_FUZZ_PILOT
    ),
    ("_project/sqlglot-upstream/repros/generator.py", KIND_CALL): AllowlistEntry(
        3, "cb0adf22cc55efcd", PARSE_ONLY, GENERATOR_FUZZ_PILOT
    ),
}


def _root_present(path: str) -> bool:
    return not path.startswith("_project/") or (REPO_ROOT / path).is_file()


BYPASS_ALLOWLIST: dict[tuple[str, str], AllowlistEntry] = {
    key: entry for key, entry in _BYPASS_ENTRIES.items() if _root_present(key[0])
}
UPSTREAM_SQLGLOT_ALLOWLIST: dict[str, str] = {
    path: reason for path, reason in _UPSTREAM_SQLGLOT_TOOLS.items() if _root_present(path)
}


def _is_pinned_scope(relative: str) -> bool:
    return not relative.startswith(ALLOWED_PATHS)


def test_allowed_paths_are_exactly_the_wrapper_and_the_vendor_bootstrap():
    assert ALLOWED_PATHS == ("benchbox/_vendor/", "benchbox/utils/dialect_utils.py")
    assert not any("sql_compat" in path for path in ALLOWED_PATHS)


def test_allowlist_never_exempts_allowed_paths_or_sql_compat():
    for path, kind in BYPASS_ALLOWLIST:
        assert _is_pinned_scope(path), path
        assert not path.startswith("benchbox/sql_compat/"), path
        assert kind in ALL_KINDS, (path, kind)


def test_no_new_vendored_sqlglot_bypass_callers():
    diff = diff_allowlist(scan_package(), BYPASS_ALLOWLIST)
    assert not diff.new_or_grown, (
        "Fork use outside benchbox/utils/dialect_utils.py and the vendor bootstrap "
        f"((path, kind): (listed, actual)): {diff.new_or_grown}. "
        "Route through the dialect_utils entry points instead of extending the allowlist."
    )


def test_bypass_allowlist_has_no_stale_entries():
    diff = diff_allowlist(scan_package(), BYPASS_ALLOWLIST)
    assert not diff.stale, (
        f"Allowlist entries exceed actual findings; shrink them ((path, kind): (listed, actual)): {diff.stale}"
    )


def test_bypass_allowlist_digests_match_the_matched_constructs():
    diff = diff_allowlist(scan_package(), BYPASS_ALLOWLIST)
    assert not diff.swapped, (
        "Counts match but the matched constructs changed; review the swap and re-pin the digest "
        f"((path, kind): (listed, actual)): {diff.swapped}"
    )


def test_allowlist_entries_carry_valid_dispositions_and_reasons():
    for (path, _kind), entry in BYPASS_ALLOWLIST.items():
        assert (REPO_ROOT / path).is_file(), path
        assert entry.disposition in DISPOSITIONS, path
        assert entry.reason.strip(), path
        assert entry.count > 0, path
        assert re.fullmatch(r"[0-9a-f]{16}", entry.digest), path


def test_scanner_detects_each_call_form():
    assert count_kind("from benchbox._vendor import sqlglot\nsqlglot.transpile('SELECT 1')\n", KIND_CALL) == 1
    assert count_kind("from benchbox._vendor import sqlglot\nsqlglot.parse_one('SELECT 1').sql()\n", KIND_CALL) == 2
    assert count_kind("from benchbox._vendor.sqlglot import transpile\ntranspile('SELECT 1')\n", KIND_CALL) == 1
    assert count_kind("from benchbox._vendor.sqlglot import parse_one as p\np('SELECT 1')\n", KIND_CALL) == 1
    assert count_kind("import benchbox._vendor.sqlglot as sg\nsg.parse('SELECT 1')\n", KIND_CALL) == 1
    assert count_kind("from benchbox._vendor.sqlglot import exp\nexp.column('a').sql()\n", KIND_CALL) == 1


def test_scanner_detects_aliased_transpile_forms():
    assert count_kind("from benchbox._vendor.sqlglot import transpile as t\nt('SELECT 1')\n", KIND_CALL) == 1
    assert count_kind("from benchbox._vendor.sqlglot import transpile as t\nf = t\n", KIND_CALL) == 1
    assert count_kind("import benchbox._vendor.sqlglot as sg\nsg.transpile('SELECT 1')\n", KIND_CALL) == 1
    assert count_kind("import benchbox._vendor.sqlglot as sg\nf = sg.transpile\n", KIND_CALL) == 1
    assert (
        count_kind("import benchbox._vendor.sqlglot\nbenchbox._vendor.sqlglot.transpile('SELECT 1')\n", KIND_CALL) == 1
    )
    assert count_kind("from benchbox import _vendor\n_vendor.sqlglot.transpile('SELECT 1')\n", KIND_CALL) == 1
    assert count_kind("import benchbox\nbenchbox._vendor.sqlglot.transpile('SELECT 1')\n", KIND_CALL) == 1
    assert count_kind("from benchbox._vendor import sqlglot as sg\nsg.parse('SELECT 1')\n", KIND_CALL) == 1
    assert (
        count_kind("from benchbox._vendor import sqlglot as sg\ngetattr(sg, 'transpile')('SELECT 1')\n", KIND_CALL) == 1
    )
    assert count_kind("from benchbox._vendor import sqlglot as sg\nsg.tokenize('SELECT 1')\n", KIND_CALL) == 1


def test_scanner_detects_dialect_generate_and_generator_use():
    assert (
        count_kind(
            "from benchbox._vendor import sqlglot\nsqlglot.Dialect.get_or_raise('x').generate(e)\n", KIND_GENERATOR
        )
        == 2
    )
    assert count_kind("from benchbox._vendor.sqlglot import exp\nd = exp.Dialect\n", KIND_GENERATOR) == 1
    assert (
        count_kind("from benchbox._vendor.sqlglot import exp\ndialect.generate(exp.column('a'))\n", KIND_GENERATOR) == 1
    )
    assert (
        count_kind("from benchbox._vendor.sqlglot import exp\ndialect.generator(identify=True)\n", KIND_GENERATOR) == 1
    )
    assert count_kind("from benchbox._vendor.sqlglot import exp\ndialect.generator_class\n", KIND_GENERATOR) == 1
    assert count_kind("from benchbox._vendor.sqlglot import Generator\n", KIND_GENERATOR) == 1
    assert count_kind("from benchbox._vendor.sqlglot import Dialect as D\n", KIND_GENERATOR) == 1
    assert count_kind("from benchbox._vendor.sqlglot import Dialects\n", KIND_GENERATOR) == 1
    assert count_kind("from benchbox._vendor.sqlglot.generator import Generator\n", KIND_GENERATOR) == 1
    assert count_kind("from benchbox._vendor.sqlglot.dialects.dialect import Dialect\n", KIND_GENERATOR) == 1
    assert count_kind("from benchbox._vendor.sqlglot import dialects\n", KIND_GENERATOR) == 1
    assert count_kind("import benchbox._vendor.sqlglot.generator as g\n", KIND_GENERATOR) == 1
    assert count_kind("from benchbox._vendor.sqlglot import transforms\n", KIND_GENERATOR) == 1
    assert count_kind("from benchbox._vendor.sqlglot import *\n", KIND_GENERATOR) == 1
    assert count_kind("import benchbox._vendor.sqlglot as sg\nsg.Generator\n", KIND_GENERATOR) == 1
    assert count_kind("import benchbox._vendor.sqlglot as sg\nsg.dialects.dialect\n", KIND_GENERATOR) == 1
    assert count_kind("import benchbox._vendor.sqlglot as sg\ngetattr(sg, 'Generator')\n", KIND_GENERATOR) == 1
    assert count_kind("import benchbox._vendor.sqlglot as sg\ngetattr(d, 'generate')\n", KIND_GENERATOR) == 1


def test_scanner_detects_optimizer_imports():
    assert count_kind("from benchbox._vendor.sqlglot import optimizer\n", KIND_OPTIMIZER) == 1
    assert count_kind("from benchbox._vendor.sqlglot import optimizer as o\n", KIND_OPTIMIZER) == 1
    assert count_kind("from benchbox._vendor.sqlglot.optimizer import optimize\n", KIND_OPTIMIZER) == 1
    assert count_kind("from benchbox._vendor.sqlglot.optimizer.qualify import qualify\n", KIND_OPTIMIZER) == 1
    assert count_kind("import benchbox._vendor.sqlglot.optimizer.scope as s\n", KIND_OPTIMIZER) == 1
    assert count_kind("from benchbox._vendor.sqlglot import optimize\n", KIND_OPTIMIZER) == 1
    assert count_kind("from benchbox._vendor.sqlglot import qualify as q\n", KIND_OPTIMIZER) == 1
    assert count_kind("from benchbox._vendor.sqlglot import *\n", KIND_OPTIMIZER) == 1
    assert count_kind("import benchbox._vendor.sqlglot as sg\nsg.optimizer.optimize(e)\n", KIND_OPTIMIZER) == 1
    assert count_kind("import benchbox._vendor.sqlglot as sg\nsg.optimize(e)\n", KIND_OPTIMIZER) == 1
    assert count_kind("import benchbox._vendor.sqlglot as sg\ngetattr(sg, 'optimizer')\n", KIND_OPTIMIZER) == 1


def test_scanner_detects_dynamic_loading_forms():
    assert count_kind("import importlib\nimportlib.import_module('benchbox._vendor.sqlglot')\n", KIND_DYNAMIC) == 1
    assert (
        count_kind("import importlib\nimportlib.import_module('benchbox._vendor.sqlglot.optimizer')\n", KIND_DYNAMIC)
        >= 1
    )
    assert (
        count_kind(
            "import importlib\nimportlib.import_module(f'benchbox._vendor.sqlglot.benchbox.{n}')\n", KIND_DYNAMIC
        )
        == 1
    )
    assert (
        count_kind(
            "from importlib import import_module as im\nim('benchbox._vendor.sqlglot.generator')\n", KIND_DYNAMIC
        )
        >= 1
    )
    assert count_kind("__import__('benchbox._vendor.sqlglot')\n", KIND_DYNAMIC) >= 1
    assert (
        count_kind(
            "import importlib\nname = 'benchbox._vendor.sqlglot.dialects'\nimportlib.import_module(name)\n",
            KIND_DYNAMIC,
        )
        == 1
    )
    assert count_kind("import importlib\nimportlib.import_module('sqlglot.benchbox.ddl')\n", KIND_DYNAMIC) >= 1
    assert count_kind("import sys\nsys.modules['benchbox._vendor.sqlglot']\n", KIND_DYNAMIC) == 1
    assert (
        count_kind("import importlib.util\nimportlib.util.find_spec('benchbox._vendor.sqlglot')\n", KIND_DYNAMIC) >= 1
    )
    assert count_kind("import importlib\nimportlib.import_module('benchbox._vendor')\n", KIND_DYNAMIC) >= 1


def test_scanner_flags_every_vendor_import_form():
    assert count_kind("import benchbox._vendor.sqlglot\n", KIND_IMPORT) == 1
    assert count_kind("from benchbox._vendor import sqlglot\n", KIND_IMPORT) == 1
    assert count_kind("from benchbox._vendor.sqlglot import exp, parse_one\n", KIND_IMPORT) == 2
    assert count_kind("from benchbox import _vendor\n", KIND_IMPORT) == 1
    assert count_kind("import benchbox\nbenchbox._vendor.sqlglot\n", KIND_IMPORT) == 1
    assert (
        count_kind("from benchbox._vendor import sqlglot\nfrom benchbox._vendor.sqlglot import exp\n", KIND_IMPORT) == 2
    )


def test_scanner_ignores_modules_without_vendored_sqlglot():
    assert scan_source("import sqlglot\nsqlglot.transpile('SELECT 1')\n") == []
    assert scan_source("connection.sql('SELECT 1')\n") == []
    assert scan_source("dialect.generate(expression)\n") == []
    assert scan_source("import importlib\nimportlib.import_module(name)\n") == []
    assert scan_source("raise ImportError('Bundled benchbox._vendor.sqlglot runtime is unavailable')\n") == []
    assert scan_source("from sqlglot import optimizer\nfrom sqlglot.optimizer import optimize\n") == []


def test_new_offender_file_fails_scan(tmp_path: Path):
    package_root = tmp_path / "benchbox"
    package_root.mkdir()
    (package_root / "new_offender.py").write_text(
        "from benchbox._vendor import sqlglot\n\n\ndef translate(query):\n    return sqlglot.transpile(query)[0]\n",
        encoding="utf-8",
    )
    found = scan_package([package_root], tmp_path)
    assert {key: summary.count for key, summary in found.items()} == {
        ("benchbox/new_offender.py", KIND_IMPORT): 1,
        ("benchbox/new_offender.py", KIND_CALL): 1,
    }
    assert diff_allowlist(found, {}).new_or_grown


def test_sql_compat_is_scanned_not_exempt(tmp_path: Path):
    compat_root = tmp_path / "benchbox" / "sql_compat"
    compat_root.mkdir(parents=True)
    (compat_root / "offender.py").write_text(
        "from benchbox._vendor import sqlglot\nsqlglot.transpile('SELECT 1')\n", encoding="utf-8"
    )
    found = scan_package([tmp_path / "benchbox"], tmp_path)
    assert ("benchbox/sql_compat/offender.py", KIND_CALL) in found


def test_vendor_bootstrap_and_wrapper_paths_are_skipped(tmp_path: Path):
    for relative in ("_vendor", "utils"):
        (tmp_path / "benchbox" / relative).mkdir(parents=True)
    source = "from benchbox._vendor import sqlglot\nsqlglot.transpile('SELECT 1')\n"
    (tmp_path / "benchbox" / "_vendor" / "loader.py").write_text(source, encoding="utf-8")
    (tmp_path / "benchbox" / "utils" / "dialect_utils.py").write_text(source, encoding="utf-8")
    (tmp_path / "benchbox" / "utils" / "other.py").write_text(source, encoding="utf-8")
    found = scan_package([tmp_path / "benchbox"], tmp_path)
    assert {path for path, _kind in found} == {"benchbox/utils/other.py"}


def _entry(summary: Summary) -> AllowlistEntry:
    return AllowlistEntry(summary.count, summary.digest, PARSE_ONLY, "synthetic")


def test_allowlist_diff_flags_growth_and_unlisted_entries():
    base = summarize(scan_source("from benchbox._vendor import sqlglot\nsqlglot.parse_one('a')\n"))
    grown = summarize(
        scan_source("from benchbox._vendor import sqlglot\nsqlglot.parse_one('a')\nsqlglot.parse_one('b')\n")
    )
    key = ("benchbox/x.py", KIND_CALL)
    allowlist = {key: _entry(base[KIND_CALL])}
    assert diff_allowlist({key: base[KIND_CALL]}, allowlist) == AllowlistDiff({}, {}, {})
    assert diff_allowlist({key: grown[KIND_CALL]}, allowlist).new_or_grown == {key: (1, 2)}
    assert diff_allowlist({key: base[KIND_CALL], ("benchbox/y.py", KIND_CALL): base[KIND_CALL]}, allowlist).new_or_grown


def test_allowlist_diff_flags_stale_entries_and_missing_files():
    base = summarize(
        scan_source("from benchbox._vendor import sqlglot\nsqlglot.parse_one('a')\nsqlglot.parse_one('b')\n")
    )
    shrunk = summarize(scan_source("from benchbox._vendor import sqlglot\nsqlglot.parse_one('a')\n"))
    key = ("benchbox/x.py", KIND_CALL)
    allowlist = {key: _entry(base[KIND_CALL])}
    assert diff_allowlist({key: shrunk[KIND_CALL]}, allowlist).stale == {key: (2, 1)}
    assert diff_allowlist({}, allowlist).stale == {key: (2, 0)}


def test_allowlist_diff_flags_one_for_one_swaps_in_the_same_file():
    parse_only = summarize(scan_source("from benchbox._vendor import sqlglot\nsqlglot.parse_one('a')\n"))
    swapped = summarize(scan_source("from benchbox._vendor import sqlglot\nsqlglot.transpile('a')\n"))
    assert parse_only[KIND_CALL].count == swapped[KIND_CALL].count
    key = ("benchbox/x.py", KIND_CALL)
    diff = diff_allowlist({key: swapped[KIND_CALL]}, {key: _entry(parse_only[KIND_CALL])})
    assert diff.swapped == {key: (parse_only[KIND_CALL].digest, swapped[KIND_CALL].digest)}


def test_allowlist_diff_flags_trading_one_kind_for_another_in_the_same_file():
    before = summarize(scan_source("from benchbox._vendor import sqlglot\nsqlglot.parse_one('a')\n"))
    after = summarize(scan_source("from benchbox._vendor import sqlglot\nd.generate(e)\n"))
    path = "benchbox/x.py"
    allowlist = {(path, kind): _entry(summary) for kind, summary in before.items()}
    actual = {(path, kind): summary for kind, summary in after.items()}
    diff = diff_allowlist(actual, allowlist)
    assert diff.new_or_grown == {(path, KIND_GENERATOR): (0, 1)}
    assert diff.stale == {(path, KIND_CALL): (1, 0)}


def test_fingerprints_ignore_line_numbers_and_argument_text():
    first = summarize(scan_source("from benchbox._vendor import sqlglot\nsqlglot.parse_one('a')\n"))
    second = summarize(
        scan_source("from benchbox._vendor import sqlglot\n\n\n\nsqlglot.parse_one('totally different')\n")
    )
    assert first == second


def imports_fork_rewrites(source: str) -> bool:
    tree = ast.parse(source)
    scanner = _Scanner()
    scanner.visit(tree)
    fork_names = scanner.module_names | {VENDORED_SQLGLOT}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name == FORK_REWRITE_PACKAGE or alias.name.startswith(FORK_REWRITE_PACKAGE + ".")
                for alias in node.names
            ):
                return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == FORK_REWRITE_PACKAGE or module.startswith(FORK_REWRITE_PACKAGE + "."):
                return True
            if module == VENDORED_SQLGLOT and any(alias.name in ("benchbox", "*") for alias in node.names):
                return True
        elif isinstance(node, ast.Attribute) and node.attr == "benchbox":
            if _dotted(node.value) in fork_names:
                return True
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith((FORK_REWRITE_PACKAGE, "sqlglot.benchbox"))
        ) or (
            isinstance(node, ast.JoinedStr)
            and any(
                isinstance(part, ast.Constant) and isinstance(part.value, str) and FORK_REWRITE_PACKAGE in part.value
                for part in node.values
            )
        ):
            return True
    return False


def scan_fork_rewrite_imports(roots: Sequence[Path] = SCAN_ROOTS, repo_root: Path = REPO_ROOT) -> list[str]:
    return [
        relative
        for relative, path in _python_files(roots, repo_root)
        if not relative.startswith(FORK_REWRITE_ALLOWED_PATHS)
        and imports_fork_rewrites(path.read_text(encoding="utf-8"))
    ]


def test_only_the_wrapper_imports_fork_rewrites():
    offenders = scan_fork_rewrite_imports()
    assert not offenders, (
        f"Fork rewrite functions are imported outside benchbox/utils/dialect_utils.py: {offenders}. "
        "Call apply_rewrite instead."
    )


def test_fork_rewrite_scanner_detects_each_import_form():
    assert imports_fork_rewrites("from benchbox._vendor.sqlglot.benchbox import rewrites\n")
    assert imports_fork_rewrites("from benchbox._vendor.sqlglot.benchbox.rewrites import safe_division\n")
    assert imports_fork_rewrites("import benchbox._vendor.sqlglot.benchbox.compat as c\n")
    assert imports_fork_rewrites("from benchbox._vendor.sqlglot import benchbox\n")
    assert imports_fork_rewrites("from benchbox._vendor.sqlglot import *\n")
    assert imports_fork_rewrites("import benchbox._vendor.sqlglot as sg\nsg.benchbox.rewrites\n")
    assert imports_fork_rewrites("import importlib\nimportlib.import_module('sqlglot.benchbox.rewrites')\n")
    assert imports_fork_rewrites(
        "import importlib\nimportlib.import_module('benchbox._vendor.sqlglot.benchbox.compat')\n"
    )
    assert imports_fork_rewrites(
        "import importlib\nimportlib.import_module(f'benchbox._vendor.sqlglot.benchbox.{n}')\n"
    )
    assert not imports_fork_rewrites("from benchbox._vendor import sqlglot\nsqlglot.transpile('SELECT 1')\n")
    assert not imports_fork_rewrites("from benchbox._vendor.sqlglot import exp\n")


def test_wrapper_is_the_only_allowed_fork_rewrite_module():
    assert FORK_REWRITE_ALLOWED_PATHS == ("benchbox/_vendor/", "benchbox/utils/dialect_utils.py")
    allowed_wrapper = (REPO_ROOT / "benchbox/utils/dialect_utils.py").read_text(encoding="utf-8")
    assert imports_fork_rewrites(allowed_wrapper)


def test_fork_ddl_scanner_detects_each_import_form():
    assert imports_fork_rewrites("from benchbox._vendor.sqlglot.benchbox import ddl\n")
    assert imports_fork_rewrites("from benchbox._vendor.sqlglot.benchbox.ddl import DdlOptions, transpile_ddl\n")
    assert imports_fork_rewrites("from benchbox._vendor.sqlglot.benchbox.layout import TableLayout\n")
    assert imports_fork_rewrites("import benchbox._vendor.sqlglot.benchbox.ddl_engines\n")
    assert imports_fork_rewrites("import importlib\nimportlib.import_module('benchbox._vendor.sqlglot.benchbox.ddl')\n")


def test_wrapper_exposes_translate_ddl_as_the_only_fork_ddl_entry():
    from benchbox.utils import dialect_utils

    assert callable(dialect_utils.translate_ddl)
    offenders = [path for path in scan_fork_rewrite_imports() if path != "benchbox/utils/dialect_utils.py"]
    assert offenders == []


def imports_upstream_sqlglot(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            if any(alias.name == "sqlglot" or alias.name.startswith("sqlglot.") for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            if module == "sqlglot" or module.startswith("sqlglot."):
                return True
    return False


def scan_upstream_sqlglot_imports(roots: Sequence[Path] = SCAN_ROOTS, repo_root: Path = REPO_ROOT) -> set[str]:
    return {
        relative
        for relative, path in _python_files(roots, repo_root)
        if imports_upstream_sqlglot(path.read_text(encoding="utf-8"))
    }


def test_upstream_sqlglot_scanner_detects_each_import_form():
    assert imports_upstream_sqlglot("import sqlglot\n")
    assert imports_upstream_sqlglot("import sqlglot.expressions as e\n")
    assert imports_upstream_sqlglot("from sqlglot import exp\n")
    assert imports_upstream_sqlglot("def f():\n    import sqlglot\n")
    assert not imports_upstream_sqlglot("from benchbox._vendor import sqlglot\n")
    assert not imports_upstream_sqlglot("from benchbox._vendor.sqlglot import exp\n")


def test_upstream_sqlglot_imports_are_exactly_the_allowlisted_project_tools():
    found = scan_upstream_sqlglot_imports()
    assert found == set(UPSTREAM_SQLGLOT_ALLOWLIST), (
        "Upstream PyPI sqlglot is a different translator from the bundled fork. "
        f"New: {sorted(found - set(UPSTREAM_SQLGLOT_ALLOWLIST))}; stale: {sorted(set(UPSTREAM_SQLGLOT_ALLOWLIST) - found)}"
    )


def test_upstream_sqlglot_allowlist_is_confined_to_project_tools_with_reasons():
    for path, reason in UPSTREAM_SQLGLOT_ALLOWLIST.items():
        assert path.startswith("_project/"), path
        assert (REPO_ROOT / path).is_file(), path
        assert reason.strip(), path


def test_scripts_directory_is_scanned_for_fork_bypass(tmp_path: Path):
    scripts_root = tmp_path / "scripts"
    scripts_root.mkdir()
    (scripts_root / "offender.py").write_text(
        "from benchbox._vendor import sqlglot\nsqlglot.transpile('SELECT 1', pretty=True)\n", encoding="utf-8"
    )
    found = scan_package([scripts_root], tmp_path)
    assert ("scripts/offender.py", KIND_CALL) in found
    assert diff_allowlist(found, {}).new_or_grown


def test_default_scan_roots_cover_scripts_and_project_tools():
    assert {root.name for root in SCAN_ROOTS} == {"benchbox", "scripts", "_project"}
