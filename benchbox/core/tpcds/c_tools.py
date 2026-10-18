# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DS (TPC-DS) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DS specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import re
import subprocess
import sys
import warnings
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Callable, Optional, Union

from benchbox.core.tpcds.parameter_log import TemplateParameters, parse_dsqgen_parameter_log
from benchbox.utils.tpc_compilation import (
    CompilationStatus,
    ensure_tpc_binaries,
    get_tpc_compiler,
)


def _option_prefix() -> str:
    return "/" if sys.platform == "win32" else "-"


def tpcds_option(name: str) -> str:
    return f"{_option_prefix()}{name.upper()}"


def _resolve_tpcds_tool_and_template_paths() -> tuple[Path, Path]:
    from benchbox.utils.tpc_compilation import get_tpc_templates_dir

    compiler = get_tpc_compiler(auto_compile=False)

    templates_root = get_tpc_templates_dir("tpc-ds")
    templates_path = templates_root / "query_templates"

    if compiler.precompiled_base:
        platform_str = compiler._get_platform_string()
        bundle_root = compiler.precompiled_base / "tpc-ds" / platform_str
        if bundle_root.exists():
            return bundle_root, templates_path

    import benchbox

    repo_root = Path(benchbox.__file__).parent.parent
    tools_path = repo_root / "_sources/tpc-ds/tools"
    return tools_path, templates_path


_PARAMETER_NAME_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\.(\d+)$")


def _substitute_parameters(template: str, parameters: dict[str, Any], query: str) -> str:
    for key, value in parameters.items():
        name, index = _PARAMETER_NAME_RE.match(key).groups()
        position = int(index)
        text = str(value)
        patterns = [rf"\[{re.escape(name)}\.{position}\]"]
        if position == 1:
            patterns.append(rf"\[{re.escape(name)}\]")
        template, count = re.subn("|".join(patterns), lambda _match, text=text: text, template)
        if count == 0 and not re.search(rf"\bdefine\s+{re.escape(name)}\s*=", template):
            raise ValueError(f"Query {query} has no substitution for parameter {key!r}")
    return template


class TPCDSError(Exception):
    pass


SUBSCALE_TEMPLATE_OVERRIDES: dict[str, tuple[str, str]] = {
    "query93.tpl": (
        "define REASON= dist(return_reasons, 1, 1);",
        'define REASON= distmember(return_reasons, random(1, rowcount("reason"), uniform), 1);',
    ),
}


def apply_subscale_template_overrides(
    query_dir: Path, scale_factor: float, template_names: Optional[Iterable[str]] = None
) -> None:
    if scale_factor >= 1:
        return
    selected = SUBSCALE_TEMPLATE_OVERRIDES.keys() if template_names is None else set(template_names)
    for template_name, (official, override) in SUBSCALE_TEMPLATE_OVERRIDES.items():
        if template_name not in selected:
            continue
        template = query_dir / template_name
        text = template.read_text(encoding="utf-8")
        if text.count(official) != 1:
            raise TPCDSError(
                f"{template_name} must contain the line its sub-SF1 override replaces exactly once: {official}"
            )
        template.write_text(text.replace(official, override, 1), encoding="utf-8")


