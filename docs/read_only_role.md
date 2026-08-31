# Read-Only Banking Role

Schema migrations and data loading use the owner connection in `DATABASE_URL`.
Analytical queries use a separate PostgreSQL login with direct, least-privilege
grants on the approved `banking` schema.

Set non-secret role names and local credentials outside Git, then provision the
role as the database owner:

```powershell
$env:BANKING_READER_USER = "banking_reader"
$env:BANKING_READER_PASSWORD = "<local-reader-password>"
python -m banking_data.role_management
```

The command creates or hardens the login and grants only schema `USAGE` plus
`SELECT` on the current banking tables. It does not grant ownership, role
management, database/schema/temporary-object creation, sequence access, or table
mutation privileges.

Provisioning is transactional and fails closed for a pre-existing target role
that belongs to another role or owns the project database, the `banking` schema,
or relevant project relations. These relationships are not removed
automatically. After applying the direct grants, the command checks the role's
effective privilege boundary before committing. Re-running the command for an
existing clean reader safely rotates its password and reapplies the grants.

Configure `BANKING_READER_DATABASE_URL` separately for future analytical query
execution. Never put a populated URL or password in source control.
