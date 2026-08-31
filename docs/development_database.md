# PostgreSQL Development Database

The local development database runs PostgreSQL 17 through Docker Compose. No
local PostgreSQL installation is required, but Docker with Compose support is a
prerequisite.

## Configure

Copy `.env.example` to `.env`, set a non-empty development-only
`POSTGRES_PASSWORD`, and keep `.env` outside Git. The default database, owner and
host port are:

- database: `banking_ai`;
- owner: `banking_owner`;
- host port: `5432`;
- container port: `5432`.

`DATABASE_URL` is reserved for the Python database integration added in the
schema task. Do not commit a populated URL.

PostgreSQL integration tests use the separate
`BANKING_TEST_OWNER_DATABASE_URL`. It must explicitly identify the banking
project's owner connection and must use `postgresql+psycopg`. The test harness
never falls back to the machine-level `DATABASE_URL`; this prevents an unrelated
project URL from receiving temporary `banking_test_*` databases. For local
development, both variables may contain the same project owner URL, but they
remain separate configuration boundaries.

Run the complete suite with:

```powershell
$env:BANKING_TEST_OWNER_DATABASE_URL = "postgresql+psycopg://banking_owner:<password>@localhost:5432/banking_ai"
python -m unittest discover -s tests -v
```

## Start and inspect

```powershell
docker compose up -d postgres
docker compose ps
docker compose logs postgres
```

Wait until `docker compose ps` reports the service as healthy.

Verify connectivity inside the container:

```powershell
docker compose exec postgres psql -U banking_owner -d banking_ai -c "SELECT current_database(), current_user;"
```

If `POSTGRES_USER` or `POSTGRES_DB` were overridden in `.env`, use those values
in the command.

## Stop and restart

Stop the service while retaining the named development volume:

```powershell
docker compose down
```

Start it again with `docker compose up -d postgres`. The database remains in the
`postgres_data` named volume across normal stop/start cycles.

## Explicit reset

The following command permanently removes the Compose-managed development
database volume. Run it only when an intentional clean reset is required:

```powershell
docker compose down --volumes
```

The next `docker compose up -d postgres` initializes a new empty database using
the current environment settings.
