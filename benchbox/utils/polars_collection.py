from __future__ import annotations

from typing import Any


def collect_engine_option(engine: str) -> dict[str, str]:
    return {} if engine == "default" else {"engine": engine}


def collect_frame(frame: Any, engine: str = "default") -> Any:
    return frame.collect(**collect_engine_option(engine))
