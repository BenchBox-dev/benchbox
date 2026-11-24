from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.platform_manifest import PLATFORM_MANIFEST
from benchbox.platforms.azure import spark_execution_utils
from benchbox.utils.run_deadline import RunDeadline, RunTimeoutError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

WAIT_SECONDS = 10


@dataclass
class Case:
    label: str
    adapter: Any
    start: Callable[[], str]
    finish: Callable[[], None]
    fail: Callable[[], None]
    stop: Callable[[], None]
    cancels: list[str]
    always: list[str] = field(default_factory=list)


class Work:
    def __init__(self, target: Callable[[], Any]) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.failure: BaseException | None = None
        self._thread = threading.Thread(target=self._run, args=(target,))

    def _run(self, target: Callable[[], Any]) -> None:
        try:
            target()
        except BaseException as exc:
            self.failure = exc

    def begin(self) -> None:
        self._thread.start()
        assert self.entered.wait(WAIT_SECONDS)

    def hold(self) -> None:
        self.entered.set()
        assert self.release.wait(WAIT_SECONDS)

    def end(self) -> None:
        self.release.set()
        if self._thread.ident is not None:
            self._thread.join(WAIT_SECONDS)


class Refuser:
    def __init__(self) -> None:
        self.refuse = False

    def check(self) -> None:
        if self.refuse:
            raise RuntimeError("service refused")


class StubCursor:
    def __init__(self, work: Work, refuser: Refuser, cancels: list[str], query_id: str, fails: bool = False) -> None:
        self.work = work
        self.refuser = refuser
        self.cancels = cancels
        self.query_id = None
        self._query_id = query_id
        self.fails = fails
        self.description = None
        self.closed = False

    def execute(self, *_args: Any, **_kwargs: Any) -> None:
        self.query_id = self._query_id
        if self.fails:
            raise RuntimeError("query failed")
        self.work.hold()
        raise RuntimeError("cancelled")

    def fetchall(self) -> list[Any]:
        return []

    def cancel(self) -> None:
        self.refuser.check()
        self.cancels.append(self._query_id)
        self.work.release.set()

    def close(self) -> None:
        self.closed = True


def _cursor_case(label: str, adapter: Any, run: Callable[[Any], Any]) -> Case:
    work = Work(lambda: run(connection))
    refuser = Refuser()
    cancels: list[str] = []
    cursors: list[StubCursor] = []

    def make_cursor() -> StubCursor:
        index = len(cursors) + 1
        cursor = StubCursor(work, refuser, cancels, f"query-{index}", fails=state["fail_next"])
        cursors.append(cursor)
        return cursor

    state = {"fail_next": False}
    connection = SimpleNamespace(cursor=make_cursor, client=MagicMock(), close=lambda: None)

    def finish() -> None:
        state["fail_next"] = True
        run(connection)
        state["fail_next"] = False

    def start() -> str:
        work.begin()
        return "query-1"

    def fail() -> None:
        refuser.refuse = True

    return Case(label, adapter, start, finish, fail, work.end, cancels)


@pytest.fixture
def athena_case(monkeypatch):
    import benchbox.platforms.athena as module

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(module, "boto3", MagicMock())
    monkeypatch.setattr(module, "athena_connect", MagicMock())
    monkeypatch.setattr(module, "check_platform_dependencies", lambda *a, **k: (True, []))
    adapter = module.AthenaAdapter(s3_staging_dir="s3://bucket/path", region="us-east-1")
    return _cursor_case("Athena", adapter, lambda c: adapter.execute_query(c, "SELECT 1", "Q1"))


@pytest.fixture
def databricks_case(monkeypatch):
    import benchbox.platforms.databricks.adapter as module

    monkeypatch.setattr(module, "check_platform_dependencies", lambda *a, **k: (True, []))
    adapter = module.DatabricksAdapter(
        server_hostname="test.azuredatabricks.net",
        http_path="/sql/1.0/warehouses/abc",
        access_token="token",
        catalog="main",
        schema="benchbox",
    )
    adapter._ensure_session_cache_disabled = lambda *a, **k: None
    return _cursor_case("Databricks", adapter, lambda c: adapter.execute_query(c, "SELECT 1", "Q1"))


@pytest.fixture
def starburst_case(monkeypatch):
    from benchbox.platforms.starburst import StarburstAdapter

    adapter = StarburstAdapter(host="test.galaxy.starburst.io", username="user@example.com/accountadmin", password="x")
    return _cursor_case("Starburst", adapter, lambda c: adapter._execute_timed_query(c, "SELECT 1", "Q1"))


