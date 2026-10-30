import os
from dataclasses import dataclass
from pathlib import Path

from caseward.api.spec import DEFAULT_SPEC_DIR


@dataclass(frozen=True)
class Settings:
    database_url: str
    oidc_issuer: str
    oidc_audience: str
    oidc_jwks_url: str | None = None
    # Check every response against the spec. On in development and tests.
    validate_responses: bool = False
    spec_dir: Path = DEFAULT_SPEC_DIR
    pool_max_size: int = 10

    @classmethod
    def from_env(cls, prefix: str) -> "Settings":
        """prefix is CASEWARD_CLIENT or CASEWARD_OPS: each API has its own database credentials."""
        env = os.environ
        return cls(
            database_url=env[f"{prefix}_DATABASE_URL"],
            oidc_issuer=env["CASEWARD_OIDC_ISSUER"],
            oidc_audience=env[f"{prefix}_OIDC_AUDIENCE"],
            oidc_jwks_url=env["CASEWARD_OIDC_JWKS_URL"],
            validate_responses=env.get("CASEWARD_VALIDATE_RESPONSES", "") == "1",
            spec_dir=Path(env.get("CASEWARD_API_SPEC_DIR", DEFAULT_SPEC_DIR)),
            pool_max_size=int(env.get("CASEWARD_DB_POOL_MAX", "10")),
        )
