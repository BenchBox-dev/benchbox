#!/usr/bin/env python3

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import multiprocessing
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
from benchbox.core.tpcds.c_tools import DSQGenBinary, _substitute_parameters, apply_subscale_template_overrides
from benchbox.core.tpcds.parameter_log import parse_dsqgen_parameter_log
from benchbox.core.tpcds.streams import _parse_dsqgen_stream_log, _split_dsqgen_stream_sql
from benchbox.core.tpch.queries import QGenBinary

SCHEMA_VERSION = 1
SUITES = ("tpch", "tpcds")
CORPUS_DIR = REPO_ROOT / "tests" / "fixtures" / "tpc_query_corpus"
BINARIES_DIR = REPO_ROOT / "benchbox" / "_binaries"
ORACLE_PLATFORM = "linux-x86_64"
SEED_GENERATOR = {
    "algorithm": 'python random.Random(f"{seed}:{label}").sample(range(2, max_seed), count + len(excluded)), excluded seeds dropped, first count kept; label is tpch or tpcds',
    "seed": 20261126,
}
MAX_SEED = 2**31 - 2
TEXT_NORMALISATION = {
    "tpch": "QGenBinary._clean_sql applied to qgen -a stdout",
    "tpcds": "dsqgen stdout from DSQGenBinary._extract_sql_from_output, CRLF and CR replaced by LF, "
    "multi-part a/b selected as BenchBox does",
    "tpcds_stream_template": "statements of the last stream in the dsqgen stdout split on (?<=;)\\n+(?=\\S), "
    "stripped, joined with a blank line, newline normalised",
    "tpcds_stream_all": "each statement of query_<n>.sql split as BenchBox does, newline normalised",
    "tpcds_explicit": "dsqgen stdout for the template rewritten as DSQGenBinary.generate_with_parameters rewrites it, "
    "extracted and normalised as tpcds; t is the sha256 of the rewritten template text (UTF-8)",
}

TPCH_QUERIES = [str(n) for n in range(1, 23)] + ["15a"]
TPCH_NAMED_SEEDS = [0, 1, 42, MAX_SEED]
TPCH_RANDOM_SEED_COUNT = 10_000
TPCH_Q11_SCALES = [0.01, 0.1, 1.0, 3.0, 10.0, 30.0, 100.0, 1000.0]
TPCH_Q11_SEEDS = [None, 0, 1, 42, MAX_SEED]
TPCH_SCALE_SWEEP_SCALES = [0.01, 0.1, 3.0, 10.0, 30.0, 100.0, 1000.0]
TPCH_REPLAY_QUERY_POSITIONS = 23

TPCDS_BASE_IDS = list(range(1, 100))
TPCDS_MULTI_PART_IDS = (14, 23, 24, 39)
TPCDS_VARIANTS = ["5a", "10a", "18a", "22a", "27a", "35a", "36a", "51a", "67a", "70a", "77a", "80a", "86a"]
TPCDS_NAMED_SEEDS = [0, 1, 42, MAX_SEED]
TPCDS_SINGLE_SAMPLED_SEEDS = 100
TPCDS_SCALES = [0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 10.0, 100.0, 1000.0, 3000.0]
TPCDS_ROWCOUNT_IDS = ["1", "9", "16", "27", "33", "34", "36", "43", "44", "46", "68", "73", "27a", "36a"]
TPCDS_SUBSCALE_OVERRIDE_IDS = ["93"]
TPCDS_SCALE_SAMPLED_SEEDS = 10
TPCDS_STREAM_COUNT = 21
TPCDS_STREAM_TEMPLATE_SAMPLED_SEEDS = 5
TPCDS_STREAM_ALL_SAMPLED_SEEDS = 4
VERIFY_STRIDE = 53
ORACLE_TIMEOUT_SECONDS = 60
TPCH_CHUNK = 128
TPCH_RANDOM_SHARD = 4000
TPCDS_STREAM_ALL_SHARD = 100
TPCDS_CHUNK = 16
TIMEOUT_ATTEMPTS = 2
REPEATS = 3
STREAM_TIMEOUT_SECONDS = 900
EXPLICIT_STEM = "tpcds_explicit"
EXPLICIT_CHUNK = 8
QUALIFICATION_VALUES = REPO_ROOT / "benchbox" / "core" / "tpcds" / "dataframe_queries" / "qualification_values.json"
QUALIFICATION_SEED = 1
QUALIFICATION_SCALE = 1.0
QUALIFICATION_ALT_SEED = 99
EQUIVALENCE_DRAWS = [
    (None, 0, 0.01),
    (1, 0, 0.01),
    (42, 0, 0.01),
    (42, 1, 0.01),
    (20260709, 2, 0.01),
    (None, 0, 1.0),
    (None, 0, 10.0),
]
MODE_RUNS = 5
REPLAY_RULE = (
    "An entry is replayable when its bundles record a seed or query parameters. Entries that record neither "
    "are not replayable and are excluded from the retirement gate."
)
EXPLICIT_MECHANISM = {
    "source": "benchbox/core/tpcds/c_tools.py DSQGenBinary.generate_with_parameters and _substitute_parameters",
    "summary": "dsqgen has no option that takes parameter values. BenchBox copies the template into a scratch "
    "work directory, rewrites the copy's text, and runs dsqgen on the copy with the options of a seeded run and no -LOG.",
    "command": "dsqgen -TEMPLATE <template> -DIALECT netezza -SCALE <sf> -RNGSEED <seed> -FILTER Y -VERBOSE N "
    "-INPUT q/templates.lst -DIRECTORY q",
    "name_pattern": "^([A-Za-z_][A-Za-z0-9_]*)\\.(\\d+)$",
    "steps": [
        "Sub-SF1 template overrides (SUBSCALE_TEMPLATE_OVERRIDES, query93.tpl) are applied to the staged template "
        "before the rewrite.",
        "Each key must match name_pattern, the form dsqgen -LOG writes (for example YEAR.01); NAME is group 1 and "
        "POSITION is int(group 2). The value is used as str(value).",
        "Every [NAME.POSITION] in the template text is replaced by the value; when POSITION is 1 every [NAME] is "
        "replaced as well. Replacement is literal text, applied key by key in the order given, so a later key "
        "sees the output of earlier ones.",
        "A key that matches nothing raises ValueError unless the template has a define NAME = statement, in "
        "which case the key is ignored.",
        "define statements stay in the rewritten template and dsqgen still parses them; the substituted names no "
        "longer appear in brackets. Brackets that were not substituted ([_LIMITA], [_END], names absent from the "
        "parameter dict) are evaluated by dsqgen from -RNGSEED and -SCALE.",
        "Queries 14, 23, 24 and 39 use query<N>.tpl for both a and b; the output is split on ';' and part 0 (a) "
        "or 1 (b) is kept.",
        "The text is the dsqgen stdout up to the 'qgen2 Query Generator' or 'Copyright Transaction' banner.",
    ],
}
DIALECT_CALL_SITES = [
    {
        "file": "benchbox/core/tpcds/c_tools.py",
        "role": "the only place a single-query dsqgen command line takes -DIALECT",
        "anchors": ['cmd.extend([f"{_opt}DIALECT", dialect])'],
        "calls": [],
    },
    {
        "file": "benchbox/core/tpcds/streams.py",
        "role": "the only place a -STREAMS dsqgen command line takes -DIALECT; the parameter defaults to netezza",
        "anchors": ['f"{opt}DIALECT",', 'dialect: str = "netezza",'],
        "calls": [],
    },
    {
        "file": "benchbox/core/tpcds/qualification/runner.py",
        "role": "qualification calls omit dialect, so the netezza default applies",
        "anchors": [],
        "calls": [{"callee": "dsqgen.generate_with_parameters", "dialect_keyword": False}],
    },
    {
        "file": "benchbox/core/equivalence/builders/tpcds.py",
        "role": "equivalence builder calls omit dialect, so the netezza default applies",
        "anchors": [],
        "calls": [{"callee": "dsqgen.generate_with_parameters", "dialect_keyword": False}],
    },
    {
        "file": "benchbox/core/tpcds/throughput_test.py",
        "role": "throughput streams call generate_dsqgen_streams without dialect, so the netezza default applies",
        "anchors": [],
        "calls": [{"callee": "generate_dsqgen_streams", "dialect_keyword": False}],
    },
    {
        "file": "benchbox/core/tpcds/dataframe_queries/parameter_adapters.py",
        "role": "parameter binding calls omit dialect, so the netezza default applies",
        "anchors": [],
        "calls": [{"callee": "dsqgen.generate_parameter_log", "dialect_keyword": False}],
    },
    {
        "file": "benchbox/core/tpcds_obt/queries.py",
        "role": "OBT parameter logging omits dialect, so the netezza default applies",
        "anchors": [],
        "calls": [{"callee": "_dsqgen.generate_parameter_log", "dialect_keyword": False}],
    },
    {
        "file": "benchbox/core/tpcds/parameter_extractor.py",
        "role": "parameter extraction omits dialect, so the netezza default applies",
        "anchors": [],
        "calls": [{"callee": "dsqgen.generate", "dialect_keyword": False}],
    },
    {
        "file": "benchbox/core/tpcds/benchmark/runner.py",
        "role": "the benchmark passes src to dsqgen, and src is base_dialect or netezza",
        "anchors": ['src = (base_dialect or "netezza").lower()'],
        "calls": [
            {"callee": "query_manager.dsqgen.generate", "dialect_keyword": True, "dialect_value": "src"},
            {"callee": "query_manager.get_query", "dialect_keyword": True, "dialect_value": "src"},
            {"callee": "query_manager.get_all_queries", "dialect_keyword": True, "dialect_value": "src"},
        ],
    },
    {
        "file": "benchbox/platforms/base/dialect_translation.py",
        "role": "the only get_tpc_base_dialect definition; it returns netezza for every benchmark",
        "anchors": ["def get_tpc_base_dialect"],
        "calls": [],
    },
    {
        "file": "benchbox/platforms/base/execution.py",
        "role": "base_dialect for platform query extraction comes from get_tpc_base_dialect",
        "anchors": ["base = self.get_tpc_base_dialect(bench_family)"],
        "calls": [],
    },
    {
        "file": "benchbox/core/dryrun.py",
        "role": "base_dialect for dry runs comes from get_tpc_base_dialect",
        "anchors": ["base = platform_adapter.get_tpc_base_dialect(benchmark_family(benchmark_id))"],
        "calls": [],
    },
]
DIALECT_ARGUMENT_EXPRESSIONS = {
    "base_dialect": ["base", "base_dialect"],
    "dsqgen_dialect": ["dialect", "src"],
}

