# Banking Data Cleaning Contract

This document describes the implemented Phase 1 transformation from immutable
CSV files in `data/raw/` to generated CSV files in `data/processed/`.

Rebuild all processed data with:

```powershell
python -m banking_data.cleaning --raw-dir data/raw --output-dir data/processed
```

The output directory is replaced only after a complete successful run. Existing
non-empty directories without a `cleaning_summary.json` marker are not replaced.

## Applied policy

- Exact duplicate rows retain the first source-order copy; only excess copies are removed.
- Every primary-key and foreign-key field is required and normalized to a positive integer.
- Rows with a missing/invalid primary key or missing/invalid foreign-key reference are rejected.
- Conflicting duplicate primary keys remaining after exact-row deduplication stop the run.
- Missing descriptive fields remain NULL. No values are imputed.
- Exact empty CSV fields are treated as missing; textual tokens are not guessed to mean NULL.
- Missing account opening dates, customer names, address fields and loan dates remain NULL.
- Invalid customer dates of birth become NULL; unusually recent but parseable dates are preserved.
- Missing or invalid transaction dates reject the transaction.
- Future loan estimated-end dates and negative account balances are preserved.
- Transaction amounts retain their source magnitude and are never sign-adjusted by transaction type.
- Decimal values are serialized without value-changing rounding: amounts/balances/principal use two
  decimal places and interest rates use four.
- No fuzzy correction or entity deduplication is performed.

The audited source contains no missing, unparseable, non-finite or over-precision
required numerical values. Because no corrective policy exists for such values,
encountering one stops the run without replacing the previous processed output.
The same fail-fast rule applies to unsupported account/loan date formats,
unexpected columns, missing source files and conflicting non-identical rows with
the same primary identifier.

Dates are parsed only through explicit formats: source timestamp, ISO date/time,
ISO date, `YYYY/MM/DD`, US `MM/DD/YYYY`, and US `MM.DD.YYYY`. Dates are emitted as
`YYYY-MM-DD` for birth dates and ISO timestamps with microseconds for date-time fields.

## Country normalization

Only these exact audited values are mapped to `United States`:

- `Pnited States`
- `Unitd States`
- `United Slates`
- `United StXtes`
- `United Staes`
- `United State`
- `United StateR`
- `United vtates`
- `United0States`
- `UnitedcStates`

No fuzzy country matching is used. Other values pass through unchanged.

## Diagnostics and reconciliation

`data/processed/cleaning_summary.json` records per-file and total input,
duplicate, accepted and rejected counts; stable rejection/normalization reason
counts; parse failures; source/output hashes; remaining null counts; and cleaned
relationship cardinalities.

Rejected rows are written under `data/processed/rejected/` with their original
values, physical source row number and deterministic semicolon-separated reason
codes. Exact duplicate copies are counted in the summary rather than classified
as rejected rows.

For every file:

```text
input rows = duplicate copies removed + accepted rows + rejected rows
```

Processed data is generated and ignored by Git. The raw files remain the canonical source.
