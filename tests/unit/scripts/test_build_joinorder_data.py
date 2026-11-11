from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_script():
    name = "build_joinorder_data"
    path = REPO_ROOT / "_project" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


build_joinorder_data = _load_script()


def test_utc_now_iso_uses_utc_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDatetime:
        @staticmethod
        def now(tz: dt.tzinfo) -> dt.datetime:
            assert tz is dt.timezone.utc
            return dt.datetime(2026, 5, 11, 3, 22, 11, 123456, tzinfo=tz)

    class FakeDatetimeModule:
        datetime = FakeDatetime
        timezone = dt.timezone

    monkeypatch.setattr(build_joinorder_data, "dt", FakeDatetimeModule)

    assert build_joinorder_data.utc_now_iso() == "2026-05-11T03:22:11Z"


def test_tomllib_uses_python311_standard_library() -> None:

    script_path = REPO_ROOT / "_project" / "scripts" / "build_joinorder_data.py"
    text = script_path.read_text(encoding="utf-8")
    assert "import tomllib" in text
    assert "import tomli" not in text


def test_container_cli_defaults_to_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BENCHBOX_CONTAINER_CLI", raising=False)
    assert build_joinorder_data.container_cli() == "docker"


def test_container_cli_honours_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHBOX_CONTAINER_CLI", "mocker")
    assert build_joinorder_data.container_cli() == "mocker"


def _metadata(files: list[dict]) -> dict:
    return {"data": {"latestVersion": {"files": files}}}


def test_select_pgdump_file_extracts_dataverse_download_metadata() -> None:
    metadata = _metadata(
        [
            {
                "label": "imdb_pg11",
                "dataFile": {
                    "id": 3590041,
                    "filesize": 1_277_543_282,
                    "checksum": {
                        "type": "MD5",
                        "value": "df3e976b235288005cb410cea09a115f",
                    },
                },
            }
        ]
    )

    dataverse_file = build_joinorder_data.select_pgdump_file(metadata)

    assert dataverse_file.file_id == 3590041
    assert dataverse_file.label == "imdb_pg11"
    assert dataverse_file.filesize == 1_277_543_282
    assert dataverse_file.checksum_type == "MD5"
    assert dataverse_file.download_url.endswith("/api/access/datafile/3590041")


def test_select_pgdump_file_rejects_ambiguous_metadata() -> None:
    metadata = _metadata(
        [
            {
                "label": "imdb_pg11",
                "dataFile": {"id": 1, "filesize": 10, "checksum": {"type": "MD5", "value": "a"}},
            },
            {
                "label": "imdb_pg11",
                "dataFile": {"id": 2, "filesize": 10, "checksum": {"type": "MD5", "value": "b"}},
            },
        ]
    )

    with pytest.raises(build_joinorder_data.DataverseMetadataError, match="exactly one"):
        build_joinorder_data.select_pgdump_file(metadata)


def test_validate_row_counts_accepts_one_percent_edge() -> None:
    row_counts = dict(build_joinorder_data.EXPECTED_ROW_COUNTS)
    expected = build_joinorder_data.EXPECTED_ROW_COUNTS["movie_info"]
    row_counts["movie_info"] = expected + int(expected * 0.01)

    validation = build_joinorder_data.validate_row_counts(row_counts)

    assert validation.ok
    assert validation.row_count_failures == []


def test_validate_row_counts_reports_missing_and_out_of_tolerance_tables() -> None:
    row_counts = dict(build_joinorder_data.EXPECTED_ROW_COUNTS)
    row_counts.pop("aka_title")
    row_counts["cast_info"] = build_joinorder_data.EXPECTED_ROW_COUNTS["cast_info"] + 400_000

    validation = build_joinorder_data.validate_row_counts(row_counts, unexpected_tables=["extra_table"])

    assert not validation.ok
    assert validation.missing_tables == ["aka_title"]
    assert validation.unexpected_tables == ["extra_table"]
    assert [(failure.table, failure.expected, failure.actual) for failure in validation.row_count_failures] == [
        ("cast_info", 36_244_344, 36_644_344)
    ]