_GZIP_LEVEL = 9
_STATEMENT_SPLIT_RE = re.compile(r"(?<=;)\n+(?=\S)")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def normalise_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def text_hash(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def hash_or_none(text: Optional[str]) -> Optional[str]:
    return None if text is None else text_hash(text)


def sample_seeds(label: str, count: int, exclude: Iterable[int]) -> list[int]:
    rng = random.Random(f"{SEED_GENERATOR['seed']}:{label}")
    excluded = set(exclude)
    drawn = rng.sample(range(2, MAX_SEED), count + len(excluded))
    return [seed for seed in drawn if seed not in excluded][:count]


class OracleTimeout(RuntimeError):
    pass


def run_checked(
    cmd: list[str], *, cwd: Path, env: dict[str, str], timeout: int = ORACLE_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str]:
    for attempt in range(TIMEOUT_ATTEMPTS):
        try:
            result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, check=False, timeout=timeout)
            break
        except subprocess.TimeoutExpired as error:
            if attempt == TIMEOUT_ATTEMPTS - 1:
                raise OracleTimeout(f"{' '.join(cmd)} did not finish within {timeout}s") from error
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed ({result.returncode}): {result.stderr.strip()[:400]}")
    return result


class Oracle:
    def __init__(self, scratch: Path) -> None:
        self.scratch = scratch
        self._dirs: dict[str, Path] = {}

    def workdir(self, key: str, factory: Callable[[Path], None]) -> Path:
        if key not in self._dirs:
            path = Path(tempfile.mkdtemp(prefix=f"{key}_", dir=self.scratch))
            factory(path)
            self._dirs[key] = path
        return self._dirs[key]


_PROCESS_ORACLES: dict[str, Any] = {}


def _init_worker(platform: str, scratch: str) -> None:
    _PROCESS_ORACLES["tpch"] = TpchOracle(platform, Path(scratch))
    _PROCESS_ORACLES["tpcds"] = TpcdsOracle(platform, Path(scratch))


def _run_task(task: tuple[str, str, Any]) -> Any:
    suite, method, spec = task
    return getattr(_PROCESS_ORACLES[suite], method)(spec)


class Workers:
    def __init__(self, platform: str, jobs: int) -> None:
        self.scratch = Path(tempfile.mkdtemp(prefix="tpc_corpus_"))
        self.pool = ProcessPoolExecutor(
            max_workers=jobs,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_init_worker,
            initargs=(platform, str(self.scratch)),
        )

    def map(self, suite: str, method: str, specs: list[Any], chunksize: int = 1) -> Iterator[Any]:
        return self.pool.map(_run_task, [(suite, method, spec) for spec in specs], chunksize=chunksize)

    def close(self) -> None:
        self.pool.shutdown()
        shutil.rmtree(self.scratch, ignore_errors=True)


