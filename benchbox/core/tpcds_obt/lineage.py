from __future__ import annotations

from benchbox.core.tpcds_obt.schema import (
    CHANNELS,
    INCOME_BAND_TABLE,
    OBT_TABLE_NAME,
    get_channels,
    get_coverage,
    get_dimension_roles,
    get_fact_specs,
    get_obt_columns,
)

HEADER = f"""# TPC-DS OBT column lineage

This page is generated from `benchbox/core/tpcds_obt/model.yaml`, the declarative model that drives the ETL, the
`CREATE TABLE` statement and this page. Regenerate it with
`uv run -- python -m benchbox.core.tpcds_obt.lineage > docs/benchmarks/tpc-ds-obt-lineage.md`.

`{OBT_TABLE_NAME}` has {len(get_obt_columns())} columns at `dimension_mode=full`. Each channel contributes the rows of its
sales table, left-joined to the matching row of its returns table on the order or ticket number and the item.
"""


def _cell(value: str | None) -> str:
    return f"`{value}`" if value else "NULL"


def _fact_rows() -> list[str]:
    rows: list[str] = ["| Column | Type | " + " | ".join(channel.title() for channel in CHANNELS) + " |"]
    rows.append("| --- | --- | " + " | ".join("---" for _ in CHANNELS) + " |")
    for fact in get_fact_specs():
        sources = " | ".join(_cell(fact.sources.get(channel)) for channel in CHANNELS)
        rows.append(f"| `{fact.column.name}` | {fact.column.sql_type()} | {sources} |")
    return rows


def _role_rows() -> list[str]:
    rows = ["| Role | Prefix | Source table | Join key | Columns |", "| --- | --- | --- | --- | --- |"]
    for role in get_dimension_roles():
        if role.via_role is not None:
            key = f"`{role.via_role}.{role.via_column}`"
        else:
            key = "<br>".join(f"{channel}: `{role.keys[channel]}`" for channel in CHANNELS if channel in role.keys)
        count = len(role.table.columns) + (2 if role.income_band else 0)
        rows.append(f"| `{role.name}` | `{role.prefix}` | `{role.table.name}` | {key} | {count} |")
    return rows


def render_lineage_markdown() -> str:
    channel_rows = ["| Channel | Sales table | Returns table | Returns join |", "| --- | --- | --- | --- |"]
    for spec in get_channels().values():
        channel_rows.append(
            f"| `{spec.name}` | `{spec.sales_table}` | `{spec.returns_table}` | `{spec.returns_join}` |"
        )
    coverage = get_coverage()
    sections = [
        HEADER.strip(),
        "## Channels\n\n" + "\n".join(channel_rows),
        "## Fact columns\n\nA channel with no source writes NULL.\n\n" + "\n".join(_fact_rows()),
        "## Dimension roles\n\n"
        "Each role adds every column of its source table under the role prefix. Roles marked with an income band "
        f"also add `<prefix>ib_lower_bound` and `<prefix>ib_upper_bound` from `{INCOME_BAND_TABLE}`. A role that joins "
        "through another role reads its key from that role's columns, so it needs no key on the fact row.\n\n"
        + "\n".join(_role_rows()),
        "## Coverage\n\nThe ETL fails when a full-width build at scale factor 1 or above breaks the coverage the "
        f"queries rely on: every calendar day from {coverage.sold_date_from} to {coverage.sold_date_to} has a sale "
        "and every item appears in at least one row.",
    ]
    return "\n\n".join(sections) + "\n"


if __name__ == "__main__":
    print(render_lineage_markdown(), end="")
