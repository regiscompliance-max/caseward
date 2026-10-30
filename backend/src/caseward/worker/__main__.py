"""Run a worker: python -m caseward.worker

Needs CASEWARD_WORKER_DATABASE_URL for a login that is a member of
caseward_app and caseward_worker.
"""

import logging
import os
import signal
import threading

from psycopg_pool import ConnectionPool

from caseward.services import worker_services_from_env
from caseward.worker.runner import Worker, build_handlers


def main() -> None:
    logging.basicConfig(level=os.environ.get("CASEWARD_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    with ConnectionPool(os.environ["CASEWARD_WORKER_DATABASE_URL"], kwargs={"autocommit": True},
                        min_size=1, max_size=2) as pool:
        Worker(pool, handlers=build_handlers(worker_services_from_env()), poll_seconds=float(os.environ.get("CASEWARD_WORKER_POLL_SECONDS", "2")),
               sweep_seconds=float(os.environ.get("CASEWARD_WORKER_SWEEP_SECONDS", "60"))).run_forever(stop)


if __name__ == "__main__":
    main()
