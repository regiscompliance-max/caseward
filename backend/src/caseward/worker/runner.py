"""The worker loop: claim a job, run it under the job's client, record the outcome.

A job's work and its success are committed together, so a crash part-way
never leaves half-done work marked as done. Kinds without a handler are
never claimed: they wait in the queue until a worker that can do them runs
(robustness rule 5).
"""

import logging
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from caseward.db import RequestContext, scoped_transaction
from caseward.services import Services
from caseward.worker import documents, extraction
from caseward.worker.deadlines import compute_deadlines

log = logging.getLogger("caseward.worker")

JobHandler = Callable[[psycopg.Connection, dict[str, Any]], Any]

HANDLERS: dict[str, JobHandler] = {
    "compute_deadlines": compute_deadlines,
}


def build_handlers(services: Services | None) -> dict[str, JobHandler]:
    """Job kinds this worker can run. Kinds whose service is not configured are
    left out, so those jobs wait for a worker that has it."""
    handlers = dict(HANDLERS)
    if services is not None:
        if services.scanner is not None:
            handlers["scan_malware"] = documents.scan_malware(services)
        if services.ocr is not None:
            handlers["ocr"] = documents.ocr(services)
        if services.ai is not None:
            handlers["extract"] = extraction.extract(services)
    return handlers

SYSTEM = RequestContext(actor_kind="worker", request_id="worker")


def describe(exc: Exception) -> str:
    # First line only: database error details can quote row values.
    return f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"[:1000]


class Worker:
    def __init__(self, pool: ConnectionPool, handlers: dict[str, JobHandler] | None = None,
                 worker_id: str | None = None, poll_seconds: float = 2.0, sweep_seconds: float = 60.0,
                 stale_after: str = "15 minutes"):
        self.pool = pool
        self.handlers = HANDLERS if handlers is None else handlers
        self.worker_id = worker_id or f"{socket.gethostname()}:{threading.get_ident()}"
        self.poll_seconds = poll_seconds
        self.sweep_seconds = sweep_seconds
        self.stale_after = stale_after

    def run_once(self) -> bool:
        """Process one job. Returns False if there was nothing to do."""
        with self.pool.connection() as conn:
            with scoped_transaction(conn, SYSTEM):
                job = conn.cursor(row_factory=dict_row).execute(
                    "select * from caseward.claim_job(%s, %s)", [self.worker_id, list(self.handlers)]).fetchone()
            if job is None:
                return False
            ctx = RequestContext(actor_kind="worker", client_id=job["client_id"], request_id=f"job-{job['id']}")
            try:
                with scoped_transaction(conn, ctx):
                    self.handlers[job["kind"]](conn, job)
                    conn.execute("select caseward.finish_job(%s)", [job["id"]])
                log.info("job %s %s done", job["id"], job["kind"])
            except Exception as exc:
                log.exception("job %s %s failed", job["id"], job["kind"])
                with scoped_transaction(conn, SYSTEM):
                    conn.execute("select caseward.finish_job(%s, %s)", [job["id"], describe(exc)])
            return True

    def drain(self, limit: int = 10_000) -> int:
        n = 0
        while n < limit and self.run_once():
            n += 1
        return n

    def sweep(self) -> tuple[int, int]:
        """Mark missed deadlines and raise alerts. Returns (missed, alerts)."""
        with self.pool.connection() as conn, scoped_transaction(conn, SYSTEM):
            missed, alerts = conn.execute("select * from caseward.sweep_deadlines()").fetchone()
        if missed or alerts:
            log.info("sweep: %s deadlines missed, %s alerts raised", missed, alerts)
        return missed, alerts

    def requeue_stale(self) -> int:
        with self.pool.connection() as conn, scoped_transaction(conn, SYSTEM):
            n = conn.execute("select caseward.requeue_stale_jobs(%s::interval)", [self.stale_after]).fetchone()[0]
        if n:
            log.warning("requeued %s jobs left running by a stopped worker", n)
        return n

    def run_forever(self, stop: threading.Event) -> None:
        next_sweep = 0.0
        while not stop.is_set():
            try:
                if time.monotonic() >= next_sweep:
                    self.requeue_stale()
                    self.sweep()
                    next_sweep = time.monotonic() + self.sweep_seconds
                if not self.run_once():
                    stop.wait(self.poll_seconds)
            except psycopg.OperationalError:
                # Database unavailable: wait and retry rather than exit.
                log.exception("database unavailable")
                stop.wait(self.poll_seconds * 5)
