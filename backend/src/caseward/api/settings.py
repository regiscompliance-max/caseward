import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from caseward.api.spec import DEFAULT_SPEC_DIR


@dataclass(frozen=True)
class IdentityProvider:
    issuer: str
    jwks_url: str
    audience: str
    mfa_amr: tuple[str, ...] = ("mfa",)
    mfa_acr: tuple[str, ...] = ()


@dataclass(frozen=True)
class Settings:
    database_url: str
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str | None = None
    identity_providers: tuple[IdentityProvider, ...] = field(default_factory=tuple)
    # Check every response against the spec. On in development and tests.
    validate_responses: bool = False
    spec_dir: Path = DEFAULT_SPEC_DIR
    pool_max_size: int = 10

    @classmethod
    def from_env(cls, prefix: str) -> "Settings":
        """prefix is CASEWARD_CLIENT or CASEWARD_OPS: each API has its own database credentials."""
        env = os.environ
        # Several trusted identity providers, as JSON:
        #   [{"issuer": "...", "jwks_url": "...", "audience": "...", "mfa_amr": ["mfa"], "mfa_acr": []}]
        # or, for one provider, CASEWARD_OIDC_ISSUER, CASEWARD_OIDC_JWKS_URL and {prefix}_OIDC_AUDIENCE.
        configured = json.loads(env.get(f"{prefix}_OIDC_PROVIDERS", "[]"))
        providers = tuple(IdentityProvider(p["issuer"], p["jwks_url"], p["audience"],
                                           tuple(p.get("mfa_amr", ["mfa"])), tuple(p.get("mfa_acr", [])))
                          for p in configured)
        if not providers:
            providers = (IdentityProvider(env["CASEWARD_OIDC_ISSUER"], env["CASEWARD_OIDC_JWKS_URL"],
                                          env[f"{prefix}_OIDC_AUDIENCE"]),)
        return cls(
            database_url=env[f"{prefix}_DATABASE_URL"],
            identity_providers=providers,
            validate_responses=env.get("CASEWARD_VALIDATE_RESPONSES", "") == "1",
            spec_dir=Path(env.get("CASEWARD_API_SPEC_DIR", DEFAULT_SPEC_DIR)),
            pool_max_size=int(env.get("CASEWARD_DB_POOL_MAX", "10")),
        )
