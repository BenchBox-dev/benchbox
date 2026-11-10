from __future__ import annotations

import concurrent.futures
import contextvars
import queue
import threading
from typing import Any, Callable

_WorkItem = tuple[concurrent.futures.Future, contextvars.Context, Callable[..., Any], tuple[Any, ...], dict[str, Any]]


class DaemonStreamExecutor:
    def __init__(self, max_workers: int, thread_name_prefix: str = "benchbox-throughput-stream") -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self._max_workers = max_workers
        self._thread_name_prefix = thread_name_prefix
        self._work_queue: queue.SimpleQueue[_WorkItem | None] = queue.SimpleQueue()
        self._idle_workers = threading.Semaphore(0)
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._shutdown = False

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> concurrent.futures.Future:
        with self._lock:
            if self._shutdown:
                raise RuntimeError("cannot schedule new futures after shutdown")
            future: concurrent.futures.Future = concurrent.futures.Future()
            self._work_queue.put((future, contextvars.copy_context(), fn, args, kwargs))
            self._spawn_worker_if_needed()
            return future

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        with self._lock:
            self._shutdown = True
            if cancel_futures:
                self._cancel_queued()
            threads = list(self._threads)
            for _ in threads:
                self._work_queue.put(None)
        if wait:
            for thread in threads:
                thread.join()

    def _cancel_queued(self) -> None:
        while True:
            try:
                item = self._work_queue.get_nowait()
            except queue.Empty:
                return
            if item is not None:
                item[0].cancel()

    def _spawn_worker_if_needed(self) -> None:
        if self._idle_workers.acquire(blocking=False):
            return
        if len(self._threads) >= self._max_workers:
            return
        thread = threading.Thread(
            target=self._worker,
            name=f"{self._thread_name_prefix}_{len(self._threads)}",
            daemon=True,
        )
        thread.start()
        self._threads.append(thread)

    def _worker(self) -> None:
        while True:
            item = self._work_queue.get()
            if item is None:
                return
            future, context, fn, args, kwargs = item
            if future.set_running_or_notify_cancel():
                try:
                    outcome = context.run(fn, *args, **kwargs)
                except BaseException as exc:
                    future.set_exception(exc)
                else:
                    future.set_result(outcome)
            del item, future, context, fn, args, kwargs
            self._idle_workers.release()
