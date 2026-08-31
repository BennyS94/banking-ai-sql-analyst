"""Deterministic grounding context for banking NL-to-SQL generation."""

from __future__ import annotations

from importlib.resources import files
import json
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from sqlalchemy import Engine

from backend.app.db.schema import (
    BANKING_SCHEMA,
    DatabaseSchema,
    ForeignKeySchema,
    introspect_banking_schema,
)


class BankingContextError(RuntimeError):
    """Raised when approved schema or semantic context cannot be built."""


class SemanticDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    definition: str

    @field_validator("name", "definition")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("semantic text must be non-empty")
        return value.strip()


class ControlledDomain(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    values: tuple[str, ...]

    @field_validator("name")
    @classmethod
    def require_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("domain name must be non-empty")
        return value.strip()

    @field_validator("values")
    @classmethod
    def require_unique_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values or any(not value.strip() for value in values):
            raise ValueError("domain values must be non-empty")
        if len(values) != len(set(values)):
            raise ValueError("domain values must be unique")
        return values


class BankingSemantics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    semantics: tuple[SemanticDefinition, ...]
    controlled_domains: tuple[ControlledDomain, ...]

    @field_validator("semantics", "controlled_domains")
    @classmethod
    def require_entries(cls, entries: tuple[object, ...]) -> tuple[object, ...]:
        if not entries:
            raise ValueError("grounding resource sections must be non-empty")
        return entries


class BankingAIContextBuilder:
    """Build context from Phase 2 introspection plus tracked semantics."""

    def __init__(self, engine: Engine, semantics_path: Path | None = None) -> None:
        self._engine = engine
        self._semantics_path = semantics_path

    def build(self) -> str:
        schema = introspect_banking_schema(self._engine)
        semantics = load_banking_semantics(self._semantics_path)
        return render_banking_context(schema, semantics)


def load_banking_semantics(path: Path | None = None) -> BankingSemantics:
    """Load and validate the tracked runtime grounding resource."""
    resource = path or Path(
        str(files("backend.app.ai.resources").joinpath("banking_semantics.json"))
    )
    try:
        payload = json.loads(resource.read_text(encoding="utf-8"))
        return BankingSemantics.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise BankingContextError(
            "Banking semantic context is missing or invalid"
        ) from exc


def render_banking_context(
    schema: DatabaseSchema, semantics: BankingSemantics
) -> str:
    """Render compact deterministic schema and semantic grounding text."""
    _validate_approved_schema(schema)
    lines = ["DATABASE DIALECT", "PostgreSQL", "", "SCHEMA", BANKING_SCHEMA]

    for table in sorted(schema.tables, key=lambda item: item.name):
        lines.extend(("", f"TABLE {BANKING_SCHEMA}.{table.name}"))
        primary_keys = set(table.primary_key)
        for column in sorted(table.columns, key=lambda item: item.name):
            attributes = [column.data_type]
            if column.name in primary_keys:
                attributes.append("primary key")
            attributes.append("nullable" if column.nullable else "not null")
            lines.append(f"- {column.name}: {', '.join(attributes)}")

    lines.extend(("", "RELATIONSHIPS"))
    for table_name, foreign_key in _ordered_relationships(schema):
        local = ", ".join(foreign_key.columns)
        referenced = ", ".join(foreign_key.referenced_columns)
        lines.append(
            f"- {BANKING_SCHEMA}.{table_name}({local}) -> "
            f"{foreign_key.referenced_schema}."
            f"{foreign_key.referenced_table}({referenced})"
        )

    lines.extend(("", "BUSINESS SEMANTICS"))
    for item in sorted(semantics.semantics, key=lambda item: item.name):
        lines.append(f"- {item.name}: {item.definition}")

    lines.extend(("", "CONTROLLED DOMAIN VALUES"))
    for domain in sorted(
        semantics.controlled_domains, key=lambda item: item.name
    ):
        lines.append(f"- {domain.name}: {', '.join(sorted(domain.values))}")

    return "\n".join(lines)


def _ordered_relationships(
    schema: DatabaseSchema,
) -> Iterable[tuple[str, ForeignKeySchema]]:
    relationships = (
        (table.name, foreign_key)
        for table in schema.tables
        for foreign_key in table.foreign_keys
    )
    return sorted(
        relationships,
        key=lambda item: (
            item[0],
            item[1].columns,
            item[1].referenced_schema,
            item[1].referenced_table,
            item[1].referenced_columns,
        ),
    )


def _validate_approved_schema(schema: DatabaseSchema) -> None:
    if schema.schema_name != BANKING_SCHEMA:
        raise BankingContextError("Only the banking schema may be rendered")
    if any(
        foreign_key.referenced_schema != BANKING_SCHEMA
        for table in schema.tables
        for foreign_key in table.foreign_keys
    ):
        raise BankingContextError("Only banking relationships may be rendered")
