from __future__ import annotations

from typing import Any


def datafusion_identifier(name: str) -> str:
    if name == name.lower() or '"' in name or "." in name:
        return name
    return f'"{name}"'


def datafusion_identifiers(names: Any) -> list[Any]:
    return [datafusion_identifier(name) if isinstance(name, str) else name for name in names]


def datafusion_column(name: str) -> Any:
    from datafusion import col

    return col(datafusion_identifier(name))
