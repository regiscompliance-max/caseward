# caseward

A compliance operations platform for Awaab's Law and related duties, for UK
social landlords, their contractors and managing agents.

Start here:

- [docs/BRIEF.md](docs/BRIEF.md): master brief (every role reads this first)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, security boundaries, data model
- [docs/api/](docs/api/): API specification and conventions
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

Apply `backend/db/migrations/*.sql` in order. Then run each API with its own
database login: a member of `caseward_app` for the client API, and of
`caseward_app` plus `caseward_staff` for the operations API.

```sh
export CASEWARD_OIDC_ISSUER=https://your-idp/ CASEWARD_OIDC_JWKS_URL=https://your-idp/.well-known/jwks.json
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

The APIs need the same `CASEWARD_S3_*` settings to issue upload and download
links. For local development, use `CASEWARD_STORAGE_BACKEND=local` and
`CASEWARD_LOCAL_STORAGE_DIR`. Run as many copies as
you like: two workers never claim the same job.
