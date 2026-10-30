import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from benchbox.core.datavault.etl.hash_functions import (
    generate_hash_key_sql,
    generate_hashdiff_sql,
)
from benchbox.core.datavault.schema import LOADING_ORDER, TABLES_BY_NAME
from benchbox.utils.compression_mixin import CompressionMixin
from benchbox.utils.file_format import detect_compression

logger = logging.getLogger(__name__)


class DataVaultETLTransformer(CompressionMixin):
    TPCH_COLUMNS = {
        "region": ["r_regionkey", "r_name", "r_comment"],
        "nation": ["n_nationkey", "n_name", "n_regionkey", "n_comment"],
        "customer": [
            "c_custkey",
            "c_name",
            "c_address",
            "c_nationkey",
            "c_phone",
            "c_acctbal",
            "c_mktsegment",
            "c_comment",
        ],
        "supplier": [
            "s_suppkey",
            "s_name",
            "s_address",
            "s_nationkey",
            "s_phone",
            "s_acctbal",
            "s_comment",
        ],
        "part": [
            "p_partkey",
            "p_name",
            "p_mfgr",
            "p_brand",
            "p_type",
            "p_size",
            "p_container",
            "p_retailprice",
            "p_comment",
        ],
        "partsupp": [
            "ps_partkey",
            "ps_suppkey",
            "ps_availqty",
            "ps_supplycost",
            "ps_comment",
        ],
        "orders": [
            "o_orderkey",
            "o_custkey",
            "o_orderstatus",
            "o_totalprice",
            "o_orderdate",
            "o_orderpriority",
            "o_clerk",
            "o_shippriority",
            "o_comment",
        ],
        "lineitem": [
            "l_orderkey",
            "l_partkey",
            "l_suppkey",
            "l_linenumber",
            "l_quantity",
            "l_extendedprice",
            "l_discount",
            "l_tax",
            "l_returnflag",
            "l_linestatus",
            "l_shipdate",
            "l_commitdate",
            "l_receiptdate",
            "l_shipinstruct",
            "l_shipmode",
            "l_comment",
        ],
    }

    def __init__(
        self,
        scale_factor: float = 1.0,
        hash_algorithm: str = "md5",
        record_source: str = "TPCH",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)

        from benchbox.core.datavault.benchmark import DataVaultBenchmark

        if hash_algorithm not in DataVaultBenchmark.SUPPORTED_HASH_ALGORITHMS:
            raise ValueError(
                f"Unsupported hash algorithm: '{hash_algorithm}'. "
                f"Supported algorithms: {DataVaultBenchmark.SUPPORTED_HASH_ALGORITHMS}."
            )

        self.scale_factor = scale_factor
        self.hash_algorithm = hash_algorithm
        self.record_source = record_source

    def _get_required_sources(self, tables: Optional[list[str]]) -> set[str]:
        if not tables:
            return set(self.TPCH_COLUMNS.keys())

        required: set[str] = set()

        hub_sources = {
            "hub_region": {"region"},
            "hub_nation": {"nation"},
            "hub_customer": {"customer"},
            "hub_supplier": {"supplier"},
            "hub_part": {"part"},
            "hub_order": {"orders"},
            "hub_lineitem": {"lineitem"},
        }
        link_sources = {
            "link_nation_region": {"nation"},
            "link_customer_nation": {"customer"},
            "link_supplier_nation": {"supplier"},
            "link_part_supplier": {"partsupp"},
            "link_order_customer": {"orders"},
            "link_lineitem": {"lineitem"},
        }
        sat_sources = {
            "sat_region": {"region"},
            "sat_nation": {"nation"},
            "sat_customer": {"customer"},
            "sat_supplier": {"supplier"},
            "sat_part": {"part"},
            "sat_partsupp": {"partsupp"},
            "sat_order": {"orders"},
            "sat_lineitem": {"lineitem"},
        }

        for table in tables:
            required.update(hub_sources.get(table, set()))
            required.update(link_sources.get(table, set()))
            required.update(sat_sources.get(table, set()))

        return required

    def transform(
        self,
        tpch_dir: Path,
        output_dir: Path,
        tables: Optional[list[str]] = None,
        load_timestamp: Optional[datetime] = None,
        output_format: str = "tbl",
    ) -> dict[str, Path]:
        import duckdb

        load_dts = load_timestamp or datetime.now()
        tables_to_generate = tables or LOADING_ORDER
        table_counts: dict[str, int] = {}

        logger.info("Transforming TPC-H data to Data Vault format")
        logger.info(f"  Source directory: {tpch_dir}")
        logger.info(f"  Output directory: {output_dir}")
        logger.info(f"  Tables to generate: {len(tables_to_generate)}")

        output_dir.mkdir(parents=True, exist_ok=True)

        conn = duckdb.connect(":memory:")

        try:
            self._load_tpch_tables(conn, tpch_dir, tables=tables_to_generate)

            output_files = {}
            for table_name in tables_to_generate:
                if table_name not in TABLES_BY_NAME:
                    logger.warning(f"Unknown table: {table_name}, skipping")
                    continue

                output_path = self._generate_dv_table(
                    conn,
                    table_name,
                    load_dts,
                    output_dir=output_dir,
                    output_format=output_format,
                )
                output_files[table_name] = output_path
                count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                table_counts[table_name] = count
                logger.debug(f"Generated {table_name}")

            if self.should_use_compression():
                logger.info(f"Compressing output files with {self.compression_type}")
                compressed_files: dict[str, Path] = {}
                for table_name, file_path in output_files.items():
                    compressed_path = self.compress_existing_file(file_path, remove_original=True)
                    compressed_files[table_name] = compressed_path
                    logger.debug(f"Compressed {file_path.name} -> {compressed_path.name}")
                output_files = compressed_files

            self._write_manifest(
                output_dir=output_dir,
                table_paths=output_files,
                table_row_counts=table_counts,
                output_format=output_format,
                load_timestamp=load_dts,
            )

            logger.info(f"Data Vault transformation complete: {len(output_files)} tables")
            return output_files

        finally:
            conn.close()

    def _find_tpch_file_pattern(self, tpch_dir: Path, table_name: str) -> str:
        base_filename = f"{table_name}.tbl"

        for ext in [".zst", ".gz"]:
            pattern = f"{base_filename}.*{ext}"
            matches = list(tpch_dir.glob(pattern))
            sharded_matches = [m for m in matches if m.name.replace(ext, "").split(".")[-1].isdigit()]
            if sharded_matches:
                logger.debug(f"Found {len(sharded_matches)} compressed sharded files for {table_name}")
                return str(tpch_dir / pattern)

        for ext in [".zst", ".gz"]:
            compressed_path = tpch_dir / f"{base_filename}{ext}"
            if compressed_path.exists():
                logger.debug(f"Found compressed single file: {compressed_path.name}")
                return str(compressed_path)

        sharded_pattern = f"{base_filename}.*"
        sharded_matches = [m for m in tpch_dir.glob(sharded_pattern) if m.name.split(".")[-1].isdigit()]
        if sharded_matches:
            logger.debug(f"Found {len(sharded_matches)} uncompressed sharded files for {table_name}")
            return str(tpch_dir / sharded_pattern)

        single_path = tpch_dir / base_filename
        if single_path.exists():
            logger.debug(f"Found single uncompressed file: {single_path.name}")
            return str(single_path)

        raise FileNotFoundError(
            f"TPC-H file not found: {table_name}.tbl in {tpch_dir}. "
            f"Searched for patterns: {base_filename}, {base_filename}.zst, "
            f"{base_filename}.gz, {base_filename}.*, {base_filename}.*.zst, {base_filename}.*.gz"
        )

    def _load_tpch_tables(self, conn: Any, tpch_dir: Path, tables: Optional[list[str]] = None) -> None:
        required_sources = self._get_required_sources(tables)

        for table_name, columns in self.TPCH_COLUMNS.items():
            if required_sources and table_name not in required_sources:
                continue

            file_pattern = self._find_tpch_file_pattern(tpch_dir, table_name)
            parallel = "false" if detect_compression(file_pattern) else "true"

            sql = f"""
                CREATE TABLE {table_name} AS
                SELECT * FROM read_csv(
                    '{file_pattern}',
                    delim='|',
                    header=false,
                    names={columns},
                    parallel={parallel}
                )
            """
            conn.execute(sql)
            logger.debug(f"Loaded TPC-H table: {table_name}")

    def _generate_dv_table(
        self,
        conn: Any,
        table_name: str,
        load_dts: datetime,
        output_dir: Path,
        output_format: str = "tbl",
    ) -> Path:
        table = TABLES_BY_NAME[table_name]
        load_dts_str = load_dts.strftime("%Y-%m-%d %H:%M:%S")

        if table.table_type == "hub":
            sql = self._get_hub_sql(table_name, load_dts_str)
        elif table.table_type == "link":
            sql = self._get_link_sql(table_name, load_dts_str)
        elif table.table_type == "satellite":
            sql = self._get_satellite_sql(table_name, load_dts_str)
        else:
            raise ValueError(f"Unknown table type: {table.table_type}")

        conn.execute(f"CREATE TABLE {table_name} AS {sql}")

        fmt = (output_format or "tbl").lower()
        if fmt not in {"csv", "tbl"}:
            raise ValueError(f"Unsupported output_format: {output_format}")
        delimiter = "," if fmt == "csv" else "|"
        extension = "csv" if fmt == "csv" else "tbl"

        output_path = output_dir / f"{table_name}.{extension}"
        conn.execute(f"COPY {table_name} TO '{output_path}' (DELIMITER '{delimiter}', HEADER false)")
        return output_path

    def _write_manifest(
        self,
        output_dir: Path,
        table_paths: dict[str, Path],
        table_row_counts: dict[str, int],
        output_format: str,
        load_timestamp: datetime,
    ) -> None:
        from benchbox.utils.datagen_manifest import (
            DataGenerationManifest,
            require_manifest_files,
            resolve_compression_metadata,
        )

        manifest = DataGenerationManifest(
            output_dir=output_dir,
            benchmark="datavault",
            scale_factor=self.scale_factor,
            compression=resolve_compression_metadata(self),
            formats=[output_format],
            extra_metadata={
                "hash_algorithm": self.hash_algorithm,
                "record_source": self.record_source,
                "load_timestamp": load_timestamp.isoformat(),
            },
        )

        dialect_metadata = {
            "csv_delimiter": "," if (output_format or "tbl").lower() == "csv" else "|",
            "csv_has_header": False,
            "csv_null_marker": "",
            "csv_normalize_booleans": False,
            "csv_quote": '"',
        }
        for name, path in table_paths.items():
            p = Path(path)
            manifest.add_entry(
                table_name=name,
                file_path=p,
                row_count=int(table_row_counts.get(name, 0)),
                size_bytes=p.stat().st_size if p.exists() else 0,
                format=output_format,
                metadata=dialect_metadata,
            )

        require_manifest_files(manifest.file_counts()[1], label="Data Vault", output_dir=output_dir)
        manifest.write()

    def _get_hub_sql(self, table_name: str, load_dts: str) -> str:
        hub_configs = {
            "hub_region": {
                "source": "region",
                "bk_col": "r_regionkey",
                "hk_col": "hk_region",
            },
            "hub_nation": {
                "source": "nation",
                "bk_col": "n_nationkey",
                "hk_col": "hk_nation",
            },
            "hub_customer": {
                "source": "customer",
                "bk_col": "c_custkey",
                "hk_col": "hk_customer",
            },
            "hub_supplier": {
                "source": "supplier",
                "bk_col": "s_suppkey",
                "hk_col": "hk_supplier",
            },
            "hub_part": {
                "source": "part",
                "bk_col": "p_partkey",
                "hk_col": "hk_part",
            },
            "hub_order": {
                "source": "orders",
                "bk_col": "o_orderkey",
                "hk_col": "hk_order",
            },
            "hub_lineitem": {
                "source": "lineitem",
                "bk_cols": ["l_orderkey", "l_linenumber"],
                "hk_col": "hk_lineitem",
            },
        }

        config = hub_configs[table_name]
        source = config["source"]

        if "bk_cols" in config:
            bk_cols = config["bk_cols"]
            hk_expr = generate_hash_key_sql(*bk_cols, algorithm=self.hash_algorithm)
            bk_select = ", ".join(bk_cols)
        else:
            bk_col = config["bk_col"]
            hk_expr = generate_hash_key_sql(bk_col, algorithm=self.hash_algorithm)
            bk_select = bk_col

        return f"""
            SELECT DISTINCT
                {hk_expr} AS {config["hk_col"]},
                {bk_select},
                TIMESTAMP '{load_dts}' AS load_dts,
                '{self.record_source}' AS record_source
            FROM {source}
        """

    def _get_link_sql(self, table_name: str, load_dts: str) -> str:
        link_configs = {
            "link_nation_region": {
                "source": "nation",
                "hk_cols": ["n_nationkey", "n_regionkey"],
                "hub_hks": [
                    ("hk_nation", "n_nationkey"),
                    ("hk_region", "n_regionkey"),
                ],
            },
            "link_customer_nation": {
                "source": "customer",
                "hk_cols": ["c_custkey", "c_nationkey"],
                "hub_hks": [
                    ("hk_customer", "c_custkey"),
                    ("hk_nation", "c_nationkey"),
                ],
            },
            "link_supplier_nation": {
                "source": "supplier",
                "hk_cols": ["s_suppkey", "s_nationkey"],
                "hub_hks": [
                    ("hk_supplier", "s_suppkey"),
                    ("hk_nation", "s_nationkey"),
                ],
            },
            "link_part_supplier": {
                "source": "partsupp",
                "hk_cols": ["ps_partkey", "ps_suppkey"],
                "hub_hks": [
                    ("hk_part", "ps_partkey"),
                    ("hk_supplier", "ps_suppkey"),
                ],
            },
            "link_order_customer": {
                "source": "orders",
                "hk_cols": ["o_orderkey", "o_custkey"],
                "hub_hks": [
                    ("hk_order", "o_orderkey"),
                    ("hk_customer", "o_custkey"),
                ],
            },
            "link_lineitem": {
                "source": "lineitem",
                "hk_cols": ["l_orderkey", "l_linenumber", "l_partkey", "l_suppkey"],
                "hub_hks": [
                    ("hk_lineitem", ["l_orderkey", "l_linenumber"]),
                    ("hk_order", "l_orderkey"),
                    ("hk_part", "l_partkey"),
                    ("hk_supplier", "l_suppkey"),
                ],
            },
        }

        config = link_configs[table_name]
        source = config["source"]

        link_hk_expr = generate_hash_key_sql(*config["hk_cols"], algorithm=self.hash_algorithm)
        link_hk_name = "hk_lineitem_link" if table_name == "link_lineitem" else f"hk_{table_name.replace('link_', '')}"

        hub_hk_exprs = []
        for hk_name, cols in config["hub_hks"]:
            if isinstance(cols, list):
                expr = generate_hash_key_sql(*cols, algorithm=self.hash_algorithm)
            else:
                expr = generate_hash_key_sql(cols, algorithm=self.hash_algorithm)
            hub_hk_exprs.append(f"{expr} AS {hk_name}")

        hub_hks_select = ",\n                ".join(hub_hk_exprs)

        return f"""
            SELECT DISTINCT
                {link_hk_expr} AS {link_hk_name},
                {hub_hks_select},
                TIMESTAMP '{load_dts}' AS load_dts,
                '{self.record_source}' AS record_source
            FROM {source}
        """

    def _get_satellite_sql(self, table_name: str, load_dts: str) -> str:
        sat_configs = {
            "sat_region": {
                "source": "region",
                "hk_col": "hk_region",
                "hk_source": "r_regionkey",
                "attrs": ["r_name", "r_comment"],
            },
            "sat_nation": {
                "source": "nation",
                "hk_col": "hk_nation",
                "hk_source": "n_nationkey",
                "attrs": ["n_name", "n_comment"],
            },
            "sat_customer": {
                "source": "customer",
                "hk_col": "hk_customer",
                "hk_source": "c_custkey",
                "attrs": ["c_name", "c_address", "c_phone", "c_acctbal", "c_mktsegment", "c_comment"],
            },
            "sat_supplier": {
                "source": "supplier",
                "hk_col": "hk_supplier",
                "hk_source": "s_suppkey",
                "attrs": ["s_name", "s_address", "s_phone", "s_acctbal", "s_comment"],
            },
            "sat_part": {
                "source": "part",
                "hk_col": "hk_part",
                "hk_source": "p_partkey",
                "attrs": [
                    "p_name",
                    "p_mfgr",
                    "p_brand",
                    "p_type",
                    "p_size",
                    "p_container",
                    "p_retailprice",
                    "p_comment",
                ],
            },
            "sat_partsupp": {
                "source": "partsupp",
                "hk_col": "hk_part_supplier",
                "hk_source": ["ps_partkey", "ps_suppkey"],
                "attrs": ["ps_availqty", "ps_supplycost", "ps_comment"],
            },
            "sat_order": {
                "source": "orders",
                "hk_col": "hk_order",
                "hk_source": "o_orderkey",
                "attrs": [
                    "o_orderstatus",
                    "o_totalprice",
                    "o_orderdate",
                    "o_orderpriority",
                    "o_clerk",
                    "o_shippriority",
                    "o_comment",
                ],
            },
            "sat_lineitem": {
                "source": "lineitem",
                "hk_col": "hk_lineitem_link",
                "hk_source": ["l_orderkey", "l_linenumber", "l_partkey", "l_suppkey"],
                "attrs": [
                    "l_quantity",
                    "l_extendedprice",
                    "l_discount",
                    "l_tax",
                    "l_returnflag",
                    "l_linestatus",
                    "l_shipdate",
                    "l_commitdate",
                    "l_receiptdate",
                    "l_shipinstruct",
                    "l_shipmode",
                    "l_comment",
                ],
            },
        }

        config = sat_configs[table_name]
        source = config["source"]
        attrs = config["attrs"]

        hk_source = config["hk_source"]
        if isinstance(hk_source, list):
            hk_expr = generate_hash_key_sql(*hk_source, algorithm=self.hash_algorithm)
        else:
            hk_expr = generate_hash_key_sql(hk_source, algorithm=self.hash_algorithm)

        hashdiff_expr = generate_hashdiff_sql(*attrs, algorithm=self.hash_algorithm)

        attrs_select = ",\n                ".join(attrs)

        return f"""
            SELECT
                {hk_expr} AS {config["hk_col"]},
                TIMESTAMP '{load_dts}' AS load_dts,
                NULL::TIMESTAMP AS load_end_dts,
                '{self.record_source}' AS record_source,
                {hashdiff_expr} AS hashdiff,
                {attrs_select}
            FROM {source}
        """
