"""Approved Phase 1 relational banking model."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


BANKING_SCHEMA = "banking"


class Base(DeclarativeBase):
    metadata = MetaData(
        schema=BANKING_SCHEMA,
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_N_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        },
    )


class AccountStatus(Base):
    __tablename__ = "account_statuses"
    __table_args__ = (
        CheckConstraint("account_status_id > 0", name="account_status_id_positive"),
        UniqueConstraint("status_name"),
    )
    account_status_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status_name: Mapped[str] = mapped_column(Text, nullable=False)


class AccountType(Base):
    __tablename__ = "account_types"
    __table_args__ = (
        CheckConstraint("account_type_id > 0", name="account_type_id_positive"),
        UniqueConstraint("type_name"),
    )
    account_type_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type_name: Mapped[str] = mapped_column(Text, nullable=False)


class CustomerType(Base):
    __tablename__ = "customer_types"
    __table_args__ = (
        CheckConstraint("customer_type_id > 0", name="customer_type_id_positive"),
        UniqueConstraint("type_name"),
    )
    customer_type_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type_name: Mapped[str] = mapped_column(Text, nullable=False)


class LoanStatus(Base):
    __tablename__ = "loan_statuses"
    __table_args__ = (
        CheckConstraint("loan_status_id > 0", name="loan_status_id_positive"),
        UniqueConstraint("status_name"),
    )
    loan_status_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status_name: Mapped[str] = mapped_column(Text, nullable=False)


class TransactionType(Base):
    __tablename__ = "transaction_types"
    __table_args__ = (
        CheckConstraint(
            "transaction_type_id > 0", name="transaction_type_id_positive"
        ),
        UniqueConstraint("type_name"),
    )
    transaction_type_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type_name: Mapped[str] = mapped_column(Text, nullable=False)


class Address(Base):
    __tablename__ = "addresses"
    __table_args__ = (
        CheckConstraint("address_id > 0", name="address_id_positive"),
    )
    address_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    street: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(Text, nullable=True)


class Branch(Base):
    __tablename__ = "branches"
    __table_args__ = (CheckConstraint("branch_id > 0", name="branch_id_positive"),)
    branch_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    branch_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    address_id: Mapped[int] = mapped_column(
        ForeignKey("banking.addresses.address_id"), nullable=False
    )


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint("customer_id > 0", name="customer_id_positive"),
        Index("ix_customers_address_id", "address_id"),
    )
    customer_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    first_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    address_id: Mapped[int] = mapped_column(
        ForeignKey("banking.addresses.address_id"), nullable=False
    )
    customer_type_id: Mapped[int] = mapped_column(
        ForeignKey("banking.customer_types.customer_type_id"), nullable=False
    )


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("account_id > 0", name="account_id_positive"),
        Index("ix_accounts_customer_id", "customer_id"),
    )
    account_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("banking.customers.customer_id"), nullable=False
    )
    account_type_id: Mapped[int] = mapped_column(
        ForeignKey("banking.account_types.account_type_id"), nullable=False
    )
    account_status_id: Mapped[int] = mapped_column(
        ForeignKey("banking.account_statuses.account_status_id"), nullable=False
    )
    balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    opening_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class Loan(Base):
    __tablename__ = "loans"
    __table_args__ = (
        CheckConstraint("loan_id > 0", name="loan_id_positive"),
        CheckConstraint("principal_amount > 0", name="principal_amount_positive"),
        CheckConstraint(
            "interest_rate >= 0 AND interest_rate <= 1",
            name="interest_rate_fraction",
        ),
        Index("ix_loans_account_id", "account_id"),
    )
    loan_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("banking.accounts.account_id"), nullable=False
    )
    loan_status_id: Mapped[int] = mapped_column(
        ForeignKey("banking.loan_statuses.loan_status_id"), nullable=False
    )
    principal_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    interest_rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    estimated_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("transaction_id > 0", name="transaction_id_positive"),
        CheckConstraint("amount > 0", name="amount_positive"),
        Index(
            "ix_transactions_account_origin_id_transaction_date",
            "account_origin_id",
            "transaction_date",
        ),
        Index(
            "ix_transactions_account_destination_id_transaction_date",
            "account_destination_id",
            "transaction_date",
        ),
        Index("ix_transactions_transaction_date", "transaction_date"),
        Index("ix_transactions_branch_id", "branch_id"),
    )
    transaction_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_origin_id: Mapped[int] = mapped_column(
        ForeignKey("banking.accounts.account_id"), nullable=False
    )
    account_destination_id: Mapped[int] = mapped_column(
        ForeignKey("banking.accounts.account_id"), nullable=False
    )
    transaction_type_id: Mapped[int] = mapped_column(
        ForeignKey("banking.transaction_types.transaction_type_id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    transaction_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False
    )
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("banking.branches.branch_id"), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