def test_validate_pgdump_file_requires_custom_format_magic(tmp_path: Path) -> None:
    dump_path = tmp_path / "imdb_pg11"
    dump_path.write_bytes(b"not-a-pgdump")
    sha256, md5, size = build_joinorder_data.hash_file(dump_path)
    dataverse_file = build_joinorder_data.DataverseFile(
        file_id=3590041,
        label="imdb_pg11",
        filesize=size,
        checksum_type="MD5",
        checksum_value=md5,
        download_url="https://example.invalid/file",
    )
    assert sha256

    with pytest.raises(build_joinorder_data.DownloadIntegrityError, match="custom-format"):
        build_joinorder_data.validate_pgdump_file(dump_path, dataverse_file)


@pytest.mark.parametrize(
    ("duckdb_type", "minimum", "maximum", "expected"),
    [
        ("INTEGER", -2_147_483_648, 2_147_483_647, True),
        ("INTEGER", 0, 2_147_483_648, False),
        ("BIGINT", 0, 2_147_483_648, True),
        ("VARCHAR", 0, 1, False),
        ("BIGINT", None, None, True),
    ],
)
def test_duckdb_integer_type_can_store_range(
    duckdb_type: str,
    minimum: int | None,
    maximum: int | None,
    expected: bool,
) -> None:
    assert build_joinorder_data.duckdb_integer_type_can_store_range(duckdb_type, minimum, maximum) is expected


def test_write_build_manifest_records_source_sha256(tmp_path: Path) -> None:
    dataverse_file = build_joinorder_data.DataverseFile(
        file_id=3590041,
        label="imdb_pg11",
        filesize=5,
        checksum_type="MD5",
        checksum_value="275876e34cf609db118f3d84b799a790",
        download_url="https://dataverse.harvard.edu/api/access/datafile/3590041",
    )
    artifact = build_joinorder_data.PgDumpArtifact(
        path=tmp_path / "source" / "imdb_pg11",
        size=5,
        sha256="2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        md5="275876e34cf609db118f3d84b799a790",
        dataverse_file=dataverse_file,
    )

    manifest_path = build_joinorder_data.write_source_manifest(tmp_path, artifact, reused=False)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["dataset_version"] == "joinorder-imdb-2013-v1"
    assert manifest["source"]["sha256"] == artifact.sha256
    assert manifest["source"]["dataverse_file_id"] == 3590041
    assert manifest["source"]["reused_existing_file"] is False


def test_download_pgdump_recovers_invalid_cached_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    destination = source_dir / "imdb_pg11"
    destination.write_bytes(b"truncated")
    valid_payload = b"PGDMPvalid"
    dataverse_file = build_joinorder_data.DataverseFile(
        file_id=3590041,
        label="imdb_pg11",
        filesize=len(valid_payload),
        checksum_type="MD5",
        checksum_value=hashlib.md5(valid_payload, usedforsecurity=False).hexdigest(),
        download_url="https://dataverse.harvard.edu/api/access/datafile/3590041",
    )

    def fake_download(_url: str, path: Path) -> None:
        assert path.name == "imdb_pg11.part"
        path.write_bytes(valid_payload)

    monkeypatch.setattr(build_joinorder_data, "resolve_dataverse_file", lambda: dataverse_file)
    monkeypatch.setattr(build_joinorder_data, "download_to_file", fake_download)

    artifact = build_joinorder_data.download_pgdump(tmp_path)

    assert destination.read_bytes() == valid_payload
    assert not destination.with_name("imdb_pg11.part").exists()
    assert artifact.path == destination
    assert artifact.sha256 == hashlib.sha256(valid_payload).hexdigest()


def test_start_postgres_container_sets_postgres_user_for_custom_role(monkeypatch: pytest.MonkeyPatch) -> None:
    stream_calls = []
    wait_calls = []
    removed = []

    def fake_stream_command(args, *, check: bool = True) -> None:
        assert check is True
        stream_calls.append(args)

    def fake_wait_for_postgres(
        *,
        container_name: str,
        database: str,
        user: str,
        timeout_seconds: int = 90,
    ) -> None:
        wait_calls.append(
            {
                "container_name": container_name,
                "database": database,
                "user": user,
                "timeout_seconds": timeout_seconds,
            }
        )

    monkeypatch.setattr(build_joinorder_data, "stream_command", fake_stream_command)
    monkeypatch.setattr(build_joinorder_data, "wait_for_postgres", fake_wait_for_postgres)
    monkeypatch.setattr(build_joinorder_data, "remove_container", lambda container_name: removed.append(container_name))

    build_joinorder_data.start_postgres_container(
        container_name="job-pg",
        image="postgres:16.2",
        database="imdb",
        user="benchbox",
        replace_existing=True,
    )

    assert removed == ["job-pg"]
    assert len(stream_calls) == 1
    command = stream_calls[0]
    assert "POSTGRES_DB=imdb" in command
    assert "POSTGRES_USER=benchbox" in command
    assert command.index("POSTGRES_USER=benchbox") < command.index("postgres:16.2")
    assert wait_calls == [
        {
            "container_name": "job-pg",
            "database": "imdb",
            "user": "benchbox",
            "timeout_seconds": 90,
        }
    ]