class TpchOracle(Oracle):
    def __init__(self, platform: str, scratch: Path) -> None:
        super().__init__(scratch)
        self.root = BINARIES_DIR / "tpc-h"
        self.qgen = self.root / platform / "qgen"
        self.templates = self.root / "templates"
        self.cleaner = object.__new__(QGenBinary)

    def _stage(self, path: Path) -> None:
        shutil.copy2(self.templates / "dists.dss", path / "dists.dss")

    def _command(self, query: str, seed: Optional[int], scale: float, log: Optional[Path]) -> list[str]:
        cmd = [str(self.qgen), "-a"]
        cmd += ["-r", str(seed)] if seed is not None else ["-d"]
        if scale != 1.0:
            cmd += ["-s", str(scale)]
        if log is not None:
            cmd += ["-l", log.name]
        cmd.append(query)
        return cmd

    def _env(self, query: str) -> dict[str, str]:
        env = os.environ.copy()
        env["DSS_QUERY"] = str(self.templates / ("variants" if query == "15a" else "queries"))
        return env

    def timeout_probe(self, spec: tuple[str, Optional[int], float]) -> dict[str, Any]:
        query, seed, scale = spec
        work = self.workdir("tpch", self._stage)
        log = work / "parameters.log"
        attempts = []
        for _ in range(TIMEOUT_ATTEMPTS):
            log.unlink(missing_ok=True)
            started = time.monotonic()
            try:
                subprocess.run(
                    self._command(query, seed, scale, log),
                    cwd=work,
                    env=self._env(query),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=ORACLE_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                attempts.append({"outcome": "timeout", "seconds": round(time.monotonic() - started, 1)})
                continue
            attempts.append({"outcome": "exit", "seconds": round(time.monotonic() - started, 1)})
            break
        return {"q": query, "seed": seed, "sf": scale, "attempts": attempts}

    def case(self, spec: tuple[str, Optional[int], float, bool]) -> dict[str, Any]:
        query, seed, scale, verify = spec
        work = self.workdir("tpch", self._stage)
        log = work / "parameters.log"
        log.unlink(missing_ok=True)
        try:
            result = run_checked(self._command(query, seed, scale, log), cwd=work, env=self._env(query))
        except OracleTimeout:
            return {"q": query, "seed": seed, "sf": scale, "p": None, "text": None, "s": "timeout"}
        lines = [line for line in log.read_text(encoding="utf-8").splitlines() if line]
        if len(lines) != 1:
            raise RuntimeError(f"qgen log for query {query} seed {seed} scale {scale} has {len(lines)} lines")
        fields = lines[0].split("\t")
        if fields[0] != query.rstrip("a"):
            raise RuntimeError(f"qgen log names query {fields[0]} for {query}")
        if verify:
            plain = run_checked(self._command(query, seed, scale, None), cwd=work, env=self._env(query))
            if plain.stdout != result.stdout:
                raise RuntimeError(f"qgen -l changed stdout for query {query} seed {seed} scale {scale}")
        text = self.cleaner._clean_sql(result.stdout)
        return {"q": query, "seed": seed, "sf": scale, "p": fields[1:], "text": text, "s": None}


class TpcdsOracle(Oracle):
    def __init__(self, platform: str, scratch: Path) -> None:
        super().__init__(scratch)
        self.root = BINARIES_DIR / "tpc-ds"
        self.tools = self.root / platform
        self.dsqgen = self.tools / "dsqgen"
        self.templates = self.root / "templates" / "query_templates"
        self.variants = self.root / "templates" / "query_variants"
        self.extractor = object.__new__(DSQGenBinary)

    def stage(self, path: Path, scale: float) -> None:
        shutil.copytree(self.templates, path / "q")
        shutil.copytree(self.variants, path / "query_variants")
        apply_subscale_template_overrides(path / "q", scale)
        for name in ("tpcds.dst", "tpcds.idx"):
            shutil.copy2(self.tools / name, path / name)

    def staged(self, scale: float) -> Path:
        return self.workdir("sub" if scale < 1 else "full", lambda path: self.stage(path, scale))

    def banner(self) -> str:
        work = self.staged(1.0)
        result = run_checked(
            self.command("query1.tpl", None, 1.0), cwd=work, env=self.env(work), timeout=ORACLE_TIMEOUT_SECONDS
        )
        return result.stderr.splitlines()[0].strip()

    @staticmethod
    def env(work: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["DSS_QUERY"] = str(work)
        return env

    @staticmethod
    def is_multi_part(base: int, variant: Optional[str]) -> bool:
        return base in TPCDS_MULTI_PART_IDS and variant in ("a", "b")

    @staticmethod
    def parse_id(query: str) -> tuple[int, Optional[str]]:
        if query[-1] in "ab":
            return int(query[:-1]), query[-1]
        return int(query), None

    def template_argument(self, base: int, variant: Optional[str], multi_part: bool) -> str:
        name = f"query{base}.tpl" if multi_part else f"query{base}{variant or ''}.tpl"
        if variant and not multi_part and (self.variants / name).exists() or not (self.templates / name).exists():
            return f"../query_variants/{name}"
        return name

    def command(
        self,
        template: str,
        seed: Optional[int],
        scale: float,
        *,
        streams: Optional[int] = None,
        log: Optional[Path] = None,
    ) -> list[str]:
        cmd = [str(self.dsqgen), "-TEMPLATE", template, "-DIALECT", "netezza", "-SCALE", str(scale)]
        if seed is not None:
            cmd += ["-RNGSEED", str(seed)]
        cmd += ["-FILTER", "Y", "-VERBOSE", "N"]
        if streams is not None:
            cmd += ["-STREAMS", str(streams)]
        cmd += ["-INPUT", "q/templates.lst", "-DIRECTORY", "q"]
        if log is not None:
            cmd += ["-LOG", log.name]
        return cmd

    def single(self, spec: tuple[str, Optional[int], float, bool]) -> dict[str, Any]:
        query, seed, scale, verify = spec
        base, variant = self.parse_id(query)
        multi_part = self.is_multi_part(base, variant)
        template = self.template_argument(base, variant, multi_part)
        work = self.staged(scale)
        log = work / "parameters.log"
        log.unlink(missing_ok=True)
        try:
            result = run_checked(self.command(template, seed, scale, log=log), cwd=work, env=self.env(work))
        except OracleTimeout:
            return {"q": query, "seed": seed, "sf": scale, "p": None, "text": None, "s": "timeout"}
        if verify:
            plain = run_checked(self.command(template, seed, scale), cwd=work, env=self.env(work))
            if plain.stdout != result.stdout:
                return {"q": query, "seed": seed, "sf": scale, "p": None, "text": None, "s": "nondeterministic"}
        sql = self.extractor._extract_sql_from_output(result.stdout, base, variant)
        if multi_part:
            sql = self.extractor._select_multi_part(sql, base, variant)
        templates = parse_dsqgen_parameter_log(log.read_text(encoding="utf-8"))[0]
        if len(templates) != 1:
            raise RuntimeError(f"dsqgen log for query {query} has {len(templates)} templates")
        return {
            "q": query,
            "seed": seed,
            "sf": scale,
            "p": dict(templates[0].substitutions),
            "text": normalise_newlines(sql),
            "s": None,
        }

    def stream_template(self, spec: tuple[str, Optional[int], int]) -> dict[str, Any]:
        query, seed, stream = spec
        base, variant = self.parse_id(query)
        template = self.template_argument(base, variant, False)
        work = self.staged(1.0)
        log = work / "parameters.log"
        log.unlink(missing_ok=True)
        cmd = self.command(template, seed, 1.0, streams=stream + 1 if stream else None, log=log)
        try:
            result = run_checked(cmd, cwd=work, env=self.env(work))
        except OracleTimeout:
            return {"q": query, "seed": seed, "stream": stream, "p": None, "text": None, "s": "timeout"}
        templates = parse_dsqgen_parameter_log(log.read_text(encoding="utf-8")).get(stream, [])
        if len(templates) != 1:
            raise RuntimeError(f"dsqgen log for query {query} stream {stream} has {len(templates)} templates")
        statements = [part.strip() for part in _STATEMENT_SPLIT_RE.split(result.stdout.strip()) if part.strip()]
        if len(statements) % (stream + 1):
            raise RuntimeError(f"query {query} stream {stream}: {len(statements)} statements do not split evenly")
        own = statements[len(statements) // (stream + 1) * stream :]
        return {
            "q": query,
            "seed": seed,
            "stream": stream,
            "p": dict(templates[0].substitutions),
            "text": normalise_newlines("\n\n".join(own)),
            "s": None,
        }

    def render(
        self, template: str, base: int, variant: Optional[str], multi_part: bool, seed: Optional[int], scale: float
    ) -> str:
        work = self.staged(scale)
        result = run_checked(self.command(template, seed, scale), cwd=work, env=self.env(work))
        sql = self.extractor._extract_sql_from_output(result.stdout, base, variant)
        if multi_part:
            sql = self.extractor._select_multi_part(sql, base, variant)
        return normalise_newlines(sql)

    def explicit(self, spec: tuple[str, dict[str, str], int, float, Optional[int], bool]) -> dict[str, Any]:
        query, parameters, seed, scale, alternate_seed, compare_seeded = spec
        base, variant = self.parse_id(query)
        multi_part = self.is_multi_part(base, variant)
        template = self.template_argument(base, variant, multi_part)
        source = (self.staged(scale) / "q" / template).resolve()
        rewritten = _substitute_parameters(source.read_text(encoding="utf-8"), parameters, query)
        scratch = source.with_name(f"explicit_{os.getpid()}_{source.name}")
        argument = str(Path(template).with_name(scratch.name))
        case: dict[str, Any] = {
            "q": query,
            "seed": seed,
            "sf": scale,
            "p": dict(parameters),
            "t": text_hash(rewritten),
            "text": None,
            "alt": None,
            "r": None,
            "s": None,
        }
        scratch.write_text(rewritten, encoding="utf-8")
        try:
            case["text"] = self.render(argument, base, variant, multi_part, seed, scale)
            if alternate_seed is not None:
                case["alt"] = text_hash(self.render(argument, base, variant, multi_part, alternate_seed, scale))
        except OracleTimeout:
            return {**case, "text": None, "alt": None, "s": "timeout"}
        finally:
            scratch.unlink(missing_ok=True)
        if compare_seeded:
            case["r"] = hash_or_none(self.single((query, seed, scale, False))["text"])
        return case

    def explicit_repeatable(self, spec: tuple[str, dict[str, str], int, float, Optional[int], bool]) -> dict[str, Any]:
        first = self.explicit(spec)
        if first["s"] is not None or all(self.explicit(spec) == first for _ in range(REPEATS - 1)):
            return first
        return {**first, "text": None, "alt": None, "r": None, "s": "nondeterministic"}

    def mode_runs(self, spec: tuple[str, Optional[int], float, int]) -> dict[str, dict[str, list[Optional[str]]]]:
        query, seed, scale, count = spec
        base, variant = self.parse_id(query)
        multi_part = self.is_multi_part(base, variant)
        template = self.template_argument(base, variant, multi_part)
        work = self.staged(scale)
        log = work / "parameters.log"
        runs: dict[str, dict[str, list[Optional[str]]]] = {"log": {"h": [], "p": []}, "plain": {"h": []}}
        for mode in ("log", "plain"):
            for _ in range(count):
                log.unlink(missing_ok=True)
                result = run_checked(
                    self.command(template, seed, scale, log=log if mode == "log" else None),
                    cwd=work,
                    env=self.env(work),
                )
                sql = self.extractor._extract_sql_from_output(result.stdout, base, variant)
                if multi_part:
                    sql = self.extractor._select_multi_part(sql, base, variant)
                runs[mode]["h"].append(text_hash(normalise_newlines(sql)))
                if mode == "log":
                    values = dict(parse_dsqgen_parameter_log(log.read_text(encoding="utf-8"))[0][0].substitutions)
                    runs["log"]["p"].append(sha256_bytes(dumps(values).encode("utf-8")))
        return runs

    def repeat(self, function: Callable[[Any], dict[str, Any]], spec: Any) -> dict[str, Any]:
        first = function(spec)
        if first["s"] is not None or all(function(spec) == first for _ in range(REPEATS - 1)):
            return first
        return {**first, "p": None, "text": None, "s": "nondeterministic"}

    def single_repeatable(self, spec: tuple[str, Optional[int], float, bool]) -> dict[str, Any]:
        return self.repeat(self.single, spec)

    def stream_template_repeatable(self, spec: tuple[str, Optional[int], int]) -> dict[str, Any]:
        return self.repeat(self.stream_template, spec)

    def stream_all_repeatable(self, spec: tuple[Optional[int], float]) -> list[dict[str, Any]]:
        first = self.stream_all(spec)
        for _ in range(REPEATS - 1):
            for line, again in zip(first, self.stream_all(spec)):
                if line["order"] != again["order"]:
                    raise RuntimeError(f"stream {line['stream']} order differs between runs for {spec}")
                line["h"] = [a if a == b else None for a, b in zip(line["h"], again["h"])]
                line["templates"] = [
                    [name, values if values == other else None]
                    for (name, values), (_, other) in zip(line["templates"], again["templates"])
                ]
        return first

    def stream_all(self, spec: tuple[Optional[int], float]) -> list[dict[str, Any]]:
        seed, scale = spec
        path = Path(tempfile.mkdtemp(prefix="streams_", dir=self.scratch))
        try:
            self.stage(path, scale)
            cmd = [
                str(self.dsqgen),
                "-STREAMS",
                str(TPCDS_STREAM_COUNT),
                "-INPUT",
                "q/templates.lst",
                "-DIRECTORY",
                "q",
                "-SCALE",
                str(scale),
                "-DIALECT",
                "netezza",
                "-OUTPUT_DIR",
                ".",
                "-LOG",
                "stream_params.log",
                "-VERBOSE",
                "Y",
            ]
            if seed is not None:
                cmd += ["-RNGSEED", str(seed)]
            run_checked(cmd, cwd=path, env=self.env(path), timeout=STREAM_TIMEOUT_SECONDS)
            log_text = (path / "stream_params.log").read_text(encoding="utf-8")
            ordering = _parse_dsqgen_stream_log(log_text)
            parameters = parse_dsqgen_parameter_log(log_text)
            lines = []
            for stream in range(TPCDS_STREAM_COUNT):
                statements = _split_dsqgen_stream_sql((path / f"query_{stream}.sql").read_text(encoding="utf-8"))
                order = ordering[stream]
                if len(statements) != len(order):
                    raise RuntimeError(f"stream {stream} seed {seed}: {len(statements)} statements, {len(order)} ids")
                lines.append(
                    {
                        "seed": seed,
                        "sf": scale,
                        "stream": stream,
                        "order": [f"{query_id}{variant or ''}" for query_id, variant in order],
                        "templates": [
                            [f"{entry.query_id}{entry.variant or ''}", dict(entry.substitutions)]
                            for entry in parameters[stream]
                        ],
                        "h": [text_hash(normalise_newlines(statement)) for statement in statements],
                    }
                )
            return lines
        finally:
            shutil.rmtree(path, ignore_errors=True)


def read_corpus_records(corpus_dir: Path, stem: str) -> Iterator[Any]:
    for path in sorted(corpus_dir.glob(f"{stem}-*.jsonl.gz")):
        with gzip.open(path, "rb") as handle:
            for line in handle.read().decode("utf-8").splitlines():
                yield json.loads(line)


def power_parameter_seed(seed: Optional[int], stream_id: int) -> int:
    return (seed or 1) + stream_id + 1000


def qualification_statements() -> list[str]:
    return [f"{n}{part}" for n in TPCDS_BASE_IDS for part in (("a", "b") if n in TPCDS_MULTI_PART_IDS else ("",))]


def equivalence_statements() -> list[str]:
    first = [f"{n}{'a' if n in TPCDS_MULTI_PART_IDS else ''}" for n in TPCDS_BASE_IDS]
    return first + [f"{n}b" for n in TPCDS_MULTI_PART_IDS]


def explicit_record(kind: str, case: dict[str, Any], draw: Optional[list[Any]]) -> dict[str, Any]:
    record: dict[str, Any] = {"set": kind, "q": case["q"], "sf": case["sf"], "seed": case["seed"]}
    if draw is not None:
        record["draw"] = draw
    record["p"] = case["p"]
    record["t"] = case["t"]
    record["h"] = hash_or_none(case["text"])
    if kind == "qualification":
        record["h_alt"] = case["alt"]
    else:
        record["r"] = case["r"]
    if case["s"] is not None:
        record["s"] = case["s"]
    return record


def capture_explicit(workers: Workers, corpus_dir: Path) -> dict[str, Any]:
    document = json.loads(QUALIFICATION_VALUES.read_text(encoding="utf-8"))
    writer = CorpusWriter(corpus_dir, EXPLICIT_STEM)
    statements = qualification_statements()
    specs = [
        (
            query,
            document["values"][str(int(query.rstrip("ab")))],
            QUALIFICATION_SEED,
            QUALIFICATION_SCALE,
            QUALIFICATION_ALT_SEED,
            False,
        )
        for query in statements
    ]
    seed_sensitive = []
    for case in workers.map("tpcds", "explicit_repeatable", specs, EXPLICIT_CHUNK):
        writer.write(explicit_record("qualification", case, None))
        if case["s"] is None and case["alt"] != text_hash(case["text"]):
            seed_sensitive.append(case["q"])
    draws = []
    for seed, stream, scale in EQUIVALENCE_DRAWS:
        rngseed = power_parameter_seed(seed, stream)
        draws.append({"seed": seed, "stream": stream, "rngseed": rngseed, "scale_factor": scale})
        logged = list(
            workers.map(
                "tpcds",
                "single_repeatable",
                [(str(number), rngseed, scale, False) for number in TPCDS_BASE_IDS],
                TPCDS_CHUNK,
            )
        )
        specs = []
        for query in equivalence_statements():
            values = logged[int(query.rstrip("ab")) - 1]
            if values["p"] is None:
                unresolved = {"q": query, "seed": rngseed, "sf": scale, "p": None, "t": None, "text": None, "r": None}
                writer.write(explicit_record("equivalence", {**unresolved, "s": values["s"]}, [seed, stream]))
            else:
                specs.append((query, values["p"], rngseed, scale, None, True))
        for case in workers.map("tpcds", "explicit_repeatable", specs, EXPLICIT_CHUNK):
            writer.write(explicit_record("equivalence", case, [seed, stream]))
    info = writer.close()
    return {
        "files": info,
        "section": {
            "file_stem": EXPLICIT_STEM,
            "mechanism": EXPLICIT_MECHANISM,
            "qualification": {
                "caller": "benchbox/core/tpcds/qualification/runner.py prepare",
                "parameters_file": str(QUALIFICATION_VALUES.relative_to(REPO_ROOT)),
                "parameters_sha256": sha256_file(QUALIFICATION_VALUES),
                "scale_factor": QUALIFICATION_SCALE,
                "seed": QUALIFICATION_SEED,
                "alternate_seed": QUALIFICATION_ALT_SEED,
                "dialect": "netezza",
                "templates": len(TPCDS_BASE_IDS),
                "statements": len(statements),
                "seed_sensitive_statements": seed_sensitive,
            },
            "equivalence": {
                "caller": "benchbox/core/equivalence/builders/tpcds.py build_tpcds_duckdb",
                "rngseed": "(seed or 1) + stream_id + 1000, as power_parameter_seed",
                "parameter_source": "dsqgen -LOG values of template <n> at that rngseed and scale, stream 0",
                "round_trip": "r is the text hash of the seeded dsqgen run at the same rngseed and scale",
                "draws": draws,
                "statements_per_draw": len(equivalence_statements()),
            },
            "text_query_generate_with_parameters": "benchbox/core/tpcds/queries.py TPCDSQueryManager"
            ".generate_with_parameters forwards to DSQGenBinary.generate_with_parameters with the default seed 1, "
            "so its output is the qualification-case mechanism at the scale factor passed",
            "records": sum(shard["records"] for shard in info.values()),
            "abnormal": sum(shard["abnormal"] for shard in info.values()),
        },
    }


def capture_evidence(workers: Workers, corpus_dir: Path) -> dict[str, Any]:
    unstable = sorted(
        {
            (record["q"], -1 if record["seed"] is None else record["seed"], record["sf"])
            for record in read_corpus_records(corpus_dir, "tpcds_single")
            if record.get("s") == "nondeterministic"
        }
    )
    specs = [(query, None if seed == -1 else seed, scale, MODE_RUNS) for query, seed, scale in unstable]
    cases = []
    for (query, seed, scale, _), runs in zip(specs, workers.map("tpcds", "mode_runs", specs)):
        cases.append(
            {
                "q": query,
                "seed": seed,
                "sf": scale,
                "log": {**runs["log"], "distinct_h": len(set(runs["log"]["h"]))},
                "plain": {**runs["plain"], "distinct_h": len(set(runs["plain"]["h"]))},
                "modes_differ": not set(runs["log"]["h"]) & set(runs["plain"]["h"]),
            }
        )
    stuck = sorted(
        {
            (record["q"], record["seed"], record["sf"])
            for record in read_corpus_records(corpus_dir, "tpch_named")
            if record.get("s") == "timeout"
        }
    )
    probes = list(workers.map("tpch", "timeout_probe", stuck))
    return {
        "nondeterministic": {
            "suite": "tpcds",
            "shape": "single",
            "runs_per_mode": MODE_RUNS,
            "finding": "Repeated dsqgen runs in one mode return one text hash; the run that writes -LOG and the "
            "run that does not return different text for these cases, so the nondeterministic status in "
            "tpcds_single means the two invocation modes disagree. BenchBox DSQGenBinary.generate runs without "
            "-LOG and generate_parameter_log runs with it.",
            "cases": cases,
        },
        "nonterminating": {
            "suite": "tpch",
            "timeout_seconds": ORACLE_TIMEOUT_SECONDS,
            "attempts": TIMEOUT_ATTEMPTS,
            "cases": probes,
        },
    }


def replay_class(entry: dict[str, Any]) -> tuple[bool, str]:
    seed, parameters = entry["seed"], entry["query_parameters"]
    if seed is None and parameters is None:
        return False, "no seed and no query parameters recorded; the bundle does not identify its parameter draw"
    if seed is None:
        return True, f"query parameters recorded: {parameters}"
    if entry["qgen_seeds"]:
        return True, f"seed {seed} recorded; the qgen seeds derived from it are listed in qgen_seeds"
    if entry["benchmark"] == "tpcds":
        return True, (
            f"seed {seed} recorded; the power stream of stream_id n draws dsqgen -RNGSEED = seed + n + 1000 "
            "(power_parameter_seed), and a throughput stream n draws from dsqgen -STREAMS at the seed or, per query, "
            "seed + n * 1000 + position"
        )
    return True, f"seed {seed} recorded"


def classify_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    classified = []
    for entry in entries:
        replayable, reason = replay_class(entry)
        rest = {key: value for key, value in entry.items() if key not in ("replayable", "reason", "bundles")}
        classified.append({**rest, "replayable": replayable, "reason": reason, "bundles": entry["bundles"]})
    return classified


def replay_summary(replay_path: Path) -> dict[str, Any]:
    entries = json.loads(replay_path.read_text(encoding="utf-8"))["entries"]
    replayable = [entry for entry in entries if entry["replayable"]]
    excluded = [entry for entry in entries if not entry["replayable"]]
    by_benchmark: dict[str, int] = {}
    for entry in excluded:
        by_benchmark[entry["benchmark"]] = by_benchmark.get(entry["benchmark"], 0) + len(entry["bundles"])
    return {
        "file": replay_path.name,
        "sha256": sha256_file(replay_path),
        "rule": REPLAY_RULE,
        "entries": len(entries),
        "bundles": sum(len(entry["bundles"]) for entry in entries),
        "replayable": {"entries": len(replayable), "bundles": sum(len(entry["bundles"]) for entry in replayable)},
        "not_replayable": {"entries": len(excluded), "bundles": sum(len(entry["bundles"]) for entry in excluded)},
        "not_replayable_bundles_by_benchmark": dict(sorted(by_benchmark.items())),
    }


def bundled_dialects() -> list[str]:
    templates = BINARIES_DIR / "tpc-ds" / "templates" / "query_templates"
    return sorted(path.stem for path in templates.glob("*.tpl") if not path.stem.startswith("query"))


def extend(args: argparse.Namespace) -> None:
    corpus_dir = Path(args.output)
    replay_path = corpus_dir / "replay.json"
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    replay["entries"] = classify_entries(replay["entries"])
    replay_path.write_text(json.dumps(replay, indent=1) + "\n", encoding="utf-8")
    workers = Workers(args.platform, args.jobs)
    try:
        explicit = capture_explicit(workers, corpus_dir)
        evidence = capture_evidence(workers, corpus_dir)
    finally:
        workers.close()
    manifest_path = corpus_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["text_normalisation"]["tpcds_explicit"] = TEXT_NORMALISATION["tpcds_explicit"]
    tpcds = manifest["tpcds"]
    tpcds["files"].update(explicit["files"])
    tpcds["explicit_parameters"] = explicit["section"]
    tpcds["dialects"] = {
        "captured": ["netezza"],
        "passed_by_benchbox": ["netezza"],
        "accepted_by_dsqgen_templates": bundled_dialects(),
        "unsupported_dialect_behavior": "DSQGenBinary._validate_dialect warns and falls back to netezza",
        "reason": "every call path in the repository reaches dsqgen with netezza; the other bundled dialect "
        "templates are never selected, so they are not captured",
        "call_sites": DIALECT_CALL_SITES,
        "argument_expressions": DIALECT_ARGUMENT_EXPRESSIONS,
        "public_api": "TPCDS.get_queries and TPCDS.get_query forward a caller-supplied base_dialect to dsqgen; no "
        "call in the repository passes a string literal, only the get_tpc_base_dialect result or a forwarded "
        "parameter",
    }
    tpcds["nondeterministic_cases"] = evidence["nondeterministic"]
    manifest["tpch"]["nonterminating_cases"] = evidence["nonterminating"]
    manifest["replay"] = replay_summary(replay_path)
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")


def dumps(record: Any) -> str:
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False)


def case_record(case: dict[str, Any], *keys: str) -> dict[str, Any]:
    record = {key: case[key] for key in keys}
    record["p"] = case["p"]
    record["h"] = hash_or_none(case["text"])
    if case["s"] is not None:
        record["s"] = case["s"]
    return record


def abnormal_count(record: dict[str, Any]) -> int:
    hashes = record.get("h")
    if isinstance(hashes, list):
        return sum(digest is None for digest in hashes)
    return int("s" in record)


class CorpusWriter:
    def __init__(self, directory: Path, stem: str, shard_records: Optional[int] = None) -> None:
        self.directory = directory
        self.stem = stem
        self.shard_records = shard_records
        self.shards: dict[str, dict[str, Any]] = {}
        self._open: Optional[tuple[str, gzip.GzipFile, Any]] = None
        self._sha = hashlib.sha256()
        self._records = 0
        self._abnormal = 0

    def write(self, record: Any) -> None:
        if self._open is not None and self.shard_records and self._records == self.shard_records:
            self._finish_shard()
        if self._open is None:
            name = f"{self.stem}-{len(self.shards):02d}.jsonl.gz"
            handle = (self.directory / name).open("wb")
            raw = gzip.GzipFile(filename="", mode="wb", fileobj=handle, compresslevel=_GZIP_LEVEL, mtime=0)
            self._open = (name, raw, handle)
            self._sha = hashlib.sha256()
            self._records = 0
            self._abnormal = 0
        data = (dumps(record) + "\n").encode("utf-8")
        self._sha.update(data)
        self._open[1].write(data)
        self._records += 1
        self._abnormal += abnormal_count(record)

    def _finish_shard(self) -> None:
        assert self._open is not None
        name, raw, handle = self._open
        raw.close()
        handle.close()
        self.shards[name] = {"sha256": self._sha.hexdigest(), "records": self._records, "abnormal": self._abnormal}
        self._open = None

    def close(self) -> dict[str, dict[str, Any]]:
        if self._open is not None:
            self._finish_shard()
        return self.shards


def qgen_banner(platform: str) -> str:
    result = subprocess.run(
        [str(BINARIES_DIR / "tpc-h" / platform / "qgen"), "-h"], capture_output=True, text=True, check=False
    )
    return (result.stdout or result.stderr).splitlines()[0].strip()


def tpch_oracle_manifest(platform: str, banner: str) -> dict[str, Any]:
    root = BINARIES_DIR / "tpc-h"
    qgen = root / platform / "qgen"
    templates = {
        str(path.relative_to(root / "templates")): sha256_file(path)
        for path in sorted((root / "templates").rglob("*"))
        if path.is_file()
    }
    return {
        "binary": {"path": f"tpc-h/{platform}/qgen", "sha256": sha256_file(qgen)},
        "version": banner,
        "distributions": {f"tpc-h/{platform}/dists.dss": sha256_file(root / platform / "dists.dss")},
        "templates": templates,
    }


def tpcds_oracle_manifest(platform: str, banner: str) -> dict[str, Any]:
    root = BINARIES_DIR / "tpc-ds"
    tools = root / platform
    templates = {
        str(path.relative_to(root / "templates")): sha256_file(path)
        for path in sorted((root / "templates").rglob("*"))
        if path.is_file()
    }
    return {
        "binary": {"path": f"tpc-ds/{platform}/dsqgen", "sha256": sha256_file(tools / "dsqgen")},
        "version": banner,
        "distributions": {
            f"tpc-ds/{platform}/tpcds.dst": sha256_file(tools / "tpcds.dst"),
            f"tpc-ds/{platform}/tpcds.idx": sha256_file(tools / "tpcds.idx"),
        },
        "templates": templates,
    }


def replay_seeds(corpus_dir: Path) -> list[int]:
    replay = json.loads((corpus_dir / "replay.json").read_text(encoding="utf-8"))
    seeds: set[int] = set()
    for entry in replay["entries"]:
        seeds.update(entry["qgen_seeds"])
    return sorted(seeds)


def capture_tpch(workers: Workers, corpus_dir: Path, profile: str, extra_seeds: list[int]) -> dict[str, Any]:
    random_count = TPCH_RANDOM_SEED_COUNT if profile == "full" else 20
    random_seeds = sample_seeds("tpch", random_count, TPCH_NAMED_SEEDS + extra_seeds)
    named_seeds: list[Optional[int]] = [None, *TPCH_NAMED_SEEDS, *extra_seeds]
    specs = [(q, seed, 1.0) for seed in named_seeds for q in TPCH_QUERIES]
    specs += [(q, None, scale) for scale in TPCH_SCALE_SWEEP_SCALES for q in TPCH_QUERIES if q != "11"]
    specs += [
        ("11", seed, scale) for scale in TPCH_Q11_SCALES for seed in TPCH_Q11_SEEDS if (seed, scale) != (None, 1.0)
    ]
    seen: set[tuple[str, Optional[int], float]] = set()
    unique = [spec for spec in specs if not (spec in seen or seen.add(spec))]
    verified = [(q, seed, scale, index % VERIFY_STRIDE == 0) for index, (q, seed, scale) in enumerate(unique)]

    named = CorpusWriter(corpus_dir, "tpch_named")
    for case in workers.map("tpch", "case", verified, TPCH_CHUNK):
        named.write(case_record(case, "q", "seed", "sf"))
    named_info = named.close()

    random_file = CorpusWriter(corpus_dir, "tpch_random", TPCH_RANDOM_SHARD)
    random_specs = [(q, seed, 1.0, seed % VERIFY_STRIDE == 0) for seed in random_seeds for q in TPCH_QUERIES]
    results = workers.map("tpch", "case", random_specs, TPCH_CHUNK)
    for seed in random_seeds:
        digest = hashlib.sha256()
        parameters: dict[str, Optional[list[str]]] = {}
        abnormal: dict[str, str] = {}
        for query in TPCH_QUERIES:
            case = next(results)
            parameters[query] = case["p"]
            if case["s"] is not None:
                abnormal[query] = case["s"]
            digest.update(f"{query}\n{case['text'] or ''}\n".encode())
        record: dict[str, Any] = {"seed": seed, "p": parameters, "h": digest.hexdigest()}
        if abnormal:
            record["s"] = abnormal
        random_file.write(record)
    random_info = random_file.close()
    return tpch_summary(named_info, random_info, random_count, extra_seeds)


def tpch_summary(
    named_info: dict[str, Any], random_info: dict[str, Any], random_count: int, extra_seeds: list[int]
) -> dict[str, Any]:
    return {
        "files": {**named_info, **random_info},
        "queries": TPCH_QUERIES,
        "named_seeds": [None, *TPCH_NAMED_SEEDS],
        "replay_seeds": extra_seeds,
        "random_seed_count": random_count,
        "q11_scales": TPCH_Q11_SCALES,
        "q11_seeds": TPCH_Q11_SEEDS,
        "scale_sweep_scales": TPCH_SCALE_SWEEP_SCALES,
        "random_digest": "sha256 over, for each query in order, '<query>\\n<cleaned text>\\n'",
    }


def propagate_nondeterminism(cases: list[dict[str, Any]], key: Callable[[dict[str, Any]], Any]) -> list[dict[str, Any]]:
    unstable = {key(case) for case in cases if case["s"] == "nondeterministic"}
    return [
        {**case, "p": None, "text": None, "s": "nondeterministic"}
        if case["s"] is None and key(case) in unstable
        else case
        for case in cases
    ]


def propagate_stream_nondeterminism(runs: list[list[dict[str, Any]]]) -> None:
    unstable: set[tuple[float, str]] = set()
    for lines in runs:
        for line in lines:
            unstable.update((line["sf"], name) for name, values in line["templates"] if values is None)
            unstable.update(
                (line["sf"], name.rstrip("ab")) for name, digest in zip(line["order"], line["h"]) if digest is None
            )
    for lines in runs:
        for line in lines:
            scale = line["sf"]
            line["templates"] = [
                [name, None if (scale, name) in unstable else values] for name, values in line["templates"]
            ]
            line["h"] = [
                None if (scale, name.rstrip("ab")) in unstable else digest
                for name, digest in zip(line["order"], line["h"])
            ]


def tpcds_query_ids() -> tuple[list[str], list[str]]:
    base = [str(n) for n in TPCDS_BASE_IDS]
    multi = [f"{n}{part}" for n in TPCDS_MULTI_PART_IDS for part in "ab"]
    single = sorted([*base, *multi, *TPCDS_VARIANTS], key=lambda q: (int(re.match(r"\d+", q).group()), q))
    stream_templates = sorted([*base, *TPCDS_VARIANTS], key=lambda q: (int(re.match(r"\d+", q).group()), q))
    return single, stream_templates


def capture_tpcds(workers: Workers, corpus_dir: Path, profile: str) -> dict[str, Any]:
    full = profile == "full"
    single_ids, stream_ids = tpcds_query_ids()
    sampled = sample_seeds("tpcds", TPCDS_SINGLE_SAMPLED_SEEDS if full else 3, TPCDS_NAMED_SEEDS)
    single_seeds: list[Optional[int]] = [None, *TPCDS_NAMED_SEEDS, *sampled]
    scale_seeds: list[Optional[int]] = [None, 1, 42, *sampled[: TPCDS_SCALE_SAMPLED_SEEDS if full else 1]]
    scale_ids = [*TPCDS_ROWCOUNT_IDS, *TPCDS_SUBSCALE_OVERRIDE_IDS]

    specs = [(q, seed, 1.0) for q in single_ids for seed in single_seeds]
    specs += [
        (q, seed, scale)
        for q in scale_ids
        for scale in TPCDS_SCALES
        if scale != 1.0
        for seed in scale_seeds
        if q not in TPCDS_SUBSCALE_OVERRIDE_IDS or scale < 1.0
    ]
    verified = [(q, seed, scale, index % VERIFY_STRIDE == 0) for index, (q, seed, scale) in enumerate(specs)]
    single = CorpusWriter(corpus_dir, "tpcds_single")
    single_cases = propagate_nondeterminism(
        list(workers.map("tpcds", "single_repeatable", verified, TPCDS_CHUNK)), lambda case: (case["q"], case["sf"])
    )
    for case in single_cases:
        single.write(case_record(case, "q", "seed", "sf"))
    single_info = single.close()

    template_seeds: list[Optional[int]] = [None, 1, 42, *sampled[: TPCDS_STREAM_TEMPLATE_SAMPLED_SEEDS if full else 1]]
    streams = range(TPCDS_STREAM_COUNT if full else 3)
    template_specs = [(q, seed, stream) for q in stream_ids for seed in template_seeds for stream in streams]
    stream_template = CorpusWriter(corpus_dir, "tpcds_stream_template")
    template_cases = propagate_nondeterminism(
        list(workers.map("tpcds", "stream_template_repeatable", template_specs, TPCDS_CHUNK)), lambda case: case["q"]
    )
    for case in template_cases:
        stream_template.write(case_record(case, "q", "seed", "stream"))
    stream_template_info = stream_template.close()

    all_seeds: list[Optional[int]] = [None, 1, 42, *sampled[: TPCDS_STREAM_ALL_SAMPLED_SEEDS if full else 1]]
    all_specs: list[tuple[Optional[int], float]] = [(seed, 1.0) for seed in all_seeds]
    all_specs += [(None, scale) for scale in TPCDS_SCALES if scale != 1.0]
    stream_all = CorpusWriter(corpus_dir, "tpcds_stream_all", TPCDS_STREAM_ALL_SHARD)
    statements = 0
    runs = list(workers.map("tpcds", "stream_all_repeatable", all_specs))
    propagate_stream_nondeterminism(runs)
    for lines in runs:
        for line in lines:
            statements += len(line["h"])
            stream_all.write(line)
    stream_all_info = stream_all.close()
    return {
        "files": {**single_info, **stream_template_info, **stream_all_info},
        "single_queries": single_ids,
        "stream_template_queries": stream_ids,
        "single_seeds": single_seeds,
        "scale_seeds": scale_seeds,
        "scales": TPCDS_SCALES,
        "rowcount_queries": TPCDS_ROWCOUNT_IDS,
        "subscale_override_queries": TPCDS_SUBSCALE_OVERRIDE_IDS,
        "stream_count": TPCDS_STREAM_COUNT,
        "stream_template_seeds": template_seeds,
        "stream_all_seeds": all_seeds,
        "stream_all_scales": [1.0, *[scale for scale in TPCDS_SCALES if scale != 1.0]],
        "stream_all_statements": statements,
        "unreachable_variant_templates": ["query_variants/query14a.tpl"],
    }


def suite_capture(suite: str, args: argparse.Namespace, workers: Workers, corpus_dir: Path) -> dict[str, Any]:
    if suite == "tpch":
        return {
            "oracle": tpch_oracle_manifest(args.platform, qgen_banner(args.platform)),
            **capture_tpch(workers, corpus_dir, args.profile, replay_seeds(corpus_dir)),
        }
    banner = TpcdsOracle(args.platform, workers.scratch).banner()
    return {"oracle": tpcds_oracle_manifest(args.platform, banner), **capture_tpcds(workers, corpus_dir, args.profile)}


def assemble_manifest(corpus_dir: Path, args: argparse.Namespace) -> None:
    parts = {suite: json.loads((corpus_dir / f"{suite}.part.json").read_text(encoding="utf-8")) for suite in SUITES}
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "profile": args.profile,
        "oracle": {"platform": args.platform, **{suite: parts[suite].pop("oracle") for suite in SUITES}},
        "seed_generator": SEED_GENERATOR,
        "max_seed": MAX_SEED,
        "text_normalisation": TEXT_NORMALISATION,
        **parts,
        "replay": replay_summary(corpus_dir / "replay.json"),
    }
    (corpus_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    for suite in SUITES:
        (corpus_dir / f"{suite}.part.json").unlink()


def capture(args: argparse.Namespace) -> None:
    corpus_dir = Path(args.output)
    corpus_dir.mkdir(parents=True, exist_ok=True)
    workers = Workers(args.platform, args.jobs)
    try:
        for suite in args.suites:
            part = suite_capture(suite, args, workers, corpus_dir)
            (corpus_dir / f"{suite}.part.json").write_text(json.dumps(part, indent=1) + "\n", encoding="utf-8")
    finally:
        workers.close()
    if all((corpus_dir / f"{suite}.part.json").exists() for suite in SUITES):
        assemble_manifest(corpus_dir, args)


def replay_entry_seeds(
    benchmark: str, test_type: str, seed: Optional[int], streams: int, first_stream: int
) -> list[int]:
    if benchmark != "tpch" or seed is None:
        return []
    if test_type == "throughput":
        return [
            seed + stream * 1001 + position
            for stream in range(first_stream, first_stream + streams)
            for position in range(TPCH_REPLAY_QUERY_POSITIONS)
        ]
    return [seed + 1000 * stream for stream in range(streams)]


def bundle_documents(ref: str) -> Iterator[tuple[str, dict[str, Any]]]:
    paths = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", ref, "results-data/bundles"],
        cwd=REPO_ROOT,
        capture_output=True,
        encoding="utf-8",
        check=True,
    ).stdout.split()
    for path in paths:
        if not path.endswith(".json") or path.endswith(".manifest.json"):
            continue
        blob = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=REPO_ROOT, capture_output=True, check=True).stdout
        document = json.loads(blob)
        if isinstance(document, dict) and "benchmark" in document and "config" in document:
            yield Path(path).stem, document


