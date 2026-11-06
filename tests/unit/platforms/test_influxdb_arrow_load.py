from __future__ import annotations

import csv
import gzip
import hashlib
from datetime import datetime
from unittest.mock import patch

import pyarrow as pa
import pytest

from benchbox.platforms.base.data_loading import DataLoadingError
from benchbox.platforms.influxdb.adapter import InfluxDBAdapter
from benchbox.platforms.influxdb.client import InfluxDBConnection, to_line_protocol

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _FakeClient:
    def __init__(self):
        self.writes: list[tuple[str, str]] = []
        self.visible_offset = 0

    def write(self, data, write_precision):
        self.writes.append((data, write_precision))

    def query(self, query):
        total = sum(len(data.split("\n")) for data, _ in self.writes)
        return pa.table({"count(*)": [total + self.visible_offset]})


@pytest.fixture
def adapter():
    with patch("benchbox.platforms.influxdb.adapter.INFLUXDB_AVAILABLE", True):
        return InfluxDBAdapter(token="token", mode="core", host="localhost", ssl=False)


def _reference_lines(adapter, path, config, measurement):
    lines = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            record = {}
            if config["timestamp"] in row:
                ts = row[config["timestamp"]]
                try:
                    record["time"] = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    record["time"] = None
            for tag in config["tags"]:
                if tag in row:
                    record[tag] = row[tag]
            for field in config["fields"]:
                if field in row and row[field]:
                    try:
                        value = float(row[field])
                    except ValueError:
                        continue
                    record[field] = (
                        int(value) if value.is_integer() and field not in adapter._FLOAT_ONLY_FIELDS else value
                    )
            tags = {c: record.get(c) for c in config["tags"] if record.get(c) is not None}
            fields = {c: record.get(c) for c in config["fields"] if record.get(c) is not None}
            if not fields:
                continue
            ts = record.get("time")
            lines.append(to_line_protocol(measurement, tags, fields, ts if isinstance(ts, datetime) else None))
    return lines


def _split(line):
    prefix, fields, *stamp = line.replace("\\ ", "\0").split(" ")
    parsed = {}
    for item in fields.split(","):
        key, _, raw = item.partition("=")
        parsed[key] = ("i", int(raw[:-1])) if raw.endswith("i") else ("f", float(raw))
    return prefix.replace("\0", "\\ "), tuple(sorted(parsed.items())), tuple(stamp)


def _load(adapter, tmp_path, name, csv_text, filename=None, client=None):
    (tmp_path / (filename or f"{name}.csv")).write_text(csv_text, encoding="utf-8")
    client = client or _FakeClient()
    conn = InfluxDBConnection(host="h", token="t", database="d")
    conn._client = client
    conn._client_type = "influxdb3"
    with patch("benchbox.platforms.influxdb._dependencies.INFLUXDB3_AVAILABLE", True):
        rows, _, meta = adapter.load_data(benchmark=None, connection=conn, data_dir=tmp_path)
    lines = [line for data, _ in client.writes for line in data.split("\n")]
    return rows, meta, lines, client


class TestInfluxArrowLoadParity:
    def test_cpu_rows_match_record_based_reference(self, adapter, tmp_path):
        header = "time,hostname,usage_user,usage_system,usage_idle,usage_nice\n"
        body = "".join(
            f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z,host {i % 3},{i % 7},{i}.5,{100 - i % 7},"
            + ("" if i % 5 == 0 else "bad" if i % 11 == 0 else "3")
            + "\n"
            for i in range(120)
        )
        body += "2026-01-01T01:00:00+01:00,h=x,0,0,0,0\n"
        body += "not-a-time,h9,1,1,1,1\n"
        config = adapter._tsbs_table_configs()["cpu"]
        (tmp_path / "ref.csv").write_text(header + body, encoding="utf-8")
        expected = _reference_lines(adapter, tmp_path / "ref.csv", config, "cpu")

        rows, meta, lines, client = _load(adapter, tmp_path, "cpu", header + body)

        assert rows["cpu"] == len(lines) == len(expected) == 122
        assert meta["total_rows"] == 122
        assert sorted(_split(line) for line in lines) == sorted(_split(line) for line in expected)

    def test_per_column_checksums_and_batching(self, adapter, tmp_path):
        header = "time,hostname,total,used_percent\n"
        body = "".join(f"2026-02-01T00:00:{i % 60:02d}Z,h{i % 4},{i * 1024},{i % 100}\n" for i in range(25_000))
        config = adapter._tsbs_table_configs()["mem"]
        (tmp_path / "ref.csv").write_text(header + body, encoding="utf-8")
        expected = _reference_lines(adapter, tmp_path / "ref.csv", config, "mem")

        rows, _, lines, client = _load(adapter, tmp_path, "mem", header + body)

        assert rows["mem"] == len(lines) == len(expected) == 25_000
        assert all(data.count("\n") < 10_000 for data, _ in client.writes)
        assert {precision for _, precision in client.writes} == {"ns"}

        def column_sums(items):
            sums = {}
            for column in ("hostname", "total", "used_percent", "time"):
                digest = hashlib.sha256()
                for line in items:
                    prefix, fields, stamp = _split(line)
                    values = {
                        "hostname": prefix.split(",", 1)[1] if "," in prefix else "",
                        "total": dict(fields).get("total"),
                        "used_percent": dict(fields).get("used_percent"),
                        "time": stamp,
                    }
                    digest.update(repr(values[column]).encode())
                sums[column] = digest.hexdigest()
            return sums

        assert column_sums(sorted(lines)) == column_sums(sorted(expected))

    def test_naive_epoch_and_invalid_timestamps(self, adapter, tmp_path):
        csv_text = (
            "time,hostname,usage_user\n2026-01-01T00:00:00,a,1\n2026-01-01 00:00:01,b,2\n1767225602,c,3\ngarbage,d,4\n"
        )

        rows, _, lines, _ = _load(adapter, tmp_path, "cpu", csv_text)

        stamps = [_split(line)[2] for line in lines]
        assert rows["cpu"] == 4
        assert stamps == [("1767225600000000000",), ("1767225601000000000",), ("1767225602000000000",), ()]

    def test_unparseable_timestamp_only_drops_its_own_row(self, adapter, tmp_path):
        csv_text = "time,hostname,usage_user\n2026-02-30 00:00:00,a,1\n2026-01-01 00:00:01,b,2\n"

        rows, _, lines, _ = _load(adapter, tmp_path, "cpu", csv_text)

        assert rows["cpu"] == 2
        assert [_split(line)[2] for line in lines] == [(), ("1767225601000000000",)]


