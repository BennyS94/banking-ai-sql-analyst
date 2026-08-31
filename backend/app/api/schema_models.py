"""Stable public response models for banking schema metadata."""

from pydantic import BaseModel, ConfigDict, Field

from backend.app.db.schema import DatabaseSchema


class ColumnResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    type: str
    nullable: bool


class ForeignKeyResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    columns: tuple[str, ...]
    referenced_schema: str
    referenced_table: str
    referenced_columns: tuple[str, ...]


class TableResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    columns: tuple[ColumnResponse, ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[ForeignKeyResponse, ...]


class DatabaseSchemaResponse(BaseModel):
    model_config = ConfigDict(frozen=True, serialize_by_alias=True)

    schema_name: str = Field(serialization_alias="schema")
    tables: tuple[TableResponse, ...]

    @classmethod
    def from_database_schema(
        cls, database_schema: DatabaseSchema
    ) -> "DatabaseSchemaResponse":
        return cls(
            schema_name=database_schema.schema_name,
            tables=tuple(
                TableResponse(
                    name=table.name,
                    columns=tuple(
                        ColumnResponse(
                            name=column.name,
                            type=column.data_type,
                            nullable=column.nullable,
                        )
                        for column in table.columns
                    ),
                    primary_key=table.primary_key,
                    foreign_keys=tuple(
                        ForeignKeyResponse(
                            columns=foreign_key.columns,
                            referenced_schema=foreign_key.referenced_schema,
                            referenced_table=foreign_key.referenced_table,
                            referenced_columns=foreign_key.referenced_columns,
                        )
                        for foreign_key in table.foreign_keys
                    ),
                )
                for table in database_schema.tables
            ),
        )
