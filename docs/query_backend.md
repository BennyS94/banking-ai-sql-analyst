# Query backend

The synchronous FastAPI backend starts with:

```powershell
python -m uvicorn backend.app.main:app --reload
```

`GET /health` checks only that the application process is serving requests. It
does not contact PostgreSQL.

## Runtime PostgreSQL configuration

The backend database boundary reads only these runtime variables:

- `BANKING_READER_USER`: the dedicated analytical role name;
- `BANKING_READER_DATABASE_URL`: its SQLAlchemy PostgreSQL connection URL.

The runtime URL must use the project's installed `postgresql+psycopg` driver.
Malformed URLs, other dialects and unsupported driver names fail through a
sanitized configuration error before runtime access is attempted.

The URL username must match `BANKING_READER_USER`. Owner/migration/loader
configuration remains under `DATABASE_URL` and is not read by the FastAPI
runtime engine. Keep all real passwords in the untracked local `.env` file.

The runtime engine is synchronous SQLAlchemy 2.x. Connectivity checks execute a
trivial read and return only the effective user and database name. Failures are
reported through a controlled application exception without returning a URL or
password. Connections used through the FastAPI dependency are always returned
to the pool when the request ends. FastAPI's lifespan disposes the cached
runtime engine at application shutdown without initializing an unused engine.

## Banking schema introspection

`backend.app.db.schema.introspect_banking_schema` reads base-table metadata from
the PostgreSQL `banking` schema through the runtime engine. PostgreSQL remains
the source of truth; the backend does not keep a parallel table definition.

The typed result includes table and column names, compiled PostgreSQL types,
nullability, primary keys and foreign-key targets. Tables and foreign keys are
sorted, while columns and key columns retain their stable PostgreSQL definition
order. Metadata outside `banking`, including system schemas and Alembic's
internal table, is not inspected.

## Schema API

`GET /api/v1/database/schema` exposes the introspected metadata through explicit
response models. The JSON contract uses `schema`, `tables`, `columns`,
`primary_key` and `foreign_keys`; PostgreSQL connection details are never part
of the response. If metadata cannot be read, the endpoint returns a sanitized
`503 Service Unavailable` response.

## Internal query executor

`ReadOnlyQueryExecutor` executes SQL that a future upstream safety layer has
already approved. It does not parse or validate SQL and is not exposed through
an HTTP endpoint. Its SQLAlchemy engine must come from the analytical reader
runtime boundary.

Results contain ordered column names, rows, row count and elapsed milliseconds.
Integers, strings, booleans and nulls retain their JSON scalar representation;
precision-sensitive PostgreSQL numeric values become decimal strings; dates and
timestamps use ISO 8601 strings. Unsupported or non-finite values fail through
an explicit normalization error. PostgreSQL execution errors are returned as a
sanitized application error, and the connection is returned to the pool on
both success and failure.
