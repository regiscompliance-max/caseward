# caseward

Compliance case management for landlords and contractors: certificates,
defects, hazard cases, legal deadlines and tenant letters.

- Architecture: [docs/adr/0001-architecture-and-stack.md](docs/adr/0001-architecture-and-stack.md)
- Data model: [docs/data-model.md](docs/data-model.md)

## Backend

```sh
cd backend
pip install -e '.[dev]'
# Needs Postgres 16+; the tests create and drop their own database.
export CASEWARD_TEST_ADMIN_DSN=postgresql://postgres:postgres@localhost:5432/postgres
pytest
```

The tests connect as non-superuser roles, because superusers bypass
row-level security.