class TPCDSCTools:
    def __init__(self) -> None:
        self.tools_path, self.templates_path = _resolve_tpcds_tool_and_template_paths()

        self.dsdgen_path = self.tools_path / "dsdgen"
        self.dsqgen_path = self.tools_path / "dsqgen"

        self.query_generator = DSQGenBinary()

    def is_available(self) -> bool:
        return self.tools_path.exists() and self.templates_path.exists() and self.dsqgen_path.exists()

    def generate_query(self, query_id: int, **kwargs: Union[str, int, float]) -> str:
        return self.query_generator.generate(query_id, **kwargs)

    def get_tools_info(self) -> dict[str, Any]:
        dsdgen_results = ensure_tpc_binaries(["dsdgen"])
        dsqgen_results = ensure_tpc_binaries(["dsqgen"])

        dsdgen_status = dsdgen_results.get("dsdgen")
        dsqgen_status = dsqgen_results.get("dsqgen")

        dsdgen_available = dsdgen_status and dsdgen_status.status in [
            CompilationStatus.PRECOMPILED,
            CompilationStatus.SUCCESS,
        ]
        dsqgen_available = dsqgen_status and dsqgen_status.status in [
            CompilationStatus.PRECOMPILED,
            CompilationStatus.SUCCESS,
        ]

        return {
            "tools_path": str(self.tools_path),
            "templates_path": str(self.templates_path),
            "available_tools": [
                {
                    "name": "dsdgen",
                    "path": str(dsdgen_status.binary_path) if dsdgen_available else str(self.dsdgen_path),
                    "exists": dsdgen_available,
                },
                {
                    "name": "dsqgen",
                    "path": str(dsqgen_status.binary_path) if dsqgen_available else str(self.dsqgen_path),
                    "exists": dsqgen_available,
                },
            ],
            "dsqgen_available": dsqgen_available,
            "templates_available": self.templates_path.exists(),
        }

    def get_available_tables(self) -> list[str]:
        from benchbox.core.tpcds.constants import TPCDS_TABLE_NAMES

        return list(TPCDS_TABLE_NAMES)


