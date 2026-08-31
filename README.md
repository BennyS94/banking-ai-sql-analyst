# banking-ai-sql-analyst

The project is intended to provide natural-language banking analytics by
translating questions into validated, read-only PostgreSQL queries.

Phase 1 implements the trusted data foundation: audited and cleaned banking
data, PostgreSQL schema and migrations, transactional loading, a
least-privilege read-only database role, and end-to-end integrity validation.
Phase 2 implements the query backend: FastAPI process health, least-privilege
PostgreSQL runtime access, banking schema introspection and its typed API, plus
an internal executor for already-approved analytical SQL. Phase 3 implements
structured, schema-grounded Groq NL-to-SQL generation and an opt-in live smoke
workflow. Phase 4 validates untrusted generated SQL and executes only approved
queries through the least-privilege runtime boundary. Phase 5 adds the
Streamlit presentation layer over the public FastAPI interface.

Install the backend and test dependencies, then start the development API:

```powershell
python -m pip install -e ".[test]"
python -m uvicorn backend.app.main:app --reload
```

In a second terminal, configure the public API URL and start the frontend:

```powershell
$env:API_BASE_URL = "http://localhost:8000"
python -m streamlit run frontend/app.py
```

The frontend reads only its API URL and HTTP timeout settings. Database and
Groq credentials remain backend-only.

## Raw dataset audit

The committed CSV files under `data/raw/` are immutable source data. Rebuild the
read-only audit reports after installing the project in the active environment:

```powershell
python -m pip install -e .
```

Then run:

```powershell
python -m banking_data.audit --raw-dir data/raw --output-dir data/audit --reference-date 2026-08-31
```

The command writes a machine-readable JSON summary and a human-readable Markdown
report under `data/audit/`. It reports source anomalies without cleaning or
rewriting the raw files.

Run the audit tests with:

```powershell
python -m unittest discover -s tests -v
```

## Banking data cleaning

After Decision Gate A approval, rebuild the generated processed dataset with:

```powershell
python -m banking_data.cleaning --raw-dir data/raw --output-dir data/processed
```

The approved transformation and diagnostic contract is documented in
[`docs/data_cleaning.md`](docs/data_cleaning.md). Generated processed files remain
ignored by Git and can always be rebuilt from the immutable raw CSV files.

## PostgreSQL development service

Local PostgreSQL setup and lifecycle commands are documented in
[`docs/development_database.md`](docs/development_database.md).

The approved relational schema and Alembic migration commands are documented in
[`docs/database_schema.md`](docs/database_schema.md).

The transactional processed-data loading workflow is documented in
[`docs/data_loading.md`](docs/data_loading.md).

The PostgreSQL analytical reader setup is documented in
[`docs/read_only_role.md`](docs/read_only_role.md).

The complete Phase 1 rebuild and validation sequence is documented in
[`docs/phase_1_workflow.md`](docs/phase_1_workflow.md).

FastAPI startup and least-privilege runtime PostgreSQL configuration are
documented in [`docs/query_backend.md`](docs/query_backend.md).

Offline and opt-in live NL-to-SQL generation checks are documented in
[`docs/nl_to_sql_generation.md`](docs/nl_to_sql_generation.md).
