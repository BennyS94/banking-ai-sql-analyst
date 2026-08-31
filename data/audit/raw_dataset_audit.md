# Raw Banking Dataset Audit

Reference date for future-date reporting: `2026-08-31`.
This report records source evidence only; it applies no cleaning decisions.

## Dataset overview

| File | Rows | Columns | Read error |
|---|---:|---:|---|
| `account_statuses.csv` | 3 | 2 | None |
| `account_types.csv` | 5 | 2 | None |
| `accounts.csv` | 1667 | 6 | None |
| `addresses.csv` | 1222 | 4 | None |
| `branches.csv` | 50 | 3 | None |
| `customer_types.csv` | 3 | 2 | None |
| `customers.csv` | 1111 | 6 | None |
| `loan_statuses.csv` | 3 | 2 | None |
| `loans.csv` | 333 | 7 | None |
| `transaction_types.csv` | 4 | 2 | None |
| `transactions.csv` | 50000 | 8 | None |

Expected CSVs: 11; discovered: 11.
Missing expected files: None.
Unexpected files: None.

## Candidate relationships

| Source | Target | Valid | Null | Orphan | Target duplicate-key rows |
|---|---|---:|---:|---:|---:|
| `customers.csv.CustomerTypeID` | `customer_types.csv.CustomerTypeID` | 1111 | 0 | 0 | 0 |
| `customers.csv.AddressID` | `addresses.csv.AddressID` | 1111 | 0 | 0 | 24 |
| `accounts.csv.CustomerID` | `customers.csv.CustomerID` | 1667 | 0 | 0 | 22 |
| `accounts.csv.AccountTypeID` | `account_types.csv.AccountTypeID` | 1667 | 0 | 0 | 0 |
| `accounts.csv.AccountStatusID` | `account_statuses.csv.AccountStatusID` | 1667 | 0 | 0 | 0 |
| `branches.csv.AddressID` | `addresses.csv.AddressID` | 50 | 0 | 0 | 24 |
| `loans.csv.AccountID` | `accounts.csv.AccountID` | 333 | 0 | 0 | 32 |
| `loans.csv.LoanStatusID` | `loan_statuses.csv.LoanStatusID` | 333 | 0 | 0 | 0 |
| `transactions.csv.AccountOriginID` | `accounts.csv.AccountID` | 50000 | 0 | 0 | 32 |
| `transactions.csv.AccountDestinationID` | `accounts.csv.AccountID` | 50000 | 0 | 0 | 32 |
| `transactions.csv.BranchID` | `branches.csv.BranchID` | 50000 | 0 | 0 | 0 |
| `transactions.csv.TransactionTypeID` | `transaction_types.csv.TransactionTypeID` | 50000 | 0 | 0 | 0 |

## account_statuses.csv

SHA-256: `50e9da75eafe6226dfee4d436e63ea8b2f01f5f6f6d7529b87243e1625393560`

### Columns and missing values

| Column | Inferred dtype | Nulls | Null % |
|---|---|---:|---:|
| `AccountStatusID` | `int64` | 0 | 0.0000 |
| `StatusName` | `object` | 0 | 0.0000 |

### Duplicates and candidate keys

Exact duplicate rows involved: 0; excess copies: 0.

| Identifier | Likely primary identifier | Nulls | Distinct | Duplicate values | Conflicting values | Clean PK candidate |
|---|---|---:|---:|---:|---:|---|
| `AccountStatusID` | True | 0 | 3 | 0 | 0 | True |

### Low-cardinality values

- `StatusName`: `Active` (1), `Closed` (1), `Inactive` (1)

## account_types.csv

SHA-256: `2df6fae9581dd9a2f2cbadc56fe60cf5c3565ae0b7717108e130073980395c3c`

### Columns and missing values

| Column | Inferred dtype | Nulls | Null % |
|---|---|---:|---:|
| `AccountTypeID` | `int64` | 0 | 0.0000 |
| `TypeName` | `object` | 0 | 0.0000 |

### Duplicates and candidate keys

Exact duplicate rows involved: 0; excess copies: 0.

| Identifier | Likely primary identifier | Nulls | Distinct | Duplicate values | Conflicting values | Clean PK candidate |
|---|---|---:|---:|---:|---:|---|
| `AccountTypeID` | True | 0 | 5 | 0 | 0 | True |

### Low-cardinality values

- `TypeName`: `Business` (1), `Checking` (1), `Payroll` (1), `Savings` (1), `Youth` (1)

## accounts.csv

SHA-256: `029190a34daf8d4711bc5fa1257a427e781295f37d4c647d89cef07ea640c5f0`

### Columns and missing values

