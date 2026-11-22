# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-H specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import atexit
import threading
from pathlib import Path
from typing import Optional, Union

from benchbox.utils.run_deadline import current_run_deadline, run_child
from benchbox.utils.tpc_compilation import CompilationStatus, ensure_tpc_binaries


class QGenBinary:
    def __init__(self) -> None:
        self.qgen_path = self._find_qgen_or_fail()
        self.templates_dir = self._find_templates_dir()
        self._work_dir: Optional[str] = None
        self._work_dir_lock = threading.Lock()

    def _ensure_work_dir(self) -> str:
        if self._work_dir is not None:
            return self._work_dir

        with self._work_dir_lock:
            if self._work_dir is None:
                import shutil
                import tempfile

                work_dir = tempfile.mkdtemp(prefix="benchbox_qgen_")
                dists_src = self.templates_dir / "dists.dss"
                if dists_src.exists():
                    shutil.copy2(dists_src, Path(work_dir) / "dists.dss")
                atexit.register(shutil.rmtree, work_dir, ignore_errors=True)
                self._work_dir = work_dir

        return self._work_dir

    def generate(self, query_id: int, *, seed: Optional[int] = None, scale_factor: float = 1.0) -> str:
        cmd = [self.qgen_path, "-a"]

        if seed is not None:
            cmd.extend(["-r", str(seed)])
        else:
            cmd.append("-d")
        if scale_factor != 1.0:
            cmd.extend(["-s", str(scale_factor)])

        if query_id == 15:
            cmd.append("15a")
            query_dir = "variants"
        else:
            cmd.append(str(query_id))
            query_dir = "queries"

        import os

        env = os.environ.copy()
        env["DSS_QUERY"] = str(self.templates_dir / query_dir)

        work_dir = self._ensure_work_dir()

        result = run_child(
            cmd,
            tool=f"qgen (query {query_id}, scale factor {scale_factor:g})",
            deadline=current_run_deadline(),
            cwd=work_dir,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        return self._clean_sql(result.stdout)

    def _find_qgen_or_fail(self) -> str:
        import logging

        import benchbox

        logger = logging.getLogger(__name__)

        results = ensure_tpc_binaries(["qgen"], auto_compile=True)
        qgen_result = results.get("qgen")

        if (
            qgen_result
            and qgen_result.status
            in [
                CompilationStatus.SUCCESS,
                CompilationStatus.NOT_NEEDED,
                CompilationStatus.PRECOMPILED,
            ]
            and qgen_result.binary_path
            and qgen_result.binary_path.exists()
        ):
            logger.info(f"Using qgen binary: {qgen_result.binary_path}")
            return str(qgen_result.binary_path)

        qgen_path = Path(benchbox.__file__).parent.parent / "_sources/tpc-h/dbgen/qgen"

        if not qgen_path.exists():
            error_msg = f"qgen binary required but not found at {qgen_path}."
            if qgen_result and qgen_result.error_message:
                error_msg += f" Auto-compilation failed: {qgen_result.error_message}"
            error_msg += " TPC-H requires the compiled qgen tool to function."

            raise RuntimeError(error_msg)

        return str(qgen_path)

    def _find_templates_dir(self) -> Path:
        from benchbox.utils.tpc_compilation import get_tpc_templates_dir

        templates_dir = get_tpc_templates_dir("tpc-h")

        queries_dir = templates_dir / "queries"
        variants_dir = templates_dir / "variants"
        dists_file = templates_dir / "dists.dss"

        if not queries_dir.exists():
            raise RuntimeError(f"TPC-H queries directory not found at {queries_dir}")
        if not variants_dir.exists():
            raise RuntimeError(f"TPC-H variants directory not found at {variants_dir}")
        if not dists_file.exists():
            raise RuntimeError(f"TPC-H dists.dss file not found at {dists_file}")

        return templates_dir

    def _extract_rowcount(self, sql: str) -> Optional[int]:
        import re

        if match := re.search(r"where\s+rownum\s*<=\s*(\d+)", sql, re.IGNORECASE):
            return int(match.group(1))

        if match := re.search(r"set\s+rowcount\s+(\d+)", sql, re.IGNORECASE):
            return int(match.group(1))

        if match := re.search(r"\bFIRST\s+(\d+)\b", sql, re.IGNORECASE):
            return int(match.group(1))

        return None

    def _clean_sql(self, sql: str) -> str:
        import re

        rowcount = self._extract_rowcount(sql)

        lines = []
        for line in sql.split("\n"):
            line = line.strip()
            if line and not line.startswith("--") and line.lower() not in ("go", ""):
                if line.lower().startswith("set rowcount") or line.lower().startswith("where rownum"):
                    continue
                lines.append(line)

        cleaned_sql = "\n".join(lines)

        cleaned_sql = re.sub(
            r"interval\s+'([^']+)'\s+(day|month|year)\s*\(\d+\)",
            r"interval '\1' \2",
            cleaned_sql,
        )

        cleaned_sql = re.sub(r"date\s+'([^']+)'\s*-\s*interval", r"date '\1' - interval", cleaned_sql)

        cleaned_sql = re.sub(
            r";\s*where\s+rownum\s*<=\s*-?\d+;?\s*$",
            ";",
            cleaned_sql,
            flags=re.IGNORECASE | re.MULTILINE,
        )

        if rowcount and rowcount > 0:
            cleaned_sql = cleaned_sql.rstrip(";").rstrip()
            cleaned_sql = f"{cleaned_sql}\nLIMIT {rowcount};"

        return cleaned_sql


class TPCHQueries:
    def __init__(self) -> None:
        self.qgen = QGenBinary()

    def get_query(self, query_id: int, *, seed: Optional[int] = None, scale_factor: float = 1.0) -> str:
        if not isinstance(query_id, int):
            raise TypeError(f"query_id must be an integer, got {type(query_id).__name__}")
        if not (1 <= query_id <= 22):
            raise ValueError(f"Query ID must be 1-22, got {query_id}")

        if scale_factor is not None:
            if not isinstance(scale_factor, (int, float)):
                raise TypeError(f"scale_factor must be a number, got {type(scale_factor).__name__}")
            if scale_factor <= 0:
                raise ValueError(f"scale_factor must be positive, got {scale_factor}")

        if seed is not None and not isinstance(seed, int):
            raise TypeError(f"seed must be an integer, got {type(seed).__name__}")

        return self.qgen.generate(query_id, seed=seed, scale_factor=scale_factor)

    def get_all_queries(self, **kwargs: Union[int, float, str]) -> dict[int, str]:
        return {i: self.get_query(i, **kwargs) for i in range(1, 23)}
