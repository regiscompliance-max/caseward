"""Request-scoped database access.

Row-level security reads the acting client and user from transaction-local
settings. Every unit of work must go through ``scoped_transaction`` so those
settings are set with ``is_local = true`` and cannot leak to the next request
that reuses a pooled connection.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

ActorKind = Literal["user", "worker", "system"]


@dataclass(frozen=True)
class RequestContext:
    actor_kind: ActorKind
    client_id: UUID | None = None
    user_id: UUID | None = None
    request_id: str | None = None
    source_ip: str | None = None


@contextmanager
def scoped_transaction(conn: psycopg.Connection, ctx: RequestContext) -> Iterator[psycopg.Connection]:
    if not conn.autocommit:
        # Otherwise conn.transaction() would open a savepoint inside an outer
        # transaction and the settings would outlive this block.
        raise ValueError("scoped_transaction needs an autocommit connection")
    with conn.transaction():
        conn.execute(
            """
            select set_config('app.client_id', %s, true),
                   set_config('app.user_id', %s, true),
                   set_config('app.actor_kind', %s, true),
                   set_config('app.request_id', %s, true),
                   set_config('app.source_ip', %s, true)
            """,
            [
                str(ctx.client_id) if ctx.client_id else "",
                str(ctx.user_id) if ctx.user_id else "",
                ctx.actor_kind,
                ctx.request_id or "",
                ctx.source_ip or "",
            ],
        )
        yield conn


def record_access(
    conn: psycopg.Connection,
    ctx: RequestContext,
    action: Literal["view", "export", "download"],
    entity_type: str,
    entity_id: str,
    detail: dict | None = None,
) -> None:
    """Audit a read. Writes are audited by database triggers; views and exports must call this."""
    conn.execute(
        """
        insert into caseward.audit_event
          (actor_user_id, actor_kind, client_id, action, entity_type, entity_id, request_id, source_ip, after)
        values (%s, %s, %s, %s, %s, %s, nullif(%s, ''), nullif(%s, '')::inet, %s)
        """,
        [ctx.user_id, ctx.actor_kind, ctx.client_id, action, entity_type, entity_id,
         ctx.request_id or "", ctx.source_ip or "", Jsonb(detail) if detail else None],
    )
