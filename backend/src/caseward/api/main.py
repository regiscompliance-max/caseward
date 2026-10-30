"""Entry points for the two deployments:

    uvicorn caseward.api.main:client_app --factory
    uvicorn caseward.api.main:operations_app --factory
"""

from fastapi import FastAPI
from psycopg_pool import ConnectionPool

from caseward.api.auth import JwtVerifier
from caseward.api.framework import build_app
from caseward.api.handlers import client, operations
from caseward.api.settings import Settings
from caseward.services import Services, fetch_govuk_bank_holidays, storage_from_env


def _create(kind: str, prefix: str, handlers) -> FastAPI:
    settings = Settings.from_env(prefix)
    pool = ConnectionPool(settings.database_url, kwargs={"autocommit": True}, max_size=settings.pool_max_size,
                          open=True)
    verifier = JwtVerifier(settings.oidc_issuer, settings.oidc_audience, jwks_url=settings.oidc_jwks_url)
    services = Services(storage=storage_from_env(), bank_holidays=fetch_govuk_bank_holidays)
    app = build_app(kind, settings, pool, verifier, handlers, services)
    app.router.on_shutdown.append(pool.close)
    return app


def client_app() -> FastAPI:
    return _create("client", "CASEWARD_CLIENT", client.HANDLERS)


def operations_app() -> FastAPI:
    return _create("operations", "CASEWARD_OPS", operations.HANDLERS)
