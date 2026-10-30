"""Access tokens from the identity provider, and who they belong to."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import jwt
import psycopg
from psycopg.rows import dict_row

from caseward.api.problems import Problem

ALGORITHMS = ["RS256", "ES256", "PS256"]


class TokenVerifier(Protocol):
    def verify(self, token: str) -> dict[str, Any]: ...


class JwtVerifier:
    """Verifies signature, issuer, audience and expiry. Keys come from the
    identity provider's JWKS endpoint, or are given directly in tests."""

    def __init__(self, issuer: str, audience: str, jwks_url: str | None = None, key: Any = None):
        if (jwks_url is None) == (key is None):
            raise ValueError("give exactly one of jwks_url or key")
        self.issuer = issuer
        self.audience = audience
        self.key = key
        self.jwks = jwt.PyJWKClient(jwks_url, cache_keys=True) if jwks_url else None

    def verify(self, token: str) -> dict[str, Any]:
        key = self.key if self.jwks is None else self.jwks.get_signing_key_from_jwt(token).key
        return jwt.decode(token, key, algorithms=ALGORITHMS, audience=self.audience, issuer=self.issuer,
                          options={"require": ["exp", "iat", "sub"]})


@dataclass(frozen=True)
class Membership:
    client_id: UUID
    client_name: str
    client_kind: str
    role: str
    special_category_access: bool


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    display_name: str
    staff_role: str | None
    memberships: tuple[Membership, ...]

    def membership(self, client_id: UUID) -> Membership | None:
        return next((m for m in self.memberships if m.client_id == client_id), None)


def unauthorised(code: str, detail: str) -> Problem:
    return Problem(401, code, "Not signed in", detail, headers={"WWW-Authenticate": "Bearer"})


def authenticate(verifier: TokenVerifier, authorization: str | None, conn: psycopg.Connection) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthorised("missing_token", "A bearer token is required.")
    try:
        claims = verifier.verify(authorization[7:].strip())
    except jwt.ExpiredSignatureError:
        raise unauthorised("token_expired", "The token has expired.") from None
    except jwt.PyJWTError:
        raise unauthorised("invalid_token", "The token is not valid.") from None
    if "mfa" not in claims.get("amr", []):
        raise unauthorised("mfa_required", "Sign in again with multi-factor authentication.")

    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        user = cur.execute("select * from caseward.resolve_user(%s)", [claims["sub"]]).fetchone()
        if user is None and claims.get("email") and claims.get("email_verified") is True:
            # First sign-in of an invited user: bind the invitation to this subject.
            if cur.execute("select caseward.claim_invitation(%s, %s) as id", [claims["sub"], claims["email"]]).fetchone()["id"]:
                user = cur.execute("select * from caseward.resolve_user(%s)", [claims["sub"]]).fetchone()
        if not user or not user["is_active"]:
            raise unauthorised("unknown_user", "This account is not registered or has been deactivated.")
        rows = cur.execute("select * from caseward.user_memberships(%s)", [user["id"]]).fetchall()
    return Principal(
        user_id=user["id"],
        display_name=user["display_name"],
        staff_role=user["staff_role"],
        memberships=tuple(Membership(**r) for r in rows),
    )
