# caseward

A compliance operations platform for Awaab's Law and related duties, for UK
social landlords, their contractors and managing agents.

Start here:

- [docs/BRIEF.md](docs/BRIEF.md): master brief (every role reads this first)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, security boundaries, data model
- [docs/api/](docs/api/): API specification and conventions
- [frontend/ops-console/](frontend/ops-console/): operations console (decision 0012)
- [docs/decisions/](docs/decisions/): decision records
- [docs/handoffs/](docs/handoffs/): hand-off notes, newest last

Use only synthetic or anonymised documents until the security gate is passed.

## Backend

```sh
cd backend
pip install -e '.[dev]'
# Needs Postgres 16+. The tests create and drop their own database.
export CASEWARD_TEST_ADMIN_DSN=postgresql://postgres:postgres@localhost:5432/postgres
pytest
```

The tests connect as non-superuser roles, because superusers bypass
row-level security.

### Running the APIs

Apply `backend/db/migrations/*.sql` in order, **as the schema owner: a role
with CREATEROLE and CREATE on the database, but not a superuser and without
BYPASSRLS** (decision 0010). The tests do the same. Then run each API with its own
database login: a member of `caseward_app` for the client API, and of
`caseward_app` plus `caseward_staff` for the operations API.

```sh
# Trusted identity providers per API (several allowed), each with its multi-factor rule:
export CASEWARD_OPS_OIDC_PROVIDERS='[{"issuer": "https://login.example/", "jwks_url": "https://login.example/keys",
  "audience": "caseward-ops", "mfa_amr": ["mfa"]}]'
export CASEWARD_CLIENT_OIDC_PROVIDERS='[...]'   # the same shape; one entry per landlord SSO
# (one provider only: CASEWARD_OIDC_ISSUER, CASEWARD_OIDC_JWKS_URL and CASEWARD_{CLIENT,OPS}_OIDC_AUDIENCE)
export CASEWARD_CLIENT_DATABASE_URL=postgresql://client_api@db/caseward CASEWARD_CLIENT_OIDC_AUDIENCE=caseward-client
export CASEWARD_OPS_DATABASE_URL=postgresql://ops_api@db/caseward CASEWARD_OPS_OIDC_AUDIENCE=caseward-ops
uvicorn caseward.api.main:client_app --factory --port 8000
uvicorn caseward.api.main:operations_app --factory --port 8001
```

Set `CASEWARD_VALIDATE_RESPONSES=1` in development to check every response
against the spec.

### Running the worker

```sh
export CASEWARD_WORKER_DATABASE_URL=postgresql://worker@db/caseward   # member of caseward_app and caseward_worker
python -m caseward.worker
```

It calculates deadlines, sweeps for missed deadlines and raises alerts every
minute, and puts back jobs left by a stopped worker. It also scans, renders
and reads documents, so it needs:

```sh
export CASEWARD_S3_BUCKET=caseward-documents CASEWARD_S3_REGION=eu-west-2 CASEWARD_S3_KMS_KEY_ID=alias/caseward
export CASEWARD_CLAMD_HOST=clamd    # ClamAV daemon
# and the tesseract binary with English data installed in the image
```

AI extraction stays off until a provider is configured **with its UK
residency approval** (decision 0008):

```sh
export CASEWARD_AI_API_KEY=... CASEWARD_AI_MODEL=... CASEWARD_AI_BASE_URL=https://<approved-endpoint>/v1
export CASEWARD_AI_ALLOWED_HOSTS=<approved-endpoint> CASEWARD_AI_RESIDENCY_APPROVAL="<signed agreement reference>"
```

Score a prompt or model change against the labelled set before it ships:
`python -m caseward.ai.evaluate path/to/labelled-set`.

The APIs need the same `CASEWARD_S3_*` settings to issue upload and download
links. For local development, use `CASEWARD_STORAGE_BACKEND=local` and
`CASEWARD_LOCAL_STORAGE_DIR`. Run as many copies as
you like: two workers never claim the same job.

## Operations console

```sh
cd frontend/ops-console
npm ci
export CASEWARD_OPS_API_URL=http://localhost:8001/ops/v1 CASEWARD_CONSOLE_URL=http://localhost:3100
export CASEWARD_CONSOLE_OIDC_PROVIDERS='[{"id": "staff", "name": "Caseward staff", "issuer": "https://login.example/",
  "client_id": "...", "client_secret": "...", "scope": "openid email", "authorization_params": {"audience": "caseward-ops"}}]'
CASEWARD_DEV_SIGN_IN=1 npm run dev   # development token sign-in as well; never set in production
npm run typecheck && npm test   # unit and component tests
npm run e2e                     # browser tests against a mocked API, with axe accessibility scans
npm run gen:api                 # regenerate API types after changing docs/api/operations.yaml
```
