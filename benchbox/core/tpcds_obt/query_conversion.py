from __future__ import annotations

import re
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlglot
import yaml
from sqlglot import exp

from benchbox.core.tpcds_obt.schema import OBT_TABLE_NAME, get_column_lineage


def _resolve_template_dir() -> Path:
    from benchbox.utils.tpc_compilation import get_tpc_templates_dir

    return get_tpc_templates_dir("tpc-ds") / "query_templates"


TEMPLATE_DIR = _resolve_template_dir()


def _load_query_specs() -> dict[str, Any]:
    with (Path(__file__).with_name("query_specs.yaml")).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


_QUERY_SPECS = _load_query_specs()

BLOCKED_QUERY_IDS = set(_QUERY_SPECS["blocked_query_ids"])

_PREFIX_TO_TABLE = _QUERY_SPECS["prefix_to_table"]

_DIM_STATIC_ROLE = _QUERY_SPECS["dim_static_role"]
_IDENTIFIER_DEFAULT_FACT_TABLES = _QUERY_SPECS["identifier_default_fact_tables"]
_CHANNEL_FACT_TABLES = _QUERY_SPECS["channel_fact_tables"]
_QUERY_IDENTIFIER_COLUMNS = _QUERY_SPECS["query_identifier_columns"]
_ROLE_PREFIX_MAP = _QUERY_SPECS["role_prefix_map"]


@dataclass(frozen=True)
class TemplateParameter:
    name: str
    kind: str
    indices: tuple[int, ...]

    def token(self, index: int) -> str:
        return f"__BB_PARAM_{self.name}__" if index == 1 else f"__BB_PARAM_{self.name}_{index}__"

    def token_expression(self, index: int) -> str:
        if self.kind == "numeric":
            return str(9_000_000 + int(zlib.crc32(self.token(index).encode()) % 1_000_000))
        return self.token(index)

    def placeholder(self, index: int) -> str:
        return f"[{self.name}]" if index == 1 else f"[{self.name}.{index}]"


@dataclass(frozen=True)
class ConvertedQuery:
    query_id: int
    template_sql: str
    placeholder_sql: str
    parameters: dict[str, TemplateParameter]
    channels: tuple[str, ...]


class ColumnMapper:
    def __init__(self) -> None:
        lineage = get_column_lineage()
        self.fact_map: dict[tuple[str, str], str] = {}
        self.dimension_map: dict[tuple[str, str, str], str] = {}
        self._role_prefix_map = _ROLE_PREFIX_MAP

        self.obt_columns = set(lineage.keys())

        for obt_name, meta in lineage.items():
            source_table = (meta.get("source_table") or "").lower()
            source_column = (meta.get("source_column") or "").lower()
            role = (meta.get("role") or "").lower()
            if not source_table or not source_column:
                continue

            tables = [tbl.strip() for tbl in source_table.split("|")]
            columns = [col.strip() for col in source_column.split("|")]

            if role == "fact":
                for tbl, col in zip(tables, columns):
                    self.fact_map[(tbl, col)] = obt_name
                continue
            prefix = self._prefix_for_role(role, obt_name, source_column)
            if not prefix:
                prefix = self._extract_prefix(obt_name, source_column)
            if prefix:
                self.dimension_map[(tables[0], columns[0], prefix)] = obt_name

    @staticmethod
    def _extract_prefix(obt_name: str, source_col: str) -> str | None:
        if not obt_name.endswith(source_col):
            return None
        return obt_name[: -len(source_col)]

    def _prefix_for_role(self, role: str, obt_name: str, source_column: str) -> str | None:
        if not role:
            return None
        if role in self._role_prefix_map:
            return self._role_prefix_map[role]
        if obt_name.startswith(f"{role}_"):
            return f"{role}_"
        if role.endswith("_dim") and obt_name.startswith(role.replace("_dim", "_")):
            return role.replace("_dim", "_")
        if role in {"date", "time"}:
            return f"{role}_"
        if role == "customer":
            return "bill_customer_"
        if role == "cdemo":
            return "bill_cdemo_"
        if role == "hdemo":
            return "bill_hdemo_"
        return None

    def map_fact(self, table: str, column: str) -> str | None:
        return self.fact_map.get((table.lower(), column.lower()))

    def map_dimension(self, table: str, column: str, role_prefix: str) -> str | None:
        return self.dimension_map.get((table.lower(), column.lower(), role_prefix))


class TemplateLoader:
    DEFINE_PATTERN = re.compile(r"^define\s+(\w+)\s*=\s*(.*?);\s*$", re.IGNORECASE)
    PARAM_PATTERN = re.compile(r"\[(\w+)\]")

    def __init__(self, query_id: int, template_dir: Path | None = None) -> None:
        self.query_id = query_id
        self.template_dir = template_dir or TEMPLATE_DIR
        self.path = self.template_dir / f"query{query_id}.tpl"
        if not self.path.exists():
            raise FileNotFoundError(f"Template for query {query_id} not found at {self.path}")

        self.raw_text = self.path.read_text(encoding="utf-8")
        self.definitions: dict[str, str] = {}
        self.body_sql: str = ""
        self.parameters: dict[str, TemplateParameter] = {}
        self.limit_value: int | None = None

        self._parse()

    def _parse(self) -> None:
        lines: list[str] = []
        define_buffer: list[str] = []
        in_define = False

        for line in self.raw_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("--") or not stripped:
                continue

            if in_define:
                define_buffer.append(stripped)
                if stripped.endswith(";"):
                    self._store_definition(" ".join(define_buffer))
                    define_buffer = []
                    in_define = False
                continue

            if stripped.lower().startswith("define"):
                define_buffer.append(stripped)
                if stripped.endswith(";"):
                    self._store_definition(" ".join(define_buffer))
                    define_buffer = []
                    in_define = False
                else:
                    in_define = True
                continue

            lines.append(line)

        self.body_sql = "\n".join(lines).strip()
        self.limit_value = self._parse_limit()
        self.parameters = self._parse_parameters()

    def _store_definition(self, line: str) -> None:
        match = self.DEFINE_PATTERN.match(line)
        if match:
            name, expr = match.groups()
            self.definitions[name] = expr

    def _parse_limit(self) -> int | None:
        raw = self.definitions.get("_LIMIT")
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def _parse_parameters(self) -> dict[str, TemplateParameter]:
        params: dict[str, TemplateParameter] = {}
        for name, expr in self.definitions.items():
            if name.startswith("_"):
                continue
            indices = sorted(
                {int(index or 1) for index in re.findall(rf"\[{re.escape(name)}(?:\.(\d+))?\]", self.body_sql)}
            )
            params[name] = TemplateParameter(name=name, kind=self._kind(expr), indices=tuple(indices))
        return params

    @staticmethod
    def _kind(expr: str) -> str:
        expr_lower = expr.lower()
        if expr_lower.startswith(("random(", "ulist(", "dist", "sub")):
            return "numeric"
        if expr_lower.startswith("text("):
            match = re.search(r'\{"([^"]+)"', expr)
            if match:
                return "numeric" if match.group(1).isdigit() else "identifier"
            return "string"
        if expr_lower.startswith("date("):
            return "string"
        return "numeric" if expr.strip('"').isdigit() else "string"


def _gather_aliases(select: exp.Select) -> dict[str, str]:
    aliases: dict[str, str] = {}
    from_expr = select.args.get("from") or select.args.get("from_")

    def _record(target: exp.Expression | None) -> None:
        if isinstance(target, exp.Table):
            alias = target.alias or target.name
            aliases[alias] = target.name
        elif isinstance(target, exp.Subquery):
            if target.alias:
                aliases[target.alias] = target.alias

    if isinstance(from_expr, exp.From):
        _record(from_expr.this)

    for join in select.args.get("joins", []) or []:
        if isinstance(join, exp.Join):
            _record(join.this)
    return aliases


def _infer_date_role(fact_column: str) -> str | None:
    if "sold_date" in fact_column:
        return "sold_date_"
    if "sold_time" in fact_column:
        return "sold_time_"
    if "ship_date" in fact_column:
        return "ship_date_"
    if "returned_date" in fact_column or "return_date" in fact_column:
        return "return_date_"
    if "return_time" in fact_column or "returned_time" in fact_column:
        return "return_time_"
    return None


def _infer_customer_role(fact_column: str, fact_table: str) -> str | None:
    if "bill_customer" in fact_column:
        return "bill_customer_"
    if "ship_customer" in fact_column:
        return "ship_customer_"
    if "returning_customer" in fact_column or fact_table.endswith("returns"):
        return "returning_customer_"
    if "refunded_customer" in fact_column:
        return "refunded_customer_"
    if fact_table == "store_sales" and fact_column.endswith("customer_sk"):
        return "bill_customer_"
    return None


