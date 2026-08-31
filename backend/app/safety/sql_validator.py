"""PostgreSQL AST validation for untrusted generated SQL."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


POSTGRES_DIALECT = "postgres"


class SQLSafetyReasonCode(StrEnum):
    """Stable structural SQL rejection reasons."""

    PARSE_ERROR = "parse_error"
    MULTIPLE_STATEMENTS = "multiple_statements"
    UNSUPPORTED_STATEMENT = "unsupported_statement"
    MUTATION_STATEMENT = "mutation_statement"
    DDL_STATEMENT = "ddl_statement"
    ADMINISTRATIVE_STATEMENT = "administrative_statement"
    DATA_MODIFYING_CTE = "data_modifying_cte"
    SELECT_INTO = "select_into"


@dataclass(frozen=True)
class SQLValidationResult:
    """Structured validation outcome with the accepted AST kept internal."""

    accepted: bool
    reason_code: SQLSafetyReasonCode | None
    message: str
    expression: exp.Expression | None = None


_ALLOWED_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)
_MUTATION_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Merge)
_DDL_NODES = (exp.Create, exp.Drop, exp.Alter, exp.TruncateTable)
_ADMINISTRATIVE_NODES = (
    exp.Copy,
    exp.Command,
    exp.Set,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Grant,
    exp.Revoke,
    exp.Analyze,
)


class SQLASTValidator:
    """Approve one structurally read-only PostgreSQL analytical statement."""

    def validate(self, sql: str) -> SQLValidationResult:
        try:
            parsed = sqlglot.parse(sql, read=POSTGRES_DIALECT)
        except (ParseError, TypeError, ValueError):
            return _rejected(
                SQLSafetyReasonCode.PARSE_ERROR,
                "SQL could not be parsed as PostgreSQL",
            )

        statements = tuple(statement for statement in parsed if statement is not None)
        if not statements:
            return _rejected(
                SQLSafetyReasonCode.PARSE_ERROR,
                "SQL did not contain an executable statement",
            )
        if len(statements) != 1:
            return _rejected(
                SQLSafetyReasonCode.MULTIPLE_STATEMENTS,
                "SQL must contain exactly one executable statement",
            )

        statement = statements[0]
        nested_rejection = _find_forbidden_structure(statement)
        if nested_rejection is not None:
            return nested_rejection
        if not isinstance(statement, _ALLOWED_ROOTS):
            return _rejected(
                SQLSafetyReasonCode.UNSUPPORTED_STATEMENT,
                f"Unsupported SQL statement type: {type(statement).__name__}",
            )

        return SQLValidationResult(
            accepted=True,
            reason_code=None,
            message="SQL is structurally read-only",
            expression=statement,
        )


def _find_forbidden_structure(
    statement: exp.Expression,
) -> SQLValidationResult | None:
    for node in statement.walk():
        if isinstance(node, exp.Into):
            return _rejected(
                SQLSafetyReasonCode.SELECT_INTO,
                "SELECT INTO is not allowed",
            )
        if isinstance(node, _MUTATION_NODES):
            reason = (
                SQLSafetyReasonCode.DATA_MODIFYING_CTE
                if node.find_ancestor(exp.CTE) is not None
                else SQLSafetyReasonCode.MUTATION_STATEMENT
            )
            return _rejected(reason, "Data mutation is not allowed")
        if isinstance(node, _DDL_NODES):
            return _rejected(
                SQLSafetyReasonCode.DDL_STATEMENT,
                "DDL statements are not allowed",
            )
        if isinstance(node, _ADMINISTRATIVE_NODES):
            return _rejected(
                SQLSafetyReasonCode.ADMINISTRATIVE_STATEMENT,
                "Administrative SQL statements are not allowed",
            )
    return None


def _rejected(
    reason_code: SQLSafetyReasonCode, message: str
) -> SQLValidationResult:
    return SQLValidationResult(
        accepted=False,
        reason_code=reason_code,
        message=message,
    )