class TestInfluxLoadFailsLoudly:
    @pytest.mark.parametrize("suffix", ["zst", "gz", "bz2"])
    def test_compressed_files_are_loaded(self, adapter, tmp_path, suffix):
        import bz2
        import gzip
        import shutil
        import subprocess

        if suffix == "zst" and shutil.which("zstd") is None:
            pytest.skip("zstd CLI not available")
        csv_text = "time,hostname,usage_user\n" + "".join(f"2026-01-01T00:00:{i:02d}Z,h{i},{i}.5\n" for i in range(40))
        raw = csv_text.encode()
        target = tmp_path / f"cpu.csv.{suffix}"
        if suffix == "gz":
            target.write_bytes(gzip.compress(raw))
        elif suffix == "bz2":
            target.write_bytes(bz2.compress(raw))
        else:
            target.write_bytes(subprocess.run(["zstd", "-q", "-c"], input=raw, capture_output=True, check=True).stdout)
        client = _FakeClient()
        conn = InfluxDBConnection(host="h", token="t", database="d")
        conn._client = client
        conn._client_type = "influxdb3"
        with patch("benchbox.platforms.influxdb._dependencies.INFLUXDB3_AVAILABLE", True):
            rows, _, meta = adapter.load_data(benchmark=None, connection=conn, data_dir=tmp_path)
        assert rows == {"cpu": 40}
        assert meta["total_rows"] == 40

    def test_no_data_files_fails(self, adapter, tmp_path):
        conn = InfluxDBConnection(host="h", token="t", database="d")
        conn._client = _FakeClient()
        conn._client_type = "influxdb3"
        with (
            patch("benchbox.platforms.influxdb._dependencies.INFLUXDB3_AVAILABLE", True),
            pytest.raises(DataLoadingError, match="nothing was loaded"),
        ):
            adapter.load_data(benchmark=None, connection=conn, data_dir=tmp_path)

    def test_multiple_compression_variants_fail(self, adapter, tmp_path):
        header = "time,hostname,usage_user\n2026-01-01T00:00:00Z,a,1\n"
        (tmp_path / "cpu.csv").write_text(header)
        (tmp_path / "cpu.csv.gz").write_bytes(gzip.compress(header.encode()))
        conn = InfluxDBConnection(host="h", token="t", database="d")
        conn._client = _FakeClient()
        conn._client_type = "influxdb3"
        with (
            patch("benchbox.platforms.influxdb._dependencies.INFLUXDB3_AVAILABLE", True),
            pytest.raises(DataLoadingError, match="Multiple data files for cpu"),
        ):
            adapter.load_data(benchmark=None, connection=conn, data_dir=tmp_path)

    def test_rows_without_fields_fail(self, adapter, tmp_path):
        csv_text = "time,hostname,usage_user\n2026-01-01T00:00:00Z,a,1\n2026-01-01T00:00:01Z,b,\n"
        with pytest.raises(DataLoadingError, match="wrote 1 of 2 source rows"):
            _load(adapter, tmp_path, "cpu", csv_text)

    def test_server_row_count_below_written_fails(self, adapter, tmp_path):
        csv_text = "time,hostname,usage_user\n2026-01-01T00:00:00Z,a,1\n2026-01-01T00:00:01Z,b,2\n"
        client = _FakeClient()
        client.visible_offset = -1
        adapter._VISIBILITY_TIMEOUT_S = 0.2
        adapter._VISIBILITY_POLL_S = 0.05
        with pytest.raises(DataLoadingError, match="reports 1 rows in cpu after writing 2"):
            _load(adapter, tmp_path, "cpu", csv_text, client=client)

    def test_write_failure_is_not_swallowed(self, adapter, tmp_path):
        (tmp_path / "cpu.csv").write_text("time,hostname,usage_user\n2026-01-01T00:00:00Z,a,1\n")

        class Conn:
            def write_table(self, **kwargs):
                raise RuntimeError("boom")

        with (
            patch("benchbox.platforms.influxdb._dependencies.INFLUXDB3_AVAILABLE", True),
            pytest.raises(DataLoadingError, match="boom"),
        ):
            adapter.load_data(benchmark=None, connection=Conn(), data_dir=tmp_path)

    def test_tags_table_writes_string_fields(self, adapter, tmp_path):
        csv_text = (
            'hostname,region,datacenter,service_version\nhost_0,us-east-1,dc1,"1.8.21"\nhost 1,"we""ird\\zone",,2.0\n'
        )

        rows, _, lines, _ = _load(adapter, tmp_path, "tags", csv_text, filename="tags.csv")

        assert rows["tags"] == 2
        assert lines == [
            'tags,hostname=host_0 region="us-east-1",datacenter="dc1",service_version="1.8.21"',
            'tags,hostname=host\\ 1 region="we\\"ird\\\\zone",service_version="2.0"',
        ]