def _infer_cdemo_role(fact_column: str, fact_table: str) -> str | None:
    if "bill_cdemo" in fact_column:
        return "bill_cdemo_"
    if "ship_cdemo" in fact_column:
        return "ship_cdemo_"
    if "returning_cdemo" in fact_column or fact_table.endswith("returns"):
        return "returning_cdemo_"
    if "refunded_cdemo" in fact_column:
        return "refunded_cdemo_"
    if fact_table == "store_sales" and fact_column.endswith("cdemo_sk"):
        return "bill_cdemo_"
    return None


def _infer_hdemo_role(fact_column: str, fact_table: str) -> str | None:
    if "bill_hdemo" in fact_column:
        return "bill_hdemo_"
    if "ship_hdemo" in fact_column:
        return "ship_hdemo_"
    if "returning_hdemo" in fact_column or fact_table.endswith("returns"):
        return "returning_hdemo_"
    if "refunded_hdemo" in fact_column:
        return "refunded_hdemo_"
    if fact_table == "store_sales" and fact_column.endswith("hdemo_sk"):
        return "bill_hdemo_"
    return None


def _infer_address_role(fact_column: str, fact_table: str) -> str | None:
    if "bill_addr" in fact_column:
        return "bill_addr_"
    if "ship_addr" in fact_column:
        return "ship_addr_"
    if "returning_addr" in fact_column or fact_table.endswith("returns"):
        return "returning_addr_"
    if "refunded_addr" in fact_column:
        return "refunded_addr_"
    if fact_table == "store_sales" and fact_column.endswith("addr_sk"):
        return "bill_addr_"
    return None