def test_query_aliases_and_underlying_count_sql_parse_flat_job_query() -> None:
    sql = """\
SELECT MIN(mc.note), MIN(t.title)
FROM company_type AS ct,
     movie_companies AS mc,
     title AS t
WHERE ct.kind = 'production companies'
  AND ct.id = mc.company_type_id
  AND t.id = mc.movie_id;
"""

    aliases = build_joinorder_data.query_aliases(sql, query_id="1a")
    count_sql = build_joinorder_data.underlying_count_sql(sql, query_id="1a")
    id_sql = build_joinorder_data.alias_id_sql(sql, query_id="1a", limit=2)

    assert aliases == [
        build_joinorder_data.QueryAlias(table="company_type", alias="ct"),
        build_joinorder_data.QueryAlias(table="movie_companies", alias="mc"),
        build_joinorder_data.QueryAlias(table="title", alias="t"),
    ]
    assert count_sql.startswith("SELECT count(*) FROM company_type AS ct")
    assert "WHERE ct.kind = 'production companies'" in count_sql
    assert 'ct.id AS "ct__id"' in id_sql
    assert id_sql.endswith("LIMIT 2")


def test_underlying_count_contract_allows_only_canonical_known_zero_queries() -> None:
    assert build_joinorder_data.underlying_count_failure("2c", 0) is None
    assert build_joinorder_data.underlying_count_failure("1a", 1) is None

    assert build_joinorder_data.underlying_count_failure("1a", 0) == {
        "query_id": "1a",
        "expected_underlying_row_count": ">=1",
        "actual_underlying_row_count": 0,
        "reason": "unexpected_empty",
    }
    assert build_joinorder_data.underlying_count_failure("2c", 1) == {
        "query_id": "2c",
        "expected_underlying_row_count": 0,
        "actual_underlying_row_count": 1,
        "reason": "known_zero_drift",
    }


def test_validate_predicate_domain_accepts_known_zero_and_exact_null_counts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    query_dir = tmp_path / "queries"
    query_dir.mkdir()
    (query_dir / "1a.sql").write_text("SELECT MIN(t.id) FROM title AS t WHERE t.id > 0;", encoding="utf-8")
    (query_dir / "2c.sql").write_text("SELECT MIN(t.id) FROM title AS t WHERE t.id < 0;", encoding="utf-8")

    def fake_psql(*, container_name: str, database: str, user: str, sql: str) -> str:
        assert container_name == "pg"
        assert database == "imdb"
        assert user == "postgres"
        if "t.id < 0" in sql:
            return "0\n"
        if "t.id > 0" in sql:
            return "7\n"
        raise AssertionError(sql)

    def fake_psql_json_rows(*, container_name: str, database: str, user: str, sql: str) -> list[dict]:
        assert container_name == "pg"
        assert database == "imdb"
        assert user == "postgres"
        for (table, _column), (nulls, rows) in build_joinorder_data.CANONICAL_NULL_COUNTS.items():
            if f'FROM public."{table}"' in sql:
                return [{"row_count": rows, "null_count": nulls}]
        raise AssertionError(sql)

    monkeypatch.setattr(build_joinorder_data, "psql", fake_psql)
    monkeypatch.setattr(build_joinorder_data, "psql_json_rows", fake_psql_json_rows)

    report = build_joinorder_data.validate_predicate_domain(
        work_dir=tmp_path,
        query_dir=query_dir,
        container_name="pg",
        database="imdb",
        user="postgres",
        expected_query_count=None,
    )

    assert report["query_failures"] == []
    assert report["known_zero_underlying_row_counts"] == {"2c": 0}
    assert report["query_underlying_row_counts"] == {"1a": 7, "2c": 0}
    assert report["null_count_failures"] == []


