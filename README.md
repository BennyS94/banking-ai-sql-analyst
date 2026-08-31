# banking-ai-sql-analyst
AI-powered banking analytics application that converts natural-language questions into validated, read-only PostgreSQL queries.

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