def extract_replay(args: argparse.Namespace) -> None:
    entries: dict[str, dict[str, Any]] = {}
    for ref in args.ref:
        for stem, document in bundle_documents(ref):
            benchmark = document["benchmark"]
            if benchmark.get("id") not in ("tpch", "tpcds", "tpcds_obt"):
                continue
            config = document["config"]
            streams = int(document.get("run", {}).get("streams") or 1)
            numbering = document.get("phases", {}).get("throughput_test", {}).get("stream_numbering", {})
            first_stream = int(numbering.get("first_stream_id", 0))
            seed = config.get("seed")
            test_type = benchmark.get("test_type")
            entry = {
                "benchmark": benchmark["id"],
                "scale_factor": benchmark.get("scale_factor"),
                "test_type": test_type,
                "seed": seed,
                "query_parameters": config.get("query_parameters"),
                "streams": streams,
                "first_stream_id": first_stream,
                "qgen_seeds": replay_entry_seeds(benchmark["id"], test_type, seed, streams, first_stream),
            }
            key = dumps(entry)
            entries.setdefault(key, {**entry, "bundles": []})["bundles"].append(stem)
    ordered = []
    for key in sorted(entries):
        entry = entries[key]
        entry["bundles"] = sorted(set(entry["bundles"]))
        ordered.append(entry)
    ordered = classify_entries(ordered)
    document = {
        "schema_version": SCHEMA_VERSION,
        "sources": args.ref,
        "bundle_count": sum(len(entry["bundles"]) for entry in ordered),
        "entries": ordered,
    }
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "replay.json").write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture the TPC-H and TPC-DS query conformance corpus")
    sub = parser.add_subparsers(dest="command", required=True)
    capture_parser = sub.add_parser("capture")
    capture_parser.add_argument("--output", default=str(CORPUS_DIR))
    capture_parser.add_argument("--platform", default=ORACLE_PLATFORM)
    capture_parser.add_argument("--profile", choices=("full", "smoke"), default="full")
    capture_parser.add_argument("--suites", nargs="+", choices=SUITES, default=list(SUITES))
    capture_parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    capture_parser.set_defaults(handler=capture)
    replay_parser = sub.add_parser("replay")
    replay_parser.add_argument("--output", default=str(CORPUS_DIR))
    replay_parser.add_argument("--ref", action="append", default=None)
    replay_parser.set_defaults(handler=extract_replay)
    extend_parser = sub.add_parser("extend")
    extend_parser.add_argument("--output", default=str(CORPUS_DIR))
    extend_parser.add_argument("--platform", default=ORACLE_PLATFORM)
    extend_parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    extend_parser.set_defaults(handler=extend)
    args = parser.parse_args(argv)
    if args.command == "replay" and args.ref is None:
        args.ref = ["HEAD", "origin/published-results"]
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    args.handler(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
