"""Create the approved banking schema.

Revision ID: 20260831_01
Revises: None
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260831_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "banking"


def upgrade() -> None:
    op.execute(sa.schema.CreateSchema(SCHEMA))

    lookup_specs = (
        ("account_statuses", "account_status_id", "status_name"),
        ("account_types", "account_type_id", "type_name"),
        ("customer_types", "customer_type_id", "type_name"),
        ("loan_statuses", "loan_status_id", "status_name"),
        ("transaction_types", "transaction_type_id", "type_name"),
    )
    for table, key, label in lookup_specs:
        op.create_table(
            table,
            sa.Column(key, sa.Integer(), nullable=False),
            sa.Column(label, sa.Text(), nullable=False),
            sa.CheckConstraint(f"{key} > 0", name=f"{key}_positive"),
            sa.PrimaryKeyConstraint(key, name=f"pk_{table}"),
            sa.UniqueConstraint(label, name=f"uq_{table}_{label}"),
            schema=SCHEMA,
        )

    op.create_table(
        "addresses",
        sa.Column("address_id", sa.Integer(), nullable=False),
        sa.Column("street", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("country", sa.Text(), nullable=True),
        sa.CheckConstraint("address_id > 0", name="address_id_positive"),
        sa.PrimaryKeyConstraint("address_id", name="pk_addresses"),
        schema=SCHEMA,
    )
    op.create_table(
        "branches",
        sa.Column("branch_id", sa.Integer(), nullable=False),
        sa.Column("branch_name", sa.Text(), nullable=True),
        sa.Column("address_id", sa.Integer(), nullable=False),
        sa.CheckConstraint("branch_id > 0", name="branch_id_positive"),
        sa.ForeignKeyConstraint(
            ["address_id"], ["banking.addresses.address_id"],
            name="fk_branches_address_id_addresses",
        ),
        sa.PrimaryKeyConstraint("branch_id", name="pk_branches"),
        schema=SCHEMA,
    )
    op.create_table(
        "customers",
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("first_name", sa.Text(), nullable=True),
        sa.Column("last_name", sa.Text(), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("address_id", sa.Integer(), nullable=False),
        sa.Column("customer_type_id", sa.Integer(), nullable=False),
        sa.CheckConstraint("customer_id > 0", name="customer_id_positive"),
        sa.ForeignKeyConstraint(
            ["address_id"], ["banking.addresses.address_id"],
            name="fk_customers_address_id_addresses",
        ),
        sa.ForeignKeyConstraint(
            ["customer_type_id"], ["banking.customer_types.customer_type_id"],
            name="fk_customers_customer_type_id_customer_types",
        ),
        sa.PrimaryKeyConstraint("customer_id", name="pk_customers"),
        schema=SCHEMA,
    )
    op.create_index("ix_customers_address_id", "customers", ["address_id"], schema=SCHEMA)

    op.create_table(
        "accounts",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("account_type_id", sa.Integer(), nullable=False),
        sa.Column("account_status_id", sa.Integer(), nullable=False),
        sa.Column("balance", sa.Numeric(14, 2), nullable=False),
        sa.Column("opening_date", sa.Date(), nullable=True),
        sa.CheckConstraint("account_id > 0", name="account_id_positive"),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["banking.customers.customer_id"],
            name="fk_accounts_customer_id_customers",
        ),
        sa.ForeignKeyConstraint(
            ["account_type_id"], ["banking.account_types.account_type_id"],
            name="fk_accounts_account_type_id_account_types",
        ),
        sa.ForeignKeyConstraint(
            ["account_status_id"], ["banking.account_statuses.account_status_id"],
            name="fk_accounts_account_status_id_account_statuses",
        ),
        sa.PrimaryKeyConstraint("account_id", name="pk_accounts"),
        schema=SCHEMA,
    )
    op.create_index("ix_accounts_customer_id", "accounts", ["customer_id"], schema=SCHEMA)

    op.create_table(
        "loans",
        sa.Column("loan_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("loan_status_id", sa.Integer(), nullable=False),
        sa.Column("principal_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("interest_rate", sa.Numeric(7, 4), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("estimated_end_date", sa.Date(), nullable=True),
        sa.CheckConstraint("loan_id > 0", name="loan_id_positive"),
        sa.CheckConstraint("principal_amount > 0", name="principal_amount_positive"),
        sa.CheckConstraint(
            "interest_rate >= 0 AND interest_rate <= 1",
            name="interest_rate_fraction",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["banking.accounts.account_id"],
            name="fk_loans_account_id_accounts",
        ),
        sa.ForeignKeyConstraint(
            ["loan_status_id"], ["banking.loan_statuses.loan_status_id"],
            name="fk_loans_loan_status_id_loan_statuses",
        ),
        sa.PrimaryKeyConstraint("loan_id", name="pk_loans"),
        schema=SCHEMA,
    )
    op.create_index("ix_loans_account_id", "loans", ["account_id"], schema=SCHEMA)

    op.create_table(
        "transactions",
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("account_origin_id", sa.Integer(), nullable=False),
        sa.Column("account_destination_id", sa.Integer(), nullable=False),
        sa.Column("transaction_type_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("transaction_date", sa.DateTime(timezone=False), nullable=False),
        sa.Column("branch_id", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.CheckConstraint("transaction_id > 0", name="transaction_id_positive"),
        sa.CheckConstraint("amount > 0", name="amount_positive"),
        sa.ForeignKeyConstraint(
            ["account_origin_id"], ["banking.accounts.account_id"],
            name="fk_transactions_account_origin_id_accounts",
        ),
        sa.ForeignKeyConstraint(
            ["account_destination_id"], ["banking.accounts.account_id"],
            name="fk_transactions_account_destination_id_accounts",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_type_id"], ["banking.transaction_types.transaction_type_id"],
            name="fk_transactions_transaction_type_id_transaction_types",
        ),
        sa.ForeignKeyConstraint(
            ["branch_id"], ["banking.branches.branch_id"],
            name="fk_transactions_branch_id_branches",
        ),
        sa.PrimaryKeyConstraint("transaction_id", name="pk_transactions"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_transactions_account_origin_id_transaction_date",
        "transactions", ["account_origin_id", "transaction_date"], schema=SCHEMA,
    )
    op.create_index(
        "ix_transactions_account_destination_id_transaction_date",
        "transactions", ["account_destination_id", "transaction_date"], schema=SCHEMA,
    )
    op.create_index(
        "ix_transactions_transaction_date", "transactions", ["transaction_date"], schema=SCHEMA
    )
    op.create_index(
        "ix_transactions_branch_id", "transactions", ["branch_id"], schema=SCHEMA
    )


def downgrade() -> None:
    for table in (
        "transactions", "loans", "accounts", "customers", "branches",
        "addresses", "transaction_types", "loan_statuses", "customer_types",
        "account_types", "account_statuses",
    ):
        op.drop_table(table, schema=SCHEMA)
    op.execute(sa.schema.DropSchema(SCHEMA))