def test_validate_predicate_domain_rejects_unexpected_empty_query(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    query_dir = tmp_path / "queries"
    query_dir.mkdir()
    (query_dir / "1a.sql").write_text("SELECT MIN(t.id) FROM title AS t WHERE t.id > 0;", encoding="utf-8")

    monkeypatch.setattr(build_joinorder_data, "psql", lambda **_kwargs: "0\n")
    monkeypatch.setattr(
        build_joinorder_data,
        "psql_json_rows",
        lambda **_kwargs: [{"row_count": 2_609_129, "null_count": 1_271_989}],
    )

    with pytest.raises(build_joinorder_data.JoinOrderBuildError, match="1 unexpected empty"):
        build_joinorder_data.validate_predicate_domain(
            work_dir=tmp_path,
            query_dir=query_dir,
            container_name="pg",
            database="imdb",
            user="postgres",
            expected_query_count=None,
        )


def test_validate_predicate_domain_rejects_incomplete_query_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    query_dir = tmp_path / "queries"
    query_dir.mkdir()
    (query_dir / "1a.sql").write_text("SELECT MIN(t.id) FROM title AS t WHERE t.id > 0;", encoding="utf-8")

    monkeypatch.setattr(build_joinorder_data, "psql", lambda **_kwargs: "7\n")
    monkeypatch.setattr(
        build_joinorder_data,
        "psql_json_rows",
        lambda **_kwargs: [{"row_count": 2_609_129, "null_count": 1_271_989}],
    )

    with pytest.raises(build_joinorder_data.JoinOrderBuildError, match="query-count failures"):
        build_joinorder_data.validate_predicate_domain(
            work_dir=tmp_path,
            query_dir=query_dir,
            container_name="pg",
            database="imdb",
            user="postgres",
            expected_query_count=build_joinorder_data.EXPECTED_QUERY_COUNT,
        )


def test_verify_reference_results_accepts_matching_full_result_hashes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    query_dir = tmp_path / "queries"
    query_dir.mkdir()
    (query_dir / "1a.sql").write_text("SELECT MIN(t.id) AS v FROM title AS t WHERE t.id > 0;", encoding="utf-8")
    (query_dir / "2c.sql").write_text("SELECT MIN(t.id) AS v FROM title AS t WHERE t.id < 0;", encoding="utf-8")

    row_json_by_query = {
        "1a": '{"v":1}',
        "2c": '{"v":null}',
    }
    reference = {
        "dataset_version": "joinorder-imdb-2013-v1",
        "postgres_version": "16.2",
        "manifest_hash": "manifest",
        "data_archive_hash": "archive",
        "gregrahn_commit": "commit",
        "queries": {
            "1a": {
                "row_count": 1,
                "underlying_row_count": 7,
                "first_row_sha256": hashlib.sha256(row_json_by_query["1a"].encode("utf-8")).hexdigest(),
            },
            "2c": {
                "row_count": 1,
                "underlying_row_count": 0,
                "first_row_sha256": hashlib.sha256(row_json_by_query["2c"].encode("utf-8")).hexdigest(),
            },
        },
    }
    reference_path = tmp_path / "reference_cardinalities.json"
    reference_path.write_text(json.dumps(reference), encoding="utf-8")

    def fake_psql(*, container_name: str, database: str, user: str, sql: str) -> str:
        assert container_name == "pg"
        assert database == "imdb"
        assert user == "postgres"
        if sql == "SHOW server_version":
            return "16.2\n"
        if "row_to_json" in sql:
            return (row_json_by_query["2c"] if "t.id < 0" in sql else row_json_by_query["1a"]) + "\n"
        if "AS benchbox_q" in sql:
            return "1\n"
        if "t.id < 0" in sql:
            return "0\n"
        if "t.id > 0" in sql:
            return "7\n"
        raise AssertionError(sql)

    monkeypatch.setattr(build_joinorder_data, "psql", fake_psql)

    report = build_joinorder_data.verify_reference_results(
        work_dir=tmp_path,
        query_dir=query_dir,
        reference_path=reference_path,
        container_name="pg",
        database="imdb",
        user="postgres",
        expected_query_count=None,
    )

    assert report["verified_query_count"] == 2
    assert report["full_result_hashes_verified"] == 2
    assert report["failures"] == []


def test_verify_reference_results_rejects_full_result_hash_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    query_dir = tmp_path / "queries"
    query_dir.mkdir()
    (query_dir / "1a.sql").write_text("SELECT MIN(t.id) AS v FROM title AS t WHERE t.id > 0;", encoding="utf-8")
    reference_path = tmp_path / "reference_cardinalities.json"
    reference_path.write_text(
        json.dumps(
            {
                "queries": {
                    "1a": {
                        "row_count": 1,
                        "underlying_row_count": 7,
                        "first_row_sha256": "0" * 64,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_psql(*, container_name: str, database: str, user: str, sql: str) -> str:
        if sql == "SHOW server_version":
            return "16.2\n"
        if "row_to_json" in sql:
            return '{"v":1}\n'
        if "AS benchbox_q" in sql:
            return "1\n"
        if "t.id > 0" in sql:
            return "7\n"
        raise AssertionError(sql)

    monkeypatch.setattr(build_joinorder_data, "psql", fake_psql)

    with pytest.raises(build_joinorder_data.JoinOrderBuildError, match="Reference result verification failed"):
        build_joinorder_data.verify_reference_results(
            work_dir=tmp_path,
            query_dir=query_dir,
            reference_path=reference_path,
            container_name="pg",
            database="imdb",
            user="postgres",
            expected_query_count=None,
        )

    manifest = json.loads((tmp_path / build_joinorder_data.BUILD_MANIFEST_NAME).read_text(encoding="utf-8"))
    failures = manifest["reference_result_verification"]["failures"]
    assert failures == [
        {
            "actual": hashlib.sha256(b'{"v":1}').hexdigest(),
            "expected": "0" * 64,
            "kind": "first_row_sha256",
            "query_id": "1a",
        }
    ]


def test_verify_reference_results_rejects_malformed_query_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    query_dir = tmp_path / "queries"
    query_dir.mkdir()
    (query_dir / "1a.sql").write_text("SELECT MIN(t.id) AS v FROM title AS t WHERE t.id > 0;", encoding="utf-8")
    reference_path = tmp_path / "reference_cardinalities.json"
    reference_path.write_text(json.dumps({"queries": {"1a": "merge-conflict-artifact"}}), encoding="utf-8")

    def fake_psql(*, container_name: str, database: str, user: str, sql: str) -> str:
        if sql == "SHOW server_version":
            return "16.2\n"
        raise AssertionError(sql)

    monkeypatch.setattr(build_joinorder_data, "psql", fake_psql)

    with pytest.raises(build_joinorder_data.JoinOrderBuildError, match="Reference result verification failed"):
        build_joinorder_data.verify_reference_results(
            work_dir=tmp_path,
            query_dir=query_dir,
            reference_path=reference_path,
            container_name="pg",
            database="imdb",
            user="postgres",
            expected_query_count=None,
        )

    manifest = json.loads((tmp_path / build_joinorder_data.BUILD_MANIFEST_NAME).read_text(encoding="utf-8"))
    failures = manifest["reference_result_verification"]["failures"]
    assert failures == [
        {
            "actual_type": "str",
            "kind": "malformed_reference_query",
            "query_id": "1a",
        }
    ]


def test_verify_reference_results_does_not_double_count_missing_query(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    query_dir = tmp_path / "queries"
    query_dir.mkdir()
    (query_dir / "1a.sql").write_text("SELECT MIN(t.id) AS v FROM title AS t WHERE t.id > 0;", encoding="utf-8")
    reference_path = tmp_path / "reference_cardinalities.json"
    reference_path.write_text(json.dumps({"queries": {}}), encoding="utf-8")

    def fake_psql(*, container_name: str, database: str, user: str, sql: str) -> str:
        if sql == "SHOW server_version":
            return "16.2\n"
        raise AssertionError(sql)

    monkeypatch.setattr(build_joinorder_data, "psql", fake_psql)

    with pytest.raises(build_joinorder_data.JoinOrderBuildError, match="Reference result verification failed"):
        build_joinorder_data.verify_reference_results(
            work_dir=tmp_path,
            query_dir=query_dir,
            reference_path=reference_path,
            container_name="pg",
            database="imdb",
            user="postgres",
            expected_query_count=None,
        )

    manifest = json.loads((tmp_path / build_joinorder_data.BUILD_MANIFEST_NAME).read_text(encoding="utf-8"))
    failures = manifest["reference_result_verification"]["failures"]
    assert failures == [{"kind": "missing_reference_query", "query_id": "1a"}]


def test_verify_tiny_fixture_rejects_incomplete_query_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    query_dir = tmp_path / "queries"
    query_dir.mkdir()
    (query_dir / "1a.sql").write_text("SELECT MIN(t.id) FROM title AS t WHERE t.id > 0;", encoding="utf-8")
    cardinalities_path = tmp_path / "tiny_reference_cardinalities.json"
    cardinalities_path.write_text(
        json.dumps(
            {
                "queries": {
                    f"{index}a": {"underlying_row_count": 1}
                    for index in range(1, build_joinorder_data.EXPECTED_QUERY_COUNT + 1)
                }
            }
        ),
        encoding="utf-8",
    )

    class FakeResult:
        def fetchone(self) -> tuple[int]:
            return (1,)

    class FakeConnection:
        def execute(self, *_args, **_kwargs) -> FakeResult:
            return FakeResult()

        def close(self) -> None:
            return None

    class FakeDuckDB:
        @staticmethod
        def connect() -> FakeConnection:
            return FakeConnection()

    loaded_fixture = False

    def fake_load_queries_for_duckdb(*_args, **_kwargs) -> None:
        nonlocal loaded_fixture
        loaded_fixture = True

    monkeypatch.setitem(sys.modules, "duckdb", FakeDuckDB)
    monkeypatch.setattr(build_joinorder_data, "load_queries_for_duckdb", fake_load_queries_for_duckdb)

    with pytest.raises(build_joinorder_data.JoinOrderBuildError, match="Expected 113 canonical query files"):
        build_joinorder_data.verify_tiny_fixture(
            fixture_dir=tmp_path / "fixture",
            query_dir=query_dir,
            cardinalities_path=cardinalities_path,
        )

    assert loaded_fixture is False


def test_aggregate_table_hash_is_deterministic_independent_of_input_order(tmp_path: Path) -> None:
    a = build_joinorder_data.TableFile("aka_name", tmp_path / "a.parquet", "a" * 64, 10, 1)
    b = build_joinorder_data.TableFile("title", tmp_path / "t.parquet", "b" * 64, 20, 2)

    assert build_joinorder_data.aggregate_table_hash([a, b]) == build_joinorder_data.aggregate_table_hash([b, a])


def _title_schema() -> list:
    return [
        build_joinorder_data.ColumnSchema(
            table="title",
            name="id",
            postgres_type="integer",
            udt_name="int4",
            ordinal_position=1,
            is_nullable=False,
        ),
        build_joinorder_data.ColumnSchema(
            table="title",
            name="title",
            postgres_type="character varying",
            udt_name="varchar",
            ordinal_position=2,
            is_nullable=True,
        ),
        build_joinorder_data.ColumnSchema(
            table="title",
            name="production_year",
            postgres_type="integer",
            udt_name="int4",
            ordinal_position=3,
            is_nullable=True,
        ),
    ]


def test_copy_table_to_csv_orders_full_table_by_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: list[list[str]] = []

    class Result:
        returncode = 0
        stderr = b""

    def fake_run(args: list[str], **_kwargs) -> Result:
        captured.append(args)
        return Result()

    monkeypatch.setattr(build_joinorder_data.subprocess, "run", fake_run)

    build_joinorder_data.copy_table_to_csv(
        container_name="pg",
        database="imdb",
        user="postgres",
        table="title",
        destination=tmp_path / "title.csv",
    )

    assert any('COPY (SELECT * FROM public."title" ORDER BY "id") TO STDOUT' in arg for arg in captured[0])


def test_logical_table_hash_matches_csv_and_parquet_despite_file_order(tmp_path: Path) -> None:
    duckdb = pytest.importorskip("duckdb")
    columns = _title_schema()
    csv_path = tmp_path / "title.csv"
    parquet_path = tmp_path / "title.parquet"
    csv_path.write_text(
        "\n".join(
            [
                "id,title,production_year",
                '1,"À bout de souffle",1960',
                "2,\\N,\\N",
                '3,"Heat",1995',
                "",
            ]
        ),
        encoding="utf-8",
    )

    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE title(id INTEGER, title VARCHAR, production_year INTEGER)")
        con.execute("INSERT INTO title VALUES (3, 'Heat', 1995), (1, 'À bout de souffle', 1960), (2, NULL, NULL)")
        con.execute(f"COPY title TO {build_joinorder_data.duckdb_literal(parquet_path)} (FORMAT 'parquet')")
        csv_hash = build_joinorder_data.logical_table_hash_from_csv(csv_path, columns)
        parquet_hash = build_joinorder_data.logical_table_hash_from_parquet(
            con=con,
            parquet_path=parquet_path,
            table="title",
            columns=columns,
        )
    finally:
        con.close()

    assert csv_hash == parquet_hash
    assert build_joinorder_data.aggregate_logical_content_hash(
        [csv_hash]
    ) == build_joinorder_data.aggregate_logical_content_hash([parquet_hash])


def test_logical_table_hash_preserves_quoted_csv_null_literal(tmp_path: Path) -> None:
    duckdb = pytest.importorskip("duckdb")
    columns = _title_schema()
    csv_path = tmp_path / "title.csv"
    literal_parquet_path = tmp_path / "title_literal.parquet"
    null_parquet_path = tmp_path / "title_null.parquet"
    csv_path.write_text(
        "\n".join(
            [
                "id,title,production_year",
                '1,"\\N",2001',
                "",
            ]
        ),
        encoding="utf-8",
    )

    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE title_literal(id INTEGER, title VARCHAR, production_year INTEGER)")
        con.execute("INSERT INTO title_literal VALUES (1, ?, 2001)", [build_joinorder_data.CSV_NULL])
        con.execute(
            f"COPY title_literal TO {build_joinorder_data.duckdb_literal(literal_parquet_path)} (FORMAT 'parquet')"
        )
        con.execute("CREATE TABLE title_null(id INTEGER, title VARCHAR, production_year INTEGER)")
        con.execute("INSERT INTO title_null VALUES (1, NULL, 2001)")
        con.execute(f"COPY title_null TO {build_joinorder_data.duckdb_literal(null_parquet_path)} (FORMAT 'parquet')")
        csv_hash = build_joinorder_data.logical_table_hash_from_csv(csv_path, columns)
        literal_hash = build_joinorder_data.logical_table_hash_from_parquet(
            con=con,
            parquet_path=literal_parquet_path,
            table="title",
            columns=columns,
        )
        null_hash = build_joinorder_data.logical_table_hash_from_parquet(
            con=con,
            parquet_path=null_parquet_path,
            table="title",
            columns=columns,
        )
    finally:
        con.close()

    assert csv_hash == literal_hash
    assert csv_hash != null_hash


def test_logical_table_hash_rejects_unsorted_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "title.csv"
    csv_path.write_text(
        "\n".join(["id,title,production_year", '2,"Second",2002', '1,"First",2001', ""]),
        encoding="utf-8",
    )

    with pytest.raises(build_joinorder_data.JoinOrderBuildError, match="strictly ordered by id"):
        build_joinorder_data.logical_table_hash_from_csv(csv_path, _title_schema())


def test_verify_logical_content_hashes_writes_manifest_section(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    duckdb = pytest.importorskip("duckdb")
    work_dir = tmp_path
    csv_dir = work_dir / "csv"
    parquet_dir = work_dir / "parquet"
    csv_dir.mkdir()
    parquet_dir.mkdir()
    (csv_dir / "title.csv").write_text(
        "\n".join(["id,title,production_year", '1,"First",2001', '2,"Second",2002', ""]),
        encoding="utf-8",
    )
    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE title(id INTEGER, title VARCHAR, production_year INTEGER)")
        con.execute("INSERT INTO title VALUES (2, 'Second', 2002), (1, 'First', 2001)")
        con.execute(
            f"COPY title TO {build_joinorder_data.duckdb_literal(parquet_dir / 'title.parquet')} (FORMAT 'parquet')"
        )
    finally:
        con.close()
    monkeypatch.setattr(build_joinorder_data, "TABLE_NAMES", ["title"])

    report = build_joinorder_data.verify_logical_content_hashes(work_dir=work_dir, schema={"title": _title_schema()})
    manifest = json.loads((work_dir / build_joinorder_data.BUILD_MANIFEST_NAME).read_text(encoding="utf-8"))

    assert report.failures == []
    assert manifest["logical_content"]["logical_content_rebuild"] == "PASS"
    assert manifest["logical_content"]["tables"][0]["status"] == "PASS"


def test_streaming_conversion_verifies_logical_content_before_deleting_csv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("duckdb")
    work_dir = tmp_path

    def fake_copy_table_to_csv(**kwargs) -> None:
        destination = Path(kwargs["destination"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            "\n".join(["id,title,production_year", '1,"First",2001', '2,"Second",2002', ""]),
            encoding="utf-8",
        )

    monkeypatch.setattr(build_joinorder_data, "TABLE_NAMES", ["title"])
    monkeypatch.setitem(build_joinorder_data.EXPECTED_ROW_COUNTS, "title", 2)
    monkeypatch.setattr(build_joinorder_data, "copy_table_to_csv", fake_copy_table_to_csv)
    monkeypatch.setattr(build_joinorder_data, "verify_csv_utf8_fidelity_for_table", lambda **_kwargs: {})

    parquet_files, logical_report = build_joinorder_data.extract_and_convert_parquet_streaming(
        work_dir=work_dir,
        schema={"title": _title_schema()},
        container_name="pg",
        database="imdb",
        user="postgres",
    )
    manifest = json.loads((work_dir / build_joinorder_data.BUILD_MANIFEST_NAME).read_text(encoding="utf-8"))

    assert len(parquet_files) == 1
    assert not (work_dir / "csv" / "title.csv").exists()
    assert (work_dir / "parquet" / "title.parquet").exists()
    assert logical_report.failures == []
    assert manifest["logical_content"]["logical_content_rebuild"] == "PASS"


def test_render_data_manifest_writes_self_consistent_hash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(build_joinorder_data, "git_head_sha", lambda: "abc123")
    schema = {
        "title": [
            build_joinorder_data.ColumnSchema(
                table="title",
                name="id",
                postgres_type="integer",
                udt_name="int4",
                ordinal_position=1,
                is_nullable=False,
            ),
            build_joinorder_data.ColumnSchema(
                table="title",
                name="title",
                postgres_type="character varying",
                udt_name="varchar",
                ordinal_position=2,
                is_nullable=True,
            ),
        ]
    }
    table_files = [
        build_joinorder_data.TableFile(
            table="title",
            path=tmp_path / "title.parquet",
            sha256="c" * 64,
            bytes=123,
            row_count=2,
        )
    ]
    manifest_path = tmp_path / "data_manifest.toml"

    build_joinorder_data.render_data_manifest(
        work_dir=tmp_path,
        schema=schema,
        table_files=table_files,
        url="https://example.com/archive.tar.zst",
        archive_sha256="d" * 64,
        output_path=manifest_path,
    )

    text = manifest_path.read_text(encoding="utf-8")
    assert f'manifest_hash = "{build_joinorder_data.compute_manifest_hash(manifest_path)}"' in text
    assert 'schema.id = "integer"' in text
    assert 'schema.title = "character varying"' in text


def test_manifest_hash_ignores_archive_sha256_after_packaging(tmp_path: Path) -> None:
    manifest = tmp_path / "data_manifest.toml"
    archive_hash_a = "a" * 64
    archive_hash_b = "b" * 64
    manifest.write_text(
        "\n".join(
            [
                'dataset_version = "joinorder-imdb-2013-v1"',
                'manifest_hash = "0"',
                f'archive_sha256 = "{archive_hash_a}"',
                'data_archive_hash = "logical"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    before = build_joinorder_data.compute_manifest_hash(manifest)
    manifest.write_text(manifest.read_text(encoding="utf-8").replace(archive_hash_a, archive_hash_b), encoding="utf-8")

    assert build_joinorder_data.compute_manifest_hash(manifest) == before


def test_resolve_canonical_parquet_dir_verifies_manifest_hashes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parquet = tmp_path / "title.parquet"
    parquet.write_bytes(b"canonical parquet bytes")
    digest = build_joinorder_data.sha256_file(parquet)
    manifest = tmp_path / "data_manifest.toml"
    manifest.write_text(
        "\n".join(
            [
                "[[tables]]",
                'name = "title"',
                'file = "title.parquet"',
                f'sha256 = "{digest}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(build_joinorder_data, "TABLE_NAMES", ["title"])
    monkeypatch.setattr(build_joinorder_data, "runtime_manifest_path", lambda: manifest)

    assert build_joinorder_data.resolve_canonical_parquet_dir(str(tmp_path)) == tmp_path.resolve()


def test_resolve_canonical_parquet_dir_rejects_corrupted_table(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    parquet = tmp_path / "title.parquet"
    parquet.write_bytes(b"corrupted")
    manifest = tmp_path / "data_manifest.toml"
    manifest.write_text(
        "\n".join(
            [
                "[[tables]]",
                'name = "title"',
                'file = "title.parquet"',
                f'sha256 = "{"0" * 64}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(build_joinorder_data, "TABLE_NAMES", ["title"])
    monkeypatch.setattr(build_joinorder_data, "runtime_manifest_path", lambda: manifest)

    with pytest.raises(build_joinorder_data.JoinOrderBuildError, match="hash mismatch for title"):
        build_joinorder_data.resolve_canonical_parquet_dir(str(tmp_path))


