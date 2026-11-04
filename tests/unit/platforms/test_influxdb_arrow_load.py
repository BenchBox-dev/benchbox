from __future__ import annotations

import csv
import hashlib
from datetime import datetime
from unittest.mock import patch

import pytest

from benchbox.platforms.influxdb.adapter import InfluxDBAdapter
from benchbox.platforms.influxdb.client import InfluxDBConnection, to_line_protocol

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _FakeClient:
    def __init__(self):
        self.writes: list[tuple[str, str]] = []

    def write(self, data, write_precision):
        self.writes.append((data, write_precision))


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


def _load(adapter, tmp_path, name, csv_text):
    (tmp_path / f"{name}.csv").write_text(csv_text, encoding="utf-8")
    client = _FakeClient()
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
        body += "2026-01-01T02:00:00Z,,,,,\n"
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