@pytest.fixture
def synapse_case(monkeypatch):
    import benchbox.platforms.azure_synapse as module

    monkeypatch.setattr(module, "pyodbc", MagicMock(Error=Exception))
    monkeypatch.setattr(module, "check_platform_dependencies", lambda *a, **k: (True, []))
    adapter = module.AzureSynapseAdapter(server="ws.sql.azuresynapse.net", username="admin", password="secret")
    return _cursor_case("Azure Synapse", adapter, lambda c: adapter.execute_query(c, "SELECT 1", "Q1"))


@pytest.fixture
def fabric_warehouse_case(monkeypatch):
    import benchbox.platforms.fabric_warehouse as module

    monkeypatch.setattr(module, "pyodbc", MagicMock(Error=Exception))
    monkeypatch.setattr(module, "check_platform_dependencies", lambda *a, **k: (True, []))
    adapter = module.FabricWarehouseAdapter(
        workspace="workspace",
        database="warehouse",
        auth_method="service_principal",
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
    )
    return _cursor_case("Fabric Warehouse", adapter, lambda c: adapter.execute_query(c, "SELECT 1", "Q1"))


@pytest.fixture
def fabric_lakehouse_case(monkeypatch):
    import benchbox.platforms.fabric_lakehouse as module

    monkeypatch.setattr(module, "pyodbc", MagicMock(Error=Exception))
    monkeypatch.setattr(module, "check_platform_dependencies", lambda *a, **k: (True, []))
    adapter = module.FabricLakehouseAdapter(
        workspace="workspace-abc",
        lakehouse="lakehouse_db",
        auth_method="service_principal",
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
    )
    return _cursor_case("Fabric Lakehouse", adapter, lambda c: adapter.execute_query(c, "SELECT 1", "Q1"))


@pytest.fixture
def athena_spark_case():
    with (
        patch("benchbox.platforms.aws.athena_spark_adapter.BOTO3_AVAILABLE", True),
        patch("benchbox.platforms.aws.athena_spark_adapter.boto3", MagicMock()),
        patch("benchbox.platforms.aws.athena_spark_adapter.CloudSparkStaging"),
        patch("benchbox.platforms.aws.athena_spark_adapter.time"),
    ):
        from benchbox.platforms.aws import AthenaSparkAdapter

        adapter = AthenaSparkAdapter(workgroup="spark", s3_staging_dir="s3://bucket/data")
        work = Work(lambda: adapter._submit_calculation("SELECT 1"))
        refuser = Refuser()
        cancels: list[str] = []
        client = MagicMock()
        client.start_session.return_value = {"SessionId": "session-1", "State": "CREATING"}
        client.get_session_status.return_value = {"Status": {"State": "IDLE"}}
        calculations = iter(["calc-1", "calc-2", "calc-3"])
        client.start_calculation_execution.side_effect = lambda **_: {
            "CalculationExecutionId": next(calculations),
            "State": "CREATING",
        }
        state = {"finish": False}

        def status(**_: Any) -> dict[str, Any]:
            if state["finish"]:
                return {"Status": {"State": "COMPLETED"}}
            work.hold()
            return {"Status": {"State": "CANCELED"}}

        def stop(CalculationExecutionId: str) -> None:
            refuser.check()
            cancels.append(CalculationExecutionId)
            work.release.set()

        def terminate(SessionId: str) -> None:
            refuser.check()
            cancels.append(f"session:{SessionId}")

        client.get_calculation_execution_status.side_effect = status
        client.stop_calculation_execution.side_effect = stop
        client.terminate_session.side_effect = terminate
        adapter._athena_client = client
        adapter.create_connection()

        def finish() -> None:
            state["finish"] = True
            adapter._submit_calculation("SELECT 2")
            state["finish"] = False

        def start() -> str:
            work.begin()
            return "calc-1"

        yield Case(
            "Athena Spark",
            adapter,
            start,
            finish,
            lambda: setattr(refuser, "refuse", True),
            work.end,
            cancels,
            always=["session:session-1"],
        )


