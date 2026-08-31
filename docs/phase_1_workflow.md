# Phase 1 Data Foundation Workflow

With Docker PostgreSQL running and owner/reader environment variables configured,
the complete data foundation is reproducible without manual data edits:

```powershell
python -m banking_data.cleaning --raw-dir data/raw --output-dir data/processed
python -m alembic upgrade head
python -m banking_data.loading --processed-dir data/processed
python -m banking_data.role_management
python -m banking_data.integrity --raw-dir data/raw --processed-dir data/processed
```

The integrity command requires owner `DATABASE_URL` and read-only
`BANKING_READER_DATABASE_URL`. It checks raw/accepted/rejected/duplicate totals,
processed hashes and row counts, processed/database primary-key sets, required
values, FK orphans, approved lookup and numeric domains, preserved source
characteristics, analytical joins, and the PostgreSQL reader privilege boundary.

For an intentional rebuild of a database that is already populated, replace the
loader command with its explicit `--replace` form. Database reset and lifecycle
commands remain documented in `development_database.md`.
