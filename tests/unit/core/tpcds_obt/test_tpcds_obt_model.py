import pytest

from benchbox.core.tpcds.schema.registry import TABLES_BY_NAME
from benchbox.core.tpcds_obt import schema
from benchbox.core.tpcds_obt.lineage import render_lineage_markdown

pytestmark = [pytest.mark.unit, pytest.mark.fast]

NEW_ROLES = {
    "bill_cur_addr",
    "bill_cur_cdemo",
    "bill_cur_hdemo",
    "bill_first_sales_date",
    "bill_first_shipto_date",
    "returning_cur_addr",
    "returning_cur_cdemo",
    "returning_cur_hdemo",
    "return_store",
    "return_call_center",
    "return_catalog_page",
    "return_web_page",
}


def _table_columns(table_name: str) -> set[str]:
    return {column.name for column in TABLES_BY_NAME[table_name].columns}


def _reference_columns(channel: str, reference: str) -> tuple[str, str]:
    alias, column = reference.split(".")
    return schema.get_channels()[channel].aliases[alias], column


def test_fact_sources_name_real_columns_of_the_channel_tables() -> None:
    for fact in schema.get_fact_specs():
        assert set(fact.sources) <= set(schema.CHANNELS), fact.column.name
        for channel, source in fact.sources.items():
            if "." not in source or source.startswith(("'", "CASE")):
                continue
            table, column = _reference_columns(channel, source)
            assert column in _table_columns(table), (fact.column.name, channel, source)


def test_role_keys_name_real_columns_of_the_channel_tables() -> None:
    for role in schema.get_dimension_roles():
        if role.via_role is not None:
            via = next(candidate for candidate in schema.get_dimension_roles() if candidate.name == role.via_role)
            assert role.via_column in _table_columns(via.table.name), role.name
            continue
        for channel, key in (role.keys or {}).items():
            table, column = _reference_columns(channel, key)
            assert column in _table_columns(table), (role.name, channel, key)


def test_roles_join_through_parents_declared_before_them() -> None:
    seen: set[str] = set()
    for role in schema.get_dimension_roles():
        assert role.via_role is None or role.via_role in seen, role.name
        seen.add(role.name)


def test_twelve_new_roles_add_the_full_column_sets() -> None:
    roles = {role.name: role for role in schema.get_dimension_roles()}
    assert set(roles) >= NEW_ROLES
    added = sum(len(roles[name].table.columns) + (2 if roles[name].income_band else 0) for name in NEW_ROLES)
    assert added == 197
    assert len(schema.get_obt_columns()) == 518 + 197


def test_every_new_role_column_is_in_the_create_table_statement() -> None:
    ddl = schema.TPCDS_OBT_TABLE.get_create_table_sql()
    for column in schema.get_obt_columns():
        if column.role in NEW_ROLES:
            assert f"    {column.name} " in ddl


def test_lineage_follows_the_columns_the_etl_reads() -> None:
    lineage = schema.get_column_lineage()
    assert lineage["store_sk"]["source_table"] == "store_sales"
    assert lineage["store_sk"]["source_column"] == "ss_store_sk"
    assert lineage["web_page_sk"]["source_table"] == "web_sales"
    assert lineage["ship_date_sk"]["source_table"] == "web_sales|catalog_sales"
    assert lineage["has_return"]["source_table"] is None
    assert lineage["return_store_s_store_name"] == {
        "source_table": "store",
        "source_column": "s_store_name",
        "role": "return_store",
        "description": "Store that took the return",
    }


def test_minimal_mode_excludes_the_new_roles() -> None:
    assert not NEW_ROLES & {column.role for column in schema.get_obt_columns("minimal")}


def test_lineage_markdown_renders_the_whole_model_deterministically() -> None:
    page = render_lineage_markdown()
    assert page == render_lineage_markdown()
    for fact in schema.get_fact_specs():
        assert f"| `{fact.column.name}` |" in page
    for role in schema.get_dimension_roles():
        assert f"| `{role.name}` | `{role.prefix}` |" in page
    for channel in schema.get_channels().values():
        assert f"| `{channel.name}` | `{channel.sales_table}` |" in page