@pytest.fixture
def dataproc_serverless_case():
    with (
        patch("benchbox.platforms.gcp.dataproc_serverless_adapter.GOOGLE_CLOUD_AVAILABLE", True),
        patch("benchbox.platforms.gcp.dataproc_serverless_adapter.dataproc_v1", MagicMock()),
        patch("benchbox.platforms.gcp.dataproc_serverless_adapter.storage", MagicMock()),
        patch("benchbox.platforms.gcp.dataproc_serverless_adapter.CloudSparkStaging"),
    ):
        from benchbox.platforms.gcp import DataprocServerlessAdapter

        adapter = DataprocServerlessAdapter(project_id="proj", gcs_staging_dir="gs://bucket/path")
        work = Work(lambda: adapter._submit_spark_sql_batch("SELECT 1"))
        refuser = Refuser()
        cancels: list[str] = []
        state = {"finish": False}
        client = MagicMock()

        def create_batch(request: dict[str, Any]) -> MagicMock:
            batch_id = request["batch_id"]
            operation = MagicMock()

            def result(timeout: float | None = None) -> Any:
                if state["finish"]:
                    return SimpleNamespace(state=SimpleNamespace(name="SUCCEEDED"))
                work.hold()
                raise RuntimeError("cancelled")

            def cancel() -> bool:
                refuser.check()
                cancels.append(batch_id)
                work.release.set()
                return True

            operation.result.side_effect = result
            operation.cancel.side_effect = cancel
            return operation

        client.create_batch.side_effect = create_batch
        adapter._batch_client = client
        adapter._upload_to_gcs = MagicMock()

        def finish() -> None:
            state["finish"] = True
            adapter._submit_spark_sql_batch("SELECT 2")
            state["finish"] = False

        def start() -> str:
            work.begin()
            return client.create_batch.call_args.kwargs["request"]["batch_id"]

        yield Case(
            "Dataproc Serverless", adapter, start, finish, lambda: setattr(refuser, "refuse", True), work.end, cancels
        )


