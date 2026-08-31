"""Banking schema and function policy for structurally safe SQL."""

from __future__ import annotations

from typing import Self

from sqlalchemy import Engine
from sqlglot import Dialect, exp
from sqlglot.errors import OptimizeError, SchemaError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from backend.app.db.schema import (
    BANKING_SCHEMA,
    DatabaseSchema,
    introspect_banking_schema,
)
from backend.app.safety.sql_validator import (
    POSTGRES_DIALECT,
    SQLSafetyReasonCode,
    SQLValidationResult,
)


# SQLGlot normalizes PostgreSQL DATE_TRUNC to TIMESTAMP_TRUNC in its AST.
APPROVED_ANALYTICAL_FUNCTIONS = frozenset(
    {
        "ABS",
        "AVG",
        "CAST",
        "COALESCE",
        "COUNT",
        "DATE_TRUNC",
        "DENSE_RANK",
        "EXTRACT",
        "LOWER",
        "MAX",
        "MIN",
        "NULLIF",
        "RANK",
        "ROUND",
        "ROW_NUMBER",
        "SUM",
        "TIMESTAMP_TRUNC",
        "UPPER",
    }
)
_SYSTEM_SCHEMAS = frozenset({"information_schema", "pg_catalog"})
_SYSTEM_FUNCTION_PREFIXES = ("pg_",)


class BankingSQLAccessPolicy:
    """Constrain an accepted AST to introspected banking objects and functions."""

    def __init__(self, database_schema: DatabaseSchema) -> None:
        if database_schema.schema_name != BANKING_SCHEMA:
            raise ValueError("Banking access policy requires banking schema metadata")

        self._database_schema = database_schema
        self._tables = {
            table.name: frozenset(column.name for column in table.columns)
            for table in database_schema.tables
        }
        self._qualification_schema = {
            database_schema.schema_name: {
                table.name: {
                    column.name: column.data_type for column in table.columns
                }
                for table in database_schema.tables
            }
        }

    @classmethod
    def from_engine(cls, engine: Engine) -> Self:
        """Build policy metadata through the Phase 2 introspection boundary."""
        return cls(introspect_banking_schema(engine))

    def validate(self, structural_result: SQLValidationResult) -> SQLValidationResult:
        """Apply access policy after structural validation without bypassing it."""
        if not structural_result.accepted or structural_result.expression is None:
            return structural_result

        expression = structural_result.expression
        table_rejection = self._validate_physical_tables(expression)
        if table_rejection is not None:
            return table_rejection

        function_rejection = _validate_functions(expression)
        if function_rejection is not None:
            return function_rejection

        try:
            qualify(
                expression.copy(),
                dialect=POSTGRES_DIALECT,
                db=self._database_schema.schema_name,
                schema=self._qualification_schema,
                allow_partial_qualification=False,
                validate_qualify_columns=True,
                quote_identifiers=False,
                identify=False,
            )
        except (OptimizeError, SchemaError):
            return _rejected(
                SQLSafetyReasonCode.UNKNOWN_COLUMN,
                "SQL references an unknown or ambiguous column",
            )

        return SQLValidationResult(
            accepted=True,
            reason_code=None,
            message="SQL is allowed by the banking access policy",
            expression=expression,
        )

    def _validate_physical_tables(
        self, expression: exp.Expression
    ) -> SQLValidationResult | None:
        try:
            scopes = traverse_scope(expression)
            physical_tables = (
                source
                for scope in scopes
                for _, source in scope.selected_sources.values()
                if isinstance(source, exp.Table)
            )
            for table in physical_tables:
                catalog = _normalized_identifier(table.args.get("catalog"))
                schema_name = _normalized_identifier(table.args.get("db"))
                table_name = _normalized_identifier(table.this)

                if catalog:
                    return _rejected(
                        SQLSafetyReasonCode.FORBIDDEN_SCHEMA,
                        "Cross-database table access is not allowed",
                    )
                if schema_name in _SYSTEM_SCHEMAS:
                    return _rejected(
                        SQLSafetyReasonCode.UNSAFE_SYSTEM_ACCESS,
                        "PostgreSQL system metadata access is not allowed",
                    )
                if schema_name and schema_name != self._database_schema.schema_name:
                    return _rejected(
                        SQLSafetyReasonCode.FORBIDDEN_SCHEMA,
                        "Only the banking schema is allowed",
                    )
                if table_name.startswith("pg_"):
                    return _rejected(
                        SQLSafetyReasonCode.UNSAFE_SYSTEM_ACCESS,
                        "PostgreSQL system relation access is not allowed",
                    )
                if table_name not in self._tables:
                    return _rejected(
                        SQLSafetyReasonCode.UNKNOWN_TABLE,
                        "SQL references an unknown banking table",
                    )
        except OptimizeError:
            return _rejected(
                SQLSafetyReasonCode.UNKNOWN_TABLE,
                "SQL table sources could not be resolved",
            )
        return None


def _validate_functions(expression: exp.Expression) -> SQLValidationResult | None:
    for function in expression.find_all(exp.Func):
        if isinstance(function, exp.Anonymous):
            function_name = function.name.upper()
            reason = (
                SQLSafetyReasonCode.FORBIDDEN_FUNCTION
                if function.name.lower().startswith(_SYSTEM_FUNCTION_PREFIXES)
                else SQLSafetyReasonCode.UNKNOWN_FUNCTION
            )
            return _rejected(reason, f"Function {function_name} is not approved")

        # Core expression nodes such as AND share SQLGlot's Func base class but
        # are operators, not callable SQL functions. All other function modules
        # are held to the explicit analytical allowlist.
        if type(function).__module__ == "sqlglot.expressions.core":
            continue
        function_name = function.sql_name().upper()
        if function_name not in APPROVED_ANALYTICAL_FUNCTIONS:
            return _rejected(
                SQLSafetyReasonCode.FORBIDDEN_FUNCTION,
                f"Function {function_name} is not approved",
            )
    return None


def _normalized_identifier(identifier: exp.Expression | None) -> str:
    if not isinstance(identifier, exp.Identifier):
        return ""
    dialect = Dialect.get_or_raise(POSTGRES_DIALECT)
    return dialect.normalize_identifier(identifier.copy()).name


def _rejected(
    reason_code: SQLSafetyReasonCode, message: str
) -> SQLValidationResult:
    return SQLValidationResult(
        accepted=False,
        reason_code=reason_code,
        message=message,
    )