class QueryConverter:
    FACT_TABLES = {"store_sales", "web_sales", "catalog_sales", "store_returns", "web_returns", "catalog_returns"}
    DIMENSION_TABLES = {
        "date_dim",
        "time_dim",
        "item",
        "store",
        "promotion",
        "reason",
        "web_site",
        "web_page",
        "call_center",
        "catalog_page",
        "ship_mode",
        "warehouse",
        "customer",
        "customer_demographics",
        "household_demographics",
        "customer_address",
    }

    CHANNEL_BY_TABLE = {
        "store_sales": "store",
        "store_returns": "store",
        "web_sales": "web",
        "web_returns": "web",
        "catalog_sales": "catalog",
        "catalog_returns": "catalog",
    }

    def __init__(self, mapper: ColumnMapper | None = None) -> None:
        self.mapper = mapper or ColumnMapper()
        self._default_dimension_prefix = {
            "date_dim": "sold_date_",
            "time_dim": "sold_time_",
            "item": "item_",
            "promotion": "promo_",
            "reason": "reason_",
            "store": "store_",
            "web_site": "web_site_",
            "web_page": "web_page_",
            "call_center": "call_center_",
            "catalog_page": "catalog_page_",
            "ship_mode": "ship_mode_",
            "warehouse": "warehouse_",
            "customer": "bill_customer_",
            "customer_demographics": "bill_cdemo_",
            "household_demographics": "bill_hdemo_",
            "customer_address": "bill_addr_",
        }
        self._fact_column_names = set(self.mapper.fact_map.values())

    def convert(self, query_id: int) -> ConvertedQuery:
        if query_id in BLOCKED_QUERY_IDS:
            raise ValueError(f"Query {query_id} cannot be converted due to missing source tables.")

        template = TemplateLoader(query_id)
        channels = self._detect_channels(template.body_sql)
        params = template.parameters
        normalized_sql = self._apply_limit_macros(template.body_sql, template.limit_value)
        tokenized_sql = self._apply_param_tokens(normalized_sql, params)

        converted = self._rewrite_sql(tokenized_sql, channels)
        placeholder_sql = self._restore_placeholders(converted, params.values())
        template_sql = self._post_process(query_id, self._normalize_intervals(placeholder_sql))

        return ConvertedQuery(
            query_id=query_id,
            template_sql=template_sql,
            placeholder_sql=placeholder_sql,
            parameters=params,
            channels=tuple(sorted(channels)),
        )

    def render(self, converted: ConvertedQuery, values: Mapping[str, str]) -> str:
        channel = converted.channels[0] if len(converted.channels) == 1 else None
        sql = converted.placeholder_sql
        for param in converted.parameters.values():

            def substitute(match: re.Match[str], param: TemplateParameter = param) -> str:
                key = f"{param.name}.{int(match.group(1) or 1):02d}"
                if key not in values:
                    raise ValueError(f"Query {converted.query_id} has no value for parameter {key}")
                value = str(values[key])
                if param.kind == "identifier":
                    return ", ".join(
                        self._map_identifier(converted.query_id, item.strip(), channel) for item in value.split(",")
                    )
                return value

            sql = self._param_pattern(param.name).sub(substitute, sql)
        return self._post_process(converted.query_id, self._normalize_intervals(sql))

    def _detect_channels(self, sql_text: str) -> set[str]:
        channels: set[str] = set()
        lowered = sql_text.lower()
        for table, channel in self.CHANNEL_BY_TABLE.items():
            if table in lowered:
                channels.add(channel)
        return channels

    def _apply_limit_macros(self, sql_text: str, limit_value: int | None) -> str:
        sql = sql_text.replace("[_LIMITA]", "").replace("[_LIMITB]", "")
        if "[_LIMITC]" in sql:
            limit_clause = f"LIMIT {limit_value}" if limit_value is not None else ""
            sql = sql.replace("[_LIMITC]", limit_clause)
        return sql

    def _apply_param_tokens(self, sql_text: str, params: dict[str, TemplateParameter]) -> str:
        result = sql_text
        for param in params.values():
            result = self._param_pattern(param.name).sub(
                lambda match, param=param: param.token_expression(int(match.group(1) or 1)), result
            )
        return result

    def _rewrite_sql(self, sql_text: str, channels: set[str]) -> str:
        parsed = sqlglot.parse_one(sql_text, read="postgres")
        rewritten = self._rewrite_expression(parsed, channels)
        self._qualify_obt_columns(rewritten)
        return rewritten.sql(dialect="duckdb", pretty=True)

    def _qualify_obt_columns(self, expression: exp.Expression) -> None:
        for select in expression.find_all(exp.Select):
            aliases = _gather_aliases(select)
            if OBT_TABLE_NAME not in aliases.values():
                continue
            for column in self._direct_columns(select):
                if not column.table and column.name != "channel" and column.name in self.mapper.obt_columns:
                    column.set("table", "obt")

    def _rewrite_expression(self, expression: exp.Expression, channels: set[str]) -> exp.Expression:
        for arg in expression.args.values():
            if isinstance(arg, exp.Expression):
                self._rewrite_expression(arg, channels)
            elif isinstance(arg, list):
                for item in arg:
                    if isinstance(item, exp.Expression):
                        self._rewrite_expression(item, channels)

        if isinstance(expression, exp.Select):
            self._rewrite_select(expression, channels)
        return expression

    def _rewrite_select(self, select: exp.Select, channels: set[str]) -> None:
        aliases = _gather_aliases(select)
        select_channels = self._channels_for_aliases(aliases) or channels
        subquery_aliases: dict[str, exp.Subquery] = {}
        joins_arg = select.args.get("joins", [])
        from_expr = select.args.get("from") or select.args.get("from_")
        if isinstance(from_expr, exp.From) and isinstance(from_expr.this, exp.Subquery) and from_expr.this.alias:
            subquery_aliases[from_expr.this.alias] = from_expr.this
        for join in joins_arg:
            if isinstance(join, exp.Join) and isinstance(join.this, exp.Subquery) and join.this.alias:
                subquery_aliases[join.this.alias] = join.this
        base_aliases = [
            alias for alias, table in aliases.items() if table in self.FACT_TABLES or table in self.DIMENSION_TABLES
        ]
        extra_aliases = [
            alias
            for alias, table in aliases.items()
            if table not in self.FACT_TABLES and table not in self.DIMENSION_TABLES
        ]
        role_map = self._infer_roles(select, aliases)
        self._rewrite_columns(select, aliases, role_map, has_obt=bool(base_aliases))
        if "wscs" in extra_aliases:
            extra_aliases = [alias for alias in extra_aliases if alias != "wscs"]

        join_conditions = self._inner_join_conditions(joins_arg)

        where_clauses: list[exp.Expression] = []
        if select.args.get("where"):
            where_clauses.append(select.args["where"].this)
        where_clauses.extend(join_conditions)
        if select_channels and base_aliases:
            channel_predicate = self._build_channel_predicate(select_channels)
            if channel_predicate is not None:
                where_clauses.append(channel_predicate)

        self._set_combined_where(select, where_clauses)

        if not base_aliases:
            return

        if base_aliases:
            table_expr = exp.table_(OBT_TABLE_NAME, alias="obt")
            select.set("joins", [])
            select.set("from_", exp.From(this=table_expr))
            joins: list[exp.Join] = []
            for alias in extra_aliases:
                subquery = subquery_aliases.get(alias)
                if subquery:
                    joins.append(exp.Join(this=subquery, kind="cross"))
                    continue
                table_name = aliases.get(alias, alias)
                joins.append(
                    exp.Join(
                        this=exp.Table(
                            this=exp.to_identifier(table_name),
                            alias=exp.TableAlias(this=exp.to_identifier(alias)),
                        ),
                        kind="cross",
                    )
                )
        if joins:
            select.set("joins", joins)
        elif extra_aliases:
            first = extra_aliases[0]
            table_name = aliases.get(first, first)
            table_expr = exp.Table(
                this=exp.to_identifier(table_name),
                alias=exp.TableAlias(this=exp.to_identifier(first)),
            )
            select.set("from_", exp.From(this=table_expr))
            joins = []
            for alias in extra_aliases[1:]:
                table_name = aliases.get(alias, alias)
                joins.append(
                    exp.Join(
                        this=exp.Table(
                            this=exp.to_identifier(table_name),
                            alias=exp.TableAlias(this=exp.to_identifier(alias)),
                        ),
                        kind="cross",
                    )
                )
            if joins:
                select.set("joins", joins)

    def _set_combined_where(self, select: exp.Select, where_clauses: list[exp.Expression]) -> None:
        if not where_clauses:
            return
        combined = where_clauses[0]
        for clause in where_clauses[1:]:
            combined = exp.and_(combined, clause)
        pruned = self._prune_current_pointer_predicates(combined)
        if pruned is not None:
            select.set("where", exp.Where(this=pruned))

    @staticmethod
    def _inner_join_conditions(joins: list[Any]) -> list[exp.Expression]:
        return [
            join.args["on"]
            for join in joins
            if join.args.get("on") and str(join.args.get("side") or "").upper() not in {"LEFT", "RIGHT", "FULL"}
        ]

    _CURRENT_POINTER_PATTERN = re.compile(r"_c_current_(addr|cdemo|hdemo)_sk$")
    _CURRENT_TARGET_PATTERN = re.compile(r"_(ca_address_sk|cd_demo_sk|hd_demo_sk)$")

    def _prune_current_pointer_predicates(self, node: exp.Expression) -> exp.Expression | None:
        if isinstance(node, exp.And):
            left = self._prune_current_pointer_predicates(node.this)
            right = self._prune_current_pointer_predicates(node.expression)
            if left is None:
                return right
            if right is None:
                return left
            node.set("this", left)
            node.set("expression", right)
            return node
        if isinstance(node, exp.Paren):
            inner = self._prune_current_pointer_predicates(node.this)
            if inner is None:
                return None
            node.set("this", inner)
            return node
        if isinstance(node, exp.EQ) and isinstance(node.left, exp.Column) and isinstance(node.right, exp.Column):
            names = {node.left.name.lower(), node.right.name.lower()}
            has_pointer = any(self._CURRENT_POINTER_PATTERN.search(name) for name in names)
            has_target = any(self._CURRENT_TARGET_PATTERN.search(name) for name in names)
            if has_pointer and has_target:
                return None
        return node

    @staticmethod
    def _build_channel_predicate(channels: set[str]) -> exp.Expression | None:
        if not channels:
            return None
        column = exp.column("channel")
        if len(channels) == 1:
            return exp.EQ(this=column, expression=exp.Literal.string(next(iter(channels))))
        values = [exp.Literal.string(channel) for channel in sorted(channels)]
        return exp.In(this=column, expressions=values)

    def _channels_for_aliases(self, aliases: dict[str, str]) -> set[str]:
        return {self.CHANNEL_BY_TABLE[table] for table in aliases.values() if table in self.CHANNEL_BY_TABLE}

    @staticmethod
    def _direct_columns(select: exp.Select) -> list[exp.Column]:
        columns: list[exp.Column] = []
        stack: list[Any] = list(select.args.values())
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, exp.Select):
                continue
            elif isinstance(node, exp.Column):
                columns.append(node)
                stack.extend(node.args.values())
            elif isinstance(node, exp.Expression):
                stack.extend(node.args.values())
        return columns

    def _rewrite_columns(
        self, select: exp.Select, aliases: dict[str, str], role_map: dict[str, str], *, has_obt: bool
    ) -> None:
        for column in self._direct_columns(select):
            table_alias = column.table
            name = column.name
            source_table: str | None = None
            if not table_alias:
                new_name = self._map_unqualified_column(name, aliases, role_map)
            else:
                source_table = aliases.get(table_alias)
                new_name = None
                if source_table:
                    role_prefix = role_map.get(table_alias)
                    if source_table in self.DIMENSION_TABLES:
                        prefix = role_prefix or self._default_dimension_prefix.get(source_table)
                        if prefix:
                            new_name = self.mapper.map_dimension(source_table, name, prefix)
                    elif source_table in self.FACT_TABLES:
                        new_name = self.mapper.map_fact(
                            source_table, f"{table_alias}_{name}" if "_" not in name else name
                        )
                        if new_name is None:
                            new_name = self.mapper.map_fact(source_table, name)
                        if new_name is None:
                            new_name = self._map_unqualified_column(name, aliases, role_map)
                elif table_alias:
                    new_name = self._map_unqualified_column(name, aliases, role_map)
            if not new_name and name in getattr(self.mapper, "obt_columns", set()):
                new_name = name
                source_table = source_table or "fact"
            if not new_name and name in self._fact_column_names:
                new_name = name
                source_table = source_table or "fact"
            if new_name:
                column.set("this", exp.to_identifier(new_name))
                if has_obt and source_table and (source_table in self.FACT_TABLES or source_table == "fact"):
                    column.set("table", "obt")
                else:
                    column.set("table", None)

    _SIMPLE_DIMENSION_PREFIX_MAP: dict[str, tuple[str, str]] = {
        "p_": ("promotion", "promo_"),
        "r_": ("reason", "reason_"),
        "cp_": ("catalog_page", "catalog_page_"),
        "cc_": ("call_center", "call_center_"),
        "sm_": ("ship_mode", "ship_mode_"),
        "w_": ("warehouse", "warehouse_"),
        "wp_": ("web_page", "web_page_"),
        "web_": ("web_site", "web_site_"),
    }

    _ALIASED_DIMENSION_PREFIX_MAP: dict[str, tuple[str, str]] = {
        "c_": ("customer", "bill_customer_"),
        "cd_": ("customer_demographics", "bill_cdemo_"),
        "hd_": ("household_demographics", "bill_hdemo_"),
        "ca_": ("customer_address", "bill_addr_"),
        "s_": ("store", "store_"),
    }

    _RETURN_PREFIX_CHANNEL_MAP: dict[str, str] = {
        "sr_": "store",
        "wr_": "web",
        "cr_": "catalog",
    }

    def _map_unqualified_column(self, name: str, aliases: dict[str, str], role_map: dict[str, str]) -> str | None:
        lowered = name.lower()

        mapped = self._map_fact_column(lowered)
        if mapped:
            return mapped

        if lowered.startswith("i_"):
            alias_for_item = self._alias_for_table(aliases, "item")
            role_prefix = role_map.get(alias_for_item or "", "item_")
            return self.mapper.map_dimension("item", lowered, role_prefix)

        if lowered.startswith("d_"):
            mapped = self._map_date_dimension_column(lowered, aliases, role_map)
            if mapped:
                return mapped

        mapped = self._map_return_prefix_column(lowered, role_map, aliases)
        if mapped:
            return mapped

        if lowered.startswith("s_store_"):
            return self._map_store_numeric_suffix(lowered, aliases, role_map)

        mapped = self._map_aliased_dimension_column(lowered, aliases, role_map)
        if mapped:
            return mapped

        return self._map_simple_dimension_column(lowered)

    def _map_fact_column(self, lowered: str) -> str | None:
        fact_prefix_map = {
            "ss_": "store_sales",
            "sr_": "store_returns",
            "ws_": "web_sales",
            "wr_": "web_returns",
            "cs_": "catalog_sales",
            "cr_": "catalog_returns",
        }
        for prefix, table in fact_prefix_map.items():
            if lowered.startswith(prefix):
                mapped = self.mapper.map_fact(table, lowered)
                if mapped:
                    return mapped
        return None

    def _map_date_dimension_column(self, lowered: str, aliases: dict[str, str], role_map: dict[str, str]) -> str | None:
        alias_for_date = self._alias_for_table(aliases, "date_dim")
        role_prefix = role_map.get(alias_for_date or "", "sold_date_")
        return self.mapper.map_dimension("date_dim", lowered, role_prefix)

    def _map_return_prefix_column(self, lowered: str, role_map: dict[str, str], aliases: dict[str, str]) -> str | None:
        for prefix, channel in self._RETURN_PREFIX_CHANNEL_MAP.items():
            if lowered.startswith(prefix):
                mapped = self._map_return_reference(lowered, role_map, aliases, channel=channel)
                if mapped:
                    return mapped
        return None

    def _map_aliased_dimension_column(
        self, lowered: str, aliases: dict[str, str], role_map: dict[str, str]
    ) -> str | None:
        for prefix, (dim_table, default_role) in self._ALIASED_DIMENSION_PREFIX_MAP.items():
            if lowered.startswith(prefix):
                alias = self._alias_for_table(aliases, dim_table)
                role_prefix = role_map.get(alias or "", default_role)
                return self.mapper.map_dimension(dim_table, lowered, role_prefix)
        return None

    def _map_store_numeric_suffix(self, lowered: str, aliases: dict[str, str], role_map: dict[str, str]) -> str | None:
        base = re.sub(r"\d+$", "", lowered)
        alias_for_store = self._alias_for_table(aliases, "store")
        role_prefix = role_map.get(alias_for_store or "", "store_")
        return self.mapper.map_dimension("store", base, role_prefix)

    def _map_simple_dimension_column(self, lowered: str) -> str | None:
        for prefix, (dim_table, role_prefix) in self._SIMPLE_DIMENSION_PREFIX_MAP.items():
            if lowered.startswith(prefix):
                return self.mapper.map_dimension(dim_table, lowered, role_prefix)
        return None

    @staticmethod
    def _alias_for_table(aliases: dict[str, str], table_name: str) -> str | None:
        for alias, table in aliases.items():
            if table == table_name:
                return alias
        return None

    def _map_return_reference(
        self, name: str, role_map: dict[str, str], aliases: dict[str, str], channel: str
    ) -> str | None:
        role_prefix = "returning_"
        if "refunded" in name:
            role_prefix = "refunded_"

        role_prefix_map = {
            "customer_sk": f"{role_prefix}customer_",
            "cdemo_sk": f"{role_prefix}cdemo_",
            "hdemo_sk": f"{role_prefix}hdemo_",
            "addr_sk": f"{role_prefix}addr_",
        }

        for suffix, prefix in role_prefix_map.items():
            if name.endswith(suffix):
                table_name = (
                    "customer"
                    if "customer" in suffix
                    else "customer_demographics"
                    if "cdemo" in suffix
                    else "household_demographics"
                    if "hdemo" in suffix
                    else "customer_address"
                )
                source_column = {
                    "customer": "c_customer_sk",
                    "customer_demographics": "cd_demo_sk",
                    "household_demographics": "hd_demo_sk",
                    "customer_address": "ca_address_sk",
                }[table_name]
                alias = self._alias_for_table(aliases, table_name) or ""
                role = role_map.get(alias, prefix)
                return self.mapper.map_dimension(table_name, source_column, role)

        if name.endswith("reason_sk"):
            return self.mapper.map_dimension("reason", "r_reason_sk", "reason_") or self.mapper.map_fact(
                f"{channel}_returns", name
            )

        if name.endswith("ticket_number") or name.endswith("order_number"):
            return "sale_id"

        if name.endswith("item_sk"):
            return "item_sk"

        return None

    @staticmethod
    def _table_from_prefix(column_name: str) -> str | None:
        lowered = column_name.lower()
        for prefix, table in _PREFIX_TO_TABLE.items():
            if lowered.startswith(prefix):
                return table
        return None

    def _infer_roles(self, select: exp.Select, aliases: dict[str, str]) -> dict[str, str]:
        role_map: dict[str, str] = {}

        conditions: list[exp.Expression] = []
        for join in select.args.get("joins", []):
            condition = join.args.get("on")
            if condition:
                conditions.append(condition)

        where_expr = select.args.get("where")
        if where_expr:
            conditions.append(where_expr.this)

        resolved: list[tuple[str, str, str, str, str | None, str | None]] = []
        for condition in conditions:
            for left, right in self._extract_column_pairs(condition):
                left_alias = self._resolve_alias(left, aliases)
                right_alias = self._resolve_alias(right, aliases)
                left_table = aliases.get(left_alias or "", self._table_from_prefix(left.name) or "")
                right_table = aliases.get(right_alias or "", self._table_from_prefix(right.name) or "")
                resolved.append((left_table, right_table, left.name, right.name, left_alias, right_alias))
                self._update_role_map(role_map, left_table, right_table, left.name, right.name, left_alias, right_alias)
                self._update_role_map(role_map, right_table, left_table, right.name, left.name, right_alias, left_alias)

        pointer_suffix = {
            "customer_demographics": "cdemo_",
            "household_demographics": "hdemo_",
            "customer_address": "addr_",
        }
        for left_table, right_table, _ln, _rn, left_alias, right_alias in resolved:
            for dim_table, dim_alias, cust_alias in (
                (left_table, left_alias, right_alias),
                (right_table, right_alias, left_alias),
            ):
                if dim_table not in pointer_suffix or cust_alias is None or dim_alias is None:
                    continue
                cust_table = right_table if dim_table == left_table else left_table
                if cust_table != "customer":
                    continue
                cust_role = role_map.get(cust_alias or "")
                if cust_role and cust_role.endswith("customer_"):
                    role_map[dim_alias] = f"{cust_role[: -len('customer_')]}{pointer_suffix[dim_table]}"

        return role_map

    def _resolve_alias(self, column: exp.Column, aliases: dict[str, str]) -> str | None:
        if column.table:
            return column.table
        prefix_table = self._table_from_prefix(column.name)
        if not prefix_table:
            return None
        return self._alias_for_table(aliases, prefix_table) or prefix_table

    def _update_role_map(
        self,
        role_map: dict[str, str],
        dim_table: str,
        fact_table: str,
        _dim_col: str,
        fact_col: str,
        dim_alias: str | None,
        fact_alias: str | None,
    ) -> None:
        if not dim_alias or not fact_alias:
            return
        if dim_table not in self.DIMENSION_TABLES or fact_table not in self.FACT_TABLES:
            return

        dim_table_lower = dim_table.lower()
        fact_col_lower = fact_col.lower()
        fact_table_lower = fact_table.lower()

        if dim_table_lower in ("date_dim", "time_dim"):
            role = _infer_date_role(fact_col_lower)
        elif dim_table_lower in _DIM_STATIC_ROLE:
            role = _DIM_STATIC_ROLE[dim_table_lower]
        elif dim_table_lower == "customer":
            role = _infer_customer_role(fact_col_lower, fact_table_lower)
        elif dim_table_lower == "customer_demographics":
            role = _infer_cdemo_role(fact_col_lower, fact_table_lower)
        elif dim_table_lower == "household_demographics":
            role = _infer_hdemo_role(fact_col_lower, fact_table_lower)
        elif dim_table_lower == "customer_address":
            role = _infer_address_role(fact_col_lower, fact_table_lower)
        else:
            role = None

        if role:
            role_map[dim_alias] = role

    def _extract_column_pairs(self, condition: exp.Expression) -> Iterable[tuple[exp.Column, exp.Column]]:
        pairs: list[tuple[exp.Column, exp.Column]] = []
        stack: list[Any] = [condition]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, exp.Select):
                continue
            elif isinstance(node, exp.EQ):
                left = node.left
                right = node.right
                if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                    pairs.append((left, right))
                stack.extend(node.args.values())
            elif isinstance(node, exp.Expression):
                stack.extend(node.args.values())
        return pairs

    def _restore_placeholders(self, sql_text: str, params: Iterable[TemplateParameter]) -> str:
        restored = sql_text
        for param in params:
            for index in sorted(param.indices, reverse=True):
                placeholder = param.placeholder(index)
                restored = restored.replace(param.token_expression(index), placeholder)
                restored = restored.replace(param.token(index), placeholder)
        return restored

    def _map_identifier(self, query_id: int, value: str, channel: str | None) -> str:
        overrides = _QUERY_IDENTIFIER_COLUMNS.get(query_id, {})
        if value.lower() in overrides:
            return overrides[value.lower()]
        return self._map_identifier_default(value, channel) or value

    def _map_identifier_default(self, value: str, channel: str | None) -> str | None:
        lowered = value.lower()
        table_hint = next(
            (table for prefix, table in _IDENTIFIER_DEFAULT_FACT_TABLES.items() if lowered.startswith(prefix)), None
        )
        if table_hint:
            mapped = self.mapper.map_fact(table_hint, lowered)
            if mapped:
                return mapped

        if channel:
            table_hint = _CHANNEL_FACT_TABLES.get(channel)
            if table_hint:
                mapped = self.mapper.map_fact(table_hint, lowered)
                if mapped:
                    return mapped
        return None

    @staticmethod
    def _normalize_intervals(sql_text: str) -> str:
        normalized = re.sub(r"\+\s*([0-9]+)\s+as\s+days", r"+ INTERVAL \1 DAY", sql_text, flags=re.IGNORECASE)
        normalized = re.sub(r"\+\s*([0-9]+)\s+days", r"+ INTERVAL \1 DAY", normalized, flags=re.IGNORECASE)
        return normalized

    def _post_process(self, query_id: int, sql_text: str) -> str:
        sql_text = self._post_process_fix_1(query_id, sql_text)
        sql_text = self._post_process_fix_2(query_id, sql_text)
        sql_text = self._post_process_fix_3(query_id, sql_text)
        sql_text = self._post_process_fix_4(query_id, sql_text)
        sql_text = self._post_process_fix_5(query_id, sql_text)
        sql_text = self._post_process_fix_6(query_id, sql_text)
        return sql_text

    def _post_process_fix_6(self, query_id: int, sql_text: str) -> str:
        if query_id in {10, 35, 69}:
            sql_text = self._correlate_customer_exists(sql_text)

        if query_id == 44:
            sql_text = re.sub(
                r"obt\.item_i_product_name AS best_performing",
                "(SELECT MAX(best_obt.item_i_product_name) FROM tpcds_sales_returns_obt AS best_obt "
                "WHERE best_obt.item_sk = asceding.item_sk) AS best_performing",
                sql_text,
            )
            sql_text = re.sub(
                r"obt\.item_i_product_name AS worst_performing",
                "(SELECT MAX(worst_obt.item_i_product_name) FROM tpcds_sales_returns_obt AS worst_obt "
                "WHERE worst_obt.item_sk = descending.item_sk) AS worst_performing",
                sql_text,
            )
            sql_text = re.sub(r"\s+AND obt\.item_i_item_sk = obt\.item_sk", "", sql_text)
            sql_text = re.sub(r"AND channel = 'store'\s+ORDER BY", "ORDER BY", sql_text)
            sql_text = sql_text.replace(
                "FROM tpcds_sales_returns_obt AS obt\nCROSS JOIN (\n  SELECT\n    *\n  FROM (\n    SELECT\n      item_sk,\n      RANK() OVER (ORDER BY rank_col ASC) AS rnk",
                "FROM (\n  SELECT\n    *\n  FROM (\n    SELECT\n      item_sk,\n      RANK() OVER (ORDER BY rank_col ASC) AS rnk",
                1,
            )

        if query_id == 54:
            sql_text = sql_text.replace(
                "obt.bill_customer_c_customer_sk = obt.ship_customer_sk",
                "obt.bill_customer_c_customer_sk = my_customers.bill_customer_c_customer_sk",
            )

        if query_id == 78:
            sql_text = sql_text.replace("sale_id IS NULL AND", "has_return = 'N' AND")
            sql_text = sql_text.replace("obt.sale_id IS NULL AND", "obt.has_return = 'N' AND")

        if query_id in {16, 94}:
            sql_text = re.sub(
                r"FROM tpcds_sales_returns_obt AS obt\s+WHERE\s+\(\s+obt\.sale_id = obt\.sale_id "
                r"AND obt\.warehouse_sk <> obt\.warehouse_sk\s+\)\s+AND channel = '(\w+)'",
                r"FROM tpcds_sales_returns_obt AS other_obt WHERE ( other_obt.sale_id = obt.sale_id "
                r"AND other_obt.warehouse_sk <> obt.warehouse_sk ) AND other_obt.channel = '\1'",
                sql_text,
            )
            sql_text = re.sub(
                r"FROM tpcds_sales_returns_obt AS obt\s+WHERE\s+obt\.sale_id = obt\.sale_id "
                r"AND (?:obt\.has_return = 'Y' AND )?channel = '(\w+)'",
                r"FROM tpcds_sales_returns_obt AS ret_obt WHERE ret_obt.sale_id = obt.sale_id "
                r"AND ret_obt.has_return = 'Y' AND ret_obt.channel = '\1'",
                sql_text,
            )

        if query_id == 95:
            sql_text = sql_text.replace(
                "WITH ws_wh AS (\n  SELECT\n    obt.sale_id,\n    obt.warehouse_sk AS wh1,\n    obt.warehouse_sk AS wh2\n"
                "  FROM tpcds_sales_returns_obt AS obt\n  WHERE\n    (\n      obt.sale_id = obt.sale_id AND obt.warehouse_sk <> obt.warehouse_sk\n    )\n    AND channel = 'web'\n)",
                "WITH ws_wh AS (\n  SELECT DISTINCT\n    obt.sale_id AS sale_id,\n    obt.warehouse_sk AS wh1,\n    other_obt.warehouse_sk AS wh2\n"
                "  FROM tpcds_sales_returns_obt AS obt\n"
                "  JOIN tpcds_sales_returns_obt AS other_obt\n"
                "    ON obt.sale_id = other_obt.sale_id AND obt.warehouse_sk <> other_obt.warehouse_sk\n"
                "  WHERE\n    obt.channel = 'web' AND other_obt.channel = 'web'\n)",
            )
            sql_text = re.sub(
                r"AND obt\.sale_id IN \(\s+SELECT\s+obt\.sale_id\s+FROM tpcds_sales_returns_obt AS obt\s+"
                r"CROSS JOIN ws_wh AS ws_wh\s+WHERE\s+obt\.sale_id = obt\.sale_id AND obt\.has_return = 'Y' "
                r"AND obt\.channel = 'web'\s+\)",
                "AND obt.sale_id IN (\n      SELECT\n        ret_obt.sale_id\n"
                "      FROM tpcds_sales_returns_obt AS ret_obt\n"
                "      WHERE\n        ret_obt.sale_id = obt.sale_id AND ret_obt.has_return = 'Y' "
                "AND ret_obt.channel = 'web'\n    )",
                sql_text,
            )

        return sql_text

    @staticmethod
    def _correlate_customer_exists(sql_text: str) -> str:
        pattern = re.compile(
            r"FROM tpcds_sales_returns_obt AS obt\s+WHERE\s+\(\s+obt\.bill_customer_c_customer_sk = "
            r"obt\.(?:ship_customer_sk|bill_customer_sk)(?P<rest>.*?)\)\s+AND channel = '(?P<channel>\w+)'",
            flags=re.DOTALL,
        )

        def _replace(match: re.Match[str]) -> str:
            rest = match.group("rest").replace("obt.", "ex_obt.")
            channel = match.group("channel")
            return (
                "FROM tpcds_sales_returns_obt AS ex_obt WHERE ( "
                f"ex_obt.bill_customer_c_customer_sk = obt.bill_customer_c_customer_sk{rest}) "
                f"AND ex_obt.channel = '{channel}'"
            )

        return pattern.sub(_replace, sql_text)

    def _post_process_fix_1(self, query_id: int, sql_text: str) -> str:
        if query_id == 2:
            sql_text = sql_text.replace(
                "obt.sold_date_d_week_seq,\n    SUM(",
                "obt.sold_date_d_week_seq AS d_week_seq,\n    SUM(",
            )
            sql_text = sql_text.replace(
                "GROUP BY\n  obt.sold_date_d_week_seq",
                "GROUP BY\n  d_week_seq",
            )
            sql_text = sql_text.replace(
                "GROUP BY\n    obt.sold_date_d_week_seq",
                "GROUP BY\n    d_week_seq",
            )
            sql_text = sql_text.replace(
                "  SELECT\n    obt.sold_date_d_week_seq AS d_week_seq,\n    SUM(",
                "  SELECT\n    obt.sold_date_d_week_seq AS d_week_seq,\n    obt.sold_date_d_year AS d_year,\n    SUM(",
            )
            sql_text = sql_text.replace(
                "GROUP BY\n  d_week_seq",
                "GROUP BY\n  d_week_seq,\n  d_year",
            )
            sql_text = sql_text.replace(
                "GROUP BY\n    d_week_seq",
                "GROUP BY\n    d_week_seq,\n    d_year",
            )
            wswscs_block = re.compile(
                r"  FROM tpcds_sales_returns_obt AS obt\n"
                r"  CROSS JOIN wswscs AS wswscs\n"
                r"  WHERE\n"
                r"    \(\n"
                r"      obt.sold_date_d_week_seq = wswscs.d_week_seq AND obt.sold_date_d_year = ([^\n]+)\n"
                r"    \)\n"
                r"    AND channel IN \('catalog', 'web'\)\n"
            )

            def _wswscs_replacement(match: re.Match[str]) -> str:
                year_expr = match.group(1)
                return f"  FROM wswscs AS wswscs\n  WHERE\n    (\n      d_year = {year_expr}\n    )\n"

            sql_text = wswscs_block.sub(_wswscs_replacement, sql_text, count=1)
            sql_text = wswscs_block.sub(_wswscs_replacement, sql_text, count=1)

        if query_id == 8:
            sql_text = sql_text.replace(
                "SELECT\n    bill_addr_ca_zip\n  FROM (",
                "SELECT\n    ca_zip\n  FROM (",
            )
            sql_text = re.sub(
                r"(\bINTERSECT\b\s+SELECT)\s+bill_addr_ca_zip\s+(FROM\s*\()",
                r"\1\n    ca_zip\n  \2",
                sql_text,
            )
            sql_text = sql_text.replace(
                "SUBSTRING(obt.bill_addr_ca_zip, 1, 5) AS ca_zip",
                "SUBSTRING(bill_addr_ca_zip, 1, 5) AS ca_zip",
            )

        if query_id == 14:
            sql_text = sql_text.replace(
                "SELECT\n    obt.item_i_item_sk AS ss_item_sk\n  FROM tpcds_sales_returns_obt AS obt",
                "SELECT\n    obt.item_i_item_sk AS ss_item_sk,\n    obt.item_i_brand_id AS brand_id,\n    obt.item_i_class_id AS class_id,\n    obt.item_i_category_id AS category_id\n  FROM tpcds_sales_returns_obt AS obt",
            )
            sql_text = sql_text.replace("= brand_id", "= cross_items.brand_id")
            sql_text = sql_text.replace("= class_id", "= cross_items.class_id")
            sql_text = sql_text.replace("= category_id", "= cross_items.category_id")

        if query_id == 16:
            sql_text = sql_text.replace(
                "obt.sale_id = cr1.cr_order_number AND channel = 'catalog'",
                "obt.sale_id = obt.sale_id AND obt.has_return = 'Y' AND obt.channel = 'catalog'",
            )

        return sql_text

    def _post_process_fix_2(self, query_id: int, sql_text: str) -> str:
        if query_id == 31:
            sql_text = sql_text.replace(
                "obt.bill_addr_ca_county,\n    obt.sold_date_d_qoy,\n    obt.sold_date_d_year,\n    SUM(obt.ext_sales_price) AS store_sales",
                "obt.bill_addr_ca_county AS ca_county,\n    obt.sold_date_d_qoy AS d_qoy,\n    obt.sold_date_d_year AS d_year,\n    SUM(obt.ext_sales_price) AS store_sales",
            )
            sql_text = sql_text.replace(
                "obt.bill_addr_ca_county,\n    obt.sold_date_d_qoy,\n    obt.sold_date_d_year,\n    SUM(obt.ext_sales_price) AS web_sales",
                "obt.bill_addr_ca_county AS ca_county,\n    obt.sold_date_d_qoy AS d_qoy,\n    obt.sold_date_d_year AS d_year,\n    SUM(obt.ext_sales_price) AS web_sales",
            )
            sql_text = sql_text.replace(
                "GROUP BY\n    obt.bill_addr_ca_county,\n    obt.sold_date_d_qoy,\n    obt.sold_date_d_year",
                "GROUP BY\n    ca_county,\n    d_qoy,\n    d_year",
            )

        if query_id in {34, 46, 68}:
            sql_text = sql_text.replace(
                "CROSS JOIN (\n  SELECT\n    obt.sale_id,\n    obt.ship_customer_sk",
                "CROSS JOIN (\n  SELECT\n    sub_obt.sale_id AS dn_sale_id,\n    sub_obt.ship_customer_sk AS dn_ship_customer_sk",
            )
            sql_text = sql_text.replace(
                "FROM tpcds_sales_returns_obt AS obt\n  WHERE\n    (\n      obt.sold_date_sk",
                "FROM tpcds_sales_returns_obt AS sub_obt\n  WHERE\n    (\n      sub_obt.sold_date_sk",
            )
            sql_text = sql_text.replace("obt.store_sk = store_s_store_sk", "sub_obt.store_sk = store_s_store_sk")
            sql_text = sql_text.replace("obt.ship_hdemo_sk", "sub_obt.ship_hdemo_sk")
            sql_text = sql_text.replace("obt.bill_hdemo_sk", "sub_obt.bill_hdemo_sk")
            sql_text = sql_text.replace(
                "GROUP BY\n    obt.sale_id,\n    obt.ship_customer_sk",
                "GROUP BY\n    sub_obt.sale_id,\n    sub_obt.ship_customer_sk",
            )
            sql_text = sql_text.replace(
                ") AS dn\nWHERE",
                ") AS dn\nWHERE\n  obt.sale_id = dn.dn_sale_id AND obt.ship_customer_sk = dn.dn_ship_customer_sk AND",
            )
            if query_id == 68:
                sql_text = sql_text.replace("  extended", "  obt.list_price AS extended")
            if query_id == 46:
                sql_text = sql_text.replace("  amt", "  obt.ext_sales_price AS amt")

        if query_id == 79:
            sql_text = sql_text.replace(
                "CROSS JOIN (\n  SELECT\n    obt.sale_id,\n    obt.ship_customer_sk",
                "CROSS JOIN (\n  SELECT\n    sub_obt.sale_id AS ms_sale_id,\n    sub_obt.ship_customer_sk AS ms_ship_customer_sk",
            )
            sql_text = sql_text.replace(
                "FROM tpcds_sales_returns_obt AS obt\n  WHERE\n    (\n      obt.sold_date_sk",
                "FROM tpcds_sales_returns_obt AS sub_obt\n  WHERE\n    (\n      sub_obt.sold_date_sk",
            )
            sql_text = sql_text.replace("obt.store_sk = store_s_store_sk", "sub_obt.store_sk = store_s_store_sk")
            sql_text = sql_text.replace("obt.ship_hdemo_sk", "sub_obt.ship_hdemo_sk")
            sql_text = sql_text.replace("obt.coupon_amt", "sub_obt.coupon_amt")
            sql_text = sql_text.replace("obt.net_profit", "sub_obt.net_profit")
            sql_text = sql_text.replace(
                "GROUP BY\n    obt.sale_id,\n    obt.ship_customer_sk,\n    obt.ship_addr_sk",
                "GROUP BY\n    sub_obt.sale_id,\n    sub_obt.ship_customer_sk,\n    sub_obt.ship_addr_sk",
            )
            sql_text = sql_text.replace(
                ") AS ms\nWHERE",
                ") AS ms\nWHERE\n  obt.sale_id = ms.ms_sale_id AND obt.ship_customer_sk = ms.ms_ship_customer_sk AND",
            )
            sql_text = sql_text.replace("  sale_id,\n  amt", "  obt.sale_id,\n  amt")
            sql_text = sql_text.replace("SUBSTRING(store_s_city", "SUBSTRING(obt.store_s_city")
            sql_text = re.sub(r"(?<![a-z_\.])bill_customer_c_", "obt.bill_customer_c_", sql_text)
            sql_text = sql_text.replace("obt.obt.bill_customer_c_", "obt.bill_customer_c_")
            sql_text = re.sub(
                r"(?<!obt\.)ship_customer_sk = obt\.bill_customer_c_customer_sk",
                "obt.ship_customer_sk = obt.bill_customer_c_customer_sk",
                sql_text,
            )

        return sql_text

    def _post_process_fix_3(self, query_id: int, sql_text: str) -> str:
        if query_id == 40:
            sql_text = re.sub(
                r"CAST\('(\d{4}-\d{2}-\d{2})' AS DATE\) - (\d+) AS days",
                r"CAST('\1' AS DATE) - INTERVAL \2 DAY",
                sql_text,
            )
            sql_text = re.sub(
                r"CAST\('(\d{4}-\d{2}-\d{2})' AS DATE\) \+ (\d+) AS days",
                r"CAST('\1' AS DATE) + INTERVAL \2 DAY",
                sql_text,
            )

        if query_id == 44:
            sql_text = sql_text.replace(
                "FROM (\n  SELECT", "FROM tpcds_sales_returns_obt AS obt CROSS JOIN (\n  SELECT", 1
            )
            sql_text = sql_text.replace("AND item_i_item_sk = item_sk", "AND obt.item_sk = asceding.item_sk", 1)
            sql_text = sql_text.replace("AND item_i_item_sk = item_sk", "AND obt.item_sk = descending.item_sk", 1)

        if query_id == 45:
            sql_text = re.sub(r"(?<![\w.])ca_(\w+)", r"bill_addr_ca_\1", sql_text)

        if query_id in {47, 57}:
            sql_text = re.sub(r"\[SELECTONE\]\s*AS\s*,", "[SELECTONE],", sql_text)
            sql_text = re.sub(r"AS\s*,\s*v1\.", ", v1.", sql_text)
            if query_id == 47:
                sql_text = sql_text.replace(
                    "obt.item_i_category,\n    obt.item_i_brand,\n    obt.store_s_store_name,\n    obt.store_s_company_name,\n    obt.sold_date_d_year,\n    obt.sold_date_d_moy,",
                    "obt.item_i_category AS i_category,\n    obt.item_i_brand AS i_brand,\n    obt.store_s_store_name AS s_store_name,\n    obt.store_s_company_name AS s_company_name,\n    obt.sold_date_d_year AS d_year,\n    obt.sold_date_d_moy AS d_moy,",
                )
                sql_text = sql_text.replace(
                    "GROUP BY\n    obt.item_i_category AS i_category,\n    obt.item_i_brand AS i_brand,\n    obt.store_s_store_name AS s_store_name,\n    obt.store_s_company_name AS s_company_name,\n    obt.sold_date_d_year AS d_year,\n    obt.sold_date_d_moy AS d_moy",
                    "GROUP BY\n    obt.item_i_category,\n    obt.item_i_brand,\n    obt.store_s_store_name,\n    obt.store_s_company_name,\n    obt.sold_date_d_year,\n    obt.sold_date_d_moy",
                )
                sql_text = re.sub(r"WHERE\n  sold_date_d_year = (\S+)", r"WHERE\n  d_year = \1", sql_text)
            if query_id == 57:
                sql_text = sql_text.replace(
                    "SELECT\n    obt.item_i_category,\n    obt.item_i_brand,\n    obt.call_center_cc_name,\n    obt.sold_date_d_year,\n    obt.sold_date_d_moy,",
                    "SELECT\n    obt.item_i_category AS i_category,\n    obt.item_i_brand AS i_brand,\n    obt.call_center_cc_name AS cc_name,\n    obt.sold_date_d_year AS d_year,\n    obt.sold_date_d_moy AS d_moy,",
                )
                sql_text = sql_text.replace(
                    "GROUP BY\n    obt.item_i_category AS i_category,",
                    "GROUP BY\n    obt.item_i_category,",
                )
                sql_text = re.sub(r"WHERE\n  sold_date_d_year = (\S+)", r"WHERE\n  d_year = \1", sql_text)

        if query_id == 49:
            sql_text = sql_text.replace("wr.wr_order_number", "obt.sale_id")
            sql_text = sql_text.replace("wr.wr_item_sk", "obt.item_sk")
            sql_text = sql_text.replace("cr.cr_order_number", "obt.sale_id")
            sql_text = sql_text.replace("cr.cr_item_sk", "obt.item_sk")

        if query_id == 51:
            sql_text = sql_text.replace(
                "CASE WHEN NOT item_sk IS NULL THEN item_sk ELSE item_sk END AS item_sk",
                "COALESCE(web.item_sk, store.item_sk) AS item_sk",
            )
            sql_text = sql_text.replace(
                "CASE WHEN item_sk IS NOT NULL THEN item_sk ELSE item_sk END AS item_sk",
                "COALESCE(web.item_sk, store.item_sk) AS item_sk",
            )
            sql_text = sql_text.replace(
                "item_sk = item_sk AND web.d_date = store.d_date",
                "web.item_sk = store.item_sk AND web.d_date = store.d_date",
            )
            sql_text = sql_text.replace(
                "obt.sold_date_d_date,\n    SUM(SUM(",
                "obt.sold_date_d_date AS d_date,\n    SUM(SUM(",
            )
            sql_text = sql_text.replace(
                "SELECT\n    item_sk,\n    sold_date_d_date,",
                "SELECT\n    item_sk,\n    d_date,",
            )
            sql_text = sql_text.replace(
                "ORDER BY sold_date_d_date\n      rows",
                "ORDER BY d_date\n      rows",
            )
            sql_text = sql_text.replace(
                "ORDER BY\n  item_sk,\n  sold_date_d_date",
                "ORDER BY\n  item_sk,\n  d_date",
            )

        return sql_text

    def _post_process_fix_4(self, query_id: int, sql_text: str) -> str:
        if query_id == 58:
            sql_text = re.sub(
                r"(SELECT\s+)sold_date_d_week_seq(\s+FROM tpcds_sales_returns_obt AS obt\s+WHERE\s+sold_date_d_date =)",
                r"\1MAX(sold_date_d_week_seq)\2",
                sql_text,
            )
            sql_text = sql_text.replace(
                "obt.sold_date_d_week_seq = (\n            SELECT\n              obt.sold_date_d_week_seq\n            FROM tpcds_sales_returns_obt AS obt\n            WHERE\n              obt.sold_date_d_date =",
                "obt.sold_date_d_week_seq = (\n            SELECT\n              MAX(obt.sold_date_d_week_seq)\n            FROM tpcds_sales_returns_obt AS obt\n            WHERE\n              obt.sold_date_d_date =",
            )
            sql_text = sql_text.replace(
                "ss_items.item_id = ws_items.item_id\n  AND ss_items.item_id = ws_items.item_id",
                "ss_items.item_id = cs_items.item_id\n  AND ss_items.item_id = ws_items.item_id",
            )
            sql_text = sql_text.replace(
                "ORDER BY\n  item_id,",
                "ORDER BY\n  ss_items.item_id,",
            )

        if query_id == 59:
            month_seq = re.search(r"sold_date_d_month_seq BETWEEN (\S+) AND", sql_text)
            dms = month_seq.group(1) if month_seq else ""
            sql_text = sql_text.replace(
                "  SELECT\n    obt.sold_date_d_week_seq,\n    obt.store_sk,",
                "  SELECT\n    obt.sold_date_d_week_seq,\n    obt.store_sk,\n    obt.store_s_store_name,\n    obt.store_s_store_id,\n    obt.sold_date_d_month_seq,",
            )
            sql_text = sql_text.replace(
                "  GROUP BY\n    obt.sold_date_d_week_seq,\n    obt.store_sk",
                "  GROUP BY\n    obt.sold_date_d_week_seq,\n    obt.store_sk,\n    obt.store_s_store_name,\n    obt.store_s_store_id,\n    obt.sold_date_d_month_seq",
            )

            sql_text = sql_text.replace(
                f"""FROM (
  SELECT
    obt.store_s_store_name AS s_store_name1,
    wss.d_week_seq AS d_week_seq1,
    obt.store_s_store_id AS s_store_id1,
    sun_sales AS sun_sales1,
    mon_sales AS mon_sales1,
    tue_sales AS tue_sales1,
    wed_sales AS wed_sales1,
    thu_sales AS thu_sales1,
    fri_sales AS fri_sales1,
    sat_sales AS sat_sales1
  FROM tpcds_sales_returns_obt AS obt
  CROSS JOIN wss AS wss
  WHERE
    (
      obt.sold_date_d_week_seq = wss.d_week_seq
      AND obt.store_sk = obt.store_s_store_sk
      AND obt.sold_date_d_month_seq BETWEEN {dms} AND {dms} + 11
    )
    AND channel = 'store'
) AS y, (""",
                f"""FROM (
  SELECT
    wss.store_s_store_name AS s_store_name1,
    wss.sold_date_d_week_seq AS d_week_seq1,
    wss.store_s_store_id AS s_store_id1,
    sun_sales AS sun_sales1,
    mon_sales AS mon_sales1,
    tue_sales AS tue_sales1,
    wed_sales AS wed_sales1,
    thu_sales AS thu_sales1,
    fri_sales AS fri_sales1,
    sat_sales AS sat_sales1
  FROM wss
  WHERE
    wss.sold_date_d_month_seq BETWEEN {dms} AND {dms} + 11
) AS y, (""",
            )

            sql_text = sql_text.replace(
                f"""  SELECT
    obt.store_s_store_name AS s_store_name2,
    wss.d_week_seq AS d_week_seq2,
    obt.store_s_store_id AS s_store_id2,
    sun_sales AS sun_sales2,
    mon_sales AS mon_sales2,
    tue_sales AS tue_sales2,
    wed_sales AS wed_sales2,
    thu_sales AS thu_sales2,
    fri_sales AS fri_sales2,
    sat_sales AS sat_sales2
  FROM tpcds_sales_returns_obt AS obt
  CROSS JOIN wss AS wss
  WHERE
    (
      obt.sold_date_d_week_seq = wss.d_week_seq
      AND obt.store_sk = obt.store_s_store_sk
      AND obt.sold_date_d_month_seq BETWEEN {dms} + 12 AND {dms} + 23
    )
    AND channel = 'store'
) AS x""",
                f"""  SELECT
    wss.store_s_store_name AS s_store_name2,
    wss.sold_date_d_week_seq AS d_week_seq2,
    wss.store_s_store_id AS s_store_id2,
    sun_sales AS sun_sales2,
    mon_sales AS mon_sales2,
    tue_sales AS tue_sales2,
    wed_sales AS wed_sales2,
    thu_sales AS thu_sales2,
    fri_sales AS fri_sales2,
    sat_sales AS sat_sales2
  FROM wss
  WHERE
    wss.sold_date_d_month_seq BETWEEN {dms} + 12 AND {dms} + 23
) AS x""",
            )
            sql_text = sql_text.replace(
                "SELECT\n  store_s_store_name,\n  store_s_store_id,",
                "SELECT\n  s_store_name1 AS store_s_store_name,\n  s_store_id1 AS store_s_store_id,",
            )
            sql_text = sql_text.replace(
                "WHERE\n  store_s_store_id = store_s_store_id AND d_week_seq1 = d_week_seq2 - 52",
                "WHERE\n  s_store_id1 = s_store_id2 AND d_week_seq1 = d_week_seq2 - 52",
            )

        if query_id == 65:
            sql_text = sql_text.replace("sb.ss_store_sk", "sb.store_sk")
            sql_text = sql_text.replace("sc.ss_store_sk", "sc.store_sk")
            sql_text = sql_text.replace("sc.ss_item_sk", "sc.item_sk")
            sql_text = sql_text.replace(
                "obt.store_sk,\n    obt.item_sk,",
                "obt.store_sk AS store_sk,\n    obt.item_sk AS item_sk,",
            )

        if query_id == 73:
            sql_text = sql_text.replace(
                "SELECT\n  bill_customer_c_last_name,\n  bill_customer_c_first_name,\n  bill_customer_c_salutation,\n  bill_customer_c_preferred_cust_flag,\n  sale_id,\n  cnt",
                "SELECT\n  bill_customer_c_last_name,\n  bill_customer_c_first_name,\n  bill_customer_c_salutation,\n  bill_customer_c_preferred_cust_flag,\n  dj.sale_id,\n  cnt",
            )
            sql_text = sql_text.replace(
                "ship_customer_sk = bill_customer_c_customer_sk",
                "dj.ship_customer_sk = obt.bill_customer_c_customer_sk",
            )

        if query_id == 66:
            sql_text = sql_text.replace("t_time_sk", "sold_time_t_time_sk")
            sql_text = sql_text.replace("sold_time_sold_time_t_time_sk", "sold_time_t_time_sk")
            sql_text = sql_text.replace("t_time", "sold_time_t_time")
            sql_text = sql_text.replace("sold_time_sold_time_t_time", "sold_time_t_time")

        if query_id == 71:
            sql_text = sql_text.replace("t_time_sk", "sold_time_t_time_sk")
            sql_text = sql_text.replace("sold_time_sold_time_t_time_sk", "sold_time_t_time_sk")
            sql_text = sql_text.replace("t_meal_time", "sold_time_t_meal_time")
            sql_text = sql_text.replace("t_hour", "sold_time_t_hour")
            sql_text = sql_text.replace("sold_time_sold_time_t_hour", "sold_time_t_hour")
            sql_text = sql_text.replace("t_minute", "sold_time_t_minute")
            sql_text = sql_text.replace("sold_time_sold_time_t_minute", "sold_time_t_minute")

        return sql_text

    def _post_process_fix_5(self, query_id: int, sql_text: str) -> str:
        if query_id == 75:
            sql_text = sql_text.replace(
                "WITH all_sales AS (\n  SELECT\n    sold_date_d_year,\n    item_i_brand_id,\n    item_i_class_id,\n    item_i_category_id,\n    item_i_manufact_id,",
                "WITH all_sales AS (\n  SELECT\n    sold_date_d_year AS d_year,\n    item_i_brand_id AS i_brand_id,\n    item_i_class_id AS i_class_id,\n    item_i_category_id AS i_category_id,\n    item_i_manufact_id AS i_manufact_id,",
            )
            sql_text = sql_text.replace(
                "GROUP BY\n    sold_date_d_year,\n    item_i_brand_id,\n    item_i_class_id,\n    item_i_category_id,\n    item_i_manufact_id",
                "GROUP BY\n    d_year,\n    i_brand_id,\n    i_class_id,\n    i_category_id,\n    i_manufact_id",
            )

        if query_id == 77:
            sql_text = sql_text.replace(
                "WITH ss AS (\n  SELECT\n    obt.store_s_store_sk,",
                "WITH ss AS (\n  SELECT\n    obt.store_s_store_sk AS s_store_sk,",
            )
            sql_text = sql_text.replace(
                "), sr AS (\n  SELECT\n    obt.store_s_store_sk,",
                "), sr AS (\n  SELECT\n    obt.store_s_store_sk AS s_store_sk,",
            )
            sql_text = sql_text.replace(
                "obt.store_s_store_sk AS store_sk,\n    SUM(obt.return_amount)",
                "obt.store_s_store_sk AS s_store_sk,\n    SUM(obt.return_amount)",
            )
            sql_text = sql_text.replace(
                "), ws AS (\n  SELECT\n    obt.web_page_wp_web_page_sk,",
                "), ws AS (\n  SELECT\n    obt.web_page_wp_web_page_sk AS wp_web_page_sk,",
            )
            sql_text = sql_text.replace(
                "), wr AS (\n  SELECT\n    obt.web_page_wp_web_page_sk,",
                "), wr AS (\n  SELECT\n    obt.web_page_wp_web_page_sk AS wp_web_page_sk,",
            )
            sql_text = sql_text.replace(
                "'catalog channel' AS channel,\n    call_center_sk AS id,",
                "'catalog channel' AS channel,\n    cs.call_center_sk AS id,",
            )

        if query_id == 78:
            sql_text = sql_text.replace(
                "ws_sold_year = ss_sold_year\n    AND item_sk = item_sk\n    AND ws_customer_sk",
                "ws_sold_year = ss_sold_year\n    AND ws.item_sk = ss.item_sk\n    AND ws_customer_sk",
            )
            sql_text = sql_text.replace(
                "cs_sold_year = ss_sold_year\n    AND item_sk = item_sk\n    AND cs_customer_sk",
                "cs_sold_year = ss_sold_year\n    AND ss.item_sk = cs.item_sk\n    AND cs_customer_sk",
            )
            sql_text = sql_text.replace(
                "ws_sold_year = ss_sold_year\n      AND item_sk = item_sk\n      AND ws_customer_sk",
                "ws_sold_year = ss_sold_year\n      AND ws.item_sk = ss.item_sk\n      AND ws_customer_sk",
            )
            sql_text = sql_text.replace(
                "cs_sold_year = ss_sold_year\n    AND item_sk = item_sk\n    AND cs_customer_sk",
                "cs_sold_year = ss_sold_year\n    AND cs.item_sk = ss.item_sk\n    AND cs_customer_sk",
            )

        if query_id == 90:
            sql_text = sql_text.replace(") AS at,", ") AS am_count,")
            sql_text = sql_text.replace("at.", "am_count.")

        if query_id == 94:
            sql_text = sql_text.replace(
                "obt.sale_id = wr1.wr_order_number AND channel = 'web'",
                "obt.sale_id = obt.sale_id AND obt.has_return = 'Y' AND obt.channel = 'web'",
            )

        if query_id == 95:
            sql_text = sql_text.replace(
                "obt.sale_id = ws_wh.ws_order_number AND channel = 'web'",
                "obt.sale_id = obt.sale_id AND obt.has_return = 'Y' AND obt.channel = 'web'",
            )

        if query_id == 70:
            sql_text = sql_text.replace(
                "SELECT\n        store_s_state\n      FROM (\n        SELECT\n          obt.store_s_state AS s_state",
                "SELECT\n        s_state\n      FROM (\n        SELECT\n          obt.store_s_state AS s_state",
            )

        if query_id == 97:
            sql_text = sql_text.replace(
                "ssci.customer_sk = csci.customer_sk AND item_sk = item_sk",
                "ssci.customer_sk = csci.customer_sk AND ssci.item_sk = csci.item_sk",
            )

        return sql_text

    @staticmethod
    def _param_pattern(name: str) -> re.Pattern[str]:
        return re.compile(rf"\[{re.escape(name)}(?:\.(\d+))?\]")


__all__ = [
    "BLOCKED_QUERY_IDS",
    "ColumnMapper",
    "ConvertedQuery",
    "QueryConverter",
    "TemplateLoader",
    "TemplateParameter",
]
