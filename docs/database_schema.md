# Banking Database Schema

The Phase 1 relational model is managed by Alembic and stored in the PostgreSQL
`banking` schema. It contains the eleven cleaned dataset tables; migration and
ownership access uses `DATABASE_URL`.

Apply the schema to an empty development database:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://banking_owner:<password>@localhost:5432/banking_ai"
python -m alembic upgrade head
```

Inspect the current revision with `python -m alembic current`. During local
development, roll back the schema with `python -m alembic downgrade base`.

The database naming, types, nullability, keys, checks and indexes follow the
approved Phase 1 data contract. Processed CSV files retain their source-facing
CamelCase columns; conversion to snake_case and database types belongs to the
loader boundary.
