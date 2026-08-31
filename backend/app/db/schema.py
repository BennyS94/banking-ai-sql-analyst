"""Typed PostgreSQL metadata introspection for the banking schema."""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, inspect
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.exc import SQLAlchemyError


BANKING_SCHEMA = "banking"
logger = logging.getLogger(__name__)


class SchemaIntrospectionError(RuntimeError):
    """Raised when approved banking metadata cannot be read."""


class ColumnSchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    data_type: str
    nullable: bool


class ForeignKeySchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    columns: tuple[str, ...]
    referenced_schema: str
    referenced_table: str
    referenced_columns: tuple[str, ...]


class TableSchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    columns: tuple[ColumnSchema, ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[ForeignKeySchema, ...]


class DatabaseSchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_name: str
    tables: tuple[TableSchema, ...]


def introspect_banking_schema(engine: Engine) -> DatabaseSchema:
    """Derive deterministic metadata for base tables in the banking schema."""
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            tables = tuple(
                _introspect_table(inspector, engine, table_name)
                for table_name in sorted(
                    inspector.get_table_names(schema=BANKING_SCHEMA)
                )
            )
    except SQLAlchemyError as exc:
        logger.exception("Banking schema introspection failed")
        raise SchemaIntrospectionError(
            "Unable to read banking schema metadata"
        ) from exc

    return DatabaseSchema(schema_name=BANKING_SCHEMA, tables=tables)


def _introspect_table(
    inspector: Inspector, engine: Engine, table_name: str
) -> TableSchema:
    columns = tuple(
        ColumnSchema(
            name=column["name"],
            data_type=column["type"].compile(dialect=engine.dialect).lower(),
            nullable=column["nullable"],
        )
        for column in inspector.get_columns(table_name, schema=BANKING_SCHEMA)
    )

    primary_key = tuple(
        inspector.get_pk_constraint(table_name, schema=BANKING_SCHEMA).get(
            "constrained_columns"
        )
        or ()
    )

    foreign_keys = tuple(
        sorted(
            (
                ForeignKeySchema(
                    columns=tuple(foreign_key.get("constrained_columns") or ()),
                    referenced_schema=(
                        foreign_key.get("referred_schema") or BANKING_SCHEMA
                    ),
                    referenced_table=foreign_key["referred_table"],
                    referenced_columns=tuple(
                        foreign_key.get("referred_columns") or ()
                    ),
                )
                for foreign_key in inspector.get_foreign_keys(
                    table_name, schema=BANKING_SCHEMA
                )
            ),
            key=lambda foreign_key: (
                foreign_key.columns,
                foreign_key.referenced_schema,
                foreign_key.referenced_table,
                foreign_key.referenced_columns,
            ),
        )
    )

    return TableSchema(
        name=table_name,
        columns=columns,
        primary_key=primary_key,
        foreign_keys=foreign_keys,
    )
