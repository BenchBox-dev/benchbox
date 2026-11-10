from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

_INSERT_TIMEOUT_S = 7200


@dataclass(frozen=True)
class HttpEndpoint:
    host: str
    port: int
    database: str
    username: str
    password: str
    secure: bool = False


def http_insert(
    endpoint: HttpEndpoint,
    table: str,
    insert_format: str,
    chunks: Iterable[bytes],
    settings: Mapping[str, object],
) -> int:
    import requests

    scheme = "https" if endpoint.secure else "http"
    params = {
        "query": f"INSERT INTO {table} FORMAT {insert_format}",
        "database": endpoint.database,
        **{key: str(value) for key, value in settings.items()},
    }
    response = requests.post(
        f"{scheme}://{endpoint.host}:{endpoint.port}/",
        params=params,
        data=chunks,
        auth=(endpoint.username, endpoint.password),
        timeout=_INSERT_TIMEOUT_S,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"ClickHouse HTTP insert into {table} failed with {response.status_code}: {response.text[:500]}"
        )
    summary = response.headers.get("X-ClickHouse-Summary")
    if summary is None:
        raise RuntimeError(f"ClickHouse HTTP insert into {table} returned no X-ClickHouse-Summary header")
    return int(json.loads(summary)["written_rows"])