class DSQGenBinary:
    def __init__(self) -> None:
        tools_path, templates_path = _resolve_tpcds_tool_and_template_paths()
        self.templates_dir = self._find_templates_or_fail(templates_path)
        self.tools_dir = tools_path
        self.dsqgen_path = self._find_dsqgen_or_fail()
        self._parameter_cache = {}
        self._query_cache = {}
        self._supported_dialects = self._detect_supported_dialects()

    def generate(
        self,
        query_id: Union[int, str],
        *,
        seed: Optional[int] = None,
        scale_factor: float = 1.0,
        stream_id: Optional[int] = None,
        dialect: str = "netezza",
    ) -> str:
        try:
            base_query_id, variant = self._parse_query_id(query_id)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid query_id: {e}") from e

        if not (1 <= base_query_id <= 99):
            raise ValueError(f"Query ID must be 1-99, got {base_query_id}")

        dialect = self._validate_dialect(dialect)

        cache_key = self._get_cache_key(base_query_id, variant, seed, scale_factor, stream_id, dialect)
        if cache_key in self._query_cache:
            return self._query_cache[cache_key]

        param_variations = self._generate_parameter_variations(seed, stream_id, scale_factor)
        self._parameter_cache[cache_key] = param_variations

        result = self._generate_with_binary(
            base_query_id,
            variant=variant,
            seed=seed,
            scale_factor=scale_factor,
            stream_id=stream_id,
            dialect=dialect,
        )

        self._query_cache[cache_key] = result
        return result

    def generate_parameter_log(
        self,
        query_id: Union[int, str],
        *,
        seed: Optional[int] = None,
        scale_factor: float = 1.0,
        stream_id: int = 0,
        dialect: str = "netezza",
    ) -> TemplateParameters:
        try:
            base_query_id, variant = self._parse_query_id(query_id)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid query_id: {e}") from e
        if not (1 <= base_query_id <= 99):
            raise ValueError(f"Query ID must be 1-99, got {base_query_id}")
        if stream_id < 0:
            raise ValueError(f"Stream ID must be >= 0, got {stream_id}")

        dialect = self._validate_dialect(dialect)
        is_multi_part = base_query_id in (14, 23, 24, 39) and variant in ("a", "b")
        cmd, opt = self._build_dsqgen_cmd(
            base_query_id,
            variant,
            seed,
            scale_factor,
            dialect,
            is_multi_part,
            streams=stream_id + 1 if stream_id else None,
        )
        try:
            _, log_text = self._run_dsqgen_with_log(
                cmd, opt, base_query_id, variant, scale_factor=scale_factor, capture_log=True
            )
        except subprocess.TimeoutExpired:
            raise TPCDSError(f"dsqgen timed out for query {base_query_id}{variant or ''}") from None
        except FileNotFoundError:
            raise TPCDSError(f"dsqgen binary not found at {self.dsqgen_path}") from None

        try:
            streams = parse_dsqgen_parameter_log(log_text or "")
        except ValueError as e:
            raise TPCDSError(f"Unreadable dsqgen parameter log for query {base_query_id}{variant or ''}: {e}") from e
        templates = streams.get(stream_id, [])
        if len(templates) != 1:
            raise TPCDSError(
                f"dsqgen parameter log for query {base_query_id}{variant or ''} has {len(templates)} templates "
                f"in stream {stream_id}; expected 1"
            )
        return templates[0]

    def _resolve_template_arg(self, query_id: int, variant: Optional[str], is_multi_part: bool) -> str:
        template_name = f"query{query_id}.tpl" if is_multi_part else f"query{query_id}{variant or ''}.tpl"
        template_rel = f"query_templates/{template_name}"
        try:
            variants_dir = self.templates_dir.parent / "query_variants"
            main_path = self.templates_dir / template_name
            alt_path = variants_dir / template_name
            if alt_path.exists() and (variant and not is_multi_part) or (not main_path.exists()):
                template_rel = f"query_variants/{template_name}"
        except Exception:
            template_rel = f"query_templates/{template_name}"

        if "query_variants/" in template_rel:
            return f"../{template_rel}"
        return template_rel.replace("query_templates/", "")

    def _build_dsqgen_cmd(
        self,
        query_id: int,
        variant: Optional[str],
        seed: Optional[int],
        scale_factor: float,
        dialect: str,
        is_multi_part: bool,
        streams: Optional[int] = None,
    ) -> tuple[list[str], str]:
        _opt = _option_prefix()
        template_arg = self._resolve_template_arg(query_id, variant, is_multi_part)

        cmd = [str(self.dsqgen_path)]
        cmd.extend([f"{_opt}TEMPLATE", template_arg])
        cmd.extend([f"{_opt}DIALECT", dialect])
        cmd.extend([f"{_opt}SCALE", str(scale_factor)])
        if seed is not None:
            cmd.extend([f"{_opt}RNGSEED", str(seed)])
        cmd.extend([f"{_opt}FILTER", "Y"])
        cmd.extend([f"{_opt}VERBOSE", "N"])
        if streams is not None:
            cmd.extend([f"{_opt}STREAMS", str(streams)])
        return cmd, _opt

    def _stage_dsqgen_workdir(self, temp_path: Path, scale_factor: float, query_id: int) -> dict[str, str]:
        import os
        import shutil as _shutil

        _shutil.copytree(self.templates_dir, temp_path / "q")
        apply_subscale_template_overrides(temp_path / "q", scale_factor, {f"query{query_id}.tpl"})
        var_src = self.templates_dir.parent / "query_variants"
        if var_src.exists():
            _shutil.copytree(var_src, temp_path / "query_variants")

        env = os.environ.copy()
        env["DSS_QUERY"] = str(temp_path)

        dsqgen_dir = Path(self.dsqgen_path).parent
        for dist_file in ("tpcds.dst", "tpcds.idx"):
            dist_src = dsqgen_dir / dist_file
            if dist_src.exists():
                _shutil.copy2(dist_src, temp_path / dist_file)
            dist_src_alt = self.tools_dir / dist_file
            if not (temp_path / dist_file).exists() and dist_src_alt.exists():
                _shutil.copy2(dist_src_alt, temp_path / dist_file)
        return env

    def _run_dsqgen(
        self, cmd: list[str], opt: str, query_id: int, variant: Optional[str], scale_factor: float
    ) -> subprocess.CompletedProcess:
        return self._run_dsqgen_with_log(cmd, opt, query_id, variant, scale_factor=scale_factor, capture_log=False)[0]

    def _run_dsqgen_with_log(
        self,
        cmd: list[str],
        opt: str,
        query_id: int,
        variant: Optional[str],
        *,
        scale_factor: float,
        capture_log: bool,
        edit_workdir: Optional[Callable[[Path], None]] = None,
    ) -> tuple[subprocess.CompletedProcess, Optional[str]]:
        import tempfile

        log_text: Optional[str] = None
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            env = self._stage_dsqgen_workdir(temp_path, scale_factor, query_id)
            if edit_workdir is not None:
                edit_workdir(temp_path)

            cmd.extend([f"{opt}INPUT", "q/templates.lst"])
            cmd.extend([f"{opt}DIRECTORY", "q"])
            log_path = temp_path / "parameters.log"
            if capture_log:
                cmd.extend([f"{opt}LOG", str(log_path)])

            result = subprocess.run(
                cmd,
                cwd=temp_dir,
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
            if capture_log and result.returncode == 0:
                if not log_path.exists():
                    raise TPCDSError(f"dsqgen wrote no parameter log for query {query_id}{variant or ''}")
                log_text = log_path.read_text(encoding="utf-8")

        if result.returncode != 0:
            error_output = result.stderr.strip() if result.stderr else "Unknown error"
            stdout_output = result.stdout.strip() if result.stdout else ""
            cmd_str = " ".join(cmd)
            raise TPCDSError(
                f"dsqgen failed for query {query_id}{variant or ''} (exit code {result.returncode}): "
                f"Command: {cmd_str}\n"
                f"Stderr: {error_output}\n"
                f"Stdout: {stdout_output}"
            )
        return result, log_text

    def _extract_sql_from_output(self, stdout: str, query_id: int, variant: Optional[str]) -> str:
        sql_output = stdout.strip()
        if not sql_output:
            raise TPCDSError(f"dsqgen returned empty output for query {query_id}{variant or ''}")

        sql_lines: list[str] = []
        for line in sql_output.split("\n"):
            if "qgen2 Query Generator" in line or "Copyright Transaction" in line:
                break
            sql_lines.append(line)

        sql_query = "\n".join(sql_lines).strip()
        if not sql_query:
            raise TPCDSError(f"No SQL query found in dsqgen output for query {query_id}{variant or ''}")
        return sql_query

    def _select_multi_part(self, sql_query: str, query_id: int, variant: Optional[str]) -> str:
        parts = [p.strip() for p in sql_query.split(";") if p.strip()]
        if len(parts) < 2:
            raise TPCDSError(f"Expected 2 queries for multi-part query {query_id}, but found {len(parts)}")

        part_index = 0 if variant == "a" else 1
        if part_index >= len(parts):
            raise TPCDSError(f"Query {query_id}{variant} not found - only {len(parts)} parts available")
        return parts[part_index]

    def _wrap_dsqgen_called_process_error(
        self, exc: subprocess.CalledProcessError, query_id: int, variant: Optional[str]
    ) -> TPCDSError:
        error_msg = exc.stderr.strip() if exc.stderr else "Unknown error"
        error_lower = error_msg.lower()
        q = f"{query_id}{variant or ''}"

        if "File '" in error_msg and "not found" in error_msg:
            return TPCDSError(f"Template not found for query {q}: {error_msg}")
        if "Substitution" in error_msg and "is used before being initialized" in error_msg:
            return TPCDSError(
                f"Template substitution error for query {q}: {error_msg}. "
                "This indicates an issue with the TPC-DS template or dsqgen configuration that needs to be fixed."
            )
        if "template" in error_lower:
            return TPCDSError(f"Template error for query {q}: {error_msg}")
        if "parameter" in error_lower:
            return TPCDSError(f"Parameter generation failed for query {q}: {error_msg}")
        return TPCDSError(f"dsqgen failed for query {q}: {error_msg}")

    def _generate_with_binary(
        self,
        query_id: int,
        *,
        variant: Optional[str] = None,
        seed: Optional[int] = None,
        scale_factor: float = 1.0,
        stream_id: Optional[int] = None,
        dialect: str = "netezza",
    ) -> str:
        is_multi_part = query_id in (14, 23, 24, 39) and variant in ("a", "b")
        cmd, opt = self._build_dsqgen_cmd(query_id, variant, seed, scale_factor, dialect, is_multi_part)

        try:
            result = self._run_dsqgen(cmd, opt, query_id, variant, scale_factor)
            sql_query = self._extract_sql_from_output(result.stdout, query_id, variant)
            if is_multi_part:
                return self._select_multi_part(sql_query, query_id, variant)
            return sql_query
        except subprocess.CalledProcessError as e:
            raise self._wrap_dsqgen_called_process_error(e, query_id, variant) from e
        except subprocess.TimeoutExpired:
            raise TPCDSError(f"dsqgen timed out for query {query_id}{variant or ''} (60s limit exceeded)") from None
        except FileNotFoundError:
            raise TPCDSError(f"dsqgen binary not found at {self.dsqgen_path}") from None

    def _detect_supported_dialects(self) -> set[str]:
        known_dialects = {"ansi", "netezza", "oracle", "db2", "sqlserver"}
        dialects = set()

        for dialect_file in self.templates_dir.glob("*.tpl"):
            dialect_name = dialect_file.stem
            if dialect_name in known_dialects:
                dialects.add(dialect_name)

        dialects.add("ansi")

        return dialects

    def _get_cache_key(
        self,
        query_id: int,
        variant: Optional[str],
        seed: Optional[int],
        scale_factor: float,
        stream_id: Optional[int],
        dialect: str,
    ) -> str:
        return f"{query_id}{variant or ''}_{seed}_{scale_factor}_{stream_id}_{dialect}"

    def _validate_dialect(self, dialect: str) -> str:
        dialect_lower = dialect.lower()

        if dialect_lower not in self._supported_dialects:
            warnings.warn(f"Dialect '{dialect}' not supported, falling back to 'netezza'", stacklevel=2)
            return "netezza"

        return dialect_lower

    def _generate_parameter_variations(
        self, base_seed: Optional[int], stream_id: Optional[int], scale_factor: float
    ) -> dict[str, Union[int, str, float, tuple[float, float]]]:
        if base_seed is None:
            import random
            import time

            base_seed = int(time.time()) % 100000

        effective_seed = base_seed
        if stream_id is not None:
            effective_seed = (base_seed * 7919 + stream_id * 3037) % 2147483647

        import random

        random.seed(effective_seed)

        params = {
            "year": random.choice(range(1998, 2003)),
            "quarter": random.randint(1, 4),
            "month": random.randint(1, 12),
            "day": random.randint(1, 28),
            "state_count": max(1, int(random.randint(1, 10) * (scale_factor**0.5))),
            "brand_count": max(1, int(random.randint(5, 50) * (scale_factor**0.3))),
            "category_count": max(1, int(random.randint(1, 20) * (scale_factor**0.2))),
            "dms_base": random.randint(1176, 1224),
            "price_range": (10.0 * scale_factor, 1000.0 * scale_factor),
            "effective_seed": effective_seed,
        }

        return params

    def _parse_query_id(self, query_id: Union[int, str]) -> tuple[int, Optional[str]]:
        if isinstance(query_id, int):
            return query_id, None

        query_str = str(query_id).strip().lower()

        if query_str[-1] in ("a", "b"):
            try:
                base_id = int(query_str[:-1])
                variant = query_str[-1]
                return base_id, variant
            except ValueError:
                pass

        try:
            return int(query_str), None
        except ValueError:
            raise ValueError(f"Invalid query ID format: {query_id}. Expected int or string like '14a'") from None

    def clear_cache(self) -> None:
        self._parameter_cache.clear()
        self._query_cache.clear()

    def get_parameter_variations(
        self,
        query_id: Union[int, str],
        *,
        seed: Optional[int] = None,
        scale_factor: float = 1.0,
        stream_id: Optional[int] = None,
    ) -> dict[str, Union[int, str, float]]:
        base_query_id, variant = self._parse_query_id(query_id)
        cache_key = self._get_cache_key(base_query_id, variant, seed, scale_factor, stream_id, "netezza")

        if cache_key not in self._parameter_cache:
            param_variations = self._generate_parameter_variations(seed, stream_id, scale_factor)
            self._parameter_cache[cache_key] = param_variations

        return self._parameter_cache[cache_key].copy()

    def get_supported_dialects(self) -> set[str]:
        return self._supported_dialects.copy()

    def get_available_queries(self) -> list[str]:
        queries = []
        for template_file in sorted(self.templates_dir.glob("query*.tpl")):
            query_name = template_file.stem
            if query_name.startswith("query"):
                query_id = query_name[5:]
                if query_id.isdigit() or (len(query_id) > 1 and query_id[:-1].isdigit() and query_id[-1] in "ab"):
                    queries.append(query_id)
        return queries

    def _find_templates_or_fail(self, templates_path: Optional[Path] = None) -> Path:
        if templates_path is None:
            _, templates_path = _resolve_tpcds_tool_and_template_paths()

        if not templates_path.exists():
            raise RuntimeError(
                f"TPC-DS query templates required but not found at {templates_path}. "
                "TPC-DS requires the query template files to function. "
                "Please ensure the TPC-DS templates are properly installed."
            )
        return templates_path

    def _clean_sql(self, sql: str) -> str:
        lines = []
        for line in sql.split("\n"):
            line = line.strip()
            if line and not line.startswith("--") and line.lower() not in ("go", ""):
                if any(
                    line.lower().startswith(cmd)
                    for cmd in [
                        "set rowcount",
                        "set ansi_nulls",
                        "set quoted_identifier",
                        "set arithabort",
                        "set concat_null_yields_null",
                        "set numeric_roundabort",
                        "set ansi_padding",
                        "use ",
                        "\\timing",
                    ]
                ):
                    continue
                lines.append(line)

        cleaned_sql = "\n".join(lines)

        cleaned_sql = re.sub(r"\[_LIMIT[ABC]\]", "", cleaned_sql, flags=re.IGNORECASE)

        cleaned_sql = re.sub(r"\[\w+(?:\.\w+)?\]", "", cleaned_sql)

        cleaned_sql = re.sub(r"\bselect\s+top\s+\d+\b", "select", cleaned_sql, flags=re.IGNORECASE)

        cleaned_sql = re.sub(r"\bdate\s*'\s*([^']+)\s*'", r"date '\1'", cleaned_sql, flags=re.IGNORECASE)

        cleaned_sql = re.sub(
            r"\bdate\s*\(\s*([^)]+)\s*\)\s*\+\s*(\d+)\s+days?\b",
            r"\1 + interval '\2' day",
            cleaned_sql,
            flags=re.IGNORECASE,
        )

        cleaned_sql = re.sub(
            r"\+\s*(\d+)\s+days?\b",
            r"+ interval '\1' day",
            cleaned_sql,
            flags=re.IGNORECASE,
        )
        cleaned_sql = re.sub(
            r"\+\s*(\d+)\s+months?\b",
            r"+ interval '\1' month",
            cleaned_sql,
            flags=re.IGNORECASE,
        )
        cleaned_sql = re.sub(
            r"\+\s*(\d+)\s+years?\b",
            r"+ interval '\1' year",
            cleaned_sql,
            flags=re.IGNORECASE,
        )

        cleaned_sql = re.sub(
            r"interval\s+'([^']+)'\s+(day|month|year)\s*\(\d+\)",
            r"interval '\1' \2",
            cleaned_sql,
            flags=re.IGNORECASE,
        )

        cleaned_sql = re.sub(
            r"\bover\s*\(\s*partition\s+by\b",
            "over (partition by",
            cleaned_sql,
            flags=re.IGNORECASE,
        )
        cleaned_sql = re.sub(
            r"\bover\s*\(\s*order\s+by\b",
            "over (order by",
            cleaned_sql,
            flags=re.IGNORECASE,
        )

        cleaned_sql = re.sub(
            r"\brows?\s+between\s+unbounded\s+preceding\s+and\s+current\s+row\b",
            "rows between unbounded preceding and current row",
            cleaned_sql,
            flags=re.IGNORECASE,
        )

        cleaned_sql = re.sub(
            r"\brank\s*\(\s*\)\s+over\s*\(",
            "rank() over (",
            cleaned_sql,
            flags=re.IGNORECASE,
        )
        cleaned_sql = re.sub(
            r"\bdense_rank\s*\(\s*\)\s+over\s*\(",
            "dense_rank() over (",
            cleaned_sql,
            flags=re.IGNORECASE,
        )

        cleaned_sql = re.sub(
            r"\bgroup\s+by\s+rollup\s*\(",
            "group by rollup(",
            cleaned_sql,
            flags=re.IGNORECASE,
        )
        cleaned_sql = re.sub(
            r"\bgroup\s+by\s+cube\s*\(",
            "group by cube(",
            cleaned_sql,
            flags=re.IGNORECASE,
        )

        cleaned_sql = re.sub(
            r"\bgroup\s+by\s+grouping\s+sets\s*\(",
            "group by grouping sets(",
            cleaned_sql,
            flags=re.IGNORECASE,
        )

        cleaned_sql = re.sub(
            r"\bwith\s+(\w+)\s+as\s*\(",
            r"with \1 as (",
            cleaned_sql,
            flags=re.IGNORECASE,
        )

        cleaned_sql = re.sub(
            r"\bcoalesce\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)",
            r"coalesce(\1, \2)",
            cleaned_sql,
            flags=re.IGNORECASE,
        )

        cleaned_sql = re.sub(r"\bcase\s+when\b", "case when", cleaned_sql, flags=re.IGNORECASE)
        cleaned_sql = re.sub(r"\belse\s+case\b", "else case", cleaned_sql, flags=re.IGNORECASE)
        cleaned_sql = re.sub(r"\bend\s+case\b", "end case", cleaned_sql, flags=re.IGNORECASE)

        cleaned_sql = re.sub(r'"([^"]*)"', r"'\1'", cleaned_sql)

        cleaned_sql = re.sub(r"''([^']*?)''", r"'\1'", cleaned_sql)

        cleaned_sql = cleaned_sql.rstrip(";")

        cleaned_sql = re.sub(r"\n\s*\n", "\n", cleaned_sql)
        cleaned_sql = re.sub(r"[ \t]+", " ", cleaned_sql)
        cleaned_sql = re.sub(r"\n\s+", "\n", cleaned_sql)

        try:
            from benchbox._vendor import sqlglot

            for source_dialect in ["postgres", "mysql", "sqlite", None]:
                try:
                    parsed = sqlglot.parse_one(cleaned_sql, dialect=source_dialect)
                    if parsed:
                        transpiled = parsed.sql(dialect="postgres", pretty=True)
                        if transpiled and len(transpiled.strip()) > 0:
                            cleaned_sql = transpiled
                            break
                except Exception:
                    continue
        except ImportError:
            pass
        except Exception:
            pass

        cleaned_sql = cleaned_sql.strip()

        if cleaned_sql and not cleaned_sql.endswith(";") and not cleaned_sql.endswith(")"):
            if re.match(r"^\s*(with\s+|select\s+)", cleaned_sql, re.IGNORECASE):
                cleaned_sql += ";"

        return cleaned_sql

    def _find_dsqgen_or_fail(self) -> Path:
        import logging

        logger = logging.getLogger(__name__)

        results = ensure_tpc_binaries(["dsqgen"], auto_compile=True)
        dsqgen_result = results.get("dsqgen")

        if (
            dsqgen_result
            and dsqgen_result.status
            in [
                CompilationStatus.SUCCESS,
                CompilationStatus.NOT_NEEDED,
                CompilationStatus.PRECOMPILED,
            ]
            and dsqgen_result.binary_path
            and dsqgen_result.binary_path.exists()
        ):
            logger.info(f"Using dsqgen binary: {dsqgen_result.binary_path}")
            return dsqgen_result.binary_path

        dsqgen_path = self.tools_dir / "dsqgen"

        if dsqgen_path.exists():
            return dsqgen_path

        error_msg = f"dsqgen binary required but not found at {dsqgen_path}."
        if dsqgen_result and dsqgen_result.error_message:
            error_msg += f" Auto-compilation failed: {dsqgen_result.error_message}"
        error_msg += " TPC-DS requires the compiled dsqgen tool to function."

        raise RuntimeError(error_msg)

    def get_query_variations(self, query_id: int) -> list[str]:
        variations = [str(query_id)]

        variants_dir = self.templates_dir.parent / "query_variants"
        if variants_dir.exists():
            for variant_suffix in ["a", "b", "c", "d"]:
                variant_file = variants_dir / f"query{query_id}{variant_suffix}.tpl"
                if variant_file.exists():
                    variations.append(f"{query_id}{variant_suffix}")

        return variations

    def validate_query_id(self, query_id: Union[int, str]) -> bool:
        try:
            base_query_id, variant = self._parse_query_id(query_id)
            if not (1 <= base_query_id <= 99):
                return False

            query_template = self.templates_dir / f"query{base_query_id}.tpl"
            if not query_template.exists():
                return False

            if variant:
                variants_dir = self.templates_dir.parent / "query_variants"
                variant_template = variants_dir / f"query{base_query_id}{variant}.tpl"
                if not variant_template.exists():
                    return False

            return True
        except (ValueError, TypeError):
            return False

    def generate_with_parameters(
        self,
        query_id: Union[int, str],
        parameters: dict[str, Any],
        *,
        scale_factor: float = 1.0,
        dialect: str = "netezza",
        seed: Optional[int] = 1,
    ) -> str:
        try:
            base_query_id, variant = self._parse_query_id(query_id)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid query_id: {e}") from e
        if not (1 <= base_query_id <= 99):
            raise ValueError(f"Query ID must be 1-99, got {base_query_id}")
        for name in parameters:
            if not _PARAMETER_NAME_RE.match(name):
                raise ValueError(f"Parameter name must look like 'YEAR.01' (as dsqgen -LOG writes it), got {name!r}")

        dialect = self._validate_dialect(dialect)
        is_multi_part = base_query_id in (14, 23, 24, 39) and variant in ("a", "b")
        cmd, opt = self._build_dsqgen_cmd(base_query_id, variant, seed, scale_factor, dialect, is_multi_part)
        q = f"{base_query_id}{variant or ''}"

        def substitute(temp_path: Path) -> None:
            template = (temp_path / "q" / self._resolve_template_arg(base_query_id, variant, is_multi_part)).resolve()
            template.write_text(
                _substitute_parameters(template.read_text(encoding="utf-8"), parameters, q), encoding="utf-8"
            )

        try:
            result, _ = self._run_dsqgen_with_log(
                cmd,
                opt,
                base_query_id,
                variant,
                scale_factor=scale_factor,
                capture_log=False,
                edit_workdir=substitute,
            )
        except subprocess.TimeoutExpired:
            raise TPCDSError(f"dsqgen timed out for query {q}") from None
        except FileNotFoundError:
            raise TPCDSError(f"dsqgen binary not found at {self.dsqgen_path}") from None

        sql_query = self._extract_sql_from_output(result.stdout, base_query_id, variant)
        return self._select_multi_part(sql_query, base_query_id, variant) if is_multi_part else sql_query


class TPCDSQueries:
    def __init__(self) -> None:
        self.dsqgen = DSQGenBinary()

    def get_query(
        self,
        query_id: Union[int, str],
        *,
        seed: Optional[int] = None,
        scale_factor: float = 1.0,
        stream_id: Optional[int] = None,
        dialect: str = "netezza",
    ) -> str:
        if isinstance(query_id, int) and not (1 <= query_id <= 99):
            raise ValueError(f"Query ID must be 1-99, got {query_id}")
        return self.dsqgen.generate(
            query_id,
            seed=seed,
            scale_factor=scale_factor,
            stream_id=stream_id,
            dialect=dialect,
        )

    def get_all_queries(self, **kwargs: Union[str, int, float]) -> dict[Union[int, str], str]:
        results = {}

        for i in range(1, 100):
            try:
                results[i] = self.get_query(i, **kwargs)
            except (ValueError, TPCDSError):
                continue

        for query_id in self.dsqgen.get_available_queries():
            if not query_id.isdigit():
                try:
                    results[query_id] = self.get_query(query_id, **kwargs)
                except (ValueError, TPCDSError):
                    continue

        return results

    def get_stream_queries(self, stream_count: int = 1, **kwargs) -> dict[int, dict[Union[int, str], str]]:
        streams = {}

        for stream_id in range(1, stream_count + 1):
            stream_kwargs = kwargs.copy()
            stream_kwargs["stream_id"] = stream_id

            if "seed" in stream_kwargs and stream_kwargs["seed"] is not None:
                stream_kwargs["seed"] = stream_kwargs["seed"] + stream_id

            streams[stream_id] = self.get_all_queries(**stream_kwargs)

        return streams

    def get_supported_dialects(self) -> set[str]:
        return self.dsqgen.get_supported_dialects()

    def get_available_queries(self) -> list[str]:
        return self.dsqgen.get_available_queries()

    def validate_query_id(self, query_id: Union[int, str]) -> bool:
        return self.dsqgen.validate_query_id(query_id)

    def clear_cache(self) -> None:
        self.dsqgen.clear_cache()