| Column | Inferred dtype | Nulls | Null % |
|---|---|---:|---:|
| `AccountID` | `int64` | 0 | 0.0000 |
| `CustomerID` | `int64` | 0 | 0.0000 |
| `AccountTypeID` | `int64` | 0 | 0.0000 |
| `AccountStatusID` | `int64` | 0 | 0.0000 |
| `Balance` | `float64` | 0 | 0.0000 |
| `OpeningDate` | `object` | 33 | 1.9796 |

### Duplicates and candidate keys

Exact duplicate rows involved: 32; excess copies: 16.

| Identifier | Likely primary identifier | Nulls | Distinct | Duplicate values | Conflicting values | Clean PK candidate |
|---|---|---:|---:|---:|---:|---|
| `AccountID` | True | 0 | 1651 | 16 | 0 | False |
| `CustomerID` | False | 0 | 862 | 488 | 485 | False |
| `AccountTypeID` | False | 0 | 5 | 5 | 5 | False |
| `AccountStatusID` | False | 0 | 3 | 3 | 3 | False |

Sample duplicated identifier values:

- `AccountID`: `['200002', '200118', '200147', '200186', '200332', '200412', '200430', '200517', '200561', '200656']`
- `CustomerID`: `['10002', '10004', '10006', '10009', '10010', '10020', '10023', '10024', '10027', '10030']`
- `AccountTypeID`: `['1', '2', '3', '4', '5']`
- `AccountStatusID`: `['1', '2', '3']`

### Numerical fields

| Column | Parse failures | Min | Median | Max | Negative | Zero | IQR outliers |
|---|---:|---:|---:|---:|---:|---:|---:|
| `Balance` | 0 | -486.68 | 49112.5 | 99828.98 | 10 | 0 | 0 |

### Date fields

| Column | Formats | Parse failures | Nulls | Min | Max | After reference date |
|---|---|---:|---:|---|---|---:|
| `OpeningDate` | YYYY-MM-DD HH:MM:SS.ffffff: 1634 | 0 | 33 | 2018-01-03T00:00:00 | 2026-07-06T15:01:42.900415 | 0 |

## addresses.csv

SHA-256: `72ac38f21aa2fa1b898496ac4e3058770027fa9c6f55114d1af95bbaa1b39398`

### Columns and missing values

| Column | Inferred dtype | Nulls | Null % |
|---|---|---:|---:|
| `AddressID` | `int64` | 0 | 0.0000 |
| `Street` | `object` | 24 | 1.9640 |
| `City` | `object` | 26 | 2.1277 |
| `Country` | `object` | 24 | 1.9640 |

### Duplicates and candidate keys

Exact duplicate rows involved: 24; excess copies: 12.

| Identifier | Likely primary identifier | Nulls | Distinct | Duplicate values | Conflicting values | Clean PK candidate |
|---|---|---:|---:|---:|---:|---|
| `AddressID` | True | 0 | 1210 | 12 | 0 | False |

Sample duplicated identifier values:

- `AddressID`: `['109', '1200', '179', '215', '224', '331', '405', '425', '542', '651']`

### Low-cardinality values

- `Country`: `None` (24), `Pnited States` (1), `Unitd States` (3), `United Slates` (1), `United StXtes` (1), `United Staes` (1), `United State` (1), `United StateR` (1), `United States` (1186), `United vtates` (1), `United0States` (1), `UnitedcStates` (1)

## branches.csv

SHA-256: `c3096f0787db77e8125f1104f3c166362c5c50dae75e46b7ced43cc408847b2b`

### Columns and missing values

| Column | Inferred dtype | Nulls | Null % |
|---|---|---:|---:|
| `BranchID` | `int64` | 0 | 0.0000 |
| `BranchName` | `object` | 0 | 0.0000 |
| `AddressID` | `int64` | 0 | 0.0000 |

### Duplicates and candidate keys

Exact duplicate rows involved: 0; excess copies: 0.

| Identifier | Likely primary identifier | Nulls | Distinct | Duplicate values | Conflicting values | Clean PK candidate |
|---|---|---:|---:|---:|---:|---|
| `BranchID` | True | 0 | 50 | 0 | 0 | True |
| `AddressID` | False | 0 | 50 | 0 | 0 | False |

## customer_types.csv

SHA-256: `c9fa43310fb528c32b83e75b76014a00e92f14ff08a1119be0572a0be3941187`

### Columns and missing values

| Column | Inferred dtype | Nulls | Null % |
|---|---|---:|---:|
| `CustomerTypeID` | `int64` | 0 | 0.0000 |
| `TypeName` | `object` | 0 | 0.0000 |

### Duplicates and candidate keys

Exact duplicate rows involved: 0; excess copies: 0.