def _response(payload: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(status_code=200, text="", json=lambda: payload or {}, raise_for_status=lambda: None)


def _livy_case(label: str, adapter: Any) -> Case:
    work = Work(lambda: adapter.execute_query(None, "SELECT 1", "Q1"))
    refuser = Refuser()
    cancels: list[str] = []
    state = {"finish": False, "submitted": 0}

    def post(url: str, **_: Any) -> SimpleNamespace:
        if url.endswith("/cancel"):
            refuser.check()
            cancels.append(f"statement:{url.split('/')[-2]}")
            work.release.set()
            return _response()
        state["submitted"] += 1
        return _response({"id": state["submitted"]})

    def get(url: str, **_: Any) -> SimpleNamespace:
        if state["finish"]:
            return _response({"state": "available", "output": {"status": "ok", "data": {"values": []}}})
        work.hold()
        return _response({"state": "cancelled"})

    def delete(url: str, **_: Any) -> SimpleNamespace:
        refuser.check()
        cancels.append(f"session:{url.rsplit('/', 1)[-1]}")
        return _response()

    adapter._ensure_session = lambda: 7
    adapter._session_id = 7
    adapter._session_created_by_us = True
    adapter._get_headers = dict
    adapter.timing_fields_for = lambda *a, **k: {}

    def finish() -> None:
        state["finish"] = True
        adapter.execute_query(None, "SELECT 2", "Q2")
        state["finish"] = False

    def start() -> str:
        work.begin()
        return "statement:1"

    fake_requests = SimpleNamespace(post=post, get=get, delete=delete)
    patcher = patch.object(spark_execution_utils, "requests", fake_requests)
    patcher.start()
    sleeper = patch.object(spark_execution_utils, "time", SimpleNamespace(sleep=lambda _seconds: None))
    sleeper.start()
    stopped = {"done": False}

    def stop() -> None:
        work.end()
        if not stopped["done"]:
            stopped["done"] = True
            sleeper.stop()
            patcher.stop()

    return Case(
        label, adapter, start, finish, lambda: setattr(refuser, "refuse", True), stop, cancels, always=["session:7"]
    )


@pytest.fixture
def fabric_spark_case():
    with (
        patch("benchbox.platforms.azure.fabric_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
        patch("benchbox.platforms.azure.fabric_spark_adapter.DefaultAzureCredential", MagicMock()),
        patch("benchbox.platforms.azure.fabric_spark_adapter.REQUESTS_AVAILABLE", True),
        patch("benchbox.platforms.azure.fabric_spark_adapter.CloudSparkStaging"),
    ):
        from benchbox.platforms.azure import FabricSparkAdapter

        case = _livy_case("Fabric Spark", FabricSparkAdapter(workspace_id="workspace", lakehouse_id="lakehouse"))
        yield case
        case.stop()


@pytest.fixture
def synapse_spark_case():
    with (
        patch("benchbox.platforms.azure.synapse_spark_adapter.AZURE_IDENTITY_AVAILABLE", True),
        patch("benchbox.platforms.azure.synapse_spark_adapter.DefaultAzureCredential", MagicMock()),
        patch("benchbox.platforms.azure.synapse_spark_adapter.REQUESTS_AVAILABLE", True),
        patch("benchbox.platforms.azure.synapse_spark_adapter.CloudSparkStaging"),
    ):
        from benchbox.platforms.azure import SynapseSparkAdapter

        case = _livy_case(
            "Synapse Spark",
            SynapseSparkAdapter(
                workspace_name="workspace", spark_pool_name="pool", storage_account="account", storage_container="data"
            ),
        )
        yield case
        case.stop()


CASES = [
    "athena_case",
    "athena_spark_case",
    "databricks_case",
    "starburst_case",
    "synapse_case",
    "fabric_warehouse_case",
    "fabric_lakehouse_case",
    "fabric_spark_case",
    "synapse_spark_case",
    "dataproc_serverless_case",
]


@pytest.fixture(params=CASES)
def case(request: pytest.FixtureRequest) -> Case:
    return request.getfixturevalue(request.param)


def test_work_the_run_started_is_cancelled_at_timeout(case: Case) -> None:
    started = case.start()

    case.adapter.cancel_remote_work()
    case.stop()

    assert sorted(case.cancels) == sorted([started, *case.always])
    assert case.adapter._started_work.keys() == []


def test_work_the_run_never_started_is_not_cancelled(case: Case) -> None:
    case.adapter.cancel_remote_work()

    assert case.cancels == case.always


def test_work_the_run_saw_finish_is_not_cancelled(case: Case) -> None:
    case.finish()
    assert case.adapter._started_work.keys() == []

    case.adapter.cancel_remote_work()

    assert case.cancels == case.always


def test_a_failed_cancel_is_reported_and_leaves_the_timeout_in_place(case: Case) -> None:
    deadline = RunDeadline(5)
    deadline.register_remote_cleanup(case.adapter, case.adapter.cancel_remote_work, case.label)
    case.start()
    case.fail()

    timeout = deadline.record_timeout()
    errors = deadline.cancel_remote_work()
    still_tracked = case.adapter._started_work.keys()
    case.stop()

    assert deadline.timeout is timeout
    assert isinstance(timeout, RunTimeoutError)
    assert len(errors) == 1
    assert errors[0].startswith(f"{case.label}: RuntimeError: ")
    assert "service refused" in errors[0]
    assert still_tracked != []


@pytest.mark.parametrize("name", ["fabric_spark_case", "synapse_spark_case"])
def test_a_livy_session_the_run_did_not_create_is_never_deleted(name: str, request: pytest.FixtureRequest) -> None:
    case: Case = request.getfixturevalue(name)
    case.adapter._session_created_by_us = False
    started = case.start()

    case.adapter.cancel_remote_work()
    case.stop()

    assert case.cancels == [started]
    assert case.adapter._session_id == 7


def test_a_livy_session_that_could_not_be_deleted_is_kept_so_close_can_retry(fabric_spark_case: Case) -> None:
    fabric_spark_case.fail()

    with pytest.raises(RuntimeError, match="Fabric Spark session delete failed"):
        fabric_spark_case.adapter.cancel_remote_work()

    assert fabric_spark_case.adapter._session_id == 7


def test_athena_spark_terminates_nothing_when_the_run_started_no_session(athena_spark_case: Case) -> None:
    athena_spark_case.adapter._session_id = None

    athena_spark_case.adapter.cancel_remote_work()

    assert athena_spark_case.cancels == []


def test_athena_spark_keeps_the_session_id_when_terminate_fails(athena_spark_case: Case) -> None:
    athena_spark_case.fail()

    with pytest.raises(RuntimeError, match="session terminate failed"):
        athena_spark_case.adapter.cancel_remote_work()

    assert athena_spark_case.adapter._session_id == "session-1"


def test_the_databricks_dataframe_adapter_declares_no_cleanup() -> None:
    from benchbox.platforms.base.adapter import PlatformAdapter
    from benchbox.platforms.databricks.dataframe_adapter import DatabricksDataFrameAdapter

    assert DatabricksDataFrameAdapter.cancel_remote_work is PlatformAdapter.cancel_remote_work
