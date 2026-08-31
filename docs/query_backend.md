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

The URL username must match `BANKING_READER_USER`. Owner/migration/loader
configuration remains under `DATABASE_URL` and is not read by the FastAPI
runtime engine. Keep all real passwords in the untracked local `.env` file.

The runtime engine is synchronous SQLAlchemy 2.x. Connectivity checks execute a
trivial read and return only the effective user and database name. Failures are
reported through a controlled application exception without returning a URL or
password. Connections used through the FastAPI dependency are always returned
to the pool when the request ends.

## Banking schema introspection

`backend.app.db.schema.introspect_banking_schema` reads base-table metadata from
the PostgreSQL `banking` schema through the runtime engine. PostgreSQL remains
the source of truth; the backend does not keep a parallel table definition.

The typed result includes table and column names, compiled PostgreSQL types,
nullability, primary keys and foreign-key targets. Tables and foreign keys are
sorted, while columns and key columns retain their stable PostgreSQL definition
order. Metadata outside `banking`, including system schemas and Alembic's
internal table, is not inspected.