| Identifier | Likely primary identifier | Nulls | Distinct | Duplicate values | Conflicting values | Clean PK candidate |
|---|---|---:|---:|---:|---:|---|
| `CustomerTypeID` | True | 0 | 3 | 0 | 0 | True |

### Low-cardinality values

- `TypeName`: `Individual` (1), `Large Enterprise` (1), `Small Business` (1)

## customers.csv

SHA-256: `d86b77581e84646c876100740223e156bda4f9379643b877be4e91ed4f0c4478`

### Columns and missing values

| Column | Inferred dtype | Nulls | Null % |
|---|---|---:|---:|
| `CustomerID` | `int64` | 0 | 0.0000 |
| `FirstName` | `object` | 22 | 1.9802 |
| `LastName` | `object` | 23 | 2.0702 |
| `DateOfBirth` | `object` | 0 | 0.0000 |
| `AddressID` | `int64` | 0 | 0.0000 |
| `CustomerTypeID` | `int64` | 0 | 0.0000 |

### Duplicates and candidate keys

Exact duplicate rows involved: 22; excess copies: 11.

| Identifier | Likely primary identifier | Nulls | Distinct | Duplicate values | Conflicting values | Clean PK candidate |
|---|---|---:|---:|---:|---:|---|
| `CustomerID` | True | 0 | 1100 | 11 | 0 | False |
| `AddressID` | False | 0 | 720 | 281 | 276 | False |
| `CustomerTypeID` | False | 0 | 3 | 3 | 3 | False |

Sample duplicated identifier values:

- `CustomerID`: `['10056', '10071', '10227', '10526', '10602', '10739', '10909', '10937', '11031', '11041']`
- `AddressID`: `['1001', '1007', '1010', '1012', '1015', '1018', '103', '1031', '1032', '1035']`
- `CustomerTypeID`: `['1', '2', '3']`

### Date fields

| Column | Formats | Parse failures | Nulls | Min | Max | After reference date |
|---|---|---:|---:|---|---|---:|
| `DateOfBirth` | DD/MM/YYYY: 2, YYYY-MM-DD: 2, YYYY-MM-DD HH:MM:SS.ffffff: 1079, other: 28 | 24 | 0 | 1960-01-18T00:00:00 | 2026-07-06T15:01:42.854835 | 0 |

Sample date parse failures:

- `DateOfBirth`: `['1962-15-01', '1974-31-01', 'NaT']`

## loan_statuses.csv

SHA-256: `f1a9a247d6e6d4d6ab8de3cfeb83a32e43128cb8461d0f6396d26a9e1caed448`

### Columns and missing values

| Column | Inferred dtype | Nulls | Null % |
|---|---|---:|---:|
| `LoanStatusID` | `int64` | 0 | 0.0000 |
| `StatusName` | `object` | 0 | 0.0000 |

### Duplicates and candidate keys

Exact duplicate rows involved: 0; excess copies: 0.

| Identifier | Likely primary identifier | Nulls | Distinct | Duplicate values | Conflicting values | Clean PK candidate |
|---|---|---:|---:|---:|---:|---|
| `LoanStatusID` | True | 0 | 3 | 0 | 0 | True |

### Low-cardinality values

- `StatusName`: `Active` (1), `Overdue` (1), `Paid Off` (1)

## loans.csv

SHA-256: `6768101ca4064ace21d48ec58235ea4cda74ee049fbaf50addc1a5de1632887f`

### Columns and missing values

| Column | Inferred dtype | Nulls | Null % |
|---|---|---:|---:|
| `LoanID` | `int64` | 0 | 0.0000 |
| `AccountID` | `int64` | 0 | 0.0000 |
| `LoanStatusID` | `int64` | 0 | 0.0000 |
| `PrincipalAmount` | `float64` | 0 | 0.0000 |
| `InterestRate` | `float64` | 0 | 0.0000 |
| `StartDate` | `object` | 6 | 1.8018 |
| `EstimatedEndDate` | `object` | 6 | 1.8018 |

### Duplicates and candidate keys

Exact duplicate rows involved: 6; excess copies: 3.

| Identifier | Likely primary identifier | Nulls | Distinct | Duplicate values | Conflicting values | Clean PK candidate |
|---|---|---:|---:|---:|---:|---|
| `LoanID` | True | 0 | 330 | 3 | 0 | False |
| `AccountID` | False | 0 | 298 | 34 | 31 | False |
| `LoanStatusID` | False | 0 | 3 | 3 | 3 | False |

Sample duplicated identifier values:

- `LoanID`: `['400091', '400322', '400329']`
- `AccountID`: `['200017', '200056', '200075', '200119', '200186', '200311', '200397', '200441', '200475', '200498']`
- `LoanStatusID`: `['1', '2', '3']`

### Numerical fields

