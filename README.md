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
