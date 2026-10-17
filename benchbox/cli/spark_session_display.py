from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rich.table import Table


def spark_session_config_table(spark_map: Mapping[str, Any] | None) -> Table | None:
    if not spark_map:
        return None
    table = Table(title="Spark Session Config", show_header=True, header_style="bold blue")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="yellow")
    for key, value in sorted(spark_map.items()):
        table.add_row(str(key), str(value))
    return table
