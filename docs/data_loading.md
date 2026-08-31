# PostgreSQL Data Loading

The loader accepts only the complete processed dataset produced by the cleaning
pipeline. It verifies the cleaning manifest, file hashes, columns and accepted
row counts before committing database changes.

Build processed data, migrate the database and load it:

```powershell
python -m banking_data.cleaning --raw-dir data/raw --output-dir data/processed
python -m alembic upgrade head
python -m banking_data.loading --processed-dir data/processed
```

`DATABASE_URL` must contain the migration/loader connection. Inserts follow
foreign-key dependency order and the complete load is one transaction. The
command prints a JSON row summary after a successful commit.

The safe rerun default refuses to load if any target table contains data. To
replace the full development dataset intentionally, use:

```powershell
python -m banking_data.loading --processed-dir data/processed --replace
```

Replacement deletes children before parents and reloads all tables within the
same transaction. Any parse, manifest, schema or database error rolls back the
replacement and preserves the previously committed dataset.