| Column | Parse failures | Min | Median | Max | Negative | Zero | IQR outliers |
|---|---:|---:|---:|---:|---:|---:|---:|
| `PrincipalAmount` | 0 | 1128.49 | 52255.85 | 99830.33 | 0 | 0 | 0 |
| `InterestRate` | 0 | 0.0301 | 0.0917 | 0.15 | 0 | 0 | 0 |

### Date fields

| Column | Formats | Parse failures | Nulls | Min | Max | After reference date |
|---|---|---:|---:|---|---|---:|
| `StartDate` | YYYY-MM-DD HH:MM:SS.ffffff: 327 | 0 | 6 | 2021-01-01T00:00:00 | 2026-08-29T15:01:44.338382 | 0 |
| `EstimatedEndDate` | YYYY-MM-DD HH:MM:SS.ffffff: 327 | 0 | 6 | 2022-01-25T00:00:00 | 2028-01-12T00:00:00 | 53 |

## transaction_types.csv

SHA-256: `8489185085dbbe6b3480adfb4349abfe155cd66f191bf8ba3999a4e8a210ad1c`

### Columns and missing values

| Column | Inferred dtype | Nulls | Null % |
|---|---|---:|---:|
| `TransactionTypeID` | `int64` | 0 | 0.0000 |
| `TypeName` | `object` | 0 | 0.0000 |

### Duplicates and candidate keys

Exact duplicate rows involved: 0; excess copies: 0.

| Identifier | Likely primary identifier | Nulls | Distinct | Duplicate values | Conflicting values | Clean PK candidate |
|---|---|---:|---:|---:|---:|---|
| `TransactionTypeID` | True | 0 | 4 | 0 | 0 | True |

### Low-cardinality values

- `TypeName`: `Deposit` (1), `Payment` (1), `Transfer` (1), `Withdrawal` (1)

## transactions.csv

SHA-256: `71aa325acb33c4413a576cdfeb3396c2cba0445529361103dc05231f0d258583`

### Columns and missing values

| Column | Inferred dtype | Nulls | Null % |
|---|---|---:|---:|
| `TransactionID` | `int64` | 0 | 0.0000 |
| `AccountOriginID` | `int64` | 0 | 0.0000 |
| `AccountDestinationID` | `int64` | 0 | 0.0000 |
| `TransactionTypeID` | `int64` | 0 | 0.0000 |
| `Amount` | `float64` | 0 | 0.0000 |
| `TransactionDate` | `object` | 1000 | 2.0000 |
| `BranchID` | `int64` | 0 | 0.0000 |
| `Description` | `object` | 0 | 0.0000 |

### Duplicates and candidate keys

Exact duplicate rows involved: 1000; excess copies: 500.

| Identifier | Likely primary identifier | Nulls | Distinct | Duplicate values | Conflicting values | Clean PK candidate |
|---|---|---:|---:|---:|---:|---|
| `TransactionID` | True | 0 | 49500 | 500 | 0 | False |
| `AccountOriginID` | False | 0 | 1651 | 1651 | 1651 | False |
| `AccountDestinationID` | False | 0 | 1651 | 1651 | 1651 | False |
| `TransactionTypeID` | False | 0 | 4 | 4 | 4 | False |
| `BranchID` | False | 0 | 50 | 50 | 50 | False |

Sample duplicated identifier values:

- `TransactionID`: `['3000044', '3000051', '3000098', '3000145', '3000164', '3000501', '3000514', '3000585', '3000767', '3000814']`
- `AccountOriginID`: `['200000', '200001', '200002', '200003', '200004', '200005', '200006', '200007', '200008', '200009']`
- `AccountDestinationID`: `['200000', '200001', '200002', '200003', '200004', '200005', '200006', '200007', '200008', '200009']`
- `TransactionTypeID`: `['1', '2', '3', '4']`
- `BranchID`: `['1', '10', '11', '12', '13', '14', '15', '16', '17', '18']`

### Numerical fields

| Column | Parse failures | Min | Median | Max | Negative | Zero | IQR outliers |
|---|---:|---:|---:|---:|---:|---:|---:|
| `Amount` | 0 | 1.01 | 2503.89 | 4999.59 | 0 | 0 | 0 |

### Date fields

| Column | Formats | Parse failures | Nulls | Min | Max | After reference date |
|---|---|---:|---:|---|---|---:|
| `TransactionDate` | YYYY-MM-DD HH:MM:SS.ffffff: 49000 | 0 | 1000 | 2020-01-01T00:00:00 | 2026-08-28T15:01:43.084879 | 0 |

## Review boundary

No rows or values were changed by this audit. Duplicate, null, orphan, categorical, date, and numerical findings require an explicit cleaning policy before processing.
