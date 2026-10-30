# caseward

A compliance operations platform for Awaab's Law and related duties, for UK
social landlords, their contractors and managing agents.

Start here:

- [docs/BRIEF.md](docs/BRIEF.md): master brief (every role reads this first)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, security boundaries, data model
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
